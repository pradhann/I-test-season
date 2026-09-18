# Multi-user: any team id, default the owner's

Workstream D, spec half. This document is the migration checklist and the
contract for agent D2. It describes only behaviour the code in this repo can
support; where the code would have to change, the file is named.

The engine today is one operator. `fpl_edge/config.py:149` builds a module-level
`USER = UserConfig()` and thirty call sites read off it. The FPL refresh token
lives in a single process-wide `.env`. The solver plan, the chat conversations
and the manually entered squad all sit in paths that have no user in them. A
second person using the same server would see the owner's squad, the owner's
plan and the owner's chat history, and would hand their FPL cookie to the same
two environment variables.

The split this workstream makes is between state that describes the game and
state that describes a manager.

| | Contents | Where it lives after D2 |
| --- | --- | --- |
| Shared | Fixtures, player state, projections, odds, the creator corpus, elite and top-10k crawls, fitted fixture and forecast models, the semantic views | `data/warehouse/fpl.duckdb` and the parquet artefacts beside it, unchanged |
| Per user | `entry_id`, the FPL access and refresh tokens, the verification record, the my-team store, the solver plan, the chat conversations, the Anthropic key from workstream E | `{DATA_ROOT}/users/{user_id}/`, one directory per user |

Shared state stays exactly where it is. Nothing in this workstream touches the
warehouse schema, `views.sql`, or the single-writer contract.

---

## 1. Every current read of `USER` and `entry_id`

This is the migration checklist. Every row is either changed by D2 or is
explicitly recorded as staying.

The grep the brief asked for (`grep -rn "USER\b\|\.entry_id\|entry_id=" fpl_edge/`)
returns 184 lines. Most of those are rival managers' entry ids in the crawl,
the copying model and `views.sql`, which are shared warehouse data about other
people and are not affected. The rows below are the ones that resolve the
question "whose team is this" or read some other field off the `USER`
singleton, which is 36 reads across 18 files, plus the two definition lines in
`config.py` and one docstring mention.

**FPL data column.** `private` means the site can reach
`https://fantasy.premierleague.com/api/my-team/{id}/`, which needs the
requesting manager's own bearer token. `public` means it reads only
`entry/{id}/`, `entry/{id}/history/`, `entry/{id}/transfers/` or
`entry/{id}/event/{gw}/picks/`, which need no login. `warehouse` means the site
resolves an id against DuckDB and makes no FPL call. `label` means the value is
only printed.

### Sites that decide whose team is read

