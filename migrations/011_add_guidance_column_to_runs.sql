-- Add guidance column to runs (deterministic, profile-aware SO WHAT for the Sentinel story arc)
-- Created: September 2026
ALTER TABLE runs
ADD COLUMN IF NOT EXISTS guidance jsonb;

COMMENT ON COLUMN runs.guidance IS
'Output of profile_guidance.reinterpret(), written once when this run
completes -- read on subsequent page loads, never regenerated for the
same row. Deterministic, no LLM call. Shape: {"status": "ok",
"so_what": text, "bucket": {"mandate_type":..., "risk_tolerance":...,
"investment_horizon":...}, "basis": {"regime":..., "confidence_band":...,
"posture":...}} or {"status": "withheld", "reason": text} when the
underlying read was paused or unavailable. NULL on rows created before
this column existed. See profile_guidance.py and migrations/009 (the
"story" column this sits alongside).';
