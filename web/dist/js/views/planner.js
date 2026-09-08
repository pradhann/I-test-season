/* Planner: solver and manual planner in ONE tab (fplreview's idiom).
   Left, a controls rail: horizon, allowed hits, chips, must-keep, ban, an
   advanced fold (time and candidate caps) and Solve. Right, the results and
   the grid: rows = your 15 (+ incoming players), columns = the next H
   gameweeks, cells = consensus xPts. The solver's headline plan is drawn INTO
   the grid (out struck, in added; the grid recomputes XI totals, bank, FTs and
   hits by its own machinery); the optimiser's unconstrained best and the
   alternatives it beat sit in a second card, each one click from the grid.

   Two currencies, never blended: the grid speaks consensus xPts from the
   planner_grid panel; the solver cards speak the plan's own objective_mode
   (the expected_points surrogate), labelled as the solver's forecast.

   Solve = POST /api/solve {mode: "transfers", options}; the runner maps the
   options onto `fpl recommend` flags, one solve at a time, status polled from
   /api/solve/status. The committed plan is read from /api/solve/transfer-plan,
   which resolves names and judges freshness: a plan that predates a deadline
   is a gap with Re-solve, never guidance.

   All game rules (FTs per GW, banking cap, hit cost) come from the payload's
   `rules` block, which the panel script reads from the verified rule
   registry; this file never hardcodes a game rule. */

import { runPanel, getJSON, postJSON, el, emptyBox, errBox, provenance, faceImg,
         stat, playerCard, fmtPrice, fmt1, fmt2, fmtAge } from "/js/app.js";

const PLANS_KEY = "itest-planner-plans-v1";
const RAIL_KEY = "itest-planner-rail-v1";
const MAX_PLANS = 5;
const POS_ORDER = { GKP: 0, DEF: 1, MID: 2, FWD: 3 };
const POLL_MS = 3000;

const CHIPS = [["wildcard", "Wildcard"], ["freehit", "Free Hit"],
               ["bboost", "Bench Boost"], ["3xc", "Triple Captain"]];
const CHIP_NAME = Object.fromEntries(CHIPS);
const HIT_CHOICES = [[0, "0 hits"], [1, "up to 1 hit"], [2, "up to 2 hits"],
                     [-1, "unconstrained"]];
/* The rail's defaults mirror solve_runner.TRANSFER_DEFAULTS (the settings
   that solved the GW4-8 problem); the server re-validates every field. */
const RAIL_DEFAULTS = { horizon: 5, max_hits: 0, chips: [], must_keep: [],
                       ban: [], seconds: 150, max_candidates: 20 };

function loadPlans() {
  try { return JSON.parse(localStorage.getItem(PLANS_KEY)) || {}; }
  catch { return {}; }
}
function storePlans(plans) {
  try { localStorage.setItem(PLANS_KEY, JSON.stringify(plans)); } catch { /* per-browser convenience */ }
}
function loadRail() {
  try { return JSON.parse(localStorage.getItem(RAIL_KEY)) || {}; }
  catch { return {}; }
}
function storeRail(rail) {
  try { localStorage.setItem(RAIL_KEY, JSON.stringify(rail)); } catch { /* same */ }
}

function fmtSigned(x, d = 1) {
  if (x == null || Number.isNaN(Number(x))) return "?";
  const n = Number(x);
  return (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n).toFixed(d);
}
function shortTs(iso) {
  if (!iso) return "?";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toISOString().slice(0, 16).replace("T", " ") + "Z";
}
/* No local age helper: app.js's fmtAge/fmtSpan is the one age vocabulary
   every view speaks. The version that lived here printed "112.2h ago", the
   arithmetic the owner asked to be removed. */
const sortedInts = xs => [...(xs || [])].map(Number).sort((a, b) => a - b);
function sameSets(a, b) {
  const x = sortedInts(a), y = sortedInts(b);
  return x.length === y.length && x.every((v, i) => v === y[i]);
}
function sameMove(m1, m2) {
  return !!m1 && !!m2 && sameSets(m1.out, m2.out) && sameSets(m1.in, m2.in);
}
/* Is `part` a strict subset of `whole` on both sides? The artefact ranks
   every candidate it solved, so the one-transfer half of a two-transfer plan
   comes back in the same list and was printed under "alternative it beat":
   "Odegaard to Tavernier" is not an alternative to a plan that makes it. */
export function subsetMove(part, whole) {
  if (!part || !whole || sameMove(part, whole)) return false;
  const has = (xs, ys) => {
    const set = new Set(sortedInts(xs));
    return sortedInts(ys).every(v => set.has(v));
  };
  const size = (part.out || []).length + (part.in || []).length;
  return size > 0 && has(whole.out, part.out) && has(whole.in, part.in);
}

function card(title, cls) {
  const c = el("section", "card" + (cls ? " " + cls : ""));
  const head = el("div", "pl-cardhead");
  head.appendChild(el("h2", null, title));
  c.appendChild(head);
  const body = el("div");
  c.appendChild(body);
  c.head = head; c.body = body;   // refreshes clear the body, never the header
  return c;
}

