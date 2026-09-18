/* Account: connect your FPL account from the browser.

   The owner's question, after the CLI sent them through a four-step DevTools
   procedure: "why is it so hard to auth". The honest answer is on the card in
   one line, because hiding it would make the steps look arbitrary: FPL has no
   API login, the only durable credential is a refresh token, and only a
   browser session can mint one. This view cannot remove that step. It makes
   everything after it one paste.

   DATA PATH. Nine routes, none of which ever returns a credential value:
     GET    /api/me
     GET    /api/account/status
     POST   /api/account/connect   {cookie}
     POST   /api/account/verify
     GET    /api/account/entry
     POST   /api/account/entry     {entry_id}
     GET    /api/account/key
     PUT    /api/account/key       {key}
     DELETE /api/account/key
   The connect route runs the exact code `fpl myteam auth --paste-cookie`
   runs, so success and failure read the same here and in the terminal.

   THREE CREDENTIALS, THREE DIFFERENT THINGS, and the card keeps them apart
   because conflating them is how somebody ends up pasting the wrong one.
   Google sign-in says who you are. The FPL paste lets the server read your
   squad before the deadline. The Anthropic key pays for your own chat turns
   and is billed on your own account at console.anthropic.com.

   CSRF. Every state changing request carries the `itest_csrf` cookie back in
   an X-CSRF-Token header, which the server compares against the digest on
   the session row. `sendJSON` below is this view's one sender. The rest of
   the app sends through app.js, which needs the same header.

   The team id comes first on the card because it is what every panel reads,
   and because the server checks it against the public entry endpoint before
   it saves: an id that no team has is refused with the place to find the
   right one, and an id that could not be checked is neither saved nor called
   wrong. The card prints the id the server says it is using, never the one
   typed into the box.

   The textarea is cleared after every submit, success or not: the paste is a
   credential and the page has no business holding it once the server has
   answered. */

import { getJSON, el, errBox, provenance, agePhrase,
         absInstant } from "/js/app.js";

const STEPS = [
  "Log in at fantasy.premierleague.com in this browser.",
  "Open DevTools (F12 or Cmd+Option+I), then Application, Cookies, "
  + "https://fantasy.premierleague.com.",
  "Copy the access_token and refresh_token values. The whole Cookie header "
  + "from any /api/ request in the Network tab works too; only refresh_token "
  + "is required.",
  "Paste below and press Verify and save.",
];

/* The server's `error_class` values are internal enums. Nothing on the page
   prints one: every branch reads this table, so the status line and the
   result box say the same thing in the same words. */
const FAILURE = {
  empty_paste: "nothing was pasted",
  malformed_paste: "the paste could not be read",
  refresh_refused: "the issuer refused the refresh token",
  session_rejected: "FPL rejected the session",
  not_configured: "no token is stored",
  network_error: "FPL could not be reached",
};
const FAILURE_TITLE = {
  empty_paste: "Nothing was pasted",
  malformed_paste: "The paste could not be read",
  refresh_refused: "The issuer refused the refresh token",
  session_rejected: "FPL rejected the session",
  not_configured: "Not connected",
  network_error: "Could not reach FPL",
};
/* Where the attempt stopped, in words rather than the `stage` enum. Client
   branches (nothing pasted) have no server stage and print none. */
const STAGE = {
  paste: "while reading the paste",
  store: "while saving the tokens",
  refresh: "while refreshing the token",
  fetch: "while reading your squad",
};
/* The one action that fixes a revoked or rejected grant. It used to appear
   only in the result box, after a second Verify; the status line shows it as
   soon as the last attempt failed. */
const REPASTE = "Log in again at fantasy.premierleague.com, copy a fresh "
  + "refresh_token from that session, and paste it into the box on this card. "
  + "The stored token was left as is.";

function failureText(cls) { return FAILURE[cls] || "the check failed"; }

/* ------------------------------------------------------------- team id */

/* postJSON throws `path: HTTP 404 {"detail": "..."}`. The detail is the
   server's sentence, with the remediation in it; the status line and the URL
   are not something to show a manager. Returns "" when the failure carries no
   server sentence, which is how the caller knows to use the error box. */
export function serverDetail(err) {
  const msg = String((err && err.message) || err || "");
  const at = msg.indexOf("{");
  if (at < 0) return "";
  try {
    const body = JSON.parse(msg.slice(at));
    return typeof body.detail === "string" ? body.detail : "";
  } catch {
    return "";
  }
}