| file:line | What it does with it | FPL data | D2 action |
| --- | --- | --- | --- |
| `fpl_edge/config.py:82` | `UserConfig.entry_id = 4490171`, the owner default | none | Stays. This is the one place the owner's id is set. |
| `fpl_edge/config.py:149` | `USER = UserConfig()` | none | Stays, but only `anonymous_context()` may read `.entry_id` off it (section 2). |
| `fpl_edge/platform/scripts/squad.py:127` | `squad_overview` falls back to `USER.entry_id` | private then public then manual, via `QuestionRouter._team_state` | Takes `ctx`; `entry_id` leaves the params schema. |
| `fpl_edge/platform/scripts/squad.py:300` | Prints `USER.team_name` when the id matches the owner | label | Takes the name from `ctx.display_name`. |
| `fpl_edge/platform/scripts/brief.py:860` | `dashboard_brief` falls back to `USER.entry_id`, then calls `squad_overview` at `:906` | private, inherited | Takes `ctx`, passes `ctx` down rather than an id. |
| `fpl_edge/platform/scripts/planner.py:165` | `planner_grid` falls back to `USER.entry_id`, builds a `QuestionRouter` at `:215` | private | Takes `ctx`; `entry_id` leaves the params schema. |
| `fpl_edge/platform/scripts/ownership.py:792` | `_squad_state` builds a `QuestionRouter` on `USER.entry_id` for the coverage column | private | Takes `ctx`. `ownership_eo` gains a `ctx` parameter; the coverage column is blank for an anonymous context with no readable squad. |
| `fpl_edge/platform/scripts/ownership.py:1758` | `my_entry` for the "does this selection include you" line | warehouse | Takes `ctx.entry_id`. |
| `fpl_edge/interfaces/qa.py:58` | `QuestionRouter.__init__` default `entry_id=USER.entry_id` | private, at `:199` | Default is removed. `entry_id` becomes required, so a caller cannot get the owner's team by omission. |
| `fpl_edge/interfaces/qa.py:199` | `PrivateTeamClient().fetch(self.entry_id)` with no explicit token source | private | Takes a `TokenManager` bound to the requesting user's store. This is the single most important line in the migration. |
| `fpl_edge/interfaces/qa.py:204` | `MyTeamStore(self.entry_id)` on the default root | none | Takes the per-user root from `ctx`. |
| `fpl_edge/interfaces/qa.py:276` | Report title uses `USER.team_name` | label | Takes `ctx.display_name`. |
| `fpl_edge/myteam/report.py:106` | Weekly report state falls back to `USER.entry_id` | private at `:123`, public at `:113`, manual at `:108` | Takes a `UserContext`. Called from the CLI and the settlement chain, not from a request. |
| `fpl_edge/myteam/account.py:216` | `account_status` falls back to `USER.entry_id` | private token state, plus `entry/{id}/` for the label | Takes `ctx`. |
| `fpl_edge/myteam/account.py:338` | `connect` falls back to `USER.entry_id` | private | Takes `ctx`; the `TokenManager` it writes through is the user's own. |
| `fpl_edge/myteam/account.py:413` | `verify` falls back to `USER.entry_id` | private | Takes `ctx`. |
| `fpl_edge/myteam/cli.py:71` | Help text interpolates `USER.entry_id` | label | Stays. The CLI is the operator on their own machine. |
| `fpl_edge/myteam/cli.py:76` | `_entry_id` falls back to `USER.entry_id` | private at `:97`, public at `:131` | Stays, and builds a local `UserContext` from `--entry` plus the local store. |
| `fpl_edge/myteam/bot.py:76` | `TeamBot.entry_id` default is `USER.entry_id` | public only (`client_factory` is `PublicEntryClient`), plus the manual store at `:85` | Takes an explicit id. The Telegram bot is single-operator and out of scope for the web session, but the default is removed so the singleton can go. |
| `fpl_edge/jobs/deadline_dag.py:739` | `MyTeamStore(UserConfig().entry_id)` for the price radar's squad filter | none, local store | Takes the owner context explicitly. A scheduled job has no request, so it uses `owner_context()`. |
| `fpl_edge/interfaces/briefing.py:213` | Hardcoded `4490171` fallback for the squad lines | warehouse | Deleted. `entry_id` becomes required. |
| `fpl_edge/interfaces/briefing.py:228` | Hardcoded `4490171` printed as "the user's FPL entry id" | label | Deleted, same change. |
| `scripts/retro_report.py:253` | Hardcoded `entry 4490171` in the HTML subtitle | label | Reads the owner context. |
| `scripts/weekly_idea_report.py:174` | `USER.entry_id` and `USER.team_name` in the HTML subtitle | label | Reads the owner context. |
| `scripts/weekly_idea_report.py:251` | `USER.entry_id` in the git commit author email | label | Reads the owner context. |

### Sites that read `USER` for something other than identity

These are engine-wide preferences, not per-request state. The brief scopes
per-user state to `entry_id`, tokens, the my-team store, plans, conversations
and the Anthropic key, so these stay on `USER` and D2 does not migrate them.
They are listed so the checklist is complete and so nobody moves them by
accident.

| file:line | Field | Why it stays |
| --- | --- | --- |
| `fpl_edge/theses/create.py:125` | `USER.supported_club` | Bias control for the thesis generator, an engine setting |
| `fpl_edge/theses/create.py:166,247,473` | `USER.season` | The season the engine runs, not a per-user choice |
| `fpl_edge/interfaces/bias.py:258,273` | `USER.supported_club` | The club-affinity question the bias report asks |
| `fpl_edge/interfaces/inbox.py:496` | `USER.supported_club` | Same, tagging ideas for club bias |
| `fpl_edge/ingest/rivals/roster.py:348,380` | `USER.mini_leagues` | Which leagues the crawl walks |
| `fpl_edge/ingest/rivals/roster.py:349` | `USER.entry_id` seeded as a rival candidate | The owner's own team in the mini-league cohort. Public data. Stays until a second user actually has leagues, which is out of scope. |
| `fpl_edge/models/copying/report.py:222,223,224` | `USER.entry_id` compared against crawled panel rows | Warehouse only, an offline model report, no request |
| `fpl_edge/sim/experiments.py:525` | `USER.rank_utility` named in a docstring | Not a read |

### Counts

