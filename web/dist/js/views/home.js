/* Dashboard: what do I do this gameweek.

   PAGE ORDER, exact. One card above the fold, the DECISION LEDGER:
   the gameweek and one Refresh control; the FIFTEEN THIS PAGE COMPUTED ON
   (squad_source, always rendered, above every answer it governs); BEFORE YOU
   READ THIS (every live gap with its fix, payload derived, nothing rendered
   when there are none); then four rows in the owner's order, TRANSFER,
   CAPTAIN, BENCH, CHIP, one row per question by the brief's PRINTED
   precedence, each with the answer, the numbers it rests on, a confidence
   word decided by served fields alone, and one expandable working block.
   Below the fold, the working: THE LINEUP (one eleven, the best formation
   legal XI by consensus xPts from brief.best_xi, with the decision tags of
   the four rows riding on the players they touch), SIGNALS (deterministic
   gates), WHAT WAS CHECKED (the watch log, folded), WHERE THE SEASON STANDS,
   and the provenance foot.

   THE CONFIDENCE LATTICE, from served fields only, in this precedence:
     none       verdict.lines[].rule starts with "no_"
     stale      verdict.lines[].state is stale, superseded or aging, or the
                row cites squad_overview while squad_source.live is false
     contested  verdict.lines[].dissent is non empty, or best_xi.close_call
     firm       none of the above
   The deciding field prints beside the word, so the label can be checked
   against the payload in one glance. No score is invented in the browser.

   THREE CALLS, in parallel: `squad_overview`, `dashboard_brief`,
   `GET /api/solve/status` (read only; a solve that survives a reload resumes
   its polling UI). Every number on this page comes from one of them;
   thresholds render from the brief's own `thresholds` echo, so this file
   contains no gate constants. Wording lives HERE, keyed by rule/kind ids,
   because the brief carries no free-text recommendation field by contract.

   REFRESH IS THREE VERBS AND THEY ARE LABELLED. Re-read costs nothing and
   re-runs both panels in place, then says whether source_as_of moved.
   Re-solve costs minutes and says so beside the button. Nothing else on this
   page writes.

   AGES ARE WHOLE DAYS, one helper, `fmtAgeDays`. No Nh, no Nd Nh, no bare
   hour count. The exact instant stays one hover away in a title. The
   deadline countdown is the shell topbar's and keeps its minutes, because it
   looks forward at a deadline rather than back at data.

   COLOUR LAW: the lineup card's OPPONENT chip reuses the fixtures tab's fx-
   ramp on fixture_board's opponent_only ease, attack ease for MID/FWD,
   defence ease for GKP/DEF (a selection, never a blend), CAPS home and lower
   away, domain served by the payload. The xPts chip prints its number with
   no tint. --good/--warn/--bad stay reserved for the risk/status channel.

   SOLVER CURRENCY LAW: gain_over_roll is the solver's own forecast in the
   plan's objective_mode currency, labelled in its forecast's name on the
   card, never summed or blended with the per player numbers on the lineup.

   Every zone degrades alone (tryPanel memo + named gaps); the page never
   blanks. */

import { runPanel, getJSON, postJSON, el, errBox, provenance,
         fmtPrice, fmt1, fmt2 } from "/js/app.js";
import { attachPlayerDrawer, showPlayerDetail } from "/js/components/playerdrawer.js";

const PHOTO = c =>
  `https://resources.premierleague.com/premierleague/photos/players/110x140/p${c}.png`;

// ---------------------------------------------------------------- utils

// A value with a stated fallback, a branch written as a call, and a string
// present only when a condition holds. The house rule forbids the rhetorical
// question mark, and a chain of ternaries is a wall of them.
function or(v, fallback) {
  if (v == null) return fallback;
  return v;
}
function pick(cond, a, b) {
  if (cond) return a;
  return b;
}
function when(cond, text) {
  if (cond) return text;
  return "";
}

function parseTs(s) {
  if (!s) return null;
  const d = new Date(String(s).replace(" ", "T").replace(/\+00:00$/, "Z"));
  if (isNaN(d)) return null;
  return d;
}

// THE age vocabulary for this page: whole days, never hours. A local helper
// until app.js grows the shared `fmtAgeDays` beside `fmtAge`; the Creators
// tab keeps its own `ageDays` for the same reason.
function fmtAgeDays(iso) {
  const d = parseTs(iso);
  if (!d) return "age unknown";
  const h = (Date.now() - d.getTime()) / 3.6e6;
  if (h < 0) return "dated ahead of now";
  if (h < 24) return "today";
  if (h < 48) return "yesterday";
  return `${Math.floor(h / 24)} days`;
}
// The same age inside a sentence: "today", "yesterday", "5 days ago".
function agoPhrase(iso) {
  const a = fmtAgeDays(iso);
  if (a === "today" || a === "yesterday") return a;
  return `${a} ago`;
}
// The same vocabulary for a span the payload serves in hours, so
// solve.age_hours of 254 reads "10 days" and never "254h".
function daysFromHours(h) {
  if (h == null || !isFinite(h)) return "age unknown";
  if (h < 24) return "today";
  if (h < 48) return "yesterday";
  return `${Math.floor(h / 24)} days`;
}
// A window LENGTH is not an age: it is the measurement's own span, and it is
// spelled out so no reader mistakes it for one.
function hoursWindow(h) {
  if (h == null) return "window length unknown";
  return `${fmt2(h)} hour window`;
}
function localClock(iso) {
  const d = parseTs(iso);
  if (!d) return "an unrecorded time";
  return d.toTimeString().slice(0, 5);
}
/** An FPL rank with thousands separators. Ranks are large and read wrong
 *  without them: 769533 and 76953 are one glance apart. */
function fmtRank(v) {
  if (v == null || !Number.isFinite(Number(v))) return "unknown";
  return Number(v).toLocaleString("en-GB");
}

