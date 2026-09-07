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

import { getJSON, postJSON, el, errBox } from "/js/app.js";

const STEPS = [
  "Log in at fantasy.premierleague.com in this browser.",
  "Open DevTools (F12 or Cmd+Option+I), then Application, Cookies, "
  + "https://fantasy.premierleague.com.",
  "Copy the access_token and refresh_token values. The whole Cookie header "
  + "from any /api/ request in the Network tab works too; only refresh_token "
  + "is required.",
  "Paste below and press Verify and save.",
];

/* ---------------------------------------------------------------- format */

function fmtWhen(iso) {
  if (!iso) return "never";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  return d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

function fmtAge(iso) {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (!isFinite(ms)) return String(iso);
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

function money(tenths) {
  return tenths == null ? "?" : `${(tenths / 10).toFixed(1)}m`;
}

/* ---------------------------------------------------------------- status */

function renderStatus(host, st) {
  host.textContent = "";
  const box = el("div", "acct-status");
  const connected = st.refresh_stored && st.squad_source === "private";
  const dot = el("span", "acct-dot " + (connected ? "on" : st.refresh_stored ? "warn" : "off"));
  dot.setAttribute("aria-hidden", "true");
  const head = el("div", "acct-status-head");
  head.appendChild(dot);

  if (!st.refresh_stored) {
    head.appendChild(el("b", null, "Not connected"));
    box.appendChild(head);
    box.appendChild(el("div", "acct-line",
      "The panels read your public picks from the last deadline, so "
      + "transfers you have made since are invisible."));
  } else {
    const who = st.last_ok?.entry_name
      ? `Connected as ${st.last_ok.entry_name}`
        + (st.last_ok.player_name ? ` (${st.last_ok.player_name})` : "")
      : (connected ? "Connected" : "Token stored, not verified");
    head.appendChild(el("b", null, who));
    box.appendChild(head);
    const r = st.refresh || {};
    const a = st.access || {};
    const lines = [];
    if (r.malformed) lines.push("refresh token stored but unreadable");
    else lines.push(`refresh token ${r.expired ? "EXPIRED" : "valid until"} `
      + `${fmtWhen(r.expires_at)}${r.expired ? "" : ` (${r.days_left} days left)`}`);
    if (a.expires_at) lines.push(`access token ${a.expired ? "expired" : "valid until"} ${fmtWhen(a.expires_at)}`);
    lines.push(`last verified ${fmtAge(st.last_ok?.at)}`);
    if (st.last_attempt && !st.last_attempt.ok) {
      lines.push(`last attempt failed ${fmtAge(st.last_attempt.at)} `
        + `(${st.last_attempt.error_class || "error"})`);
    }
    for (const t of lines) box.appendChild(el("div", "acct-line", t));
  }
  const src = el("div", "acct-line acct-source");
  src.append(el("span", "acct-k", "squad source "), el("b", null, st.squad_source),
             `: ${st.squad_source_reason}`);
  box.appendChild(src);
  host.appendChild(box);
}

/* ---------------------------------------------------------------- result */

function renderOutcome(host, out) {
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
  const label = {
    malformed_paste: "The paste could not be read",
    refresh_refused: "The issuer refused the refresh grant",
    session_rejected: "FPL rejected the session",
    not_configured: "Not connected",
    network_error: "Could not reach FPL",
  }[out.error_class] || "Failed";
  bad.appendChild(el("b", null, `${label} (stage: ${out.stage})`));
  bad.appendChild(el("pre", "acct-msg", out.message || ""));
  if (out.remediation) bad.appendChild(el("div", "acct-fix", out.remediation));
  if (out.error_class === "refresh_refused" || out.error_class === "session_rejected") {
    bad.appendChild(el("div", "sub",
      "Log in again at fantasy.premierleague.com, copy a fresh refresh_token "
      + "from that session, and paste it above. The stored token was left as is."));
  }
  host.appendChild(bad);
}

/* ---------------------------------------------------------------- view */

export default async function account(host) {
  const card = el("section", "card acct-card");
  card.appendChild(el("h2", null, "Connect your FPL account"));
  card.appendChild(el("p", "sub",
    "One paste, then the panels read your real squad, bank and chips before "
    + "the deadline instead of last week's public picks."));

  const statusHost = el("div", "acct-status-host");
  statusHost.setAttribute("aria-live", "polite");
  card.appendChild(statusHost);

  const why = el("p", "acct-why");
  card.appendChild(why);

  const ol = el("ol", "acct-steps");
  for (const s of STEPS) ol.appendChild(el("li", null, s));
  card.appendChild(ol);

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

  host.appendChild(card);

  let cli = "pbpaste | uv run fpl myteam auth --paste-cookie";

  async function refresh() {
    try {
      const st = await getJSON("/api/account/status");
      renderStatus(statusHost, st);
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
      renderOutcome(resultHost, {
        ok: false, stage: "paste", error_class: "malformed_paste",
        message: "Nothing to save: paste the Cookie header or the two tokens first.",
      });
      ta.focus();
      return;
    }
    busy(true);
    try {
      const out = await postJSON("/api/account/connect", { cookie });
      renderOutcome(resultHost, out);
      if (out.status) renderStatus(statusHost, out.status);
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
      if (out.status) renderStatus(statusHost, out.status);
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