/* What GET /api/account/entry says, rendered. `saved` is the difference
   between a team the manager chose and the server's own default, and the line
   says which one it is, in the server's words. */
export function renderEntry(host, info) {
  host.textContent = "";
  const box = el("div", "acct-entry-state");
  const head = el("div", "acct-line");
  head.append(el("span", "acct-k", "reading team "),
              el("b", null, String(info.entry_id ?? "unknown")));
  if (info.team_name) head.append(` (${info.team_name})`);
  box.appendChild(head);
  box.appendChild(el("div", "sub", info.source || ""));
  host.appendChild(box);
}

/* The outcome of a save. A refusal prints the server's own detail, which
   carries the remediation, so the page invents no wording of its own. */
export function renderEntryResult(host, out) {
  host.textContent = "";
  if (!out) return;
  if (out.ok) {
    const ok = el("div", "acct-result ok");
    ok.setAttribute("role", "status");
    const who = out.team_name
      ? `Saved team ${out.entry_id} (${out.team_name})`
      : `Saved team ${out.entry_id}`;
    ok.appendChild(el("b", null, who));
    if (out.note) ok.appendChild(el("div", "sub", out.note));
    host.appendChild(ok);
    return;
  }
  const bad = el("div", "acct-result bad");
  bad.setAttribute("role", "alert");
  bad.appendChild(el("b", null, "Team id not saved"));
  bad.appendChild(el("p", "acct-msg", out.detail || "The save did not complete."));
  host.appendChild(bad);
}

/* ------------------------------------------------------------ csrf sender */

/* The double-submit token. The cookie is readable by the page on purpose:
   the server holds its digest and compares what the header echoes back, so a
   cross-site POST, which cannot read this origin's cookies, has nothing to
   send. An anonymous caller has no session to ride on and needs no token. */
export function csrfToken(cookieText) {
  const raw = cookieText === undefined ? document.cookie : cookieText;
  for (const part of String(raw || "").split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === "itest_csrf") return decodeURIComponent(rest.join("="));
  }
  return "";
}

/* One sender for this view's writes. PUT and DELETE have no helper in app.js
   and every method here needs the header, so the fetch is local. */
