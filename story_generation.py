"""
story_generation.py — LLM-powered narrative layer for Sentinel and Atlas.

Reads a build_sentinel_intelligence_object() / build_atlas_intelligence_object()
output and produces the prose for the headline elaboration, WHAT DESERVES
ATTENTION, and WHAT COULD CHANGE THIS sections of the story arc.

SO WHAT is deliberately NOT generated here as of the personalization work —
it moved to profile_guidance.py, a deterministic, profile-aware rule layer,
since portfolio instructions differ per viewer (mandate/risk/horizon) while
everything else in this file stays shared and profile-agnostic, cached once
per run. See profile_guidance.py's module docstring for the reasoning.

Everything in this file except the one LLM call is deterministic, plain
Python, and independently testable — the reliability-band caveat, the
briefing_allowed pause gate, and the numeric grounding for trigger
conditions are all enforced here in code, not trusted to prompt
instructions. See the design review this was built against for the
reasoning behind each of these (CAUTION-band wording is verbatim from
intelligence_object.reliability(); briefing_allowed mirrors main_api.py's
existing confidence gate; change_triggers are restricted to numbers
already present on the intelligence_object).

Provider/model selection is a single config point (STORY_LLM_CONFIG)
read by _call_story_llm(), the one call site every other function goes
through — switching providers or models later is a config change here,
not a rewrite of generate_story() or anything upstream of it. This is
deliberately not a plugin system: one branch per provider in
_call_story_llm() is enough until there's a second real provider to
support.
"""
from __future__ import annotations

import json
import os

# ══════════════════════════════════════════════════════════════════
# PROVIDER CONFIG — the one place to change provider/model/params.
# ══════════════════════════════════════════════════════════════════
STORY_LLM_CONFIG = {
    "provider":   "anthropic",
    "model":      "claude-opus-5",
    "max_tokens": 2048,
    "effort":     "high",
}

