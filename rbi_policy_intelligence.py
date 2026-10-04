"""Deterministic, source-grounded RBI MPC intelligence for fresh EconIQ runs."""

import re
from datetime import datetime, timezone


_DRIVER_RULES = (
    ("INFLATION", re.compile(r"\b(inflation|cpi|price pressures?|disinflation)\b", re.I)),
    ("GROWTH", re.compile(r"\b(growth|gdp|economic activity|demand)\b", re.I)),
    ("LIQUIDITY", re.compile(r"\b(liquidity|surplus|deficit|durable liquidity)\b", re.I)),
    ("FX_INR", re.compile(r"\b(inr|rupee|exchange rate|foreign exchange)\b", re.I)),
    ("GLOBAL_RATES", re.compile(r"\b(global rates?|fed funds|central banks?|monetary policy abroad)\b", re.I)),
    ("COMMODITIES", re.compile(r"\b(commodity|crude oil|oil prices?)\b", re.I)),
    ("FOOD_INFLATION", re.compile(r"\b(food inflation|food prices?|vegetable prices?|monsoon)\b", re.I)),
    ("FINANCIAL_CONDITIONS", re.compile(r"\b(financial conditions?|credit conditions?|borrowing costs?)\b", re.I)),
    ("EXTERNAL_RISKS", re.compile(r"\b(external risks?|geopolitical|global uncertainty|trade uncertainty)\b", re.I)),
)

_TONE_RULES = (
    ("inflation_vigilance", 0.45, re.compile(r"\b(close vigil|remain resolute in its commitment to align inflation|risks? of higher inflation have (?:amplified|increased)|upside (?:risks?|pressure) (?:to|on) (?:headline )?inflation)\b", re.I)),
    ("inflation_persistence", 0.35, re.compile(r"\b(higher inflation is expected to rise|inflationary pressures? (?:remain|persist)|inflation risks? remain elevated)\b", re.I)),
    ("durable_disinflation", -0.35, re.compile(r"\b(underlying inflation pressures? continue to remain benign|core inflation (?:remains|is) (?:benign|moderate)|durable disinflation)\b", re.I)),
    ("growth_support", -0.35, re.compile(r"\b(policy (?:is )?supportive of growth|support growth through (?:an )?accommodative|accommodative policy stance)\b", re.I)),
    ("policy_patience", 0.15, re.compile(r"\b(prudent to wait for greater clarity|before taking any policy action|data-dependent and closely monitor)\b", re.I)),
)

_RATE_RE = re.compile(
    r"(?:policy\s+)?repo\s+rate[^;!?]{0,120}?\b(\d{1,2}(?:\.\d{1,2})?)\s*(?:per cent|%)",
    re.I,
)
_PERCENT_RE = re.compile(r"\b(\d{1,2}(?:\.\d{1,2})?)\s*(?:per cent|%)", re.I)
_FY_RE = re.compile(r"\b(?:FY\s*)?(20\d{2})\s*[-–/]\s*(\d{2,4})\b", re.I)
_QUARTER_RE = re.compile(r"\bQ([1-4])\s*[: ]\s*(?:(?:FY\s*)?(20\d{2})\s*[-–/]\s*(\d{2,4})|(?:at\s+)?(\d{1,2}(?:\.\d{1,2})?)\s*(?:per cent|%))", re.I)
_FORECAST_WORD_RE = re.compile(r"\b(projected|projection|forecast|estimated|estimate)\b", re.I)
_HEADING_RE = re.compile(
    r"^(?:growth and inflation outlook(?: global outlook| domestic outlook)?|global outlook|domestic outlook|rationale for monetary policy decisions|monetary policy decisions)\s*\d*\.?$",
    re.I,
)
_SYSTEM_LIQUIDITY_RE = re.compile(
    r"\b(system liquidity|liquidity conditions?|durable liquidity|liquidity (?:surplus|deficit|operations?|injection|absorption)|money[- ]market conditions?|OMO operations?)\b",
    re.I,
)
_LAF_ONLY_RE = re.compile(r"\bliquidity adjustment facility\s*\(\s*LAF\s*\)", re.I)
_TONE_CONTEXT_EXCLUSION_RE = re.compile(
    r"\b(central banks?|Federal Reserve|exports?|imports?|merchandise|global equity markets?|investors|US dollar)\b",
    re.I,
)
_MPC_ATTRIBUTION_RE = re.compile(r"\b(MPC|RBI|Reserve Bank)\b", re.I)


def _sentences(text):
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text or "") if part.strip()]


def _first_sentence(sentences, pattern):
    return next((sentence for sentence in sentences if pattern.search(sentence)), None)


