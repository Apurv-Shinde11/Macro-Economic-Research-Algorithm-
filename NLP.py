"""
NLP.py — IndianMacroNLP
Multi-provider LLM engine with graceful fallback chain.

Provider order (cost-optimised, quality-first):
  1. Google Gemini 1.5 Flash  — best quality, free tier
  2. Groq llama-3.3-70b       — fast, free tier (current working)
  3. Mistral Small             — free tokens fallback
  4. Keyword engine            — zero cost, always available

Source field values:
  "llm+keyword"  — LLM fired and merged with keyword scores
  "keyword"      — All LLMs failed, keyword engine only
"""

import os
import json
import re
import streamlit as st


# =========================
# 📰 KEYWORD BASELINE
# =========================
BULLISH_KEYWORDS = [
    "rally", "surge", "growth", "recovery", "expansion", "capex",
    "investment", "profit", "earnings", "beat", "upgrade", "inflow",
    "FII buying", "rate cut", "dovish", "liquidity", "optimism",
    "breakout", "outperform", "strong", "robust", "momentum"
]
BEARISH_KEYWORDS = [
    "crash", "fall", "decline", "recession", "slowdown", "inflation",
    "hike", "tightening", "outflow", "sell-off", "weak", "miss",
    "downgrade", "loss", "risk", "uncertainty", "volatility",
    "contagion", "default", "crisis", "pressure", "war", "sanctions"
]
NEUTRAL_KEYWORDS = [
    "stable", "hold", "pause", "watch", "mixed", "range-bound",
    "sideways", "flat", "unchanged", "neutral", "consolidation"
]


