/* Pipelines — the control panel.

   Pipelines are the product; this page is its face. One row per registered
   pipeline, grouped by family: health WITH ITS REASON (the reason is the
   product — a bare red dot that cannot say why is worth nothing at the
   deadline), the schedule in human words, the last run, duration against the
   running average, the last ten runs as a sparkline, next due, and a Run
   button.

   DATA PATH. Panels are the only data path: `pipeline_board` serves
   everything drawn here and `pipeline_run_log` serves one run's log tail in
   the drawer. The ONE write this page performs goes through
   POST /api/pipelines/{id}/run — the same runner seam the CLI uses, so a
   click and a cron tick leave identical ledger rows.

   THE CONFIRM RULE (PIPELINES.md §5 decision 4). A metered pipeline never
   runs from a bare click: the first POST returns {needs_confirm} with the
   credit estimate and this month's ledger spend, and the row grows an inline
   confirm strip showing exactly those numbers. Never a browser confirm() —
   a dialog that quotes no cost is not a confirmation, it is a speed bump.

   NOTHING IS FABRICATED. A pipeline that never ran says "never ran"; a run
   with no log file gets a named gap naming why logs can be absent; an empty
   ledger renders the panel's own {empty, reason}. Every timestamp is
   relative with the absolute in its title. prefers-reduced-motion gets a
   static RUNNING badge — the pulse is defined only under no-preference. */

import { runPanel, el, emptyBox, errBox, provenance, getJSON, postJSON }
  from "/js/app.js";

/* ------------------------------------------------------------------ utils */

const num = v => (typeof v === "number" && isFinite(v) ? v : null);

function parseTs(s) {
  if (!s) return null;
  const d = new Date(String(s).replace(" ", "T").replace(/\+00:00$/, "Z"));
  return isNaN(d) ? null : d;
}
/* "12m ago" / "in 3h" — every timestamp on this page is relative, with the
   absolute in the title attribute of whatever renders it. */
function relTime(s) {
  const d = parseTs(s);
  if (!d) return null;
  let ms = Date.now() - d.getTime();
  const future = ms < 0;
  ms = Math.abs(ms);
  const mins = ms / 6e4;
  let word;
  if (mins < 1) word = "moments";
  else if (mins < 90) word = `${Math.round(mins)}m`;
  else if (mins < 48 * 60) word = `${Math.round(mins / 60)}h`;
  else word = `${Math.round(mins / 60 / 24)}d`;
  return future ? `in ${word}` : `${word} ago`;
}
function absTime(s) {
  const d = parseTs(s);
  return d ? d.toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }) : "unknown instant";
}
/* Durations: sub-second to minutes, one system. */
function fmtDur(ms) {
  const v = num(ms);
  if (v == null) return "–";
  if (v < 1000) return `${(v / 1000).toFixed(1)}s`;
  if (v < 90_000) return `${Math.round(v / 1000)}s`;
  const m = Math.floor(v / 60_000), s = Math.round((v % 60_000) / 1000);
  return s ? `${m}m ${s}s` : `${m}m`;
}
function fmtInt(v) { return v == null ? "–" : Number(v).toLocaleString(); }

/* Panel call that reports failure as data — one absent script degrades one
   section, not the page. Same memo idiom as the fixtures view. */
const MISSING = new Map();
async function tryPanel(script, params = {}) {
  const gone = MISSING.get(script);
  if (gone) return { ok: false, error: gone, script, missing: true };
  try {
    const { result, provenance: prov } = await runPanel(script, params);
    return { ok: true, result, prov, script };
  } catch (e) {
    const missing = /HTTP 404|no panel script named/.test(String(e.message || e));
    if (missing) MISSING.set(script, e);
    return { ok: false, error: e, script, missing };
  }
}

/* --------------------------------------------------- payload → view model */

/* The single place this view's reads meet pipeline_board's row schema —
   pinned by the web-contract test exactly as the fixtures flatten() is.
   Everything below renders the model, never the raw row. */