STORY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline_elaboration":    {"type": "string"},
        "what_deserves_attention": {"type": "string"},
        # Structured claim-then-check hooks: every convergence/contradiction
        # relationship described anywhere in headline_elaboration or
        # what_deserves_attention must be declared here as an index into
        # intelligence_object["convergence"] / ["contradictions"]. This is
        # what _validate_grounding() checks against the real arrays — it
        # does not parse the prose itself. An index that doesn't exist
        # (including any index at all when the array is empty) fails the
        # whole output closed, same as an ungrounded change_trigger number.
        "convergence_refs": {
            "type": "array",
            "items": {"type": "integer"},
        },
        "contradiction_refs": {
            "type": "array",
            "items": {"type": "integer"},
        },
        "change_triggers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "signal_id": {"type": "string"},
                    "sentence":  {"type": "string"},
                },
                "required": ["signal_id", "sentence"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "headline_elaboration",
        "what_deserves_attention",
        "convergence_refs", "contradiction_refs",
        "change_triggers",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You write the narrative sections of a macro-economics dashboard. You are
given a structured "intelligence object" for today's read — signals,
their stances, detected convergence/contradictions — plus (if present)
a set of pre-computed "change trigger candidates" with real numbers
already attached, and an existing deterministic narrative paragraph
written by an older, simpler rule-based system for the same underlying
regime. Your job is to write something better-grounded than that old
paragraph, not to repeat it — use it only as background on how this
regime has historically been described, never as a source of facts or
numbers to copy.

HARD RULES — violating any of these makes your output unusable:
1. Every number you write (a value, a threshold, a distance, a
   percentage) must come verbatim from intelligence_object or
   change_trigger_candidates. Never compute, round differently, or
   estimate a number — including never copying a number from
   reference_narrative unless that same number also appears in
   intelligence_object.
2. Never state a relationship between two signals that isn't already
   present in "convergence" or "contradictions". You may reference and
   summarize those entries; you may not invent new ones. Every such
   relationship you describe anywhere in headline_elaboration or
   what_deserves_attention — including phrases like "the one clean
   convergence" or "these signals confirm each other" — must be
   declared in convergence_refs / contradiction_refs as the index of
   the real entry in intelligence_object["convergence"] /
   ["contradictions"] you are describing. If convergence or
   contradictions is empty, do not describe any signals as converging
   or contradicting, in any words, and leave the corresponding refs
   list empty. An output with prose describing a relationship but no
   matching ref (or a ref pointing past the end of the real array) is
   rejected outright.
3. Do not write about confidence, reliability, briefings, or pausing —
   that is handled entirely outside your output, upstream of this call.
   You are only ever invoked when it does not apply.
4. If change_trigger_candidates is empty, "what_could_change_this" must
   say plainly that no quantified trigger levels are available yet for
   this module — do not invent directional language to fill the gap.
5. Write in plain, direct language for someone making portfolio
   decisions — no hedging filler, no restating the JSON as a list."""


def _nearest_boundary_str_variants(n) -> list[str]:
    """A couple of plausible string forms of a number, for a loose but
    real substring check against the LLM's free-text sentence."""
    if n is None:
        return []
    variants = {str(n)}
    if isinstance(n, float):
        if n == int(n):
            variants.add(str(int(n)))
        variants.add(f"{n:.1f}")
        variants.add(f"{n:.2f}")
    return list(variants)


def _build_trigger_candidates(io: dict) -> list[dict]:
    """
    Deterministic. For every signal with a real distance_to_threshold,
    produces a pre-written candidate sentence with the real numbers
    already in it. The LLM selects and rephrases from this list; it
    never computes or invents a threshold number of its own.
    """
    candidates = []
    for sig in io.get("signals", []):
        distance = sig.get("distance_to_threshold")
        boundary = sig.get("nearest_boundary")
        value = sig.get("value")
        if distance is None or boundary is None or value is None:
            continue
        if not isinstance(value, (int, float)):
            continue  # categorical signals (e.g. rbi_stance's "PAUSE") have no numeric trigger
        direction = "above" if value > boundary else "below" if value < boundary else "at"
        candidates.append({
            "signal_id":             sig["id"],
            "label":                 sig["label"],
            "value":                 value,
            "nearest_boundary":      boundary,
            "distance_to_threshold": distance,
            "sentence": (
                f"{sig['label']} (currently {value}) is {distance} {direction} "
                f"the {boundary} threshold."
            ),
        })
    return candidates


def assemble_headline_block(io: dict, llm_headline_elaboration: str) -> dict:
    """
    Combines the deterministic reliability preamble (when present) with
    the LLM's elaboration text. The LLM is never responsible for the
    caution wording — it's inserted here, deterministically, whenever
    reliability_flag == CAUTION. Never trusts the LLM to have included
    it; this places it unconditionally, then asserts it's actually
    present in the final text before returning.
    """
    confidence = io.get("confidence") or {}
    flag = confidence.get("reliability_flag")
    note = confidence.get("reliability_note")

    if flag == "CAUTION":
        if not note:
            raise ValueError(
                "CAUTION flag set but reliability_note is missing on the "
                "intelligence_object — refusing to assemble headline "
                "without the required caveat text."
            )
        text = note + "\n\n" + llm_headline_elaboration
    else:
        text = llm_headline_elaboration

    if flag == "CAUTION" and note not in text:
        raise AssertionError(
            "Reliability note missing from assembled output under CAUTION "
            "band — blocking render rather than shipping without it."
        )

    return {"text": text, "caution_applied": (flag == "CAUTION")}


def _build_paused_message(confidence: dict) -> str:
    """
    Deterministic — a fixed template with two real numbers plugged in,
    same principle as the CAUTION note: code produces this, never the
    model. Mirrors main_api.py's existing briefing_allowed gate (0.60
    threshold) rather than inventing new copy for it.
    """
    pct = round(confidence["score"] * 100)
    return (
        f"Briefing paused today. Confidence is {pct}%, below the 60% "
        f"reliability gate the system requires before generating a full "
        f"narrative read. This isn't a missing feature — the system "
        f"itself judged today's signals too uncertain to narrate "
        f"confidently. The WHY section below shows the real, unaffected "
        f"signal relationships, and the full evidence is in the section "
        f"beneath it."
    )


def _validate_grounding(llm_output: dict, candidates: list[dict], io: dict) -> bool:
    """
    Hard check, fails closed: any violation rejects the WHOLE output
    rather than silently editing or dropping just the offending item,
    since editing the model's output is itself a silent-failure risk.

    Two independent things are checked:

    1. change_triggers — every one the LLM wrote must reference a
       signal_id that was actually offered to it, and must state that
       signal's real value/threshold numbers, not a rephrased or
       invented one.

    2. convergence_refs / contradiction_refs — the structured
       claim-then-check hook for relationships described in prose
       (headline_elaboration / what_deserves_attention). This does not
       parse the prose itself; it only checks that every declared index
       actually exists in intelligence_object["convergence"] /
       ["contradictions"]. An empty real array makes every possible
       index invalid, so any relationship claim made while the array is
       empty is rejected here regardless of how it's worded — this is
       what closes the "clean convergence" class of ungrounded claim.
    """
    candidates_by_id = {c["signal_id"]: c for c in candidates}

    for trigger in llm_output.get("change_triggers", []):
        sig_id = trigger.get("signal_id")
        sentence = trigger.get("sentence", "")

        if sig_id not in candidates_by_id:
            print(
                f"[STORY_GEN] Grounding violation: signal_id {sig_id!r} "
                f"not in offered candidates",
                flush=True,
            )
            return False

        cand = candidates_by_id[sig_id]
        value_variants    = _nearest_boundary_str_variants(cand["value"])
        boundary_variants = _nearest_boundary_str_variants(cand["nearest_boundary"])
        if not any(v in sentence for v in value_variants) or \
           not any(b in sentence for b in boundary_variants):
            print(
                f"[STORY_GEN] Grounding violation: sentence for {sig_id!r} "
                f"doesn't contain its real value/threshold numbers: {sentence!r}",
                flush=True,
            )
            return False

    convergence    = io.get("convergence") or []
    contradictions = io.get("contradictions") or []

    for idx in llm_output.get("convergence_refs", []):
        if not isinstance(idx, int) or idx < 0 or idx >= len(convergence):
            print(
                f"[STORY_GEN] Grounding violation: convergence_refs index "
                f"{idx!r} invalid — intelligence_object.convergence has "
                f"{len(convergence)} entries",
                flush=True,
            )
            return False

    for idx in llm_output.get("contradiction_refs", []):
        if not isinstance(idx, int) or idx < 0 or idx >= len(contradictions):
            print(
                f"[STORY_GEN] Grounding violation: contradiction_refs index "
                f"{idx!r} invalid — intelligence_object.contradictions has "
                f"{len(contradictions)} entries",
                flush=True,
            )
            return False

    return True


def _call_anthropic(system_prompt: str, user_content: str, config: dict) -> dict:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=config["model"],
        max_tokens=config["max_tokens"],
        thinking={"type": "adaptive"},
        output_config={
            "effort": config["effort"],
            "format": {"type": "json_schema", "schema": STORY_OUTPUT_SCHEMA},
        },
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ValueError("No text block in LLM response")
    return json.loads(text)


def _call_story_llm(
    io: dict,
    candidates: list[dict],
    deterministic_narrative: str | None,
) -> dict:
    """
    Single call site for the LLM. Provider/model come from
    STORY_LLM_CONFIG — swapping providers means adding a branch here
    and changing the config, not touching generate_story() or anything
    upstream of it.
    """
    user_content = (
        f"<intelligence_object>\n{json.dumps(io, indent=2)}\n</intelligence_object>\n\n"
        f"<change_trigger_candidates>\n{json.dumps(candidates, indent=2)}\n</change_trigger_candidates>\n\n"
        f"<reference_narrative source=\"regime_engine._build_narrative — deterministic, "
        f"pre-existing, background context only, not a source of new facts\">\n"
        f"{deterministic_narrative or '(none available for this module)'}\n"
        f"</reference_narrative>"
    )

    provider = STORY_LLM_CONFIG["provider"]
    if provider == "anthropic":
        return _call_anthropic(SYSTEM_PROMPT, user_content, STORY_LLM_CONFIG)
    raise NotImplementedError(f"Unknown story-generation provider: {provider!r}")


def generate_story(io: dict, deterministic_narrative: str | None = None) -> dict:
    """
    io: build_sentinel_intelligence_object() or build_atlas_intelligence_object()
        output. deterministic_narrative: regime_output["narrative"] for
        Sentinel (the old _build_narrative template); None for Atlas,
        which has no equivalent.

    Returns {"status": "paused" | "unavailable" | "ok", ...}. "paused"
    and "unavailable" are deliberately distinct: paused means the
    system itself judged today's read too uncertain (nothing failed);
    unavailable means generation was attempted and failed or produced
    output that didn't pass the grounding check.
    """
    confidence = io.get("confidence")  # None for Atlas today — no gate applies

    if confidence and confidence.get("briefing_allowed") is False:
        return {"status": "paused", "message": _build_paused_message(confidence)}

    candidates = _build_trigger_candidates(io)

    try:
        llm_output = _call_story_llm(io, candidates, deterministic_narrative)
    except Exception as e:
        print(f"[STORY_GEN] LLM call failed: {e}", flush=True)
        return {"status": "unavailable"}

    if not _validate_grounding(llm_output, candidates, io):
        return {"status": "unavailable"}

    try:
        headline_block = assemble_headline_block(io, llm_output["headline_elaboration"])
    except (ValueError, AssertionError) as e:
        print(f"[STORY_GEN] Headline assembly failed: {e}", flush=True)
        return {"status": "unavailable"}

    return {
        "status":                  "ok",
        "headline":                headline_block["text"],
        "what_deserves_attention": llm_output["what_deserves_attention"],
        "change_triggers":         llm_output["change_triggers"],
    }
