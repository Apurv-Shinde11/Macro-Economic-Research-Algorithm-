# explanation_engine.py

class ExplanationEngine:
    def __init__(self):
        pass

    def generate_full_explanation(self, final_intel):
        """
        Generates a structured, client-ready macro narrative.
        """

        regime_data = final_intel.get("macro_regime", {})
        scenario_block = final_intel.get("scenarios", {})
        asset_block = final_intel.get("asset_implications", {})
        positioning = final_intel.get("positioning", {})

        # =========================
        # 🧠 REGIME
        # =========================
        regime = regime_data.get("regime", "").replace("_", " ").title()
        confidence = regime_data.get("confidence", 0.6)
        drivers = regime_data.get("drivers", [])

        driver_text = ", ".join(drivers) if drivers else "mixed macro signals"

        # =========================
        # 🎯 SCENARIOS
        # =========================
        scenarios = scenario_block.get("scenarios", [])
        meta = scenario_block.get("meta", {})

        dominant_scenario = meta.get("dominant_scenario", "Base Case")
        dispersion = meta.get("dispersion", 0.3)

        scenario_lines = []
        for s in scenarios:
            name = s.get("name", "")
            prob = int(s.get("probability", 0) * 100)
            tag = s.get("dominance", "")

            scenario_lines.append(f"- {name} ({prob}%) [{tag}]")

        scenario_text = "\n".join(scenario_lines)

        # =========================
        # 📊 ASSET VIEWS
        # =========================
        assets = asset_block.get("assets", {})
        sectors = asset_block.get("sectors", {})

        asset_summary = ", ".join([f"{k.capitalize()} ({v})" for k, v in assets.items()])

        top_sectors = [
            f"{k.capitalize()} ({v})"
            for k, v in sectors.items()
            if "Positive" in v or "Negative" in v
        ]

        sector_summary = ", ".join(top_sectors[:4]) if top_sectors else "No strong sector tilts"

        # =========================
        # 🧭 POSITIONING
        # =========================
        stance = positioning.get("stance", "Neutral")
        allocation = positioning.get("allocation", {})
        conviction = positioning.get("meta", {}).get("conviction", 0.6)

        alloc_text = ", ".join([f"{k.capitalize()} {int(v*100)}%" for k, v in allocation.items()])

        # =========================
        # 🧠 FINAL NARRATIVE
        # =========================
        narrative = f"""
## 🧭 Macro Regime Overview
The current environment reflects a **{regime}** regime with **{int(confidence*100)}% confidence**, driven by {driver_text}.

## 🔮 Scenario Landscape
The scenario distribution indicates a **{dominant_scenario}** as the dominant path, with moderate uncertainty (dispersion: {round(dispersion,2)}).

{scenario_text}

## 📊 Asset & Sector Implications
At the asset level: {asset_summary}.

Sector positioning highlights: {sector_summary}.

## 🎯 Portfolio Positioning
The recommended stance is **{stance}**, with an overall conviction level of **{round(conviction,2)}**.

Suggested allocation: {alloc_text}.

## ⚠️ Strategic View
The current setup suggests maintaining flexibility, with close monitoring of macro triggers—particularly those related to inflation dynamics, external sector risks (oil, INR), and policy direction.

""".strip()

        return narrative