def _policy_rationale_sentences(text):
    marker = "Rationale for Monetary Policy Decisions"
    start = (text or "").find(marker)
    if start >= 0:
        rationale = text[start + len(marker):]
        rationale = re.split(r"The minutes of the MPC", rationale, maxsplit=1, flags=re.I)[0]
        return [sentence for sentence in _sentences(rationale) if _is_substantive_sentence(sentence)]

    sentences = [sentence for sentence in _sentences(text) if _is_substantive_sentence(sentence)]
    policy_context = re.compile(
        r"\b(MPC|RBI|Reserve Bank|monetary policy|policy stance|policy rate|policy action|inflation target)\b",
        re.I,
    )
    return [sentence for sentence in sentences if policy_context.search(sentence)]


def _is_substantive_sentence(sentence):
    words = re.findall(r"\b[\w’'-]+\b", sentence or "")
    return len(words) >= 8 and not _HEADING_RE.fullmatch((sentence or "").strip())


def _normalize_fy(year, end_year):
    return f"FY{year}-{end_year[-2:]}"


def _period_from_sentence(sentence):
    match = _FY_RE.search(sentence or "")
    return _normalize_fy(match.group(1), match.group(2)) if match else None


def _projection_facts(sentences, metric_type):
    projections = []
    for sentence in sentences:
        if not _is_substantive_sentence(sentence) or not _FORECAST_WORD_RE.search(sentence):
            continue

        is_core = metric_type == "CPI" and re.search(r"\bcore inflation\b", sentence, re.I)
        if metric_type == "CPI":
            if not is_core and not re.search(r"\bCPI inflation\b|\bheadline inflation\b|\bInflation for Q[1-4]", sentence, re.I):
                continue
            metric = "CPI_CORE" if is_core else "CPI_HEADLINE"
        else:
            if not re.search(r"\b(?:real )?GDP growth\b", sentence, re.I):
                continue
            metric = "GDP_REAL"

        quarter_projection = re.search(
            r"\bQ([1-4])\s*[: ]\s*(20\d{2})\s*[-–/]\s*(\d{2,4})\s+(?:is\s+)?projected\s+(?:at|to\s+be)\s+(\d{1,2}(?:\.\d{1,2})?)\s*(?:per cent|%)",
            sentence,
            re.I,
        )
        if quarter_projection:
            projections.append({
                "metric": metric,
                "period": f"Q{quarter_projection.group(1)}_{_normalize_fy(quarter_projection.group(2), quarter_projection.group(3))}",
                "value_pct": float(quarter_projection.group(4)),
                "evidence": sentence[:360],
            })
            continue

        # Annual forecast sentences can also enumerate quarter values.
        annual_period = _period_from_sentence(sentence)
        annual_projection = re.search(
            r"\bprojected(?:\s+to\s+be|\s+at)?\s+(\d{1,2}(?:\.\d{1,2})?)\s*(?:per cent|%)",
            sentence,
            re.I,
        )
        if annual_period and annual_projection:
            projections.append({
                "metric": metric,
                "period": annual_period,
                "value_pct": float(annual_projection.group(1)),
                "evidence": sentence[:360],
            })
            fiscal_match = _FY_RE.search(sentence)
            fiscal_period = _normalize_fy(fiscal_match.group(1), fiscal_match.group(2))
            fiscal_start = fiscal_match.group(1)
            for quarter_match in re.finditer(r"\bQ([1-4])\s+at\s+(\d{1,2}(?:\.\d{1,2})?)\s*(?:per cent|%)", sentence, re.I):
                projections.append({
                    "metric": metric,
                    "period": f"Q{quarter_match.group(1)}_{fiscal_period}",
                    "value_pct": float(quarter_match.group(2)),
                    "evidence": sentence[:360],
                })
            # Some RBI releases state next-year Q1 separately in the same paragraph.
            next_year_match = re.search(
                r"\bQ1\s*[: ]\s*(20\d{2})\s*[-–/]\s*(\d{2,4})\s+(?:is\s+)?projected\s+(?:at|to\s+be)\s+(\d{1,2}(?:\.\d{1,2})?)\s*(?:per cent|%)",
                sentence,
                re.I,
            )
            if next_year_match:
                projections.append({
                    "metric": metric,
                    "period": f"Q1_{_normalize_fy(next_year_match.group(1), next_year_match.group(2))}",
                    "value_pct": float(next_year_match.group(3)),
                    "evidence": sentence[:360],
                })
            continue

        # A separately stated next-year quarter, e.g. "Q1:2027-28 is projected at 5.3".
        quarter_projection = re.search(
            r"\bQ([1-4])\s*[: ]\s*(20\d{2})\s*[-–/]\s*(\d{2,4})\s+(?:is\s+)?projected\s+(?:at|to\s+be)\s+(\d{1,2}(?:\.\d{1,2})?)\s*(?:per cent|%)",
            sentence,
            re.I,
        )
        if quarter_projection:
            projections.append({
                "metric": metric,
                "period": f"Q{quarter_projection.group(1)}_{_normalize_fy(quarter_projection.group(2), quarter_projection.group(3))}",
                "value_pct": float(quarter_projection.group(4)),
                "evidence": sentence[:360],
            })
            continue

        # Generic one-value forecast form; only retain a period explicitly stated in that sentence.
        value_match = re.search(
            r"\bprojected(?:\s+to\s+be|\s+at)?\s+(\d{1,2}(?:\.\d{1,2})?)\s*(?:per cent|%)",
            sentence,
            re.I,
        )
        if value_match:
            projections.append({
                "metric": metric,
                "period": annual_period,
                "value_pct": float(value_match.group(1)),
                "evidence": sentence[:360],
            })
    return projections


