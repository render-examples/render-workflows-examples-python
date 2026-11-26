-- One row per document. The client script upserts on document_id, so extracting
-- the same document again updates its row instead of adding one.
CREATE TABLE IF NOT EXISTS invoice_extractions (
    document_id text PRIMARY KEY,
    record      jsonb,                         -- null when nothing could be extracted
    exceptions  jsonb NOT NULL DEFAULT '[]',   -- what a person should review
    task_run_id text NOT NULL,                 -- the Render task run that produced this row
    updated_at  timestamptz NOT NULL DEFAULT now()
);