function rowModel(r) {
  if (!r || typeof r !== "object") return r;
  const h = r.health || {};
  const m = r.metered || {};
  return {
    id: r.id,
    description: r.description,
    family: r.family,
    schedule: r.schedule,
    enabled: r.enabled,
    state: h.state,
    reason: h.reason,
    fails: h.consecutive_failures,
    last: r.last_run || null,
    avgMs: num(r.avg_duration_ms),
    nextDue: r.next_due,
    confirmRequired: !!m.confirm_required,
    creditsEstimate: num(m.credits_estimate),
    monthCredits: num(m.month_credits),
    runs: Array.isArray(r.runs) ? r.runs : [],
  };
}

const STATE_DOT = {
  ok: "good", failing: "bad", stale: "warn",
  running: "run", never_ran: "idle", disabled: "idle",
};
const STATE_WORD = {
  ok: "ok", failing: "failing", stale: "stale",
  running: "running", never_ran: "never ran", disabled: "disabled",
};

/* ------------------------------------------------------- tiny components */

/* Health, always with its reason. The dot alone is decoration; the sentence
   is the product. */
function healthEl(md) {
  const box = el("div", "pl-health");
  const line = el("div", "pl-hline");
  line.appendChild(el("span", `pl-dot ${STATE_DOT[md.state] || "idle"}`));
  line.appendChild(el("b", `pl-word ${STATE_DOT[md.state] || "idle"}`,
    STATE_WORD[md.state] || String(md.state || "?")));
  box.appendChild(line);
  box.appendChild(el("div", "pl-reason", md.reason || "no reason served"));
  return box;
}

/* Last run: relative time + outcome + rows, absolute + note in the title. */
function lastRunEl(md) {
  const d = el("div", "pl-last");
  if (!md.last) {
    d.appendChild(el("span", "pl-never", "never ran"));
    return d;
  }
  const l = md.last;
  const bits = [];
  const rel = relTime(l.started);
  if (rel) bits.push(rel);
  if (l.status) bits.push(l.status);
  d.appendChild(el("span", "pl-when", bits.join(" · ") || "–"));
  const rows = [];
  if (l.rows_written != null) rows.push(`${fmtInt(l.rows_written)} rows`);
  if (l.rows_unchanged) rows.push(`${fmtInt(l.rows_unchanged)} unchanged`);
  if (rows.length) d.appendChild(el("span", "pl-rows", rows.join(" · ")));
  d.title = [
    l.started ? `started ${absTime(l.started)}` : null,
    l.trigger ? `trigger: ${l.trigger}` : null,
    l.credits ? `${l.credits} credits` : null,
    l.note ? `note: ${String(l.note).slice(0, 300)}` : null,
  ].filter(Boolean).join("\n");
  return d;
}

/* Duration: the last run against the average of the last 20 OK runs, as a
   thin two-segment bar. Over-average is tinted warn — slower than usual is
   the early smell of a hung fetch. */
function durationEl(md) {
  const d = el("div", "pl-dur");
  const lastMs = md.last ? num(md.last.duration_ms) : null;
  if (lastMs == null && md.avgMs == null) {
    d.appendChild(el("span", "pl-mut", "–"));
    return d;
  }
  const max = Math.max(lastMs || 0, md.avgMs || 0) || 1;
  const bars = el("div", "pl-durbars");
  const b1 = el("div", "pl-durbar last"
    + (lastMs != null && md.avgMs != null && lastMs > md.avgMs * 1.25 ? " over" : ""));
  b1.style.width = `${Math.max(2, Math.round(64 * ((lastMs || 0) / max)))}px`;
  const b2 = el("div", "pl-durbar avg");
  b2.style.width = `${Math.max(2, Math.round(64 * ((md.avgMs || 0) / max)))}px`;
  bars.append(b1, b2);
  d.appendChild(bars);
  d.appendChild(el("span", "pl-durtext",
    `${fmtDur(lastMs)} vs ${fmtDur(md.avgMs)} avg`));
  d.title = `last run ${fmtDur(lastMs)}; average of recent OK runs ${fmtDur(md.avgMs)}`;
  return d;
}

/* The sparkline: last ten run durations as micro-bars, oldest → newest.
   Plain DOM, --s1 fill, error runs --bad. Recessive; the exact values ride
   in the title. */
