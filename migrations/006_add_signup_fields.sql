-- Add gated signup preference fields
-- Created: June 2026
ALTER TABLE profiles
ADD COLUMN IF NOT EXISTS role text DEFAULT 'other',
ADD COLUMN IF NOT EXISTS aum_size text DEFAULT 'individual',
ADD COLUMN IF NOT EXISTS heard_from text DEFAULT 'other',
ADD COLUMN IF NOT EXISTS approved_at timestamptz,
ADD COLUMN IF NOT EXISTS rejected_at timestamptz;