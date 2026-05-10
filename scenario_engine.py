from utils import ensure_dict, safe_get, ensure_list, safe_float


class ScenarioEngine:
    def __init__(self):
        pass

    def _score_node(self, node):
        node = ensure_dict(node, "Node")
        pressure_map = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        lag_map      = {"IMMEDIATE": 3, "LAGGED": 2, "STRUCTURAL": 1, "FORWARD": 2}
        pressure   = safe_get(node, "pressure",   "LOW")
        confidence = safe_float(safe_get(node, "confidence", 0.5))
        lag        = safe_get(node, "lag",        "LAGGED")
        return (
            pressure_map.get(pressure, 1) * 0.5 +
            confidence * 2 +
            lag_map.get(lag, 1) * 0.3
        )

    # =========================
    # ✅ ITEM 2 — MARKET STRESS READER
    # Returns (vix, fii_net_crore, crude_price).
    # Crude is injected by main.py via nse_snapshot["crude_price"]
    # from the live ticker. Falls back to $85 (neutral — below
    # the $95 tail risk threshold) if not available.
    # =========================
    def _read_market_stress(self, nse_snapshot):
        nse = nse_snapshot if isinstance(nse_snapshot, dict) else {}

        # VIX
        vix = safe_float(nse.get("india_vix", 0))
        if not vix:
            vix = safe_float(
                nse.get("indices", {}).get("india_vix", {}).get("last", 0)
            )
        if not vix:
            vix = 15.0

        # FII net
        fii_net = safe_float(nse.get("fii_net_crore", 0))
        if not fii_net:
            fii_info = nse.get("fii_dii", {}).get("fii", {})
            fii_net  = safe_float(fii_info.get("net", 0))

        # ✅ ITEM 2 — Crude price from live ticker
        # Injected by main.py: nse_snapshot["crude_price"] = ticker_data["Crude"]["price"]
        # Fallback: $85 (neutral — below the $95 risk threshold)
        crude = safe_float(nse.get("crude_price", 0))
        if not crude:
            crude = 85.0

        return vix, fii_net, crude

    # =========================
    # ✅ ITEM 2 — PROBABILITY ADJUSTER
    # Shifts scenario probabilities based on live
    # VIX, FII flow, and crude price signals.
    #
    # VIX rules:
    #   > 25       → tail +8%,  base -4%,  upside -4%
    #   20–25      → tail +5%,  base -3%,  upside -2%
    #   < 14       → tail -2%,  base +1%,  upside +1%
    #
    # FII rules:
    #   selling > 2000 Cr → upside -4%, base -4%, tail +2%
    #   selling > 1000 Cr → upside -2%, base -2%, tail +1%
    #   buying  > 2000 Cr → upside +4%, base +2%, tail -3%
    #
    # Crude rules (✅ ITEM 2):
    #   > $100    → tail +6%,  base -3%,  upside -3%
    #   > $95     → tail +4%,  base -2%,  upside -2%
    #
    # All adjustments applied before re-normalisation —
    # final probabilities always sum to 1.0.
    # =========================
    def _apply_market_stress_adjustments(self, scenarios, vix, fii_net, crude=85.0):
        adj_meta = {
            "vix":           vix,
            "fii_net_crore": fii_net,
            "crude_price":   crude,
            "vix_signal":    "NEUTRAL",
            "fii_signal":    "NEUTRAL",
            "crude_signal":  "NEUTRAL",
            "adjustments":   []
        }

        # Build name → index map for direct access
        idx = {s["name"]: i for i, s in enumerate(scenarios)}

        # ── SHIFT HELPER ──
        # Must be defined before any call block below.
        def shift(name, delta, reason):
            if name in idx:
                scenarios[idx[name]]["probability"] = max(
                    0.02,
                    safe_float(scenarios[idx[name]].get("probability", 0)) + delta
                )
                if reason:
                    adj_meta["adjustments"].append(f"{name}: {delta:+.2f} ({reason})")

        # ── ✅ ITEM 2 — CRUDE OIL ADJUSTMENTS ──
        # Live crude price from ticker shifts tail risk probability
        # in real-time. Crude > $100 is extreme shock territory;
        # > $95 is the scenario description trigger threshold.
        if crude > 100:
            adj_meta["crude_signal"] = "EXTREME_OIL_SHOCK"
            shift("External Shock / Inflation Risk", +0.06, f"Crude ${crude:.0f} > $100")
            shift("Base Case",                       -0.03, f"Crude ${crude:.0f} > $100")
            shift("Growth Upside",                   -0.03, f"Crude ${crude:.0f} > $100")
        elif crude > 95:
            adj_meta["crude_signal"] = "ELEVATED_OIL"
            shift("External Shock / Inflation Risk", +0.04, f"Crude ${crude:.0f} > $95")
            shift("Base Case",                       -0.02, f"Crude ${crude:.0f} > $95")
            shift("Growth Upside",                   -0.02, f"Crude ${crude:.0f} > $95")
        else:
            adj_meta["crude_signal"] = "NEUTRAL"

        # ── VIX ADJUSTMENTS ──
        if vix > 25:
            adj_meta["vix_signal"] = "EXTREME_STRESS"
            shift("External Shock / Inflation Risk", +0.08, f"VIX {vix:.1f} > 25")
            shift("Base Case",                       -0.04, f"VIX {vix:.1f} > 25")
            shift("Growth Upside",                   -0.04, f"VIX {vix:.1f} > 25")
        elif vix > 20:
            adj_meta["vix_signal"] = "ELEVATED_STRESS"
            shift("External Shock / Inflation Risk", +0.05, f"VIX {vix:.1f} > 20")
            shift("Base Case",                       -0.03, f"VIX {vix:.1f} > 20")
            shift("Growth Upside",                   -0.02, f"VIX {vix:.1f} > 20")
        elif vix < 14:
            adj_meta["vix_signal"] = "LOW_VOLATILITY"
            shift("External Shock / Inflation Risk", -0.02, f"VIX {vix:.1f} < 14")
            shift("Base Case",                       +0.01, f"VIX {vix:.1f} < 14")
            shift("Growth Upside",                   +0.01, f"VIX {vix:.1f} < 14")

        # ── FII FLOW ADJUSTMENTS ──
        if fii_net < -2000:
            adj_meta["fii_signal"] = "HEAVY_SELLING"
            shift("Growth Upside",                   -0.04, f"FII selling Rs.{abs(fii_net):,.0f} Cr")
            shift("Base Case",                       -0.04, f"FII selling Rs.{abs(fii_net):,.0f} Cr")
            shift("External Shock / Inflation Risk", +0.02, f"FII selling Rs.{abs(fii_net):,.0f} Cr")
            if "Growth Upside" in idx:
                scenarios[idx["Growth Upside"]]["dominance_hint"] = "WEAKENING"
        elif fii_net < -1000:
            adj_meta["fii_signal"] = "NET_SELLING"
            shift("Growth Upside",                   -0.02, f"FII selling Rs.{abs(fii_net):,.0f} Cr")
            shift("Base Case",                       -0.02, f"FII selling Rs.{abs(fii_net):,.0f} Cr")
            shift("External Shock / Inflation Risk", +0.01, f"FII selling Rs.{abs(fii_net):,.0f} Cr")
        elif fii_net > 2000:
            adj_meta["fii_signal"] = "STRONG_BUYING"
            shift("Growth Upside",                   +0.04, f"FII buying Rs.{fii_net:,.0f} Cr")
            shift("Base Case",                       +0.02, f"FII buying Rs.{fii_net:,.0f} Cr")
            shift("External Shock / Inflation Risk", -0.03, f"FII buying Rs.{fii_net:,.0f} Cr")

        if adj_meta["adjustments"]:
            print(
                f"  [Scenario] Market stress adjustments — "
                f"VIX: {vix:.1f} ({adj_meta['vix_signal']}), "
                f"FII: Rs.{fii_net:,.0f} Cr ({adj_meta['fii_signal']}), "
                f"Crude: ${crude:.0f} ({adj_meta['crude_signal']})"
            )
            for a in adj_meta["adjustments"]:
                print(f"    → {a}")

        return adj_meta

    def generate_scenarios(self, regime_output, cause_effect_output,
                           nse_snapshot=None):
        """
        Generates three scenarios (base, upside, downside) with
        probabilities dynamically adjusted by VIX, FII flow,
        and live crude price (✅ ITEM 2).

        nse_snapshot — optional, passed from main.py pipeline.
                       Must contain nse_snapshot["crude_price"]
                       injected by main.py from ticker_data.
                       If None, market stress adjustments are skipped.
        """
        regime_output       = ensure_dict(regime_output,       "Regime Output")
        cause_effect_output = ensure_dict(cause_effect_output, "Cause Effect Output")

        regime      = safe_get(regime_output, "regime",     "")
        regime_conf = safe_float(safe_get(regime_output, "confidence", 0.6))
        narrative   = safe_get(regime_output, "narrative",  "")
        drivers     = ensure_list(safe_get(regime_output, "drivers", []))
        inputs      = ensure_dict(safe_get(regime_output, "inputs", {}), "Inputs")
        components  = ensure_dict(safe_get(regime_output, "components", {}), "Components")

        growth    = safe_float(safe_get(inputs, "growth",          7.2))
        inflation = safe_float(safe_get(inputs, "inflation",        5.0))
        liq_score = safe_float(safe_get(inputs, "liquidity_score",  0))
        repo      = safe_float(safe_get(inputs, "repo_rate",        6.5))

        chain    = ensure_list(safe_get(cause_effect_output, "chain", []))
        dominant = ensure_dict(safe_get(cause_effect_output, "dominant_driver", {}),
                               "Dominant")

        # ── Aggregate macro forces ──
        forces = {
            "growth":    0,
            "liquidity": 0,
            "inflation": 0,
            "external":  0,
            "risk":      0
        }

        for node in chain:
            node  = ensure_dict(node, "Chain Node")
            score = self._score_node(node)
            cat   = safe_get(node, "category", "")

            if cat == "GROWTH":
                forces["growth"]    += score
            elif cat in ["LIQUIDITY", "POLICY"]:
                forces["liquidity"] += score
            elif cat == "DEMAND":
                forces["growth"]    -= score
                forces["risk"]      += score
            elif cat == "EXTERNAL":
                forces["external"]  += score
                forces["risk"]      += score
            elif cat == "REGIME":
                forces["risk"]      += score

        scenarios = []

        # ── BASE CASE ──
        base_asset_impact = self._asset_impact_for_regime(regime)

        scenarios.append({
            "name":        "Base Case",
            "type":        "baseline",
            "description": (
                f"Continuation of {str(regime).replace('_', ' ').title()} dynamics. "
                f"GDP at {growth}%, inflation at {inflation}%, "
                f"repo rate at {repo}%."
            ),
            "probability":  regime_conf,
            "drivers":      drivers,
            "asset_impact": base_asset_impact,
            "key_risk":     "Regime fails to sustain if liquidity conditions reverse",
            "time_horizon": "medium-term"
        })

        # ── GROWTH UPSIDE ──
        upside_score = forces["growth"] + forces["liquidity"] - forces["risk"]

        scenarios.append({
            "name":        "Growth Upside",
            "type":        "bullish",
            "description": (
                f"GDP accelerates toward {round(growth + 0.8, 1)}%+ driven by "
                "capex cycle, credit expansion, and global tailwinds. "
                "RBI holds rates — liquidity remains supportive. "
                "FII flows turn strongly positive."
            ),
            "probability":  max(0.10, safe_float(upside_score) / 15),
            "drivers": [
                "Capex cycle acceleration",
                "Credit growth expansion",
                "Global risk-on environment",
                "FII equity inflows"
            ],
            "asset_impact": {
                "equities": "Strong outperformance — cyclicals and mid-caps lead",
                "bonds":    "Neutral — yields stable",
                "gold":     "Underperforms in risk-on",
                "cash":     "Deploy cash into risk assets"
            },
            "key_risk":     "Overheating inflation if growth exceeds 8%",
            "time_horizon": "medium-term"
        })

        # ── EXTERNAL SHOCK / TAIL ──
        downside_score = forces["risk"] + forces["external"]

        scenarios.append({
            "name":        "External Shock / Inflation Risk",
            "type":        "bearish",
            "description": (
                "Oil spike above $95/bbl or INR breach of 85 triggers "
                "imported inflation surge. RBI forced into emergency tightening. "
                "FII outflows accelerate. Equity multiples compress 15-20%."
            ),
            "probability":  max(0.05, safe_float(downside_score) / 15),
            "drivers": [
                "Crude oil above $95/bbl",
                "INR weakness beyond 85",
                "US Fed hawkish surprise",
                "Global risk-off selloff"
            ],
            "asset_impact": {
                "equities": "Sell cyclicals — rotate to FMCG, IT, Pharma defensives",
                "bonds":    "Avoid long duration — shift to T-bills and short end",
                "gold":     "Strong outperformance as inflation hedge",
                "cash":     "Raise cash buffer to 15-20%"
            },
            "key_risk":     "Contagion from EM selloff compounds domestic pressures",
            "time_horizon": "short-term"
        })

        # ── MARKET STRESS ADJUSTMENTS ──
        # Probability shifts (VIX, FII, Crude) applied before normalisation.
        # Description override and probability floor for crude >= $95 also live here —
        # not in main_api.py — so edits to main_api.py cannot regress this behaviour.
        stress_meta = {}
        if nse_snapshot:
            vix, fii_net, crude = self._read_market_stress(nse_snapshot)
            stress_meta         = self._apply_market_stress_adjustments(
                scenarios, vix, fii_net, crude
            )
            if crude >= 95:
                self._apply_crude_description_override(scenarios, crude)

        # ── NORMALISE PROBABILITIES ──
        total = sum(safe_float(safe_get(s, "probability", 0)) for s in scenarios)
        if total > 0:
            for s in scenarios:
                s["probability"] = round(
                    safe_float(safe_get(s, "probability", 0)) / total, 2
                )

        # ── TAG DOMINANCE ──
        dominant_scenario = max(
            scenarios,
            key=lambda x: safe_float(safe_get(x, "probability", 0))
        )

        for s in scenarios:
            prob = safe_float(safe_get(s, "probability", 0))
            if safe_get(s, "name") == safe_get(dominant_scenario, "name"):
                s["dominance"] = "DOMINANT"
            elif prob >= 0.20:
                s["dominance"] = "CHALLENGER"
            else:
                s["dominance"] = "TAIL"

        probs      = [safe_float(safe_get(s, "probability", 0)) for s in scenarios]
        dispersion = round(max(probs) - min(probs), 2) if probs else 0

        return {
            "scenarios": scenarios,
            "meta": {
                "regime_anchor":     regime,
                "dominant_scenario": safe_get(dominant_scenario, "name"),
                "dispersion":        dispersion,
                "confidence":        round(regime_conf * (1 - dispersion), 2),
                # ✅ All three stress signals surfaced for display/debug
                "vix":               stress_meta.get("vix",           15.0),
                "fii_net_crore":     stress_meta.get("fii_net_crore", 0),
                "crude_price":       stress_meta.get("crude_price",   85.0),
                "vix_signal":        stress_meta.get("vix_signal",    "NEUTRAL"),
                "fii_signal":        stress_meta.get("fii_signal",    "NEUTRAL"),
                "crude_signal":      stress_meta.get("crude_signal",  "NEUTRAL"),
            }
        }

    def _apply_crude_description_override(self, scenarios, crude):
        """
        When crude >= $95, floor the External Shock probability and prepend
        a live warning to its description. Probability shifts already applied
        by _apply_market_stress_adjustments; this adds the floor + label.
        """
        min_prob = 0.30 if crude >= 100 else 0.20
        for sc in scenarios:
            sc_type = (sc.get("type") or "").lower()
            sc_name = (sc.get("name") or "").lower()
            if "external" in sc_type or "shock" in sc_type or "external" in sc_name or "shock" in sc_name:
                sc["probability"] = max(safe_float(sc.get("probability", 0)), min_prob)
                sc["description"] = (
                    f"⚠️ Crude at ${crude:.0f} — External Shock threshold reached. "
                    + (sc.get("description") or "")
                )
                break

    def _asset_impact_for_regime(self, regime):
        regime  = str(regime)
        mapping = {
            "LIQUIDITY_DRIVEN_EXPANSION": {
                "equities": "Overweight — cyclicals, banks, infra lead",
                "bonds":    "Neutral — yields range-bound",
                "gold":     "Underweight — risk-on reduces safe haven demand",
                "cash":     "Reduce — deploy into risk assets"
            },
            "STABLE_GROWTH": {
                "equities": "Overweight — broad participation",
                "bonds":    "Neutral",
                "gold":     "Neutral",
                "cash":     "Neutral"
            },
            "MONETARY_TIGHTENING": {
                "equities": "Underweight — multiple compression risk",
                "bonds":    "Underweight duration — prefer short end",
                "gold":     "Overweight — inflation hedge",
                "cash":     "Overweight — preserve capital"
            },
            "LIQUIDITY_TIGHTENING": {
                "equities": "Underweight — high beta most at risk",
                "bonds":    "Short duration only",
                "gold":     "Overweight",
                "cash":     "High — defensive positioning"
            },
            "EARLY_CYCLE_RECOVERY": {
                "equities": "Overweight — rate sensitives lead (banks, RE, autos)",
                "bonds":    "Overweight duration — rates heading lower",
                "gold":     "Neutral",
                "cash":     "Reduce gradually"
            },
            "GROWTH_SLOWDOWN_SUPPORT": {
                "equities": "Neutral to underweight",
                "bonds":    "Overweight — flight to safety + rate cuts",
                "gold":     "Overweight",
                "cash":     "Elevated"
            },
            "INFLATION_PRESSURE_WITH_EXTERNAL_RISK": {
                "equities": "Underweight — avoid domestic cyclicals",
                "bonds":    "Short duration only — avoid long end",
                "gold":     "Overweight — inflation and uncertainty hedge",
                "cash":     "Elevated buffer"
            },
            "STAGFLATION_RISK": {
                "equities": "Underweight — only IT exports and Pharma",
                "bonds":    "Avoid — real returns negative",
                "gold":     "Strong overweight",
                "cash":     "High — capital preservation"
            }
        }
        return mapping.get(regime, {
            "equities": "Neutral",
            "bonds":    "Neutral",
            "gold":     "Neutral",
            "cash":     "Neutral"
        })