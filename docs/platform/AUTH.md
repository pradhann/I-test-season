# AUTH: identity, sessions, and per-user model keys

Workstream E, spec half. E2 implements this. Read section 1 before anything
else, because the ask that produced this document conflates two separate
things and building it as asked would produce something that cannot work.

Status: specification. Nothing here is implemented yet. Every file path named
below is read-only for this document.

---

## 1. Two different things: identity, and access to Claude

**Google sign-in gives identity.** It answers one question: who is this
visitor. It returns a stable user id (`sub`) and a verified email address.
That is all it returns and all this platform needs from it.

**Google sign-in does not give access to Claude.** There is no OAuth flow
published by Anthropic that lets a third-party web application act on a
visitor's Claude Pro or Claude Max subscription. Subscription auth belongs to
the Claude apps and the Claude Code CLI, which hold their own login on the
machine they run on. A web service cannot ask a visitor to "connect your
Claude account" and then send messages billed to that visitor's plan, because
no such grant exists to ask for.

**So the design is two steps, not one.**

1. Google OAuth for identity. The visitor signs in with Google and the server
   knows who they are.
2. Bring your own key for model access. Each signed-in user pastes their own
   Anthropic API key from `console.anthropic.com`. The server stores it
   encrypted, scoped to that user, and uses it only for that user's chat turns
   and that user's briefing. The key is metered on that user's own Anthropic
   account, and the user can revoke it at Anthropic at any time without
   touching this platform.

**The operator's own login is never used on behalf of another user.** Today
`fpl_edge/platform/chat_agent.py` and `fpl_edge/platform/briefing_intel.py`
run against the owner's Claude Code CLI login on the owner's Mac. In the
multi-user design, a request from a user who is not the operator never reaches
that login, never reads `ANTHROPIC_*` from the server process environment, and
never spawns a CLI that would fall back to a machine-level credential. Section
7 gives the mechanism and section 10 gives the test that proves it.

**Correction, 2026-09-20.** The paragraph below said a user could not spend
their own Claude subscription. That is right about a sign-in flow and wrong
about the outcome. `claude setup-token` mints a long-lived token from the
user's own subscription on the user's own machine, and the Agent SDK reads
that token from `CLAUDE_CODE_OAUTH_TOKEN` in `ClaudeAgentOptions.env`, beside
`ANTHROPIC_API_KEY`. So a user pastes either an API key, billed on their
Anthropic account, or a setup token, spending their own subscription, and the
server puts each in the variable its kind belongs in. What remains true is
that this server cannot ask for that grant: there is no OAuth flow to send a
visitor through, so the user mints the value themselves and pastes it, which
is the same shape as the API key it sits beside.

**If the owner wants users signed in to their Claude Max subscriptions
through an OAuth flow, that is not something this codebase can build.** Not
with more effort, not with a different library, not by proxying the CLI. The
grant does not exist. The two
options that do exist are the one specified here (each user brings an API key
and pays for their own tokens) or the operator paying for everyone from a
single operator key, which is a billing decision and a rate-limit decision,
not an auth design. This document specifies the first. If the owner later
chooses the second, the only change is where `UserContext.anthropic_key` comes
from, and every other rule below stands unchanged.

---

## 2. Google OAuth 2.0, Authorization Code with PKCE

### 2.1 Endpoints and routes

Google's endpoints, fixed:

| Purpose | URL |
| --- | --- |
| Issuer | `https://accounts.google.com` |
| Authorization | `https://accounts.google.com/o/oauth2/v2/auth` |
| Token | `https://oauth2.googleapis.com/token` |
| JWKS | `https://www.googleapis.com/oauth2/v3/certs` |

Three new routes on this server:

| Method | Route | What it does |
| --- | --- | --- |
| GET | `/auth/google/start` | Mints state, nonce and PKCE verifier, sets the handshake cookie, returns 302 to Google's authorization endpoint |
| GET | `/auth/google/callback` | Exchanges the code, verifies the id_token, creates or updates the user row, issues the session cookie, returns 302 to `next` |
| POST | `/auth/logout` | Deletes the session row and clears all three cookies, returns 204 |

### 2.2 `/auth/google/start`

Query parameters accepted: `next`, a same-origin path only. Anything that is
not a path beginning with a single `/` is replaced by `/`. An open redirect
here would let a phishing page bounce a real sign-in to an attacker's host.

Generated per call, all from `secrets`:

| Value | Construction | Purpose |
| --- | --- | --- |
| `state` | `secrets.token_urlsafe(32)` | Binds the callback to this browser and this start. Compared on return with `hmac.compare_digest`. |
| `nonce` | `secrets.token_urlsafe(32)` | Binds the id_token to this start. Compared against the `nonce` claim. |
| `code_verifier` | `secrets.token_urlsafe(64)` | PKCE. Never leaves this server except in the token exchange. |
| `code_challenge` | `base64url(sha256(code_verifier))`, no padding | Sent to Google with `code_challenge_method=S256`. |

All four, plus the sanitised `next`, go into one short-lived handshake cookie
`itest_oauth`: HttpOnly, Secure, SameSite=Lax, Path=/auth, Max-Age=600. Lax is
correct and required, because the return from Google is a top-level GET
navigation, which Lax permits. Ten minutes is the whole budget for a sign-in;
an abandoned handshake expires on its own and leaves no server-side row to
clean up.

Redirect parameters sent to Google:

```
client_id=$GOOGLE_CLIENT_ID
redirect_uri=$GOOGLE_REDIRECT_URI
response_type=code
scope=openid email
state=<state>
nonce=<nonce>
code_challenge=<challenge>
code_challenge_method=S256
access_type=online
prompt=select_account
```

`scope` is exactly `openid email`. Not `profile`, not `https://www.googleapis.com/auth/userinfo.profile`, nothing else. The platform needs an id and an
address to display. Asking for more would put a scope on the consent screen
that this application cannot justify.

### 2.3 `/auth/google/callback`

Order of operations, and each step is a hard failure that redirects to
`/?auth_error=<class>` rather than rendering a stack trace:

