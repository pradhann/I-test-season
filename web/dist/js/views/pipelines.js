/* Pipelines: the control panel.

   One sortable table, one row per registered pipeline, sorted by default so
   the rows that need attention are at the top: stale first, then by what is
   due soonest. The columns are family, task, how long ago it last ran, its
   status, whether it is stale by its own window, and when it is next due.

   Everything else lives in the row's expandable: the health reason, the last
   run's note and the path to its captured log, the recent runs, and the Run
   button. The button says what a run costs before it is clicked.

   DATA PATH. Panels are the only data path: `pipeline_board` serves every
   field drawn here and `pipeline_run_log` serves one run's log tail. The ONE
   write this page performs goes through POST /api/pipelines/{id}/run, the
   same runner seam the scheduler uses, so a click and a tick leave identical
   ledger rows.

   THE CONFIRM RULE (PIPELINES.md decision 4). A metered pipeline never runs
   from a bare click: the first POST returns {needs_confirm} with the credit
   estimate and this month's ledger spend, and the row grows an inline confirm
   strip showing exactly those numbers. Never a browser dialog, which can
   quote neither.

   AGES ARE IN DAYS. "today", "yesterday", "6 days". A pipeline that ran 14
   hours ago either ran today or it did not, and "14h" makes a reader do the
   arithmetic. Next due reads the same way, "today", "tomorrow", "in 4 days":
   the deadline countdown in the topbar is the one surface in the app that
   keeps hours and minutes, because inside a day the minutes are the
   decision there and nowhere else (R23, R24).

   NOTHING IS FABRICATED. A pipeline that never ran says so; a run with no log
   file gets a named gap; an empty ledger renders the panel's own {empty,
   reason}. Every timestamp is relative with the absolute in its title. */

import { runPanel, el, emptyBox, errBox, provenance, getJSON, postJSON,
         sortableTh, rowLink, gapBox, pick, when, fmtAgeDays, agePhrase,
         absInstant } from "/js/app.js";
import { icon } from "/js/components/icons.js";

/* ------------------------------------------------------------------ utils */

function num(v) {
  if (typeof v === "number" && isFinite(v)) return v;
  return null;
}
/* A NUMBER with a stated fallback, which is this file's own because it
   rejects a non-finite value as well as a null. The three branch shapes
   beside it, `or`, `pick` and `when`, are app.js's; four views wrote them
   independently and the shared layer owns one copy. */
function numOr(v, fallback) {
  const n = num(v);
  if (n == null) return fallback;
  return n;
}

/* The one placeholder on this page: an en dash for a value that is absent.
   Not a question mark, not an em dash, not the word null. */
const NONE = "–";

function parseTs(s) {
  if (!s) return null;
  const d = new Date(String(s).replace(" ", "T").replace(/\+00:00$/, "Z"));
  if (isNaN(d)) return null;
  return d;
}
/* "today" / "in 4 days": the shared DAY vocabulary from app.js with the
   direction affixed, and no ladder of its own. A stamp in the past goes
   through agePhrase, a stamp in the future counts whole days, and the exact
   instant is always one hover away through absTime. */
function relTime(s) {
  const d = parseTs(s);
  if (!d) return null;
  const hours = (d.getTime() - Date.now()) / 3.6e6;
  if (hours <= 0) return agePhrase(s);
  if (fmtAgeDays(s) == null) return null;
  if (hours < 24) return "today";
  if (hours < 48) return "tomorrow";
  return `in ${Math.floor(hours / 24)} days`;
}
function absTime(s) {
  const abs = absInstant(s);
  if (!abs) return "unknown instant";
  return abs;
}
/* Ages, in days, as a person says them. The board serves the last run's age
   in days (last_run_age_days, computed against its own generated_at); a run
   further down the history carries only its stamp, so daysSince turns that
   into the same unit rather than into a second vocabulary. */