def extract_mpc_facts(document, retrieved_at=None):
    """Extract facts only when explicit source text supports them."""
    document = document if isinstance(document, dict) else {}
    text = str(document.get("full_text") or document.get("text") or "")
    sentences = _sentences(text)

    rate_match = None
    rate_sentence = None
    for sentence in sentences:
        rate_match = _RATE_RE.search(sentence)
        if rate_match:
            rate_sentence = sentence
            break
    decision = None
    if rate_sentence:
        if re.search(r"\b(unchanged|maintain|maintained|keep|kept|hold|held)\b", rate_sentence, re.I):
            decision = "PAUSE"
        elif re.search(r"\b(increase|increased|raise|raised|hike)\b", rate_sentence, re.I):
            decision = "HIKE"
        elif re.search(r"\b(reduce|reduced|lower|lowered|cut)\b", rate_sentence, re.I):
            decision = "CUT"

    stance_sentence = _first_sentence(
        sentences,
        re.compile(r"\b(stance of monetary policy|policy stance|withdrawal of accommodation|neutral stance|accommodative stance)\b", re.I),
    )
    stance = None
    if stance_sentence:
        if re.search(r"withdrawal of accommodation|tighten|hawkish", stance_sentence, re.I):
            stance = "WITHDRAWAL_OF_ACCOMMODATION"
        elif re.search(r"accommodative|dovish|support growth", stance_sentence, re.I):
            stance = "ACCOMMODATIVE"
        elif re.search(r"neutral", stance_sentence, re.I):
            stance = "NEUTRAL"

    vote_split = None
    vote_sentence = _first_sentence(sentences, re.compile(r"\b(vot(?:e|ed|ing)|majority|dissent)\b", re.I))
    vote_result = None
    if vote_sentence:
        vote_match = re.search(r"\b(\d+)\s*[-–:]\s*(\d+)\b", vote_sentence)
        if vote_match:
            vote_split = {"for": int(vote_match.group(1)), "against": int(vote_match.group(2))}
        elif re.search(r"\bunanimously\b", vote_sentence, re.I):
            vote_result = "UNANIMOUS"

    inflation_pattern = re.compile(r"\b(CPI|inflation|price pressures?)\b", re.I)
    growth_pattern = re.compile(r"\b(GDP|growth)\b", re.I)
    risk_refs = {
        key: [sentence[:360] for sentence in sentences if pattern.search(sentence)][:5]
        for key, pattern in (
            ("inflation", inflation_pattern),
            ("growth", growth_pattern),
            ("liquidity", _SYSTEM_LIQUIDITY_RE),
            ("global_rates", re.compile(r"\b(central banks?|Federal Reserve|global rates?|policy tightening)\b", re.I)),
            ("global_external", re.compile(r"\b(global|external|geopolitical|trade uncertainty)\b", re.I)),
            ("inr_fx", re.compile(r"\b(INR|rupee|exchange rate|foreign exchange)\b", re.I)),
        )
    }
    rationale_sentences = _policy_rationale_sentences(text)
    forward_guidance = [
        sentence[:360] for sentence in rationale_sentences
        if re.search(
            r"\b(greater clarity.*policy action|close vigil|remain resolute|data-dependent|closely monitor|recalibration of policy rates|policy stance)\b",
            sentence,
            re.I,
        )
    ][:5]
    dissent = _first_sentence(sentences, re.compile(r"\b(dissent|voted against|vote against)\b", re.I))
    next_meeting_sentence = _first_sentence(
        sentences,
        re.compile(r"\bnext meeting of the MPC is scheduled for\b", re.I),
    )
    next_meeting_match = re.search(
        r"\bnext meeting of the MPC is scheduled for\s+(.+?)(?:\.|$)",
        next_meeting_sentence or "",
        re.I,
    )
    explicit_change = None
    if rate_sentence:
        bps_match = re.search(r"\bby\s+(\d+)\s*(?:basis points|bps)\b", rate_sentence, re.I)
        if bps_match:
            amount = int(bps_match.group(1))
            explicit_change = amount if decision == "HIKE" else -amount if decision == "CUT" else 0 if decision == "PAUSE" else None

    retrieved = retrieved_at or document.get("retrieved_at")
    return {
        "meeting_date": document.get("meeting_date") or document.get("date"),
        "meeting_id": document.get("meeting_id"),
        "source_reference": document.get("prid"),
        "policy_date": document.get("policy_date") or document.get("date"),
        "repo_rate_pct": float(rate_match.group(1)) if rate_match else None,
        "repo_rate_change_bps": explicit_change,
        "repo_rate_evidence": rate_sentence[:360] if rate_sentence else None,
        "policy_stance": stance,
        "policy_stance_evidence": stance_sentence[:360] if stance_sentence else None,
        "policy_decision": decision,
        "policy_decision_evidence": rate_sentence[:360] if rate_sentence else None,
        "vote_split": vote_split,
        "vote_result": vote_result,
        "vote_evidence": vote_sentence[:360] if vote_sentence and (vote_split or vote_result) else None,
        "dissent": dissent[:360] if dissent else None,
        "next_meeting_date": next_meeting_match.group(1).strip() if next_meeting_match else None,
        "next_meeting_evidence": next_meeting_sentence[:360] if next_meeting_sentence else None,
        "cpi_projections": _projection_facts(sentences, "CPI"),
        "gdp_projections": _projection_facts(sentences, "GDP"),
        "liquidity_references": risk_refs["liquidity"],
        "inflation_risk_references": risk_refs["inflation"],
        "growth_risk_references": risk_refs["growth"],
        "global_rate_references": risk_refs["global_rates"],
        "global_external_risk_references": risk_refs["global_external"],
        "inr_fx_references": risk_refs["inr_fx"],
        "forward_guidance": forward_guidance,
        "source": {
            "name": document.get("source"),
            "url": document.get("url"),
            "reference": document.get("prid"),
            "acquisition": "LIVE" if document.get("fetched") else "FALLBACK",
            "observed_at": document.get("meeting_date") or document.get("date"),
            "published_at": document.get("published_at"),
            "retrieved_at": retrieved,
        },
        "evidence_available": bool(text),
    }