1. Read `itest_oauth`. Absent or unreadable means the handshake expired.
   Class `handshake_expired`.
2. If Google returned `error`, stop. Class `consent_declined` for
   `access_denied`, otherwise `provider_error`.
3. Compare the returned `state` to the cookie's state with
   `hmac.compare_digest`. Mismatch is class `state_mismatch`, and it is
   logged with the request's IP because it is either a stale tab or a forged
   callback.
4. POST to the token endpoint with `grant_type=authorization_code`, the
   `code`, `redirect_uri`, `client_id`, `client_secret`, and `code_verifier`.
   Use the `httpx` client this repo already depends on, with an explicit
   10 second timeout.
5. Verify the `id_token`. Use `pyjwt` with `PyJWKClient` against Google's
   JWKS, cached in process for an hour. Required checks: signature,
   `iss` is `https://accounts.google.com` or `accounts.google.com`, `aud`
   equals `GOOGLE_CLIENT_ID`, `exp` is in the future, `nonce` equals the
   cookie's nonce, and `email_verified` is true. A failure at any one of these
   is class `token_invalid`, and no user row is written.
6. Upsert the user. `sub` is the primary key. `email` is overwritten on every
   sign-in, because a Google account can change its address and the display
   should follow it.
7. Create the session (section 3) and set the cookies.
8. Delete `itest_oauth` by setting it expired.
9. 302 to the `next` from the handshake cookie.

### 2.4 What is stored from the id_token

| Claim | Stored as | Why |
| --- | --- | --- |
| `sub` | `users.sub`, primary key | The only stable identifier. An email address is not one. |
| `email` | `users.email` | Shown in the header so a user can tell which account they are in. |

Nothing else. Not `name`, not `picture`, not `hd`, not the refresh token, not
the access token, not the raw id_token. The access token is discarded the
moment the id_token is verified, because this platform calls no Google API
after sign-in. A row in `users` is three columns of identity and two
timestamps, and that is the whole of what Google sign-in leaves behind.

### 2.5 Environment variables the owner sets

| Variable | Example | Notes |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` | `8471...apps.googleusercontent.com` | From the Console, step 4 below |
| `GOOGLE_CLIENT_SECRET` | `GOCSPX-...` | Same screen. Railway secret store only, never `.env` in the repo |
| `GOOGLE_REDIRECT_URI` | `https://itest.up.railway.app/auth/google/callback` | Must match a registered URI byte for byte, including scheme, host, port and path |
| `SESSION_SECRET` | 43 chars of base64url | `python -c "import secrets;print(secrets.token_urlsafe(32))"` |
| `USER_KEY_ENC_SECRET` | 44 chars of base64 | `python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"` |
| `OPERATOR_EMAIL` | `nripeshpradhan@gmail.com` | The one account that gets the operator rows in the matrix |
| `PUBLIC_ENTRY_ID` | `4490171` | The FPL entry anonymous visitors see on the shared panels |

`fpl_edge/config.py:secret()` already reads environment first and `.env`
second, and raises with a usable message when a required name is missing. Use
it. Do not add a second reader.

### 2.6 Registering the client in Google Cloud Console

Five minutes, once, by the owner. Nobody else can do it (section 9).

1. Open `console.cloud.google.com` and select a project, or create one called
   `i-test-season`.
2. APIs and Services, then OAuth consent screen. User type External. Fill in
   app name, user support email, developer contact email. Save.
3. On the Scopes step add `openid` and `.../auth/userinfo.email` only. Save.
   While the app is in Testing, add your own Google address under Test users.
   Publishing is only needed when someone other than the test users signs in.
4. APIs and Services, then Credentials, then Create credentials, then OAuth
   client ID. Application type Web application.
5. Under Authorised redirect URIs add both of these, exactly:
   - `https://<your-app>.up.railway.app/auth/google/callback`
   - `http://localhost:8321/auth/google/callback`
   Google permits `http` for `localhost` specifically, which is what makes
   local development work without a tunnel.
6. Create. Copy the client ID and client secret into Railway's variables, and
   into your local `.env` for the localhost redirect.

A redirect URI that differs by a trailing slash, by `http` versus `https`, or
by `www`, is a different URI and Google refuses it with `redirect_uri_mismatch`. That error is the single most common failure here and it
is always this.

---

## 3. Session

### 3.1 The cookie

| Property | Value |
| --- | --- |
| Name | `itest_session` |
| Value | `<sid>.<signature>`, where `sid` is `secrets.token_urlsafe(32)` and the signature is `hmac.new(SESSION_SECRET, sid, sha256)` in base64url |
| HttpOnly | yes |
| Secure | yes, always in production. Set from a single `COOKIE_SECURE` flag that is false only when the bind host is loopback, so local development over `http://localhost:8321` still works |
| SameSite | `Lax` |
| Path | `/` |
| Max-Age | 2592000 (30 days) |

Lax rather than Strict because the OAuth callback and any link into the app
from outside are top-level navigations, and Strict would drop the cookie on
the first one and present a signed-in user with a signed-out page. Lax already
withholds the cookie from cross-site POST, which is the case CSRF cares about.

### 3.2 The server side

Sessions live in a small SQLite database on the Railway volume, not in the
DuckDB warehouse. The warehouse is append-only analytics behind a single
writer, and a session write on every sign-in and every rotation would contend
for that write lock against the pipelines. Use `sqlite3` from the standard
library with WAL enabled. Path from `AUTH_DB` with a default of
`data/auth/auth.sqlite3`, beside the warehouse but not inside it.

```sql
CREATE TABLE users (
  sub          TEXT PRIMARY KEY,
  email        TEXT NOT NULL,
  is_operator  INTEGER NOT NULL DEFAULT 0,
  entry_id     INTEGER,                 -- their FPL entry, workstream D
  created_utc  TEXT NOT NULL,
  last_seen_utc TEXT NOT NULL
);

CREATE TABLE sessions (
  sid_hash     TEXT PRIMARY KEY,        -- sha256 of the sid, never the sid
  sub          TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
  csrf_hash    TEXT NOT NULL,           -- sha256 of the csrf token
  created_utc  TEXT NOT NULL,
  expires_utc  TEXT NOT NULL,
  user_agent   TEXT                     -- for the sessions list on Account
);
```