| Measure | Count |
| --- | --- |
| Lines returned by the brief's grep over `fpl_edge/` | 184 |
| Items listed in the two tables above | 39 |
| Of those, definitions rather than reads (`config.py:82,149`) | 2 |
| Of those, a docstring mention rather than a read (`sim/experiments.py:525`) | 1 |
| Actual reads to migrate or record as staying | 36, across 18 files |
| Of the 36, reads that can reach a private FPL endpoint | 11 |
| Of the 36, reads that reach public FPL endpoints only | 4 |
| Of the 36, reads that are warehouse, local store or label only | 21 |
| Hardcoded `4490171` outside `config.py` and `tests/` | 5, of which 2 are live fallbacks (`interfaces/briefing.py:213,228`), 1 is a printed label (`scripts/retro_report.py:253`), and 2 are documented HTTP 404 examples in docstrings (`fpl_edge/ingest/rivals/picks.py:9`, `fpl_edge/models/copying/features.py:19`) |

The eleven private reads are `interfaces/qa.py:199`, which is the one line that
actually constructs a `PrivateTeamClient` for a panel, and the ten that reach it
transitively or call `PrivateTeamClient` themselves:
`platform/scripts/squad.py:127`, `platform/scripts/brief.py:860`,
`platform/scripts/planner.py:165`, `platform/scripts/ownership.py:792`,
`interfaces/qa.py:58`, `myteam/report.py:106`, `myteam/cli.py:76`,
`myteam/account.py:216`, `myteam/account.py:338`, `myteam/account.py:413`.

The four public-only reads are `myteam/bot.py:76`, whose `client_factory` is
`PublicEntryClient`, and `ingest/rivals/roster.py:348,349,380`, which walk
league standings.

The two docstring 404 examples are prose, not code, but they name the owner's
id in a shipped module. D2 replaces them with a placeholder so the acceptance
grep is unambiguous.

---

## 2. `UserContext`

A new module, `fpl_edge/platform/user_context.py`.

```python
@dataclass(frozen=True, slots=True)
class UserContext:
    user_id: str            # "anonymous", or the identity id from workstream E
    entry_id: int           # the FPL team this context reads
    authenticated: bool     # False for the anonymous default
    display_name: str | None = None   # the FPL team name, from entry/{id}/
    store: UserStore | None = None    # None when anonymous

    @property
    def can_read_private(self) -> bool:
        """True only when this context owns a stored FPL refresh token."""
        return self.store is not None and self.store.has_fpl_tokens()

    def tokens(self) -> TokenManager:
        """The token manager for THIS user. Raises when anonymous."""

    def anthropic_key(self) -> str | None:
        """Decrypted per-user key, or None. Written by workstream E,
        declared here so D2 and E2 do not disagree about the field."""
```

`UserStore` is described in section 3. The context holds the store handle, not
the decrypted secrets: nothing is decrypted until a call actually needs it, and
no secret is ever a dataclass field that a repr could print.

### How it is constructed

One FastAPI dependency in the same module, wired into `fpl_edge/platform/app.py`:

```python
def current_user(request: Request) -> UserContext: ...
```

The dependency resolves identity through a single function,
`resolve_identity(request) -> Identity | None`. Until workstream E lands, that
function always returns `None` and every request gets the anonymous context.
When E lands it reads the session cookie. Tests drive two users by installing
their own resolver through `app.dependency_overrides[current_user]`, which is
FastAPI's own mechanism and ships no back door.

An environment-flagged `X-FPL-Edge-User` header was considered for the same
purpose and rejected: a header that selects a user identity is an
authentication bypass whether or not a flag guards it, and the flag would
outlive the workstream.

### What anonymous means

| Property | Value |
| --- | --- |
| `user_id` | `"anonymous"` |
| `entry_id` | `UserConfig().entry_id`, which is 4490171 |
| `authenticated` | `False` |
| `store` | `None` |
| `can_read_private` | `False`, structurally, because there is no store |
| Readable | Every shared panel, plus the owner's public team from `entry/{id}/`, `entry/{id}/history/`, `entry/{id}/transfers/` and `entry/{id}/event/{gw}/picks/` |
| Not readable | `my-team/{id}/`, the owner's my-team store, the owner's plans, the owner's conversations |
| Writable | Nothing |

Before the first deadline of a season, `entry/{id}/event/{gw}/picks/` returns
404 for everyone, so an anonymous visitor sees the squad panel's empty state
with its existing reason text. That is correct and is not a regression: it is
what an unauthenticated visitor is entitled to see.