def _tone_analysis(text, is_live, facts=None):
    facts = facts or {}
    sentences = _policy_rationale_sentences(text)
    evidence = []
    for sentence in sentences:
        if _TONE_CONTEXT_EXCLUSION_RE.search(sentence) and not _MPC_ATTRIBUTION_RE.search(sentence):
            continue
        for theme, weight, pattern in _TONE_RULES:
            match = pattern.search(sentence)
            if match:
                evidence.append({
                    "theme": theme,
                    "direction": "NEUTRAL" if theme == "policy_patience" else "HAWKISH" if weight > 0 else "DOVISH",
                    "weight": weight,
                    "excerpt": sentence[:360],
                    "matched_phrase": match.group(0),
                })
    raw_score = (
        round(max(-1.0, min(1.0, sum(item["weight"] for item in evidence) / len(evidence))), 2)
        if evidence else None
    )
    raw_label = "UNKNOWN" if raw_score is None else "HAWKISH" if raw_score >= 0.2 else "DOVISH" if raw_score <= -0.2 else "NEUTRAL"
    score = raw_score
    confidence = min(0.9, 0.35 + 0.12 * len(evidence)) if evidence else 0.0
    if not is_live:
        confidence = round(confidence * 0.5, 2)

    stance = facts.get("policy_stance")
    decision = facts.get("policy_decision")
    anchor_alignment = "ALIGNED"
    anchor_note = None
    conflicts = (
        ((stance == "NEUTRAL" or (stance is None and decision == "PAUSE")) and raw_label in {"HAWKISH", "DOVISH"})
        or (stance == "ACCOMMODATIVE" and raw_label == "HAWKISH")
        or (stance == "WITHDRAWAL_OF_ACCOMMODATION" and raw_label == "DOVISH")
    )
    if conflicts:
        anchor_alignment = "MIXED"
        anchor_description = (
            f"explicit {stance.lower().replace('_', ' ')} stance"
            if stance else "explicit unchanged repo decision"
        )
        anchor_note = (
            f"Phrase-derived {raw_label.lower()} language differs from the {anchor_description}; "
            f"the repo decision is {decision or 'unknown'} and does not mechanically determine tone."
        )
        score = round(raw_score * 0.55, 2)
        confidence = round(confidence * 0.7, 2)
    elif not evidence and stance == "NEUTRAL":
        score = 0.0
        raw_label = "NEUTRAL"
        confidence = 0.25 if is_live else 0.12
    label = "UNKNOWN" if score is None else "HAWKISH" if score >= 0.2 else "DOVISH" if score <= -0.2 else "NEUTRAL"
    return {
        "score": score,
        "label": label,
        "confidence": round(confidence, 2),
        "evidence": evidence,
        "method": "rbi_policy_rationale_rules_with_fact_anchors_v1",
        "confidence_basis": "Heuristic from policy-rationale evidence count, live-source status, and explicit stance alignment; not a calibrated probability.",
        "anchor_alignment": anchor_alignment,
        "anchor_note": anchor_note,
        "anchors": {
            "policy_decision": decision,
            "repo_rate_change_bps": facts.get("repo_rate_change_bps"),
            "policy_stance": stance,
            "forward_guidance_available": bool(facts.get("forward_guidance")),
            "forward_guidance": facts.get("forward_guidance", []),
        },
    }