The row holds `sha256(sid)`, not `sid`. A copy of this database is then not a
bag of live session cookies.

`is_operator` is set at upsert time by comparing the verified email to
`OPERATOR_EMAIL`, case-folded. It is a stored column rather than a runtime
comparison so that changing `OPERATOR_EMAIL` later does not silently demote a
running session, and so the matrix can be enforced with one integer read.

### 3.3 Lifetime and renewal

Absolute lifetime 30 days from creation. A request that presents a session
older than 24 hours gets the same `sid` with a new `expires_utc` and a
re-issued cookie, so an active user is never signed out mid-gameweek. There is
no sliding extension past the absolute 30 days: after 30 days the user signs
in again, which costs one click because Google will not re-prompt for consent.

An expired row is treated as absent. A daily delete of rows past
`expires_utc` runs as part of whatever housekeeping task E2 picks; it is not
correctness, only hygiene.

### 3.4 Invalidation

| Event | Effect |
| --- | --- |
| `POST /auth/logout` | The session row is deleted and `itest_session`, `itest_csrf` and `itest_oauth` are cleared with Max-Age=0. Other sessions for the same user are untouched, because signing out of a phone should not sign out the laptop. |
| Sign out everywhere | `DELETE FROM sessions WHERE sub = ?`. Offered on the Account tab. |
| `SESSION_SECRET` rotated | Every existing cookie fails its signature check and every user signs in again. This is the intended blast radius and the reason the secret is rotated only in response to a suspected leak. |
| `USER_KEY_ENC_SECRET` rotated | Sessions are unaffected. Stored keys become undecryptable. See section 5.4. |
| A stored key set, rotated or removed | Sessions are unaffected. The key is not a session credential and treating it as one would sign a user out for pasting a new key. |

---

## 4. CSRF

### 4.1 The mechanism

Double-submit token, checked in one middleware, ahead of every route.

At session creation the server mints `csrf = secrets.token_urlsafe(32)`,
stores `sha256(csrf)` on the session row, and sets a second cookie
`itest_csrf` with the raw value: Secure, SameSite=Lax, Path=/, same Max-Age as
the session, and **not** HttpOnly, because the page has to read it.

Every request whose method is POST, PUT, PATCH or DELETE must carry the same
value in an `X-CSRF-Token` header. The middleware compares the header to
`sha256` of the stored hash with `hmac.compare_digest`. A mismatch or an
absent header is 403 with detail `csrf token missing or wrong`.

Two precise rules that keep this from breaking things:

- **The check applies only when a session cookie is present.** An anonymous
  request has no cookie to ride on, so there is nothing to forge. This is what
  lets the public panels stay callable by POST without a token.
- **The check applies to every state-changing method without an allowlist of
  exceptions**, including POSTs that only read, such as
  `POST /api/scripts/{name}/run` and `POST /api/query`. "This POST is
  read-only" is a fact about today's handlers, not something a middleware can
  verify, and the exception list would rot.

`GET /auth/google/callback` changes state and carries no CSRF header. It is
protected by the `state` parameter instead, which is what `state` is for.

SSE is unaffected. `GET /api/conversations/{id}/stream` is a GET, the browser
`EventSource` API cannot set headers, and no token is required on it.

### 4.2 The state-changing routes, from `fpl_edge/platform/app.py`

Twenty-one exist today and three are added, so twenty-four routes carry the
check.

| # | Method | Route | Source |
| --- | --- | --- | --- |
| 1 | POST | `/api/scripts/{name}/run` | app.py:260 |
| 2 | POST | `/api/query` | app.py:283 |
| 3 | POST | `/api/inbox/{delivery_id}/ack` | app.py:312 |
| 4 | POST | `/api/monitors/{name}/run` | app.py:323 |
| 5 | POST | `/api/solve` | app.py:341 |
| 6 | POST | `/api/players/{code}/fetch_profile` | app.py:394 |
| 7 | POST | `/api/pipelines/{task_id}/run` | app.py:495 |
| 8 | POST | `/api/content/sources/{source_key}/fetch` | app.py:704 |
| 9 | POST | `/api/ingest/link` | app.py:839 |
| 10 | POST | `/api/ingest/link/{job_id}/accept` | app.py:865 |
| 11 | POST | `/api/ingest/link/{job_id}/decline` | app.py:878 |
| 12 | DELETE | `/api/ingest/link/{job_id}` | app.py:890 |
| 13 | POST | `/api/content/items/{item_id}/discard` | app.py:904 |
| 14 | POST | `/api/content/items/{item_id}/restore` | app.py:920 |
| 15 | POST | `/api/content/items/{item_id}/gameweek` | app.py:928 |
| 16 | POST | `/api/conversations` | app.py:951 |
| 17 | DELETE | `/api/conversations/{conv_id}` | app.py:960 |
| 18 | POST | `/api/conversations/{conv_id}/chat` | app.py:980 |
| 19 | POST | `/api/conversations/{conv_id}/stop` | app.py:1020 |
| 20 | POST | `/api/account/connect` | routes_account.py:77 |
| 21 | POST | `/api/account/verify` | routes_account.py:94 |
| 22 | POST | `/auth/logout` | new |
| 23 | PUT | `/api/account/key` | new |
| 24 | DELETE | `/api/account/key` | new |

### 4.3 Client changes E2 must make

Two client seams send these requests and both need the header.

- `web/dist/js/app.js`, functions `runPanel()` and `postJSON()`. Read
  `document.cookie` for `itest_csrf` and add `X-CSRF-Token`. The cookie itself
  needs no `credentials` option, because the SPA is same-origin and `fetch`
  already sends same-origin cookies by default.
- `web/chat-app/src/api.js`, which holds every chat call in one file
  (`sendTurn`, `newConversation`, `deleteConversation`, `stopTurn`). Change it
  there and rebuild with vite. The served artefact is
  `web/dist/chat-app/assets/index.js` and it is a build output, so editing the
  bundle by hand would be undone by the next build.

