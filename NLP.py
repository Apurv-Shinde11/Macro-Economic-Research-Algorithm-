"""
NLP.py — IndianMacroNLP
Multi-provider LLM engine with graceful fallback chain.

Provider order (cost-optimised, quality-first):
  1. Google Gemini 2.0 Flash           — best quality, free tier
  2. Google Gemini 2.0 Flash Lite      — Gemini fallback
  3. OpenRouter Llama 3.1 8B (free)    — no IP blocks
  4. OpenRouter Llama 3.2 3B (free)    — smaller fallback
  5. OpenRouter Mistral 7B (free)      — final free fallback
  6. Groq llama-3.3-70b-versatile      — last resort
  7. Keyword engine                     — zero cost, always available
"""

import os
import json
import re
import time

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

# Gemini models tried in order — first working one wins.
# gemini-2.0-flash / gemini-2.0-flash-lite hit 429 on free tier;
# upgrade to a paid Gemini API key to resolve rate limits.
# gemini-1.5-flash / gemini-1.5-flash-8b return 404 (deprecated).
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash-latest",
]

OPENROUTER_MODELS = [
    "openai/gpt-4o-mini",
    "google/gemini-flash-1.5",
    "meta-llama/llama-3.3-70b-instruct:free",
    "openai/gpt-oss-20b:free",
    "mistralai/mistral-7b-instruct:free",
    "google/gemma-3-4b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
]


def _get_key(name):
    val = os.environ.get(name, "")
    if val and "your_" not in str(val) and len(str(val)) > 8:
        return str(val)
    return ""