`anonymous_context()` is the only function in the repo permitted to read
`USER.entry_id`. A test in `tests/unit/` asserts that by grepping the tree,
which is why the field stays at `fpl_edge/config.py:82` rather than being
renamed. One place, greppable, and `tests/unit/test_config.py:11` keeps
working unchanged.

### The private-endpoint invariant

**`PrivateTeamClient` is only ever constructed with a `TokenManager` bound to
the requesting user's own store, and is only ever asked for that user's own
`entry_id`.**

Three code changes make that structural rather than a rule people remember.

| File | Change |
| --- | --- |
| `fpl_edge/myteam/tokens.py:109` | `TokenManager.env_path` defaults to `ENV_PATH`, a process-wide file. The default is removed. A `TokenManager` must be handed a path, so no caller can pick up the operator's tokens by writing `TokenManager()`. |
| `fpl_edge/myteam/private.py:99` | `PrivateTeamClient()` with no arguments falls back to `secret("FPL_SESSION_COOKIE")`, the same process-wide read. The no-argument construction is removed. |
| `fpl_edge/myteam/private.py:145` | `fetch(entry_id)` builds `my-team/{entry_id}/`. It gains an assertion that `entry_id` equals the id the token manager's store is bound to, and raises otherwise. FPL would reject the mismatch anyway; the point is not to send it. |

`fpl_edge/myteam/sources.py:62` already refuses any URL containing `/my-team/`,
`/me/` or `/accounts/login` through `forbid_authenticated_url`, and
`PublicEntryClient._get` calls it on every request. That guard stays and is the
reason the anonymous path cannot reach a private endpoint even by a bug in a
caller.

---

## 3. The per-user store

### Location

The root is one constant, `USER_DATA_ROOT`, in a new module
`fpl_edge/platform/user_store.py`:

```python
USER_DATA_ROOT = Path(os.environ.get("FPL_EDGE_DATA_ROOT", "data")) / "users"
```

On the Mac that is `data/users/`. On Railway, workstream B sets
`FPL_EDGE_DATA_ROOT` to the mount point of the attached volume, so the store
lands on the same volume as `fpl.duckdb` and survives a redeploy. D2 owns the
constant and B owns the value. No other module builds a user path.

### Layout

```
{USER_DATA_ROOT}/{user_id}/
    profile.json            entry_id, display_name, created, updated   plaintext
    fpl_tokens.enc          access_token, refresh_token, issuer, client_id
    anthropic_key.enc       key, last4, added                          workstream E
    account.json            the verification record                    plaintext
    myteam/entry_{id}.json  the manually confirmed squad               plaintext
    plans/transfer_plan.json  the solver artefact
    jobs/                   solve run logs, status and exit files
    chat/{conv_id}/events.jsonl, meta.json
    chat/assets/{id}.png
```

`user_id` is a hex string with no path separators. `UserStore.__init__`
validates it against `^[0-9a-f]{8,64}$` and refuses anything else, so an
identity value from workstream E cannot become a path traversal.

`profile.json` is plaintext on purpose. An FPL entry id and a team name are
public: `entry/{id}/` serves both to anyone. Encrypting them would suggest they
are secret and would make the directory unreadable for support.

### Encryption

Fernet, from `cryptography`. The package is already in `uv.lock` as a
transitive dependency and D2 promotes it to a declared dependency in
`pyproject.toml`.

| Item | Value |
| --- | --- |
| Key | `FPL_EDGE_SECRET_KEY`, a 32-byte urlsafe-base64 Fernet key |
| Where it lives | Railway's secret store in production, `.env` on the Mac. Never in the repo, never in an image layer, never logged. |
| What is encrypted | `fpl_tokens.enc` and `anthropic_key.enc`, nothing else |
| Missing key | The server refuses to start if any user directory exists and the key is unset, rather than starting and silently treating every user as disconnected. `fpl_edge/config.py:32 secret()` already raises on a missing required secret; this reuses it. |
| Logging | The existing rule holds: `fpl_edge/myteam/tokens.py` reports expiry instants and HTTP status and never the credential. `UserStore` follows it, and its `__repr__` prints the path and whether a secret is present, never a value. |

The refresh grant is single-use and cross-process, which is why
`fpl_edge/myteam/tokens.py:135` takes a file lock on `.env.lock`. That lock
moves with the tokens: the lock file becomes
`{USER_DATA_ROOT}/{user_id}/fpl_tokens.enc.lock`, so two requests for the same
user still serialise and two different users no longer block each other.

