CREATE TABLE IF NOT EXISTS geopolitical_watch_cache (
  theme_key text PRIMARY KEY,
  theme_label text NOT NULL,
  status text,
  status_confidence text,
  headline_summary text,
  context text,
  india_transmission_channel text,
  india_linkage text,
  watch_for text,
  source_headlines jsonb,
  fetched_at timestamptz DEFAULT now()
);