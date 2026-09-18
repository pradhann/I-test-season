/* Account: connect your FPL account from the browser.

   The owner's question, after the CLI sent them through a four-step DevTools
   procedure: "why is it so hard to auth". The honest answer is on the card in
   one line, because hiding it would make the steps look arbitrary: FPL has no
   API login, the only durable credential is a refresh token, and only a
   browser session can mint one. This view cannot remove that step. It makes
   everything after it one paste.

   DATA PATH. Three routes, all loopback-only, none of which ever returns a
   token value:
     GET  /api/account/status
     POST /api/account/connect   {cookie}
     POST /api/account/verify
   The connect route runs the exact code `fpl myteam auth --paste-cookie`
   runs, so success and failure read the same here and in the terminal.

   The textarea is cleared after every submit, success or not: the paste is a
   credential and the page has no business holding it once the server has
   answered. */

import { getJSON, postJSON, el, errBox, provenance, agePhrase,
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

  const statusHost = el("div", "acct-status-host");
  statusHost.setAttribute("aria-live", "polite");
  card.appendChild(statusHost);

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
      const out = await postJSON("/api/account/connect", { cookie });
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
      const out = await postJSON("/api/account/verify", {});
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

  await refresh();
}