### Key rotation

`MultiFernet([new_key, *old_keys])` reads with any key in the list and writes
with the first. The environment carries `FPL_EDGE_SECRET_KEY` and an optional
`FPL_EDGE_SECRET_KEY_OLD`.

| Step | What happens |
| --- | --- |
| 1 | Set `FPL_EDGE_SECRET_KEY` to the new key and `FPL_EDGE_SECRET_KEY_OLD` to the previous one, then restart. Every existing file still decrypts. |
| 2 | Run the rewrap pass, a function in `user_store.py` exposed as a CLI command. It walks `{USER_DATA_ROOT}/*/` and rewrites each `.enc` file through `MultiFernet.rotate`, atomically, under the same per-user lock. |
| 3 | Unset `FPL_EDGE_SECRET_KEY_OLD` and restart. |

If a key is lost entirely, the ciphertext is gone and cannot be recovered. The
store then reports the user as not connected, deletes the unreadable file, and
the Account tab shows the existing paste flow. It never falls back to another
user's tokens and never falls back to the operator's `.env`.

---

## 4. Artefacts that are per-user today but stored as if shared

Each of these is personalised to entry 4490171 and sits in a path with no user
in it. A second user on the same server would read and overwrite the owner's
copy.

| Artefact | Path today | Moves to | Files that must change |
| --- | --- | --- | --- |
| The my-team store | `data/myteam/entry_{id}.json` | `{root}/{user_id}/myteam/entry_{id}.json` | `fpl_edge/myteam/store.py:31` (`DEFAULT_ROOT`). The `root=` keyword already exists on `MyTeamStore.__init__` and every test passes it, so only the default and the four callers that omit it change: `fpl_edge/interfaces/qa.py:204`, `fpl_edge/myteam/report.py:108`, `fpl_edge/myteam/bot.py:85`, `fpl_edge/jobs/deadline_dag.py:739`. |
| The solver plan | `data/warehouse/transfer_plan.json` | `{root}/{user_id}/plans/transfer_plan.json` | `fpl_edge/cli/recommend.py:37` writes it; `fpl_edge/platform/app.py:1293` (`_TRANSFER_PLAN_PATH`) and `fpl_edge/platform/scripts/brief.py:88` read it. The path becomes a function of the context rather than a module constant. `web/dist/js/views/planner.js:826,943` already print `tplan.path` from the payload, so the UI needs no change. |
| The GW1 plan | `data/warehouse/gw1_plan.json` | `{root}/{user_id}/plans/gw1_plan.json` | `fpl_edge/cli/solve.py:489`, `fpl_edge/platform/app.py:1160`, `fpl_edge/interfaces/squad_section.py:22`, `fpl_edge/interfaces/qa.py:293`, `scripts/gw1_squad.py:161` |
| Solve run logs | `data/warehouse/jobs/` | `{root}/{user_id}/jobs/` | `fpl_edge/platform/solve_runner.py:40` (`JOBS_DIR`) |
| Chat conversations | `data/warehouse/chat/{conv_id}/` | `{root}/{user_id}/chat/{conv_id}/` | `fpl_edge/platform/chat_agent.py:75` (`CHAT_ROOT`). The agent already takes `root=` at `chat_agent.py:428`, so the change is one agent instance per user rather than one per server, keyed in `app.py` where the singleton is built. |
| Chat assets | `data/warehouse/chat/assets/{id}.png` | `{root}/{user_id}/chat/assets/` | `fpl_edge/platform/chat_agent.py:439`, and the serving route `fpl_edge/platform/app.py:1027` which must resolve the asset inside the requesting user's directory and 404 outside it |
| The FPL verification record | `data/cache/fpl_account.json` | `{root}/{user_id}/account.json` | `fpl_edge/myteam/account.py:64` (`RECORD_PATH`) |
| FPL tokens | `.env` keys `FPL_ACCESS_TOKEN`, `FPL_REFRESH_TOKEN`, `FPL_OAUTH_ISSUER`, `FPL_OAUTH_CLIENT_ID` | `{root}/{user_id}/fpl_tokens.enc` | `fpl_edge/myteam/tokens.py:157,159,310-322` |
| The briefing intel artefact | `data/warehouse/briefing_intel.json` | `{root}/{user_id}/briefing_intel.json` | `fpl_edge/platform/briefing_intel.py:73` (`ARTEFACT_NAME`). It reads `squad_overview` and `dashboard_brief` (`briefing_intel.py:79,85`), so its content is the owner's squad. |
| The retro report | `data/warehouse/retro_report.html` | `{root}/{user_id}/retro_report.html` | `scripts/retro_report.py:15` |