def _driver_analysis(text):
    drivers = []
    sentences = _sentences(text)
    for driver, pattern in _DRIVER_RULES:
        evidence = [
            sentence[:360]
            for sentence in sentences
            if _is_substantive_sentence(sentence)
            and pattern.search(sentence)
            and (driver != "LIQUIDITY" or _SYSTEM_LIQUIDITY_RE.search(sentence))
        ][:3]
        if not evidence:
            continue
        joined = " ".join(evidence)
        direction = "MENTIONED"
        if driver in {"INFLATION", "FOOD_INFLATION"}:
            if re.search(r"upside|elevated|persistent|pressure|risk", joined, re.I):
                direction = "UPWARD_PRESSURE"
            elif re.search(r"moderated|easing|declining|disinflation|softening", joined, re.I):
                direction = "DISINFLATIONARY"
        elif driver == "GROWTH":
            if re.search(r"strong|resilient|robust|momentum", joined, re.I):
                direction = "RESILIENT"
            elif re.search(r"slow|weak|downside|moderate|uncertain", joined, re.I):
                direction = "DOWNSIDE_RISK"
        elif re.search(r"pressure|risk|uncertain|tight|deficit|depreciat", joined, re.I):
            direction = "PRESSURING"
        elif re.search(r"ease|surplus|support|improv", joined, re.I):
            direction = "SUPPORTIVE"
        importance = "HIGH" if re.search(r"significant|elevated|upside|persistent|material|major", joined, re.I) else "MEDIUM"
        drivers.append({
            "driver": driver,
            "direction": direction,
            "importance": importance,
            "evidence": evidence,
            "confidence": 0.72 if importance == "HIGH" else 0.58,
        })
    return drivers


def _projection_comparisons(current, previous):
    previous_by_key = {
        (row.get("metric"), row.get("period")): row
        for row in previous or []
        if row.get("metric") and row.get("period")
    }
    comparisons = []
    for current_row in current or []:
        key = (current_row.get("metric"), current_row.get("period"))
        previous_row = previous_by_key.get(key)
        if not previous_row or current_row.get("value_pct") is None or previous_row.get("value_pct") is None:
            continue
        delta = round(current_row["value_pct"] - previous_row["value_pct"], 2)
        comparisons.append({
            "metric": key[0],
            "period": key[1],
            "current_pct": current_row["value_pct"],
            "previous_pct": previous_row["value_pct"],
            "direction": "UP" if delta > 0 else "DOWN" if delta < 0 else "UNCHANGED",
            "delta_pp": delta,
        })
    return comparisons


def _projection_summary(comparisons, metric):
    annual = next((item for item in comparisons if item["metric"] == metric and item["period"] == "FY2026-27"), None)
    if not annual:
        return "UNKNOWN", None
    return annual["direction"], annual["delta_pp"]


def _meeting_month(meeting_id):
    match = re.fullmatch(r"(\d{4})-(\d{2})", str(meeting_id or ""))
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return year * 12 + month


