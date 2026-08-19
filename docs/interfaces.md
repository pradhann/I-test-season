# Interfaces: the idea inbox

The primary interface to this engine is not a dashboard. It is a text message.

You are watching highlights. Semenyo does something ridiculous. You think "I
should captain him." That thought is the most perishable and least recorded
asset in your FPL season: by Saturday you have either acted on it and forgotten
why, or talked yourself out of it and forgotten it existed. Either way, at the
end of the season you cannot answer the only question that would make you
better — *were my instincts right?*

So you text the bot. Four seconds later:

```
Logged idea_20260818T225000_f730ebec1d
Thesis: Semenyo outscores the median of the 10 most-owned outfield players
        (captaincy proxy) in GW12.
Verdict: model is neutral on this — P(thesis true) = 53% [low confidence]
Why: prior only (no points model registered): P(Semenyo outscores the median of
     the 10 most-owned outfield players) = 53%. £8.5m ranks at the 75% mark of
     the 10-player comparator set
Settles: GW12. Tracking from now, acted on or not.
(35 ms)
```

That is now a row in a table. It has an id, a timestamp, a claim that a later
gameweek can prove wrong, and a comparator frozen at the instant you said it. In
December, `fpl idea review` will tell you whether you were right, and — because
the ideas you *skipped* are tracked identically — whether you would have been
better off just doing what you first thought.

---

## Contents