Two rules govern the move.

**Conversations are keyed by user then conversation id, not by conversation id
alone.** `chat_agent.py:518` validates a conversation id against
`^[0-9a-f]{32}$` and `:522` resolves it under a single root. With one root per
user, a conversation id from another user's directory simply does not exist and
`UnknownConversation` is raised, which is the correct answer and needs no extra
ownership check.

**The solve stays one at a time for the whole server, even though the artefact
is per user.** `fpl_edge/platform/solve_runner.py` enforces one running solve
because the MILP takes the DuckDB write lock when it commits. That constraint
is about the shared warehouse, not about the user, so the lock stays global and
a second user's solve request returns the running status with a note saying the
server is busy. Making the lock per user would put two writers on one DuckDB
file, which the store does not support.

No migration script ships. On first boot, an absent per-user file is the
existing empty state: the my-team store returns no confirmed squad, the plan
route returns `exists: false` with its existing reason, the conversation list is
empty. The owner's existing files are moved into `{root}/owner/` by hand, as a
one-line `mv` documented in `DEPLOYMENT.md`, because doing it in code would mean
shipping a migration that runs once and is then dead.

---

## 5. The panel contract change

A panel script is a pure function over a warehouse read copy and its validated
params. The registry at `fpl_edge/platform/registry.py` gives it a fresh read
copy, validates params in and result out, and stamps provenance.

### How the context reaches a script

`run_script(name, params, db=...)` gains one keyword-only argument, `ctx`. The
registry inspects the script's signature and passes `ctx=` only to scripts that
declare a `ctx` parameter. A script that does not declare it cannot receive it.

The context is **not** a param. Params are echoed back in the run record and in
the panel's provenance, and a user id in that record would leak into every
screenshot and every log line. The context is a separate argument for that
reason.

### `entry_id` leaves the params schemas

`entry_id` is currently a declared param on three scripts
(`squad.py:39`, `planner.py:51`, `brief.py:159`). It is removed from all three.
The id comes only from the context.

This is the structural half of the private-endpoint invariant. If a caller can
pass `entry_id` in the request body, and the server has the requesting user's
bearer token, then `PrivateTeamClient.fetch(other_id)` is one bad line away.
Removing the param means the id and the token always come from the same object.
The acceptance test is a grep: no registered script declares `entry_id` in its
params schema.

### Which panels change

| Panel id | Script | Change |
| --- | --- | --- |
| `squad` | `squad_overview` | Takes `ctx`. Its whole payload is one manager's team. |
| `brief` | `dashboard_brief` | Takes `ctx`. It calls `squad_overview` at `brief.py:906` and reads the plan artefact. |
| `planner` | `planner_grid` | Takes `ctx`. Reads the squad and the plan. |
| `ownership` | `ownership_eo` | Takes `ctx`. Shared data except the coverage column (`ownership.py:792`) and the "includes you" line (`ownership.py:1758`). Both degrade to their existing blank states for a context with no readable squad. |

### Which panels do not change

These read only shared warehouse state. Verified by grep: none of their modules
contains `USER` or a user-identity `entry_id`.

| Panel id | Script | Module |
| --- | --- | --- |
| `projections` | `projection_table` | `scripts/projections.py` |
| `fixtures` | `fixture_board` | `scripts/fixtures.py` |
| `fixture_detail` | `fixture_detail` | `scripts/fixtures.py` |
| `player_radar` | `player_radar` | `scripts/radar.py` |
| `player_profile` | `player_profile` | `scripts/player_profile.py` |
| `creator_report_card` | `creator_report_card` | `scripts/creators.py` |
| `creator_board` | `creator_board` | `scripts/creators.py` |
| `creator_detail` | `creator_detail` | `scripts/creators.py` |
| `player_chatter` | `player_chatter` | `scripts/creators.py` |
| `pipeline_board` | `pipeline_board` | `scripts/pipelines_panel.py` |
| `pipeline_run_log` | `pipeline_run_log` | `scripts/pipelines_panel.py` |

`scripts/creators.py` contains 58 lines mentioning `entry_id`, all of which are
`dim_panel_member.entry_id`: the crawled entry ids of tracked creators. Those
are shared warehouse data about other people and are unaffected.