class IndianMacroNLP:

    def __init__(self):
        self.providers = self._init_providers()

    def _init_providers(self):
        providers = []
        gemini_key     = _get_key("GEMINI_API_KEY")
        openrouter_key = _get_key("OPENROUTER_API_KEY")
        groq_key       = _get_key("GROQ_API_KEY")
        mistral_key    = _get_key("MISTRAL_API_KEY")
        openai_key     = _get_key("OPENAI_API_KEY")

        print(
            f"[NLP] Key check — "
            f"gemini={'found' if gemini_key else 'MISSING'} "
            f"openrouter={'found' if openrouter_key else 'MISSING'} "
            f"groq={'found' if groq_key else 'MISSING'}",
            flush=True
        )

        if gemini_key:
            providers.append({
                "name":   "gemini",
                "key":    gemini_key,
                "models": GEMINI_MODELS,
                "fn":     self._call_gemini
            })
        if openrouter_key:
            providers.append({
                "name":   "openrouter",
                "key":    openrouter_key,
                "models": OPENROUTER_MODELS,
                "fn":     self._call_openrouter
            })
        if groq_key:
            providers.append({
                "name":   "groq",
                "key":    groq_key,
                "models": [
                    "llama-3.3-70b-versatile",  # primary
                    "llama-3.1-8b-instant",     # fast fallback
                ],
                "fn":     self._call_groq
            })
        if mistral_key:
            providers.append({
                "name":  "mistral",
                "key":   mistral_key,
                "model": "mistral-small-latest",
                "fn":    self._call_mistral
            })
        if openai_key:
            providers.append({
                "name":  "openai",
                "key":   openai_key,
                "model": "gpt-4o-mini",
                "fn":    self._call_openai
            })
        return providers

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

    def _build_rbi_prompt(self, news_text: str) -> str:
        return f"""You are analyzing central bank policy signals for Indian financial markets.

NEWS TEXT:
{news_text}

Respond with ONLY valid JSON:
{{
  "rbi_stance": "HAWKISH" or "DOVISH" or "NEUTRAL",
  "rate_direction": "HIKE" or "CUT" or "PAUSE" or "UNKNOWN",
  "liquidity_signal": "TIGHT" or "ACCOMMODATIVE" or "NEUTRAL",
  "confidence": <float 0.0-1.0>,
  "key_phrase": "<most relevant phrase from text or empty>"
}}

Rules:
- Base ONLY on provided text
- If text contains no RBI/central bank content, return all NEUTRAL with confidence 0.3
- Return ONLY JSON, no other text
"""

    def _build_fii_prompt(self, news_text: str) -> str:
        return f"""You are analyzing foreign institutional investor sentiment for Indian markets.

NEWS TEXT:
{news_text}

Respond with ONLY valid JSON:
{{
  "fii_sentiment": "BUYING" or "SELLING" or "NEUTRAL",
  "em_risk_appetite": "RISK_ON" or "RISK_OFF" or "NEUTRAL",
  "india_specific": true or false,
  "confidence": <float 0.0-1.0>,
  "key_phrase": "<most relevant phrase or empty>"
}}

Rules:
- Base ONLY on provided text
- If text has no FII content, return NEUTRAL with confidence 0.3
- Return ONLY JSON, no other text
"""

    def _build_market_prompt(self, news_text: str) -> str:
        return f"""You are analyzing Indian market sentiment from news.

NEWS TEXT:
{news_text}

Respond with ONLY valid JSON:
{{
  "market_sentiment": "BULLISH" or "BEARISH" or "NEUTRAL",
  "dominant_theme": "<2-4 words describing main theme>",
  "equity_bias": "RISK_ON" or "RISK_OFF" or "NEUTRAL",
  "volatility_signal": "HIGH" or "NORMAL" or "LOW",
  "confidence": <float 0.0-1.0>
}}

Rules:
- Base ONLY on provided text
- Return ONLY JSON, no other text
"""

    def get_regime_scores_v2(
        self, news_text: str, hard_signals: dict = None
    ) -> dict:
        """
        Enhanced NLP scoring using three focused calls instead of one monolithic prompt.

        Weights:
          RBI/policy:    40%
          Market news:   35%
          FII narrative: 25%

        Falls back to original get_regime_scores() if all three calls fail.
        hard_signals: dict of signal values for validation (optional).
        """
        results = {}

        # Call 1 — RBI/policy
        try:
            rbi_raw = self.generate_text(self._build_rbi_prompt(news_text))
            if rbi_raw:
                cleaned = re.sub(r'```json|```', '', rbi_raw.strip())
                results['rbi'] = json.loads(cleaned)
                print(f"[NLP_V2] RBI: {results['rbi']}", flush=True)
        except Exception as e:
            print(f"[NLP_V2] RBI call failed: {e}", flush=True)

        # Call 2 — FII narrative
        try:
            fii_raw = self.generate_text(self._build_fii_prompt(news_text))
            if fii_raw:
                cleaned = re.sub(r'```json|```', '', fii_raw.strip())
                results['fii'] = json.loads(cleaned)
                print(f"[NLP_V2] FII: {results['fii']}", flush=True)
        except Exception as e:
            print(f"[NLP_V2] FII call failed: {e}", flush=True)

        # Call 3 — Market sentiment
        try:
            mkt_raw = self.generate_text(self._build_market_prompt(news_text))
            if mkt_raw:
                cleaned = re.sub(r'```json|```', '', mkt_raw.strip())
                results['market'] = json.loads(cleaned)
                print(f"[NLP_V2] Market: {results['market']}", flush=True)
        except Exception as e:
            print(f"[NLP_V2] Market call failed: {e}", flush=True)

        if not results:
            print("[NLP_V2] All calls failed — falling back to v1", flush=True)
            return self.get_regime_scores(news_text)

        return self._aggregate_nlp(results, hard_signals)

    def _aggregate_nlp(
        self, results: dict, hard_signals: dict = None
    ) -> dict:
        """
        Aggregates three NLP results into a single regime score dict
        matching get_regime_scores() output format exactly.
        """
        weights = {'rbi': 0.40, 'market': 0.35, 'fii': 0.25}
        total_weight = 0
        weighted_conf = 0.0
        for key, w in weights.items():
            if key in results:
                c = results[key].get('confidence', 0.5)
                weighted_conf += c * w
                total_weight += w

        final_conf = (
            round(weighted_conf / total_weight, 3)
            if total_weight > 0 else 0.5
        )

        # Equity bias: market is primary, FII is secondary
        equity_bias = 'NEUTRAL'
        if 'market' in results:
            equity_bias = results['market'].get('equity_bias', 'NEUTRAL')
        elif 'fii' in results:
            fii_sent = results['fii'].get('fii_sentiment', 'NEUTRAL')
            if fii_sent == 'BUYING':
                equity_bias = 'RISK_ON'
            elif fii_sent == 'SELLING':
                equity_bias = 'RISK_OFF'

        # RBI policy implication
        rbi_impl = 'PAUSE'
        if 'rbi' in results:
            rbi_impl = results['rbi'].get('rate_direction', 'PAUSE')

        # Sentiment score: -1 to +1
        sent_map = {
            'BULLISH': 0.6, 'BEARISH': -0.6, 'NEUTRAL': 0.0,
            'RISK_ON': 0.5, 'RISK_OFF': -0.5,
        }
        sentiment = 0.0
        if 'market' in results:
            ms = results['market'].get('market_sentiment', 'NEUTRAL')
            sentiment = sent_map.get(ms, 0.0)

        # Dominant theme
        dominant = 'Mixed signals'
        if 'market' in results:
            dominant = results['market'].get('dominant_theme', 'Mixed signals')

        # Validation against hard signals — reject obvious NLP/data conflicts
        if hard_signals:
            vix = hard_signals.get('vix_score', 0.5)
            fii_score = hard_signals.get('fii_score', 0.5)
            # VIX stressed but NLP says RISK_ON — neutralize
            if vix < 0.2 and equity_bias == 'RISK_ON':
                equity_bias = 'NEUTRAL'
                final_conf = round(final_conf * 0.85, 3)
                print(
                    "[NLP_V2] Override: VIX stressed but NLP said RISK_ON — neutralized",
                    flush=True
                )
            # FII strongly selling but NLP says RISK_ON — reduce confidence
            if fii_score < 0.25 and equity_bias == 'RISK_ON':
                final_conf = round(final_conf * 0.90, 3)
                print(
                    "[NLP_V2] Override: FII selling but NLP said RISK_ON — confidence reduced",
                    flush=True
                )

        output = {
            'dominant_theme':         dominant,
            'sentiment_score':        sentiment,
            'regime_type':            (
                'BULLISH' if equity_bias == 'RISK_ON' else
                'BEARISH' if equity_bias == 'RISK_OFF' else
                'NEUTRAL'
            ),
            'growth_intensity':       3,
            'rbi_policy_implication': rbi_impl,
            'equity_bias':            equity_bias,
            'confidence':             final_conf,
            'key_signals':            [],
            'india_specific_risks':   [],
            'global_macro_factors':   [],
            'reasoning':              '',
            'source':                 'llm_v2',
            'provider':               'multi',
            'nlp_source':             'llm_v2',
            'hard_data':              {},
            'rbi_detail':             results.get('rbi', {}),
            'fii_detail':             results.get('fii', {}),
            'market_detail':          results.get('market', {}),
        }

        print(
            f"[NLP_V2] Aggregated: "
            f"conf={final_conf:.3f} "
            f"bias={equity_bias} "
            f"rbi={rbi_impl}",
            flush=True
        )
        return output

    def _call_gemini(self, provider, prompt):
        import urllib.request
        last_error = None
        for model in provider.get("models", GEMINI_MODELS):
            try:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent?key={provider['key']}"
                )
                payload = json.dumps({
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature":      0.3,
                        "maxOutputTokens":  800,
                        "responseMimeType": "application/json"
                    }
                }).encode("utf-8")
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                result = self._parse_llm_response(text)
                print(f"[NLP] Gemini success with model: {model}")
                return result
            except Exception as e:
                print(f"[NLP] Gemini model {model} failed: {str(e)[:80]}")
                last_error = e
                continue
        raise last_error or Exception("All Gemini models failed")

    def _call_groq(self, provider, prompt):
        import urllib.request
        last_error = None
        for model in provider.get("models", ["llama-3.3-70b-versatile"]):
            try:
                payload = json.dumps({
                    "model":       model,
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
                text   = data["choices"][0]["message"]["content"]
                result = self._parse_llm_response(text)
                print(f"[NLP] Groq success with model: {model}")
                return result
            except Exception as e:
                print(f"[NLP] Groq model {model} failed: {str(e)[:80]}")
                last_error = e
                continue
        raise last_error or Exception("All Groq models failed")

    def _call_openrouter(self, provider, prompt):
        import requests as _req
        last_error = None
        for model in provider.get("models", OPENROUTER_MODELS):
            try:
                print(f"[NLP] Trying OpenRouter {model}...", flush=True)
                headers = {
                    "Authorization": f"Bearer {provider['key']}",
                    "Content-Type":  "application/json",
                    "HTTP-Referer":  "https://macro-economic-research-algorithm.vercel.app",
                    "X-Title":       "Sentinel Macro Intelligence",
                }
                payload = {
                    "model":       model,
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  800,
                    "temperature": 0.1,
                }
                r = _req.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
                if r.status_code != 200:
                    raise Exception(f"HTTP {r.status_code}: {r.text[:200]}")
                data   = r.json()
                text   = data["choices"][0]["message"]["content"]
                result = self._parse_llm_response(text)
                print(f"[NLP] OpenRouter {model} succeeded", flush=True)
                return result
            except Exception as e:
                print(f"[NLP] OpenRouter {model} failed: {str(e)[:80]}", flush=True)
                last_error = e
                time.sleep(2)
                continue
        raise last_error or Exception("All OpenRouter models failed")

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

    def _parse_llm_response(self, text):
        if not text:
            raise ValueError("Empty response from LLM")
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text)
        text = text.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON found in response: {text[:200]}")
        parsed = json.loads(match.group())
        required = [
            "dominant_theme", "sentiment_score", "regime_type",
            "rbi_policy_implication", "equity_bias", "confidence"
        ]
        missing = [f for f in required if f not in parsed]
        if missing:
            raise ValueError(f"Missing fields: {missing}")
        return parsed

    def _keyword_scores(self, text):
        text_lower = text.lower()
        bull  = sum(1 for w in BULLISH_KEYWORDS if w.lower() in text_lower)
        bear  = sum(1 for w in BEARISH_KEYWORDS if w.lower() in text_lower)
        neut  = sum(1 for w in NEUTRAL_KEYWORDS if w.lower() in text_lower)
        total = bull + bear + neut + 1
        sentiment  = (bull - bear) / total
        regime     = (
            "BULLISH" if sentiment >  0.15 else
            "BEARISH" if sentiment < -0.15 else
            "NEUTRAL"
        )
        confidence = min(0.5, (bull + bear) / 20)
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

    def _merge_outputs(self, llm_output, keyword_output, provider_name):
        merged = {}
        for field in [
            "dominant_theme", "regime_type", "rbi_policy_implication",
            "equity_bias", "key_signals", "india_specific_risks",
            "global_macro_factors", "reasoning"
        ]:
            merged[field] = llm_output.get(field, keyword_output.get(field))
        for field in ["sentiment_score", "confidence", "growth_intensity"]:
            llm_val = float(llm_output.get(field, 0))
            kw_val  = float(keyword_output.get(field, 0))
            merged[field] = round(llm_val * 0.7 + kw_val * 0.3, 3)
        merged["source"]   = "llm+keyword"
        merged["provider"] = provider_name
        return merged

    def get_regime_scores(self, news_text):
        keyword_output = self._keyword_scores(news_text)
        if not news_text or len(news_text.strip()) < 20:
            return self._build_output(keyword_output)
        prompt = self._build_prompt(news_text)
        errors = []
        for provider in self.providers:
            try:
                llm_output = provider["fn"](provider, prompt)
                merged     = self._merge_outputs(llm_output, keyword_output, provider["name"])
                return self._build_output(merged)
            except Exception as e:
                errors.append(f"{provider['name']}: {str(e)[:120]}")
                continue
        if errors:
            print(f"[NLP] All LLM providers failed:\n" + "\n".join(f"  - {e}" for e in errors))
        return self._build_output(keyword_output)

    def generate_text(self, prompt: str) -> str | None:
        """
        Generic method: sends a prompt to the LLM provider chain and returns
        raw text response, or None if all providers fail. Uses the same
        provider order as get_regime_scores. Used by features that need custom
        structured output (e.g. Geopolitical Watch) rather than regime scoring.
        """
        import urllib.request as _urllib

        for provider in self.providers:
            name = provider["name"]
            try:
                print(f"[NLP] Trying {name} for generate_text...", flush=True)

                if name == "gemini":
                    last_err = None
                    for model in provider.get("models", GEMINI_MODELS):
                        try:
                            url = (
                                f"https://generativelanguage.googleapis.com/v1beta/models/"
                                f"{model}:generateContent?key={provider['key']}"
                            )
                            payload = json.dumps({
                                "contents": [{"parts": [{"text": prompt}]}],
                                "generationConfig": {
                                    "temperature": 0.2,
                                    "maxOutputTokens": 1000,
                                },
                            }).encode("utf-8")
                            req = _urllib.Request(
                                url, data=payload,
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with _urllib.urlopen(req, timeout=25) as resp:
                                data = json.loads(resp.read().decode("utf-8"))
                            text = data["candidates"][0]["content"]["parts"][0]["text"]
                            print(f"[NLP] generate_text: {name}/{model} succeeded", flush=True)
                            return text
                        except Exception as _e:
                            print(f"[NLP] generate_text: {name}/{model} failed: {str(_e)[:80]}", flush=True)
                            last_err = _e
                            continue
                    if last_err:
                        raise last_err

                elif name == "openrouter":
                    import requests as _req
                    last_err = None
                    for model in provider.get("models", OPENROUTER_MODELS):
                        try:
                            r = _req.post(
                                "https://openrouter.ai/api/v1/chat/completions",
                                headers={
                                    "Authorization": f"Bearer {provider['key']}",
                                    "Content-Type":  "application/json",
                                    "HTTP-Referer":  "https://macro-economic-research-algorithm.vercel.app",
                                    "X-Title":       "Sentinel Macro Intelligence",
                                },
                                json={
                                    "model":       model,
                                    "messages":    [{"role": "user", "content": prompt}],
                                    "max_tokens":  1000,
                                    "temperature": 0.1,
                                },
                                timeout=30,
                            )
                            if r.status_code != 200:
                                raise Exception(f"HTTP {r.status_code}: {r.text[:100]}")
                            text = r.json()["choices"][0]["message"]["content"]
                            print(f"[NLP] generate_text: {name}/{model} succeeded", flush=True)
                            return text
                        except Exception as _e:
                            print(f"[NLP] generate_text: {name}/{model} failed: {str(_e)[:80]}", flush=True)
                            last_err = _e
                            time.sleep(1)
                            continue
                    if last_err:
                        raise last_err

                else:
                    # groq, mistral, openai — all use OpenAI-compatible format
                    api_urls = {
                        "groq":    "https://api.groq.com/openai/v1/chat/completions",
                        "mistral": "https://api.mistral.ai/v1/chat/completions",
                        "openai":  "https://api.openai.com/v1/chat/completions",
                    }
                    api_url = api_urls.get(name)
                    if not api_url:
                        continue
                    models = (
                        provider.get("models")
                        or ([provider["model"]] if provider.get("model") else [])
                    )
                    last_err = None
                    for model in models:
                        try:
                            payload = json.dumps({
                                "model":       model,
                                "messages":    [{"role": "user", "content": prompt}],
                                "temperature": 0.2,
                                "max_tokens":  1000,
                            }).encode("utf-8")
                            req = _urllib.Request(
                                api_url, data=payload,
                                headers={
                                    "Content-Type":  "application/json",
                                    "Authorization": f"Bearer {provider['key']}",
                                },
                                method="POST",
                            )
                            with _urllib.urlopen(req, timeout=25) as resp:
                                data = json.loads(resp.read().decode("utf-8"))
                            text = data["choices"][0]["message"]["content"]
                            print(f"[NLP] generate_text: {name}/{model} succeeded", flush=True)
                            return text
                        except Exception as _e:
                            print(f"[NLP] generate_text: {name}/{model} failed: {str(_e)[:80]}", flush=True)
                            last_err = _e
                            continue
                    if last_err:
                        raise last_err

            except Exception as e:
                print(f"[NLP] generate_text: {name} failed: {str(e)[:80]}", flush=True)
                continue

        print("[NLP] generate_text: all providers failed", flush=True)
        return None

    def _build_output(self, scores):
        return {
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
            "source":                 scores.get("source",    "keyword"),
            "provider":               scores.get("provider",  "none"),
            "hard_data": {
                "repo_rate":      5.25,
                "fiscal_deficit": 4.9,
                "capex_lakh_cr":  12.2,
                "gdp_growth":     7.2,
                "cpi":            4.8
            }
        }