class IndianMacroNLP:

    def __init__(self):
        self.providers = self._init_providers()

    # =========================
    # 🔧 PROVIDER INIT
    # =========================
    def _init_providers(self):
        """
        Builds the ordered provider list from available secrets.
        Only registers providers whose keys are present and valid.
        """
        providers = []

        def _get_key(name):
            try:
                val = st.secrets.get(name, "")
                if val and "your_" not in str(val) and len(str(val)) > 8:
                    return str(val)
            except Exception:
                pass
            return os.environ.get(name, "")

        gemini_key  = _get_key("GEMINI_API_KEY")
        groq_key    = _get_key("GROQ_API_KEY")
        mistral_key = _get_key("MISTRAL_API_KEY")
        openai_key  = _get_key("OPENAI_API_KEY")

        # Priority 1 — Gemini 1.5 Flash (best quality, free)
        if gemini_key:
            providers.append({
                "name":     "gemini",
                "key":      gemini_key,
                "model":    "gemini-1.5-flash",
                "fn":       self._call_gemini
            })

        # Priority 2 — Groq (current working, fast, free)
        if groq_key:
            providers.append({
                "name":     "groq",
                "key":      groq_key,
                "model":    "llama-3.3-70b-versatile",
                "fn":       self._call_groq
            })

        # Priority 3 — Mistral (free tokens)
        if mistral_key:
            providers.append({
                "name":     "mistral",
                "key":      mistral_key,
                "model":    "mistral-small-latest",
                "fn":       self._call_mistral
            })

        # Priority 4 — OpenAI (paid, last resort)
        if openai_key:
            providers.append({
                "name":     "openai",
                "key":      openai_key,
                "model":    "gpt-4o-mini",
                "fn":       self._call_openai
            })

        return providers

    # =========================
    # 📝 PROMPT BUILDER
    # =========================
    def _build_prompt(self, news_text):
        return f"""You are an expert Indian macroeconomic analyst.

Analyse the following news headlines and return ONLY a valid JSON object.

Headlines:
{news_text[:2000]}

Return ONLY this JSON structure, no other text:
{{
  "dominant_theme": "2-4 word theme (e.g. Liquidity Support, Inflation Risk)",
  "sentiment_score": <float between -1.0 and 1.0>,
  "regime_type": "one of: BULLISH/BEARISH/NEUTRAL/WATCH",
  "growth_intensity": <integer 1-5>,
  "rbi_policy_implication": "one of: CUT/HIKE/PAUSE/UNKNOWN",
  "equity_bias": "one of: RISK_ON/RISK_OFF/NEUTRAL",
  "confidence": <float between 0.0 and 1.0>,
  "key_signals": ["signal 1", "signal 2", "signal 3"],
  "india_specific_risks": ["risk 1", "risk 2"],
  "global_macro_factors": ["factor 1", "factor 2"],
  "reasoning": "2-3 sentence explanation of your analysis"
}}"""

    # =========================
    # 🤖 PROVIDER CALL FUNCTIONS
    # =========================
    def _call_gemini(self, provider, prompt):
        import urllib.request
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{provider['model']}:generateContent?key={provider['key']}"
        )
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature":    0.3,
                "maxOutputTokens":800,
                "responseMimeType":"application/json"
            }
        }).encode("utf-8")

        req  = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        text = (
            data["candidates"][0]["content"]["parts"][0]["text"]
        )
        return self._parse_llm_response(text)

    def _call_groq(self, provider, prompt):
        import urllib.request
        payload = json.dumps({
            "model":       provider["model"],
            "messages":    [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens":  800
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {provider['key']}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        text = data["choices"][0]["message"]["content"]
        return self._parse_llm_response(text)

    def _call_mistral(self, provider, prompt):
        import urllib.request
        payload = json.dumps({
            "model":       provider["model"],
            "messages":    [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens":  800
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.mistral.ai/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {provider['key']}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        text = data["choices"][0]["message"]["content"]
        return self._parse_llm_response(text)

    def _call_openai(self, provider, prompt):
        import urllib.request
        payload = json.dumps({
            "model":       provider["model"],
            "messages":    [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens":  800
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {provider['key']}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        text = data["choices"][0]["message"]["content"]
        return self._parse_llm_response(text)

    # =========================
    # 🔍 RESPONSE PARSER
    # =========================
    def _parse_llm_response(self, text):
        """
        Extracts and validates JSON from LLM response.
        Handles markdown code blocks, leading text, etc.
        """
        if not text:
            raise ValueError("Empty response from LLM")

        # Strip markdown code fences
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*",     "", text)
        text = text.strip()

        # Find JSON object in response
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON found in response: {text[:200]}")

        parsed = json.loads(match.group())

        # Validate required fields
        required = [
            "dominant_theme", "sentiment_score", "regime_type",
            "rbi_policy_implication", "equity_bias", "confidence"
        ]
        missing = [f for f in required if f not in parsed]
        if missing:
            raise ValueError(f"Missing fields: {missing}")

        return parsed

    # =========================
    # 🔑 KEYWORD ENGINE
    # (always available, zero cost)
    # =========================
    def _keyword_scores(self, text):
        text_lower = text.lower()
        bull = sum(1 for w in BULLISH_KEYWORDS if w.lower() in text_lower)
        bear = sum(1 for w in BEARISH_KEYWORDS if w.lower() in text_lower)
        neut = sum(1 for w in NEUTRAL_KEYWORDS if w.lower() in text_lower)
        total = bull + bear + neut + 1

        sentiment = (bull - bear) / total
        regime    = (
            "BULLISH" if sentiment >  0.15 else
            "BEARISH" if sentiment < -0.15 else
            "NEUTRAL"
        )
        confidence = min(0.5, (bull + bear) / 20)

        # Extract key signals from text
        signals = []
        for kw in BULLISH_KEYWORDS + BEARISH_KEYWORDS:
            if kw.lower() in text_lower and kw not in signals:
                signals.append(kw)
                if len(signals) >= 4:
                    break

        return {
            "dominant_theme":         "Keyword-derived signal",
            "sentiment_score":         round(sentiment, 3),
            "regime_type":             regime,
            "growth_intensity":        3 if sentiment > 0 else 2,
            "rbi_policy_implication":  "PAUSE",
            "equity_bias":             (
                "RISK_ON"  if sentiment >  0.1 else
                "RISK_OFF" if sentiment < -0.1 else
                "NEUTRAL"
            ),
            "confidence":              round(confidence, 3),
            "key_signals":             signals[:3],
            "india_specific_risks":    [],
            "global_macro_factors":    [],
            "reasoning":               "LLM unavailable — keyword engine used.",
            "source":                  "keyword",
            "provider":                "none"
        }

    # =========================
    # 🔀 MERGE FUNCTION
    # Blends LLM output with keyword scores
    # =========================
    def _merge_outputs(self, llm_output, keyword_output, provider_name):
        """
        Merges LLM intelligence with keyword baseline.
        LLM fields take priority; keyword fills gaps.
        """
        merged = {}

        # LLM fields take full priority
        for field in [
            "dominant_theme", "regime_type", "rbi_policy_implication",
            "equity_bias", "key_signals", "india_specific_risks",
            "global_macro_factors", "reasoning"
        ]:
            merged[field] = llm_output.get(field, keyword_output.get(field))

        # Blend numeric scores — LLM weighted 70%, keyword 30%
        for field in ["sentiment_score", "confidence", "growth_intensity"]:
            llm_val = float(llm_output.get(field, 0))
            kw_val  = float(keyword_output.get(field, 0))
            merged[field] = round(llm_val * 0.7 + kw_val * 0.3, 3)

        merged["source"]   = "llm+keyword"
        merged["provider"] = provider_name

        return merged

    # =========================
    # 🚀 MAIN ENTRY POINT
    # =========================
    def get_regime_scores(self, news_text):
        """
        Runs the full NLP pipeline.
        Tries each provider in order, falls back to keyword engine.

        Returns a dict with all fields including:
          source:   "llm+keyword" or "keyword"
          provider: provider name or "none"
        """
        keyword_output = self._keyword_scores(news_text)

        if not news_text or len(news_text.strip()) < 20:
            return self._build_output(keyword_output)

        prompt   = self._build_prompt(news_text)
        errors   = []

        for provider in self.providers:
            try:
                llm_output = provider["fn"](provider, prompt)
                merged     = self._merge_outputs(
                    llm_output, keyword_output, provider["name"]
                )
                output = self._build_output(merged)
                return output

            except Exception as e:
                errors.append(f"{provider['name']}: {str(e)[:120]}")
                continue

        # All providers failed — use keyword engine
        if errors:
            print(f"[NLP] All LLM providers failed:\n" +
                  "\n".join(f"  - {e}" for e in errors))

        return self._build_output(keyword_output)

    # =========================
    # 🏗️ OUTPUT BUILDER
    # =========================
    def _build_output(self, scores):
        """
        Wraps scores into the full output dict
        expected by the rest of the pipeline.
        """
        return {
            # Core NLP fields
            "dominant_theme":         scores.get("dominant_theme",        ""),
            "sentiment_score":        scores.get("sentiment_score",        0.0),
            "regime_type":            scores.get("regime_type",            "NEUTRAL"),
            "growth_intensity":       scores.get("growth_intensity",       3),
            "rbi_policy_implication": scores.get("rbi_policy_implication", "PAUSE"),
            "equity_bias":            scores.get("equity_bias",            "NEUTRAL"),
            "confidence":             scores.get("confidence",             0.5),
            "key_signals":            scores.get("key_signals",            []),
            "india_specific_risks":   scores.get("india_specific_risks",   []),
            "global_macro_factors":   scores.get("global_macro_factors",   []),
            "reasoning":              scores.get("reasoning",              ""),

            # Source tracking — used by main.py for badge display
            "source":                 scores.get("source",    "keyword"),
            "provider":               scores.get("provider",  "none"),

            # Hard data block — populated by main.py after this call
            "hard_data": {
                "repo_rate":      6.5,
                "fiscal_deficit": 4.5,
                "capex_lakh_cr":  10.0,
                "gdp_growth":     7.0,
                "cpi":            5.0
            }
        }