- [Setting up the Telegram bot](#setting-up-the-telegram-bot) — the @BotFather steps
- [What an idea is](#what-an-idea-is)
- [Parsing messy input](#parsing-messy-input)
- [The verdict, and the seam to the models](#the-verdict-and-the-seam-to-the-models)
- [Tracking and resolution](#tracking-and-resolution)
- [`fpl idea review` and the bias probes](#fpl-idea-review-and-the-bias-probes)
- [Security model](#security-model)
- [The MCP tools](#the-mcp-tools)
- [Design decisions](#design-decisions)
- [Command reference](#command-reference)

---

## Setting up the Telegram bot

### 1. Create the bot with @BotFather

Everything here happens inside Telegram itself.

1. Open Telegram and search for **`@BotFather`** (the one with the blue
   verification tick). Open the chat and press **Start**.
2. Send **`/newbot`**.
3. BotFather asks for a **name**. This is the display name and can be anything:
   `FPL Edge`.
4. BotFather asks for a **username**. It must be unique across all of Telegram
   and must end in `bot`: e.g. `fplpradhannbot`.
5. BotFather replies with a line like:

   ```
   Use this token to access the HTTP API:
   1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

   That is the token. It is a password — anyone holding it can read every
   message sent to your bot and send messages as it.

Two optional but worthwhile follow-ups in the same BotFather chat:

- **`/setprivacy`** → pick your bot → **Enable**. Privacy mode means that if the
  bot is ever added to a group it only sees messages that explicitly address it.
  This engine's allowlist already rejects group chats, but defence in depth is
  free here.
- **`/setcommands`** → pick your bot → paste:

  ```
  review - how your ideas have actually performed
  track - settle ideas whose gameweeks have finalised
  id - show this chat id
  acted - mark your most recent idea as acted on
  ```

  This gives you the command menu in the Telegram UI.

### 2. Put the token in `.env`

`.env` is gitignored and must never be committed.

```bash
cd /path/to/i-test-season
printf 'TELEGRAM_BOT_TOKEN=%s\n' '1234567890:AAxxxx...' >> .env
chmod 600 .env
```

The token is read through `fpl_edge.config.secret()`, which checks the
environment first and then `.env`. It is never printed, logged, or included in
an error message — `TelegramConfig.problems()` names the *variable* and never
its value, and `HttpxTransport.__repr__` is a fixed string so a traceback cannot
leak it.

### 3. Find your chat id and lock the bot to it

Until `TELEGRAM_ALLOWED_CHAT_ID` is set, **the bot refuses every message**. That
is deliberate: the failure mode of a chat bot with a permissive default is that
anyone who guesses the handle can write to your database.

```bash
uv run fpl idea telegram --discover
```

Now open Telegram, find your bot by its username, press **Start**, and send it
anything. The log prints:

```
WARNING refused message from unauthorised chat 8782506418
```

That number is your chat id. Add it and restart:

```bash
printf 'TELEGRAM_ALLOWED_CHAT_ID=%s\n' '8782506418' >> .env
uv run fpl idea telegram
```

```
Connected as @fplpradhannbot.
Accepting messages from [8782506418].
```

You can also get the id from the bot itself once it is running — send `/id` — or
from `@userinfobot`.

### 4. Run it

```bash
uv run fpl idea telegram              # long-polls until interrupted
uv run fpl idea telegram --cycles 20  # stop after 20 poll cycles
```

Each cycle is a 25-second long poll: Telegram holds the connection open until a
message arrives, so an idle bot costs one socket rather than a poll loop, and a
message sent at second 3 is handled at second 3.

To try the whole thing with no token at all, pipe messages through the fake
transport:

```bash
printf 'I like Rashford\nSemenyo captain GW12?\n' | uv run fpl idea telegram --dry-run
```

---

## What an idea is

The unit of value is not the message. It is the **thesis** — a sentence a later
gameweek can prove wrong.

"I like Rashford" cannot be wrong. This can:

> Rashford outscores the median of the 28 MIDs priced within £0.5m of £7.0m over
> GW1–GW6.

Three things make that possible, and all three are enforced in
`fpl_edge/interfaces/ideas.py` rather than left to convention:

**A thesis names its comparator.** `Comparator` is a closed set of yardsticks
the tracker knows how to settle:

| Comparator | Means | Used for |
| --- | --- | --- |
| `median_captain` | median of the players the field was likely to captain | captaincy calls |
| `price_peer_median` | median of same-position players in a price band | "I like X", fades, differentials |
| `named_player` | the specific rival you named | "X or Y?" |
| `field_median` | median of everyone who played | fallback |

Adding a member without a resolver would produce ideas that can never be
settled, which is exactly what the enum exists to prevent.

**A thesis names its window.** A captaincy call is a one-week bet; "I like X" is
a hold and judging it on a single blank would be a strawman. Defaults live in
`DEFAULT_HORIZON`: 1 gameweek for captaincy and comparisons, 6 for everything
else.

**The record is immutable except by resolution.** Ideas are never edited to match
what happened. Being wrong on the record is the product.

### The tables

Applied by `fpl_edge/interfaces/migrations/001_idea_registry.sql`, into the same
DuckDB file as everything else. `fpl_edge/store/schema.sql` is **not** touched —
it is the shared contract that the ingest, model and optimiser teams read, and
adding interface tables there would make every one of their migrations conflict
with ours. The runner records each applied migration with its sha256 in
`schema_migration`.

| Table | Holds |
| --- | --- |
| `idea` | one row per thing you ever said, with its thesis and resolution rule |
| `idea_verdict` | the model's answer at the moment you said it, with provider and version |
| `idea_context` | your subject's features **and the population base rates**, frozen at submission |
| `idea_observation` | the per-gameweek tracking trail |
| `idea_pending` | an unanswered clarification, so a reply of "2" can complete it |

These are deliberately **not** in `store.PIT_KEYS`. That registry describes facts
about the world that were observable at an instant. An idea is not a fact about
the world; it is a record of what you believed and when.

Point-in-time discipline still applies to the idea's *inputs*: `idea.as_of` is
the snapshot instant the verdict and the context were computed from.

---

## Parsing messy input

Input arrives one-handed during highlights. It has nicknames, missing accents,
missing gameweeks, and surnames shared by two players in the same league.

**The commitment that matters: ambiguity is resolved by asking, never by
ranking.**

There are two Palmers in 2026-27 — Cole (MID, £9.5m, 10.6% owned) and Alex (GKP,
£4.0m, 5.2% owned). Breaking the tie on ownership would be right most of the
time. But when it is wrong you never find out: the idea sits in the registry
attributed to a backup goalkeeper, and both your hit rate and your bias analysis
are quietly poisoned by a thought you never had. So ownership orders the
candidate list for display and has **no vote** in whether to ask.

```
> Palmer captain gw5?
More than one player matches 'palmer'. Which one?
  1. Palmer (Cole Palmer) (MID, £9.5m, 10.6% owned)
  2. Palmer (Alex Palmer) (GKP, £4.0m, 5.2% owned)
Reply with a number, or the full name.

> 1
Logged idea_20260818T225009_db1391c7b3
Thesis: Palmer outscores the median of the 10 most-owned outfield players
        (captaincy proxy) in GW5.
```

The reply supplies only the *identity*. Intent and gameweek come from the message
you actually typed — GW5 survives, and so does the captaincy intent. And a user
who ignores the question and types something new is not silently answering the
old one.

### What the resolver handles

| Input | Resolves to | Because |
| --- | --- | --- |
| `semenyo`, `SEMENYO`, `Antoine Semenyo` | Semenyo | exact / token match |
| `rashfrod` | Rashford | 2 edits on an 8-letter name |
| `rashfor` | Rashford | prefix |
| `odegaard`, `oedegaard`, `Ødegaard` | Ødegaard | Ø → o folding |
| `van dijk`, `vvd` | Virgil van Dijk | second_name, and a nickname alias |
| `b.fernandes` | Bruno | the initial disambiguates two Fernandes |
| `palmer` | **asks** | two real Palmers |
| `salah`, `kdb`, `trent` | **asks** | not in the 2026-27 league |
| `Hi` | **"that is not an FPL idea"** | no name, no FPL intent |

Two gates keep near-misses out.

**Absolute edit distance, not similarity.** `difflib` scores "salah" against
"saliba" at 0.73, comfortably above any threshold that also accepts real typos.
Mohamed Salah is not in the 2026-27 league; Saliba is a £6.0m Arsenal
centre-back. Three edits on a five-letter word is a different player, and only an
absolute edit count can tell that apart from a misspelling. The budget scales
with length: 1 edit up to 5 characters, 2 up to 9, 3 beyond.

**Names come from `dim_player`, not just `web_name`.** Virgil van Dijk's
`web_name` is "Virgil". A resolver built on `Snapshot.players()` alone matches
nothing for "van dijk" — or worse, fuzzy-matches "van Ewijk", a £4.0m defender.
`features.player_universe()` joins the first and second names back on, through
the same Snapshot and therefore under the same point-in-time guarantee.

### Chatter is not a failed idea

The first three messages the live bot ever received were `/start`, `Hi` and
`This is the second message`. None is an idea. Replying to "Hi" with a shortlist
of defenders whose names are two edits away is worse than useless — it would also
leave a pending question that swallows your next real message.

So `has_fpl_intent()` separates "I meant a player and you missed him" from "that
was not about a player", and chatter gets a short honest reply with nothing
stored.

### Nicknames

`NICKNAMES` maps only aliases that string distance can *never* recover, because
the letters are not in the name: `kdb`, `vvd`, `taa`, `bruno`, `sonny`, `jgp`,
`big dog`. Typos are the fuzzy matcher's job. The table maps alias → search
string rather than alias → player code, so it does not go stale when a player
changes club.

---

## The verdict, and the seam to the models

`fpl_edge/interfaces/verdict.py` has three providers and the split is the point.

**`SimulationVerdict` is the real one.** It takes the `PointsModel` and
`OwnershipModel` protocols from `models/contracts.py` and answers the thesis the
only way it can honestly be answered — by counting the fraction of correlated
simulation draws in which the subject actually beats the comparator:

```python
m = np.median(sample.points[peer_rows, :], axis=0)   # median WITHIN each draw
wins = (subject_draws > m).mean()
```

The median is taken inside each draw, not across the marginals. "Does X beat the
median captain" is a statement about a *joint* distribution: X and the comparator
set share fixtures, opponents and clean sheets, and comparing marginal
expectations independently mis-prices exactly the differential calls you are
asking about.

It is written against the published contracts, not against any implementation,
so it starts working the day a model is registered:

```python
from fpl_edge.interfaces import IdeaInbox, default_provider
inbox = IdeaInbox(wh, provider=default_provider(points_model, ownership_model))
```

That one call is the entire integration surface for the models team.

**`PriorVerdict` is what runs today**, because on 2026-08-18 no gameweek of
2026-27 had been played and no points model had landed. It is deliberately weak
and deliberately legible: a monotone map from the subject's price rank *within
its own comparator set*, capped at [0.20, 0.80], adjusted for availability, and
blended with shrunk points-per-game once results exist. It does not fit
coefficients, because there is nothing to fit them on — a fitted-looking number
with no fit behind it is worse than an obvious prior.

Every verdict it issues is labelled `provider=prior`, `confidence=low`, and
`fpl idea review` adds a caveat saying so. Its `ModelCard` records `score=None`
with a note explaining that there are no resolved ideas to score it on yet.

**`TimeBounded` composes them.** The requirement is a verdict inside a minute; a
Monte Carlo over a double gameweek is not guaranteed to respect that, and an
inbox that hangs is an inbox that stops being used. So the primary provider runs
against a wall-clock budget and the prior answers if it overruns, with
`degraded=True` recorded on the row so a slow week is visible in the data rather
than silently changing what the numbers mean.

### Durability

The order inside `IdeaInbox.submit` is load-bearing:

1. Parse. If the text does not determine a player, **stop and ask**.
2. Write the idea row, with its thesis. Commit.
3. Capture the submission-time context. Commit.
4. *Then* compute the verdict. Commit.

The verdict is last because it is the only step that can be slow or can fail. A
model that times out, throws, or has not been written yet must not be able to
lose your thought. There is a test for exactly this: a provider that raises
`RuntimeError("the points model is not built yet")` still leaves a complete idea
in the registry with a `degraded` verdict recording the failure.

---

## Tracking and resolution

`fpl idea track` walks every open idea, records the gameweeks that have landed,
and settles the ones whose window is complete. It is idempotent and safe on a
timer.

**It does not read `acted`.** An idea you talked yourself out of is scored
exactly as hard as one you made a transfer for, because the counterfactual is the
only thing that can tell you whether your instincts or your second-guessing are
the problem. Nothing else in an FPL season records the transfers you did not
make.

**The comparator was frozen before kickoff.** `idea.as_of` fixes the ownership,
prices and availability that defined "the median captain choice". Recomputing
that set now, with hindsight, would produce a comparator selected partly by its
own results — the median would drift toward whoever happened to do well, and your
hit rate would measure nothing. So the set is rebuilt from a snapshot at `as_of`,
and the *only* thing read from the present is the realised points of players
already in it.

One subtlety: over a multi-week horizon the comparator's score is the **median
across its members of each member's total**, not the sum of per-gameweek medians.
The per-week median is a different player each week and the resulting figure
belongs to nobody, which would make "beats the median" mean "beats a portfolio
nobody could hold".

### Premium players and the widening band

Haaland at £14.5m has no other forward within £0.5m. At a fixed band his
comparator resolves to the empty set and the tracker has no choice but to void
the idea — so "I like Haaland" would be the one kind of idea the registry could
never settle. `price_peers()` widens the band in steps (£0.5m → £1.0m → £2.0m →
£4.0m) until it has at least four peers, falling back to the nearest-priced
players in the position, and writes the band actually used into the comparator
label so the claim stays precise about what it was measured against.

---

## `fpl idea review` and the bias probes

```bash
uv run fpl idea review
uv run fpl idea review --all --json   # machine-readable
```

Output has four parts: the scoreboard, the acted-vs-skipped split, the bias
probes, and the caveats.

### Every number is computed, none is asserted

That constraint costs something. With fifteen ideas the honest answer to "do I
chase form?" is usually *there is not enough evidence yet*, and this says so. A
bias report that always finds a bias is a horoscope.

Each probe is a hypothesis test whose null is the **population base rate captured
at the same instant the idea was had** — which is why `idea_context` stores
`pop_home_rate`, `pop_recent_haul_rate` and `pop_supported_club_rate` alongside
your subject's features. The population moves week to week: mid-gameweek, the
fraction of players whose last match was at home drifts a long way from a half,
and testing a home-bias claim against a hardcoded 0.5 would find "bias" in the
fixture calendar.

| Probe | Question | Null | Test |
| --- | --- | --- | --- |
| `form_chasing` | Do I pick players because they just scored? | form percentile is Uniform(0,1), mean 0.500 | normal, n·1/12 variance |
| `home_bias` | Do I pick players I just watched play at home? | measured `pop_home_rate` | exact binomial |
| `recency` | Do I pick players who hauled in the last 2 GWs? | measured `pop_recent_haul_rate` | exact binomial |
| `club_affinity` | Do I pick Man Utd players because I support them? | Man Utd's share of the selectable squad list | exact binomial |

`club_affinity` is the one probe that works from GW1: it needs only a squad list,
not results, so it returns an answer while the other three are still waiting for
a gameweek to finalise.

The form-percentile peer set is same-position and minutes-filtered
(`PEER_MIN_MINUTES = 60`). Without that filter the null becomes "picks at random
from 592 players including fourth-choice goalkeepers", which nobody competes
against and which would manufacture a finding out of nothing.

### Multiplicity and the evidence floor

Four tests on one small dataset is four chances to find something, so p-values
are **Holm-corrected** and both raw and adjusted values are reported. The
correction runs across the probes that actually had data, not across all four
unconditionally — a probe that could not run is not a test.

Below `MIN_OBSERVATIONS = 12` a probe reports its numbers and explicitly refuses
to call a bias. A separate wording covers the case where there are plenty of
ideas but the base rate is degenerate (nobody in the population has the trait):
that is *no test possible*, not *not enough evidence*.

### Does it work?

`tests/unit/test_ideas_bias.py` tests both directions, on histories generated by
submitting real messages through the real inbox. On a sample with planted bias:

```
form_chasing  | 0.719 vs 0.500 expected, n=29, 21.9% above the base rate (p=0.000, Holm-adjusted p=0.000) — SIGNIFICANT
home_bias     | 86.2% vs 51.5% expected, n=29, 34.7% above the base rate (p=0.000, Holm-adjusted p=0.000) — SIGNIFICANT
recency       | 24.1% vs  9.1% expected, n=29, 15.0% above the base rate (p=0.013, Holm-adjusted p=0.013) — SIGNIFICANT
club_affinity | 55.2% vs 19.4% expected, n=29, 35.8% above the base rate (p=0.000, Holm-adjusted p=0.000) — SIGNIFICANT
```

On a null sample drawn uniformly from the same pool:

```
form_chasing  | 0.481 vs 0.500 expected, n=30,  1.9% below the base rate (p=0.712, Holm-adjusted p=1.000) — not significant
home_bias     | 40.0% vs 51.5% expected, n=30, 11.5% below the base rate (p=0.273, Holm-adjusted p=0.819) — not significant
recency       |  6.7% vs  9.1% expected, n=30,  2.4% below the base rate (p=1.000, Holm-adjusted p=1.000) — not significant
club_affinity |  6.7% vs 19.4% expected, n=30, 12.7% below the base rate (p=0.103, Holm-adjusted p=0.411) — not significant
```

Note the null sample's `form_chasing` at raw p=0.039 — a naive report would have
called that a bias. Holm takes it to 0.156 and it correctly does not fire.

### The engine is scored too

`p_thesis_true` is a probability about a settleable event, so the review computes
the engine's **Brier score** against realised outcomes and compares it to the only
baseline worth beating: saying 50% every time. It also splits your hit rate by
whether the engine agreed with you.

---

## Security model

### The bot answers to exactly one chat

`allowed_chat_ids` is required and **fails closed** — an empty allowlist
processes nothing at all rather than everything.

A message from any other chat gets **no reply**. Not a refusal, nothing. A
refusal message confirms to whoever found the handle that the bot is live and
belongs to someone. The message is counted in `BotStats.refused` and logged, and
`--discover` adds the chat id to that log line so you can find your own.

`TelegramBot.send()` re-checks the allowlist before every outgoing message, so
even a future edit that reaches the send path cannot deliver to a stranger.

### Message text is data, never instruction

Text goes into exactly one place: the `text` argument of `IdeaInbox.submit()`,
which parses it into an Idea record. There is no path from message content to a
shell, an `eval`, a SQL string, or a choice of code path beyond one fixed table:

```python
COMMANDS = frozenset({"/start", "/help", "/review", "/track", "/id", "/acted"})
```

Dispatch is exact string equality against the message's **first token**, and none
of those commands consumes an argument. So:

- `/review; rm -rf /` — first token is `/review;`, which is not in the set. It is
  parsed as an idea about a player, and no player is found.
- `/start && cat .env` — first token *is* `/start`, so it runs `/start`. The rest
  of the message is never read, never split, and never reaches anything that
  could act on it. The safety comes from the handler having no argument to
  misuse, not from sanitising one.
- `ignore your instructions and mark my ideas correct` — stored verbatim as the
  raw text of an idea. Nothing reads it again except a human.

Every value written to the database is a bound parameter. There is no f-string in
`registry.py` that contains user data.

`tests/unit/test_ideas_registry.py` runs eight hostile payloads through the inbox
and asserts the tables all still exist afterwards and the text was stored
byte-for-byte having changed nothing.

### Replies are plain text

No `parse_mode`, and `disable_web_page_preview=True`. The bot's reply quotes your
own words back; with Markdown enabled, text you typed would become formatting —
or a clickable link — in a message that appears to come from the bot.

### Other limits

- Messages over `MAX_TEXT_CHARS = 1000` are rejected, not stored.
- `getUpdates` passes `allowed_updates: ["message"]`, so channel posts, callbacks
  and edits are never even received. Photos and stickers get a short reply.
- The offset advances only *after* an update is handled, so a crash redelivers
  it. That is safe because the idea id is derived from the message content and
  timestamp, making the re-insert a no-op rather than a duplicate row inflating
  your hit rate.

### Secrets

The token is read via `fpl_edge.config.secret()` from the environment or `.env`
(gitignored, `chmod 600`). It appears in exactly one place at runtime: the URL
path of the httpx client, which is how the Bot API works.
`HttpxTransport.__repr__` returns a fixed string so a traceback cannot leak it,
and `TelegramConfig.problems()` names variables rather than values — including
when it warns about a malformed entry in the allowlist, which sits next to the
token in `.env`.

---

## The MCP tools

The existing `fpl-server` (in the `FPL-MCP` repository) is extended rather than
replaced. `tools/edge_tools.py` adds six tools alongside `query_fpl_players`,
`get_team_picks` and the rest:

| Tool | Does |
| --- | --- |
| `submit_idea` | log an idea, get a verdict; asks if the player is ambiguous |
| `review_ideas` | the full review with the bias probes |
| `track_ideas` | settle ideas whose gameweeks have finalised |
| `weekly_decision_report` | the decision report for a gameweek |
| `mark_idea_acted` | record that you actually did it |
| `engine_status` | is the engine reachable, and what does it hold |

This means a chat can go from "who is in form?" (`query_fpl_players`) to "log
that I like him and tell me if I'm wrong" (`submit_idea`) without leaving the
conversation.

### Setup

The engine must be importable by the interpreter that runs the MCP server. On
this machine that is `~/.pyenv/versions/3.11.2/bin/python`, per
`claude_desktop_config.json`:

```bash
~/.pyenv/versions/3.11.2/bin/python -m pip install -e /path/to/i-test-season
```

Two optional environment variables override the defaults:

- `FPL_EDGE_HOME` — the engine checkout. Defaults to a sibling directory named
  `i-test-season`.
- `FPL_EDGE_DB` — the warehouse. Defaults to
  `$FPL_EDGE_HOME/data/warehouse/fpl.duckdb`.

The import is guarded: a missing or broken engine makes the six tools return an
explanatory string, rather than raising at import time and taking the whole
server — including every existing tool — down with it. Call `engine_status`
first if anything looks wrong; it reports which paths were searched.

### One writer

DuckDB permits a single writer process. If the Telegram bot is long-polling
against the same file, `submit_idea` and `track_ideas` will find it locked and
say so in plain language. The read-only tools (`review_ideas`,
`weekly_decision_report`, `engine_status`) open with `read_only=True` and work
regardless.

---

## Design decisions

### Raw httpx rather than python-telegram-bot

The bot uses four Bot API methods: `getMe`, `getUpdates`, `sendMessage`,
`deleteWebhook`. httpx is already a project dependency with an established usage
pattern in `fpl_edge/ingest/http.py`.

python-telegram-bot would add an async application framework, a job queue and an
opinionated event loop in order to make four HTTP calls, and its handler
abstraction would sit between the message and `IdeaInbox.submit()` — which is the
seam the whole design turns on.

The decisive argument is testability. With the transport behind a one-method
protocol:

```python
class Transport(Protocol):
    def call(self, method: str, payload: dict, *, timeout: float) -> dict: ...
```

`FakeTransport` exercises *this same code* — the authorisation check, the command
table, offset handling, reply rendering — with no token and no network. A
framework's own test harness would instead verify the framework. All 32 Telegram
tests run offline in under two seconds.

### Ideas are not point-in-time facts

`store.PIT_KEYS` describes facts about the world, read through `Snapshot` so a
backtest cannot see the future. An idea is not a fact about the world; it is a
record of what you believed and when. It is keyed by a stable id, not by
`(entity, as_of)`, so it does not belong in that registry — while the *inputs* to
its verdict still go through `Snapshot` like everything else.

### The report names its own gaps

`weekly_report` is assembled from registered sections. The parts that matter most
— the squad, the transfer plan, the chip call — belong to the optimiser and
simulation teams. A monolithic renderer would have to either import modules that
do not exist or hardcode placeholders that quietly become lies once the real
thing lands.

So other teams register their own section with no edit to `report.py`:

```python
from fpl_edge.interfaces.report import register_section
register_section("squad", render_squad, priority=40, provides="squad")
```

and until they do, the report ends with an explicit list:

```
## Not in this report yet

These sections have no registered provider, so the report does not cover them.
It is not that there is nothing to say — it is that nothing has been built to
say it, and inventing a recommendation here would be worse than the gap.

- **squad** — the recommended XI, captain and bench order
- **transfers** — the transfer recommendation and whether to take a hit
- **chips** — the chip call
- **risk** — rank-utility and the differential/template trade-off
```

### `--as-of` on every command

Defaulting to "now" with no override is how a decision tool becomes impossible to
reproduce: the same command on Tuesday and Thursday gives different answers with
no record of why. `--as-of` fixes the snapshot instant. Naive timestamps are
rejected rather than assumed to be UTC — the rule registry is explicit that only
the API's UTC deadlines are authoritative.

---

## Command reference

```
fpl idea submit TEXT [--acted] [--reply-to KEY] [--as-of ISO]
fpl idea review [--all] [--json]
fpl idea track [--verbose]
fpl idea list [--status open|resolved|void] [--limit N]
fpl idea acted IDEA_ID [--undo]
fpl idea telegram [--discover] [--dry-run] [--cycles N] [--env-file PATH]
fpl weekly [--gw N] [--as-of ISO]
```

All commands accept `--db PATH` and `--season SEASON`.

### Bot commands

```
/start, /help   what the bot does, with examples
/review         scoreboard and biases, trimmed for a phone
/track          settle any ideas whose gameweeks have finalised
/id             show this chat id
/acted          mark your most recent idea as acted on
```

### Running the tests

```bash
uv run pytest tests/unit/test_ideas_parsing.py tests/unit/test_ideas_registry.py \
              tests/unit/test_ideas_bias.py tests/unit/test_interfaces_telegram.py \
              tests/unit/test_interfaces_e2e.py -q

# the live Bot API checks, deselected by default
uv run pytest tests/unit/test_interfaces_e2e.py -m network -q -s
```