---

## 5. Per-user Anthropic API key

### 5.1 Storage

One row per user, in the same SQLite database as the sessions.

```sql
CREATE TABLE user_keys (
  sub          TEXT PRIMARY KEY REFERENCES users(sub) ON DELETE CASCADE,
  ciphertext   BLOB NOT NULL,
  nonce        BLOB NOT NULL,           -- 12 bytes, fresh per encryption
  last4        TEXT NOT NULL,           -- the only plaintext fragment kept
  key_version  INTEGER NOT NULL,        -- which USER_KEY_ENC_SECRET encrypted it
  created_utc  TEXT NOT NULL,
  rotated_utc  TEXT
);
```

Encryption is AES-256-GCM from `cryptography.hazmat.primitives.ciphers.aead.AESGCM`, with the 32-byte key decoded from
`USER_KEY_ENC_SECRET`. The associated data is the user's `sub`, so a
ciphertext moved to another user's row fails to decrypt rather than silently
authorising the wrong account. A fresh 12-byte nonce per encryption, from
`os.urandom`.

`USER_KEY_ENC_SECRET` lives in Railway's secret store. It is never in the
repository, never in `.env` as committed, never in a `Dockerfile`, never in a
log line, and never in the healthcheck payload. `.gitignore` already covers
`.env`; this document adds no new file for it to cover because the secret has
no file.

### 5.2 The routes

