"""
story_generation.py — LLM-powered narrative layer for Sentinel and Atlas.

Reads a build_sentinel_intelligence_object() / build_atlas_intelligence_object()
output and produces the prose for the headline elaboration, SO WHAT, WHAT
DESERVES ATTENTION, and WHAT COULD CHANGE THIS sections of the story arc.

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

import hashlib
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
        "so_what":                 {"type": "string"},
        "what_deserves_attention": {"type": "string"},
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
        "headline_elaboration", "so_what",
        "what_deserves_attention", "change_triggers",
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
   summarize those entries; you may not invent new ones.
3. Do not write about confidence, reliability, briefings, or pausing —
   that is handled entirely outside your output, upstream of this call.
   You are only ever invoked when it does not apply.
4. If change_trigger_candidates is empty, "what_could_change_this" must
   say plainly that no quantified trigger levels are available yet for
   this module — do not invent directional language to fill the gap.
5. Write in plain, direct language for someone making portfolio
   decisions — no hedging filler, no restating the JSON as a list."""

# First 12 hex chars of SHA-256 of SYSTEM_PROMPT, logged with every call
# to story_generation_log so a later fine-tuning pass can tell whether
# two logged calls were generated under the same instructions — the
# prompt will be tuned over time, and that correlation can't be
# reconstructed after the fact if it isn't captured when it happens.
_SYSTEM_PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]


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


def _validate_grounding(llm_output: dict, candidates: list[dict]) -> bool:
    """
    Hard check: every change_trigger the LLM wrote must reference a
    signal_id that was actually offered to it, and must state that
    signal's real value/threshold numbers — not a rephrased or invented
    one. Fails closed: any violation rejects the WHOLE output rather
    than silently editing or dropping just the offending item, since
    editing the model's output is itself a silent-failure risk.
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

    return True


def _call_anthropic(system_prompt: str, user_content: str, config: dict, metadata: dict) -> dict:
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

    metadata["input_tokens"]  = response.usage.input_tokens
    metadata["output_tokens"] = response.usage.output_tokens

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ValueError("No text block in LLM response")
    parsed = json.loads(text)
    metadata["raw_llm_output"] = parsed
    return parsed


def _call_story_llm(
    io: dict,
    candidates: list[dict],
    deterministic_narrative: str | None,
    metadata: dict,
) -> dict:
    """
    Single call site for the LLM. Provider/model come from
    STORY_LLM_CONFIG — swapping providers means adding a branch here
    and changing the config, not touching generate_story() or anything
    upstream of it. `metadata` is an out-parameter the callee fills in
    (token usage, the raw parsed output) so generate_story() can log
    the full call to story_generation_log without changing this
    function's return contract (still just the parsed dict).
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
        return _call_anthropic(SYSTEM_PROMPT, user_content, STORY_LLM_CONFIG, metadata)
    raise NotImplementedError(f"Unknown story-generation provider: {provider!r}")


def _log_story_call(
    supabase_client,
    io: dict,
    candidates: list[dict],
    deterministic_narrative: str | None,
    result: dict | None,
    metadata: dict,
    error_message: str | None,
) -> None:
    """
    Best-effort, non-blocking log of this call to story_generation_log —
    a dedicated append-only table for a future fine-tuning corpus and
    operational history, separate from wherever the caller caches
    `result` for display (runs.story / global_macro_cache). Never
    raises: a logging failure must never affect the story generation or
    caching this call is actually part of.

    supabase_client is injected by the caller (main_api.py's existing
    module-level client) rather than constructed here, so this module
    doesn't gain a hard dependency on Supabase being configured —
    passing None (the default) just skips logging.
    """
    if supabase_client is None or result is None:
        return
    try:
        supabase_client.table("story_generation_log").insert({
            "module":                    io.get("module", "unknown"),
            "status":                    result.get("status"),
            "intelligence_object":       io,
            "change_trigger_candidates": candidates,
            "deterministic_narrative":   deterministic_narrative,
            "story_result":              result,
            "raw_llm_output":            metadata.get("raw_llm_output"),
            "provider":                  STORY_LLM_CONFIG.get("provider"),
            "model":                     STORY_LLM_CONFIG.get("model"),
            "effort":                    STORY_LLM_CONFIG.get("effort"),
            "system_prompt_hash":        _SYSTEM_PROMPT_HASH,
            "input_tokens":              metadata.get("input_tokens"),
            "output_tokens":             metadata.get("output_tokens"),
            "error_message":             error_message,
        }).execute()
    except Exception as e:
        print(f"[STORY_GEN] Logging to story_generation_log failed: {e}", flush=True)


def generate_story(
    io: dict,
    deterministic_narrative: str | None = None,
    supabase_client=None,
) -> dict:
    """
    io: build_sentinel_intelligence_object() or build_atlas_intelligence_object()
        output. deterministic_narrative: regime_output["narrative"] for
        Sentinel (the old _build_narrative template); None for Atlas,
        which has no equivalent. supabase_client: the caller's existing
        Supabase client, used only to log this call to
        story_generation_log — pass None to skip logging entirely.

    Returns {"status": "paused" | "unavailable" | "ok", ...}. "paused"
    and "unavailable" are deliberately distinct: paused means the
    system itself judged today's read too uncertain (nothing failed);
    unavailable means generation was attempted and failed or produced
    output that didn't pass the grounding check.
    """
    confidence = io.get("confidence")  # None for Atlas today — no gate applies
    candidates: list[dict] = []
    metadata: dict = {}
    error_message: str | None = None
    result: dict | None = None

    try:
        if confidence and confidence.get("briefing_allowed") is False:
            result = {"status": "paused", "message": _build_paused_message(confidence)}
            return result

        candidates = _build_trigger_candidates(io)

        try:
            llm_output = _call_story_llm(io, candidates, deterministic_narrative, metadata)
        except Exception as e:
            print(f"[STORY_GEN] LLM call failed: {e}", flush=True)
            error_message = str(e)
            result = {"status": "unavailable"}
            return result

        if not _validate_grounding(llm_output, candidates):
            error_message = "grounding validation failed"
            result = {"status": "unavailable"}
            return result

        try:
            headline_block = assemble_headline_block(io, llm_output["headline_elaboration"])
        except (ValueError, AssertionError) as e:
            print(f"[STORY_GEN] Headline assembly failed: {e}", flush=True)
            error_message = str(e)
            result = {"status": "unavailable"}
            return result

        result = {
            "status":                  "ok",
            "headline":                headline_block["text"],
            "so_what":                 llm_output["so_what"],
            "what_deserves_attention": llm_output["what_deserves_attention"],
            "change_triggers":         llm_output["change_triggers"],
        }
        return result
    finally:
        _log_story_call(
            supabase_client, io, candidates, deterministic_narrative,
            result, metadata, error_message,
        )
