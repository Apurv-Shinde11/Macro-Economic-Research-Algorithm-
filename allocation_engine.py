class IndianSectorOptimizer:
    def __init__(self):
        # Weighting reflects the 2026-27 Fiscal Reality
        self.sector_sensitivity = {
            "NIFTY_BANK": {"ir_sensitivity": 0.8, "capex": 0.4, "tax_impact": -0.2},
            "NIFTY_INFRA": {"ir_sensitivity": 0.3, "capex": 1.2, "tax_impact": 0.0}, 
            "NIFTY_IT": {"ir_sensitivity": 0.1, "capex": 0.2, "tax_impact": 0.0},
            "NIFTY_FIN_SERVICES": {"ir_sensitivity": 0.7, "capex": 0.3, "tax_impact": -1.5}, 
            "NIFTY_PHARMA": {"ir_sensitivity": 0.1, "capex": 0.0, "tax_impact": 0.0}
        }

    def allocate(self, intel):
        """
        Processes AI Intel and Stress-Test inputs to generate 
        actionable institutional signals.
        """
        # --- 1. DATA EXTRACTION & CLEANING ---
        hard_data = intel.get('hard_data', {})
        sentiment_score = intel.get('sentiment_score', 0)
        hawkish_signals = intel.get('hawkish_signals', 0)

        try:
            repo = float(str(hard_data.get('repo_rate', 5.25)))
            capex = float(str(hard_data.get('capex_lakh_cr', 12.2)))
        except (ValueError, TypeError):
            repo = 5.25
            capex = 12.2

        recommendations = {}

        # --- 2. THE MULTI-LAYER DECISION LOGIC ---
        for sector, weights in self.sector_sensitivity.items():
            # A: Growth Factor (How much the ₹12.2L Cr helps)
            growth_score = (capex / 10.0) * weights['capex']
            
            # B: Interest Rate Headwind (Higher Repo = Lower Score)
            # We treat 5.0% as the 'Neutral' baseline for 2026
            ir_headwind = (repo - 5.0) * weights['ir_sensitivity']
            
            # C: Friction/Tax Impact
            friction_score = hawkish_signals * weights['tax_impact']

            # --- 3. FINAL AGGREGATE SCORE ---
            final_score = growth_score - ir_headwind + friction_score + (sentiment_score * 0.1)

            # --- 4. ACTION & REASONING GENERATION ---
            if final_score > 1.4:
                action = "STRONG OVERWEIGHT"
                reason = f"High Capex multiplier ({weights['capex']}x) provides a valuation floor despite rates."
            elif final_score > 0.8:
                action = "OVERWEIGHT"
                reason = "Favorable growth-to-risk ratio. Sentiment remains supportive."
            elif final_score < 0.3:
                action = "UNDERWEIGHT"
                reason = f"Structural headwinds: Repo at {repo}% and tax friction ({weights['tax_impact']}) outweigh growth."
            else:
                action = "NEUTRAL"
                reason = "Balanced macro signals. Sector sensitivity is currently at equilibrium."

            recommendations[sector] = {
                "Action": action, 
                "Reason": reason
            }

        return recommendations