def _comparison(current_facts, previous_facts, current_tone, previous_tone):
    same_meeting = bool(
        previous_facts
        and (
            (current_facts.get("meeting_id") and current_facts.get("meeting_id") == previous_facts.get("meeting_id"))
            or (current_facts.get("source_reference") and current_facts.get("source_reference") == previous_facts.get("source_reference"))
            or (current_facts.get("meeting_date") and current_facts.get("meeting_date") == previous_facts.get("meeting_date"))
        )
    )
    current_month = _meeting_month(current_facts.get("meeting_id"))
    previous_month = _meeting_month((previous_facts or {}).get("meeting_id"))
    meeting_gap = current_month - previous_month if current_month and previous_month else None
    if (
        not previous_facts
        or not previous_facts.get("evidence_available")
        or same_meeting
        or meeting_gap is None
        or meeting_gap < 1
        or meeting_gap > 3
    ):
        return {
            "status": "UNAVAILABLE",
            "reason": "A consecutive previous MPC source could not be confirmed.",
            "previous_meeting_date": None,
            "repo": "UNKNOWN",
            "stance": "UNKNOWN",
            "inflation_projection": "UNKNOWN",
            "growth_projection": "UNKNOWN",
            "inflation_projection_delta_pp": None,
            "growth_projection_delta_pp": None,
            "projection_deltas": [],
            "language": "UNKNOWN",
            "risk_emphasis": [],
        }
    current_rate = current_facts.get("repo_rate_pct")
    previous_rate = previous_facts.get("repo_rate_pct")
    repo_delta = "UNKNOWN"
    if current_rate is not None and previous_rate is not None:
        repo_delta = "UNCHANGED" if current_rate == previous_rate else "HIKE" if current_rate > previous_rate else "CUT"

    stance_delta = "UNKNOWN"
    if current_facts.get("policy_stance") and previous_facts.get("policy_stance"):
        stance_rank = {
            "ACCOMMODATIVE": -1,
            "NEUTRAL": 0,
            "WITHDRAWAL_OF_ACCOMMODATION": 1,
        }
        current_stance = stance_rank.get(current_facts["policy_stance"])
        previous_stance = stance_rank.get(previous_facts["policy_stance"])
        if current_stance is not None and previous_stance is not None:
            stance_delta = (
                "UNCHANGED" if current_stance == previous_stance
                else "MORE HAWKISH" if current_stance > previous_stance
                else "MORE DOVISH"
            )
        elif current_facts["policy_stance"] == previous_facts["policy_stance"]:
            stance_delta = "UNCHANGED"
    language_delta = "UNKNOWN"
    if current_tone["score"] is not None and previous_tone["score"] is not None:
        diff = current_tone["score"] - previous_tone["score"]
        language_delta = "MORE HAWKISH" if diff >= 0.2 else "MORE DOVISH" if diff <= -0.2 else "BROADLY UNCHANGED"

    def drivers_from_facts(facts):
        references = []
        for key in ("inflation_risk_references", "growth_risk_references", "liquidity_references", "global_rate_references", "global_external_risk_references", "inr_fx_references"):
            references.extend(facts.get(key, []))
        return {item["driver"] for item in _driver_analysis(" ".join(references))}

    projection_deltas = _projection_comparisons(
        current_facts.get("cpi_projections", []) + current_facts.get("gdp_projections", []),
        previous_facts.get("cpi_projections", []) + previous_facts.get("gdp_projections", []),
    )
    inflation_projection, inflation_delta = _projection_summary(projection_deltas, "CPI_HEADLINE")
    growth_projection, growth_delta = _projection_summary(projection_deltas, "GDP_REAL")
    risk_emphasis = sorted(drivers_from_facts(current_facts) - drivers_from_facts(previous_facts))
    return {
        "status": "AVAILABLE",
        "reason": None,
        "previous_meeting_date": previous_facts.get("meeting_date"),
        "repo": repo_delta,
        "stance": stance_delta,
        "inflation_projection": inflation_projection,
        "inflation_projection_delta_pp": inflation_delta,
        "growth_projection": growth_projection,
        "growth_projection_delta_pp": growth_delta,
        "projection_deltas": projection_deltas,
        "language": language_delta,
        "risk_emphasis": risk_emphasis,
    }


_TRANSMISSION_ASSETS = (
    "short_duration_rates", "long_duration_bonds", "yield_curve", "banks",
    "nbfc_hfc", "rate_sensitive_equities", "broader_equities", "inr", "gold", "credit_conditions",
)


def _transmission(tone):
    tone_label = tone.get("label", "UNKNOWN")
    if tone_label == "UNKNOWN":
        return {asset: {"direction": "UNKNOWN", "explanation": None} for asset in _TRANSMISSION_ASSETS}
    if tone.get("anchor_alignment") == "MIXED":
        explanation = tone.get("anchor_note") or "Policy-language evidence is mixed with the explicit MPC policy stance."
        return {asset: {"direction": "MIXED", "explanation": explanation} for asset in _TRANSMISSION_ASSETS}
    if tone_label == "HAWKISH":
        directions = {
            "short_duration_rates": ("PRESSURING", "A firmer policy tone can keep near-term rate expectations elevated."),
            "long_duration_bonds": ("MILDLY PRESSURING", "Higher-for-longer expectations can limit long-bond price gains."),
            "yield_curve": ("MIXED", "A firm near-term policy path can anchor short yields while long yields also reflect growth and supply."),
            "banks": ("MIXED", "Higher funding costs may pressure margins, while asset repricing can partly offset the effect."),
            "nbfc_hfc": ("MILDLY PRESSURING", "Elevated funding costs can weigh on lender spreads and borrower demand."),
            "rate_sensitive_equities": ("MILDLY PRESSURING", "Higher discount rates can weigh on rate-sensitive valuations."),
            "broader_equities": ("MIXED", "Discount-rate pressure may be offset by the growth outlook; the statement alone does not set an equity direction."),
            "inr": ("MILDLY SUPPORTIVE", "A relatively firm rate stance can support rate differentials, subject to external flows."),
            "gold": ("MILDLY PRESSURING", "Higher rates can raise the opportunity cost of holding gold, while external risks can offset this."),
            "credit_conditions": ("PRESSURING", "A restrictive policy tone can keep system borrowing costs elevated."),
        }
    elif tone_label == "DOVISH":
        directions = {
            "short_duration_rates": ("SUPPORTIVE", "A softer policy tone can pull down near-term rate expectations."),
            "long_duration_bonds": ("MILDLY SUPPORTIVE", "Lower expected policy rates can support bond prices, subject to inflation and supply."),
            "yield_curve": ("MIXED", "Easing expectations affect the front end, while growth and supply still shape long yields."),
            "banks": ("MILDLY SUPPORTIVE", "Lower funding costs can help margins and loan demand, with timing varying by balance sheet."),
            "nbfc_hfc": ("SUPPORTIVE", "Lower funding costs can ease refinancing pressure and support borrower affordability."),
            "rate_sensitive_equities": ("SUPPORTIVE", "Lower discount rates can support rate-sensitive valuations."),
            "broader_equities": ("MILDLY SUPPORTIVE", "Easier financial conditions can support risk appetite, subject to growth and earnings."),
            "inr": ("MILDLY PRESSURING", "Lower relative rates can narrow carry support for the rupee, subject to capital flows."),
            "gold": ("MILDLY SUPPORTIVE", "Lower rates can reduce gold's opportunity cost, while currency and risk factors also matter."),
            "credit_conditions": ("SUPPORTIVE", "A softer policy path can reduce marginal borrowing costs over time."),
        }
    else:
        return {asset: {"direction": "NEUTRAL", "explanation": "The extracted policy-language evidence does not support a directional transmission call."} for asset in _TRANSMISSION_ASSETS}
    return {asset: {"direction": direction, "explanation": explanation} for asset, (direction, explanation) in directions.items()}