| Method | Route | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/account/key` | none | `{"set": true, "last4": "AbC3", "created_utc": "...", "rotated_utc": null}` or `{"set": false, "last4": null}` |
| PUT | `/api/account/key` | `{"key": "<pasted>"}` | the same shape as GET, after the write |
| DELETE | `/api/account/key` | none | `{"set": false, "last4": null}` |

Validation on PUT, before anything is stored:

1. Strip whitespace. Reject empty with 400 and `paste a key, the field was empty`.
2. Reject anything that does not match `^sk-ant-[A-Za-z0-9_-]{20,}$` with 400
   and `that does not look like an Anthropic API key. It starts with sk-ant-`.
   Format only. This is a typo check, not a validity check.
3. Optionally verify it once against Anthropic with a one-token call, and
   store it only on success. Recommended, because the alternative is a user
   who thinks they are configured and discovers otherwise at the deadline. The
   verification's failure text is shown verbatim and the key is not stored.
4. Encrypt, write the row, keep `key[-4:]` as `last4`.

Rotation is a PUT over an existing row: the old ciphertext is overwritten in
place and `rotated_utc` is set. There is no history table, because a history
table of API keys is a second place to leak from. A turn already in flight
finishes on the key it started with; no new turn sees the old key.

Revocation is DELETE, which removes the row. It does not sign the user out and
does not touch their conversations. Revoking at Anthropic is a separate act
that the Account tab should name, because deleting the row here does nothing
about a key that has already leaked.

### 5.3 The never-in-a-log rule

A stored key, decrypted or encrypted, appears in no log line, no exception
message, no response body, no traceback, no request echo, and no
`repr()`.

Mechanically:

- The dataclass that carries the key defines `__repr__` returning
  `UserKey(last4=...)`, and `__str__` the same. A f-string of it in a log line
  is then safe by construction rather than by review.
- The key is never a dict value in anything passed to `logging`, and never a
  field on a pydantic model that a FastAPI handler returns.
- `PUT /api/account/key` takes its body as a plain dict, exactly as
  `routes_account.py:78` already does for the FPL cookie, and for exactly the
  same reason recorded there: a pydantic validation error would echo the body
  back in the 422 detail.
- The chat and briefing call sites pass the key into
  `ClaudeAgentOptions.env` or into the Anthropic client constructor, never
  into `os.environ`, never into argv, and never into a prompt.
- The SDK's `stderr` callback in `chat_agent.py` streams the CLI's stderr into
  the conversation transcript. That stream is filtered for the sentinel before
  it is written, because a CLI that echoes its own environment on a crash
  would otherwise put the key in a stored transcript.

### 5.4 Encryption key rotation

Rotating `USER_KEY_ENC_SECRET` makes every stored ciphertext undecryptable.
`key_version` makes that detectable rather than mysterious. The server holds
the current version and, during a rotation window, the previous one under
`USER_KEY_ENC_SECRET_PREV`; a row at the old version is decrypted with the old
secret and re-encrypted at the new one on next use. When
`USER_KEY_ENC_SECRET_PREV` is absent and a row is at an older version, the
route returns 409 with `your stored key could not be read after a server key
rotation. Paste it again on the Account tab.` It does not return 500 and it
does not return an empty key.

### 5.5 The test that proves it

`tests/platform/test_key_never_leaks.py`, one test, no mocking of the thing
under test:

```
SENTINEL = "sk-ant-api03-ZZZTESTSENTINELZZZ0000000000"
```

1. Sign in a fake user, PUT the sentinel, assert 200 and that the response
   body contains `last4` and does not contain the sentinel.
2. Drive every route in the section 6 matrix with `caplog.set_level(DEBUG)`
   over the root logger, both a success and a forced failure per route where a
   failure is reachable.
3. Assert for each response: `SENTINEL not in response.text`, and
   `SENTINEL not in json.dumps(dict(response.headers))`.
4. Assert over `caplog.records`: no record's `getMessage()`, no record's
   `args`, and no formatted record contains the sentinel.
5. Assert `SENTINEL not in repr(user_key_object)` and
   `SENTINEL not in str(user_key_object)`.
6. Read the SQLite file as bytes and assert the sentinel is absent from it,
   which catches an encryption path accidentally storing plaintext.

---

## 6. The access matrix

Three tiers. **Anonymous** means no session is required. **Signed-in** means
any authenticated user, acting on their own data. **Operator** means
`users.is_operator = 1`, one account. **Key** means a decryptable
`user_keys` row for the caller.

Enforcement is a FastAPI dependency per tier: `allow_anonymous`,
`require_session`, `require_operator`, `require_key`. A route with no
dependency is a bug, and E2 adds a test that asserts every route on the app
carries one of the four.

Failure codes, uniformly: 401 when no session and one is required, 403 when a
session exists but the tier is not met, 403 with the remediation text of
section 7 when the key is the thing missing.

### 6.1 Routes

| # | Method | Route | Anonymous | Signed-in | Operator | Key | Note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | GET | `/api/health` | yes | | | | Anonymous body is `{ok, now}` only. `warehouse` path and `repo_sha` are operator fields. |
| 2 | GET | `/api/deadline` | yes | | | | Public fixture data |
| 3 | GET | `/api/panels` | yes | | | | Catalogue. Anonymous sees only the 13 public scripts of 6.2 |
| 4 | POST | `/api/scripts/{name}/run` | per script | per script | per script | | Tier is the script's own, from 6.2 |
| 5 | POST | `/api/query` | | | yes | | Arbitrary guarded SQL over the whole warehouse |
| 6 | GET | `/api/inbox` | | | yes | | Operator deliveries |
| 7 | POST | `/api/inbox/{delivery_id}/ack` | | | yes | | |
| 8 | GET | `/api/monitors` | | | yes | | |
| 9 | POST | `/api/monitors/{name}/run` | | | yes | | Still 501, and the tier still applies |
| 10 | POST | `/api/solve` | | yes | | | Runs on the caller's own squad, workstream D |
| 11 | GET | `/api/solve/status` | | yes | | | Caller's own run |
| 12 | GET | `/api/solve/plan` | | yes | | | Caller's own artefact |
| 13 | GET | `/api/solve/transfer-plan` | | yes | | | Caller's own artefact |
| 14 | POST | `/api/players/{code}/fetch_profile` | | yes | | | Spends network and writes a shared cache. Rate-limited per user |
| 15 | GET | `/api/players/{code}/fetch_profile` | | yes | | | |
| 16 | POST | `/api/pipelines/{task_id}/run` | | | yes | | Takes the warehouse write lock |
| 17 | GET | `/api/pipelines/{task_id}/run_state` | | | yes | | |
| 18 | GET | `/api/content/sources` | | | yes | | Corpus operations |
| 19 | POST | `/api/content/sources/{source_key}/fetch` | | | yes | | |
| 20 | GET | `/api/content/sources/{source_key}/fetch_state` | | | yes | | |
| 21 | GET | `/api/briefing` | | yes | | | Reads a stored artefact. No model call, so no key |
| 22 | POST | `/api/briefing` | | yes | | yes | Authors a briefing for the caller. The only briefing route that spends tokens. Built as POST on the same path rather than the `/refresh` suffix this table first named: one resource, the read and the write |
| 23 | POST | `/api/ingest/link` | | | yes | | The one route that writes the corpus |
| 24 | GET | `/api/ingest/link/{job_id}` | | | yes | | |
| 25 | POST | `/api/ingest/link/{job_id}/accept` | | | yes | | |
| 26 | POST | `/api/ingest/link/{job_id}/decline` | | | yes | | |
| 27 | DELETE | `/api/ingest/link/{job_id}` | | | yes | | |
| 28 | POST | `/api/content/items/{item_id}/discard` | | | yes | | |
| 29 | POST | `/api/content/items/{item_id}/restore` | | | yes | | |
| 30 | POST | `/api/content/items/{item_id}/gameweek` | | | yes | | |
| 31 | POST | `/api/conversations` | | yes | | | Creates an empty conversation, no model call |
| 32 | GET | `/api/conversations` | | yes | | | Scoped to the caller's own conversations |
| 33 | DELETE | `/api/conversations/{conv_id}` | | yes | | | Own conversation only, 404 for another user's id |
| 34 | POST | `/api/conversations/{conv_id}/chat` | | yes | | yes | The chat turn. Section 7 |
| 35 | GET | `/api/conversations/{conv_id}/stream` | | yes | | | Own conversation. SSE, cookie auth, no CSRF header |
| 36 | GET | `/api/conversations/{conv_id}/events` | | yes | | | Own conversation |
| 37 | POST | `/api/conversations/{conv_id}/stop` | | yes | | | Own conversation |
| 38 | GET | `/api/chat/assets/{asset_id}.{ext}` | | yes | | | Asset must belong to a conversation the caller owns, otherwise 404 |
| 39 | GET | `/api/account/status` | | yes | | | FPL token state for the caller. Operator-only until workstream D makes the token store per-user |
| 40 | POST | `/api/account/connect` | | yes | | | Same condition |
| 41 | POST | `/api/account/verify` | | yes | | | Same condition |
| 42 | GET | `/api/account/key` | | yes | | | Returns `set` and `last4`, never the key |
| 43 | PUT | `/api/account/key` | | yes | | | |
| 44 | DELETE | `/api/account/key` | | yes | | | |
| 45 | GET | `/api/me` | yes | | | | `{signed_in: false}` when anonymous, otherwise `{signed_in, email, is_operator, key_set, last4, entry_id}` |
| 46 | GET | `/auth/google/start` | yes | | | | |
| 47 | GET | `/auth/google/callback` | yes | | | | Protected by `state`, not by a session |
| 48 | POST | `/auth/logout` | | yes | | | 204 when already signed out |
| 49 | GET | `/` and the `web/dist` static mount | yes | | | | The shell must load so it can render the sign-in button |
| 50 | GET | `/docs` | | | yes | | FastAPI default, currently public |
| 51 | GET | `/redoc` | | | yes | | FastAPI default, currently public |
| 52 | GET | `/openapi.json` | | | yes | | The schema names every operator route. Gate it with the others |

Fifty-two rows. Eight anonymous, twenty-three signed-in, twenty-one operator.
Two of the signed-in rows also require a stored key: row 22 and row 34.

`/docs`, `/redoc` and `/openapi.json` are served today because
`create_app()` passes no `docs_url=None`. Gating them is one dependency on the
app's `openapi` route or, more directly, constructing `FastAPI(docs_url=None,
redoc_url=None, openapi_url=None)` and re-adding all three behind
`require_operator`. E2 picks one; both satisfy the row.

### 6.2 Panel scripts, the tier for row 4

Eighteen scripts are registered by `fpl_edge/platform/scripts/__init__.py`.
Fifteen have a `Panel` declaration in `fpl_edge/platform/panels.py`; the other
three stay registered as sources for `dashboard_brief`.

| Script | Panel id | Tier | Why |
| --- | --- | --- | --- |
| `squad_overview` | `squad` | anonymous | The shared team. An anonymous caller may not pass `entry_id`; the server pins `PUBLIC_ENTRY_ID` |
| `fixture_board` | `fixtures` | anonymous | League-wide fixture data |
| `fixture_detail` | `fixture_detail` | anonymous | League-wide |
| `projection_table` | `projections` | anonymous | League-wide |
| `ownership_eo` | `ownership` | anonymous | Field data |
| `player_radar` | `player_radar` | anonymous | League-wide |
| `player_profile` | `player_profile` | anonymous | Cached Understat, league-wide |
| `player_chatter` | `player_chatter` | anonymous | Public corpus |
| `creator_report_card` | `creator_report_card` | anonymous | Public corpus |
| `creator_board` | `creator_board` | anonymous | Public corpus |
| `creator_detail` | `creator_detail` | anonymous | Public corpus |
| `market_watch` | none | anonymous | Bookmaker prices, no panel today |
| `price_radar` | none | anonymous | Price flow, no panel today |
| `dashboard_brief` | `brief` | signed-in | Aggregates the caller's solve state and watch log |
| `planner_grid` | `planner` | signed-in | The caller's own squad and the solver |
| `idea_registry` | none | operator | The operator's idea ledger |
| `pipeline_board` | `pipeline_board` | operator | Operator infrastructure |
| `pipeline_run_log` | `pipeline_run_log` | operator | Operator logs, including captured stderr |

Thirteen anonymous, two signed-in, three operator.

Two rules that make the anonymous tier safe rather than merely permissive:

1. An anonymous call to `POST /api/scripts/{name}/run` has its `params`
   reduced to the panel's `default_params` plus a whitelist per script. It may
   not pass `entry_id`, `manager_id`, or any parameter that selects a person.
   The whole point of the public view is one published team, not a facility
   for reading any team.
2. `GET /api/panels` filters its list by the caller's tier, so an anonymous
   visitor's UI does not render tabs that will 401 on click.

### 6.3 The tabs

| Tab | Route in `web/dist/index.html` | Tier |
| --- | --- | --- |
| Dashboard | `#home` | anonymous, reduced to the public panels |
| Planner | `#planner` | signed-in |
| Projections | `#xpoints` | anonymous |
| EliteFPL | `#template` | anonymous |
| Creators | `#creators` | anonymous |
| Fixtures | `#fixtures` | anonymous |
| Pipelines | `#pipelines` | operator |
| Chat | `#chat` | signed-in, and a key for sending |
| Account | `#account` | signed-in |