async function sendJSON(path, { method = "POST", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = csrfToken();
  if (token) headers["X-CSRF-Token"] = token;
  const init = { method, headers };
  if (body !== undefined) init.body = JSON.stringify(body);
  const r = await fetch(path, init);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status} ${await r.text()}`);
  if (r.status === 204) return {};
  return r.json();
}

/* ------------------------------------------------------------- who you are */

/* What the server says went wrong on the way back from Google. The classes
   come from fpl_edge/platform/auth/oauth.py and the sentences are the ones
   that module defines, so the page invents no wording of its own. */
const AUTH_ERROR = {
  handshake_expired: "The sign-in took longer than ten minutes, so it was "
    + "started over. Press Sign in with Google again.",
  consent_declined: "Google sign-in was cancelled, so nothing was signed in.",
  provider_error: "Google returned an error instead of a sign-in. Try again, "
    + "and if it repeats, check that the redirect URI in the Google Console "
    + "matches this address exactly.",
  state_mismatch: "The sign-in did not come back to the tab that started it. "
    + "Press Sign in with Google again in this tab.",
  token_invalid: "Google's answer did not verify, so no account was signed "
    + "in. Try again.",
  not_configured: "Google sign-in is not configured on this deployment. "
    + "DEPLOYMENT.md section 13.7 lists the owner's steps.",
};

export function authErrorFrom(search) {
  const value = new URLSearchParams(String(search || "")).get("auth_error");
  if (!value) return "";
  return AUTH_ERROR[value] || "The sign-in did not complete.";
}

/* GET /api/me, rendered. Signed out with no client configured says so rather
   than drawing a button that cannot work. */
export function renderIdentity(host, me, onSignOut) {
  host.textContent = "";
  const box = el("div", "acct-status");
  const head = el("div", "acct-status-head");
  const dot = el("span", "acct-dot " + (me.signed_in ? "on" : "off"));
  dot.setAttribute("aria-hidden", "true");
  head.appendChild(dot);

  if (me.signed_in) {
    head.appendChild(el("b", null, `Signed in as ${me.email}`));
    box.appendChild(head);
    box.appendChild(el("div", "acct-line",
      me.is_operator
        ? "This account is the operator, so the pipelines, the query path "
          + "and the inbox are reachable from here."
        : "Your plan, your chat and your team id are your own. The shared "
          + "panels read league-wide data."));
    const out = el("button", null, "Sign out");
    out.type = "button";
    out.onclick = onSignOut;
    const row = el("div", "acct-actions");
    row.appendChild(out);
    box.appendChild(row);
    host.appendChild(box);
    return;
  }

  if (me.anon_is_owner) {
    head.appendChild(el("b", null, "Running as the owner"));
    box.appendChild(head);
    box.appendChild(el("div", "acct-line",
      "This deployment answers unauthenticated requests as the operator, "
      + "which is how the server runs on the owner's own machine. Sign-in is "
      + "not needed here."));
    host.appendChild(box);
    return;
  }

  head.appendChild(el("b", null, "Not signed in"));
  box.appendChild(head);
  if (!me.sign_in_available) {
    box.appendChild(el("div", "acct-line", AUTH_ERROR.not_configured));
    host.appendChild(box);
    return;
  }
  box.appendChild(el("div", "acct-line",
    "Sign in to keep your own team id, your own plan and your own chat. "
    + "Google gives this server your address and a stable id, and nothing "
    + "else."));
  const link = el("a", "primary acct-signin", "Sign in with Google");
  link.href = "/auth/google/start?next=" + encodeURIComponent("/#account");
  const row = el("div", "acct-actions");
  row.appendChild(link);
  box.appendChild(row);
  host.appendChild(box);
}

/* ----------------------------------------------------------- anthropic key */

/* GET /api/account/key, rendered. The server returns `set` and the last four
   characters and never the key, so this is everything there is to show. */
export function renderKeyState(host, info) {
  host.textContent = "";
  const box = el("div", "acct-entry-state");
  const line = el("div", "acct-line");
  if (info.set) {
    line.append(el("span", "acct-k", "key stored, ending "),
                el("b", null, String(info.last4 || "")));
    box.appendChild(line);
    box.appendChild(el("div", "sub",
      "Saving again replaces it. Removing it here stops this server using "
      + "it and does not revoke it at Anthropic, which is a separate step on "
      + "console.anthropic.com."));
  } else {
    line.append("No key stored. Chat answers 403 until one is saved.");
    box.appendChild(line);
  }
  host.appendChild(box);
}

export function renderKeyResult(host, out) {
  host.textContent = "";
  if (!out) return;
  if (out.ok) {
    const ok = el("div", "acct-result ok");
    ok.setAttribute("role", "status");
    ok.appendChild(el("b", null, out.title || "Saved"));
    if (out.note) ok.appendChild(el("div", "sub", out.note));
    host.appendChild(ok);
    return;
  }
  const bad = el("div", "acct-result bad");
  bad.setAttribute("role", "alert");
  bad.appendChild(el("b", null, "Key not saved"));
  bad.appendChild(el("p", "acct-msg", out.detail || "The save did not complete."));
  host.appendChild(bad);
}

/* ---------------------------------------------------------------- format */

function fmtWhen(iso) {
  if (!iso) return "never";
  const abs = absInstant(iso);
  if (!abs) return String(iso);
  return abs;
}

/* The shared day vocabulary: "today", "yesterday", "4 days ago". A token
   checked eleven hours ago was checked today, and the hours are noise. */
function fmtAge(iso) {
  const phrase = agePhrase(iso);
  if (!phrase) return "at an unknown time";
  return phrase;
}

function money(tenths) {
  return tenths == null ? "?" : `${(tenths / 10).toFixed(1)}m`;
}

/* ---------------------------------------------------------------- status */

/* A stored refresh token proves nothing: the issuer can revoke the grant
   long before the token's own `exp`, and the only signal is the last live
   attempt. So the failed attempt, not the token's expiry date, decides the
   headline. Reading `last_ok.entry_name` first is what let a revoked grant
   keep reading "Connected as i-test" under a green dot. */
function isBroken(st) {
  return !!(st.refresh_stored && st.last_attempt && st.last_attempt.ok === false);
}
function headlineState(st) {
  if (!st.refresh_stored) return "absent";
  if (isBroken(st)) return "broken";
  if (st.squad_source === "private") return "connected";
  return "unverified";
}
const CARD_TITLE = {
  absent: "Connect your FPL account",
  broken: "Reconnect your FPL account",
  connected: "Your FPL account",
  unverified: "Verify your FPL account",
};

function tokenLines(st) {
  const r = st.refresh || {}, a = st.access || {};
  const lines = [];
  if (r.malformed) lines.push("refresh token stored but unreadable");
  else if (r.expired) lines.push(`refresh token expired ${fmtWhen(r.expires_at)}`);
  else lines.push(`refresh token stored, its own expiry ${fmtWhen(r.expires_at)} `
    + `(${r.days_left} days left)`);
  if (a.expires_at) lines.push(`access token ${a.expired ? "expired" : "valid until"} ${fmtWhen(a.expires_at)}`);
  return lines;
}

export function renderStatus(host, st) {
  host.textContent = "";
  const box = el("div", "acct-status");
  const state = headlineState(st);
  const dot = el("span", "acct-dot "
    + { connected: "on", unverified: "warn", broken: "off", absent: "off" }[state]);
  dot.setAttribute("aria-hidden", "true");
  const head = el("div", "acct-status-head");
  head.appendChild(dot);

  if (state === "absent") {
    head.appendChild(el("b", null, "Not connected"));
    box.appendChild(head);
    box.appendChild(el("div", "acct-line",
      "The panels read your public picks from the last deadline, so "
      + "transfers you have made since are invisible."));
  } else if (state === "broken") {
    head.appendChild(el("b", "acct-broken", "Connection broken"));
    box.appendChild(head);
    const at = st.last_attempt || {};
    box.appendChild(el("div", "acct-line acct-err",
      `The last check failed ${fmtAge(at.at)}: ${failureText(at.error_class)}. `
      + "The panels are back on your public picks from the last deadline."));
    // The server's own sentence, which says what the expiry date does not.
    if (st.summary) box.appendChild(el("div", "acct-line acct-err", st.summary));
    box.appendChild(el("div", "acct-fix", REPASTE));
    if (st.last_ok?.at) {
      const who = st.last_ok.entry_name
        ? ` as ${st.last_ok.entry_name}`
          + (st.last_ok.player_name ? ` (${st.last_ok.player_name})` : "")
        : "";
      box.appendChild(el("div", "acct-line",
        `last read your squad${who} ${fmtAge(st.last_ok.at)}`));
    }
  } else {
    const who = state === "connected" && st.last_ok?.entry_name
      ? `Connected as ${st.last_ok.entry_name}`
        + (st.last_ok.player_name ? ` (${st.last_ok.player_name})` : "")
      : (state === "connected" ? "Connected" : "Token stored, not verified");
    head.appendChild(el("b", null, who));
    box.appendChild(head);
    const lines = tokenLines(st);
    lines.push(`last verified ${st.last_ok?.at ? fmtAge(st.last_ok.at) : "never"}`);
    for (const t of lines) box.appendChild(el("div", "acct-line", t));
  }
  const src = el("div", "acct-line acct-source");
  src.append(el("span", "acct-k", "squad source "), el("b", null, st.squad_source),
             `: ${st.squad_source_reason}`);
  box.appendChild(src);
  host.appendChild(box);
}

/* ---------------------------------------------------------------- result */

export function renderOutcome(host, out) {
  host.textContent = "";
  if (out.ok) {
    const ok = el("div", "acct-result ok");
    ok.setAttribute("role", "status");
    const who = out.entry_name
      ? `Connected as ${out.entry_name}` + (out.player_name ? ` (${out.player_name})` : "")
      : `Connected (entry ${out.entry_id})`;
    ok.appendChild(el("b", null, who));
    if (out.squad) {
      const s = out.squad;
      const chips = Object.entries(s.chips || {}).map(([k, v]) => `${k}=${v}`).join(", ");
      ok.appendChild(el("div", null,
        `Live squad read: ${s.picks} picks, bank ${money(s.bank_tenths)}, `
        + `value ${money(s.value_tenths)}, free transfers ${s.free_transfers ?? "?"}`
        + (chips ? `, chips: ${chips}` : "")));
    }
    ok.appendChild(el("div", "sub",
      "The dashboard and planner read this squad on their next load. "
      + "Renewal is automatic for about six months."));
    host.appendChild(ok);
    return;
  }
  const bad = el("div", "acct-result bad");
  bad.setAttribute("role", "alert");
  bad.appendChild(el("b", null, FAILURE_TITLE[out.error_class] || "Failed"));
  // Prose in a paragraph: `pre` set every one of these sentences in mono and
  // read as a machine dump. The stage enum stays out of the headline; where
  // the server got to is a plain-English line under it.
  bad.appendChild(el("p", "acct-msg", out.message || ""));
  if (STAGE[out.stage]) bad.appendChild(el("div", "acct-line", `Stopped ${STAGE[out.stage]}.`));
  if (out.remediation) bad.appendChild(el("div", "acct-fix", out.remediation));
  if (out.error_class === "refresh_refused" || out.error_class === "session_rejected") {
    bad.appendChild(el("div", "sub", REPASTE));
  }
  host.appendChild(bad);
}

/* ---------------------------------------------------------------- view */

export default async function account(host) {
  const card = el("section", "card acct-card");
  const title = el("h2", null, CARD_TITLE.absent);
  card.appendChild(title);
  card.appendChild(el("p", "sub",
    "One paste, then the panels read your real squad, bank and chips before "
    + "the deadline instead of last week's public picks."));

  /* Who you are comes first: every other control on this card acts on the
     account named here, and a manager who reads the FPL paste box without
     knowing which account it lands in has been told the wrong thing. */
  const idHost = el("div", "acct-id-host");
  idHost.setAttribute("aria-live", "polite");
  card.appendChild(idHost);

  const statusHost = el("div", "acct-status-host");
  statusHost.setAttribute("aria-live", "polite");
  card.appendChild(statusHost);

  /* Team id first: it is the one setting that changes what every panel
     reads, and it works before any credential is pasted. */
  const entryBlock = el("div", "acct-entry");
  entryBlock.appendChild(el("h3", null, "Your team id"));
  const entryHost = el("div", "acct-entry-host");
  entryHost.setAttribute("aria-live", "polite");
  entryBlock.appendChild(entryHost);
  const entryForm = el("form", "acct-entry-form");
  entryForm.setAttribute("autocomplete", "off");
  const entryLabel = el("label", "acct-label", "Team id");
  entryLabel.htmlFor = "acct-entry-id";
  const entryInput = el("input", "acct-entry-input");
  entryInput.id = "acct-entry-id";
  entryInput.type = "text";
  entryInput.inputMode = "numeric";
  entryInput.placeholder = "1234567";
  entryInput.setAttribute("aria-describedby", "acct-entry-help");
  const entryHelp = el("div", "sub", "");
  entryHelp.id = "acct-entry-help";
  const entrySave = el("button", "primary", "Save team id");
  entrySave.type = "submit";
  entryForm.append(entryLabel, entryInput, entryHelp, entrySave);
  entryBlock.appendChild(entryForm);
  const entryResultHost = el("div", "acct-entry-result");
  entryBlock.appendChild(entryResultHost);
  card.appendChild(entryBlock);

  /* The Anthropic key. Its own block, above the FPL paste, because the two
     credentials are unrelated and the FPL one is the longer job. */
  const keyBlock = el("div", "acct-entry");
  keyBlock.appendChild(el("h3", null, "Your Anthropic API key"));
  keyBlock.appendChild(el("div", "sub",
    "Chat and any model call you trigger run on your own key and are billed "
    + "to your own Anthropic account. Create one at console.anthropic.com, "
    + "paste it here, and revoke it there whenever you want."));
  const keyHost = el("div", "acct-key-host");
  keyHost.setAttribute("aria-live", "polite");
  keyBlock.appendChild(keyHost);
  const keyForm = el("form", "acct-form");
  keyForm.setAttribute("autocomplete", "off");
  const keyLabel = el("label", "acct-label", "API key");
  keyLabel.htmlFor = "acct-key";
  const keyInput = el("input", "acct-entry-input");
  keyInput.id = "acct-key";
  keyInput.type = "password";
  keyInput.autocomplete = "off";
  keyInput.spellcheck = false;
  keyInput.placeholder = "sk-ant-...";
  keyInput.setAttribute("aria-describedby", "acct-key-help");
  const keyHelp = el("div", "sub",
    "Stored encrypted on the server and never shown again. The page clears "
    + "the box as soon as it is sent.");
  keyHelp.id = "acct-key-help";
  const keySave = el("button", "primary", "Save key");
  keySave.type = "submit";
  const keyRemove = el("button", null, "Remove key");
  keyRemove.type = "button";
  const keyRow = el("div", "acct-actions");
  keyRow.append(keySave, keyRemove);
  keyForm.append(keyLabel, keyInput, keyHelp, keyRow);
  keyBlock.appendChild(keyForm);
  const keyResultHost = el("div", "acct-key-result");
  keyBlock.appendChild(keyResultHost);
  card.appendChild(keyBlock);

  const why = el("p", "acct-why");
  card.appendChild(why);

  /* The four DevTools steps are the whole job the first time and noise once a
     token is stored, so they fold shut as soon as one is. `refresh()` sets
     `open` from the status. */
  const steps = el("details", "acct-stepfold");
  steps.appendChild(el("summary", null, "How to get the tokens, in four steps"));
  const ol = el("ol", "acct-steps");
  for (const s of STEPS) ol.appendChild(el("li", null, s));
  steps.appendChild(ol);
  card.appendChild(steps);

  const form = el("form", "acct-form");
  form.setAttribute("autocomplete", "off");
  const label = el("label", "acct-label", "Cookie header, or the two tokens");
  label.htmlFor = "acct-cookie";
  const ta = el("textarea", "acct-textarea");
  ta.id = "acct-cookie";
  ta.rows = 5;
  ta.spellcheck = false;
  ta.placeholder = "access_token=eyJ...; refresh_token=eyJ...   (or the whole Cookie header)";
  ta.setAttribute("aria-describedby", "acct-help");
  const help = el("div", "sub", "Never stored by the page: sent once to the "
    + "local server, which keeps it in .env exactly as the CLI would.");
  help.id = "acct-help";
  const row = el("div", "acct-actions");
  const save = el("button", "primary", "Verify and save");
  save.type = "submit";
  const again = el("button", null, "Verify again");
  again.type = "button";
  const copy = el("button", null, "Copy CLI command");
  copy.type = "button";
  copy.title = "For the terminal instead: copy the cookie, then run this";
  row.append(save, again, copy);
  form.append(label, ta, help, row);
  card.appendChild(form);

  const resultHost = el("div", "acct-result-host");
  card.appendChild(resultHost);

  const cliLine = el("div", "acct-cli");
  card.appendChild(cliLine);

  /* Every other view footers the route and the read time it drew from; this
     one had none, so nothing on the page said how old the token state was. */
  const provHost = el("div", "acct-prov-host");
  card.appendChild(provHost);

  host.appendChild(card);

  let cli = "pbpaste | uv run fpl myteam auth --paste-cookie";

  /* One place decides what the card says about the connection, so a status
     that arrives with a connect or verify reply changes the title and the
     fold exactly as a reload would. */
  function paint(st) {
    renderStatus(statusHost, st);
    const state = headlineState(st);
    title.textContent = CARD_TITLE[state];
    steps.open = state === "absent";
    provHost.textContent = "";
    // An ISO stamp, which is what app.js's provenance ages and titles with.
    provHost.appendChild(provenance({
      script: "/api/account/status",
      generated_at: new Date().toISOString(),
    }));
  }

  async function refreshIdentity() {
    try {
      const me = await getJSON("/api/me");
      renderIdentity(idHost, me, signOut);
      const failed = authErrorFrom(location.search);
      if (failed) {
        const bad = el("div", "acct-result bad");
        bad.setAttribute("role", "alert");
        bad.appendChild(el("b", null, "Sign-in did not complete"));
        bad.appendChild(el("p", "acct-msg", failed));
        idHost.appendChild(bad);
      }
      /* The key block is only actionable for a caller the server will store a
         key for, which is anyone it answers as a person. */
      keyBlock.hidden = !(me.signed_in || me.anon_is_owner);
      return me;
    } catch (e) {
      idHost.textContent = "";
      idHost.appendChild(errBox(e));
      return null;
    }
  }

  async function signOut() {
    try {
      await sendJSON("/auth/logout");
    } catch (e) {
      idHost.appendChild(errBox(e));
      return;
    }
    location.reload();
  }

  async function refreshKey() {
    if (keyBlock.hidden) return null;
    try {
      const info = await getJSON("/api/account/key");
      renderKeyState(keyHost, info);
      return info;
    } catch (e) {
      keyHost.textContent = "";
      keyHost.appendChild(errBox(e));
      return null;
    }
  }

  keyForm.onsubmit = async (ev) => {
    ev.preventDefault();
    const typed = keyInput.value.trim();
    keyInput.value = "";              // cleared whether or not it succeeds
    keySave.disabled = true;
    keySave.textContent = "Saving...";
    try {
      const out = await sendJSON("/api/account/key",
                                 { method: "PUT", body: { key: typed } });
      renderKeyResult(keyResultHost, {
        ok: true,
        title: `Key saved, ending ${out.last4}`,
        note: "Chat uses it from your next message.",
      });
      await refreshKey();
    } catch (e) {
      const detail = serverDetail(e);
      renderKeyResult(keyResultHost, { ok: false, detail });
      if (!detail) {
        keyResultHost.textContent = "";
        keyResultHost.appendChild(errBox(e));
      }
    } finally {
      keySave.disabled = false;
      keySave.textContent = "Save key";
      keyInput.value = "";
    }
  };

  keyRemove.onclick = async () => {
    keyRemove.disabled = true;
    try {
      await sendJSON("/api/account/key", { method: "DELETE" });
      renderKeyResult(keyResultHost, {
        ok: true,
        title: "Key removed",
        note: "Revoke it at console.anthropic.com as well if it has been "
          + "copied anywhere else.",
      });
      await refreshKey();
    } catch (e) {
      keyResultHost.textContent = "";
      keyResultHost.appendChild(errBox(e));
    } finally {
      keyRemove.disabled = false;
    }
  };

  async function refreshEntry() {
    try {
      const info = await getJSON("/api/account/entry");
      renderEntry(entryHost, info);
      entryHelp.textContent = info.where_is_my_id || "";
      if (info.entry_id != null) entryInput.value = String(info.entry_id);
      return info;
    } catch (e) {
      entryHost.textContent = "";
      entryHost.appendChild(errBox(e));
      return null;
    }
  }

  entryForm.onsubmit = async (ev) => {
    ev.preventDefault();
    const typed = entryInput.value.trim();
    entrySave.disabled = true;
    entrySave.textContent = "Checking...";
    try {
      const out = await sendJSON("/api/account/entry",
                                 { body: { entry_id: typed } });
      renderEntryResult(entryResultHost, out);
      await refreshEntry();
      await refresh();
    } catch (e) {
      /* The server's refusals carry the remediation in `detail`; anything
         else is a transport failure and gets the shared error box. */
      const detail = serverDetail(e);
      if (detail) renderEntryResult(entryResultHost, { ok: false, detail });
      else {
        entryResultHost.textContent = "";
        entryResultHost.appendChild(errBox(e));
      }
    } finally {
      entrySave.disabled = false;
      entrySave.textContent = "Save team id";
    }
  };

  async function refresh() {
    try {
      const st = await getJSON("/api/account/status");
      paint(st);
      why.textContent = st.why_hard;
      cli = st.cli || cli;
      cliLine.textContent = "";
      cliLine.append("Terminal equivalent: ", el("code", null, cli));
      return st;
    } catch (e) {
      statusHost.textContent = "";
      statusHost.appendChild(errBox(e));
      return null;
    }
  }

  function busy(on) {
    save.disabled = again.disabled = on;
    save.textContent = on ? "Verifying..." : "Verify and save";
  }

  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const cookie = ta.value.trim();
    ta.value = "";                 // cleared whether or not it succeeds
    if (!cookie) {
      // Its own class: nothing was read because nothing was sent, and no
      // server stage ran, so neither the headline nor the lines below it may
      // claim a read failed.
      renderOutcome(resultHost, {
        ok: false, error_class: "empty_paste",
        message: "Paste the Cookie header or the two tokens, then press Verify and save.",
      });
      ta.focus();
      return;
    }
    busy(true);
    try {
      const out = await sendJSON("/api/account/connect", { body: { cookie } });
      renderOutcome(resultHost, out);
      if (out.status) paint(out.status);
    } catch (e) {
      resultHost.textContent = "";
      resultHost.appendChild(errBox(e));
    } finally {
      busy(false);
      ta.value = "";
    }
  };

  again.onclick = async () => {
    busy(true);
    try {
      const out = await sendJSON("/api/account/verify", { body: {} });
      renderOutcome(resultHost, out);
      if (out.status) paint(out.status);
    } catch (e) {
      resultHost.textContent = "";
      resultHost.appendChild(errBox(e));
    } finally { busy(false); }
  };

  copy.onclick = async () => {
    const prev = copy.textContent;
    try {
      await navigator.clipboard.writeText(cli);
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Select and copy: " + cli;
    }
    setTimeout(() => { copy.textContent = prev; }, 1800);
  };

  await refreshIdentity();
  await refreshKey();
  await refreshEntry();
  await refresh();
}