function fmtSigned(v, digits = 0) {
  if (v == null) return "–";
  const s = Math.abs(v).toLocaleString(undefined,
    { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return pick(v >= 0, "+", "−") + s;
}

// A section with no data says WHICH data and WHY, never whitespace.
function namedGap(title, body) {
  const d = el("div", "fx-gap");
  d.appendChild(el("b", null, title));
  if (body instanceof Node) d.appendChild(body);
  else d.appendChild(document.createTextNode(body));
  return d;
}

// Panel call that reports failure as data (fixtures idiom, memoised 404s).
const MISSING = new Map();
async function tryPanel(script, params = {}) {
  const gone = MISSING.get(script);
  if (gone) return { ok: false, error: gone, script, missing: true, cached: true };
  try {
    const { result, provenance: prov } = await runPanel(script, params);
    return { ok: true, result, prov, script };
  } catch (e) {
    const missing = /HTTP 404|no panel script named/.test(String(e.message || e));
    if (missing) MISSING.set(script, e);
    return { ok: false, error: e, script, missing };
  }
}

function card(title, sub) {
  const c = el("section", "card");
  if (title) c.appendChild(el("h2", null, title));
  if (sub) c.appendChild(el("p", "sub", sub));
  return c;
}

function citeChip(panel, asOf) {
  const b = el("button", "cite");
  b.type = "button";
  b.textContent = pick(asOf, `${panel} · ${fmtAgeDays(asOf)}`, String(panel));
  b.title = pick(asOf, `as of ${asOf}`, `${panel}: no as-of instant served`);
  return b;
}

/* FPL's five FDR steps, easiest to hardest. Same classes and the same
   thresholds the Fixtures board uses, so one colour vocabulary. */
const FX_CLASSES = ["fx-d1", "fx-d2", "fx-d3", "fx-d4", "fx-d5"];

const CHIP_NAME = { "3xc": "Triple Captain", bboost: "Bench Boost",
                    wildcard: "Wildcard", freehit: "Free Hit" };
const CHIP_SHORT = { wildcard: "WC", freehit: "FH", bboost: "BB", "3xc": "TC" };

/* THE COLLISION RULE, authored here, not served. One player can attract two
   decisions: the sell leg of a transfer can also be the captain pick. The
   payload carries no ordering between questions, so this view states one and
   says that it did. The most expensive decision wins the visible tag; the
   loser renders as a counted mark that names the row it came from. */
const DECISION_ORDER = ["transfer", "captain", "bench", "chip"];

function shortDate(iso) {
  // UTC: a 06:48Z artefact is that day's, not the evening before in the
  // browser's own zone
  const d = parseTs(iso);
  if (!d) return null;
  return d.toLocaleDateString("en-GB",
    { day: "numeric", month: "short", timeZone: "UTC" });
}
/* Payload prose from other panels may carry em-dash asides; this page prints
   none (prose_style.py's rule), so they are rewritten at the point of print.
   alerts[].news is FPL's own copy and intel text is model authored, so this
   is the last guard between a supplier's punctuation and the page. */
function noDash(s) {
  if (s == null) return s;
  return String(s).replace(/\s+\u2014\s+/g, "; ").replace(/\u2014/g, ", ");
}

// Name the currency a plan's gain is priced in: "consensus forecast" (what
// every other surface shows) or "engine forecast" (the engine's own model),
// with the engine-fill share when the consensus left gaps.
function fcName(plan) {
  const src = plan && plan.forecast_source;
  if (!src) return "solver forecast";
  const fill = plan.engine_fill_share;
  if (!src.startsWith("consensus")) return "engine forecast";
  return `consensus forecast${when(fill, ` (${Math.round(fill * 100)}% engine fill)`)}`;
}

// ------------------------------------------------------------------ view

export default async function home(host) {
  const dh = attachPlayerDrawer("home");

  // ONE card above the fold. Everything in it is an answer or the reason an
  // answer is weaker than it looks.
  const ledgerCard = card(null, null);
  ledgerCard.classList.add("db-ledger");
  const ledgerHead = el("div", "dl-head");
  const sourceBlock = el("div", "db-source");
  // A disclosure, not a div: on a laptop it stands open and the four answers
  // still clear the fold, and on a phone the count and the kinds stay above
  // the answers while the reasons are one tap away. Nothing is hidden that
  // the summary does not name.
  const gapStrip = el("details", "db-gaps");
  const wideEnoughForGaps = matchMedia("(min-width: 900px)").matches;
  const verdictCard = el("div", "db-verdict");
  ledgerCard.append(ledgerHead, sourceBlock, gapStrip, verdictCard);

  const pitchCard = card("The lineup", null);
  const pitchBody = el("div");
  pitchCard.appendChild(pitchBody);
  const tilesCard = card("Signals",
    "deterministic gates over the panels, each with the gate it cleared and "
    + "the panel it came from");
  const tilesBody = el("div");
  tilesCard.appendChild(tilesBody);
  const watchCard = card(null, null);
  const watchBody = el("div");
  watchCard.appendChild(watchBody);
  const standingStrip = el("section", "card db-standing");
  const foot = el("div", "db-foot");
  host.append(ledgerCard);

  // One line naming the calls in flight. The reserve sits on the CARD, not
  // on the line: the source block and the gap strip render above the four
  // rows, so holding only the rows' height would still push them down.
  ledgerCard.classList.add("db-pending");
  ledgerCard.appendChild(el("div", "db-loading",
    "Reading squad_overview and dashboard_brief."));

  const [sqR, brR, stR] = await Promise.all([
    tryPanel("squad_overview", {}),
    tryPanel("dashboard_brief", {}),
    getJSON("/api/solve/status")
      .then(pl => ({ ok: true, payload: pl }))
      .catch(e => ({ ok: false, error: e })),
  ]);
  const sq = pick(sqR.ok && !sqR.result.empty, sqR.result, null);
  let brief = pick(brR.ok && !brR.result.empty, brR.result, null);
  let solveStatus = pick(stR.ok, stR.payload, null);
  let thr = or(brief && brief.thresholds, {});
  let median = or(brief && brief.xi_median_xpts, null);
  let bestXi = or(brief && brief.best_xi, null);
  let squadSource = or(brief && brief.squad_source, null);
  // haul odds are an engine simulation with a date; they render only when
  // that simulation is newer than the last deadline that passed
  let lastDeadline = parseTs(brief?.solve?.last_deadline_utc);
  let haulGen = parseTs(brief?.p_haul_generated);
  let haulFresh = !!(haulGen && lastDeadline && haulGen > lastDeadline);
  let haulUnavailable = "haul odds unavailable (last simulation "
    + or(shortDate(brief?.p_haul_generated), "date unknown") + ")";
  let squadCodes = new Set();
  let teamFix = new Map();
  let minutesBy = new Map();
  let easeDom = null;
  // THE fifteen this page computes on. The brief serves them under `squad`
  // when it can, which removes a cross-panel join from the page's primary
  // object; squad_overview's own rows are the fallback and say so.
  let squad15 = [];
  let squadFromBrief = false;

  function readBrief() {
    thr = or(brief && brief.thresholds, {});
    median = or(brief && brief.xi_median_xpts, null);
    bestXi = or(brief && brief.best_xi, null);
    squadSource = or(brief && brief.squad_source, null);
    lastDeadline = parseTs(brief?.solve?.last_deadline_utc);
    haulGen = parseTs(brief?.p_haul_generated);
    haulFresh = !!(haulGen && lastDeadline && haulGen > lastDeadline);
    haulUnavailable = "haul odds unavailable (last simulation "
      + or(shortDate(brief?.p_haul_generated), "date unknown") + ")";
    const served = or(brief && brief.squad, null);
    squadFromBrief = !!(served && served.length);
    squad15 = pick(squadFromBrief, served,
      [...or(sq && sq.starters, []), ...or(sq && sq.bench, [])]);
    squadCodes = new Set(squad15.map(x => x.code));
    teamFix = new Map(or(brief && brief.team_fixtures, []).map(
      tf => [tf.team_code, tf]));
    minutesBy = new Map(or(brief && brief.squad_projection, []).map(
      m => [m.code, m]));
    const d = brief?.fixtures_scale?.domain;
    easeDom = null;
    if (Array.isArray(d) && d.length) easeDom = Math.abs(d[d.length - 1]);
  }
  readBrief();

  function easeClass(ease) {
    // the fixtures view's bucket(): five classes across the served
    // [-dom, +dom]; positive ease = easier = blue. No domain, no colour.
    if (ease == null || easeDom == null) return null;
    const s = Math.max(-1, Math.min(1, ease / easeDom));
    if (s >= 3 / 5) return FX_CLASSES[0];
    if (s >= 1 / 5) return FX_CLASSES[1];
    if (s > -1 / 5) return FX_CLASSES[2];
    if (s > -3 / 5) return FX_CLASSES[3];
    return FX_CLASSES[4];
  }

  const pitchCardByCode = new Map();   // code -> .pp element (for drills)
  const playerIndex = new Map();       // code -> best-known player object
  const remember = r => { if (r && r.code != null) playerIndex.set(r.code, r); };

  function reindex() {
    playerIndex.clear();
    squad15.forEach(remember);
    for (const a of or(brief && brief.alerts, [])) or(a.players, []).forEach(remember);
    for (const tl of or(brief && brief.tiles, [])) remember(tl.player);
    for (const mv of or(brief && brief.moves, [])) { remember(mv.in); remember(mv.out); }
    for (const mv of or(brief?.solve?.plan?.moves, [])) {
      remember(mv.in); remember(mv.out);
    }
    remember(brief?.solve?.plan?.captain);
    for (const ln of or(brief?.verdict?.lines, [])) {
      remember(ln.pick);
      for (const mv of or(ln.moves, [])) { remember(mv.in); remember(mv.out); }
      for (const d of or(ln.dissent, [])) {
        remember(d.player); remember(d.in); remember(d.out);
      }
    }
  }
  reindex();

  // own-player price-fall risk rides ON the lineup card (the squad is where
  // own-player risk belongs)
  const dropByCode = new Map();
  function readDrops() {
    dropByCode.clear();
    for (const a of or(brief && brief.alerts, [])) {
      if (a.rule !== "own_price_fall") continue;
      for (const c of or(a.codes, [])) dropByCode.set(c, or(a.numbers, {}));
    }
  }
  readDrops();

  function openDrawer(code) {
    const ref = or(playerIndex.get(code), { code, name: String(code) });
    showPlayerDetail(dh, ref, {});
  }

  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  function scrollToCard(node) {
    node.scrollIntoView({ behavior: pick(reduceMotion, "auto", "smooth"),
                          block: "start" });
  }
  // the transfer row holds the solver's working; a drill that used to scroll
  // to a solver card opens that row instead
  const rowByQuestion = new Map();
  function openRow(question) {
    const row = rowByQuestion.get(question);
    if (!row) return scrollToCard(ledgerCard);
    row.open = true;
    scrollToCard(row);
  }

  function drillTo(drill) {
    if (!drill) return;
    if (drill.drawer != null) return openDrawer(drill.drawer);
    if (drill.focus === "pitch" || drill.focus === "squad")
      return scrollToCard(pitchCard);
    if (drill.focus === "solver" || drill.focus === "moves")
      return openRow("transfer");
    if (drill.tab) { location.hash = "#" + drill.tab; }
  }

  function tinyFace(code) {
    const img = el("img", "avatar");
    img.loading = "lazy"; img.alt = "";
    img.src = PHOTO(code);
    img.onerror = () => { img.onerror = null; img.style.visibility = "hidden"; };
    return img;
  }

  // ------------------------------------------------- the ledger header

  // Re-read is the zero-cost verb: it re-runs both panels and then says
  // whether the panels' own as_of moved. A control that spins and changes
  // nothing, silently, is the failure this reports its way out of.
  let refreshNote = null;
  function renderHead() {
    ledgerHead.textContent = "";
    const gwLabel = or(brief?.gw, sq?.gw);
    const left = el("div", "dl-headleft");
    if (gwLabel != null) left.appendChild(el("b", "dl-gw", `GW${gwLabel}`));
    const dl = or(brief?.deadline_utc, brief?.solve?.next_deadline_utc);
    if (dl) {
      const dEl = el("span", "dl-deadline", `deadline ${shortDate(dl)}`);
      dEl.title = `deadline ${dl}; the countdown to it is in the topbar, `
        + "which is the one place on this page that speaks in hours";
      left.appendChild(dEl);
    }
    ledgerHead.appendChild(left);

    const right = el("div", "dl-headright");
    const age = el("span", "dl-read", `read ${fmtAgeDays(brief?.as_of)}`);
    age.title = `the oldest load-bearing clock behind this page: ${or(brief?.as_of, "none served")}`;
    right.appendChild(age);
    right.appendChild(refreshButton());
    refreshNote = el("span", "dl-note");
    right.appendChild(refreshNote);
    ledgerHead.appendChild(right);
  }
  function refreshButton() {
    const b = el("button", "chip dl-refresh", "Refresh");
    b.type = "button";
    b.title = "re-reads squad_overview and dashboard_brief over the warehouse "
      + "and reports whether their as-of moved. It writes nothing and costs "
      + "seconds. Re-run solve, on the transfer row, is the expensive one.";
    b.onclick = async () => {
      b.disabled = true;
      const was = { ...or(brief?.sources_as_of, {}) };
      const wasAsOf = brief?.as_of;
      refreshNote.textContent = "re-reading";
      const [s2, b2] = await Promise.all([
        tryPanel("squad_overview", {}), tryPanel("dashboard_brief", {})]);
      if (b2.ok && !b2.result.empty) brief = b2.result;
      if (s2.ok && !s2.result.empty && sq) {
        // squad_overview's own rows feed the lineup fallback and the chip
        // ledger; the served object is replaced field by field so the
        // closures above keep pointing at live data
        Object.assign(sq, s2.result);
      }
      readBrief(); reindex(); readDrops();
      const now = or(brief?.sources_as_of, {});
      const moved = Object.keys(now).filter(k => now[k] !== was[k]);
      renderAll();
      if (moved.length) {
        refreshNote.textContent = `${moved.join(", ")} moved`;
      } else {
        refreshNote.textContent = `nothing moved, still ${fmtAgeDays(wasAsOf)}`;
      }
      refreshNote.title = "a re-read moves a number only when a pipeline has "
        + "written since the last read; this compares the panels' own as-of "
        + "values before and after";
      b.disabled = false;
    };
    return b;
  }

  // ----------------------------------------- THE LEDGER: four questions

  // The haul probability is the engine simulation's, which can be weeks
  // older than every consensus number beside it, so its date rides with it.
  function haulSimTag() {
    const d = shortDate(brief?.p_haul_generated);
    return when(d, ` · sim ${d}`);
  }
  function dissentText(d) {
    const n = or(d.numbers, {});
    if (d.voice === "mean_xpts") {
      return `consensus prefers ${or(d.player?.name, "an unnamed player")}`
        + when(n.xpts != null, ` (${fmt1(n.xpts)} xPts)`);
    }
    if (d.voice === "haul_odds") {
      if (!haulFresh) return null;
      return `haul odds prefer ${or(d.player?.name, "an unnamed player")}`
        + when(n.p_haul != null,
               ` (${Math.round(n.p_haul * 100)}%${haulSimTag()})`);
    }
    if (d.voice === "creator_armband") {
      // a creator armband call on a player you do not own is not a captain
      // option; it is labelled so it cannot read as one
      return `creators: ${or(n.armband_calls, "an unreported number of")} named `
        + `${or(d.player?.name, "an unnamed player")}`
        + when(d.player?.code != null && !squadCodes.has(d.player.code),
               " (not owned)");
    }
    if (d.voice === "rule_moves") {
      return `rules prefer ${or(d.out?.name, "an unnamed player")} to `
        + `${or(d.in?.name, "an unnamed player")}`;
    }
    if (d.voice === "solver") {
      return `solver ${String(d.rule).replace("solve_", "")}`
        + when(n.age_hours != null, ` · ${daysFromHours(n.age_hours)} old`);
    }
    return String(d.voice);
  }
  function dissentChip(d) {
    const txt = dissentText(d);
    if (txt == null) {
      const omit = el("span", "chip db-omit", "haul odds not shown");
      omit.title = haulUnavailable;
      return omit;
    }
    const c = el("button", "vd-dissent", txt);
    c.type = "button";
    c.title = `${d.voice}; its own measure, displayed beside the pick, `
      + `never summed into it · ${d.source_panel}`;
    c.onclick = (e) => { e.stopPropagation(); drillTo(d.drill); };
    return c;
  }
  // A bolded lead and its trailing clause as ONE flex item, so the row's
  // gap never opens a space before the clause's own comma.
  function phrase(lead, rest) {
    const w = el("span");
    w.append(el("b", null, lead), document.createTextNode(rest));
    return w;
  }
  function verdictFace(ref) {
    const wrap = el("span", "vd-face");
    if (ref?.code == null) return wrap;
    const img = el("img", "avatar");
    img.alt = ""; img.loading = "lazy";
    img.src = PHOTO(ref.code);
    img.onerror = () => { img.onerror = null; img.style.visibility = "hidden"; };
    wrap.appendChild(img);
    return wrap;
  }

  /* The confidence word and the field that decided it. Four words over
     served fields, in a fixed precedence: absence of an answer outranks a
     dated answer, and a dated answer outranks a disputed one. */
  function confidenceOf(ln) {
    const fields = [];
    let asOf = or(ln.source_as_of, or(brief?.sources_as_of, {})[ln.source_panel]);
    // the brief's own clock is the payload's as_of; it keeps no entry for
    // itself in sources_as_of, and an unclocked row reads as unknowable
    if (asOf == null && ln.source_panel === "dashboard_brief")
      asOf = or(brief?.as_of, null);
    const cite = `${ln.source_panel} ${fmtAgeDays(asOf)}`;
    if (String(ln.rule).startsWith("no_")) {
      return { word: "none", fields: [ln.rule, cite] };
    }
    const dated = parseTs(asOf);
    const predates = !!(dated && lastDeadline && dated < lastDeadline);
    const onOldSquad = ln.source_panel === "squad_overview"
      && squadSource && squadSource.live === false;
    if (["stale", "superseded", "aging"].includes(ln.state) || predates
        || onOldSquad) {
      if (ln.state) fields.push(`state ${ln.state}`);
      if (onOldSquad) fields.push("squad_source.live false");
      fields.push(cite);
      return { word: "stale", fields };
    }
    const closeCall = ln.question === "captain" && bestXi && bestXi.close_call;
    if (or(ln.dissent, []).length || closeCall) {
      if (closeCall) fields.push("best_xi.close_call");
      for (const d of or(ln.dissent, [])) fields.push(d.voice);
      fields.push(cite);
      return { word: "contested", fields };
    }
    return { word: "firm", fields: [cite] };
  }

  /* One row per question. Column 1 the question, column 2 the answer, column
     3 the numbers it rests on (one per line, right aligned on a shared
     decimal edge), column 4 the confidence word and the field that decided
     it, column 5 the caret onto the working. */
  function verdictRow(ln) {
    const row = el("details", "dl-row");
    rowByQuestion.set(ln.question, row);
    const sum = el("summary", "dl-sum");
    sum.appendChild(el("b", "dl-q", ln.question));
    const main = el("span", "dl-answer");
    const n = or(ln.numbers, {});
    const put = (...parts) => parts.forEach(x => main.appendChild(
      pick(typeof x === "string", document.createTextNode(String(x)), x)));
    const numBits = [];
    switch (ln.rule) {
      case "solver_plan": {
        for (const mv of or(ln.moves, [])) {
          const strip = el("span", "vd-movestrip");
          strip.append(verdictFace(mv.out),
                       el("s", "vd-out", mv.out.name),
                       el("span", "sv-arrow", "→"),
                       verdictFace(mv.in),
                       el("b", null, mv.in.name));
          main.appendChild(strip);
        }
        if (n.gain_over_roll != null)
          numBits.push(`${fmtSigned(n.gain_over_roll, 1)} xPts vs rolling`
            + `, ${fcName(brief?.solve?.plan)}`);
        if (n.optimality_gap_pct != null)
          numBits.push(`${fmt1(n.optimality_gap_pct)}% gap`);
        if (n.age_hours != null) numBits.push(`${daysFromHours(n.age_hours)} old`);
        if (n.hits) numBits.push(`${n.hits} hit(s)`);
        // The balance the plan leaves. A negative one cannot be executed, and
        // the page used to print each price change without the net or the
        // result, so the rejection happened at the FPL site instead of here.
        if (n.bank_after_tenths != null) {
          const bank = n.bank_after_tenths / 10;
          if (bank < 0) {
            const bad = el("b", "vd-unaffordable",
              `not affordable: bank would be ${fmtPrice(bank)}`);
            bad.title = "the plan spends more than you hold; FPL will reject "
              + "these transfers";
            main.appendChild(bad);
          } else {
            numBits.push(`${fmtPrice(bank)} left in the bank`);
          }
        }
        break;
      }
      case "solver_roll":
        put(el("b", null, "bank the transfer"),
            ", no move cleared the bar vs rolling");
        if (n.free_transfers != null)
          numBits.push(`${n.free_transfers} FT carried forward`);
        if (n.optimality_gap_pct != null)
          numBits.push(`${fmt1(n.optimality_gap_pct)}% gap`);
        if (n.age_hours != null) numBits.push(`${daysFromHours(n.age_hours)} old`);
        break;
      case "rule_moves_solver_stale":
      case "rule_moves_solver_missing": {
        for (const mv of or(ln.moves, [])) {
          const strip = el("span", "vd-movestrip");
          strip.append(verdictFace(mv.out),
                       el("s", "vd-out", mv.out.name),
                       el("span", "sv-arrow", "→"),
                       verdictFace(mv.in),
                       el("b", null, mv.in.name));
          main.appendChild(strip);
        }
        numBits.push(pick(ln.rule === "rule_moves_solver_stale",
          "rule-based, the solver plan is stale",
          "rule-based, no solver plan stands"));
        break;
      }
      case "no_move_named":
        put("no move named: no plan stands and nothing cleared a gate");
        break;
      case "solver_plan_captain":
      case "mean_xpts_captain": {
        // no solver plan stands: the line is the best XI's own top three,
        // each with its consensus number and its opponent, and a lead under
        // the served gate is a close call, never an asserted pick
        let cands = [];
        if (ln.rule === "mean_xpts_captain" && bestXi)
          cands = or(bestXi.captain_candidates, []);
        if (cands.length) {
          const c0 = cands[0], c1 = cands[1];
          if (bestXi.close_call && c1) {
            put(el("b", null, "close call"),
                `: ${c0.player.name} ${fmt1(c0.xpts)} vs `
                + `${c1.player.name} ${fmt1(c1.xpts)} consensus xPts`);
          } else {
            put(verdictFace(c0.player), el("b", null, c0.player.name),
                ` ${oppText(c0.player.team_code)}`);
          }
          numBits.push(`${fmt1(c0.xpts)} xPts`);
          if (bestXi.captain_lead_xpts != null)
            numBits.push(`lead ${fmt1(bestXi.captain_lead_xpts)} `
              + `(close-call gate ${or(thr.captain_close_call_xpts, "not served")})`);
          if (haulFresh && c0.p_haul != null)
            numBits.push(`${Math.round(c0.p_haul * 100)}% haul odds`
              + haulSimTag());
          if (n.captain_delta_xpts != null)
            numBits.push(`${fmtSigned(n.captain_delta_xpts, 1)} xPts against `
              + `your armband`);
          break;
        }
        put(verdictFace(ln.pick), el("b", null, or(ln.pick?.name, "no pick served")));
        if (ln.pick?.team_code != null) numBits.push(oppText(ln.pick.team_code));
        if (ln.pick?.code != null && !squadCodes.has(ln.pick.code))
          numBits.push("not in your 15; enters via the plan's moves");
        // A solver pick is quoted in the solver's OWN currency first; the
        // consensus figure rides beside it, labelled. Two sources are named
        // separately only when they disagree: printing one figure twice
        // reads as two independent confirmations of it.
        {
          let solverX = null;
          if (ln.rule === "solver_plan_captain") solverX = n.pick_solver_xpts;
          const same = solverX != null && n.pick_xpts != null
            && Math.abs(solverX - n.pick_xpts) < 0.05;
          if (same) {
            numBits.push(`${fmt1(n.pick_xpts)} xPts`
              + when(n.solver_gw != null, ` GW${n.solver_gw}`)
              + `, solver and consensus agree`);
          } else {
            if (solverX != null)
              numBits.push(`${fmt1(solverX)} xPts`
                + when(n.solver_gw != null, ` GW${n.solver_gw}`)
                + `, ${fcName(brief?.solve?.plan)}`);
            if (n.pick_xpts != null)
              numBits.push(`consensus ${fmt1(n.pick_xpts)} xPts`);
          }
        }
        if (n.pick_p_haul != null && haulFresh)
          numBits.push(`${Math.round(n.pick_p_haul * 100)}% haul odds`
            + haulSimTag());
        // What the armband change is actually worth: the one free action on
        // the page, no transfer and no hit. Served only when the pick differs
        // from the locked armband.
        if (n.captain_delta_xpts != null)
          numBits.push(`${fmtSigned(n.captain_delta_xpts, 1)} xPts against `
            + `your armband`);
        break;
      }
      case "no_captain_named":
        put("no captain named: neither a plan nor a projection stands");
        break;
      case "bench_inversion_applied":
      case "bench_confirmed": {
        // the lineup below draws ONE eleven, the best XI; this line says how
        // far the locked picks sit from it, by name
        if (bestXi && or(bestXi.xi_codes, []).length === 11) {
          const nd = or(bestXi.n_differs, 0);
          const out = or(bestXi.differs, []).slice(0, nd).map(d => d.name);
          const inn = or(bestXi.differs, []).slice(nd).map(d => d.name);
          if (nd) {
            put(phrase(`${nd} locked starter${when(nd > 1, "s")} not in the `
              + `best XI`, `: ${out.join(", ")}`
              + when(inn.length, ` (in: ${inn.join(", ")})`)));
          } else {
            put(el("b", null, "your locked XI is the best XI"));
          }
          if (n.n_changes != null) numBits.push(`${n.n_changes} change(s)`);
          if (n.swap_delta_xpts != null)
            numBits.push(`${fmtSigned(n.swap_delta_xpts, 1)} xPts`);
          if (bestXi.formation) numBits.push(bestXi.formation);
          if (bestXi.xi_xpts != null)
            numBits.push(`Σ ${fmt1(bestXi.xi_xpts)} xPts`);
          break;
        }
        if (ln.rule === "bench_inversion_applied") {
          put(el("b", null,
            `${n.n_changes} bench change${when(n.n_changes > 1, "s")}`),
            " by consensus xPts");
          if (n.swap_delta_xpts != null)
            numBits.push(`${fmtSigned(n.swap_delta_xpts, 1)} xPts`);
        } else {
          put("your bench order stands");
        }
        break;
      }
      case "no_bench_named":
        put("no bench read: squad unreadable");
        break;
      // One flex item, not two: the answer cell is a flex row with a gap, so
      // a trailing text node would float its comma away from the word.
      case "solver_plan_chip":
        put(phrase(or(CHIP_NAME[ln.chip], String(ln.chip)),
                   ", the plan spends it"));
        break;
      case "chip_hold":
        put(phrase("hold", ", the plan spends no chip"));
        break;
      case "no_chip_named":
        put("hold by default: no plan stands to ask");
        break;
      default:
        put(ln.rule);
    }

    // the collision: a player carrying a higher-precedence decision from
    // another row is named here, because acting on that row invalidates this
    const clash = collisionFor(ln);
    if (clash) {
      const c = el("span", "dl-clash", clash);
      c.title = "one player, two decisions. This view orders them "
        + `${DECISION_ORDER.join(" over ")}; the payload serves no ordering `
        + "between questions, so the order is this page's and is stated here.";
      main.appendChild(c);
    }

    const nums = el("span", "dl-nums");
    for (const b of numBits) nums.appendChild(el("span", "dl-num", b));
    const conf = confidenceOf(ln);
    row.classList.add("dl-" + conf.word);
    const meta = el("span", "dl-meta");
    const word = el("b", "dl-conf", conf.word);
    word.title = "decided by the fields printed beneath it, and by nothing "
      + "else: no score is computed in the browser";
    meta.appendChild(word);
    for (const f of conf.fields) meta.appendChild(el("span", "dl-field", f));
    // Sources disagreeing about the captained man is louder than any single
    // number: the solver keeps the pick (precedence), the market's dissent
    // rides beside it so a triple captain is never spent unknowingly.
    if (n.solver_vs_consensus != null && n.divergence_gate != null
        && Math.abs(n.solver_vs_consensus) >= n.divergence_gate) {
      const dv = el("span", "chip warn",
        `solver ${fmt1(n.pick_solver_xpts)} vs consensus `
        + `${fmt1(n.pick_xpts)} xPts`);
      dv.title = "the solver's own forecast for this player differs from the "
        + `provider consensus by ${fmtSigned(n.solver_vs_consensus, 2)} xPts `
        + `(gate ${n.divergence_gate}); the pick rests on a number the `
        + "market does not share";
      meta.appendChild(dv);
    }
    sum.append(main, nums, meta, el("span", "dl-caret"));
    row.appendChild(sum);
    const work = el("div", "dl-work");
    workFor(ln, work);
    row.appendChild(work);
    return row;
  }

  // ---------------------------------------------- the four working blocks

  function workRow(host2, term, body) {
    const dt = el("dt", null, term);
    const dd = el("dd");
    if (body instanceof Node) dd.appendChild(body);
    else dd.appendChild(document.createTextNode(String(body)));
    host2.append(dt, dd);
  }
  function workList() { return el("dl", "dl-worklist"); }

  const solverBox = el("div", "sv-box");

  function workFor(ln, box) {
    const dl2 = workList();
    const n = or(ln.numbers, {});
    if (ln.question === "transfer") {
      const hdr = or(brief && brief.header, null);
      if (hdr && hdr.free_transfers != null) {
        const stale = hdr.free_transfers_state === "stale"
          || hdr.free_transfers_state === "missing";
        const ftTxt = pick(stale, "?", String(hdr.free_transfers));
        const ft = el("span", null, `${ftTxt} free transfer(s)`
          + when(stale, `, the plan's count is ${hdr.free_transfers_state}`));
        ft.title = "from the solver plan"
          + when(hdr.free_transfers_as_of, ` · read ${hdr.free_transfers_as_of}`);
        workRow(dl2, "budget", ft);
      }
      if (hdr && hdr.bank_tenths != null)
        workRow(dl2, "bank", fmtPrice(hdr.bank_tenths / 10));
      if (brief && brief.moves_suppressed > 0)
        workRow(dl2, "capped", `${brief.moves_suppressed} more cleared the `
          + `gates, suppressed at the served cap of `
          + `${or(thr.move_cap, "an unserved number")}`);
      if (thr.solve_fresh_window_h != null)
        workRow(dl2, "fresh", `a plan counts as fresh inside `
          + `${thr.solve_fresh_window_h} hours of the solve, which is the `
          + `brief's own solve_fresh_window_h`);
      box.appendChild(dl2);
      box.appendChild(solverBox);
      renderSolver();
      renderRuleMoves(box);
      return;
    }
    if (ln.question === "captain") {
      if (bestXi && or(bestXi.captain_candidates, []).length)
        box.appendChild(captainBlock(lockedCaptain()));
      if (n.your_captain_code != null) {
        const mine = playerIndex.get(n.your_captain_code);
        workRow(dl2, "your armband",
          or(mine && mine.name, String(n.your_captain_code)));
      }
      for (const c of or(bestXi && bestXi.captain_candidates, [])) {
        const m = minutesBy.get(c.player.code);
        if (!m) continue;
        if (m.xmins != null) {
          workRow(dl2, "minutes", `${c.player.name} ${fmt1(m.xmins)} expected `
            + `minutes, ${or(m.n_sources, "an unreported number of")} source(s)`);
        } else if (m.p_appear != null) {
          workRow(dl2, "minutes", `${c.player.name} `
            + `${Math.round(m.p_appear * 100)}% to appear, `
            + `${or(m.n_sources, "an unreported number of")} source(s)`);
        }
      }
      for (const d of or(ln.dissent, [])) {
        const txt = dissentText(d);
        if (txt == null) continue;
        workRow(dl2, "dissent", `${txt}. ${d.source_panel}, `
          + `${fmtAgeDays(d.source_as_of)}. Its own measure, printed, never `
          + `summed into the pick.`);
      }
      // the exact instant stays in the title; an ISO stamp in body text is
      // the thing the age vocabulary exists to replace
      const xsrc = el("span", null,
        `${or(noDash(brief?.xpts_source), "not served")}`
        + when(brief?.xpts_as_of, `. Read ${fmtAgeDays(brief?.xpts_as_of)}.`));
      xsrc.title = `projection_table as of ${or(brief?.xpts_as_of, "unknown")}`;
      workRow(dl2, "xPts source", xsrc);
      if (!haulFresh)
        workRow(dl2, "haul odds", `${haulUnavailable}. `
          + `${or(noDash(brief?.p_haul_source), "")}`);
      if (thr.captain_close_call_xpts != null)
        workRow(dl2, "gate", `captain_close_call_xpts `
          + `${thr.captain_close_call_xpts}`);
      box.appendChild(dl2);
      return;
    }
    if (ln.question === "bench") {
      if (bestXi) {
        if (bestXi.formation) workRow(dl2, "formation", bestXi.formation);
        if (bestXi.xi_xpts != null)
          workRow(dl2, "best XI", `Σ ${fmt1(bestXi.xi_xpts)} xPts, `
            + `the captain counted once`);
        if (bestXi.reason) workRow(dl2, "note", bestXi.reason);
        // one row per direction, not one per player: the same six names as
        // six terms read as six separate findings
        const ndf = or(bestXi.n_differs, 0);
        const ds = or(bestXi.differs, []);
        const named = p2 => `${p2.name} (${or(p2.pos, "position unknown")})`;
        if (ndf)
          workRow(dl2, "you start, the best XI benches",
            ds.slice(0, ndf).map(named).join(", "));
        if (ds.length > ndf)
          workRow(dl2, "you bench, the best XI starts",
            ds.slice(ndf).map(named).join(", "));
      }
      if (sq && sq.projected_xi_xpts != null) {
        const xi = el("span", null,
          `Σ ${fmt1(sq.projected_xi_xpts)} xPts over your locked XI`);
        xi.title = "the captain is counted once here; the Planner grid "
          + "doubles the armband and lands higher";
        workRow(dl2, "your XI", xi);
      }
      if (median != null)
        workRow(dl2, "XI median", `${fmt1(median)} xPts per starter, the `
          + `anchor the lineup's numbers are read against`);
      if (thr.bench_margin_xpts != null)
        workRow(dl2, "gate", `bench_margin_xpts ${thr.bench_margin_xpts}`);
      const link = el("button", "chip", "show it in the lineup");
      link.type = "button";
      link.onclick = () => scrollToCard(pitchCard);
      workRow(dl2, "evidence", link);
      box.appendChild(dl2);
      return;
    }
    // chip
    const hdr = or(brief && brief.header, null);
    if (hdr && hdr.chip_rule) workRow(dl2, "rule", hdr.chip_rule);
    if (hdr && hdr.chip_state) workRow(dl2, "read under", hdr.chip_state);
    const gwNow = or(brief && brief.gw, null);
    for (const c of or(sq && sq.chips, [])) {
      const short = or(CHIP_SHORT[c.chip], String(c.chip).toUpperCase());
      const total = or(c.windows, []).length;
      const left = Math.max(0, total - or(c.played, []).length);
      const usedNow = gwNow != null && or(c.windows, []).some(([lo, hi]) =>
        gwNow >= lo && gwNow <= hi
        && or(c.played, []).some(g => g >= lo && g <= hi));
      const line = el("span", null, `${left} of ${total} left`
        + when(or(c.played, []).length, `, used GW${or(c.played, []).join(", GW")}`)
        + when(usedNow, ", spent in this window"));
      line.title = or(c.windows, []).map(([lo, hi]) => {
        const playedIn = or(c.played, []).filter(g => g >= lo && g <= hi);
        return `GW${lo} to GW${hi}: `
          + pick(playedIn.length, "used GW" + playedIn.join(", GW"), "available");
      }).join(" · ");
      workRow(dl2, short, line);
    }
    if (!or(sq && sq.chips, []).length && sq)
      workRow(dl2, "chips", "this squad source serves no chip ledger");
    box.appendChild(dl2);
  }

  function lockedCaptain() {
    return or(squad15.find(x => x.is_captain), null);
  }

  // The collision cross-reference. Returns the clause for the LOSING row.
  function collisionFor(ln) {
    const lines = or(brief?.verdict?.lines, []);
    const mine = codesOf(ln);
    if (!mine.size) return "";
    const rank = DECISION_ORDER.indexOf(ln.question);
    for (const other of lines) {
      const r2 = DECISION_ORDER.indexOf(other.question);
      if (r2 < 0 || r2 >= rank) continue;
      for (const c of codesOf(other)) {
        if (!mine.has(c)) continue;
        const who = or(playerIndex.get(c)?.name, String(c));
        return `. ${who} is also named by the ${other.question} row above, `
          + `which this page settles first`;
      }
    }
    return "";
  }
  function codesOf(ln) {
    const out = new Set();
    if (ln.pick?.code != null) out.add(ln.pick.code);
    for (const mv of or(ln.moves, [])) {
      if (mv.out?.code != null) out.add(mv.out.code);
      if (mv.in?.code != null) out.add(mv.in.code);
    }
    if (ln.question === "captain" && bestXi?.captain?.code != null)
      out.add(bestXi.captain.code);
    if (ln.question === "bench")
      for (const d of or(bestXi && bestXi.differs, []))
        if (d.code != null) out.add(d.code);
    return out;
  }

  function renderVerdict() {
    verdictCard.textContent = "";
    rowByQuestion.clear();
    if (!brief) {
      verdictCard.appendChild(namedGap("No answers to assemble.",
        "dashboard_brief is unavailable; every row on this page rides in it."));
      return;
    }
    const V = brief.verdict;
    if (!V || !or(V.lines, []).length) {
      verdictCard.appendChild(namedGap("No verdict served.",
        "the brief carries no verdict block; a backend gap, not a "
        + "quiet day."));
      return;
    }
    for (const ln of V.lines) verdictCard.appendChild(verdictRow(ln));
    // the precedence, verbatim from the payload, behind a small disclosure
    const det = el("details", "vd-how");
    det.appendChild(el("summary", null, "how ties break"));
    det.appendChild(el("p", "vd-prec", V.precedence));
    verdictCard.appendChild(det);
  }

  // -------------------- blocker-row wording (kept nested for the tests)
  function claimFor(a) {
    /* Wording keyed by rule id: the brief carries numbers, never prose.
       Bench order and captaincy have no row here on purpose: the ledger
       answers those two questions directly. */
    const frag = document.createDocumentFragment();
    const P = i => or(or(a.players, [])[i],
                      { name: String(or(or(a.codes, [])[i], "an unnamed player")) });
    const n = or(a.numbers, {});
    const co = (txt) => el("code", null, txt);
    const add = (...parts) => parts.forEach(x =>
      frag.appendChild(pick(typeof x === "string",
                            document.createTextNode(String(x)), x)));
    switch (a.rule) {
      case "availability":
        add(`${P(0).name} is flagged `, co(String(or(a.status, "unstated"))));
        if (a.news) add(`; FPL says: `, el("q", null, a.news));
        break;
      case "own_price_fall": {
        add(`${P(0).name} `, co(fmtSigned(n.net_per_hour) + "/hr"),
            ` in the ${hoursWindow(n.window_h)} (net `,
            co(fmtSigned(n.net)), `); price watch, observed flow.`);
        // A short window read long ago is a closed observation, not a live
        // rate. Saying so stops a 34 minute snapshot from yesterday being
        // read as what transfers are doing now.
        let snapDays = null;
        if (a.source_as_of) snapDays = agoPhrase(a.source_as_of);
        if (snapDays && snapDays !== "today")
          add(` The window closed ${snapDays}; this is not the `
              + `current rate.`);
        break;
      }
      case "solve_stale":
        add("solver: plan predates the deadline. ", or(a.reason, ""));
        break;
      case "solve_missing":
        add("solver: ", or(a.reason, "no stored solve for this season."));
        break;
      case "source_gap":
        add(`${a.source_panel} answered nothing: `,
            or(a.reason, "no reason given"));
        break;
      default:
        add(a.rule, " ", JSON.stringify(n));
    }
    return frag;
  }
  function alertRow(a) {
    const row = el("div", "al wl p" + a.priority);
    row.appendChild(el("b", "al-kind", a.kind));
    if (or(a.codes, []).length) row.appendChild(tinyFace(a.codes[0]));
    const claim = el("span", "al-claim");
    claim.appendChild(claimFor(a));
    row.appendChild(claim);
    const cite = citeChip(a.source_panel, a.source_as_of);
    cite.onclick = () => drillTo(a.drill);
    row.appendChild(cite);
    if (a.drill) {
      row.classList.add("drillable");
      row.onclick = (e) => { if (e.target !== cite) drillTo(a.drill); };
    }
    return row;
  }

  // ------------------------------------------------------------- lineup
  // per-render context read by pcard (which only ever reads squad fields
  // off its player argument)
  let capCodeCur = null;
  let viceCodeCur = null;
  const tagByCode = new Map();     // code -> {tag, question, extra[]}

  function riskOf(status) {
    if (!status || status === "a") return null;
    if (status === "d") return { letter: "d", cls: "d" };
    if (status === "i") return { letter: "i", cls: "i" };
    if (status === "s") return { letter: "s", cls: "s" };
    return { letter: status, cls: "i" };
  }
  function oppChip(teamCode, pos) {
    const tf = teamFix.get(teamCode);
    const chip = el("span", "pp-chip pp-opp");
    if (!tf || !tf.next) {
      chip.appendChild(document.createTextNode("–"));
      chip.appendChild(el("i", null, "opp"));
      chip.title = pick(tf,
        "blank gameweek; no fixture in the next round",
        "no fixture data served for this club");
      return chip;
    }
    const nx = tf.next;
    const defensive = pos === "GKP" || pos === "DEF";
    const ease = pick(defensive, nx.defence_ease, nx.attack_ease);
    const kls = easeClass(ease);
    if (kls) chip.classList.add(kls);
    chip.appendChild(document.createTextNode(nx.label));
    chip.appendChild(el("i", null, pick(nx.is_home, "H", "A")));
    let easeTxt = or(nx.unavailable, "no fitted rating for this fixture");
    if (ease != null)
      easeTxt = `${pick(defensive, "defence", "attack")} ease `
        + `${fmtSigned(ease, 2)} goals vs league average `
        + `(opponent-only lens, fixture_board)`;
    chip.title = `${nx.opponent} (${pick(nx.is_home, "home", "away")}); `
      + easeTxt
      + when(nx.attack_rank != null,
             ` · ranks: attack ${nx.attack_rank}, defence ${nx.defence_rank} `
             + `(1 = easiest)`);
    return chip;
  }
  function minChip(code) {
    const m = minutesBy.get(code);
    const chip = el("span", "pp-chip pp-min");
    if (m && m.xmins != null) {
      chip.appendChild(document.createTextNode(String(Math.round(m.xmins))));
      chip.appendChild(el("i", null, "xmin"));
      chip.title = `${fmt1(m.xmins)} expected minutes; provider consensus `
        + `(${or(m.n_sources, "an unreported number of")} source(s))`;
    } else if (m && m.p_appear != null) {
      chip.appendChild(document.createTextNode(
        `${Math.round(m.p_appear * 100)}%`));
      chip.appendChild(el("i", null, "appear"));
      chip.title = `${Math.round(m.p_appear * 100)}% to appear; provider `
        + `consensus (${or(m.n_sources, "an unreported number of")} source(s)); `
        + `no provider serves expected minutes for this gameweek, so none `
        + `are shown`;
    } else {
      chip.appendChild(document.createTextNode("–"));
      chip.appendChild(el("i", null, "min"));
      chip.title = "no provider minutes column for this player this gameweek";
    }
    return chip;
  }
  function pcard(p) {
    const isCap = capCodeCur != null && p.code === capCodeCur;
    const isVice = viceCodeCur != null && p.code === viceCodeCur;
    const b = el("button", "pp" + when(isCap, " cap"));
    b.type = "button";
    if (p.team_code != null) b.dataset.club = String(p.team_code);
    let xLabel = "no projection";
    if (p.xpts != null) xLabel = fmt1(p.xpts) + " expected points";
    b.setAttribute("aria-label",
      `${p.name}, ${p.pos}, ${fmtPrice(p.price)}, ${xLabel}`);

    const risk = riskOf(p.status);
    if (risk) {
      const r = el("span", "pp-risk " + risk.cls, risk.letter);
      r.title = pick(p.news, `${p.news} `, `status ${p.status} `) + "(FPL)";
      b.appendChild(r);
    }
    const drop = dropByCode.get(p.code);
    if (drop) {
      // price-fall risk: the own_price_fall alert's numbers, on the card
      const dEl = el("span", "pp-drop" + when(risk, " shift"), "↓");
      dEl.title = `price-fall risk: net ${fmtSigned(drop.net)} in the `
        + `${hoursWindow(drop.window_h)} (${fmtSigned(drop.net_per_hour)}/hr)`
        + `; observed flow, not a predicted change`;
      b.appendChild(dEl);
    }
    if (isCap || isVice)
      b.appendChild(el("span", "ribbon" + when(isVice && !isCap, " v"),
                       pick(isCap, "C", "V")));

    const img = el("img", "pp-face");
    img.alt = ""; img.loading = "lazy"; img.decoding = "async";
    img.src = PHOTO(p.code);
    // clubmark discipline: one class flip, monogram underneath, zero reflow
    img.addEventListener("error", () => b.classList.add("fall"));
    b.appendChild(img);
    const letters = String(or(p.name, "")).replace(/[^A-Za-zÀ-ž]/g, "")
      .slice(0, 2).toUpperCase();
    const mg = el("span", "pp-mg", pick(letters.length, letters, "FPL"));
    mg.setAttribute("aria-hidden", "true");
    b.appendChild(mg);

    b.appendChild(el("span", "pp-nm", p.name));

    // the decision tag, when one of the four rows named this player
    const dec = tagByCode.get(p.code);
    if (dec) {
      const tag = el("span", "pp-tag", dec.tag);
      tag.title = `the ${dec.question} row names this player`
        + when(dec.extra.length,
               `. Also named by: ${dec.extra.join(", ")}. This page settles `
               + `${DECISION_ORDER.join(" over ")}.`);
      b.appendChild(tag);
      b.classList.add("tagged");
      if (dec.extra.length) b.appendChild(el("span", "pp-more",
        String(dec.extra.length)));
    }

    // ONE info row, three chips: xPts · opponent · minutes
    const rowEl = el("span", "pp-row");
    const xchip = el("span", "pp-chip pp-x");
    xchip.appendChild(document.createTextNode(
      pick(p.xpts != null, fmt1(p.xpts), "–")));
    xchip.appendChild(el("i", null, "xPts"));
    xchip.title = pick(p.xpts != null && median != null,
      `${fmt2(p.xpts)} consensus xPts vs XI median ${fmt2(median)}`,
      "no cached projection");
    rowEl.appendChild(xchip);
    rowEl.appendChild(oppChip(p.team_code, p.pos));
    rowEl.appendChild(minChip(p.code));
    b.appendChild(rowEl);

    b.onclick = () => openDrawer(p.code);
    pitchCardByCode.set(p.code, b);
    return b;
  }
  function oppText(teamCode) {
    // opponent + venue from fixture_board's opponent_only lens, the same
    // source the lineup's opponent chip reads
    const tf = teamFix.get(teamCode);
    if (!tf) return "(no fixture data)";
    if (!tf.next) return "(blank GW)";
    return `${tf.next.opponent} (${pick(tf.next.is_home, "H", "A")})`;
  }
  function captainBlock(lockedCap) {
    // the top three of the best XI by consensus xPts, each with its number
    // AND its opponent; a lead under the served gate is a close call
    const cands = or(bestXi.captain_candidates, []);
    const c0 = cands[0], c1 = cands[1];
    const box = el("div", "db-capblock");
    const lead = el("p", "db-armband");
    if (bestXi.close_call && c1) {
      lead.append(el("b", null, "close call"),
        document.createTextNode(`; ${c0.player.name} ${fmt1(c0.xpts)} vs `
          + `${c1.player.name} ${fmt1(c1.xpts)} consensus xPts, lead `
          + `${fmt1(bestXi.captain_lead_xpts)} under the `
          + `${or(thr.captain_close_call_xpts, "unserved")} gate`));
    } else {
      let clear = "";
      if (c1 && bestXi.captain_lead_xpts != null)
        clear = `, ${fmt1(bestXi.captain_lead_xpts)} clear of ${c1.player.name}`;
      lead.append(el("b", null, c0.player.name),
        document.createTextNode(`; ${fmt1(c0.xpts)} consensus xPts${clear}`));
    }
    box.appendChild(lead);
    const tbl = el("table", "data db-captbl");
    const hd = el("tr");
    hd.append(el("th", null, "candidate"), el("th", "num", "consensus xPts"),
              el("th", null, "opponent"));
    if (haulFresh) {
      const haulTh = el("th", "num", "haul odds");
      haulTh.title = or(brief?.p_haul_source, "engine simulation")
        + when(shortDate(brief?.p_haul_generated),
               `, simulated ${shortDate(brief?.p_haul_generated)}`);
      hd.appendChild(haulTh);
    }
    const thd = el("thead"); thd.appendChild(hd); tbl.appendChild(thd);
    const tb = el("tbody");
    for (const c of cands) {
      const tr = el("tr");
      const who = el("td");
      who.appendChild(tinyFace(c.player.code));
      who.appendChild(document.createTextNode(c.player.name
        + when(lockedCap && lockedCap.code === c.player.code,
               " (your locked armband)")));
      tr.appendChild(who);
      tr.appendChild(el("td", "num", pick(c.xpts != null, fmt1(c.xpts), "–")));
      tr.appendChild(el("td", null, oppText(c.player.team_code)));
      if (haulFresh)
        tr.appendChild(el("td", "num",
          pick(c.p_haul != null, `${Math.round(c.p_haul * 100)}%`, "–")));
      tb.appendChild(tr);
    }
    tbl.appendChild(tb);
    box.appendChild(tbl);
    return box;
  }

  // The tags the lineup carries, built from the four rows, one per player,
  // highest-precedence decision visible and the rest counted.
  function buildTags() {
    tagByCode.clear();
    const claim = (code, tag, question) => {
      if (code == null) return;
      const cur = tagByCode.get(code);
      if (!cur) { tagByCode.set(code, { tag, question, extra: [] }); return; }
      cur.extra.push(`${question} (${tag})`);
    };
    for (const ln of or(brief?.verdict?.lines, [])) {
      if (ln.question === "transfer") {
        for (const mv of or(ln.moves, [])) {
          claim(mv.out?.code, "SELL", "transfer");
          claim(mv.in?.code, "BUY", "transfer");
        }
      } else if (ln.question === "captain") {
        claim(or(ln.pick?.code, bestXi?.captain?.code), "C", "captain");
        if (ln.numbers?.your_captain_code != null
            && ln.numbers.your_captain_code !== or(ln.pick?.code, null))
          claim(ln.numbers.your_captain_code, "your C", "captain");
      } else if (ln.question === "bench") {
        const nd = or(bestXi && bestXi.n_differs, 0);
        const ds = or(bestXi && bestXi.differs, []);
        ds.slice(0, nd).forEach(d => claim(d.code, "SIT", "bench"));
        ds.slice(nd).forEach(d => claim(d.code, "START", "bench"));
      } else if (ln.question === "chip" && ln.chip) {
        for (const c of or(bestXi && bestXi.bench_codes, []))
          claim(c, or(CHIP_SHORT[ln.chip], String(ln.chip)), "chip");
      }
    }
  }

  function renderPitch() {
    pitchBody.textContent = "";
    pitchCardByCode.clear();
    buildTags();
    if (!squad15.length) {
      let why = "empty";
      if (sqR.ok) why = String(or(sqR.result.reason, "empty"));
      else why = String(or(sqR.error && sqR.error.message, sqR.error));
      pitchBody.appendChild(namedGap("No squad to draw.", why));
      return;
    }
    const byCode = new Map(squad15.map(x => [x.code, x]));
    const lockedCap = lockedCaptain();
    // ONE eleven: the brief's best formation-legal XI by consensus xPts.
    // Without it the locked picks draw, labelled as the fallback they are.
    const usable = !!(bestXi && or(bestXi.xi_codes, []).length === 11);
    let xiCodes = squad15.filter(x => x.is_starter).map(x => x.code);
    if (usable) xiCodes = bestXi.xi_codes;
    // FPL's bench convention: the goalkeeper is ALWAYS slot 1, then the
    // three outfielders. The best XI's outfielders rank by consensus xPts;
    // the locked fallback keeps the manager's own outfield order.
    const benchOrder = (codes, byXpts) => {
      const ps = codes.map(c => byCode.get(c)).filter(Boolean);
      const gk = ps.filter(p => p.pos === "GKP");
      const out = ps.filter(p => p.pos !== "GKP");
      if (byXpts) out.sort((a, b) => or(b.xpts, -1) - or(a.xpts, -1));
      return [...gk, ...out].map(p => p.code);
    };
    let benchCodes = benchOrder(
      squad15.filter(x => !x.is_starter).map(x => x.code), false);
    if (usable) benchCodes = benchOrder(bestXi.bench_codes, true);
    capCodeCur = or(lockedCap && lockedCap.code, null);
    if (usable && bestXi.captain) capCodeCur = bestXi.captain.code;
    viceCodeCur = null;

    const head = el("div", "db-pitchhead");
    if (usable) {
      const lead = el("span", "db-pitchdelta",
        "best XI by consensus xPts"
        + when(bestXi.formation, ` · ${bestXi.formation}`)
        + when(bestXi.xi_xpts != null, ` · Σ ${fmt1(bestXi.xi_xpts)} xPts`));
      lead.title = `${bestXi.source_panel}`
        + when(bestXi.source_as_of, ` as of ${bestXi.source_as_of}`)
        + when(brief?.xpts_source, `; ${brief?.xpts_source}`);
      head.appendChild(lead);
      const nd = or(bestXi.n_differs, 0);
      const out = or(bestXi.differs, []).slice(0, nd).map(d => d.name);
      const inn = or(bestXi.differs, []).slice(nd).map(d => d.name);
      const diff = el("span", "chip" + pick(nd, " warn", " s1"),
        pick(nd, `your locked picks differ by ${nd}`,
             "your locked XI is this XI"));
      diff.title = pick(nd,
        `locked but benched here: ${or(out.join(", "), "none")}; `
        + `benched by you, starting here: ${or(inn.join(", "), "none")}`,
        "the locked picks and the best XI are the same eleven");
      head.appendChild(diff);
    } else {
      head.appendChild(el("span", "db-pitchdelta",
        "your locked picks (no best XI served)"));
      const why = el("span", "chip warn",
        or(bestXi && bestXi.reason, "dashboard_brief served no best_xi block"));
      head.appendChild(why);
    }
    pitchBody.appendChild(head);

    const pitch = el("div", "pitch db-pitch2");
    const byPos = { GKP: [], DEF: [], MID: [], FWD: [] };
    for (const c of xiCodes) {
      const pl = byCode.get(c);
      if (pl) or(byPos[pl.pos], byPos.MID).push(pl);
    }
    for (const pos of ["GKP", "DEF", "MID", "FWD"]) {
      if (!byPos[pos].length) continue;
      const rowEl = el("div", "row");
      byPos[pos].forEach(pl => rowEl.appendChild(pcard(pl)));
      pitch.appendChild(rowEl);
    }
    pitchBody.appendChild(pitch);

    // the bench: a visually distinct tray below the pitch; GK first, then
    // consensus xPts order
    const tray = el("div", "bench db-benchtray");
    tray.appendChild(el("span", "db-benchlbl", "bench"));
    benchCodes.forEach((c, i) => {
      const pl = byCode.get(c);
      if (!pl) return;
      const slot = el("span", "bn-slot");
      slot.appendChild(el("i", "bn-i", String(i + 1)));
      slot.appendChild(pcard(pl));
      tray.appendChild(slot);
    });
    pitchBody.appendChild(tray);

    const footLine = el("p", "sub");
    let src = "squad_overview";
    if (squadFromBrief) src = "dashboard_brief.squad";
    footLine.title = `squad_overview reports ${or(sq && sq.provenance_source,
      "no provenance source")}; the block above the answers names it once`;
    footLine.textContent = `source: ${src}`
      + when(median != null, ` · XI median ${fmt2(median)} xPts per starter`)
      + when(easeDom != null,
             ` · opponent chip colour = fixture ease, the fixtures tab's ramp`)
      + `. Badges: C captain, ↓ price-fall risk from observed transfer flow, `
      + `a letter for an FPL availability flag. Hover any badge for its `
      + `numbers.`;
    pitchBody.appendChild(footLine);
    for (const note of or(sq && sq.notes, []))
      pitchBody.appendChild(el("p", "sub", note));
  }

  // ------------------------------- the rule moves, in the transfer working
  function moveFace(ref, side) {
    const wrap = el("span", "sv-side " + side);
    const img = el("img", "sv-face");
    img.alt = ""; img.loading = "lazy";
    img.src = PHOTO(ref.code);
    img.addEventListener("error", () => wrap.classList.add("fall"));
    wrap.appendChild(img);
    const mg = el("span", "sv-mg",
      String(or(ref.name, "")).replace(/[^A-Za-zÀ-ž]/g, "").slice(0, 2)
        .toUpperCase());
    if (ref.team_code != null) wrap.dataset.club = String(ref.team_code);
    wrap.appendChild(mg);
    wrap.appendChild(el("b", null, ref.name));
    if (ref.price != null) wrap.appendChild(el("i", null, fmtPrice(ref.price)));
    return wrap;
  }
  function gwsText(gws) {
    if (!gws || !gws.length) return "an unstated window";
    if (gws.length > 1) return `${gws[0]} to ${gws[gws.length - 1]}`;
    return String(gws[0]);
  }
  function moveSentence(mv) {
    /* Fixed templates keyed by rule id; every number is the payload's. */
    const n = or(mv.numbers, {});
    if (mv.rule === "coverage_gap") {
      return `${mv.team} has the #${n.attack_rank} easiest attacking run and `
        + `you hold ${n.held_count}. ${mv.in.name}; ${fmt1(n.cand_xpts)} xPts `
        + `GW${n.next_gw}, ${n.cand_goals}G+${n.cand_assists}A in `
        + `GW${gwsText(mv.gws)}; fits for ${mv.out.name} `
        + `(${fmt1(n.out_xpts)} xPts).`;
    }
    if (mv.rule === "form_upgrade") {
      return `${mv.in.name} beats ${mv.out.name} on both gates: `
        + `${n.cand_returns} vs ${n.out_returns} returns in `
        + `GW${gwsText(mv.gws)} (gate ≥ +${or(thr.form_returns_margin, "unserved")}) `
        + `and ${fmt1(n.cand_xpts)} vs ${fmt1(n.out_xpts)} xPts `
        + `GW${n.next_gw} (gate ≥ +${fmt1(thr.form_xpts_margin)}).`;
    }
    return `${mv.rule}: ${mv.out.name} to ${mv.in.name}`;
  }
  function moveEl(mv) {
    const n = or(mv.numbers, {});
    const box = el("div", "db-move");
    const strip = el("span", "sv-strip");
    strip.append(moveFace(mv.out, "out"),
                 el("span", "sv-arrow", "→"),
                 moveFace(mv.in, "in"));
    box.appendChild(strip);
    box.appendChild(el("p", "db-movewhy", moveSentence(mv)));
    if (n.in_price != null && n.out_price != null) {
      const spare = n.out_price + or(n.bank, 0) - n.in_price;
      box.appendChild(el("p", "db-movemath",
        `${fmtPrice(n.out_price)} sale + ${fmtPrice(or(n.bank, 0))} bank covers `
        + `${fmtPrice(n.in_price)}`
        + when(spare >= 0, ` (${fmtPrice(spare)} spare)`)));
    }
    const meta = el("span", "t-meta");
    for (const s of or(mv.sources, []))
      meta.appendChild(citeChip(s.panel, s.as_of));
    meta.appendChild(el("span", "t-gate", "rule-based; not the solver"));
    box.appendChild(meta);
    box.onclick = () => drillTo(mv.drill);
    box.classList.add("drillable");
    return box;
  }
  function renderRuleMoves(box) {
    const list = or(brief && brief.moves, []);
    if (!list.length) {
      const gapNote = or(brief && brief.empty_kinds, [])
        .find(e => e.kind === "moves");
      box.appendChild(el("p", "db-quiet",
        pick(gapNote, `No rule move cards: ${gapNote && gapNote.reason}`,
             "No candidate cleared the coverage or form gates today.")));
      return;
    }
    for (const mv of list) box.appendChild(moveEl(mv));
  }

  // ------------------------------------------------------------- solver
  let solveKicked = false;
  let solvePollTimer = null;
  let solveTickerEl = null;
  let tplanPromise = null;   // /api/solve/transfer-plan, fetched once per view

  /* The optimiser's top move when the hit cap displaced it, one line beside
     the headline. Read from the artefact itself via /api/solve/transfer-plan
     (the brief's plan block carries no field for it); fetched once, appended
     only when the plan is standing and the move differs from the headline. */
  function unconstrainedLine(target, plan) {
    tplanPromise = or(tplanPromise,
      getJSON("/api/solve/transfer-plan").catch(() => null));
    tplanPromise.then(tp => {
      if (!tp || !tp.exists || tp.stale || !target.isConnected) return;
      const u = tp.plan.unconstrained, c = or(tp.plan.chosen, {});
      if (!u) return;
      const same = arr => [...or(arr, [])].map(Number).sort((a, b) => a - b)
        .join(",");
      if (same(u.out) === same(c.out) && same(u.in) === same(c.in)) return;
      const line = el("p", "sv-lines sv-uncon");
      line.appendChild(document.createTextNode(
        `if hits were free: ${u.n_transfers} changes, ${u.hits} hits, `
        + `${fmtSigned(u.gain_over_roll, 1)} xPts vs rolling, ${fcName(plan)}; `));
      const a = el("a", null, "see Planner");
      a.href = "#planner";
      line.appendChild(a);
      line.title = "the optimiser's top move when the headline was held to the "
        + "hit cap; the Planner tab draws it into the grid with one click";
      target.appendChild(line);
    });
  }
  function lastLogLine() {
    const t = or(solveStatus?.log_tail, []);
    for (let i = t.length - 1; i >= 0; i--) {
      const s = String(t[i]).trim();
      if (s) return s;
    }
    return "";
  }
  function startSolvePolling() {
    if (solvePollTimer) return;
    solvePollTimer = setInterval(async () => {
      let st;
      try { st = await getJSON("/api/solve/status"); }
      catch { return; }   // transient poll miss; the next tick retries
      solveStatus = st;
      if (st.state === "running") {
        if (solveTickerEl) solveTickerEl.textContent = lastLogLine();
        return;
      }
      clearInterval(solvePollTimer);
      solvePollTimer = null;
      if (st.state === "done") {
        // the artefact changed on disk: refetch the brief and redraw, since
        // the transfer row, the gaps and the lineup all read the plan
        const r = await tryPanel("dashboard_brief", {});
        if (r.ok && !r.result.empty) brief = r.result;
        readBrief(); reindex(); readDrops();
      }
      renderAll();
    }, 5000);
  }
  function rerunButton(prominent) {
    const b = el("button", "chip" + when(prominent, " sv-rerun"),
      "Re-run solve");
    b.type = "button";
    b.title = "runs a fresh solve against your current 15 and commits a new "
      + "plan. It costs 2 to 5 minutes of compute, which is why it is not the "
      + "Refresh control.";
    b.onclick = async () => {
      b.disabled = true;
      b.textContent = "starting";
      try {
        await postJSON("/api/solve", { mode: "transfers" });
        solveKicked = true;
        solveStatus = { state: "running",
                        started_utc: new Date().toISOString(), log_tail: [] };
        renderSolver();
        startSolvePolling();
      } catch (e) {
        b.disabled = false;
        b.textContent = "Re-run solve";
        b.insertAdjacentElement("afterend", errBox(e));
      }
    };
    return b;
  }
  function solveRunningEl() {
    const box = el("div", "sv-running");
    const line = el("p", "sv-runline");
    line.appendChild(el("span", "sv-spin"));
    line.appendChild(document.createTextNode(
      `Solving, started ${localClock(solveStatus?.started_utc)}, `
      + `typically 2 to 5 minutes`));
    box.appendChild(line);
    solveTickerEl = el("p", "sv-ticker", lastLogLine());
    box.appendChild(solveTickerEl);
    return box;
  }
  /* The transfer row's working block. The plan body renders ONLY under
     fresh/aging: a stale plan's moves, captain and alternatives were priced
     against a squad you no longer have, so the block shows state, reason, age
     and Re-run, and nothing else. This guard is the most valuable rule on the
     page and it moved here with the plan body it protects. */
  function renderSolver() {
    solverBox.textContent = "";
    solveTickerEl = null;
    const S = or(brief && brief.solve, null);
    // The guard, character for character as the contract test pins it. The
    // && form keeps the literal and avoids the house rule on question marks.
    const plan = (S && (S.state === "fresh" || S.state === "aging"))
      && S.plan || null;
    const running = solveStatus?.state === "running";

    const head = el("div", "sv-head");
    head.appendChild(el("b", "sv-lbl", "the solver plan"));
    if (S && S.state !== "missing") {
      let cls = " bad";
      if (S.state === "fresh") cls = " s1";
      if (S.state === "aging") cls = " warn";
      const chip = el("span", "chip" + cls,
        S.state + when(S.age_hours != null,
                       ` · ${daysFromHours(S.age_hours)} old`));
      chip.title = pick(S.generated_at,
        `plan generated ${S.generated_at}`, "no generated_at on the plan");
      head.appendChild(chip);
    }
    solverBox.appendChild(head);

    if (!brief) {
      solverBox.appendChild(namedGap("Solve state unknowable.",
        "dashboard_brief unavailable; the solve block rides in it."));
    } else if (S.state === "missing") {
      solverBox.appendChild(namedGap("No transfer plan artefact.",
        or(S.reason, "no stored solve for this season.")));
    } else if (S.state === "stale" || S.state === "superseded") {
      // honest: a stale plan's moves were priced against a squad you no
      // longer have, and the brief serves no plan body for it, on purpose
      solverBox.appendChild(el("p", "sv-stale",
        or(noDash(S.reason), "this plan solved for a deadline that has passed.")
        + when(S.generated_at,
               ` Written ${shortDate(S.generated_at)}, `
               + `${agoPhrase(S.generated_at)}.`)));
    } else if (!plan) {
      solverBox.appendChild(namedGap("Plan body absent.",
        `solve state is "${S.state}" but the brief served no plan payload; `
        + `a backend gap, not a quiet day.`));
    } else {
      const h = or(plan.horizon_gws, []);
      const hSpan = `GW${or(h[0], "?")} to GW${or(h[h.length - 1], "?")}`;

      if (plan.is_roll) {
        // banking the transfer IS the recommendation
        const roll = el("div", "sv-roll");
        roll.appendChild(el("b", "sv-rollhead", "Bank the transfer"));
        roll.appendChild(el("p", "sv-why",
          `The solved recommendation over ${hSpan}: no move cleared the bar `
          + `vs rolling`
          + pick(plan.free_transfers != null,
                 `; you carry ${plan.free_transfers} free transfer(s) forward.`,
                 ".")));
        solverBox.appendChild(roll);
      } else if (or(plan.moves, []).length) {
        const box = el("div", "sv-move sv-planmoves");
        for (const mv of plan.moves) {
          const row = el("div", "sv-moverow");
          const strip = el("span", "sv-strip");
          strip.append(moveFace(mv.out, "out"),
                       el("span", "sv-arrow", "→"),
                       moveFace(mv.in, "in"));
          row.appendChild(strip);
          const bits = [];
          if (mv.price_delta != null)
            bits.push(`price ${fmtSigned(mv.price_delta, 1)}`);
          if (mv.out_flow)
            bits.push(`${mv.out.name} flow `
              + `${fmtSigned(mv.out_flow.net_per_hour)}/hr`
              + when(mv.out_flow.window_h != null,
                     ` (${hoursWindow(mv.out_flow.window_h)})`));
          if (mv.in_flow)
            bits.push(`${mv.in.name} flow `
              + `${fmtSigned(mv.in_flow.net_per_hour)}/hr`
              + when(mv.in_flow.window_h != null,
                     ` (${hoursWindow(mv.in_flow.window_h)})`));
          if (bits.length)
            row.appendChild(el("p", "sv-flowline", bits.join(" · ")));
          box.appendChild(row);
        }
        solverBox.appendChild(box);
      } else {
        solverBox.appendChild(el("p", "sub",
          "the plan names no paired moves; the Planner tab has the raw sets."));
      }

      // the gain, in the solver's own currency, labelled as such, with the
      // optimality gap and plan age BESIDE it and never in a fold: a 31.7%
      // gap materially discounts the headline
      if (!plan.is_roll && plan.gain_over_roll != null) {
        const line = el("p", "sv-gainline");
        line.appendChild(el("b", null,
          `${fmtSigned(plan.gain_over_roll, 1)} xPts`));
        line.appendChild(document.createTextNode(
          ` over ${hSpan} vs rolling, ${fcName(plan)}`));
        let gapTxt = "closed within tolerance";
        if (plan.optimality_gap_pct != null)
          gapTxt = `${fmt1(plan.optimality_gap_pct)}% optimality gap`;
        line.appendChild(el("span", "sv-gapline",
          " · " + gapTxt
          + when(plan.age_hours != null,
                 ` · ${daysFromHours(plan.age_hours)} old`)
          + when(plan.solve_seconds != null,
                 ` · solved in ${Math.round(plan.solve_seconds)}s`)
          + when(plan.free_transfers != null,
                 ` · ${plan.free_transfers} free transfer(s)`)));
        line.title = "the solver's own forecast in its own currency ("
          + or(plan.objective_mode, "unnamed")
          + "), never blended with the per-player numbers on the lineup; the "
          + "gap is how far from proven best the solve stopped";
        solverBox.appendChild(line);
      }

      // shown for a roll too: bank it beside "if hits were free" is the trade
      unconstrainedLine(solverBox, plan);

      // hits, only when the plan actually spends points
      if (or(plan.hits, 0) > 0) {
        const v = plan.hit_verdict;
        const line = el("p", "sv-lines");
        line.appendChild(document.createTextNode(
          `hits: ${plan.hits} (−${or(plan.hit_points, plan.hits * 4)} pts)`));
        if (v && v.justified != null) {
          line.appendChild(el("span",
            "chip " + pick(v.justified, "s1", "warn"),
            pick(v.justified, "justified", "not justified")));
          if (v.expected_gain != null && v.breakeven_gain != null)
            line.appendChild(document.createTextNode(
              `; expected ${fmt1(v.expected_gain)} vs breakeven `
              + `${fmt1(v.breakeven_gain)}`));
        }
        solverBox.appendChild(line);
      }

      // the chip, when the plan spends one
      if (plan.chip) {
        const line = el("p", "sv-lines");
        line.appendChild(el("span", "chip s1",
          or(CHIP_NAME[plan.chip], String(plan.chip))));
        line.appendChild(document.createTextNode(
          `, the plan spends it` + when(plan.gw != null, ` in GW${plan.gw}`)));
        solverBox.appendChild(line);
      }

      // the armband, with the avatar; yours printed only when it differs
      if (plan.captain) {
        const line = el("p", "sv-capline");
        line.appendChild(tinyFace(plan.captain.code));
        const differs = plan.your_captain
          && plan.your_captain.code !== plan.captain.code;
        line.appendChild(document.createTextNode(
          `Solver captain: ${plan.captain.name}`
          + when(differs, `; yours: ${plan.your_captain.name}`)));
        solverBox.appendChild(line);
      }

      // the losing alternatives, one collapsed line
      const alts = or(plan.alternatives, []);
      if (alts.length) {
        const det = el("details", "sv-alts");
        det.appendChild(el("summary", null,
          "beat: " + alts.map(a => a.summary).join(" · ")));
        for (const a of alts) {
          det.appendChild(el("p", "sv-altrow",
            a.summary
            + when(a.objective != null,
                   `; ${fmt1(a.objective)} ${or(plan.objective_mode, "")}`
                   + ` (solver currency)`)
            + when(a.hits, ` · ${a.hits} hit(s)`)));
        }
        solverBox.appendChild(det);
      }

      // the solver's own confessions
      if (or(plan.notes, []).length || plan.bounds) {
        const det = el("details", "sv-notes");
        det.appendChild(el("summary", null, "solver notes and bounds"));
        for (const nt of or(plan.notes, []))
          det.appendChild(el("p", "mono", nt));
        if (plan.bounds) det.appendChild(el("p", "mono", plan.bounds));
        solverBox.appendChild(det);
      }
    }

    const controls = el("div", "sv-controls");
    // ONE re-solve control on the page. When the plan cannot guide, the
    // blockers strip above owns it as that blocker's named fix; when the plan
    // stands, this is the only place it can live.
    const blockerOwnsRerun = !!(S && S.state !== "fresh" && S.state !== "aging");
    if (running) {
      controls.appendChild(solveRunningEl());
    } else {
      if (!blockerOwnsRerun) {
        controls.appendChild(rerunButton(!S));
        // the cost rides BESIDE the button, not only in its title: a rusher
        // at T-minus-hours must know the click costs minutes
        controls.appendChild(el("span", "db-quiet sv-cost",
          "costs 2 to 5 minutes"));
      }
      // a failed run is reported only when it is newer than the plan that
      // stands; an older failure beside a fresh plan is noise
      const failedAt = parseTs(or(solveStatus?.finished_utc,
                                  solveStatus?.started_utc));
      const planAt = parseTs(S?.generated_at);
      const failedNewer = !!(solveStatus
        && (solveStatus.state === "failed" || solveStatus.state === "error")
        && (!plan || !planAt || (failedAt && failedAt > planAt)));
      if (failedNewer)
        controls.appendChild(el("p", "sv-ticker sv-fail",
          `${pick(solveKicked, "solve", "last solve")} `
          + or(shortDate(or(solveStatus.finished_utc, solveStatus.started_utc)),
               "")
          + " failed: " + noDash(or(lastLogLine(), "no log tail served"))));
      const a = el("a", "chip", "full detail in the Planner tab");
      a.href = "#planner";
      controls.appendChild(a);
    }
    solverBox.appendChild(controls);
    // no accept button: the dashboard argues; the owner decides.
  }

  // -------------------------------------------------------------- tiles
  function tileText(t) {
    /* claim / implication templates keyed by kind; every number is the
       payload's; a missing required arg throws (the tile contract).
       Unknown kinds fall to the default template on purpose: any future
       kind degrades honestly. */
    const name = t.player?.name;
    const ctx = or(t.context, {});
    switch (t.kind) {
      case "xpts_standout":
        return {
          claim: `${name} projects ${fmt1(t.number.value)} over the window; `
            + `${ctx.weakest_starter} holds ${fmt1(ctx.weakest_starter_sum)}.`,
          imp: "→ a same-position upgrade path clears the printed margin.",
        };
      case "template_gap":
        return {
          claim: `${name} is owned by ${fmt1(t.number.value)}% of the game; `
            + `not by you.`,
          imp: "→ an unowned near-universal player is your largest "
            + "single-GW rank risk.",
        };
      case "differential":
        return {
          claim: `${name}: ${fmt1(t.number.value)} xPts next GW at `
            + `${fmt1(ctx.own_pct)}% owned.`,
          imp: `→ clears your XI median (${fmt2(ctx.xi_median)}) with the `
            + `field absent; two chips, two sources.`,
        };
      case "fixture_turn":
        return {
          claim: `${t.team}: ${String(or(ctx.axis, "")).replace("_", " ")} `
            + `moves ${ctx.rank_near} to ${ctx.rank_far} between windows.`,
          imp: "→ the run turns; timing context for moves involving "
            + `${t.team}.`,
        };
      case "price_rise_target":
        return {
          claim: `${name} net ${fmtSigned(t.number.value)}/hr inflow in the `
            + `${hoursWindow(t.number.window_h)}.`,
          imp: "→ a named target's flow is against waiting; flow, not a "
            + "prediction.",
        };
      default:
        return { claim: `${t.kind}: ${fmt1(t.number.value)} ${t.number.unit}`,
                 imp: "" };
    }
  }
  function tileEl(t, showGate) {
    // required-args contract: a tile missing any leg throws rather than
    // rendering a number without its source
    for (const req of ["kind", "number", "gate", "source_panel"]) {
      if (t[req] == null) throw new Error(`tile missing required ${req}`);
    }
    const { claim, imp } = tileText(t);
    const a = el("a", "tile p" + t.priority);
    a.href = "javascript:void 0";
    a.appendChild(el("span", "t-claim", claim));
    const numEl = el("span", "t-num");
    numEl.appendChild(document.createTextNode(
      pick(t.kind === "price_rise_target", fmtSigned(t.number.value),
           fmt1(t.number.value))));
    numEl.appendChild(el("i", null,
      " " + t.number.unit
      + when(t.number.window_h != null, ` · ${hoursWindow(t.number.window_h)}`)));
    a.appendChild(numEl);
    if (imp) a.appendChild(el("span", "t-imp", imp));
    const meta = el("span", "t-meta");
    let srcs = or(t.sources, []);
    if (!srcs.length) srcs = [{ panel: t.source_panel, as_of: t.source_as_of }];
    for (const s of srcs) meta.appendChild(citeChip(s.panel, s.as_of));
    // gate text prints ONCE per gate-type (the first tile of the kind);
    // later tiles of the same kind reference it, full text in the title
    if (showGate) {
      meta.appendChild(el("span", "t-gate", "gate: " + t.gate));
    } else {
      const g = el("span", "t-gate", "same gate as the "
        + String(t.kind).replace(/_/g, " ") + " above");
      g.title = "gate: " + t.gate;
      meta.appendChild(g);
    }
    a.appendChild(meta);
    a.onclick = (e) => { e.preventDefault(); drillTo(t.drill); };
    return a;
  }
  function renderTiles() {
    tilesBody.textContent = "";
    if (!brief) {
      tilesBody.appendChild(namedGap("dashboard_brief unavailable.",
        "No gates were checked; this is a gap, not a quiet day."));
      return;
    }
    const tiles = or(brief.tiles, []);
    if (!tiles.length) {
      tilesBody.appendChild(el("p", "db-quiet",
        "Nothing cleared a gate this morning. The tabs have everything at "
        + "full depth."));
    } else {
      const grid = el("div", "tiles");
      const gateSeen = new Set();
      for (const t of tiles) {
        const first = !gateSeen.has(t.kind);
        gateSeen.add(t.kind);
        grid.appendChild(tileEl(t, first));
      }
      tilesBody.appendChild(grid);
    }
    // own-player price flow: a timing note on a decision already made, so it
    // sits under the gates rather than beside the answers
    const flows = or(brief.alerts, []).filter(a => a.rule === "own_price_fall");
    if (flows.length) {
      const box = el("div", "db-watchlist");
      box.appendChild(el("span", "db-chiplbl", "price flow on players you own"));
      for (const a of flows) box.appendChild(alertRow(a));
      tilesBody.appendChild(box);
    }
  }

  // ---------------------------------------------------------- watch log
  function renderWatch() {
    watchBody.textContent = "";
    if (!brief) {
      watchBody.appendChild(namedGap("The watch did not stand.",
        "dashboard_brief unavailable; no check ran, which is different "
        + "from every check coming back clear."));
      return;
    }
    const rows = or(brief.watch_log, []);
    const counts = { clear: 0, firing: 0, gap: 0 };
    for (const w of rows) counts[w.status] = or(counts[w.status], 0) + 1;

    // ONE meta-status line for the whole page: suppression + checks + the
    // oldest clock, collapsed together; the breakdown lives behind the click
    const sup = or(brief.suppressed_counts, {});
    const supN = Object.values(sup).reduce((a, b) => a + b, 0);
    const det = el("details", "db-watchfold");
    const sum = el("summary", null,
      when(supN, `+${supN} cleared gates suppressed · `)
      + `${rows.length} checks · ${counts.clear} clear`
      + when(counts.firing, ` · ${counts.firing} firing`)
      + when(counts.gap, ` · ${counts.gap} gap`));
    det.appendChild(sum);
    if (supN > 0)
      det.appendChild(el("p", "sub",
        `suppressed beyond the tile cap: ${Object.entries(sup)
          .map(([k, v]) => `${v} ${k}`).join(", ")}; the tabs have `
        + `everything.`));

    const tbl = el("table", "data db-watch");
    const tb = el("tbody");
    for (const w of rows) {
      const tr = el("tr", "w-" + w.status);
      tr.appendChild(el("td", "w-check", w.check.replace(/_/g, " ")));
      const st = el("td", "w-status");
      let cls = "";
      if (w.status === "firing") cls = "s1";
      if (w.status === "gap") cls = "warn";
      st.appendChild(el("span", "chip " + cls,
        pick(w.status === "gap", "GAP", w.status)));
      tr.appendChild(st);
      tr.appendChild(el("td", "w-detail", w.detail));
      tr.appendChild(el("td", "w-src", w.source_panel));
      const ts = el("td", "w-asof num", fmtAgeDays(w.as_of));
      if (w.as_of) ts.title = w.as_of;
      tr.appendChild(ts);
      tb.appendChild(tr);
    }
    tbl.appendChild(tb);
    const wrap = el("div", "scroll-x");
    wrap.appendChild(tbl);
    det.appendChild(wrap);
    det.appendChild(el("p", "sub",
      "Absence of a signal above means checked-and-clear, never "
      + "didn't-look; that is what this table is for."));
    watchBody.appendChild(det);
  }

  // ------------------------------------------- BEFORE YOU READ THIS -----
  // The fifteen this page computed on, always printed, above every answer it
  // governs; then every live gap in the data behind the page, with its fix,
  // all from the payloads. The gap strip renders nothing when there is none.
  function gapRow(kind, text, fix, title) {
    const row = el("div", "gap-row");
    row.appendChild(el("b", "gap-kind", kind));
    const body = el("span", "gap-text");
    if (text instanceof Node) body.appendChild(text);
    else body.appendChild(document.createTextNode(String(text)));
    if (title) body.title = title;
    row.appendChild(body);
    const fixEl = el("span", "gap-fix");
    if (fix) fixEl.appendChild(fix);
    row.appendChild(fixEl);
    return row;
  }
  function cmdFix(cmd) {
    const wrap = el("span", "gap-cmd");
    wrap.appendChild(el("code", null, cmd));
    const b = el("button", "chip", "copy");
    b.type = "button";
    b.onclick = async () => {
      try { await navigator.clipboard.writeText(cmd); b.textContent = "copied"; }
      catch { b.textContent = "select the command and copy it"; }
    };
    wrap.appendChild(b);
    wrap.appendChild(el("span", "db-quiet", "then press Refresh"));
    return wrap;
  }

  function renderGaps() {
    // The squad gap's fix is one paste on the Account tab (the CLI equivalent
    // stays for terminal people). FPL has no API login, so the browser cookie
    // step is the one thing no tool removes; everything after it is one paste.
    function connectFix(cmd) {
      const wrap = el("span", "gap-cmd");
      const link = el("a", "chip", "Connect your FPL account");
      link.href = "#account";
      link.title = "one paste of your fantasy.premierleague.com cookies; the "
        + "panels read your live team on the next request";
      wrap.appendChild(link);
      if (cmd) wrap.appendChild(cmdFix(cmd));
      return wrap;
    }

    // ---- the fifteen, always rendered, above the answers it governs
    sourceBlock.textContent = "";
    sourceBlock.classList.remove("warn");
    const gwNext = or(brief && brief.gw, null);
    const srcLine = el("div", "src-line");
    srcLine.appendChild(el("b", "src-kind", "the fifteen"));
    const body = el("span", "src-text");
    if (squadSource) {
      const live = squadSource.live === true;
      sourceBlock.classList.toggle("warn", !live);
      body.textContent = `${squadSource.label}`
        + when(squadSource.picks_gw != null, `, GW${squadSource.picks_gw} picks`)
        + `, read ${fmtAgeDays(squadSource.as_of)}. `
        + pick(live,
               `Every answer below is computed on the team you hold.`,
               `Transfers made since are not in this 15, and the captain, `
               + `bench and chip answers below were computed on it.`);
      body.title = `squad_source.as_of ${or(squadSource.as_of, "not served")}`;
      srcLine.appendChild(body);
      if (!live) srcLine.appendChild(connectFix(squadSource.fix));
    } else if (sq) {
      sourceBlock.classList.add("warn");
      body.textContent = `GW${or(sq.gw, "unknown")} picks from `
        + `${sq.provenance_source}, read ${fmtAgeDays(sq.as_of)}. The brief `
        + `served no squad_source block, so the fix command is not known here.`;
      srcLine.appendChild(body);
    } else {
      sourceBlock.classList.add("warn");
      body.textContent = "No squad was read, so nothing below was computed on "
        + "a fifteen. The lineup card carries the panel's own reason.";
      srcLine.appendChild(body);
    }
    sourceBlock.appendChild(srcLine);

    // ---- the gaps
    gapStrip.textContent = "";
    const rows = [];
    const kinds = [];
    const S = or(brief && brief.solve, null);
    // 1. the solver plan cannot guide
    if (S && S.state !== "fresh" && S.state !== "aging") {
      let txt = `Solver plan ${S.state}`
        + pick(S.reason, `: ${noDash(S.reason)}`, ".")
        + when(S.generated_at,
               ` Written ${shortDate(S.generated_at)}, `
               + `${agoPhrase(S.generated_at)}.`);
      if (solveStatus
          && (solveStatus.state === "failed" || solveStatus.state === "error"))
        txt += ` Last re-run `
          + or(shortDate(or(solveStatus.finished_utc, solveStatus.started_utc)), "")
          + ` failed: ` + noDash(or(lastLogLine(), "no log tail served"));
      // the fix, with what it costs beside it and not only in its title
      let fix = el("span", "gap-cmd");
      fix.append(rerunButton(false),
                 el("span", "db-quiet sv-cost", "costs 2 to 5 minutes"));
      if (solveStatus?.state === "running")
        fix = el("span", "db-quiet", "solving now");
      kinds.push("solver");
      rows.push(gapRow("solver", txt, fix,
        "state from dashboard_brief.solve; last run from /api/solve/status"));
    } else if (!brief) {
      kinds.push("solver");
      rows.push(gapRow("solver",
        "dashboard_brief did not answer; the solve state is unknown.", null));
    }
    // 2. the consensus predates the last deadline
    const xAs = parseTs(brief?.xpts_as_of);
    if (brief && xAs && lastDeadline && xAs < lastDeadline) {
      const a = el("a", "chip", "Pipelines tab");
      a.href = "#pipelines";
      kinds.push("consensus");
      rows.push(gapRow("consensus",
        `Consensus xPts are ${fmtAgeDays(brief.xpts_as_of)} old, from before `
        + `the last deadline (${shortDate(S?.last_deadline_utc)}); every xPts `
        + `on this page is that vintage. `
        + `${or(noDash(brief.xpts_source), "")}`, a));
    } else if (brief && !xAs) {
      kinds.push("consensus");
      rows.push(gapRow("consensus",
        "No consensus xPts as-of served; the xPts chips carry no date.", null));
    }
    // 3. the haul simulation predates the last deadline
    if (brief && brief.p_haul_generated && !haulFresh) {
      kinds.push("haul odds");
      rows.push(gapRow("haul odds",
        `${haulUnavailable}; older than the last deadline`
        + when(S?.last_deadline_utc, ` (${shortDate(S.last_deadline_utc)})`)
        + ", so no haul number renders on this page."
        + when(brief.p_haul_source, ` Source: ${noDash(brief.p_haul_source)}`),
        null));
    }
    // 4. the squad carries an FPL availability flag
    for (const a of or(brief && brief.alerts, [])) {
      if (a.rule !== "availability") continue;
      kinds.push("availability");
      rows.push(gapRow("availability", claimFor(a), null,
        `${a.source_panel}, ${fmtAgeDays(a.source_as_of)}`));
    }
    // 5. a gate could not be evaluated at all
    for (const e of or(brief && brief.empty_kinds, [])) {
      if (e.kind === "moves") continue;   // the transfer row carries that one
      kinds.push(e.kind.replace(/_/g, " "));
      rows.push(gapRow(e.kind.replace(/_/g, " "), e.reason, null));
    }
    if (!rows.length) { gapStrip.hidden = true; return; }
    gapStrip.hidden = false;
    gapStrip.open = wideEnoughForGaps;
    const head = el("summary", "gap-head");
    head.append(el("b", "gap-h", "Before you read this"),
      el("span", "db-quiet",
        `${rows.length} thing${when(rows.length > 1, "s")} stop this page `
        + `answering better, each one named with its fix: `
        + `${kinds.join(", ")}.`));
    gapStrip.appendChild(head);
    const shown = rows.slice(0, 4);
    shown.forEach(r => gapStrip.appendChild(r));
    if (rows.length > shown.length)
      gapStrip.appendChild(el("p", "db-quiet",
        `+${rows.length - shown.length} more in the watch log below.`));
  }

  // ------------------------------------------------ where the season is
  function renderStanding() {
    // The objective is P(top-1k) and the dashboard could not say where the
    // season stood: GW3 scored 23 against a field average of 51 and the rank
    // fell from 141,593 to 769,533 with nothing on the page reporting it.
    // Every number here is the manager's own crawled gameweek against FPL's
    // published average for that gameweek. Nothing is modelled.
    standingStrip.textContent = "";
    const st = or(brief && brief.standing, null);
    if (!st) { standingStrip.hidden = true; return; }
    standingStrip.hidden = false;
    const rows = or(st.gws, []).filter(g => g.points != null);
    if (!rows.length) {
      standingStrip.appendChild(namedGap("Season standing unknown.",
        or(st.reason, "no crawled gameweek for this entry yet.")));
      return;
    }
    const head = el("div", "db-sthead");
    head.appendChild(el("h2", null, "Where the season stands"));
    if (st.overall_rank != null) {
      const r = el("div", "db-strank");
      r.appendChild(el("b", null, fmtRank(st.overall_rank)));
      let moveTxt = "overall rank";
      if (st.rank_move != null && st.rank_move !== 0) {
        // FPL ranks count upward from the top, so a positive move is a fall.
        const fell = st.rank_move > 0;
        moveTxt = `overall rank, ${pick(fell, "down", "up")} `
          + `${fmtRank(Math.abs(st.rank_move))} places on the gameweek`;
      }
      r.appendChild(el("span", "sub", moveTxt));
      head.appendChild(r);
    }
    standingStrip.appendChild(head);

    const grid = el("div", "db-stgws");
    for (const g of rows) {
      const cell = el("div", "db-stgw");
      cell.appendChild(el("i", "db-stlab", `GW${g.gw}`));
      cell.appendChild(el("b", null, String(g.points)));
      if (g.delta != null) {
        const d = el("span",
          "db-stdelta " + pick(g.delta >= 0, "up", "down"),
          `${fmtSigned(g.delta, 0)} vs field`);
        d.title = `the field averaged ${g.field} in GW${g.gw}`;
        cell.appendChild(d);
      } else {
        cell.appendChild(el("span", "db-stdelta flat", "field average not "
          + "published yet"));
      }
      const bits = [];
      if (g.bench_points) bits.push(`${g.bench_points} left on the bench`);
      if (g.hit_cost) bits.push(`${g.hit_cost} spent on hits`);
      if (bits.length) cell.appendChild(el("span", "db-stsub", bits.join(", ")));
      grid.appendChild(cell);
    }
    standingStrip.appendChild(grid);

    const footBits = [];
    if (st.total_points != null)
      footBits.push(`${st.total_points} points over ${rows.length} gameweek`
        + when(rows.length > 1, "s"));
    if (st.vs_field_total != null)
      footBits.push(`${fmtSigned(st.vs_field_total, 0)} against the field in `
        + `total`);
    if (footBits.length)
      standingStrip.appendChild(el("p", "db-stfoot",
        footBits.join(", ") + "."));
    standingStrip.appendChild(citeChip("dashboard_brief", brief?.as_of));
  }

  // ---------------------------------------------------------------- foot
  function renderFoot() {
    foot.textContent = "";
    if (sqR.ok && sqR.prov) foot.appendChild(provenance(sqR.prov));
    if (brR.ok && brR.prov) foot.appendChild(provenance(brR.prov));
    if (!brief) return;
    const clocks = Object.entries(or(brief.sources_as_of, {}))
      .map(([k, v]) => `${k} ${fmtAgeDays(v)}`).join(" · ");
    if (clocks) {
      const line = el("div", "provenance", "source clocks: " + clocks);
      line.title = Object.entries(or(brief.sources_as_of, {}))
        .map(([k, v]) => `${k} ${or(v, "not served")}`).join("\n");
      foot.appendChild(line);
    }
    if (brief.season && brief.entry_id != null)
      foot.appendChild(el("div", "provenance",
        `${brief.season} · entry ${brief.entry_id}`
        + when(brief.projection_gw != null,
               ` · projections for GW${brief.projection_gw}`)));
    for (const note of or(brief.notes, []))
      foot.appendChild(el("div", "provenance", note));
  }

  function renderAll() {
    ledgerCard.classList.remove("db-pending");
    for (const n of [...ledgerCard.querySelectorAll(".db-loading")]) n.remove();
    renderHead();
    renderGaps();
    renderVerdict();
    renderPitch();
    renderTiles();
    renderWatch();
    renderStanding();
    renderFoot();
  }
  // the working arrives below the answers, after the answers exist, so the
  // ledger growing out of its loading line moves nothing already painted
  host.append(pitchCard, tilesCard, watchCard, standingStrip, foot);
  renderAll();
  if (solveStatus && solveStatus.state === "running") startSolvePolling();
}