function daysSince(stamp) {
  const d = parseTs(stamp);
  if (!d) return null;
  return (Date.now() - d.getTime()) / 86400000;
}
function agoWords(stamp) {
  const days = daysSince(stamp);
  if (days == null) return NONE;
  return ageWords(days);
}
function ageWords(days) {
  const d = num(days);
  if (d == null) return "never ran";
  const whole = Math.floor(d);
  if (whole === 0) return "today";
  if (whole === 1) return "yesterday";
  return `${whole} days`;
}
/* Durations: sub-second to minutes, one system. */
function fmtDur(ms) {
  const v = num(ms);
  if (v == null) return NONE;
  if (v < 1000) return `${(v / 1000).toFixed(1)}s`;
  if (v < 90_000) return `${Math.round(v / 1000)}s`;
  const m = Math.floor(v / 60_000), s = Math.round((v % 60_000) / 1000);
  if (s) return `${m}m ${s}s`;
  return `${m}m`;
}
function fmtInt(v) {
  if (v == null) return NONE;
  return Number(v).toLocaleString();
}

/* Panel call that reports failure as data: one absent script degrades one
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

/* --------------------------------------------------- payload to view model */

/* The single place this view's reads meet pipeline_board's row schema,
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
    dueKind: r.due_kind,
    enabled: r.enabled,
    state: h.state,
    reason: h.reason,
    last: r.last_run || null,
    ageDays: num(r.last_run_age_days),
    lastSuccess: r.last_success,
    staleWindow: r.stale_window,
    stale: !!r.stale_by_window,
    avgMs: num(r.avg_duration_ms),
    nextDue: r.next_due,
    confirmRequired: !!m.confirm_required,
    creditsEstimate: num(m.credits_estimate),
    runs: pick(Array.isArray(r.runs), r.runs, []),
  };
}

/* Health state to its dot class and its words. Every state health.py can
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
/* Sort order for the status column: worst first, because a board is read
   for what is wrong with it. */
const STATE_RANK = {
  failing: 0, stale: 1, never_ran: 2, refused: 3, running: 4,
  ok: 5, disabled: 6,
};

/* Ledger status to its dot class and its words. The five statuses the ledger
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
function runWord(s) {
  if (RUN_WORD[s]) return RUN_WORD[s];
  if (s) return String(s).replace(/_/g, " ");
  return NONE;
}

/* What one run costs, from what the board actually serves: the registry's
   credit estimate and the mean of this pipeline's own recent successful runs.
   "Run" alone asks the operator to remember which of sixteen pipelines spends
   money and which holds the machine for twenty minutes.

   A classifier over the description was tried first and dropped: it read
   "never solves" as a solve, "velocity radar" as a network fetch and "ASR
   audio-cache sweep" as GPU time, and it missed the two tasks that fetch
   without saying so. Six of sixteen rows wrong. What kind of work a task does
   is a registry fact nobody has written down yet, so the button prints the
   two costs that ARE stored and the description, printed above it verbatim,
   says the rest. */
function costWords(md) {
  const bits = [];
  if (md.creditsEstimate) bits.push(`about ${md.creditsEstimate} credits`);
  if (md.avgMs != null) bits.push(`usually ${fmtDur(md.avgMs)}`);
  if (!bits.length) bits.push("no recorded cost yet");
  return bits.join(", ");
}

/* ------------------------------------------------------- tiny components */

/* A chip: one dot, one word, one class. The status and stale columns are
   both chips so they read as states rather than as text. */
function chip(cls, word, title) {
  const c = el("span", `pipe-chip ${cls}`);
  c.appendChild(el("span", `pipe-dot ${cls}`));
  c.appendChild(el("b", null, word));
  if (title) c.title = title;
  return c;
}

/* Health, always with its reason. The dot alone is decoration; the sentence
   is the product, so the expandable leads with it. */
function healthEl(md) {
  const box = el("div", "pipe-health");
  const dot = STATE_DOT[md.state] || "idle";
  box.appendChild(chip(dot, STATE_WORD[md.state] || String(md.state || NONE)));
  box.appendChild(el("div", "pipe-reason", md.reason || "no reason served"));
  return box;
}

/* ------------------------------------------------------------------ view */