`creator_board` reads the owner's squad today only through
`squad_overview`'s payload as consumed by the UI, not by calling it. If
`docs/platform/creators_design_A.md` line 549's personalised lanes are ever
built into the script, it joins the first table.

### The rule

**A panel script never reads `USER`.** It receives a `ctx` or it reads shared
state, and there is no third option. Enforced by a test that imports every
registered script module and asserts that none of them imports `USER` from
`fpl_edge.config`.

---

## 6. The Account tab: the team-id input

A new route in `fpl_edge/platform/routes_account.py`:

```
POST /api/account/entry   {"entry_id": 1234567}
```

It validates before it stores.

| Step | Detail |
| --- | --- |
| Request | `GET https://fantasy.premierleague.com/api/entry/{entry_id}/` with the repo's `USER_AGENT`, a 15 second timeout, and no cookie or bearer header. This is the public endpoint, so it works for an anonymous user validating their own id before they have connected anything. |
| 200 | Store `entry_id`, `name` and the joined `player_first_name` plus `player_last_name` into `profile.json`. Respond with the id and the team name so the tab can show what it saved. |
| 404 | No entry with that id. Nothing is stored. The response says the id was not found and that the id is the number in the URL when the manager views their own points page. |
| Other non-200, or a network failure | The check could not be completed. Nothing is stored. The response says so and offers a retry, and does not claim the id is invalid. |
| An id that is not a positive integer | 400 before any request is made. |

`fpl_edge/myteam/account.py:243 default_entry_lookup` already makes exactly this
call, but its `except Exception` at `:262` returns `(None, None)` for every
failure, so it cannot tell a 404 from a timeout. That is correct for its current
job, which is fetching a cosmetic label after a connect has already succeeded.
It is wrong for a validator. D2 adds a second function in the same file that
returns the status code and the body, and `default_entry_lookup` becomes a thin
wrapper over it so there is still one place that knows the URL.

Changing the stored `entry_id` while FPL tokens are stored invalidates those
tokens for the new id, because a bearer token is bound to the account it was
issued for. The route therefore clears `fpl_tokens.enc` and `account.json` when
the id changes, and the response says the FPL connection was cleared and needs a
fresh paste. Leaving the tokens in place would leave the private fetch failing
with an authentication error that looks like an expired session.

The loopback guard at `routes_account.py:37 _require_loopback` stays for now.
Workstream E replaces it with the session check and deletes it, per E's own
acceptance. D2 must not weaken it in the meantime: an account route that answers
a non-loopback client before sessions exist is an open credential form.

---

## 7. Acceptance, as testable statements

| # | Statement | How it is proven |
| --- | --- | --- |
| 1 | Two users with different team ids see different squads from one running server. | One `TestClient` over one app. Two contexts installed through `app.dependency_overrides`, with different `entry_id` and different store roots. `POST /api/scripts/squad_overview/run` twice, assert the two payloads carry different `entry_id` and different `starters`. |
| 2 | Two users see different plans. | Write a different `plans/transfer_plan.json` under each user root. `GET /api/solve/transfer-plan` twice, assert each gets its own. |
| 3 | Two users see different conversations. | Create a conversation as user A. `GET /api/conversations` as user B returns an empty list. `GET /api/conversations/{a_conv_id}/events` as B returns 404. |
| 4 | The owner's id is set in exactly one place. | `grep -rn "4490171" fpl_edge/ scripts/ web/` returns only `fpl_edge/config.py:82`. |
| 5 | Exactly one function reads `USER.entry_id`. | A test greps `fpl_edge/` and `scripts/` for `USER.entry_id` and asserts the only hit is inside `anonymous_context` in `fpl_edge/platform/user_context.py`. |
| 6 | No panel script reads `USER`. | A test imports every module named by `PANELS` in `fpl_edge/platform/panels.py` and asserts none of them binds `USER` from `fpl_edge.config`. |
| 7 | No panel script takes `entry_id` as a param. | A test walks the registry and asserts `entry_id` is absent from every `params_schema`. |
| 8 | A private endpoint is never reached with another user's tokens. | A test builds a context for user A, calls `squad_overview` with an id belonging to B, and asserts the private client raises before any HTTP call is attempted. A second test asserts `TokenManager()` with no path raises a `TypeError`. |
| 9 | An anonymous request reads only public endpoints. | A test installs a transport that records every URL, runs the anonymous context through `squad_overview`, `dashboard_brief` and `planner_grid`, and asserts no recorded URL contains `/my-team/`. |
| 10 | An anonymous request gets the owner's public team. | Assert the anonymous payload's `entry_id` equals `UserConfig().entry_id`, and that its `provenance_source` is never `private_api`. |
| 11 | Tokens and keys are not in any log line or response body. | Write a known token into a user store, drive the account routes and the squad panel with logging captured at DEBUG, and assert the token string appears in neither the captured log nor any response body. |
| 12 | A stored token is not readable without the key. | Write tokens, read the `.enc` file as bytes, assert the plaintext token is not a substring. Then read it back with the wrong key and assert the store reports not connected rather than raising an unhandled exception. |
| 13 | Key rotation preserves access. | Write with key A, add A as the old key and B as the new, assert the read still succeeds, run the rewrap, remove A, assert the read still succeeds. |
| 14 | A user id cannot escape its directory. | `UserStore("../../etc")` raises. |
| 15 | The team-id input refuses an unknown id. | Stub the public endpoint to 404, `POST /api/account/entry`, assert 404 with the remediation text and that `profile.json` is unchanged. |
| 16 | The 61 existing test fixtures still use 4490171. | The baseline suite result is unchanged. Statement 4's grep excludes `tests/`. |
| 17 | The pre-existing failure set has not grown or changed membership. | `uv run pytest -q > out.txt 2>&1; echo "REAL_PYTEST_EXIT=$?" >> out.txt`, then `grep "^FAILED" out.txt \| sort` diffed against the recorded baseline. |

