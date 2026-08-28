# Creators rebuild — the contracts (2026-08-28)

Four workstreams build in parallel against this file. It is the agreement
between them; nobody changes it unilaterally. If it is wrong, say so and change
it HERE first.

Decision taken by the owner: **A's Deadline Board as the spine, C's shared
player strip as the cross-tab component, B's said-vs-owned grid as a second
view.** The three proposals are `creators_design_{A,B,C}.md`.

## The governing sentence

> No creator here has earned a weight — the aggregate record is 34.6%, below
> chance. So this is not a forecast. It is **the field's intent, and what they
> actually own**, and the rows that matter are where those disagree with your
> squad.

Everything on the page serves that. Authority is never implied; volume and
recency are honest orderings, measured accuracy is not available.

## 1. `player_chatter` — the cross-tab panel (NEW)

Params: `{ "code": int (required), "days": int = 30 }`

```jsonc
{
  "code": 223094, "name": "Haaland", "as_of": "<iso>",
  "said": [{                       // creator claims + watch calls
    "person": "FPL Harry",         // null when only the show is known
    "person_basis": "sole_host"|"title"|"stated"|"manual"|null,
    "show": "FPL Harry",
    "action": "buy"|"sell"|"hold"|"captain"|"triple_captain"|"bench"|"avoid"|"watch",
    "is_observation": true,        // true for watch -- NEVER shown as a recommendation
    "conviction": "high"|"medium"|"low"|null,
    "extractor": "cue"|"llm:<model>",
    "quote": "...", "start_s": 812.0, "deep_link": "https://...",
    "published_at": "<iso>", "item_title": "...", "item_url": "...",
    "url_basis": "link"|"enclosure"   // enclosure => "play audio", not "open episode"
  }],
  "owned": [{                      // what the panel ACTUALLY owns -- hard data
    "person": "Mark Sutherns", "entry_id": 252,
    "multiplier": 2, "role": "captain"|"start"|"bench",
    "gw": 1, "as_of": "<iso>"
  }],
  "owned_reason": "...",           // when empty: whose squads are uncrawled, and why
  "noticed": [{                    // intel_item -- MEASURED, not spoken
    "kind": "out_of_position"|"set_piece"|"availability"|"press_conference",
    "headline": "...", "body": "...", "source": "...", "source_url": "...",
    "published_at": "<iso>", "confidence": 0.8
  }],
  "counts": { "said": 4, "observations": 1, "owned": 3, "noticed": 2,
              "panel_size": 16, "squads_known": 7 },
  "reason": null                   // why a section is empty, when it is
}
```

**Ordering is DID → SAID → NOTICED**, deliberately: what the panel owns is
verified fact, what they said carries a below-chance record, and `noticed` is
measured rather than either. `said` and `noticed` must never merge — one is
🗣 spoken, the other ⚙ measured.

**The panel must NOT emit a `net` or a consensus score.** Counting agreement
into a single number is the authority claim this page exists to refuse.

## 2. `creator_board` — extended for the Deadline Board

Already serves `scope`, `creators[]`, `consensus[]`, per-show `entry.people[]`,
and `take.watching[]`. It additionally needs, per consensus row:
- `mine`: `{ "in_squad": bool, "multiplier": int|null, "role": str|null }`
- `panel_owned`: `{ "n": int, "of": int, "people": [str] }` — how many panel
  members actually hold him, which is the DID channel at board level.

## 3. Paste-a-link — the job API

- `POST /api/ingest/link  {url}` → `{job_id, stages:[fetch,transcribe,analyse,attribute]}`
- `GET  /api/ingest/link/{job_id}` → `{stage, pct, eta_s, done, error, item_id}`

Measured rates for a real ETA, not a guess: **captions ≈ 286× realtime**,
**local ASR ≈ 11.5× realtime**. A 20-minute video is ~4s via captions, ~105s
via ASR — the UI names which path it is on BEFORE the wait starts.

Failure states that already exist in the warehouse and must be handled by name:
a non-episode URL (an FPL league invite was once ingested as an article titled
`a6fgym`), the same video pasted twice under two URL forms, a video with
neither captions nor downloadable audio, and a source that returns 403/429 —
which is the source declining and must stop, not retry.

## 4. Rules every workstream honours

- **No fabricated data.** Nulls carry reasons; reasons get rendered.
- **`cue` and `llm:` stay visually distinguishable.** A keyword window is not a
  considered take.
- **A watch is an observation.** Never rendered as a buy. `is_observation`
  exists so the UI cannot get this wrong by omission.
- **A show is not a person.** The Wire has four hosts with four teams; flat
  show-level identity is populated only when a show has exactly one verified
  person.
- **Zero-build UI**: ES modules, no bundler, no libraries, both themes from
  tokens, charts follow the `dataviz` skill.