export default async function pipelines(host) {
  const card = el("section", "card pipe-card");
  const head = el("div", "pipe-head");
  head.appendChild(el("h2", null, "Pipelines"));
  const refreshBtn = el("button", "pipe-refresh", "Refresh");
  refreshBtn.onclick = () => load();
  head.appendChild(refreshBtn);
  card.appendChild(head);
  card.appendChild(el("p", "sub",
    "Every registered pipeline, stale ones first and then by what is due "
    + "soonest. Open a row for its reason, its last run and its log. Running "
    + "from a row uses the same runner the scheduler uses, and says what it "
    + "costs first."));

  const summaryRow = el("div", "pipe-summary");
  const body = el("div", "pipe-body");
  const foot = el("div");
  card.append(summaryRow, body, foot);
  host.appendChild(card);

  // ---- state ----
  let M = null;                       // {res, models: rowModel[]}
  /* The default IS the sort: stale first, ties broken by next due. Clicking
     a header replaces it; there is no hidden second ordering underneath. */
  let sort = { key: "stale", dir: -1 };
  const open = new Set();             // task ids whose expandable is open
  const active = new Map();           // task_id -> run_id started from HERE
  const pendingConfirm = new Map();   // task_id -> the needs_confirm payload

  const timers = new Set();
  const clearTimers = () => { for (const t of timers) clearTimeout(t); timers.clear(); };
  const later = (fn, ms) => {
    const t = setTimeout(() => { timers.delete(t); fn(); }, ms);
    timers.add(t);
  };
  const onHash = () => {
    if ((location.hash || "").slice(1).split("?")[0] === "pipelines") return;
    clearTimers();
    removeEventListener("hashchange", onHash);
  };
  addEventListener("hashchange", onHash);

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
    M = { res, models: (res.rows || []).map(rowModel) };
    renderSummary();
    renderTable();
  }

  /* ------------------------------------------------------------ summary */
  function renderSummary() {
    summaryRow.textContent = "";
    const s = M.res.summary || {};
    const staleNow = M.models.filter(m => m.stale).length;
    const chips = [
      { k: "failing", v: s.n_failing, cls: "bad" },
      { k: "past their window", v: staleNow, cls: "warn" },
      { k: "ok", v: s.n_ok, cls: "good" },
      { k: "no fetch", v: s.n_refused, cls: "hold" },
      { k: "never ran", v: s.n_never_ran, cls: "idle" },
    ];
    for (const c of chips) {
      const cls = "pipe-stat " + c.cls
        + when(c.cls === "bad" && num(c.v), " alarm")
        + when(!num(c.v), " zero");
      const box = el("div", cls);
      box.appendChild(el("div", "v", String(numOr(c.v, 0))));
      box.appendChild(el("div", "k", c.k));
      summaryRow.appendChild(box);
    }
    if (num(s.n_running)) {
      const box = el("div", "pipe-stat run");
      box.appendChild(el("div", "v", String(s.n_running)));
      box.appendChild(el("div", "k", "running"));
      summaryRow.appendChild(box);
    }
    const overCap = num(s.month_credits) != null && num(s.month_credits_cap)
      && s.month_credits > s.month_credits_cap * 0.8;
    const credits = el("div", "pipe-stat credits" + when(overCap, " warn"));
    credits.appendChild(el("div", "v",
      `${numOr(s.month_credits, 0)}/${numOr(s.month_credits_cap, NONE)}`));
    credits.appendChild(el("div", "k", "odds credits this month"));
    credits.title = "Sum of credits_spent across every pipeline's ledger rows "
      + "this calendar month (UTC), against the Odds API free-tier allowance.";
    summaryRow.appendChild(credits);
  }

  /* --------------------------------------------------------------- table */

  /* The six columns, in order, with their sort keys. One list feeds the
     header, the sort and the per-cell labels the folded layout shows, so a
     cell can never carry a name the header does not. */
  const COLUMNS = [
    { key: "family", label: "family", numeric: false, first: 1 },
    { key: "task", label: "task", numeric: false, first: 1 },
    /* Oldest first: somebody sorting by last run is looking for the row that
       has not run, not for the one that just did. */
    { key: "age", label: "last run", numeric: true, first: -1 },
    { key: "state", label: "status", numeric: false, first: 1 },
    { key: "stale", label: "stale", numeric: false, first: -1 },
    { key: "due", label: "next due", numeric: true, first: 1 },
  ];
  const labelOf = key => (COLUMNS.find(c => c.key === key) || {}).label;

  function keyVal(md, key) {
    if (key === "family") return md.family || "";
    if (key === "task") return md.id || "";
    if (key === "age") return md.ageDays;
    if (key === "state") return numOr(STATE_RANK[md.state], 9);
    if (key === "stale") return pick(md.stale, 1, 0);
    const due = parseTs(md.nextDue);
    if (!due) return null;
    return due.getTime();
  }

  /* One comparator, one tie-break. Whatever the chosen key, two rows that
     tie are ordered by what is due soonest, which is what makes the default
     "stale, then next due" rather than "stale, then whatever". */
  function ordered() {
    const dueOf = md => {
      const v = keyVal(md, "due");
      if (v == null) return Infinity;
      return v;
    };
    const rank = (a, b, key, dir) => {
      const va = keyVal(a, key), vb = keyVal(b, key);
      if (typeof va === "string" || typeof vb === "string")
        return String(va).localeCompare(String(vb)) * dir;
      if (va == null && vb == null) return 0;
      if (va == null) return 1;                    // absent sorts last, always
      if (vb == null) return -1;
      return (va - vb) * dir;
    };
    return M.models.slice().sort((a, b) =>
      rank(a, b, sort.key, sort.dir)
      || (dueOf(a) - dueOf(b))
      || String(a.id).localeCompare(String(b.id)));
  }

  /* The SHARED sortable header (app.js sortableTh). This view had the app's
     only complete implementation and it drew its arrow inside the label text
     ("STALE ▼"), which is metadata in a header cell. The shared one draws
     the mark as its own 16px icon beside the name, keeps the aria-sort and
     the keyboard path, and takes the first direction from COLUMNS. */
  function headerCell(col) {
    const on = sort.key === col.key;
    let dir = null;
    if (on) dir = sort.dir;
    return sortableTh(col.label, {
      num: col.numeric,
      first: col.first,
      active: on,
      dir,
      title: `sort by ${col.label}`,
      onSort: (next) => { sort = { key: col.key, dir: next }; renderTable(); },
    });
  }

  function renderTable() {
    body.textContent = "";
    const tbl = el("table", "data pipe-table");
    const thead = el("thead"), hr = el("tr");
    for (const col of COLUMNS) hr.appendChild(headerCell(col));
    thead.appendChild(hr);
    const tb = el("tbody");
    tbl.append(thead, tb);
    for (const md of ordered()) {
      const tr = rowEl(md);
      tb.appendChild(tr);
      if (open.has(md.id)) tb.appendChild(expandEl(md, tr));
    }
    const wrap = el("div", "scroll-x");
    wrap.appendChild(tbl);
    body.appendChild(wrap);
  }

  /* Every cell carries the column it sits in, named from COLUMNS rather than
     retyped. Wide, the header says it once; folded to a card, CSS prints the
     label above each cell, so "in 3d" is never a number on its own. */
  function cell(node, key) {
    node.dataset.label = labelOf(key);
    return node;
  }

  function rowEl(md) {
    const tr = el("tr", "pipe-row"
      + when(md.state === "failing", " failing")
      + when(!md.enabled, " disabled")
      + when(open.has(md.id), " open"));

    tr.appendChild(cell(el("td", "pipe-fam", md.family || NONE), "family"));

    const name = el("td", "pipe-name");
    // the disclosure mark comes from the one icon set, not from two
    // different arrow families (R39, R41)
    const caret = el("span", "pipe-caret");
    caret.appendChild(icon(pick(open.has(md.id), "chevron-down",
                                "chevron-right")));
    name.appendChild(caret);
    name.appendChild(el("b", null, md.id));
    if (md.description) name.title = md.description;
    tr.appendChild(cell(name, "task"));

    const age = el("td", "num pipe-age", ageWords(md.ageDays));
    if (md.last && md.last.started) {
      age.title = `started ${absTime(md.last.started)}`
        + when(md.last.trigger, `, trigger ${md.last.trigger}`);
    } else {
      age.title = "no ledger row for this pipeline yet";
    }
    tr.appendChild(cell(age, "age"));

    const st = el("td");
    st.appendChild(chip(STATE_DOT[md.state] || "idle",
                        STATE_WORD[md.state] || String(md.state || NONE),
                        md.reason || ""));
    tr.appendChild(cell(st, "state"));

    const stale = el("td");
    if (md.stale) {
      stale.appendChild(chip("warn", "stale",
        `no success inside this task's own ${md.staleWindow} window`));
    } else {
      stale.appendChild(chip("good", "fresh",
        `succeeded inside its ${md.staleWindow} window`));
    }
    tr.appendChild(cell(stale, "stale"));

    const due = el("td", "num pipe-due");
    if (md.nextDue) {
      due.textContent = relTime(md.nextDue) || NONE;
      due.title = `next due ${absTime(md.nextDue)}`;
    } else {
      due.textContent = NONE;
      due.title = "no scheduled instant: on demand, or no future deadline known";
    }
    tr.appendChild(cell(due, "due"));

    /* The row is a button: it opens its expandable on click, on Enter and on
       Space, through app.js's one `rowLink` (R18). Nothing else on the row is
       clickable, so nothing competes. */
    rowLink(tr, () => toggle(md),
      `${md.id}: ${STATE_WORD[md.state] || md.state || "state unknown"}. `
      + `${md.reason || ""} Open its last run, its log and its Run button.`);
    tr.setAttribute("aria-expanded", pick(open.has(md.id), "true", "false"));
    tr.setAttribute("aria-controls", `pipe-exp-${md.id}`);
    return tr;
  }

  /* Open one row, close the rest: two open expandables push the thing you
     were comparing off the screen. Focus follows the panel and comes back to
     the row when it closes. */
  function toggle(md) {
    const wasOpen = open.has(md.id);
    open.clear();
    if (!wasOpen) open.add(md.id);
    renderTable();
    const row = body.querySelector(`[aria-controls="pipe-exp-${md.id}"]`);
    if (!row) return;
    if (wasOpen) { row.focus(); return; }
    const first = body.querySelector(`#pipe-exp-${md.id} button`);
    if (first) first.focus();
    else row.focus();
  }

  /* -------------------------------------------------------- the expandable */

  function expandEl(md, row) {
    const tr = el("tr", "pipe-exprow");
    const td = el("td");
    td.colSpan = COLUMNS.length;
    tr.appendChild(td);
    const box = el("div", "pipe-exp");
    box.id = `pipe-exp-${md.id}`;
    box.setAttribute("role", "region");
    box.setAttribute("aria-label", `${md.id} details`);
    td.appendChild(box);
    box.onclick = e => e.stopPropagation();

    if (md.description) box.appendChild(el("p", "sub", md.description));
    box.appendChild(healthEl(md));

    const facts = el("dl", "pipe-facts");
    const fact = (k, v, title) => {
      facts.appendChild(el("dt", null, k));
      const dd = el("dd", null, v);
      if (title) dd.title = title;
      facts.appendChild(dd);
    };
    fact("schedule", `${md.schedule || NONE} (${md.dueKind || NONE})`);
    fact("stale window", md.staleWindow || NONE,
         "how late a firing may run before the scheduler drops it");
    fact("last success", lastSuccessWords(md), lastSuccessTitle(md));
    fact("average run", fmtDur(md.avgMs), "mean of the last 20 successful runs");
    box.appendChild(facts);

    box.appendChild(lastRunEl(md));
    box.appendChild(actionEl(md, row));
    if (pendingConfirm.has(md.id)) {
      box.appendChild(confirmStrip(md, row));
    }
    box.appendChild(runsEl(md));
    return tr;
  }

  function lastSuccessWords(md) {
    if (!md.lastSuccess) return "never";
    return agoWords(md.lastSuccess);
  }
  function startedTitle(started) {
    if (!started) return "no start stamp";
    return absTime(started);
  }
  function spendWords(spend) {
    if (spend == null) return "spend unknown";
    return String(spend);
  }
  function lastSuccessTitle(md) {
    if (!md.lastSuccess) return "no successful run on record";
    return absTime(md.lastSuccess);
  }

  /* The last run, in full: when, who asked, what it did, and the note the
     runner wrote. A JSON note gives up its model and token count as named
     fields; anything else is printed exactly as it was stored. */
  function lastRunEl(md) {
    const box = el("div", "pipe-lastrun");
    box.appendChild(el("h3", null, "Last run"));
    if (!md.last) {
      box.appendChild(gapBox("no runs recorded",
        "this pipeline has no fetch_run rows yet, which is what its status "
        + "says",
        "the first scheduler tick or Run click writes one"));
      return box;
    }
    const l = md.last;
    const line = el("div", "pipe-runline");
    line.appendChild(chip(RUN_DOT[l.status] || "idle", runWord(l.status)));
    const started = el("span", "pipe-when", ageWords(md.ageDays));
    started.title = startedTitle(l.started);
    line.appendChild(started);
    line.appendChild(el("span", "pipe-mut", `trigger ${l.trigger || NONE}`));
    line.appendChild(el("span", "pipe-mut", fmtDur(l.duration_ms)));
    line.appendChild(el("span", "pipe-mut",
      `${fmtInt(num(l.rows_written) || 0)} rows written`));
    if (num(l.rows_unchanged)) {
      line.appendChild(el("span", "pipe-mut",
        `${fmtInt(l.rows_unchanged)} unchanged`));
    }
    if (num(l.credits)) {
      line.appendChild(el("span", "pipe-mut", `${l.credits} credits`));
    }
    if (l.model) line.appendChild(el("span", "pipe-mut", `model ${l.model}`));
    if (num(l.tokens)) {
      line.appendChild(el("span", "pipe-mut", `${fmtInt(l.tokens)} tokens`));
    }
    box.appendChild(line);

    if (l.note) {
      const note = el("pre", "pipe-note", String(l.note));
      note.title = pick(l.note_is_json,
        "the ledger note, stored as JSON and printed as stored",
        "the ledger note, exactly as the runner wrote it");
      box.appendChild(note);
    }
    if (l.log_path) {
      const path = el("div", "pipe-path");
      path.appendChild(el("span", "pipe-mut", "log"));
      path.appendChild(el("code", null, l.log_path));
      box.appendChild(path);
    }
    return box;
  }

  /* The recent runs, and one run's log tail on demand. Ten rows is what the
     board serves; the tail is a second panel call, so it is not paid for
     until somebody asks for it. */
  function runsEl(md) {
    const box = el("div", "pipe-runs");
    if (!md.runs.length) return box;
    box.appendChild(el("h3", null, "Recent runs"));
    const list = el("div", "pipe-runlist");
    for (const r of md.runs) {
      const item = el("button", "pipe-runitem"
        + when(r.status === "error", " bad"));
      item.appendChild(chip(RUN_DOT[r.status] || "idle", runWord(r.status)));
      const ran = el("span", "pipe-when", agoWords(r.started));
      ran.title = startedTitle(r.started);
      item.appendChild(ran);
      item.appendChild(el("span", "pipe-mut", fmtDur(r.duration_ms)));
      item.appendChild(el("span", "pipe-mut", r.trigger || NONE));
      item.setAttribute("aria-label",
        `${runWord(r.status)} run, ${agoWords(r.started)}`
        + ". Show its log.");
      item.onclick = e => { e.stopPropagation(); toggleLog(item, r); };
      list.appendChild(item);
    }
    box.appendChild(list);
    box.appendChild(el("p", "sub", "Click a run for the tail of its log."));
    return box;
  }

  async function toggleLog(item, run) {
    const existing = item.nextElementSibling;
    if (existing && existing.classList.contains("pipe-logbox")) {
      existing.remove();
      return;
    }
    const logBox = el("div", "pipe-logbox");
    logBox.appendChild(el("p", "sub", "loading log"));
    item.after(logBox);

    const r = await tryPanel("pipeline_run_log", { run_id: run.run_id });
    logBox.textContent = "";
    if (!r.ok) { logBox.appendChild(errBox(r.error)); return; }
    const res = r.result;
    if (res.empty || !res.found) {
      logBox.appendChild(gapBox("no log for this run",
        res.reason || "the ledger row is the run's record; its log file is "
        + "not on this machine",
        "run it again from this row to capture one"));
      return;
    }
    if (res.truncated) {
      logBox.appendChild(el("p", "sub",
        `showing the last ${res.lines.length} of ${res.n_lines_total} lines`));
    }
    const pre = el("pre", "pipe-log");
    pre.textContent = res.lines.join("\n") || "(the log file is empty)";
    logBox.appendChild(pre);
    pre.scrollTop = pre.scrollHeight;
  }

  /* --------------------------------------------------------- the trigger */

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
      const b = el("span", "pipe-mut", "disabled in the registry");
      b.title = "disabled in the registry; the trigger route refuses it too";
      box.appendChild(b);
      return box;
    }
    const cost = costWords(md);
    const btn = el("button", "pipe-run", `Run now: ${cost}`);
    btn.setAttribute("aria-label", `run ${md.id} now. It costs ${cost}.`);
    /* The registry's own words for what this run does, on the control
       itself: the expandable prints the description above, and the click is
       never blind either way. */
    btn.title = `${md.description || md.id}. One run costs ${cost}.`;
    if (md.confirmRequired) {
      btn.classList.add("metered");
      btn.title += " The cost is quoted again before anything runs.";
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

  async function startRun(md, row, confirmed) {
    let resp;
    try {
      resp = await postJSON(`/api/pipelines/${md.id}/run`,
                            { confirm: confirmed });
    } catch (e) {
      /* 409 (already running) and everything else land here: show the
         server's own words on the row rather than a console line. */
      showNote(md, String(e.message || e));
      return;
    }
    if (resp.needs_confirm) {
      pendingConfirm.set(md.id, resp);
      renderTable();
      return;
    }
    pendingConfirm.delete(md.id);
    if (resp.started && resp.run_id) {
      active.set(md.id, resp.run_id);
      renderTable();
      pollRun(md.id, resp.run_id);
    }
  }

  /* The inline confirm strip, inside the row's own expandable, quoting the
     exact numbers the server returned. Never a browser dialog. */
  function confirmStrip(md, row) {
    const payload = pendingConfirm.get(md.id) || {};
    const strip = el("div", "pipe-confirm");
    strip.onclick = e => e.stopPropagation();
    const spend = payload.month_spend;
    const cap = payload.month_cap;
    strip.appendChild(el("b", null,
      `about ${numOr(payload.credits_estimate, NONE)} credits`));
    strip.appendChild(el("span", null,
      ` · ${spendWords(spend)}${when(cap, `/${cap}`)} used this month`));
    const go = el("button", "pipe-run confirm", "Confirm run");
    go.onclick = async (e) => {
      e.stopPropagation();
      go.disabled = true;
      pendingConfirm.delete(md.id);
      await startRun(md, row, true);
    };
    const cancel = el("button", "pipe-cancel", "Cancel");
    cancel.onclick = (e) => {
      e.stopPropagation();
      pendingConfirm.delete(md.id);
      renderTable();
    };
    strip.append(go, cancel);
    return strip;
  }

  function showNote(md, text) {
    const box = body.querySelector(`#pipe-exp-${md.id}`);
    if (!box) return;
    box.querySelectorAll(".pipe-err").forEach(n => n.remove());
    box.appendChild(el("div", "pipe-err", text));
  }

  /* Poll every 2s until the run is terminal, then reload the whole board:
     the ledger row, health and the averages all moved. */
  function pollRun(taskId, runId) {
    const step = async () => {
      if (!document.body.contains(card)) return;
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

  /* ---------------------------------------------------------- lifecycle */
  body.appendChild(el("p", "sub", "loading"));
  await load();

  /* The board refreshes itself: ledger rows arrive from the scheduler too,
     not only from this page's own Run clicks. The open row and any pending
     confirm survive, because both live in state rather than in the DOM. */
  const autorefresh = async () => {
    if (!document.body.contains(card)) return;
    await load();
    later(autorefresh, 60_000);
  };
  later(autorefresh, 60_000);
}