function sparkEl(md) {
  const box = el("div", "pl-spark");
  const runs = [...md.runs].reverse();          // payload is newest-first
  if (!runs.length) {
    box.title = "no runs recorded yet";
    return box;
  }
  const max = Math.max(...runs.map(r => num(r.duration_ms) || 0), 1);
  for (const r of runs) {
    const h = Math.max(2, Math.round(16 * ((num(r.duration_ms) || 0) / max)));
    const b = el("span", "pl-sbar" + (r.status === "error" ? " bad" : ""));
    b.style.height = `${h}px`;
    box.appendChild(b);
  }
  box.title = runs.map(r =>
    `${r.started ? absTime(r.started) : "?"} · ${r.status} · ${fmtDur(r.duration_ms)}`
  ).join("\n");
  return box;
}

/* ------------------------------------------------------------------ view */

export default async function pipelines(host) {
  /* One drawer per visit — the fixtures rule: re-entering must not stack a
     second one, and leaving must remove it and its key handler. */
  document.querySelectorAll("aside.pl-drawer").forEach(n => n.remove());
  const drawer = el("aside", "drawer pl-drawer");
  document.body.appendChild(drawer);
  const closeDrawer = () => drawer.classList.remove("open");
  const onKey = e => {
    if (!drawer.isConnected) { removeEventListener("keydown", onKey); return; }
    if (e.key === "Escape") closeDrawer();
  };
  addEventListener("keydown", onKey);

  const timers = new Set();
  const clearTimers = () => { for (const t of timers) clearTimeout(t); timers.clear(); };
  const later = (fn, ms) => { const t = setTimeout(() => { timers.delete(t); fn(); }, ms); timers.add(t); };

  const onHash = () => {
    closeDrawer();
    if ((location.hash || "").slice(1).split("?")[0] !== "pipelines") {
      drawer.remove();
      clearTimers();
      removeEventListener("hashchange", onHash);
      removeEventListener("keydown", onKey);
    }
  };
  addEventListener("hashchange", onHash);

  const card = el("section", "card pl-card");
  const head = el("div", "pl-head");
  head.appendChild(el("h2", null, "Pipelines"));
  const refreshBtn = el("button", "pl-refresh", "Refresh");
  refreshBtn.onclick = () => load();
  head.appendChild(refreshBtn);
  card.appendChild(head);
  card.appendChild(el("p", "sub",
    "Every registered pipeline: its health and why, when it last ran, how "
    + "long it takes against its own average, and when it is next due. "
    + "Run triggers the same runner the scheduler uses; metered pipelines "
    + "quote their cost first."));

  const summaryRow = el("div", "pl-summary");
  const body = el("div", "pl-body");
  const foot = el("div");
  card.append(summaryRow, body, foot);
  host.appendChild(card);

  // ---- state ----
  let M = null;                       // {res, models: rowModel[], byId}
  const active = new Map();           // task_id -> run_id started from THIS page
  const pendingConfirm = new Map();   // task_id -> the needs_confirm payload

  /* --------------------------------------------------------- data fetch */
  async function load() {
    const r = await tryPanel("pipeline_board", {});
    if (!r.ok) {
      body.textContent = "";
      summaryRow.textContent = "";
      body.appendChild(errBox(r.error));
      body.appendChild(el("p", "sub",
        "The board panel refused this request, so there is nothing to draw. "
        + "The failure is shown rather than an empty list, because an empty "
        + "list would read as “no pipelines”."));
      return;
    }
    foot.textContent = "";
    foot.appendChild(provenance(r.prov));
    const res = r.result;
    if (res.empty) {
      summaryRow.textContent = "";
      body.textContent = "";
      body.appendChild(emptyBox(res.reason,
        "The board reads the fetch_run ledger; every pipeline execution "
        + "writes one row there. Until the first run, health would be a "
        + "guess, and this page does not guess."));
      return;
    }
    const models = (res.rows || []).map(rowModel);
    M = { res, models, byId: new Map(models.map(m => [m.id, m])) };
    renderSummary();
    renderRows();
  }

  /* ------------------------------------------------------------ summary */
  function renderSummary() {
    summaryRow.textContent = "";
    const s = M.res.summary || {};
    const chips = [
      { k: "failing", v: s.n_failing, cls: "bad" },
      { k: "ok", v: s.n_ok, cls: "good" },
      { k: "stale", v: s.n_stale, cls: "warn" },
      { k: "never ran", v: s.n_never_ran, cls: "idle" },
    ];
    /* failing>0 jumps first in reading order; a healthy board leads with ok. */
    if (!num(s.n_failing)) chips.push(chips.shift());
    for (const c of chips) {
      const chip = el("div", "pl-stat " + c.cls
        + (c.cls === "bad" && num(c.v) ? " alarm" : "")
        + (!num(c.v) ? " zero" : ""));
      chip.appendChild(el("div", "v", String(c.v ?? 0)));
      chip.appendChild(el("div", "k", c.k));
      summaryRow.appendChild(chip);
    }
    if (num(s.n_running)) {
      const chip = el("div", "pl-stat run");
      chip.appendChild(el("div", "v", String(s.n_running)));
      chip.appendChild(el("div", "k", "running"));
      summaryRow.appendChild(chip);
    }
    const credits = el("div", "pl-stat credits"
      + (num(s.month_credits) != null && num(s.month_credits_cap)
         && s.month_credits > s.month_credits_cap * 0.8 ? " warn" : ""));
    credits.appendChild(el("div", "v",
      `${s.month_credits ?? 0}/${s.month_credits_cap ?? "?"}`));
    credits.appendChild(el("div", "k", "odds credits this month"));
    credits.title = "Sum of credits_spent across every pipeline's ledger rows "
      + "this calendar month (UTC), against the Odds API free-tier allowance.";
    summaryRow.appendChild(credits);
  }

  /* --------------------------------------------------------------- rows */
  function renderRows() {
    body.textContent = "";
    const families = M.res.families || [];
    for (const fam of families) {
      const rows = M.models.filter(m => m.family === fam);
      if (!rows.length) continue;
      const sec = el("div", "pl-family");
      sec.appendChild(el("h3", "pl-famname", fam));
      for (const md of rows) sec.appendChild(rowEl(md));
      body.appendChild(sec);
    }
  }

  function rowEl(md) {
    const row = el("div", "pl-row"
      + (md.state === "failing" ? " failing" : "")
      + (md.enabled ? "" : " disabled"));

    const name = el("div", "pl-name");
    name.appendChild(el("b", null, md.id));
    name.appendChild(el("div", "pl-desc", md.description || ""));
    row.appendChild(name);

    row.appendChild(healthEl(md));

    const sched = el("div", "pl-sched", md.schedule || "–");
    sched.title = "the registry's schedule, in words";
    row.appendChild(sched);

    row.appendChild(lastRunEl(md));
    row.appendChild(durationEl(md));
    row.appendChild(sparkEl(md));

    const due = el("div", "pl-due");
    if (md.nextDue) {
      due.appendChild(el("span", null, relTime(md.nextDue) || "–"));
      due.title = `next due ${absTime(md.nextDue)}`;
    } else {
      due.appendChild(el("span", "pl-mut", "—"));
      due.title = "no scheduled instant: on demand, or no future deadline known";
    }
    row.appendChild(due);

    row.appendChild(actionEl(md, row));

    /* Row click → the drawer. The button owns its own click. */
    row.onclick = () => openDrawer(md);
    row.tabIndex = 0;
    row.onkeydown = e => { if (e.key === "Enter") openDrawer(md); };

    /* A pending confirm survives a board refresh: the strip re-renders from
       the map rather than living only in the previous DOM. */
    if (pendingConfirm.has(md.id)) {
      row.appendChild(confirmStrip(md, pendingConfirm.get(md.id)));
    }
    return row;
  }

  function actionEl(md, row) {
    const box = el("div", "pl-act");
    const running = md.state === "running" || active.has(md.id);
    if (running) {
      const badge = el("span", "pl-running", "running");
      badge.title = "a firing is claimed and not yet finished";
      box.appendChild(badge);
      return box;
    }
    if (!md.enabled) {
      const b = el("span", "pl-mut", "disabled");
      b.title = "disabled in the registry; the trigger route refuses it too";
      box.appendChild(b);
      return box;
    }
    const btn = el("button", "pl-run", "Run");
    if (md.confirmRequired) {
      btn.title = `metered: ~${md.creditsEstimate ?? "?"} credits — cost is `
        + "quoted before anything runs";
      btn.classList.add("metered");
    }
    btn.onclick = async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      await startRun(md, row, false);
      btn.disabled = false;
    };
    box.appendChild(btn);
    return box;
  }

  /* ----------------------------------------------------- the run flow */
  async function startRun(md, row, confirmed) {
    let resp;
    try {
      resp = await postJSON(`/api/pipelines/${md.id}/run`,
                            { confirm: confirmed });
    } catch (e) {
      /* 409 (already running) and everything else land here — show the
         server's own words on the row rather than a console line. */
      showRowNote(row, String(e.message || e), "bad");
      return;
    }
    if (resp.needs_confirm) {
      pendingConfirm.set(md.id, resp);
      renderRows();
      return;
    }
    pendingConfirm.delete(md.id);
    if (resp.started && resp.run_id) {
      active.set(md.id, resp.run_id);
      renderRows();
      pollRun(md.id, resp.run_id);
    }
  }

  /* The inline confirm strip — ON the row, quoting the exact numbers the
     server returned. Never a browser confirm() dialog. */
  function confirmStrip(md, payload) {
    const strip = el("div", "pl-confirm");
    strip.onclick = e => e.stopPropagation();
    const spend = payload.month_spend;
    const cap = payload.month_cap;
    strip.appendChild(el("b", null,
      `~${payload.credits_estimate ?? "?"} credits`));
    strip.appendChild(el("span", null,
      ` · ${spend == null ? "spend unknown" : spend}`
      + `${cap ? `/${cap}` : ""} used this month`));
    const go = el("button", "pl-run confirm", "Confirm run");
    go.onclick = async (e) => {
      e.stopPropagation();
      go.disabled = true;
      pendingConfirm.delete(md.id);
      await startRun(md, strip.parentElement, true);
    };
    const cancel = el("button", "pl-cancel", "Cancel");
    cancel.onclick = (e) => {
      e.stopPropagation();
      pendingConfirm.delete(md.id);
      renderRows();
    };
    strip.append(go, cancel);
    return strip;
  }

  function showRowNote(row, text, cls) {
    row.querySelectorAll(".pl-note").forEach(n => n.remove());
    row.appendChild(el("div", "pl-note " + (cls || ""), text));
  }

  /* Poll every 2s until the run is terminal, then reload the whole board —
     the ledger row, health and averages all moved. */
  function pollRun(taskId, runId) {
    const step = async () => {
      if (!drawer.isConnected && !document.body.contains(card)) return;
      let st;
      try { st = await getJSON(`/api/pipelines/${taskId}/run_state`); }
      catch { later(step, 2000); return; }
      const terminal =
        (st.run_id === runId && (st.state === "done" || st.state === "error"))
        || (st.last_run && st.last_run.run_id === runId);
      if (!terminal) { later(step, 2000); return; }
      active.delete(taskId);
      await load();
    };
    later(step, 2000);
  }

  /* -------------------------------------------------------- the drawer */
  async function openDrawer(md) {
    drawer.textContent = "";
    drawer.classList.add("open");
    drawer.scrollTop = 0;

    const head = el("div", "pl-dhead");
    const title = el("div");
    title.appendChild(el("div", "pl-dname", md.id));
    title.appendChild(el("div", "sub", `${md.family} · ${md.schedule}`));
    head.appendChild(title);
    const close = el("button", null, "Close");
    close.onclick = closeDrawer;
    head.appendChild(close);
    drawer.appendChild(head);

    if (md.description) drawer.appendChild(el("p", "sub", md.description));
    drawer.appendChild(healthEl(md));

    drawer.appendChild(el("h2", null, "Recent runs"));
    if (!md.runs.length) {
      const gap = el("div", "pl-gap");
      gap.appendChild(el("b", null, "No runs recorded."));
      gap.appendChild(document.createTextNode(
        " This pipeline has no fetch_run rows yet — health above says "
        + "the same thing. The first scheduler tick or Run click writes one."));
      drawer.appendChild(gap);
      return;
    }

    const tbl = el("table", "data pl-runs");
    const thead = el("thead");
    const hr = el("tr");
    for (const h of ["status", "started", "duration", "rows", "trigger"])
      hr.appendChild(el("th", null, h));
    thead.appendChild(hr);
    const tbody = el("tbody");
    tbl.append(thead, tbody);

    for (const r of md.runs) {
      const tr = el("tr", "pl-runrow"
        + (r.status === "error" ? " bad" : ""));
      const st = el("td");
      st.appendChild(el("span", "pl-dot "
        + (r.status === "ok" ? "good"
          : r.status === "error" ? "bad" : "warn")));
      st.appendChild(document.createTextNode(" " + r.status));
      tr.appendChild(st);
      const when = el("td", null, relTime(r.started) || "–");
      when.title = r.started ? absTime(r.started) : "no start stamp";
      tr.appendChild(when);
      tr.appendChild(el("td", "num", fmtDur(r.duration_ms)));
      const rows = [];
      if (r.rows_written != null) rows.push(fmtInt(r.rows_written));
      if (r.rows_unchanged) rows.push(`${fmtInt(r.rows_unchanged)} unch.`);
      tr.appendChild(el("td", "num", rows.join(" + ") || "–"));
      tr.appendChild(el("td", null, r.trigger || "–"));
      tr.title = r.note ? String(r.note).slice(0, 400)
        : "click for this run's log";
      tr.onclick = () => toggleLog(tr, r);
      tbody.appendChild(tr);
    }
    const wrap = el("div", "scroll-x");
    wrap.appendChild(tbl);
    drawer.appendChild(wrap);
    drawer.appendChild(el("p", "sub",
      "Click a run for the tail of its captured log."));
  }

  /* One run's log tail, expanded inline under its row. Monospace, scrolling
     in its own container; a run without a log gets its named gap. */
  async function toggleLog(tr, run) {
    const existing = tr.nextElementSibling;
    if (existing && existing.classList.contains("pl-logrow")) {
      existing.remove();
      return;
    }
    const logTr = el("tr", "pl-logrow");
    const td = el("td");
    td.colSpan = 5;
    td.appendChild(el("p", "sub", "loading log…"));
    logTr.appendChild(td);
    tr.after(logTr);

    const r = await tryPanel("pipeline_run_log", { run_id: run.run_id });
    td.textContent = "";
    if (!r.ok) { td.appendChild(errBox(r.error)); return; }
    const res = r.result;
    if (res.empty || !res.found) {
      const gap = el("div", "pl-gap");
      gap.appendChild(el("b", null, "No log for this run."));
      gap.appendChild(document.createTextNode(
        " " + (res.reason || res.empty && res.reason || "The ledger row is "
        + "the run's record; its log file is not on this machine.")));
      td.appendChild(gap);
      return;
    }
    if (res.truncated) {
      td.appendChild(el("p", "sub",
        `showing the last ${res.lines.length} of ${res.n_lines_total} lines`));
    }
    const pre = el("pre", "pl-log");
    pre.textContent = res.lines.join("\n") || "(the log file is empty)";
    td.appendChild(pre);
    pre.scrollTop = pre.scrollHeight;
  }

  /* ---------------------------------------------------------- lifecycle */
  body.appendChild(el("p", "sub", "loading…"));
  await load();

  /* The board refreshes itself: ledger rows arrive from the scheduler too,
     not only from this page's own Run clicks. Pending confirms and the
     drawer survive a refresh (the strip re-renders from its map; the drawer
     is not touched). */
  const autorefresh = async () => {
    if (!document.body.contains(card)) return;
    await load();
    later(autorefresh, 60_000);
  };
  later(autorefresh, 60_000);
}