export default async function planner(host) {
  // ---- rail settings (remembered per browser; the server re-validates) ----
  const rail = { ...RAIL_DEFAULTS, ...loadRail() };
  rail.chips = (rail.chips || []).filter(c => CHIP_NAME[c]);
  let horizon = Math.min(8, Math.max(1, Number(rail.horizon) || 5));
  rail.horizon = horizon;

  // ---- layout ----
  const provLine = el("p", "pl-prov");
  const layout = el("div", "pl-layout");
  const aside = el("aside", "card pl-rail");
  aside.setAttribute("aria-label", "solver controls");
  const main = el("div", "pl-main");
  const solverCard = card("Solver plan", "pl-solver");
  const altCard = card("If hits were free, and the alternatives it beat", "pl-alts");
  const plannerCard = el("section", "card");
  plannerCard.appendChild(el("h2", null, "Transfer planner"));
  const sub = el("p", "sub");
  plannerCard.appendChild(sub);
  layout.append(aside, main);
  main.append(solverCard, altCard, plannerCard);
  host.append(provLine, layout);

  let res, prov;
  async function fetchPayload() {
    ({ result: res, provenance: prov } = await runPanel("planner_grid", { horizon }));
  }
  try { await fetchPayload(); }
  catch (e) { plannerCard.appendChild(errBox(e)); return; }
  if (res?.empty) { plannerCard.appendChild(emptyBox(res.reason)); return; }

  // ---- static bits of the payload ----
  const R = res.rules;                 // {free_per_gw, max_banked, hit_cost}
  const tenths = p => Math.round((p.price ?? 0) * 10);
  let byCode, gridMax;
  function index() {
    byCode = new Map();
    for (const p of res.squad) byCode.set(p.code, p);
    for (const p of res.candidates) if (!byCode.has(p.code)) byCode.set(p.code, p);
    gridMax = 0.001;
    for (const per of Object.values(res.xpts))
      for (const v of Object.values(per)) if (v > gridMax) gridMax = v;
  }
  index();
  const xp = (code, gw) => res.xpts[String(code)]?.[String(gw)] ?? null;
  const sprd = (code, gw) => res.spread[String(code)]?.[String(gw)] ?? null;
  const pApp = (code, gw) => res.p_appear?.[String(code)]?.[String(gw)] ?? null;
  const met = code => res.metrics?.[String(code)] ?? null;

  /* what the grid cells display, user-selectable */
  const CELL_METRICS = {
    xpts:   { label: "xPts (consensus)", get: xp,   fmt: fmt1, max: () => gridMax },
    spread: { label: "source spread",    get: sprd, fmt: fmt2,
              max: () => Math.max(0.001, ...Object.values(res.spread).flatMap(o => Object.values(o))) },
    p_appear: { label: "p(appear)",      get: pApp, fmt: fmt2, max: () => 1 },
  };
  let cellMetric = "xpts";

  /* pool metric columns, toggleable set */
  const POOL_COLS = {
    own:    { label: "own %",  get: c => c.own_pct,           fmt: fmt1 },
    xsum:   { label: "ΣxPts", get: c => c._xsum,         fmt: fmt2 },
    xg:     { label: "xG",     get: c => met(c.code)?.xg,     fmt: fmt2 },
    xa:     { label: "xA",     get: c => met(c.code)?.xa,     fmt: fmt2 },
    shots:  { label: "shots",  get: c => met(c.code)?.shots,  fmt: v => String(v) },
    mins:   { label: "mins",   get: c => met(c.code)?.mins,   fmt: v => String(Math.round(v)) },
    goals:  { label: "G",      get: c => met(c.code)?.goals,  fmt: v => String(Math.round(v)) },
    papp:   { label: "p(app)", get: c => pApp(c.code, res.gws[0]), fmt: fmt2 },
  };
  let poolCols = ["own", "xsum", "xg", "shots"];
  let poolSort = "xsum";
  let poolPos = "", poolTeam = "", poolMax = "", poolSearch = "";
  let poolGw = null;               // which GW an added transfer lands in
  let adding = null;               // candidate code while choosing who to sell

  // ---- plan state: an ordered list of {gw, out, in} ----
  let moves = [];
  let picking = null;                  // {gw, out} while choosing a replacement
  let notice = "";
  let pitchGw = res.gws[0];            // the GW the pitch view plans against

  function squadAt(gw) {               // codes in the squad entering GW `gw`'s moves applied
    const codes = new Set(res.squad.map(p => p.code));
    for (const m of moves) if (m.gw <= gw) { codes.delete(m.out); codes.add(m.in); }
    return codes;
  }
  function bankBefore(gw) {            // bank after all moves in GWs <= gw
    let bank = res.bank_tenths;
    for (const m of moves) if (m.gw <= gw) {
      const o = byCode.get(m.out), n = byCode.get(m.in);
      bank += (o ? tenths(o) : 0) - (n ? tenths(n) : 0);
    }
    return bank;
  }

  /* Best legal XI by xPts (1 GKP, 3-5 DEF, 2-5 MID, 1-3 FWD), captain
     auto-assigned to the highest xPts in the XI and doubled. */
  function xiTotal(codes, gw) {
    const by = { GKP: [], DEF: [], MID: [], FWD: [] };
    for (const c of codes) {
      const p = byCode.get(c);
      if (p && by[p.pos]) by[p.pos].push(xp(c, gw) ?? 0);
    }
    for (const k in by) by[k].sort((a, b) => b - a);
    const gk = by.GKP[0] ?? 0;
    let best = null, bestCap = null;
    for (let d = 3; d <= Math.min(5, by.DEF.length); d++)
      for (let m = 2; m <= Math.min(5, by.MID.length); m++) {
        const f = 10 - d - m;
        if (f < 1 || f > 3 || f > by.FWD.length) continue;
        const outfield = [...by.DEF.slice(0, d), ...by.MID.slice(0, m),
                          ...by.FWD.slice(0, f)];
        const sum = gk + outfield.reduce((a, x) => a + x, 0);
        const cap = Math.max(gk, ...outfield);
        if (best === null || sum + cap > best) { best = sum + cap; bestCap = cap; }
      }
    if (best !== null) return { total: best, capXpts: bestCap };
    // Degenerate squad (should not happen with same-position swaps): top 11.
    const all = Object.values(by).flat().sort((a, b) => b - a).slice(0, 11);
    return { total: all.reduce((a, x) => a + x, 0) + (all[0] ?? 0),
             capXpts: all[0] ?? 0 };
  }

  /* Who the grid doubled. The table badged `(C)` from the LOCKED FPL armband
     while xiTotal auto-captains the XI's top xPts, so the "XI xPts" row could
     double a different player from the one wearing the badge, eight points
     apart, with nothing on the page reconciling them. */
  function capName(codes, gw, capXpts) {
    if (capXpts == null) return null;
    let hit = null;
    for (const c of codes) {
      const v = xp(c, gw);
      if (v != null && Math.abs(v - capXpts) < 1e-9) { hit = byCode.get(c); break; }
    }
    return hit ? hit.name : null;
  }

  /* One pass over the horizon: transfers, FTs used/banked, hits, bank, XI. */
  function compute() {
    let ft = res.ft_entering, bank = res.bank_tenths;
    const codes = new Set(res.squad.map(p => p.code));
    const perGw = []; let totalX = 0, totalHits = 0;
    for (const g of res.gws) {
      const mv = moves.filter(m => m.gw === g);
      for (const m of mv) {
        codes.delete(m.out); codes.add(m.in);
        const o = byCode.get(m.out), n = byCode.get(m.in);
        bank += (o ? tenths(o) : 0) - (n ? tenths(n) : 0);
      }
      const ftUsed = Math.min(mv.length, ft);
      const hits = mv.length - ftUsed;
      const x = xiTotal(codes, g);
      perGw.push({ gw: g, transfers: mv.length, ftAvail: ft, ftUsed, hits, bank,
                   xi: x.total, capName: capName(codes, g, x.capXpts) });
      totalX += x.total; totalHits += hits;
      ft = Math.min(R.max_banked, ft - ftUsed + R.free_per_gw);
    }
    return { perGw, totalX, totalHits,
             net: totalX - totalHits * R.hit_cost };
  }

  /* Drop moves the current payload cannot honour (unknown player, out not in
     squad at that GW, position mismatch), replayed in order so a cascade of
     dependent moves stays consistent. Returns how many were dropped. */
  function sanitise() {
    const clean = [];
    const keepable = m => {
      const o = byCode.get(m.out), n = byCode.get(m.in);
      if (!o || !n || o.pos !== n.pos) return false;
      if (!res.gws.includes(m.gw)) return false;
      const codes = new Set(res.squad.map(p => p.code));
      for (const prev of clean) if (prev.gw <= m.gw) { codes.delete(prev.out); codes.add(prev.in); }
      return codes.has(m.out) && !codes.has(m.in);
    };
    const dropped = moves.filter(m => !keepable(m) || !clean.push(m)).length;
    moves = clean;
    return dropped;
  }

  // ---- containers ----
  const toolbar = el("div", "filters");
  const summaryBox = el("div");
  const pitchBox = el("div");
  const pickerBox = el("div");
  const gridBox = el("div");
  const poolBox = el("div");
  const noteLine = el("p", "sub pl-notice");
  noteLine.setAttribute("role", "status");
  const gridHint = el("p", "pl-gridhint",
    "Click a player to remove, keep or ban. Click Solve to let the optimiser fill the plan.");
  plannerCard.append(toolbar, summaryBox, gridHint, pitchBox, pickerBox, gridBox, poolBox, noteLine);
  plannerCard.appendChild(provenance(prov));

  function describePayload() {
    sub.textContent =
      `Consensus xPts per GW from the planner_grid panel` +
      (prov?.generated_at ? ` (as of ${shortTs(prov.generated_at)})` : "") +
      `; cell tint = magnitude, hover for the cross-source spread. ` +
      `Click a player, or a cell, to remove, keep or ban him. Grid captain is ` +
      `auto-assigned to the XI's top xPts. ` + (res.notes || []).join(" ");
  }
  describePayload();

  // ---- squad provenance, printed at the top of the tab ----
  function renderProvenance() {
    provLine.textContent = "";
    const label = String(res.provenance_source || "unknown source");
    const live = /^live/i.test(label);
    const isPublic = /^public/i.test(label);
    provLine.className = "pl-prov" + (live ? "" : " warn");
    provLine.appendChild(el("b", null, "Squad: "));
    provLine.appendChild(document.createTextNode(
      isPublic ? `public GW${res.gws[0] - 1} picks, published after that deadline` : label));
    if (!live) {
      provLine.appendChild(document.createTextNode(
        ". Not your live team: the grid and the solver both plan from this squad, so " +
        "any transfer you have already made this week is invisible here. Fix: run "));
      provLine.appendChild(el("code", null, "uv run fpl myteam auth"));
      provLine.appendChild(document.createTextNode(" once."));
    }
    if (prov?.generated_at)
      provLine.appendChild(el("span", "pl-asof", ` squad as of ${shortTs(prov.generated_at)}`));
  }
  renderProvenance();

  // ---- solver state ----
  let tplan = null;           // GET /api/solve/transfer-plan
  let solveStatus = null;     // GET /api/solve/status
  let lastState = null;
  let kicked = false;         // a solve started from this tab, in this session
  async function fetchTplan() {
    try { tplan = await getJSON("/api/solve/transfer-plan"); }
    catch (e) { tplan = { exists: false, reason: String(e.message || e) }; }
  }
  await fetchTplan();

  /* name lookup: the grid's own payload first, the plan route's resolution
     second, the bare code last (never a guessed name) */
  function who(code) {
    const c = Number(code);
    const p = byCode.get(c);
    if (p) return p;
    const r = tplan?.players?.[String(c)];
    return r ? { code: c, ...r } : { code: c, name: String(c), pos: "?", price: null, team: null };
  }
  /* The artefact stores the move as SETS (out, in). Pair them within
     position by price, the way the dashboard brief does, so a two-move plan
     draws as two rows and each row is a same-position swap the grid accepts. */
  function pairMoves(mv) {
    const outs = (mv?.out || []).map(who), ins = (mv?.in || []).map(who);
    const byPos = pos => arr => arr.filter(p => (p.pos || "?") === pos)
      .sort((a, b) => (b.price ?? 0) - (a.price ?? 0));
    const positions = [...new Set([...outs, ...ins].map(p => p.pos || "?"))]
      .sort((a, b) => (POS_ORDER[a] ?? 9) - (POS_ORDER[b] ?? 9));
    const pairs = [];
    for (const pos of positions) {
      const o = byPos(pos)(outs), i = byPos(pos)(ins);
      const n = Math.max(o.length, i.length);
      for (let k = 0; k < n; k++) pairs.push({ out: o[k] || null, in: i[k] || null, pos });
    }
    return pairs;
  }
  const planGw = () => tplan?.plan?.gw ?? null;
  const planFresh = () => !!(tplan?.exists && !tplan.stale && tplan.plan);
  /* Why a move cannot be drawn into the grid; null when it can. */
  function applyBlocker(mv) {
    if (!planFresh()) return "the plan is stale; re-solve first";
    if (mv?.chip) return `a ${CHIP_NAME[mv.chip] || mv.chip} plan: the grid has no chip accounting`;
    const gw = planGw();
    if (!res.gws.includes(gw))
      return `the plan's first gameweek GW${gw} is not in the grid (GW${res.gws[0]}-${res.gws.at(-1)})`;
    const squad = new Set(res.squad.map(p => p.code));
    const notHeld = (mv?.out || []).map(Number).filter(c => !squad.has(c));
    if (notHeld.length)
      return `the plan sells ${notHeld.map(c => who(c).name).join(", ")}, not in the grid's squad: the squad source changed since the solve`;
    const pairs = pairMoves(mv);
    if (pairs.some(p => !p.out || !p.in || !byCode.has(p.in.code)))
      return "the plan's in/out sets do not pair within position";
    return null;
  }
  function isApplied(mv) {
    if (!mv || applyBlocker(mv)) return false;
    const pairs = pairMoves(mv);
    if (moves.length !== pairs.length) return false;
    const gw = planGw();
    return moves.every(m => m.gw === gw)
      && sameSets(moves.map(m => m.out), pairs.map(p => p.out.code))
      && sameSets(moves.map(m => m.in), pairs.map(p => p.in.code));
  }
  function applyMove(mv, label) {
    const why = applyBlocker(mv);
    if (why) { notice = `Not drawn into the grid: ${why}.`; render(); return; }
    const gw = planGw();
    moves = pairMoves(mv).map(p => ({ gw, out: p.out.code, in: p.in.code }));
    moves.sort((a, b) => a.gw - b.gw);
    picking = null; adding = null; pitchGw = gw;
    const dropped = sanitise();
    const said = pairMoves(mv).map(p => `${p.out.name} → ${p.in.name}`).join(", ");
    notice = moves.length
      ? `${label} fills the grid: GW${gw} ${said}.` + (dropped ? ` ${dropped} pair(s) the grid could not honour were dropped.` : "")
      : `${label} is a roll: no transfer drawn, the grid shows your 15 as they stand.`;
    render();
    gridBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // ---- the solve rail ----
  const railBody = el("div");
  const statusBox = el("div", "pl-status");
  aside.append(el("h2", null, "Solver"), railBody, statusBox);

  function field(labelText, control, hint) {
    const f = el("div", "pl-field");
    const lab = el("label", null, labelText);
    if (control?.id) lab.htmlFor = control.id;
    f.appendChild(lab);
    f.appendChild(control);
    if (hint) f.appendChild(el("div", "pl-hint", hint));
    return f;
  }
  function persist() { storeRail(rail); }

  function renderRail() {
    railBody.textContent = "";

    // horizon: one horizon for the grid and the solver, never two
    const hz = el("select"); hz.id = "pl-horizon";
    for (let h = 1; h <= 8; h++) {
      const o = el("option", null, `${h} GW${h > 1 ? "s" : ""}`); o.value = h;
      if (h === horizon) o.selected = true;
      hz.appendChild(o);
    }
    hz.onchange = async () => {
      horizon = Number(hz.value); rail.horizon = horizon; persist();
      try {
        await fetchPayload();
        if (res?.empty) { plannerCard.textContent = ""; plannerCard.appendChild(emptyBox(res.reason)); return; }
        index();
        const dropped = sanitise();
        notice = dropped ? `${dropped} move(s) fell outside the new horizon and were dropped.` : "";
        describePayload();
        render();
      } catch (e) { plannerCard.appendChild(errBox(e)); }
    };
    railBody.appendChild(field("horizon", hz,
      "gameweeks the objective sums over; the grid shows the same columns"));

    // hits allowed for the headline
    const hits = el("select"); hits.id = "pl-hits";
    for (const [v, lbl] of HIT_CHOICES) {
      const o = el("option", null, lbl); o.value = v;
      if (Number(rail.max_hits) === v) o.selected = true;
      hits.appendChild(o);
    }
    hits.onchange = () => { rail.max_hits = Number(hits.value); persist(); };
    railBody.appendChild(field("hits for the headline", hits,
      "hit-taking moves are still solved and shown as the unconstrained best"));

    // chips: four toggles, default all off
    const chipBox = el("div", "pl-checks");
    for (const [key, name] of CHIPS) {
      const lab = el("label", "pl-check");
      const cb = el("input"); cb.type = "checkbox"; cb.value = key;
      cb.checked = rail.chips.includes(key);
      cb.onchange = () => {
        rail.chips = CHIPS.map(([k]) => k).filter(k => k === key ? cb.checked : rail.chips.includes(k));
        persist();
      };
      lab.append(cb, document.createTextNode(" " + name));
      chipBox.appendChild(lab);
    }
    railBody.appendChild(field("chips the optimiser may play", chipBox,
      "off by default: a chip is your decision, not the objective's. A plan that spends one is labelled a chip plan."));

    // must-keep: toggles over the squad
    const keepBox = el("div", "pl-toggles");
    const squadSorted = [...res.squad].sort((a, b) =>
      (POS_ORDER[a.pos] ?? 9) - (POS_ORDER[b.pos] ?? 9) || b.price - a.price);
    rail.must_keep = (rail.must_keep || []).filter(c => byCode.has(Number(c)));
    for (const p of squadSorted) {
      const b = el("button", "pl-toggle", p.name);
      b.type = "button";
      b.title = `${p.pos} ${p.team ?? ""} ${fmtPrice(p.price)}`;
      const on = rail.must_keep.includes(p.code);
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.onclick = () => {
        const now = b.getAttribute("aria-pressed") === "true";
        rail.must_keep = now ? rail.must_keep.filter(c => c !== p.code) : [...rail.must_keep, p.code];
        rail.ban = (rail.ban || []).filter(c => c !== p.code);
        b.setAttribute("aria-pressed", now ? "false" : "true");
        persist(); renderBans(); keepHint();
      };
      keepBox.appendChild(b);
    }
    /* Fifteen names under "locked in every gameweek" read as fifteen locks,
       because a pressed toggle differs from an unpressed one only by its
       background. The count is stated, and the whole block is dimmed while
       nothing is selected. */
    const keepNote = el("div", "pl-hint");
    const keepHint = () => {
      const n = rail.must_keep.length;
      keepBox.classList.toggle("pl-none", n === 0);
      keepNote.textContent = n === 0
        ? `None selected: the solver may sell any of the ${squadSorted.length}. `
          + "Press a name to lock him in every gameweek of the horizon."
        : `${n} of ${squadSorted.length} locked in every gameweek of the horizon `
          + "(OptimizerConfig.locked); the rest can be sold.";
    };
    const keepField = field("must keep", keepBox);
    keepField.appendChild(keepNote);
    keepHint();
    railBody.appendChild(keepField);

    // ban: a typeahead over the universe, bans as removable chips
    const banWrap = el("div");
    const banIn = el("input"); banIn.type = "text"; banIn.id = "pl-ban";
    banIn.placeholder = "search player or team to ban"; banIn.autocomplete = "off";
    banIn.setAttribute("aria-label", "search a player to ban");
    const banHits = el("div", "pl-banhits");
    const banList = el("div", "pl-toggles");
    banWrap.append(banIn, banHits, banList);
    rail.ban = (rail.ban || []).filter(c => byCode.has(Number(c)));
    function renderBans() {
      banList.textContent = "";
      for (const c of rail.ban) {
        const p = who(c);
        const b = el("button", "chip bad pl-banchip", `${p.name} ×`);
        b.type = "button";
        b.setAttribute("aria-label", `remove ban on ${p.name}`);
        b.onclick = () => { rail.ban = rail.ban.filter(x => x !== c); persist(); renderBans(); };
        banList.appendChild(b);
      }
      if (!rail.ban.length) banList.appendChild(el("span", "pl-hint", "no bans"));
    }
    banIn.oninput = () => {
      banHits.textContent = "";
      const term = banIn.value.trim().toLowerCase();
      if (term.length < 2) return;
      const rows = res.candidates
        .filter(c => !rail.ban.includes(c.code) && !rail.must_keep.includes(c.code))
        .filter(c => c.name.toLowerCase().includes(term) || (c.team || "").toLowerCase().includes(term))
        .slice(0, 8);
      for (const c of rows) {
        const b = el("button", "pl-banhit", `${c.name} (${c.pos}, ${c.team ?? "?"}, ${fmtPrice(c.price)})`);
        b.type = "button";
        b.onclick = () => {
          rail.ban = [...rail.ban, c.code]; persist();
          banIn.value = ""; banHits.textContent = ""; renderBans(); banIn.focus();
        };
        banHits.appendChild(b);
      }
      if (!rows.length) banHits.appendChild(el("span", "pl-hint", "no match"));
    };
    renderBans();
    railBody.appendChild(field("ban", banWrap,
      "never owned; a banned player you hold is sold in the first gameweek (OptimizerConfig.banned). " +
      "Outside the candidate universe a ban is moot and the solver notes it."));

    // advanced fold: time and candidate caps
    const adv = el("details", "pl-adv");
    adv.appendChild(el("summary", null, "advanced: time and candidate caps"));
    const sec = el("input"); sec.type = "number"; sec.min = 10; sec.max = 900; sec.step = 10;
    sec.value = rail.seconds; sec.id = "pl-seconds";
    sec.onchange = () => { rail.seconds = Number(sec.value) || RAIL_DEFAULTS.seconds; persist(); };
    adv.appendChild(field("seconds per MILP", sec,
      "150s x 20 solved the GW4-8 problem in 219-238s; 60s found no incumbent"));
    const mc = el("input"); mc.type = "number"; mc.min = 5; mc.max = 80; mc.step = 5;
    mc.value = rail.max_candidates; mc.id = "pl-cands";
    mc.onchange = () => { rail.max_candidates = Number(mc.value) || RAIL_DEFAULTS.max_candidates; persist(); };
    adv.appendChild(field("candidates per position", mc,
      "the universe cap for every MILP; a capped solve is best-found, not a proven optimum"));
    const reset = el("button", null, "reset to defaults");
    reset.type = "button";
    reset.onclick = () => {
      Object.assign(rail, JSON.parse(JSON.stringify(RAIL_DEFAULTS)));
      horizon = rail.horizon; persist(); renderRail(); hz.dispatchEvent(new Event("change"));
    };
    adv.appendChild(reset);
    railBody.appendChild(adv);

    // Solve
    const solveBtn = el("button", "primary pl-solve", "Solve");
    solveBtn.type = "button";
    solveBtn.onclick = () => startSolve(solveBtn);
    railBody.appendChild(solveBtn);
    railBody.appendChild(el("p", "pl-hint",
      "runs `fpl recommend` as its own process (one at a time, minutes not seconds); " +
      "objective: expected_points, a surrogate the plan's notes name."));
    solveBtnRef = solveBtn;
    renderStatus();
  }
  let solveBtnRef = null;

  function solveOptions() {
    return {
      horizon, max_hits: Number(rail.max_hits),
      chips: [...rail.chips], must_keep: [...rail.must_keep], ban: [...rail.ban],
      seconds: Number(rail.seconds), max_candidates: Number(rail.max_candidates),
    };
  }
  async function startSolve(btn) {
    if (btn) { btn.disabled = true; btn.textContent = "starting…"; }
    try {
      const s = await postJSON("/api/solve", { mode: "transfers", options: solveOptions() });
      kicked = true;
      lastState = "running";
      solveStatus = s;
      notice = s.already_running
        ? "A solve is already running; attached to it."
        : "Solve started; the plan below refreshes when it commits.";
      renderStatus(); renderSolverCards(); noteLine.textContent = notice;
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = "Solve"; }
      statusBox.textContent = "";
      statusBox.appendChild(errBox(e));
    }
  }

  /* Did THIS run produce the plan the cards show?

     The rail printed one run's mode, times, exit code and settings directly
     above a plan card built from a different run, so "keep 1, ban 1" sat
     three inches from "solved with: keep none, ban none" and the log fold
     recommended four transfers and an 8-point hit that appeared nowhere on
     the page.

     The test is containment, not equality: the artefact stamps
     `generated_at` when the run reconstructs the squad, about a second after
     the process starts, not when it commits. So the run owns the plan when
     the plan's stamp falls inside [started, finished]. A running solve owns
     nothing yet; the plan on screen is still the previous one. */
  function runOwnsPlan(s) {
    const gen = tplan?.plan?.generated_at;
    if (!s || !gen || s.state === "running") return false;
    const g = Date.parse(gen), a = Date.parse(s.started_utc || "");
    const b = Date.parse(s.finished_utc || "");
    if (!Number.isFinite(g) || !Number.isFinite(a)) return false;
    const end = Number.isFinite(b) ? b : a;
    return g >= a - 2000 && g <= end + 2000;
  }
  function statusChip(state) {
    const cls = state === "running" ? "chip warn" : state === "done" ? "chip good"
      : state === "failed" ? "chip bad" : "chip";
    return el("span", cls, state || "idle");
  }
  function renderStatus() {
    statusBox.textContent = "";
    const s = solveStatus || { state: "idle", log_tail: [] };
    const running = s.state === "running";
    if (solveBtnRef) {
      solveBtnRef.disabled = running;
      solveBtnRef.textContent = running ? "Solving…" : "Solve";
    }
    const owns = runOwnsPlan(s);
    const planStamp = tplan?.exists && tplan.plan ? tplan.plan.generated_at : null;
    const ranAtAll = !!(s.started_utc || s.finished_utc);
    const line = el("div", "pl-statusline");
    line.appendChild(statusChip(s.state));
    const bits = [];
    if (s.mode) bits.push(`mode ${s.mode}`);
    if (s.started_utc) bits.push(`started ${shortTs(s.started_utc)}`);
    if (s.finished_utc) bits.push(`finished ${shortTs(s.finished_utc)}`);
    if (s.exit_code != null) bits.push(`exit ${s.exit_code}`);
    if (s.reason) bits.push(s.reason);
    line.appendChild(el("span", "pl-hint", bits.join(" · ")));
    statusBox.appendChild(line);
    if (ranAtAll && !running && planStamp) {
      statusBox.appendChild(el("div", owns ? "pl-hint" : "pl-hint pl-runsplit",
        owns
          ? "This run produced the plan card."
          : "Last run, not the run behind the plan card, which was generated "
            + `${shortTs(planStamp)}. The settings and log below describe this `
            + "run, not that plan."));
    }
    if (s.options && s.mode === "transfers") {
      const o = s.options;
      statusBox.appendChild(el("div", "pl-hint",
        `${owns ? "settings" : "this run's settings"}: horizon ${o.horizon}, ` +
        `hits ${o.max_hits < 0 ? "unconstrained" : "≤" + o.max_hits}, ` +
        `chips ${o.chips?.length ? o.chips.map(c => CHIP_NAME[c] || c).join("/") : "off"}, ` +
        `keep ${o.must_keep?.length || 0}, ban ${o.ban?.length || 0}, ` +
        `${o.seconds}s per solve, ${o.max_candidates} candidates per position`));
    }
    // A failed run is an error only when it is newer than the plan that
    // stands; an older failure beside a standing plan is a footnote.
    if (s.state === "failed") {
      const failedAt = new Date(s.finished_utc || s.started_utc || 0).getTime();
      const planAt = planFresh() ? new Date(tplan.plan.generated_at).getTime() : 0;
      if (kicked || !planFresh() || failedAt > planAt) {
        statusBox.appendChild(el("div", "err",
          "The last solve failed: " + (lastLogLine(s) || "no log tail served") +
          ". The plan below is whatever stood before it."));
      } else {
        statusBox.appendChild(el("div", "pl-hint",
          `that failure (${shortTs(s.finished_utc || s.started_utc)}) is older than the standing plan; see the log fold.`));
      }
    }
    if (s.log_tail && s.log_tail.length) {
      const det = el("details", "pl-logfold");
      det.open = running;
      det.appendChild(el("summary", null,
        owns || running || !planStamp
          ? "solver log (tail)"
          : "solver log (tail), from this run, not the plan card"));
      const pre = el("pre", "pl-log", s.log_tail.join("\n"));
      det.appendChild(pre);
      statusBox.appendChild(det);
      pre.scrollTop = pre.scrollHeight;
    }
  }
  function lastLogLine(s) {
    const t = s?.log_tail || [];
    for (let i = t.length - 1; i >= 0; i--) { const x = String(t[i]).trim(); if (x) return x; }
    return "";
  }

  let timer = null;
  async function poll() {
    if (!host.isConnected) { clearInterval(timer); return; }   // view left
    let s;
    try { s = await getJSON("/api/solve/status"); }
    catch { return; }                                          // transient miss; next tick
    solveStatus = s;
    const finished = lastState === "running" && (s.state === "done" || s.state === "failed");
    lastState = s.state;
    renderStatus();
    if (finished) {
      await fetchTplan();
      if (s.state === "done" && planFresh() && !applyBlocker(tplan.plan.chosen)) {
        applyMove(tplan.plan.chosen, "The new solver plan");
      } else {
        notice = s.state === "done" ? "Solve finished; the plan card is refreshed."
          : "Solve failed; see the solver log in the rail.";
        render();
      }
    }
  }

  // ---- solver result cards ----
  function moveRows(mv) {
    const box = el("div", "pl-moves");
    const pairs = pairMoves(mv);
    if (!pairs.length) {
      box.appendChild(el("p", "pl-roll", "Roll: bank the transfer. No move cleared the bar against rolling."));
      return box;
    }
    for (const p of pairs) {
      const row = el("div", "pl-moverow");
      const side = (pl, cls) => {
        const s = el("span", "pl-side " + cls);
        if (!pl) { s.appendChild(el("span", "pl-hint", "(none)")); return s; }
        s.appendChild(faceImg(pl.code, "avatar"));
        s.appendChild(el("span", "pl-nm", pl.name));
        s.appendChild(el("span", "pl-hint",
          [pl.pos, pl.team, pl.price != null ? fmtPrice(pl.price) : null].filter(Boolean).join(" · ")));
        return s;
      };
      row.append(side(p.out, "out"), el("span", "pl-arrow", "→"), side(p.in, "in"));
      box.appendChild(row);
    }
    return box;
  }
  function moveSummary(mv) {
    const pairs = pairMoves(mv);
    if (!pairs.length) return "roll (no move)";
    return pairs.map(p => `${p.out?.name ?? "?"} → ${p.in?.name ?? "?"}`).join(", ");
  }
  function applyButton(mv, label) {
    const holder = el("span", "pl-apply");
    if (isApplied(mv)) { holder.appendChild(el("span", "chip good", "in the grid")); return holder; }
    const why = applyBlocker(mv);
    const b = el("button", null, "Fill the grid with this");
    b.type = "button";
    if (why) { b.disabled = true; b.title = why; holder.append(b, el("span", "pl-hint", " " + why)); }
    else { b.onclick = () => applyMove(mv, label); holder.appendChild(b); }
    return holder;
  }
  function gainLine(mv, plan, opts = {}) {
    // The solver's own currency, named beside the number; never the grid's.
    const roll = plan.roll?.objective;
    const gain = mv?.gain_over_roll ?? (roll != null && mv?.objective != null ? mv.objective - roll : null);
    const h = plan.horizon_gws || [];
    const line = el("p", "pl-gain");
    line.appendChild(el("b", null, `${fmtSigned(gain)} ${plan.objective_mode || "?"}`));
    line.appendChild(document.createTextNode(
      ` over GW${h[0]}-${h.at(-1)} vs rolling, solver forecast`));
    /* How big the search actually was, beside the number it produced. It sat
       in the provenance footer while "20/position" sat in the rail, which
       read as eighty players considered rather than nine moves tried. */
    if (plan.n_candidates_screened != null) {
      const solved = plan.n_candidates_solved ?? 0;
      line.appendChild(el("span", "pl-screened",
        ` · ${solved} of ${plan.n_candidates_screened} candidate moves solved in full`));
    }
    if (opts.gap !== undefined) {
      line.appendChild(el("span", "pl-gap", " · " + (opts.gap != null
        ? `${fmt1(opts.gap)}% optimality gap (best found, not proven)` : "gap closed within tolerance")));
    }
    line.title = `the solver's own forecast in its own currency (${plan.objective_mode}); ` +
      `rolling scores ${fmt1(roll)} in the same currency`;
    return line;
  }
  function constraintsLine(plan) {
    const c = plan.constraints || {};
    const h = plan.horizon_gws || [];
    const chips = c.chips ?? (plan.chips_allowed ? CHIPS.map(([k]) => k) : []);
    const maxHits = c.max_hits ?? plan.max_hits;
    const bits = [
      `GW${h[0]}-${h.at(-1)} (${h.length} GW${h.length > 1 ? "s" : ""})`,
      maxHits == null ? null : (maxHits < 0 ? "hits unconstrained" : `hits ≤${maxHits} for the headline`),
      `chips ${chips.length ? chips.map(k => CHIP_NAME[k] || k).join("/") : "off"}`,
      `keep ${(c.must_keep || []).length ? c.must_keep.map(x => who(x).name).join(", ") : "none"}`,
      `ban ${(c.ban || []).length ? c.ban.map(x => who(x).name).join(", ") : "none"}`,
      // "150s x 20/position" is a universe cap, not the number of moves the
      // solver tried, and beside a nine-move search it read as eighty
      // players considered. The cap stays in the bounds note; the headline
      // settings line keeps only the time limit.
      c.seconds != null ? `${Math.round(c.seconds)}s per solve` : null,
    ].filter(Boolean);
    return el("p", "pl-hint pl-settings", "solved with: " + bits.join(" · "));
  }
  function sourceLine(plan) {
    const bits = [
      `source ${tplan.path || "transfer_plan.json"}`,
      `generated ${shortTs(plan.generated_at)}`
        + (fmtAge(plan.generated_at) ? ` (${fmtAge(plan.generated_at)} ago)` : ""),
      `objective ${plan.objective_mode} (surrogate; see notes)`,
      plan.solve_seconds != null ? `solved in ${Math.round(plan.solve_seconds)}s` : null,
      // the screened/solved counts moved up beside the gain, where the size
      // of the search is read
    ].filter(Boolean);
    return el("p", "provenance", bits.join(" · "));
  }
  function resolveButton(label) {
    const b = el("button", "primary", label || "Re-solve");
    b.type = "button";
    b.onclick = () => startSolve(b);
    return b;
  }
  function xiBlock(chosen) {
    const box = el("div", "pl-xi");
    const xi = new Set((chosen.starting_xi || []).map(Number));
    const cap = Number(chosen.captain), vice = Number(chosen.vice_captain);
    const mark = c => c === cap ? "C" : c === vice ? "V" : null;
    const gw = planGw();
    const rows = { GKP: [], DEF: [], MID: [], FWD: [], "?": [] };
    for (const c of xi) { const p = who(c); (rows[p.pos] || rows["?"]).push(p); }
    const pitch = el("div", "pitch pl-pitch");
    for (const pos of ["GKP", "DEF", "MID", "FWD", "?"]) {
      if (!rows[pos].length) continue;
      const row = el("div", "row");
      for (const p of rows[pos].sort((a, b) => (xp(b.code, gw) ?? -1) - (xp(a.code, gw) ?? -1))) {
        const v = xp(p.code, gw);
        row.appendChild(playerCard(p, { mark: mark(p.code),
          sub: `${fmtPrice(p.price)} · ${v == null ? "?" : fmt1(v) + " xPts"}` }));
      }
      pitch.appendChild(row);
    }
    box.appendChild(pitch);
    const capP = who(cap), viceP = who(vice);
    box.appendChild(el("p", "pl-hint",
      `Solver XI for GW${gw}: captain ${capP.name}, vice ${viceP.name} (from the plan's chosen block). ` +
      `Card numbers are the grid's consensus xPts for GW${gw}, not the solver's; the grid's own captain is its top xPts.`));
    return box;
  }
  function consistencyChecks(plan) {
    const items = [];
    const chosen = plan.chosen || {};
    if (planGw() !== res.gws[0])
      items.push(`plan solved for GW${planGw()}; the grid starts at GW${res.gws[0]}`);
    if (plan.free_transfers != null && plan.free_transfers !== res.ft_entering)
      items.push(`free transfers: solver read ${plan.free_transfers}, the grid's squad panel says ${res.ft_entering}`);
    const h = plan.horizon_gws || [];
    if (h.length && h.length !== res.gws.length)
      items.push(`plan horizon ${h.length} GWs, grid horizon ${res.gws.length} GWs: the gain is over the plan's own horizon`);
    if (isApplied(chosen)) {
      const gridBank = bankBefore(planGw());
      if (chosen.bank_after_tenths != null && chosen.bank_after_tenths !== gridBank)
        items.push(`bank after: solver ${fmtPrice(chosen.bank_after_tenths / 10)}, grid ${fmtPrice(gridBank / 10)} ` +
                   `(the grid values every player at his current price; the solver at your selling prices)`);
    }
    const squad = new Set(res.squad.map(p => p.code));
    const notHeld = (chosen.out || []).map(Number).filter(c => !squad.has(c));
    if (notHeld.length)
      items.push(`the plan sells ${notHeld.map(c => who(c).name).join(", ")}, who the grid's squad does not hold`);
    if (!items.length) return null;
    const box = el("div", "pl-checks-out");
    box.appendChild(el("b", null, "Data checks: "));
    const ul = el("ul");
    for (const t of items) ul.appendChild(el("li", null, t));
    box.appendChild(ul);
    return box;
  }

  function renderSolverCards() {
    solverCard.body.textContent = "";
    altCard.body.textContent = "";
    [...solverCard.head.querySelectorAll(".chip")].forEach(n => n.remove());
    const running = solveStatus?.state === "running";

    if (!tplan?.exists) {
      solverCard.body.appendChild(emptyBox(tplan?.reason || "no transfer plan yet",
        "Set the rail and Solve; the committed plan fills the grid."));
      altCard.hidden = true;
      return;
    }
    const plan = tplan.plan;
    // The server decides freshness, not this file. Both tabs read the same
    // verdict, so a five-hour-old plan can no longer read green here and amber
    // on the Dashboard.
    const state = tplan.state || (tplan.stale ? "stale" : "aging");
    const stateClass = { fresh: "good", aging: "warn" }[state] || "bad";
    // Same refusal the Dashboard makes: a plan whose moves were priced
    // against a different fifteen is a record, not guidance.
    if (state === "superseded") {
      const gap = el("div", "err");
      gap.appendChild(el("b", null, "This plan does not fit your squad. "));
      gap.appendChild(document.createTextNode(
        tplan.superseded_reason
        || "it was solved against a different fifteen."));
      solverCard.head.appendChild(st);
      solverCard.body.appendChild(gap);
      altCard.hidden = true;
      return;
    }
    const st = el("span", "chip " + stateClass,
      state + (tplan.age_hours != null ? ` · ${fmt1(tplan.age_hours)}h old` : ""));
    st.title = `generated ${plan.generated_at}`;
    solverCard.head.appendChild(st);

    if (tplan.stale) {
      // Honesty: a stale plan is a gap with Re-solve, never guidance.
      const gap = el("div", "err");
      gap.appendChild(el("b", null, "No standing plan. "));
      gap.appendChild(document.createTextNode(tplan.stale_reason || "the plan predates a deadline."));
      gap.appendChild(document.createTextNode(" Its moves are not shown as guidance. "));
      if (!running) gap.appendChild(resolveButton("Re-solve"));
      else gap.appendChild(el("span", "chip warn", "solving now"));
      solverCard.body.appendChild(gap);
      solverCard.body.appendChild(el("p", "provenance",
        `source ${tplan.path || "transfer_plan.json"} · generated ${shortTs(plan.generated_at)} · ` +
        `next deadline GW${tplan.next_gw ?? "?"} ${shortTs(tplan.next_deadline_utc)}`));
      altCard.hidden = true;
      return;
    }

    const chosen = plan.chosen || {};
    if (chosen.chip) {
      const lab = el("p", "pl-chipplan");
      lab.appendChild(el("span", "chip s1", `chip plan: ${CHIP_NAME[chosen.chip] || chosen.chip}`));
      lab.appendChild(document.createTextNode(
        " The headline spends a chip. That is a different decision from a transfer plan and is not drawn into the grid."));
      solverCard.body.appendChild(lab);
    }
    solverCard.body.appendChild(constraintsLine(plan));
    solverCard.body.appendChild(moveRows(chosen));
    solverCard.body.appendChild(gainLine(chosen, plan, { gap: tplan.optimality_gap_pct ?? null }));

    const strip = el("div", "stats");
    strip.append(stat(String(chosen.n_transfers ?? 0), "transfers"));
    strip.append(stat(chosen.hits ? `−${chosen.hit_points ?? chosen.hits * R.hit_cost}` : "0",
                      chosen.hits ? `hit points (${chosen.hits} hit${chosen.hits > 1 ? "s" : ""})` : "hit points",
                      chosen.hits ? "bad" : ""));
    if (chosen.bank_after_tenths != null)
      strip.append(stat(fmtPrice(chosen.bank_after_tenths / 10), "bank after (solver)"));
    if (plan.free_transfers != null)
      strip.append(stat(String(plan.free_transfers), "free transfers (solver read)"));
    solverCard.body.appendChild(strip);

    const act = el("div", "filters pl-actions");
    act.appendChild(applyButton(chosen, "The solver plan"));
    if (!running) act.appendChild(resolveButton("Re-solve with the rail's settings"));
    solverCard.body.appendChild(act);

    if (chosen.starting_xi?.length) solverCard.body.appendChild(xiBlock(chosen));
    const checks = consistencyChecks(plan);
    if (checks) solverCard.body.appendChild(checks);

    const det = el("details", "pl-notes");
    det.appendChild(el("summary", null, "solver notes and bounds"));
    for (const n of plan.notes || []) det.appendChild(el("p", "mono", n));
    if (plan.bounds) det.appendChild(el("p", "mono", plan.bounds));
    solverCard.body.appendChild(det);
    solverCard.body.appendChild(sourceLine(plan));

    // ---- second card: the unconstrained best and the beaten alternatives ----
    altCard.hidden = false;
    const u = plan.unconstrained;
    const shown = [chosen];
    if (u && !sameMove(u, chosen)) {
      shown.push(u);
      const box = el("div", "pl-uncon");
      const head = el("p", "pl-unconhead");
      head.appendChild(el("b", null, "If hits were free: "));
      head.appendChild(document.createTextNode(
        `the optimiser's top move, ${u.n_transfers} change${u.n_transfers === 1 ? "" : "s"}, ` +
        `${u.hits} hit${u.hits === 1 ? "" : "s"} (−${u.hit_points} pts already charged), `));
      head.appendChild(el("b", null, `${fmtSigned(u.gain_over_roll)} ${plan.objective_mode}`));
      head.appendChild(document.createTextNode(
        ` vs rolling. Displaced from the headline by the hit cap (${plan.max_hits < 0 ? "none" : "≤" + plan.max_hits}).`));
      box.appendChild(head);
      if (u.chip) box.appendChild(el("p", "pl-chipplan")).appendChild(
        el("span", "chip s1", `chip plan: ${CHIP_NAME[u.chip] || u.chip}`));
      box.appendChild(moveRows(u));
      box.appendChild(applyButton(u, "The unconstrained best"));
      altCard.body.appendChild(box);
    } else if (plan.max_hits != null && plan.max_hits >= 0) {
      altCard.body.appendChild(el("p", "pl-hint",
        `No move displaced by the hit cap: the headline was also the optimiser's top move within ${plan.max_hits} hit(s).`));
    }

    const alts = (plan.alternatives || []).filter(a => !shown.some(s => sameMove(s, a)));
    if (alts.length) {
      const tbl = el("table", "data pl-alttable");
      const thead = el("thead"); const hr = el("tr");
      for (const [lbl, num] of [["move it beat", 0], ["hits", 1], [`vs rolling (${plan.objective_mode})`, 1], ["", 0]])
        hr.appendChild(el("th", num ? "num" : "", lbl));
      thead.appendChild(hr); tbl.appendChild(thead);
      const tbody = el("tbody");
      const roll = plan.roll?.objective;
      for (const a of alts) {
        const tr = el("tr");
        const nameTd = el("td");
        if (a.chip) nameTd.appendChild(el("span", "chip s1", `chip plan: ${CHIP_NAME[a.chip] || a.chip}`));
        nameTd.appendChild(document.createTextNode((a.chip ? " " : "") + moveSummary(a)));
        // Not a road not taken: this row is part of the plan above it.
        if (subsetMove(a, chosen))
          nameTd.appendChild(el("div", "pl-hint",
            `part of the headline plan: ${a.n_transfers ?? (a.out || []).length} of its `
            + `${chosen.n_transfers ?? (chosen.out || []).length} transfers, scored on its own`));
        tr.appendChild(nameTd);
        tr.appendChild(el("td", "num", String(a.hits ?? 0)));
        tr.appendChild(el("td", "num", roll != null && a.objective != null ? fmtSigned(a.objective - roll) : "?"));
        const td = el("td"); td.appendChild(applyButton(a, "The alternative")); tr.appendChild(td);
        tbody.appendChild(tr);
      }
      tbl.appendChild(tbody);
      const wrap = el("div", "scroll-x"); wrap.appendChild(tbl);
      altCard.body.appendChild(wrap);
    } else {
      altCard.body.appendChild(el("p", "pl-hint", "no further alternatives in the artefact (it keeps at most five)."));
    }
    altCard.body.appendChild(el("p", "provenance",
      `same artefact and currency as the headline; each row is one click from the grid, where the grid's own ` +
      `consensus xPts, FTs and hit costs take over.`));
  }

  // ---- toolbar: plan save/load/clear + cell metric ----
  function renderToolbar() {
    toolbar.textContent = "";
    const plans = loadPlans();

    const sel = el("select");
    sel.setAttribute("aria-label", "saved plans");
    sel.appendChild(el("option", null, "saved plans"));
    for (const name of Object.keys(plans)) sel.appendChild(el("option", null, name));
    sel.onchange = () => {
      const name = sel.value;
      if (!plans[name]) return;
      moves = (plans[name].moves || []).map(m => ({ ...m }));
      nameInput.value = name;
      picking = null;
      const dropped = sanitise();
      notice = `Loaded "${name}"` + (dropped ? ` (${dropped} move(s) no longer valid, dropped)` : "") + ".";
      render();
    };

    const nameInput = el("input");
    nameInput.type = "text"; nameInput.placeholder = "plan name"; nameInput.size = 12;
    nameInput.setAttribute("aria-label", "plan name");

    const saveBtn = el("button", null, "Save");
    saveBtn.onclick = () => {
      const name = nameInput.value.trim();
      if (!name) { notice = "Name the plan before saving."; return render(); }
      const all = loadPlans();
      if (!(name in all) && Object.keys(all).length >= MAX_PLANS) {
        notice = `Plan limit is ${MAX_PLANS}; delete one first.`; return render();
      }
      all[name] = { moves, horizon, savedAt: new Date().toISOString() };
      storePlans(all);
      notice = `Saved "${name}" (${moves.length} move(s)).`;
      render();
    };

    const delBtn = el("button", null, "Delete");
    delBtn.onclick = () => {
      const name = nameInput.value.trim() || sel.value;
      const all = loadPlans();
      if (all[name]) { delete all[name]; storePlans(all); notice = `Deleted "${name}".`; }
      else notice = "No saved plan by that name.";
      render();
    };

    const clearBtn = el("button", null, "Clear moves");
    clearBtn.onclick = () => { moves = []; picking = null; notice = "Moves cleared."; render(); };

    const cmLabel = el("label", null, "cells");
    const cm = el("select"); cm.id = "pl-cells"; cmLabel.htmlFor = cm.id;
    for (const [k, m] of Object.entries(CELL_METRICS)) {
      const o = el("option", null, m.label); o.value = k;
      if (k === cellMetric) o.selected = true;
      cm.appendChild(o);
    }
    cm.onchange = () => { cellMetric = cm.value; render(); };
    toolbar.append(sel, nameInput, saveBtn, delBtn, clearBtn, cmLabel, cm);
  }

  // ---- summary strip ----
  function renderSummary() {
    summaryBox.textContent = "";
    const c = compute();
    const wrap = el("div", "scroll-x");
    const table = el("table", "data");
    const thead = el("thead"); const hr = el("tr");
    hr.appendChild(el("th", null, "plan (grid, consensus xPts)"));
    for (const g of res.gws) hr.appendChild(el("th", "num", `GW${g}`));
    thead.appendChild(hr); table.appendChild(thead);
    const tbody = el("tbody");
    const row = (label, cell) => {
      const tr = el("tr");
      tr.appendChild(el("td", null, label));
      for (const p of c.perGw) { const td = el("td", "num"); cell(td, p); tr.appendChild(td); }
      tbody.appendChild(tr);
    };
    row("XI xPts, captain doubled", (td, p) => {
      td.textContent = fmt1(p.xi);
      td.title = p.capName
        ? `doubling ${p.capName}, the XI's top xPts this gameweek`
        : "captain doubled";
    });
    // Named, not implied: the row above doubles this player, and it is not
    // necessarily the one badged with your locked armband.
    row("doubled", (td, p) => {
      td.textContent = p.capName || "–";
      td.classList.add("pl-capname");
    });
    row("transfers", (td, p) => td.textContent = String(p.transfers));
    row("FTs used / available", (td, p) => td.textContent = `${p.ftUsed}/${p.ftAvail}`);
    row("hit pts", (td, p) => {
      td.textContent = p.hits ? `−${p.hits * R.hit_cost}` : "0";
      if (p.hits) td.style.color = "var(--bad)";
    });
    row("bank", (td, p) => {
      td.textContent = fmtPrice(p.bank / 10);
      if (p.bank < 0) td.style.color = "var(--bad)";
    });
    table.appendChild(tbody); wrap.appendChild(table); summaryBox.appendChild(wrap);

    const strip = el("div", "stats");
    strip.append(stat(fmt1(c.net), "net xPts (grid)", "good"));
    strip.append(stat(fmt1(c.totalX), "gross xPts (grid)"));
    strip.append(stat(c.totalHits ? `−${c.totalHits * R.hit_cost}` : "0",
                      "hit points (grid)", c.totalHits ? "bad" : ""));
    const endBank = c.perGw.at(-1)?.bank ?? res.bank_tenths;
    strip.append(stat(fmtPrice(endBank / 10), "bank at end (grid)",
                      c.perGw.some(p => p.bank < 0) ? "bad" : ""));
    strip.append(stat(String(moves.length), "moves planned"));
    summaryBox.appendChild(strip);
    // The grid and the solver count different things, and the page used to
    // print both totals with nothing saying so: the reader was left to
    // reconcile a 15-point gap by themselves. The grid sums the eleven with
    // the top xPts doubled. The solver's objective adds the bench at fixed
    // autosub weights and picks its own lineup in every gameweek, so it is
    // structurally the larger number.
    if (tplan?.plan?.chosen?.objective != null) {
      const solverX = Number(tplan.plan.chosen.objective);
      const line = el("p", "sub pl-reconcile");
      line.append(
        `The solver scores this plan at ${fmt1(solverX)} over the same `
        + `gameweeks, ${fmt1(solverX - c.totalX)} above the grid. The grid `
        + `sums the eleven with the top xPts doubled; the solver adds the `
        + `bench at fixed autosub weights and chooses its own XI each `
        + `gameweek. Two objectives, not two answers to one question.`);
      summaryBox.appendChild(line);
    }
    if (c.perGw.some(p => p.bank < 0))
      summaryBox.appendChild(el("p", "sub")).appendChild(
        el("span", "chip bad", "bank goes negative: plan is not affordable"));
  }

  // ---- the pitch: the FPL-site select-and-remove idiom ----
  function renderPitch() {
    pitchBox.textContent = "";
    const box = el("div");

    // GW tabs
    const tabs = el("div", "filters");
    tabs.appendChild(el("label", null, "plan for"));
    for (const g of res.gws) {
      const chip = el("button", "chip" + (g === pitchGw ? " s1" : ""), `GW${g}`);
      chip.type = "button";
      chip.setAttribute("aria-pressed", g === pitchGw ? "true" : "false");
      chip.onclick = () => { pitchGw = g; picking = null; adding = null; render(); };
      tabs.appendChild(chip);
    }
    tabs.appendChild(el("span", "sub", "  the squad entering this gameweek"));
    box.appendChild(tabs);

    const codes = squadAt(pitchGw);
    const outThisGw = new Map(moves.filter(m => m.gw === pitchGw)
                                   .map(m => [m.in, m]));
    const players = [...codes].map(c => byCode.get(c)).filter(Boolean);
    const byPos = { GKP: [], DEF: [], MID: [], FWD: [] };
    for (const pl of players) (byPos[pl.pos] || byPos.MID).push(pl);
    for (const k in byPos)
      byPos[k].sort((a, b) => (xp(b.code, pitchGw) ?? -1) - (xp(a.code, pitchGw) ?? -1));

    const pitch = el("div", "pitch pitch-select");
    for (const posRow of ["GKP", "DEF", "MID", "FWD"]) {
      const row = el("div", "row");
      for (const pl of byPos[posRow]) {
        if (picking && picking.out === pl.code) {
          // FPL's empty shirt: the slot stays open until a replacement is picked
          row.appendChild(placeholderCard(pl));
          continue;
        }
        const inMove = outThisGw.get(pl.code);      // this player IS an incoming
        const cardEl = playerCard(pl, {
          mark: pl.is_captain ? "C" : null,
          sub: `${fmtPrice(pl.price)} · ${fmt1(xp(pl.code, pitchGw)) ?? "?"}`,
        });
        cardEl.classList.add("selectable");
        cardEl.tabIndex = 0;
        cardEl.setAttribute("role", "button");
        cardEl.setAttribute("aria-haspopup", "menu");
        if (inMove) {
          cardEl.classList.add("incoming");
          cardEl.title = `${pl.name}: in for ${byCode.get(inMove.out)?.name ?? inMove.out}; click for undo, keep or ban`;
        } else {
          cardEl.title = `${pl.name}: remove, keep or ban (GW${pitchGw})`;
        }
        cardEl.onclick = () => openMenu(cardEl, pl.code, pitchGw);
        cardEl.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); cardEl.click(); } };
        row.appendChild(cardEl);
      }
      if (byPos[posRow].length) pitch.appendChild(row);
    }
    box.appendChild(pitch);
    pitchBox.appendChild(box);
  }

  let removeMoveByRef = () => {};       // bound inside renderGrid (shares cascade)

  /* The empty slot a removed player leaves on the pitch (FPL's blank shirt),
     with undo; clicking it scrolls to the replacement picker. */
  function placeholderCard(pl) {
    const d = el("div", "pcard pl-empty");
    d.tabIndex = 0;
    d.setAttribute("role", "button");
    d.title = `${pl.pos} slot open: pick a replacement for ${pl.name}`;
    d.appendChild(el("div", "pl-emptypos", pl.pos));
    d.appendChild(el("div", "nm", "pick a replacement"));
    d.appendChild(el("div", "sub", `for ${pl.name}`));
    const undo = el("button", "chip pl-undo", "undo");
    undo.type = "button";
    undo.setAttribute("aria-label", `undo removing ${pl.name}`);
    undo.onclick = e => { e.stopPropagation(); picking = null; notice = `${pl.name} stays.`; render(); };
    d.appendChild(undo);
    d.onclick = () => pickerBox.scrollIntoView({ behavior: "smooth", block: "start" });
    d.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); d.click(); } };
    return d;
  }

  // ---- player action menu: the FPL transfers-page idiom ----
  // One small menu anchored to the clicked player: Remove (transfer out from
  // that GW, then the same-position picker), Keep and Ban (the rail's solver
  // constraints, reflected there at once), or undo of a planned move.
  let menu = null;                    // {box, anchor, off} while open
  function closeMenu(refocus) {
    if (!menu) return;
    const { box, anchor, off } = menu;
    menu = null; off();
    box.remove();
    if (anchor?.isConnected) {
      anchor.setAttribute("aria-expanded", "false");
      if (refocus) anchor.focus();
    }
  }
  function openMenu(anchor, code, gw) {
    if (menu && menu.anchor === anchor) { closeMenu(true); return; }
    closeMenu(false);
    const p = byCode.get(code);
    if (!p) return;
    const box = el("div", "pl-menu");
    box.setAttribute("role", "menu");
    const headId = `pl-menuhead-${code}`;
    box.setAttribute("aria-labelledby", headId);
    const head = el("div", "pl-menuhead"); head.id = headId;
    head.appendChild(faceImg(code, "avatar"));
    const txt = el("div", "pl-menutext");
    txt.appendChild(el("div", "pl-menunm", p.name));
    const v = xp(code, gw);
    txt.appendChild(el("div", "pl-hint",
      [p.pos, p.team ?? "?", fmtPrice(p.price), `GW${gw}: ${v == null ? "?" : fmt1(v)} xPts`].join(" · ")));
    head.appendChild(txt);
    box.appendChild(head);

    const items = [];
    const item = (label, hint, cls, act) => {
      const b = el("button", "pl-menuitem" + (cls ? " " + cls : ""));
      b.type = "button"; b.setAttribute("role", "menuitem");
      b.appendChild(el("span", "pl-menulbl", label));
      if (hint) b.appendChild(el("span", "pl-hint", hint));
      b.onclick = () => { closeMenu(false); act(); };
      box.appendChild(b); items.push(b);
    };
    const inMove = moves.find(m => m.in === code);
    const outMove = moves.find(m => m.out === code);
    const inSquad = res.squad.some(s => s.code === code);
    if (!outMove && squadAt(gw).has(code)) {
      item("Remove", `transfer out from GW${gw}, then pick a ${p.pos}`, "bad", () => {
        picking = { gw, out: code };
        poolPos = p.pos;                // the pool follows, FPL-site style
        notice = `${p.name} removed from GW${gw}: pick a replacement ${p.pos}, or undo.`;
        render();
        pickerBox.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    if (outMove)
      item("Undo transfer out", `out in GW${outMove.gw} for ${byCode.get(outMove.in)?.name ?? outMove.in}`, "",
           () => removeMoveByRef(outMove));
    if (inMove)
      item("Undo transfer in", `in for ${byCode.get(inMove.out)?.name ?? inMove.out} in GW${inMove.gw}`, "",
           () => removeMoveByRef(inMove));
    if (inSquad) {
      const kept = rail.must_keep.includes(code);
      item(kept ? "Release keep" : "Keep",
           kept ? "the solver may sell him again" : "the solver locks him in every GW of the horizon", "", () => {
        rail.must_keep = kept ? rail.must_keep.filter(c => c !== code) : [...rail.must_keep, code];
        if (!kept) rail.ban = (rail.ban || []).filter(c => c !== code);
        persist(); renderRail();
        notice = kept ? `${p.name} released: the solver may sell him.`
                      : `${p.name} kept: the solver locks him in every GW (see the rail).`;
        noteLine.textContent = notice;
      });
    }
    const banned = (rail.ban || []).includes(code);
    item(banned ? "Unban" : "Ban",
         banned ? "the solver may own him again" : "never owned by the solver; sold in the first GW if held", "", () => {
      rail.ban = banned ? rail.ban.filter(c => c !== code) : [...(rail.ban || []), code];
      if (!banned) rail.must_keep = rail.must_keep.filter(c => c !== code);
      persist(); renderRail();
      notice = banned ? `${p.name} unbanned.` : `${p.name} banned: the solver never owns him (see the rail).`;
      noteLine.textContent = notice;
    });

    box.onkeydown = e => {
      const i = items.indexOf(document.activeElement);
      const n = items.length;
      if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); closeMenu(true); }
      else if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % n].focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + n) % n].focus(); }
      else if (e.key === "Home") { e.preventDefault(); items[0].focus(); }
      else if (e.key === "End") { e.preventDefault(); items[n - 1].focus(); }
    };
    const onDown = e => { if (!box.contains(e.target) && !anchor.contains(e.target)) closeMenu(false); };
    const onKey = e => { if (e.key === "Escape" && !box.contains(e.target)) closeMenu(true); };
    const onMove = () => closeMenu(false);
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", onMove, { capture: true, passive: true });
    window.addEventListener("resize", onMove);
    const off = () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", onMove, { capture: true });
      window.removeEventListener("resize", onMove);
    };
    menu = { box, anchor, off };
    anchor.setAttribute("aria-expanded", "true");
    host.appendChild(box);
    // fixed to the viewport, beside the anchor, flipped up or left when short of room
    const r = anchor.getBoundingClientRect();
    const w = box.offsetWidth, h = box.offsetHeight;
    let left = r.left, top = r.bottom + 4;
    if (left + w > window.innerWidth - 8) left = Math.max(8, window.innerWidth - 8 - w);
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 4);
    box.style.left = `${Math.round(left)}px`;
    box.style.top = `${Math.round(top)}px`;
    items[0]?.focus();
  }

  // ---- candidate picker ----
  function renderPicker() {
    pickerBox.textContent = "";
    if (!picking) return;
    const out = byCode.get(picking.out);
    const box = el("div", "card");
    box.appendChild(el("h2", null,
      `Replace ${out?.name ?? picking.out} (${out?.pos ?? "?"}) from GW${picking.gw}`));
    const funds = bankBefore(picking.gw) + (out ? tenths(out) : 0);
    box.appendChild(el("p", "sub",
      `Funds: ${fmtPrice(funds / 10)} (bank after earlier moves + sale at current price). ` +
      `Same position only, sorted by consensus xPts over the rest of the horizon.`));

    const search = el("input");
    search.type = "text"; search.placeholder = "search player / team"; search.size = 20;
    search.setAttribute("aria-label", "search a replacement");
    box.appendChild(el("div", "filters")).append(search,
      (() => { const b = el("button", null, "Undo remove");
               b.onclick = () => { picking = null; notice = `${out?.name ?? "the player"} stays.`; render(); }; return b; })());

    const listBox = el("div", "scroll-x");
    box.appendChild(listBox);

    const inSquad = squadAt(picking.gw);
    const remaining = res.gws.filter(g => g >= picking.gw);
    const pool = res.candidates
      .filter(cd => cd.pos === out?.pos && !inSquad.has(cd.code))
      .map(cd => ({ ...cd,
        sum: remaining.reduce((a, g) => a + (xp(cd.code, g) ?? 0), 0) }))
      .sort((a, b) => b.sum - a.sum);

    function renderList() {
      listBox.textContent = "";
      const term = search.value.trim().toLowerCase();
      const rows = pool.filter(cd => !term ||
        cd.name.toLowerCase().includes(term) || (cd.team || "").toLowerCase().includes(term))
        .slice(0, 30);
      if (!rows.length) { listBox.appendChild(emptyBox("no matching candidate")); return; }
      const table = el("table", "data");
      const thead = el("thead"); const hr = el("tr");
      for (const [lbl, num] of [["player", 0], ["team", 0], ["price", 1],
                                ["owned %", 1], [`ΣxPts GW${picking.gw}-${remaining.at(-1)}`, 1], ["", 0]])
        hr.appendChild(el("th", num ? "num" : "", lbl));
      thead.appendChild(hr); table.appendChild(thead);
      const tbody = el("tbody");
      for (const cd of rows) {
        const short = tenths(cd) > funds;
        const tr = el("tr");
        const nameTd = el("td");
        nameTd.appendChild(faceImg(cd.code, "avatar"));
        nameTd.appendChild(document.createTextNode(cd.name));
        tr.appendChild(nameTd);
        tr.appendChild(el("td", null, cd.team ?? "?"));
        tr.appendChild(el("td", "num", fmtPrice(cd.price)));
        tr.appendChild(el("td", "num", cd.own_pct == null ? "?" : fmt1(cd.own_pct)));
        tr.appendChild(el("td", "num", fmt2(cd.sum)));
        const act = el("td");
        if (short) act.appendChild(el("span", "chip bad", "£ short"));
        else {
          const b = el("button", null, "in");
          b.onclick = () => {
            const gw = picking.gw;
            moves.push({ gw, out: picking.out, in: cd.code });
            moves.sort((a, b2) => a.gw - b2.gw);
            picking = null;
            notice = `GW${gw}: planned ${out?.name} → ${cd.name}.`;
            render();
          };
          act.appendChild(b);
        }
        tr.appendChild(act);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody); listBox.appendChild(table);
    }
    search.oninput = renderList;
    renderList();
    pickerBox.appendChild(box);
    search.focus();
  }

  // ---- the grid ----
  function xpCell(td, code, gw) {
    const M = CELL_METRICS[cellMetric];
    const v = M.get(code, gw);
    if (v == null) { td.textContent = "?"; return; }
    const pct = Math.min(65, Math.round(65 * v / M.max()));
    td.textContent = M.fmt(v);
    td.style.background = `color-mix(in oklab, var(--s1) ${pct}%, var(--surface))`;
    if (pct > 55) td.style.color = "#fff";
    const bits = [`xPts ${fmt2(xp(code, gw))}`];
    const sp = sprd(code, gw); if (sp != null) bits.push(`spread ${fmt2(sp)}`);
    const pa = pApp(code, gw); if (pa != null) bits.push(`p(app) ${fmt2(pa)}`);
    const m = met(code);
    if (m?.xg != null) bits.push(`xG ${fmt2(m.xg)} · ${m.shots ?? 0} shots so far`);
    td.title = bits.join(" · ");
  }

  function renderGrid() {
    gridBox.textContent = "";
    const outAt = new Map(moves.map(m => [m.out, m]));   // code -> move (out)
    const inAt = new Map(moves.map(m => [m.in, m]));     // code -> move (in)

    const wrap = el("div", "scroll-x");
    const table = el("table", "data sticky-first pl-grid");
    const thead = el("thead"); const hr = el("tr");
    for (const lbl of ["player", "pos", "team"]) hr.appendChild(el("th", null, lbl));
    hr.appendChild(el("th", "num", "price"));
    for (const g of res.gws) hr.appendChild(el("th", "num", `GW${g}`));
    thead.appendChild(hr); table.appendChild(thead);
    const tbody = el("tbody");

    const removeMove = (m) => {
      // Cascade: a later move selling this move's incoming player dies with it.
      const dead = new Set([m]);
      let grew = true;
      while (grew) {
        grew = false;
        for (const other of moves)
          if (!dead.has(other) && [...dead].some(d => other.out === d.in && other.gw >= d.gw)) {
            dead.add(other); grew = true;
          }
      }
      moves = moves.filter(x => !dead.has(x));
      notice = dead.size > 1 ? `Removed ${dead.size} linked move(s).` : "Move removed.";
      render();
    };
    removeMoveByRef = removeMove;

    const playerRow = (p, joinedGw) => {
      const tr = el("tr");
      const outMove = outAt.get(p.code);
      const picked = !!picking && picking.out === p.code;   // removed, slot open
      if (outMove) tr.classList.add("pl-outrow");
      if (joinedGw != null) tr.classList.add("pl-inrow");
      if (picked) tr.classList.add("pl-pickrow");
      const nameTd = el("td");
      if (picked) {
        nameTd.appendChild(el("span", "pl-placeholder", `${p.pos}: pick a replacement`));
        nameTd.appendChild(document.createTextNode(" "));
        const undo = el("button", "chip pl-undo", "undo");
        undo.type = "button";
        undo.setAttribute("aria-label", `undo removing ${p.name}`);
        undo.onclick = () => { picking = null; notice = `${p.name} stays.`; render(); };
        nameTd.appendChild(undo);
        nameTd.title = `${p.name} removed from GW${picking.gw}; pick a replacement above or undo`;
      } else {
        nameTd.appendChild(faceImg(p.code, "avatar"));
        const nm = el("span", outMove ? "pl-struck" : "", p.name);
        if (p.is_captain) {
          const armband = el("i", "pl-armband", "C");
          armband.title = "your locked FPL armband. The grid's own total "
            + "doubles the XI's top xPts each gameweek, named in the "
            + "'doubled' row, which is not always this player.";
          nm.appendChild(armband);
        }
        nameTd.appendChild(nm);
        nameTd.classList.add("pl-playerbtn");
        nameTd.tabIndex = 0;
        nameTd.setAttribute("role", "button");
        nameTd.setAttribute("aria-haspopup", "menu");
        nameTd.title = `${p.name}: remove, keep or ban (GW${pitchGw})`;
        nameTd.onclick = () => openMenu(nameTd, p.code, pitchGw);
        nameTd.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); nameTd.click(); } };
      }
      if (joinedGw != null) {
        nameTd.appendChild(document.createTextNode(" "));
        nameTd.appendChild(el("span", "chip good", `IN GW${joinedGw}`));
      }
      if (outMove) {
        nameTd.appendChild(document.createTextNode(" "));
        nameTd.appendChild(el("span", "chip bad", `OUT GW${outMove.gw}`));
      }
      tr.appendChild(nameTd);
      tr.appendChild(el("td", null, p.pos));
      tr.appendChild(el("td", null, p.team ?? "?"));
      tr.appendChild(el("td", "num", fmtPrice(p.price)));
      for (const g of res.gws) {
        const td = el("td", "num");
        const joined = joinedGw == null || g >= joinedGw;
        if (!joined) { td.textContent = "·"; td.style.color = "var(--faint)"; }
        else if (picked && g > picking.gw) { td.textContent = "·"; td.style.color = "var(--faint)"; }
        else if (picked && g === picking.gw) {
          const chip = el("button", "chip warn pl-pickchip", "pick a replacement");
          chip.type = "button";
          chip.title = "the slot is open until a replacement is chosen";
          chip.onclick = () => pickerBox.scrollIntoView({ behavior: "smooth", block: "start" });
          td.appendChild(chip);
        }
        else if (picked) { xpCell(td, p.code, g); }
        else if (outMove && g > outMove.gw) { td.textContent = "·"; td.style.color = "var(--faint)"; }
        else if (outMove && g === outMove.gw) {
          const inn = byCode.get(outMove.in);
          const chip = el("button", "chip bad", `OUT → ${inn?.name ?? outMove.in}`);
          chip.type = "button";
          chip.title = "click to undo this transfer";
          chip.onclick = () => removeMove(outMove);
          td.appendChild(chip);
        } else {
          xpCell(td, p.code, g);
          td.classList.add("clickable");
          td.tabIndex = 0;
          td.setAttribute("aria-haspopup", "menu");
          td.title = (td.title ? td.title + " · " : "") + `click to remove, keep or ban (GW${g})`;
          td.onclick = () => openMenu(td, p.code, g);
          td.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); td.click(); } };
        }
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    };

    const squadSorted = [...res.squad].sort((a, b) =>
      (POS_ORDER[a.pos] ?? 9) - (POS_ORDER[b.pos] ?? 9) || b.price - a.price);
    for (const p of squadSorted) playerRow(p, null);
    for (const m of moves) {
      const p = byCode.get(m.in);
      if (p && inAt.get(m.in) === m) playerRow(p, m.gw);
    }

    table.appendChild(tbody); wrap.appendChild(table);
    gridBox.appendChild(wrap);
  }

  function renderPool() {
    poolBox.textContent = "";
    const box = el("div", "card");
    /* The heading counted the whole universe while the footer counted the
       rows, so "all 639 players" stood over "showing 40 of 637": the two
       players the plan buys are in the squad by the time the list is built
       and were subtracted from one number but not the other. Both now count
       the same set. */
    const poolGwNow = poolGw ?? res.gws[0];
    const heldNow = squadAt(poolGwNow);
    const addable = res.candidates.filter(c => !heldNow.has(c.code)).length;
    box.appendChild(el("h2", null,
      `Player pool: ${addable} players you do not hold in GW${poolGwNow}`));
    box.appendChild(el("p", "sub",
      `Browse anyone, add from the list. ${res.metrics_note || ""}`));

    // -- filter row --
    const f = el("div", "filters");
    const search = el("input"); search.type = "text";
    search.placeholder = "search player / team"; search.size = 16;
    search.setAttribute("aria-label", "search the pool");
    search.value = poolSearch;
    search.oninput = () => { poolSearch = search.value; renderPool(); };

    const posSel = el("select"); posSel.setAttribute("aria-label", "position");
    for (const v of ["", "GKP", "DEF", "MID", "FWD"])
      posSel.appendChild(Object.assign(el("option", null, v || "all positions"), { value: v }));
    posSel.value = poolPos;
    posSel.onchange = () => { poolPos = posSel.value; renderPool(); };

    const teams = [...new Set(res.candidates.map(c => c.team).filter(Boolean))].sort();
    const teamSel = el("select"); teamSel.setAttribute("aria-label", "team");
    teamSel.appendChild(Object.assign(el("option", null, "all teams"), { value: "" }));
    for (const t of teams) teamSel.appendChild(Object.assign(el("option", null, t), { value: t }));
    teamSel.value = poolTeam;
    teamSel.onchange = () => { poolTeam = teamSel.value; renderPool(); };

    const maxIn = el("input"); maxIn.type = "number"; maxIn.step = "0.5";
    maxIn.placeholder = "max £"; maxIn.style.width = "68px"; maxIn.value = poolMax;
    maxIn.setAttribute("aria-label", "maximum price");
    maxIn.oninput = () => { poolMax = maxIn.value; renderPool(); };

    const sortSel = el("select"); sortSel.setAttribute("aria-label", "sort");
    for (const [k, c] of Object.entries(POOL_COLS))
      sortSel.appendChild(Object.assign(el("option", null, `sort: ${c.label}`), { value: k }));
    sortSel.value = poolSort;
    sortSel.onchange = () => { poolSort = sortSel.value; renderPool(); };

    const gwSel = el("select"); gwSel.setAttribute("aria-label", "gameweek to add in");
    for (const g of res.gws)
      gwSel.appendChild(Object.assign(el("option", null, `add in GW${g}`), { value: g }));
    gwSel.value = String(poolGw ?? res.gws[0]);
    gwSel.onchange = () => { poolGw = Number(gwSel.value); renderPool(); };

    f.append(search, posSel, teamSel, maxIn, sortSel, gwSel);
    box.appendChild(f);

    // -- metric column toggles --
    const cols = el("div", "filters");
    cols.appendChild(el("label", null, "columns:"));
    for (const [k, c] of Object.entries(POOL_COLS)) {
      const chip = el("button", "chip" + (poolCols.includes(k) ? " s1" : ""), c.label);
      chip.type = "button";
      chip.setAttribute("aria-pressed", poolCols.includes(k) ? "true" : "false");
      chip.onclick = () => {
        poolCols = poolCols.includes(k)
          ? poolCols.filter(x => x !== k) : [...poolCols, k];
        renderPool();
      };
      cols.appendChild(chip);
    }
    box.appendChild(cols);

    // -- rows --
    const gw = poolGwNow;
    const remaining = res.gws.filter(g => g >= gw);
    const inSquadNow = heldNow;
    const term = poolSearch.trim().toLowerCase();
    let rows = res.candidates
      .filter(c => !inSquadNow.has(c.code))
      .filter(c => !poolPos || c.pos === poolPos)
      .filter(c => !poolTeam || c.team === poolTeam)
      .filter(c => !poolMax || c.price <= Number(poolMax))
      .filter(c => !term || c.name.toLowerCase().includes(term)
                         || (c.team || "").toLowerCase().includes(term))
      .map(c => ({ ...c,
        _xsum: remaining.reduce((a, g) => a + (xp(c.code, g) ?? 0), 0) }));
    const S = POOL_COLS[poolSort];
    rows.sort((a, b) => ((S.get(b) ?? -1e9) - (S.get(a) ?? -1e9)));
    const total = rows.length;
    rows = rows.slice(0, 40);

    const wrap = el("div", "scroll-x");
    const table = el("table", "data");
    const thead = el("thead"); const hr = el("tr");
    hr.appendChild(el("th", null, "player"));
    hr.appendChild(el("th", null, "pos"));
    hr.appendChild(el("th", null, "team"));
    hr.appendChild(el("th", "num", "price"));
    for (const k of poolCols) hr.appendChild(el("th", "num", POOL_COLS[k].label));
    hr.appendChild(el("th", null, ""));
    thead.appendChild(hr); table.appendChild(thead);
    const tbody = el("tbody");

    for (const c of rows) {
      const tr = el("tr");
      const nameTd = el("td");
      nameTd.appendChild(faceImg(c.code, "avatar"));
      nameTd.appendChild(document.createTextNode(c.name));
      tr.appendChild(nameTd);
      tr.appendChild(el("td", null, c.pos));
      tr.appendChild(el("td", null, c.team ?? "?"));
      tr.appendChild(el("td", "num", fmtPrice(c.price)));
      for (const k of poolCols) {
        const v = POOL_COLS[k].get(c);
        tr.appendChild(el("td", "num", v == null ? "?" : POOL_COLS[k].fmt(v)));
      }
      const act = el("td");
      const addBtn = el("button", null, "+ add");
      addBtn.setAttribute("aria-expanded", adding === c.code ? "true" : "false");
      addBtn.onclick = () => { adding = adding === c.code ? null : c.code; renderPool(); };
      act.appendChild(addBtn);
      tr.appendChild(act);
      tbody.appendChild(tr);

      // -- inline sell chooser --
      if (adding === c.code) {
        const sellTr = el("tr");
        const td = el("td");
        td.colSpan = 5 + poolCols.length;
        td.appendChild(el("b", null, `Sell whom for ${c.name} in GW${gw}? `));
        const sameNow = [...squadAt(gw)]
          .map(code => byCode.get(code))
          .filter(pl => pl && pl.pos === c.pos);
        if (!sameNow.length)
          td.appendChild(el("span", "chip bad", `no ${c.pos} to sell`));
        for (const pl of sameNow) {
          const bankAfter = bankBefore(gw) + tenths(pl) - tenths(c);
          const chip = el("button",
            "chip " + (bankAfter < 0 ? "bad" : "good"),
            `${pl.name} → bank ${fmtPrice(bankAfter / 10)}`);
          chip.type = "button";
          chip.style.marginRight = "6px";
          chip.title = bankAfter < 0 ? "not affordable" : "click to plan this transfer";
          if (bankAfter < 0) chip.disabled = true;
          else chip.onclick = () => {
            moves.push({ gw, out: pl.code, in: c.code });
            moves.sort((a, b) => a.gw - b.gw);
            adding = null;
            notice = `GW${gw}: planned ${pl.name} → ${c.name}.`;
            render();
          };
          td.appendChild(chip);
        }
        sellTr.appendChild(td);
        tbody.appendChild(sellTr);
      }
    }
    table.appendChild(tbody); wrap.appendChild(table);
    box.appendChild(wrap);
    if (total > rows.length)
      box.appendChild(el("p", "sub",
        `showing ${rows.length} of ${total} after filters; search or filter to narrow`));
    poolBox.appendChild(box);
  }

  function render() {
    closeMenu(false);
    renderSolverCards();
    renderToolbar();
    renderSummary();
    renderPitch();
    renderPicker();
    renderGrid();
    renderPool();
    noteLine.textContent = notice;
  }

  // ---- first paint: the standing plan fills the grid when it can ----
  renderRail();
  try { solveStatus = await getJSON("/api/solve/status"); lastState = solveStatus.state; }
  catch { solveStatus = null; }
  renderStatus();
  if (planFresh() && !moves.length && !applyBlocker(tplan.plan.chosen)) {
    applyMove(tplan.plan.chosen, "The standing solver plan");
    notice += " Clear moves to plan by hand.";
    noteLine.textContent = notice;
  } else {
    render();
  }
  timer = setInterval(poll, POLL_MS);
}
