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

import { runPanel, el, emptyBox, errBox, provenance, getJSON, postJSON,
         fmtSpan, fmtAge } from "/js/app.js";

/* ------------------------------------------------------------------ utils */

const num = v => (typeof v === "number" && isFinite(v) ? v : null);

/* The one placeholder on this page: an en dash for a value that is absent.
   Not a question mark, not an em dash, not the word null. */
const NONE = "–";

function parseTs(s) {
  if (!s) return null;
  const d = new Date(String(s).replace(" ", "T").replace(/\+00:00$/, "Z"));
  return isNaN(d) ? null : d;
}
/* "12m ago" / "in 4d 16h": the shared span vocabulary from app.js with the
   direction affixed. Every timestamp on this page is relative, with the
   absolute in the title attribute of whatever renders it. */
function relTime(s) {
  const d = parseTs(s);
  if (!d) return null;
  const hours = (d.getTime() - Date.now()) / 3.6e6;
  if (hours > 0) return `in ${fmtSpan(hours)}`;
  const age = fmtAge(s);
  return age ? `${age} ago` : null;
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
  if (v == null) return NONE;
  if (v < 1000) return `${(v / 1000).toFixed(1)}s`;
  if (v < 90_000) return `${Math.round(v / 1000)}s`;
  const m = Math.floor(v / 60_000), s = Math.round((v % 60_000) / 1000);
  return s ? `${m}m ${s}s` : `${m}m`;
}
function fmtInt(v) { return v == null ? NONE : Number(v).toLocaleString(); }

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

/* Health state → its dot class and its words. Every state health.py can
   return has an entry in both; a state with no entry would print its own
   enum, which is not English. */
const STATE_DOT = {
  ok: "good", failing: "bad", refused: "hold", stale: "warn",
  running: "run", never_ran: "idle", disabled: "idle",
};
const STATE_WORD = {
  ok: "ok", failing: "failing", refused: "no fetch", stale: "stale",
  running: "running", never_ran: "never ran", disabled: "disabled",
};

/* Ledger status → its dot class and its words. The five statuses the ledger
   writes are five different things, so they get five different dots: a run
   that refused is not a run that failed, and neither is a run that skipped
   because the data was already fresh. */
const RUN_DOT = {
  ok: "good", error: "bad", refused: "hold",
  skipped_fresh: "skip", no_source: "gap",
};
const RUN_WORD = {
  ok: "ok", error: "error", refused: "refused",
  skipped_fresh: "already fresh", no_source: "no source",
};
const runWord = s => RUN_WORD[s] || (s ? String(s).replace(/_/g, " ") : NONE);

/* ------------------------------------------------------- tiny components */

/* Health, always with its reason. The dot alone is decoration; the sentence
   is the product. The reason is one line of the run's detail, so when there
   is more to read `onLog` opens the drawer at this run's log. */
function healthEl(md, onLog) {
  const box = el("div", "pipe-health");
  const line = el("div", "pipe-hline");
  const dot = STATE_DOT[md.state] || "idle";
  line.appendChild(el("span", `pipe-dot ${dot}`));
  line.appendChild(el("b", `pipe-word ${dot}`,
    STATE_WORD[md.state] || String(md.state || NONE)));
  box.appendChild(line);
  box.appendChild(el("div", "pipe-reason", md.reason || "no reason served"));
  if (onLog && md.last && md.runs.length) {
    const log = el("button", "pipe-loglink", "Read the log");
    log.title = "the drawer, opened at this run's captured log";
    log.setAttribute("aria-label", `read the log of the last ${md.id} run`);
    log.onclick = e => { e.stopPropagation(); onLog(); };
    box.appendChild(log);
  }
  return box;
}