---

## 7. How the model call gets its key

### 7.1 The source

`UserContext` (workstream D) carries the caller through the request. It gains
one field:

```python
anthropic_key: str | None   # decrypted at the point of use, never at request start
```

Decryption happens in the handler that is about to spend tokens, not in the
middleware that builds the context. A request that never calls a model never
decrypts a key, so a key spends less time in process memory and fewer code
paths can log it.

### 7.2 `chat_agent.py`

Today `ChatAgent.build_options()` at `fpl_edge/platform/chat_agent.py:713`
returns `ClaudeAgentOptions` with `cli_path=self._claude_bin` and no
credentials, and `_run_turn_inner()` calls `self._scrub_environment()` at
line 783, which deletes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
`ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, `CLAUDE_CODE_ENTRYPOINT` and
`CLAUDE_CODE_SSE_PORT` from `os.environ`. The CLI then authenticates with the
operator's own login.

The change is one option and one parameter.

```python
def build_options(self, conv, session_id, *, anthropic_key: str,
                  stderr_cb=None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        ...,
        env={"ANTHROPIC_API_KEY": anthropic_key},
    )
```

`ClaudeAgentOptions.env` is documented in the SDK as the environment passed to
the Claude Code subprocess, and the SDK composes the child environment as
`os.environ` plus this dict. That is exactly the seam needed: the key is
per-turn and per-process, and it is never written to `os.environ`.

Writing it to `os.environ` would be wrong for a reason worth stating: the
server runs turns from different conversations concurrently in threads, and
`os.environ` is process-global, so two users' turns would race and one could
be billed to the other's key. `options.env` has no such property.

`_scrub_environment()` **stays**. With per-user keys it becomes belt and
braces rather than the whole belt, and it is what makes "the operator's
environment is never consulted" true by construction: there is nothing left in
`os.environ` for the CLI to inherit, so a bug that forgot to pass
`options.env` fails loudly with an auth error instead of silently spending the
operator's subscription.

`cli_path` keeps pointing at the `claude` binary. On Railway there is no
`~/.local/bin/claude` and no `~/.claude` credentials directory, so the binary
comes from the image and its only possible credential is the `ANTHROPIC_API_KEY` passed in `options.env`. That is the deployment property that makes
this safe rather than merely intended.

### 7.3 `briefing_intel.py`

The pass is a single one-shot synthesis: `tools=[]`, `allowed_tools=[]`, no
MCP servers, `max_turns=1`. It has two callers and one rule about whose
credential each one spends.

`_ask_model(prompt, env=...)` is the single place that builds
`ClaudeAgentOptions` and runs the query. It takes no view on whose credential
is in `env`.

`_run_model(prompt)` is the scheduled pass. It passes `env={}`, so the CLI
falls back to the login on the machine the server runs on, which is the
operator's. It runs from the `briefing_intel` task with no user and no
session, and from the operator-tier `POST /api/pipelines/{task_id}/run`.

`generate_for_user(db_path, season=..., ctx=..., credential_env=...)` is the
request path behind `POST /api/briefing`. The route reads the caller's own
credential through `UserContext.credential_env()` and hands it in. An empty
mapping is refused for anybody but the operator, which is the loud failure a
dropped credential needs.

This section previously proposed switching the pass to the Anthropic SDK
directly, on the grounds that a subprocess is a second place a key could
reach argv or stderr. That is no longer the right trade. Section 1 now allows
two kinds of credential, and a token from `claude setup-token` spends a
Claude subscription that `anthropic.Anthropic(api_key=...)` cannot present.
Keeping `claude-agent-sdk` is what makes the subscription kind work at all,
and `ClaudeAgentOptions.env` is the same per-subprocess seam the chat already
uses, with the same never-in-argv and never-in-a-log guarantees.

The module imports nothing from `fpl_edge/platform/auth/`, so it cannot look
a credential up: one either arrives as an argument or the call runs on the
machine's own login. `tests/unit/test_user_keys.py` enforces that.

`_scrub_environment()` stays, and now also clears `CLAUDE_CODE_OAUTH_TOKEN`.
With nothing left in `os.environ` for the CLI to inherit, a run whose `env`
failed to arrive fails with an auth error rather than spending whatever login
the server process was carrying.

### 7.4 When no key is set

`require_key` returns 403, never 401, because the caller is authenticated and
the thing missing is a resource, not an identity. The body:

```json
{
  "error": "no_api_key",
  "detail": "No Anthropic API key is stored for this account. Open the Account tab, paste a key from console.anthropic.com, and send the message again.",
  "remediation_url": "/#account"
}
```

That text is the one the UI prints, verbatim, in both the chat composer and
the briefing panel. It contains no em dash, no rhetorical construction, and
names the exact action.

The rule that matters more than the status code: **there is no fallback**.
Not to `ANTHROPIC_API_KEY` from the server environment, not to the operator's
CLI login, not to a shared key, not to a degraded model. A missing key is a
403 and the turn never starts. A fallback here would mean the operator paying
for a stranger's chat, silently, and discovering it on an invoice.

### 7.5 `analyze.py` and the batch path

`fpl_edge/ingest/content/analyze.py` is not a request path. `analyze_transcript()` runs from the `content_analyse` and `content_analyse_backlog`
tasks in `fpl_edge/pipelines/registry.py`, on a schedule, with no user and no
session. It has two backends today: `claude -p` through `_analyze_via_cli()`
at line 449, and an `anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))`
fallback at line 522.

The rule, plainly: **the batch analysis runs under the operator's credential
only, is never per-user, and never reads the `user_keys` table.**

Two consequences E2 enforces:

1. `fpl_edge/ingest/content/analyze.py` imports nothing from the key store
   module. A test asserts this by walking the module's imports, so a future
   edit that reaches for a user's key fails the suite rather than the review.
2. The content pipeline stays on the owner's machine, under the CLI login, and
   the Railway web service has no `ANTHROPIC_*` in its environment at all.
   This keeps brief rule 6 literally true for the server. If the owner later
   moves the content job to Railway, it goes as a separate worker service with
   its own operator key in its own environment, and the web service's
   environment is still empty of `ANTHROPIC_*`. A worker and a web process
   sharing an environment would undo the guarantee that section 10's test
   proves.

The line 522 fallback is therefore correct as written and stays. It reads the
operator's key from the operator's environment, in a process that has no
request, no session and no user.

---

## 8. Deleting the loopback guard

`fpl_edge/platform/routes_account.py` currently guards all three routes with
`_require_loopback()` at line 39, comparing `request.client.host` against
`LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}`.

It is **replaced by `require_session`, and deleted.** `_require_loopback`,
`LOOPBACK_HOSTS`, and the three `_require_loopback(request)` calls all go. The
docstring's "Loopback only" bullet is rewritten to describe the session check.
The "No token leaves" bullet stays exactly as it is, because it is still true
and still load-bearing.

Coexistence is the wrong answer for a specific reason, not a stylistic one. On
Railway the request reaches uvicorn from the platform's proxy, so
`request.client.host` is the proxy's address rather than the visitor's. Under
some proxy configurations that address is loopback. A guard that is
accidentally always-true is worse than no guard: it reads as protection in the
source, passes its own unit test locally, and authorises the entire internet
in production. Keeping both guards also means the local path and the deployed
path take different branches, so the deployed behaviour is never the one that
was tested.

The test at whatever path currently asserts the 403 for a non-loopback client
is rewritten to assert 401 for no session and 200 for a session, not deleted.
The behaviour it pins, that a stranger cannot reach these routes, is the
behaviour that must survive.

One detail for E2: if uvicorn runs behind Railway's proxy and any code later
needs the real client IP, that comes from `X-Forwarded-For` with
`--proxy-headers` and a configured trusted-host list. Nothing in this spec
depends on the client IP, and adding a dependency on it would reintroduce the
problem just described.

---

## 9. What the owner must do, and no agent can

These are the owner's own steps. Rule 7 of the shared brief: an agent builds
the surface that receives a credential and never enters one.

1. Create the Google Cloud project and the OAuth client, following section
   2.6. An agent cannot sign in to Google Cloud Console as the owner.
2. Register both redirect URIs, the Railway one and
   `http://localhost:8321/auth/google/callback`, exactly as written.