---

## 8. Ordering against workstreams B and E

D2 can be built, tested and merged before either B or E lands. Two seams make
that true.

### The store root comes from B, with a working default

| | |
| --- | --- |
| What D2 owns | The constant `USER_DATA_ROOT` in `fpl_edge/platform/user_store.py` and the layout under it |
| What B owns | The value of `FPL_EDGE_DATA_ROOT`, which B sets to the volume mount point in `railway.toml` and documents in `DEPLOYMENT.md` |
| Default until B lands | `data/users`, relative to the repo root, which is where every other data directory already sits and is already gitignored |
| What B must not do | Introduce a second way to build a user path. The volume layout section of `DEPLOYMENT.md` names this directory and points here. |

D2 should therefore land before or alongside B, not after: B's `make deploy-check`
boots the image against an empty volume and asserts every panel returns a
structured empty state, and the per-user directories are part of what empty
means.

### The identity comes from E, with a stub

| | |
| --- | --- |
| What D2 owns | `UserContext`, `current_user`, `anonymous_context()`, and the `resolve_identity(request) -> Identity \| None` seam |
| What D2 ships | `resolve_identity` returns `None` always. Every request is anonymous. The server behaves exactly as it does today, reading the owner's public team, with the private path reachable only from the CLI and the loopback-guarded account routes. |
| What E owns | The body of `resolve_identity`: the session cookie, its verification, and the mapping from a Google subject to a `user_id` |
| How D2 tests two users without E | `app.dependency_overrides[current_user]`, FastAPI's own mechanism. No flag, no header, no code path that exists only for tests. |
| What E must not do | Re-derive the store path or the context shape. E adds one method body and populates `UserContext.user_id`. |

### The Anthropic key field comes from E

D2 declares `UserContext.anthropic_key()` and the `anthropic_key.enc` slot in
the store, and nothing reads them. `fpl_edge/platform/chat_agent.py` and
`fpl_edge/platform/briefing_intel.py` keep their current behaviour in D2: the
operator's CLI login, with `ANTHROPIC_*` scrubbed from the subprocess
environment at `chat_agent.py:707` and `briefing_intel.py:388`. E2 changes those
two modules to take the key from the context and to refuse with a remediation
message when none is set.

Declaring the field in D2 rather than E2 is deliberate. The store's encryption,
its rotation and its "no secret in a repr" rule are one mechanism, and building
it twice for two secrets is how the second one ends up weaker than the first.

### Summary

| Workstream | Blocks D2 | Blocked by D2 |
| --- | --- | --- |
| A (refactor) | Yes, D2 rebases on A's module map | No |
| B (Railway) | No, D2 has a working default root | Partly: B's volume layout documents D2's directory |
| E (auth) | No, D2 has an anonymous default and a dependency override | Yes: E needs `UserContext` and the encrypted store to exist |