/* Last run: relative time + outcome + rows, absolute + note in the title. */
function lastRunEl(md) {
  const d = el("div", "pipe-last");
  if (!md.last) {
    d.appendChild(el("span", "pipe-never", "never ran"));
    return d;
  }
  const l = md.last;
  const bits = [];
  const rel = relTime(l.started);
  if (rel) bits.push(rel);
  if (l.status) bits.push(runWord(l.status));
  d.appendChild(el("span", "pipe-when", bits.join(" · ") || NONE));
  /* A run that wrote nothing and left nothing unchanged touched no rows;
     "0 rows" reads as a failure on a run that succeeded. */
  const written = num(l.rows_written) || 0;
  const unchanged = num(l.rows_unchanged) || 0;
  if (written || unchanged) {
    const rows = [`${fmtInt(written)} rows`];
    if (unchanged) rows.push(`${fmtInt(unchanged)} unchanged`);
    d.appendChild(el("span", "pipe-rows", rows.join(" · ")));
  }
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
  const d = el("div", "pipe-dur");
  const lastMs = md.last ? num(md.last.duration_ms) : null;
  if (lastMs == null && md.avgMs == null) {
    d.appendChild(el("span", "pipe-mut", "–"));
    return d;
  }
  const max = Math.max(lastMs || 0, md.avgMs || 0) || 1;
  const bars = el("div", "pipe-durbars");
  const b1 = el("div", "pipe-durbar last"
    + (lastMs != null && md.avgMs != null && lastMs > md.avgMs * 1.25 ? " over" : ""));
  b1.style.width = `${Math.max(2, Math.round(64 * ((lastMs || 0) / max)))}px`;
  const b2 = el("div", "pipe-durbar avg");
  b2.style.width = `${Math.max(2, Math.round(64 * ((md.avgMs || 0) / max)))}px`;
  bars.append(b1, b2);
  d.appendChild(bars);
  d.appendChild(el("span", "pipe-durtext",
    `${fmtDur(lastMs)} vs ${fmtDur(md.avgMs)} avg`));
  d.title = `last run ${fmtDur(lastMs)}; average of recent OK runs ${fmtDur(md.avgMs)}`;
  return d;
}

/* The sparkline: last ten run durations as micro-bars, oldest → newest.
   Plain DOM, --s1 fill, error runs --bad. Recessive; the exact values ride
   in the title. */
function sparkEl(md) {
  const box = el("div", "pipe-spark");
  const runs = [...md.runs].reverse();          // payload is newest-first
  if (!runs.length) {
    box.title = "no runs recorded yet";
    return box;
  }
  /* One run has no range to be measured against, so it is drawn at a fixed
     low height: normalised against itself it would fill the cell, and a
     full-height bar means a slow run, not a first run. */
  const max = Math.max(...runs.map(r => num(r.duration_ms) || 0), 1);
  for (const r of runs) {
    const h = runs.length < 2 ? 5
      : Math.max(2, Math.round(16 * ((num(r.duration_ms) || 0) / max)));
    const b = el("span", "pipe-sbar" + (r.status === "error" ? " bad" : "")
      + (runs.length < 2 ? " lone" : ""));
    b.style.height = `${h}px`;
    box.appendChild(b);
  }
  box.title = (runs.length < 2
    ? "one run recorded; the bar has no earlier run to be measured against\n"
    : "") + runs.map(r =>
    `${r.started ? absTime(r.started) : NONE} · ${runWord(r.status)}`
    + ` · ${fmtDur(r.duration_ms)}`
  ).join("\n");
  return box;
}

/* ------------------------------------------------------------------ view */