3. Copy `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` into Railway's variables
   and into the local `.env`. An agent never reads or writes either value.
4. Generate and set `SESSION_SECRET` and `USER_KEY_ENC_SECRET` with the two
   one-liners in section 2.5, into Railway's secret store. Generate them on
   your own machine; do not let an agent generate a secret it has seen.
5. Set `OPERATOR_EMAIL` to your own Google address and `PUBLIC_ENTRY_ID` to
   `4490171`.
6. Deploy, then open the app and sign in once. That first sign-in is what
   writes the `users` row with `is_operator = 1`. Until it happens, every
   operator route on the matrix answers 403 for everyone, which is the correct
   state for a server nobody has claimed.
7. On the Account tab, paste your own Anthropic API key from
   `console.anthropic.com`. Chat and briefing answer 403 until you do.
8. If the app is to be used by anyone other than you, publish the OAuth
   consent screen in the Console, or add each person as a test user. Google
   blocks sign-in for anyone else while the app is in Testing.

---

## 10. Acceptance, restated as tests

Each of these is one test, and each fails today.

### 10.1 Unauthenticated chat is 401

`tests/platform/test_auth_matrix.py::test_chat_requires_session`

```
client = TestClient(create_app(db=tmp_warehouse))
r = client.post("/api/conversations/abc/chat", json={"text": "hi"})
assert r.status_code == 401
```

