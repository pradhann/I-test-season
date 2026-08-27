# Creator panel contract (2026-08-27)

The Creators tab and the panel scripts that feed it are built in parallel, so
this file is the shape both sides agree on. Neither side may change it
unilaterally: if it is wrong, say so and change it HERE first.

Two scripts, both registered in `fpl_edge/platform/scripts/creators.py`.

## 1. `creator_board` — the tab's landing payload

Params: `{ "days": int = 30, "gw": int|null }`

Result (or the standard `{empty, reason}`):

```jsonc
{
  "as_of": "<iso>",                 // snapshot instant everything was read at
  "window_days": 30,
  "gw": 3,                          // gameweek the takes are about
  "creators": [{
    "creator": "Let's Talk FPL",
    "kinds": ["youtube", "podcast"],
    "sources": [{ "key": "...", "kind": "youtube", "url": "https://...",
                  "last_item_at": "<iso>|null", "last_status": 200|null,
                  "discovery": "auto"|"manual" }],
    "n_items": 47, "n_items_window": 12, "n_claims_window": 24,
    "last_item_at": "<iso>|null",
    "latest": {                     // most recent item, ALWAYS from a real row
      "item_id": "...", "title": "...", "url": "https://...",
      "published_at": "<iso>", "kind": "youtube",
      "text_source": "description"|"transcript"|"article"
    },
    "take": {                       // the summarised position; null when absent
      "summary": "...",             // from content_analysis
      "model": "claude-opus-5",
      "transfers_in":  [{ "code": 223094, "name": "Haaland",
                          "conviction": "high"|"medium"|"low",
                          "quote": "...", "start_s": 812.0 }],
      "transfers_out": [ ... same shape ... ],
      "captain":       [ ... same shape ... ],
      "chips":         [{ "chip": "wildcard", "stance": "...", "quote": "...",
                          "horizon_gw": 7 }]
    },
    "take_reason": "no analysis yet: item carries show notes only",  // when take is null
    "record": {                     // never invent: nulls where unmeasured
      "scored": 24, "hits": 9, "hit_rate": 0.375,
      "wilson_lo95": 0.21, "weight": 0.0, "earned": false
    },
    "entry": { "entry_id": 53517, "name": "Ben Crellin",
               "verified": true, "source_url": "..." },
    "entry_reason": "no published team id found"   // when entry is null
  }],
  "consensus": [{                   // cross-creator, the "who agrees" view
    "code": 223094, "name": "Haaland", "pos": "FWD", "team": "MCI",
    "price": 15.5, "own_pct": 68.1,
    "buy":     { "n": 5, "creators": ["...", "..."] },
    "sell":    { "n": 1, "creators": ["..."] },
    "captain": { "n": 7, "creators": ["..."] },
    "net": 4                        // buy - sell, the sort key
  }],
  "provenance": { ... standard ... }
}
```

## 2. `creator_detail` — one creator, expanded

Params: `{ "creator": str (required), "days": int = 60, "limit": int = 40 }`

```jsonc
{
  "creator": "Let's Talk FPL",
  "entry": { ... as above ... } | null,
  "squad": [{ "code": ..., "name": "...", "pos": "MID", "price": 8.0,
              "multiplier": 2, "is_captain": true }] | null,
  "squad_reason": "picks become public at the deadline; none stored for GW3",
  "transfers": [{ "gw": 3, "in_name": "...", "in_code": ...,
                  "out_name": "...", "out_code": ..., "time_utc": "<iso>" }],
  "transfers_reason": "a gameweek's transfers are public only after its deadline",
  "items": [{
    "item_id": "...", "title": "...", "url": "...", "published_at": "<iso>",
    "kind": "youtube", "text_source": "transcript",
    "analysis": { ... same shape as `take` ... } | null,
    "claims": [{ "code": ..., "name": "...", "action": "buy",
                 "confidence": 0.8, "quote": "...", "start_s": 812.0,
                 "deep_link": "https://youtube.com/watch?v=...&t=812s",
                 "extractor": "llm:claude-opus-5"|"cue" }]
  }],
  "provenance": { ... }
}
```

## Rules both sides must honour

- **Nothing is invented.** A creator with no analysis gets `take: null` AND a
  `take_reason` a human can read. A creator with no verified entry gets
  `entry: null` and `entry_reason`. The UI renders the reason, never a blank
  or a plausible stand-in.
- **`deep_link` is built server-side**, because only the panel knows the
  platform's URL grammar (YouTube `&t=NNNs`, podcast episode + offset). If a
  timestamp is unknown, `start_s` is null and `deep_link` is the item URL.
- **Point in time**: claims and analyses are read through the sanctioned
  `published_at` path; manager facts through `sem_*(as_of)`.
- **Extractor is surfaced**, not averaged away: a `cue` claim (keyword window)
  and an `llm:` claim (semantic, with conviction) are different evidence and
  the UI must let a reader tell them apart.
- **Track record stays honest**: with every earned weight currently 0.0, the
  UI says "no creator has beaten a coin flip yet", never an empty leaderboard
  that reads as missing data.
