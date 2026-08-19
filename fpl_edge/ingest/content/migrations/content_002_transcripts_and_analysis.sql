-- Full transcripts, indexed, and LLM analyses of them.
--
-- transcript_segment keeps the complete text of a video or episode as
-- timestamped rows, so "what did they say about X and WHEN in the video"
-- is a query, not a re-fetch. content_item.text remains the joined text for
-- the cue extractor; segments are the authoritative, navigable form.
--
-- content_analysis stores the structured LLM read of a transcript: summary,
-- key transfers, captaincy and chip advice with conviction levels and
-- verbatim supporting quotes. One row per (item, model) so re-analysis with
-- a newer model never overwrites what an older one said.

CREATE TABLE IF NOT EXISTS transcript_segment (
    item_id   VARCHAR NOT NULL,
    seq       INTEGER NOT NULL,     -- order within the item
    start_s   DOUBLE,               -- seconds from the start; NULL for articles
    text      VARCHAR NOT NULL,
    PRIMARY KEY (item_id, seq)
);

CREATE TABLE IF NOT EXISTS content_analysis (
    item_id       VARCHAR NOT NULL,
    model         VARCHAR NOT NULL,
    created_utc   TIMESTAMPTZ NOT NULL,
    analysis_json VARCHAR NOT NULL,  -- the validated TranscriptAnalysis, as JSON
    PRIMARY KEY (item_id, model)
);

-- Claims now carry their extractor: 'cue' (keyword windows, pseudo-confidence
-- from cue distance) or 'llm:<model>' (semantic extraction, confidence encodes
-- the stated conviction band: high=0.8 medium=0.6 low=0.4). Scoring can then
-- report the two channels separately instead of averaging noise into signal.
ALTER TABLE content_claim ADD COLUMN IF NOT EXISTS extractor VARCHAR DEFAULT 'cue';