No cookie, no header. The assertion is on the status code and on the absence
of any conversation record: a 401 that still created a conversation directory
would be a leak of another kind. Extended, in the same file, over every route
in the 6.1 matrix that is not anonymous, table-driven from a single tuple that
E2 also uses to wire the dependencies. One source for the matrix, one source
for the test.

### 10.2 Signed in, no key, 403 with remediation, and the operator's environment is never consulted

`tests/platform/test_auth_matrix.py::test_chat_without_key_never_reads_operator_env`

The proof of "never consulted" is a guard that fails on read, not an assertion
on the output. Reading is what must not happen, so reading is what the test
makes fatal.

```python
POISON = "sk-ant-OPERATOR-MUST-NEVER-BE-READ"

class TrapEnv(dict):
    def __getitem__(self, k):
        if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_")):
            raise AssertionError(f"operator environment consulted: {k}")
        return super().__getitem__(k)
    def get(self, k, default=None):
        if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_")):
            raise AssertionError(f"operator environment consulted: {k}")
        return super().get(k, default)
```

The test:

1. Sets `os.environ["ANTHROPIC_API_KEY"] = POISON` so the operator credential
   exists and a fallback would find it.
2. Monkeypatches `os.environ` to a `TrapEnv` for the duration of the request,
   and monkeypatches `fpl_edge.config.secret` to raise on any name starting
   `ANTHROPIC_`.
3. Monkeypatches `subprocess.run`, `subprocess.Popen`, and the SDK's transport
   factory to raise `AssertionError("a model process was spawned")`. A 403
   that first spawned a CLI would pass a status-code assertion and fail this
   one.
4. Signs in a user with no `user_keys` row and posts a chat turn.
5. Asserts `r.status_code == 403`, `r.json()["error"] == "no_api_key"`, and
   that the detail string equals the section 7.4 text exactly, so a reworded
   remediation is a failing test rather than a silent UI regression.
6. Asserts `POISON not in r.text`.

`_scrub_environment()` deletes `ANTHROPIC_*` from `os.environ` before a turn
runs, and `dict.pop` on `TrapEnv` is not trapped, which is deliberate:
deleting the operator's credential is the behaviour the design wants, and only
reading it is the failure.

### 10.3 A stored key is absent from every log and every response

`tests/platform/test_key_never_leaks.py`, specified in full at section 5.5.
The six assertions there are the acceptance: response bodies, response
headers, log records, `repr`, `str`, and the raw bytes of the SQLite file.

### 10.4 The matrix is exhaustive

`tests/platform/test_auth_matrix.py::test_every_route_has_a_tier`

Walk `app.routes`, skip the static mount, and assert that every remaining
route's dependency list contains exactly one of the four tier dependencies. A
route added later with no tier fails this test on the commit that adds it,
which is the only way a matrix this long stays true.

---

## 11. Libraries

**No OAuth library and no session library.** The standard library plus what
this repo already depends on is enough, and each of the three candidates costs
more than it returns here.

| Need | Recommendation | Why |
| --- | --- | --- |
| OAuth 2.0 client | `httpx`, already a direct dependency | The flow is one redirect and one POST. Authlib would add a dependency and a framework integration to save roughly forty lines, and its Starlette integration wants its own session backing store, which would then be the second session mechanism in the app. |
| PKCE, state, nonce | `secrets`, `hashlib`, `base64` from the standard library | Four values and one sha256. |
| id_token verification | `pyjwt[crypto]`, declared as a direct dependency | It already resolves in `uv.lock` as a transitive of `mcp`, which pins `pyjwt[crypto]`, so declaring it direct changes the lock's shape not at all. `PyJWKClient` handles Google's key rotation, which is the part worth not writing by hand. |
| Session cookie | `hmac` and `secrets`, plus `sqlite3` for the server-side row | Starlette's `SessionMiddleware` needs `itsdangerous`, which is **not** in `uv.lock` today, and it puts the whole session in the cookie, which is the wrong shape when the session must be revocable from the server and must carry a CSRF hash. |
| Key encryption at rest | `cryptography`, declared as a direct dependency | Already in `uv.lock` transitively via `pyjwt[crypto]`. `AESGCM` with associated data is thirty lines including the version handling. |
| Auth storage | `sqlite3` from the standard library | Keeps transactional per-request writes out of the single-writer DuckDB warehouse. |

Net change to `pyproject.toml`: two names that already resolve, nothing new to
download.

```toml
  "pyjwt[crypto]>=2.9",
  "cryptography>=43",
```

---

## 12. What this document could not determine

- **`UserContext` does not exist yet.** A grep across `fpl_edge`, `scripts`
  and `tests` finds no definition. Sections 6 and 7 assume the shape workstream
  D will produce and name the one field they need. If D lands a different
  shape, section 7.1 is the only part that moves.
- **Whether `/api/account/*` becomes per-user is D's call, not E's.** The FPL
  token store in `fpl_edge/myteam/tokens.py` writes to `.env` today, which is
  process-global and cannot hold two users' tokens. Rows 39 to 41 are marked
  signed-in on the assumption that D makes the store per-user. Until it does,
  they are operator-only, and E2 should implement them that way rather than
  granting a second user access to the operator's FPL session.
- **Railway's proxy and `request.client.host`.** Section 8 argues the loopback
  guard may be accidentally always-true behind the platform proxy. That is the
  documented behaviour of proxied deployments generally; it was not verified
  against Railway specifically, because there is no deployment to verify
  against. The argument for deletion does not depend on it: the session check
  is strictly better regardless.
- **Per-user rate limits.** Row 14 (`fetch_profile`) and row 16 (pipeline
  runs) both spend shared resources on behalf of one caller. This document
  sets their tier and says nothing about their rate, which is a separate
  decision and probably a separate workstream.
