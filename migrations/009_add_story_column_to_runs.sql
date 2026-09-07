-- Add story column to runs (LLM-generated narrative for the Sentinel story arc)
-- Created: September 2026
ALTER TABLE runs
ADD COLUMN IF NOT EXISTS story jsonb;

COMMENT ON COLUMN runs.story IS
'Output of story_generation.generate_story(), written once when this run
completes -- read on subsequent page loads, never regenerated for the
same row. Shape: {"status": "ok", "headline": text, "so_what": text,
"what_deserves_attention": text, "change_triggers": [...]}
or {"status": "paused", "message": text} when briefing_allowed was
false for this run, or {"status": "unavailable"} if generation was
attempted and failed. NULL on rows created before this column existed.';