export default async function pipelines(host) {
  /* One drawer per visit — the fixtures rule: re-entering must not stack a
     second one, and leaving must remove it and its key handler. */
  document.querySelectorAll("aside.pipe-drawer").forEach(n => n.remove());
  const drawer = el("aside", "drawer pipe-drawer");
  /* A dialog, named by the pipeline id inside it. Closed it is offscreen but
     still in the DOM, so it is inert and hidden from assistive technology
     until it opens; focus moves in on open and back to the row on close. */
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-labelledby", "pipe-drawer-title");
  drawer.setAttribute("aria-hidden", "true");
  drawer.inert = true;
  drawer.tabIndex = -1;
  document.body.appendChild(drawer);
  let opener = null;                  // the row to hand focus back to
  const closeDrawer = () => {
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    drawer.inert = true;
    if (opener && opener.isConnected) opener.focus();
    opener = null;
  };
  const onKey = e => {
    if (!drawer.isConnected) { removeEventListener("keydown", onKey); return; }
    if (e.key === "Escape" && drawer.classList.contains("open")) closeDrawer();
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

  const card = el("section", "card pipe-card");
  const head = el("div", "pipe-head");
  head.appendChild(el("h2", null, "Pipelines"));
  const refreshBtn = el("button", "pipe-refresh", "Refresh");
  refreshBtn.onclick = () => load();
  head.appendChild(refreshBtn);
  card.appendChild(head);
  card.appendChild(el("p", "sub",
    "Every registered pipeline: its health and why, when it last ran, how "
    + "long it takes against its own average, and when it is next due. "
    + "Run triggers the same runner the scheduler uses; metered pipelines "
    + "quote their cost first."));

  const summaryRow = el("div", "pipe-summary");
  const body = el("div", "pipe-body");
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
        "The board panel refused this request, so no row could be drawn. "
        + "The registry still holds every pipeline; what is missing is the "
        + "board's read of the ledger."));
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
      { k: "no fetch", v: s.n_refused, cls: "hold" },
      { k: "stale", v: s.n_stale, cls: "warn" },
      { k: "never ran", v: s.n_never_ran, cls: "idle" },
    ];
    /* failing>0 jumps first in reading order; a healthy board leads with ok. */
    if (!num(s.n_failing)) chips.push(chips.shift());
    for (const c of chips) {
      const chip = el("div", "pipe-stat " + c.cls
        + (c.cls === "bad" && num(c.v) ? " alarm" : "")
        + (!num(c.v) ? " zero" : ""));
      chip.appendChild(el("div", "v", String(c.v ?? 0)));
      chip.appendChild(el("div", "k", c.k));
      summaryRow.appendChild(chip);
    }
    if (num(s.n_running)) {
      const chip = el("div", "pipe-stat run");
      chip.appendChild(el("div", "v", String(s.n_running)));
      chip.appendChild(el("div", "k", "running"));
      summaryRow.appendChild(chip);
    }
    const credits = el("div", "pipe-stat credits"
      + (num(s.month_credits) != null && num(s.month_credits_cap)
         && s.month_credits > s.month_credits_cap * 0.8 ? " warn" : ""));
    credits.appendChild(el("div", "v",
      `${s.month_credits ?? 0}/${s.month_credits_cap ?? NONE}`));
    credits.appendChild(el("div", "k", "odds credits this month"));
    credits.title = "Sum of credits_spent across every pipeline's ledger rows "
      + "this calendar month (UTC), against the Odds API free-tier allowance.";
    summaryRow.appendChild(credits);
  }

  /* --------------------------------------------------------------- rows */

  /* The eight columns, in grid order. One list feeds the header row and the
     per-cell labels the folded layout shows, so a cell can never carry a
     name the header does not. */
  const COLUMNS = ["pipeline", "health", "schedule", "last run", "duration",
                   "recent runs", "next due", "action"];

  function headerRow() {
    const hr = el("div", "pipe-row pipe-hrow");
    hr.setAttribute("role", "presentation");
    for (const c of COLUMNS) hr.appendChild(el("div", "pipe-hcell", c));
    return hr;
  }

  function renderRows() {
    body.textContent = "";
    const families = M.res.families || [];
    for (const fam of families) {
      const rows = M.models.filter(m => m.family === fam);
      if (!rows.length) continue;
      const sec = el("div", "pipe-family");
      sec.appendChild(el("h3", "pipe-famname", fam));
      sec.appendChild(headerRow());
      for (const md of rows) sec.appendChild(rowEl(md));
      body.appendChild(sec);
    }
  }

  /* Every cell carries the column it sits in. Wide, the header row says it
     once; folded to two columns, CSS prints the label above each cell, so
     "in 3d" is never a number on its own. */
  function cell(node, column) {
    node.dataset.label = column;
    return node;
  }

  function rowEl(md) {
    const row = el("div", "pipe-row"
      + (md.state === "failing" ? " failing" : "")
      + (md.enabled ? "" : " disabled"));

    const name = el("div", "pipe-name");
    name.appendChild(el("b", null, md.id));
    const desc = el("div", "pipe-desc", md.description || "");
    /* The description is clipped to the column; the full text stays reachable
       without opening the drawer. */
    if (md.description) desc.title = md.description;
    name.appendChild(desc);
    row.appendChild(cell(name, "pipeline"));

    row.appendChild(cell(healthEl(md, () => openDrawer(md, row, true)),
                         "health"));

    const sched = el("div", "pipe-sched", md.schedule || NONE);
    sched.title = "the registry's schedule, in words";
    row.appendChild(cell(sched, "schedule"));

    row.appendChild(cell(lastRunEl(md), "last run"));
    row.appendChild(cell(durationEl(md), "duration"));
    row.appendChild(cell(sparkEl(md), "recent runs"));

    const due = el("div", "pipe-due");
    if (md.nextDue) {
      due.appendChild(el("span", null, relTime(md.nextDue) || NONE));
      due.title = `next due ${absTime(md.nextDue)}`;
    } else {
      due.appendChild(el("span", "pipe-mut", NONE));
      due.title = "no scheduled instant: on demand, or no future deadline known";
    }
    row.appendChild(cell(due, "next due"));

    row.appendChild(cell(actionEl(md, row), "action"));

    /* The row is a button: it opens the drawer on click, on Enter and on
       Space. The Run button inside it owns its own click. */
    row.onclick = () => openDrawer(md, row);
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-haspopup", "dialog");
    row.setAttribute("aria-label",
      `${md.id}: ${STATE_WORD[md.state] || md.state || "state unknown"}. `
      + `${md.reason || ""} Open its runs and logs.`);
    row.onkeydown = e => {
      if (e.key !== "Enter" && e.key !== " " && e.key !== "Spacebar") return;
      e.preventDefault();                 // Space would scroll the page
      openDrawer(md, row);
    };

    /* A pending confirm survives a board refresh: the strip re-renders from
       the map rather than living only in the previous DOM. */
    if (pendingConfirm.has(md.id)) {
      row.appendChild(confirmStrip(md, pendingConfirm.get(md.id)));
    }
    return row;
  }

  function actionEl(md, row) {
    const box = el("div", "pipe-act");
    const running = md.state === "running" || active.has(md.id);
    if (running) {
      const badge = el("span", "pipe-running", "running");
      badge.title = "a firing is claimed and not yet finished";
      box.appendChild(badge);
      return box;
    }
    if (!md.enabled) {
      const b = el("span", "pipe-mut", "disabled");
      b.title = "disabled in the registry; the trigger route refuses it too";
      box.appendChild(b);
      return box;
    }
    const btn = el("button", "pipe-run", "Run");
    btn.setAttribute("aria-label", `run ${md.id} now`);
    if (md.confirmRequired) {
      btn.title = `metered: about ${md.creditsEstimate ?? NONE} credits. The `
        + "cost is quoted before anything runs.";
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
    const strip = el("div", "pipe-confirm");
    strip.onclick = e => e.stopPropagation();
    const spend = payload.month_spend;
    const cap = payload.month_cap;
    strip.appendChild(el("b", null,
      `about ${payload.credits_estimate ?? NONE} credits`));
    strip.appendChild(el("span", null,
      ` · ${spend == null ? "spend unknown" : spend}`
      + `${cap ? `/${cap}` : ""} used this month`));
    const go = el("button", "pipe-run confirm", "Confirm run");
    go.onclick = async (e) => {
      e.stopPropagation();
      go.disabled = true;
      pendingConfirm.delete(md.id);
      await startRun(md, strip.parentElement, true);
    };
    const cancel = el("button", "pipe-cancel", "Cancel");
    cancel.onclick = (e) => {
      e.stopPropagation();
      pendingConfirm.delete(md.id);
      renderRows();
    };
    strip.append(go, cancel);
    return strip;
  }

  function showRowNote(row, text, cls) {
    row.querySelectorAll(".pipe-note").forEach(n => n.remove());
    row.appendChild(el("div", "pipe-note " + (cls || ""), text));
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
  async function openDrawer(md, fromRow, openLog) {
    drawer.textContent = "";
    drawer.inert = false;
    drawer.removeAttribute("aria-hidden");
    drawer.classList.add("open");
    drawer.scrollTop = 0;
    if (fromRow) opener = fromRow;

    const head = el("div", "pipe-dhead");
    const title = el("div");
    const name = el("div", "pipe-dname", md.id);
    name.id = "pipe-drawer-title";        // the dialog's accessible name
    title.appendChild(name);
    title.appendChild(el("div", "sub", `${md.family} · ${md.schedule}`));
    head.appendChild(title);
    const close = el("button", null, "Close");
    close.setAttribute("aria-label", `close the ${md.id} panel`);
    close.onclick = closeDrawer;
    head.appendChild(close);
    drawer.appendChild(head);
    /* Focus follows the panel: the close button is the first control, and
       closing hands focus back to the row that opened it. */
    close.focus();

    if (md.description) drawer.appendChild(el("p", "sub", md.description));
    drawer.appendChild(healthEl(md));

    drawer.appendChild(el("h2", null, "Recent runs"));
    if (!md.runs.length) {
      const gap = el("div", "pipe-gap");
      gap.appendChild(el("b", null, "No runs recorded."));
      gap.appendChild(document.createTextNode(
        " This pipeline has no fetch_run rows yet, which is what health "
        + "above says. The first scheduler tick or Run click writes one."));
      drawer.appendChild(gap);
      return;
    }

    const tbl = el("table", "data pipe-runs");
    const thead = el("thead");
    const hr = el("tr");
    for (const h of ["status", "started", "duration", "rows", "trigger"])
      hr.appendChild(el("th", null, h));
    thead.appendChild(hr);
    const tbody = el("tbody");
    tbl.append(thead, tbody);

    let first = null;
    for (const r of md.runs) {
      const tr = el("tr", "pipe-runrow"
        + (r.status === "error" ? " bad" : ""));
      const st = el("td");
      /* Five ledger statuses, five dots. no_source, refused and
         skipped_fresh are three different things and none of them is an
         error, so none of them shares a dot with one. */
      st.appendChild(el("span", "pipe-dot " + (RUN_DOT[r.status] || "idle")));
      st.appendChild(document.createTextNode(" " + runWord(r.status)));
      tr.appendChild(st);
      const when = el("td", null, relTime(r.started) || NONE);
      when.title = r.started ? absTime(r.started) : "no start stamp";
      tr.appendChild(when);
      tr.appendChild(el("td", "num", fmtDur(r.duration_ms)));
      const written = num(r.rows_written) || 0;
      const unchanged = num(r.rows_unchanged) || 0;
      const rows = [];
      if (written || unchanged) rows.push(fmtInt(written));
      if (unchanged) rows.push(`${fmtInt(unchanged)} unch.`);
      tr.appendChild(el("td", "num", rows.join(" + ") || NONE));
      tr.appendChild(el("td", null, r.trigger || NONE));
      tr.title = r.note ? String(r.note).slice(0, 400)
        : "click for this run's log";
      tr.tabIndex = 0;
      tr.setAttribute("role", "button");
      tr.setAttribute("aria-label",
        `${runWord(r.status)} run, ${relTime(r.started) || "no start stamp"}`
        + ". Show its log.");
      tr.onclick = () => toggleLog(tr, r);
      tr.onkeydown = e => {
        if (e.key !== "Enter" && e.key !== " " && e.key !== "Spacebar") return;
        e.preventDefault();
        toggleLog(tr, r);
      };
      tbody.appendChild(tr);
      if (!first) first = tr;
    }
    const wrap = el("div", "scroll-x");
    wrap.appendChild(tbl);
    drawer.appendChild(wrap);
    drawer.appendChild(el("p", "sub",
      "Click a run for the tail of its captured log."));
    /* Arrived from the health cell: the last run's log is what was asked
       for, so it opens without a second click. */
    if (openLog && first) await toggleLog(first, md.runs[0]);
  }

  /* One run's log tail, expanded inline under its row. Monospace, scrolling
     in its own container; a run without a log gets its named gap. */
  async function toggleLog(tr, run) {
    const existing = tr.nextElementSibling;
    if (existing && existing.classList.contains("pipe-logrow")) {
      existing.remove();
      return;
    }
    const logTr = el("tr", "pipe-logrow");
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
      const gap = el("div", "pipe-gap");
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
    const pre = el("pre", "pipe-log");
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