def _watch_triggers(facts, drivers):
    drivers_by_name = {item["driver"]: item for item in drivers}
    triggers = []

    def matching_evidence(driver_name, pattern):
        driver = drivers_by_name.get(driver_name, {})
        return next((
            sentence for sentence in driver.get("evidence", [])
            if pattern.search(sentence)
        ), None)

    def add(what, why, change, source_type, evidence=None):
        triggers.append({
            "what_to_watch": what,
            "why_it_matters": why,
            "interpretation_change": change,
            "source_type": source_type,
            "source_evidence": evidence,
        })

    inflation = drivers_by_name.get("INFLATION")
    headline_forecast = next((
        row for row in facts.get("cpi_projections", [])
        if row.get("metric") == "CPI_HEADLINE" and row.get("period") == "FY2026-27"
    ), None)
    inflation_evidence = headline_forecast.get("evidence") if headline_forecast else matching_evidence(
        "INFLATION", re.compile(r"\b(CPI|headline inflation|inflation target)\b", re.I)
    )
    if inflation and inflation_evidence:
        add(
            "Next CPI print",
            "Tests the inflation risks described in the MPC resolution.",
            "A sustained move in headline or core inflation would change the policy-inflation read.",
            "SOURCE_DERIVED",
            inflation_evidence,
        )
    food = drivers_by_name.get("FOOD_INFLATION")
    food_evidence = matching_evidence(
        "FOOD_INFLATION", re.compile(r"\b(food inflation|food prices?|price pressures?|monsoon)\b", re.I)
    )
    if food and food_evidence:
        add(
            "Food inflation",
            "The resolution links food-price pressure and monsoon risk to the inflation outlook.",
            "A reversal in food disinflation or worsening supply risk would alter the inflation assessment.",
            "SOURCE_DERIVED",
            food_evidence,
        )
    commodities = drivers_by_name.get("COMMODITIES")
    crude_evidence = matching_evidence("COMMODITIES", re.compile(r"\b(crude|oil prices?)\b", re.I))
    if commodities and crude_evidence:
        add(
            "Crude oil",
            "The resolution identifies volatile oil prices as an inflation and growth risk.",
            "A sustained oil-price shock would strengthen the imported-inflation risk.",
            "SOURCE_DERIVED",
            crude_evidence,
        )
    if facts.get("next_meeting_date"):
        add(
            f"Next MPC · {facts['next_meeting_date']}",
            "The date is stated in the RBI resolution.",
            "The next resolution can confirm or revise the current stance and guidance.",
            "SOURCE_DERIVED",
            facts.get("next_meeting_evidence"),
        )

    add(
        "System liquidity",
        "EconIQ monitoring item; this resolution did not establish current system-liquidity conditions.",
        "A durable shift between surplus and deficit would affect transmission, but is not inferred from LAF wording.",
        "ECONIQ_MONITORING",
    )
    add(
        "INR",
        "EconIQ monitoring item; the retrieved resolution contains no explicit INR or exchange-rate reference.",
        "A sustained currency move could affect imported inflation, but is not attributed to this MPC text.",
        "ECONIQ_MONITORING",
    )
    return triggers


def _primary_reason(text):
    sentences = _policy_rationale_sentences(text)
    if not sentences:
        return "The policy rationale was not sufficiently extracted from the retrieved source."

    def strength(sentence):
        score = 0
        if re.search(r"\bMPC\b", sentence, re.I):
            score += 3
        if re.search(r"\b(close vigil|remain resolute|inflation target)\b", sentence, re.I):
            score += 5
        if re.search(r"\b(policy action|policy rates?|greater clarity)\b", sentence, re.I):
            score += 3
        if re.search(r"\b(inflation|growth)\b", sentence, re.I):
            score += 1
        if _HEADING_RE.fullmatch(sentence.strip()):
            return -100
        return score

    candidate = max(sentences, key=strength)
    if strength(candidate) <= 0:
        return "The policy rationale was not sufficiently extracted from the retrieved source."
    return candidate[:360]


