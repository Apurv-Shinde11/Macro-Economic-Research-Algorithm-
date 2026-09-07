-- Dedicated log of every real story_generation.generate_story() call --
-- separate from runs.story (display-only, one row per run, overwritten
-- on the next run). This table is append-only: every call, every
-- module, every status, kept as its own row -- built as a future
-- fine-tuning training corpus (input/output pairs where status='ok')
-- and as operational history for how often generation pauses or fails.
-- Created: September 2026

CREATE TABLE IF NOT EXISTS story_generation_log (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at                  timestamptz DEFAULT now(),
  module                      text NOT NULL,
  status                      text NOT NULL,
  intelligence_object         jsonb NOT NULL,
  change_trigger_candidates   jsonb,
  deterministic_narrative     text,
  story_result                jsonb NOT NULL,
  raw_llm_output              jsonb,
  provider                    text,
  model                       text,
  effort                      text,
  system_prompt_hash          text,
  input_tokens                integer,
  output_tokens               integer,
  error_message               text
);

COMMENT ON TABLE story_generation_log IS
'Append-only log of every real generate_story() call, one row per call,
regardless of outcome. Distinct from runs.story, which holds only the
latest story for display and gets overwritten each run. Intended uses:
(1) a future fine-tuning training corpus -- filter status=''ok'' for
real input/output pairs; (2) operational history of how often
generation pauses (briefing_allowed=false) or fails, which is not
persisted anywhere else today. Deliberately carries no user_id --
this is a model input/output corpus, not a per-user activity log,
so it stays unattributed if ever exported for actual fine-tuning.';

COMMENT ON COLUMN story_generation_log.module IS
'''sentinel'' or ''atlas''.';

COMMENT ON COLUMN story_generation_log.status IS
'''ok'', ''paused'' (briefing_allowed was false -- LLM was never called),
or ''unavailable'' (LLM call failed, or its output failed the numeric
grounding check in _validate_grounding()).';

COMMENT ON COLUMN story_generation_log.intelligence_object IS
'The exact build_sentinel_intelligence_object()/build_atlas_intelligence_object()
output this call received.';

COMMENT ON COLUMN story_generation_log.change_trigger_candidates IS
'The exact output of _build_trigger_candidates() for this call. Logged
verbatim rather than left to be recomputed later from
intelligence_object, since _build_trigger_candidates()''s own logic may
change over time -- recomputing from an old row could silently
reconstruct candidates that do not match what the model actually saw.';

COMMENT ON COLUMN story_generation_log.deterministic_narrative IS
'The old regime_engine._build_narrative() text passed as background
context for this call (Sentinel only; NULL for Atlas, which has no
equivalent).';

COMMENT ON COLUMN story_generation_log.story_result IS
'The exact dict generate_story() returned to its caller for this call
-- {"status": "ok", "headline": ..., "so_what": ..., ...} or
{"status": "paused", "message": ...} or {"status": "unavailable"}.';

COMMENT ON COLUMN story_generation_log.raw_llm_output IS
'What the LLM actually returned before grounding validation, when a
call reached the LLM at all (NULL for status=''paused'', where the LLM
was never invoked). Populated even when _validate_grounding() rejects
the output and story_result ends up ''unavailable'' -- this is the only
place a rejected generation attempt is preserved, for debugging/tuning
the grounding validator and prompt over time.';

COMMENT ON COLUMN story_generation_log.system_prompt_hash IS
'First 12 hex characters of SHA-256 of SYSTEM_PROMPT at call time, not
the full prompt text repeated on every row. Lets a later fine-tuning
pass tell whether two status=''ok'' rows were generated under the same
instructions -- the prompt will be tuned over time, and that
correlation cannot be reconstructed after the fact if not captured here.';

COMMENT ON COLUMN story_generation_log.input_tokens IS
'response.usage.input_tokens for this call. NULL for status=''paused''.';

COMMENT ON COLUMN story_generation_log.output_tokens IS
'response.usage.output_tokens for this call (includes thinking tokens
billed as output). NULL for status=''paused''.';

COMMENT ON COLUMN story_generation_log.error_message IS
'Exception text when status=''unavailable'' due to a call failure (not a
grounding rejection, which is captured via raw_llm_output instead).
NULL otherwise.';
