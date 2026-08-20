# Using the platform

Three surfaces, all running now.

## 1. The web dashboard

```bash
uv run fpl platform serve      # then open http://localhost:8321
```

Panels: your squad, the projection table (5 providers, 592 players), a 5-GW
fixture ticker, the price radar, and your idea registry. A panel with no data
tells you *why* rather than showing a plausible number.

To keep it running like the bots:

```bash
make deploy-platform           # launchd, restarts on boot
```

## 2. Telegram — @fplpradhannbot (already deployed)

Ask it anything the router understands:
- "review my team" → pitch image with prices, flags, xPts
- "which defenders have the highest xPoints"
- "suggest me transfers"
- "which fixtures to target next"
- "summarize FPL Harry" / paste any YouTube link → transcript + analysis
- "I like Rashford" → logged as a tracked idea with a falsifiable thesis

## 3. The deadline DAG (already deployed)

Fires on its own, keyed to the real UTC deadline:

| When | What |
|---|---|
| T-30h | presser + projection refresh, injury digest |
| 02:00 UK nightly | price radar (only messages you if something moved) |
| T-4h | the solve delivery |
| T-90m | lineup captaincy check |

`make dag-status` shows the next-due times. `make dag-tick` runs one now.

## Before the GW1 deadline (Fri 21 Aug 17:30 UTC)

1. `uv run fpl myteam auth` — refresh the session so the squad panel and
   transfer advice read your real team (the access token expires every 8h; the
   refresh token lasts ~6 months).
2. `make solve` — generates a fresh plan. Without this the T-4h delivery will
   honestly report "no fresh solve" rather than hand you a stale squad.
3. Open `localhost:8321` and confirm the dashboard renders — it was built after
   browser automation got blocked here, so it is served and syntax-checked but
   not visually confirmed.

## What is not built yet

See `docs/platform/ROADMAP.md`. Short version: the rank-aware solver is
substantially built but unverified end to end, the projection *ensemble* (as
opposed to the ingested sources) is not blended yet, and there is no
confirmed-lineup feed.
