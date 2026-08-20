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

1. `uv run fpl myteam auth` — reports token state and verifies against the live
   account, refreshing the 8-hour access token through the stored refresh token.
   That is the everyday case and needs no input. If it reports the grant was
   *refused*, the session was revoked: `uv run fpl myteam auth --paste-cookie`
   once (hidden prompt) and the ~6-month refresh token takes over again.
2. `uv run python scripts/gw1_squad.py` — re-solves the plan. The recommended
   squad section leads with the solve time and warns past 24h; acting on a
   stale plan means acting on stale prices, injuries and odds.
3. Open `localhost:8321` and confirm the dashboard renders — it was built after
   browser automation got blocked here, so it is served and syntax-checked but
   not visually confirmed.

Once 1 and 2 are both done, `uv run fpl weekly` ends its recommended-squad
section with **Versus your actual squad**: the exact players to move, paired
by position so every row is a legal transfer, plus whether you and the plan
agree on the captain. Before the first deadline those transfers are unlimited
and free, so that diff *is* the GW1 decision.

## Authentication, in one paragraph

FPL authenticates with a bearer token, not a cookie — a session cookie on its
own now returns 403. `auth --paste-cookie` extracts the `access_token` and
`refresh_token` from a pasted browser Cookie header (pasting just those two
values works too) and stores them in `.env`, which is gitignored; no password
is stored or asked for. Refresh tokens are single-use, so redemption is
serialised across processes with a lock on `.env.lock` — without it the bot
and the DAG could refresh at once and the loser would replay a spent token.
Revoke by logging out of that browser session.

## What is not built yet

See `docs/platform/ROADMAP.md`. Short version: the rank-aware solver is
substantially built but unverified end to end, the projection *ensemble* (as
opposed to the ingested sources) is not blended yet, and there is no
confirmed-lineup feed.