def build_rbi_policy_intelligence(current_document, previous_document=None, retrieved_at=None):
    """Build RBI intelligence without mutating or feeding EconIQ model outputs."""
    current_document = current_document if isinstance(current_document, dict) else {}
    previous_document = previous_document if isinstance(previous_document, dict) else None
    current_text = str(current_document.get("full_text") or current_document.get("text") or "")
    is_live = bool(current_document.get("fetched") and current_text)
    evidence_document = current_document if is_live else {
        key: current_document.get(key)
        for key in ("date", "meeting_date", "meeting_id", "prid", "source", "url", "retrieved_at")
    }
    facts = extract_mpc_facts(evidence_document, retrieved_at)
    if not previous_document or not previous_document.get("fetched"):
        previous_document = None
    previous_facts = extract_mpc_facts(previous_document) if previous_document else None
    if (
        previous_facts
        and facts.get("repo_rate_pct") is not None
        and previous_facts.get("repo_rate_pct") is not None
    ):
        facts["repo_rate_change_bps"] = int(round(
            (facts["repo_rate_pct"] - previous_facts["repo_rate_pct"]) * 100
        ))
    tone = _tone_analysis(current_text, is_live, facts)
    previous_text = str((previous_document or {}).get("full_text") or (previous_document or {}).get("text") or "")
    previous_tone = _tone_analysis(
        previous_text,
        bool((previous_document or {}).get("fetched") and previous_text),
        previous_facts or {},
    )
    comparison = _comparison(facts, previous_facts, tone, previous_tone)
    drivers = _driver_analysis(current_text)

    label = tone["label"]
    posture = {
        "HAWKISH": "CAUTIOUSLY HAWKISH",
        "DOVISH": "CAUTIOUSLY DOVISH",
        "NEUTRAL": "NEUTRAL",
        "UNKNOWN": "UNDETERMINED",
    }[label]
    primary_reason = _primary_reason(current_text)
    if facts["policy_decision"] == "HIKE":
        implication = "The extracted policy decision is a rate increase; monitor transmission to funding costs and demand."
    elif facts["policy_decision"] == "CUT":
        implication = "The extracted policy decision is a rate reduction; monitor how quickly funding conditions respond."
    elif facts["policy_decision"] == "PAUSE":
        implication = "The policy rate was held; the source language and incoming inflation data shape the next move."
    else:
        implication = "No policy-rate implication assigned because the decision was not extracted confidently."
    change_parts = []
    if comparison["status"] == "AVAILABLE":
        if comparison["repo"] != "UNKNOWN":
            change_parts.append(f"repo {comparison['repo'].lower()}")
        if comparison["stance"] != "UNKNOWN":
            change_parts.append(f"stance {comparison['stance'].lower()}")
        if comparison["inflation_projection"] != "UNKNOWN":
            change_parts.append(
                f"inflation forecast revised {comparison['inflation_projection'].lower()}"
                + (f" {abs(comparison['inflation_projection_delta_pp']):.1f} pp" if comparison.get("inflation_projection_delta_pp") is not None else "")
            )
        if comparison["growth_projection"] != "UNKNOWN":
            change_parts.append(
                f"growth forecast revised {comparison['growth_projection'].lower()}"
                + (f" {abs(comparison['growth_projection_delta_pp']):.1f} pp" if comparison.get("growth_projection_delta_pp") is not None else "")
            )
        if comparison["language"] != "UNKNOWN":
            change_parts.append(f"policy language {comparison['language'].lower()}")
    change_summary = (
        "; ".join(change_parts) + "."
        if change_parts
        else "Previous-meeting comparison unavailable from the retrieved source material."
    )
    interpretation = {
        "posture": posture,
        "primary_reason": primary_reason,
        "change_from_previous": change_summary,
        "policy_implication": implication,
    }
    return {
        "version": "1.0",
        "facts": facts,
        "tone": tone,
        "comparison": comparison,
        "drivers": drivers,
        "interpretation": interpretation,
        "transmission": _transmission(tone),
        "watch_triggers": _watch_triggers(facts, drivers),
        "provenance": {
            "source_type": "PRIMARY" if is_live else "FALLBACK",
            "acquisition": "LIVE" if is_live else "FALLBACK",
            "retrieved_at": retrieved_at or current_document.get("retrieved_at") or datetime.now(timezone.utc).isoformat(),
            "fallback_reason": None if is_live else "Official MPC source text was unavailable; no live policy facts are asserted.",
            "comparison_source_available": bool(previous_facts and previous_facts.get("evidence_available")),
        },
    }