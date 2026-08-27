/* Squad-vs-field diff and the what-if exposure simulator.
 *
 * Owned separately from template.js so the two halves of the Template tab can
 * be built in parallel. template.js calls renderTools(host, ctx) once, after
 * its own cards; everything below that call belongs to this module.
 *
 * ctx is the seam. It carries, at minimum:
 *   ctx.res      the full ownership_eo payload (fields[], rows[], squad, ...)
 *   ctx.fieldKey the field the page is currently measuring against
 *   ctx.measure  "eo" | "own"
 *   ctx.onFocus  (code) => void   ask the page to open its player drawer
 * template.js re-invokes renderTools whenever the selection changes, so this
 * module renders from ctx and holds no cross-render state of its own. The
 * simulator's picks live in a closure created fresh by each call, so changing
 * the field or the measure resets the swap — which is correct: a swap is only
 * meaningful against one field.
 *
 * ---------------------------------------------------------------------------
 * WHY THESE TWO TOOLS LOOK LIKE THIS
 *
 * The page's identity is  rank move ≈ Σ (your multiplier − the field's EO) × points.
 * Both halves of the subtraction are MULTIPLIERS, which is the one fact that
 * shaped every decision here:
 *
 *   - The diff is a DUMBBELL on a shared multiplier axis, not two magnitude
 *     bars. Your 1× and the field's 0.83 are the same kind of number, so they
 *     belong on one track; the segment between them IS the term, and "covered",
 *     "hole" and "differential" become distances rather than numbers to compare.
 *     Ownership never orders the list — the term does, in both directions.
 *   - Head-count `own` is NOT a multiplier. Every number in this module reads
 *     the field's `eo`, whichever measure the page above is showing, and says
 *     so; a field with no EO gets an explained empty state instead of a units
 *     error.
 *   - A percentage is meaningless without its population (Opta's rule for
 *     radars: percentiles, not raw values). "58% EO" means nothing until you
 *     know that only ~70 players in the game clear 1% at all. So the swarm
 *     plots the field's whole held distribution and every row carries a
 *     percentile against a NAMED denominator.
 *   - No grand totals. Σ of this field's EO across the players on this page
 *     comes to more multipliers than a legal squad can apply (the crawl cannot
 *     identify every bench place; the modelled feeds carry no squad-sum
 *     constraint). That is measurable from the payload, so the card says it,
 *     and every aggregate below is over a basis this page names out loud.
 *
 * WHERE THE NUMBERS COME FROM. Both tools read `rows[].fields[ctx.fieldKey]`
 * and `differentials[]`, because the seam is field-KEYED: the reader can point
 * these cards at any field in the ladder and the per-field measurements are
 * the only source that answers for all of them. The panel's own `diff` and
 * `whatif.players` carry the SELECTED field only, so they cannot serve a
 * fieldKey the page has switched to. What this module does take from `whatif`
 * is the part only the panel can know: `safe_to_recompute` and
 * `not_safe_to_recompute` are rendered verbatim in the simulator's rail, and
 * `selection.includes_you` decides whether "your transfer does not move the
 * field" may be said at all. Where the payload publishes neither, the rail
 * names the assumption instead of asserting the fact.
 */

import { el, emptyBox, faceImg, fmtPrice, fmt1 } from "/js/app.js";

/* ---------------------------------------------------------------- helpers */

const NS = "http://www.w3.org/2000/svg";
function sv(tag, attrs, text) {
  const n = document.createElementNS(NS, tag);
  for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
  if (text != null) n.textContent = text;
  return n;
}

const pct = v => v == null ? "–" : `${Number(v).toFixed(1)}%`;
const sgn2 = v => v == null ? "–"
  : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}`;
const mult = v => v == null ? "–" : `${Number(v).toFixed(2)}×`;
function ord(n) {
  if (n == null) return "–";
  const r = Math.round(n), t = r % 100;
  const s = (t >= 11 && t <= 13) ? "th"
          : ["th", "st", "nd", "rd"][r % 10] || "th";
  return `${r}${s}`;
}

/* The same multiplier vocabulary template.js uses, so a number means one thing
   on both halves of the tab. A read that carried a real multiplier is used as
   given; otherwise the role is converted with the standard weights and the
   result is FLAGGED as assumed (a triple captain would make it 3, and neither
   half of this page can see chips). "Not owned" is a measured 0. */
const ROLE_MULT = { captain: 2, start: 1, bench: 0 };
const ROLE_NAME = { captain: "captain 2×", start: "starting 1×", bench: "benched 0×" };
function myMult(r) {
  if (!r) return { v: null, assumed: false };
  if (r.your_mult != null) return { v: r.your_mult, assumed: false };
  if (r.your_role && ROLE_MULT[r.your_role] != null)
    return { v: ROLE_MULT[r.your_role], assumed: true };
  if (r.in_squad === false) return { v: 0, assumed: false };
  return { v: null, assumed: false };
}

/* Percentile against an ASCENDING array, reported as "share of the population
   at or below this value". Ties resolve upward, so the single heaviest player
   reads 100th and the statement stays true for every reader. */
function pctileIn(sorted, v) {
  if (v == null || !sorted.length) return null;
  let lo = 0, hi = sorted.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (sorted[m] <= v) lo = m + 1; else hi = m; }
  return 100 * lo / sorted.length;
}
function quantile(sorted, q) {
  if (!sorted.length) return null;
  const i = (sorted.length - 1) * q, lo = Math.floor(i), hi = Math.ceil(i);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
}

/* Every player this field holds at all. Under 1% EO fewer than one manager in
   a hundred has any exposure to him, so he is not part of the field's template
   in any sense — and on this payload that is two thirds of the rows. The floor
   is stated on the page next to every number computed over it. */
const HELD_FLOOR = 1;      // % EO
const TOP_N = 20;          // the field's template, for the cover basis

/* ------------------------------------------------------------------ view */

export function renderTools(host, ctx) {
  host.textContent = "";
  if (!ctx || !ctx.res) return;

  const res = ctx.res;
  const fields = res.fields || [];
  const byKey = Object.fromEntries(fields.map(f => [f.key, f]));
  const field = byKey[ctx.fieldKey];
  const focus = typeof ctx.onFocus === "function" ? ctx.onFocus : () => {};

  const diffCard = el("section", "card tt");
  const simCard = el("section", "card tt");
  host.append(diffCard, simCard);

  const heads = () => {
    diffCard.appendChild(el("h2", null, "Your 15 against the template"));
    simCard.appendChild(el("h2", null, "What if — the exposure simulator"));
  };

  // ---- guards ---------------------------------------------------------
  if (!field) {
    heads();
    const why = emptyBox("no field is selected",
      "Both tools compare your squad with one field. Pick a field above.");
    diffCard.appendChild(why);
    simCard.appendChild(emptyBox("no field is selected"));
    return;
  }
  if (!res.squad || !res.squad.readable) {
    heads();
    const hint = "Run `fpl myteam auth` once, or text /setsquad with your 15. " +
      "Until then this page can describe the field but not your position in it.";
    diffCard.appendChild(emptyBox(res.squad?.note || "your squad could not be read", hint));
    simCard.appendChild(emptyBox("your squad could not be read", hint));
    return;
  }
  if (!(field.measures || []).includes("eo")) {
    heads();
    const hint = "Exposure is your multiplier minus the field's effective " +
      "ownership. This field only reports head-count ownership, and a " +
      "multiplier minus a head count is not a number. Pick a field that " +
      "publishes EO — the tools below refuse rather than guess.";
    diffCard.appendChild(emptyBox(`${field.label} publishes no effective ownership`, hint));
    simCard.appendChild(emptyBox(`${field.label} publishes no effective ownership`, hint));
    return;
  }

  // ---- the universe ---------------------------------------------------
  /* rows ∪ differentials, deduped by code. The page above toggles between the
     two sets; a hole or a differential must not appear and vanish with a
     toolbar the reader has forgotten about, so both tools always read the
     union. */
  const uni = new Map();
  for (const r of [...(res.rows || []), ...(res.differentials || [])])
    if (r && r.code != null && !uni.has(r.code)) uni.set(r.code, r);
  const all = [...uni.values()];

  const eoOf = r => {
    const f = r && r.fields && r.fields[field.key];
    const v = f ? f.eo : null;
    return v == null ? null : v;
  };
  /* One term of the identity, per point he scores. `over` lets the simulator
     ask "what would this be at a different multiplier" without mutating the
     payload — nothing in this module writes to res. */
  const termOf = (r, over) => {
    const v = eoOf(r);
    const m = over === undefined ? myMult(r).v : over;
    return (v == null || m == null) ? null : m - v / 100;
  };

  const squad = all.filter(r => r.in_squad === true);
  const squadCodes = new Set(squad.map(r => r.code));

  /* The squad is only visible through in_squad flags on the rows this panel
     returned — there is no roster in the payload — so "your 15" can be empty
     even when the read succeeded. That is a coverage hole, not a squad of
     nobody, and it gets said rather than summed to zero. */
  if (!all.length || !squad.length) {
    heads();
    const hint = !all.length
      ? "The panel returned no players at all, so there is nothing to lay " +
        "your squad against."
      : `The panel returned ${all.length} players and none of them is flagged ` +
        `as yours, even though the squad read succeeded` +
        (res.squad.n != null ? ` with ${res.squad.n} players` : "") +
        ". Both tools below can only see your squad through those flags, so " +
        "they refuse rather than report a squad of nobody.";
    const why = all.length ? "none of your 15 appears in these rows"
                           : "this panel returned no players";
    diffCard.appendChild(emptyBox(why, hint));
    simCard.appendChild(emptyBox(why, hint));
    return;
  }

  // the field's held population — the denominator every percentile names
  const heldVals = all.map(eoOf).filter(v => v != null && v >= HELD_FLOOR)
    .sort((a, b) => a - b);
  const pctileOf = v => v == null || v < HELD_FLOOR ? null : pctileIn(heldVals, v);

  // the field's template: its top N by EO, a property of the field alone, so
  // before/after in the simulator are compared over an identical set
  const template = all.filter(r => eoOf(r) != null)
    .sort((a, b) => eoOf(b) - eoOf(a)).slice(0, TOP_N);
  const templateCodes = new Set(template.map(r => r.code));

  // what the swarm plots: the held population plus your 15, whatever their EO
  const plotted = all.filter(r =>
    eoOf(r) != null && (eoOf(r) >= HELD_FLOOR || r.in_squad === true));

  /* Diverging ramp, anchored at the 90th percentile of |term| over the players
     actually drawn — the same doctrine as the field map above, for the same
     reason: one captain-heavy premium at ±1.2 would otherwise flatten every
     ordinary ±0.2 term to the same neutral gray. Past the anchor the colour
     saturates, and the legend says so. */
  const termScale = (() => {
    const ts = plotted.map(r => termOf(r)).filter(t => t != null)
      .map(Math.abs).sort((a, b) => a - b);
    if (!ts.length) return 0.4;
    return Math.max(0.2, ts[Math.floor(ts.length * 0.9)] ?? ts[ts.length - 1]);
  })();
  /* Warm pole = the field is ahead of you, cool pole = you are ahead of it,
     neutral gray in the middle. Identical semantics to the ledger above
     (.lrow.heavy / .lrow.light), so the two cards teach one colour. */
  const ramp = t => {
    if (t == null) return "var(--ttl-mid)";
    const f = Math.min(1, Math.abs(t) / (termScale || 1));
    const pole = t >= 0 ? "var(--ttl-light)" : "var(--ttl-heavy)";
    return `color-mix(in oklab, ${pole} ${Math.round(100 * f)}%, var(--ttl-mid))`;
  };

  // multiplier axis shared by every dumbbell on the page
  const AX = Math.max(2, Math.ceil(Math.max(
    0, ...all.map(r => (eoOf(r) ?? 0) / 100)) * 10) / 10);
  const axPos = v => `${(100 * Math.min(Math.max(v, 0), AX) / AX).toFixed(2)}%`;
  // the 1× rule every track carries — "am I above or below a start?"
  for (const c of [diffCard, simCard]) c.style.setProperty("--ax1", axPos(1));

  const anyAssumed = squad.some(r => myMult(r).assumed);
  const measureNote = ctx.measure === "own"
    ? "The measure above is set to head-count ownership; these two tools stay " +
      "on effective ownership regardless, because the identity's second term " +
      "is a multiplier and a multiplier minus a head count is not a number."
    : null;

  // ---- shared components ----------------------------------------------

  /* One dumbbell row. Both marks live on the same 0–AX× axis: a hollow ring
     for the field's EO, a filled square for your multiplier, and the segment
     between them tinted by the term. Shape carries identity (never colour
     alone) and the term is printed at the end of every row, so the chart is
     its own table view. `ghost` draws where your multiplier used to be. */
  function dumbbell(r, opts = {}) {
    const v = eoOf(r), m = opts.mine === undefined ? myMult(r).v : opts.mine;
    const t = (v == null || m == null) ? null : m - v / 100;
    const row = el("div", "ttrow" + (opts.flat ? " flat" : ""));
    row.appendChild(faceImg(r.code, "avatar" + (squadCodes.has(r.code) ? " mine" : "")));

    const id = el("div", "ttid");
    const nm = el("div", "ttname");
    nm.appendChild(el("span", "n", r.name));
    if (opts.chip) nm.appendChild(el("span", "chip " + (opts.chipCls || ""), opts.chip));
    id.appendChild(nm);
    const pc = pctileOf(v);
    id.appendChild(el("div", "sub",
      [r.pos, r.team, fmtPrice(r.price)].filter(Boolean).join(" · ")));
    /* The percentile is the point of this line: 58% EO means nothing until you
       know it beats four fifths of everything the field owns. Its denominator
       is named here and again in the card's opening sentence. */
    const ctxLine = el("div", "sub ctx");
    ctxLine.textContent = `EO ${pct(v)} · ` + (pc == null
      ? `under ${HELD_FLOOR}%, outside the ${heldVals.length} held`
      : `${ord(pc)} pct of ${heldVals.length} held`);
    ctxLine.title = v == null
      ? `${field.label} publishes no effective ownership for him.`
      : `${field.label} effective ownership ${pct(v)}` + (pc == null
        ? `, below the ${HELD_FLOOR}% floor — he is outside the ` +
          `${heldVals.length} players this field holds at all.`
        : `, higher than ${ord(pc)} of the ${heldVals.length} players this ` +
          `field holds at ${HELD_FLOOR}% EO or more.`);
    id.appendChild(ctxLine);
    row.appendChild(id);

    const track = el("div", "tttrack");
    track.appendChild(el("span", "ttaxis"));
    if (v != null && m != null) {
      const a = Math.min(m, v / 100), b = Math.max(m, v / 100);
      const link = el("span", "ttlink");
      link.style.left = axPos(a);
      link.style.width = `${(100 * (Math.min(b, AX) - Math.min(a, AX)) / AX).toFixed(2)}%`;
      link.style.background = ramp(t);
      track.appendChild(link);
    }
    if (opts.ghost != null && opts.ghost !== m) {
      const g = el("span", "ttghost");
      g.style.left = axPos(opts.ghost);
      g.title = `you were at ${mult(opts.ghost)} here`;
      track.appendChild(g);
    }
    if (v != null) {
      const f = el("span", "ttfield");
      f.style.left = axPos(v / 100);
      f.title = `${field.label} effective ownership ${pct(v)} = ${mult(v / 100)}`;
      track.appendChild(f);
    }
    if (m != null) {
      const y = el("span", "ttmine");
      y.style.left = axPos(m);
      y.title = `your multiplier ${mult(m)}`;
      track.appendChild(y);
    }
    row.appendChild(track);

    const val = el("div", "ttterm");
    val.textContent = t == null ? "–" : sgn2(t);
    val.title = t == null
      ? `${field.label} publishes no EO for him, so no term can be stated.`
      : `${mult(m)} − ${mult(v / 100)} = ${sgn2(t)} per point he scores`;
    row.appendChild(val);

    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.title = "open his full ladder";
    const go = () => focus(r.code);
    row.onclick = go;
    row.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
    return row;
  }

  function axisHeader() {
    const h = el("div", "ttrow ttaxrow");
    h.appendChild(el("span", ""));            // avatar gutter
    h.appendChild(el("span", ""));            // id gutter
    const track = el("div", "tttrack ttscale");
    for (let v = 0; v <= AX + 1e-9; v += 0.5) {
      const tick = el("span", "tttick");
      tick.style.left = axPos(v);
      tick.appendChild(el("i", null, `${v.toFixed(v % 1 ? 1 : 0)}×`));
      track.appendChild(tick);
    }
    h.appendChild(track);
    h.appendChild(el("span", ""));
    return h;
  }

  function markLegend(host2) {
    const leg = el("div", "ttlegend");
    leg.append(el("span", "tlabel", "read"),
               el("span", "ttkey field"), el("span", "sub", "the field's EO"),
               el("span", "ttkey mine"), el("span", "sub", "your multiplier"));
    const r = el("span", "ttramp");
    for (let i = -6; i <= 6; i++) {
      const s = el("span");
      s.style.background = ramp(i / 6 * termScale);
      r.appendChild(s);
    }
    leg.append(el("span", "ttsep"),
               el("span", "sub", `field ahead by ${termScale.toFixed(2)}+`), r,
               el("span", "sub", `you ahead by ${termScale.toFixed(2)}+`));
    leg.title = "the segment between the two marks is the term; its colour " +
      "saturates at the 90th percentile of |term| over the players drawn, so " +
      "one extreme cannot flatten the rest";
    host2.appendChild(leg);
  }

  function tile(host2, v, k, cls, title) {
    const d = el("div", "stat" + (cls ? " " + cls : ""));
    d.appendChild(el("div", "v", v));
    d.appendChild(el("div", "k", k));
    if (title) d.title = title;
    host2.appendChild(d);
    return d;
  }

  // ======================================================= card 1: the diff

  const myEo = squad.map(eoOf).filter(v => v != null).sort((a, b) => a - b);
  const myPctiles = squad.map(r => pctileOf(eoOf(r))).filter(p => p != null)
    .sort((a, b) => a - b);
  const squadSwing = squad.reduce((a, r) => a + (termOf(r) ?? 0), 0);
  const squadKnown = squad.filter(r => termOf(r) != null).length;
  const coverN = template.filter(r => squadCodes.has(r.code)).length;
  const topBasis = t => template.reduce((a, r) =>
    a + (t(r) ?? 0), 0);
  const topExposure = topBasis(r => termOf(r));

  function renderDiff() {
    diffCard.textContent = "";
    diffCard.appendChild(el("h2", null, "Your 15 against the template"));
    diffCard.appendChild(el("p", "sub",
      `Both marks on every track below are multipliers, so they can honestly ` +
      `sit on one axis: a hollow ring where ${field.label} has its effective ` +
      `ownership, a filled square where your own multiplier is. The distance ` +
      `between them is the identity's term — what you gain or concede for ` +
      `every point that player scores. Gaps are ranked by that term in both ` +
      `directions, never by ownership and never alphabetically.`));

    // --- basis, stated before any number is shown ---
    const basis = el("div", "toolbar");
    basis.appendChild(el("span", "tlabel", "Basis"));
    basis.appendChild(el("span", "sub",
      `${field.label} · % of: ${field.denominator}` +
      (field.gw != null ? ` · gameweek ${field.gw}` : "")));
    basis.appendChild(el("span", "chip",
      `your 15 via ${res.squad.source}` +
      (res.squad.gw != null ? ` at GW${res.squad.gw}` : "")));
    if (res.squad.n != null && squad.length !== res.squad.n)
      basis.appendChild(Object.assign(el("span", "chip bad",
        `${squad.length} of your ${res.squad.n} are on this page`),
        { title: "The rest are outside the rows this panel returned, so every " +
                 "squad-side total below is short by them. It is a coverage " +
                 "hole, not a zero." }));
    if (anyAssumed)
      basis.appendChild(Object.assign(el("span", "chip warn",
        "multipliers inferred from your roles"),
        { title: res.squad.has_multipliers === false
            ? "The squad read supplies roles but not multipliers, so captain " +
              "is taken as 2× and a triple-captain chip would make it 3×."
            : "Some multipliers are inferred from your squad role." }));

    // the aggregate the payload cannot support — said out loud, not hidden
    const measured = all.filter(r => eoOf(r) != null);
    const sumEo = measured.reduce((a, r) => a + eoOf(r) / 100, 0);
    if (sumEo > 12.5)
      basis.appendChild(Object.assign(el("span", "chip warn",
        `this field's EO sums to ${sumEo.toFixed(1)}× over ${measured.length} players`),
        { title: "A manager applies 12 multipliers in a normal week — eleven " +
                 "starters plus the captain's extra — and at most 16 on a " +
                 "bench boost. A sum above that means the field-side read is " +
                 "counting bench places it could not identify, or is a model " +
                 "with no squad-sum constraint. It is why nothing here is " +
                 "reported as one grand total: every figure is per player, or " +
                 "over a basis this card names." }));
    diffCard.appendChild(basis);
    if (measureNote) diffCard.appendChild(el("p", "sub", measureNote));

    // --- the ten-second read ---
    const tiles = el("div", "stats");
    const med = quantile(myPctiles, 0.5);
    tile(tiles, ord(med), `median percentile of your 15`, null,
      `Half your squad sits above the ${ord(med)} percentile of the ` +
      `${heldVals.length} players ${field.label} holds at ${HELD_FLOOR}% EO or ` +
      `more. High means you are playing the template; low means you are not.`);
    tile(tiles, `${coverN}/${TOP_N}`, `of the field's top ${TOP_N} you hold`,
      coverN >= TOP_N * 0.6 ? "good" : "bad",
      `The ${TOP_N} players with the highest EO in ${field.label} — its ` +
      `template. Holding one is insurance, not upside; missing one is a hole.`);
    /* Deliberately uncoloured. Σ over your own 15 is a measure of DISTANCE
       from the field, not of quality — a template squad and a maverick one
       both have a legitimate case, and a green tile would be this card
       picking a strategy for the reader. */
    tile(tiles, sgn2(squadSwing), `Σ term over your own ${squadKnown}`, null,
      `Σ (your multiplier − ${field.label} EO) across the ${squadKnown} of your ` +
      `squad this field measures. It is complete on both sides over that set, ` +
      `which is why it is quoted and a whole-game total is not. Higher means ` +
      `your 15 sit further from what the field already owns — more swing in ` +
      `both directions, neither better nor worse on its own.`);
    tile(tiles, sgn2(topExposure), `Σ term over the field's top ${TOP_N}`,
      topExposure >= 0 ? "good" : "bad",
      `The same sum over the field's own template instead of over your squad. ` +
      `Negative is normal and is the price of any differential position: for ` +
      `every point those ${TOP_N} score you move ${Math.abs(topExposure).toFixed(2)} ` +
      `against the field.`);
    diffCard.appendChild(tiles);

    // --- the swarm: where your 15 sit in the field's own distribution ---
    renderSwarm(diffCard);

    // --- the ranked gaps ---
    const scored = all.map(r => ({ r, t: termOf(r) })).filter(p => p.t != null);
    const holes = scored.filter(p => p.t < 0).sort((a, b) => a.t - b.t).slice(0, 8);
    const diffs = scored.filter(p => p.t > 0 && squadCodes.has(p.r.code))
      .sort((a, b) => b.t - a.t).slice(0, 8);

    const cols = el("div", "ttcols");
    cols.append(
      panel("Your holes — ranked by what they cost", holes,
        `Every player the field is heavier on than you, worst first. A hole is ` +
        `not "a player I do not own": it is exposure sized by the field's EO, ` +
        `so a 4% player you are missing is not one.`,
        p => squadCodes.has(p.r.code)
          ? { chip: ROLE_NAME[p.r.your_role] || "owned", cls: "warn" }
          : { chip: "not owned", cls: "" }),
      panel("Your differentials — ranked by what they win", diffs,
        `Where your multiplier exceeds the field's EO, best first. Only your ` +
        `own 15 can appear here, including any the field's top rows never ` +
        `mention — the ranking is the term, so a player the field has barely ` +
        `heard of rises on his own.`,
        p => ({ chip: ROLE_NAME[p.r.your_role] || "owned", cls: "s1" })));
    diffCard.appendChild(cols);
    markLegend(diffCard);

    /* Owned, but no term can be stated for him — either the field has no read
       on him or the squad read gave no role. A player who silently vanishes
       from both lists is the worst outcome here, so each one is named with
       the reason he is missing. */
    const noField = squad.filter(r => eoOf(r) == null);
    const noRole = squad.filter(r => eoOf(r) != null && myMult(r).v == null);
    const names = l => l.map(r => r.name).join(", ");
    if (noField.length || noRole.length) {
      const d = el("p", "sub");
      if (noField.length) d.append(
        `${field.label} publishes no effective ownership for ${names(noField)}` +
        `. Blank is not zero — the term cannot be stated. `);
      if (noRole.length) d.append(
        `Your squad read gives no role for ${names(noRole)}, so ` +
        `${noRole.length === 1 ? "his" : "their"} multiplier is unknown. `);
      d.append(`${names([...noField, ...noRole])} ` +
        `${noField.length + noRole.length === 1 ? "is" : "are"} therefore ` +
        `absent from both lists above and from every sum on this card, which ` +
        `is why the totals name the count they are over. This card will not ` +
        `invent a number to make the ranking look complete.`);
      diffCard.appendChild(d);
    }

    diffCard.appendChild(el("p", "sub",
      `Every value drawn above is printed beside its own row, and the full ` +
      `set is in the “Every player” table, so nothing here is reachable only ` +
      `by colour or only by hover. Click any row for that player's ladder.`));

    function panel(title, list, why, chipFor) {
      const c = el("div", "ttcol");
      c.appendChild(el("h3", null, title));
      c.appendChild(el("p", "sub", why));
      if (!list.length) {
        c.appendChild(el("p", "sub", "none — nothing on this side of zero."));
        return c;
      }
      c.appendChild(axisHeader());
      for (const p of list) {
        const k = chipFor(p);
        c.appendChild(dumbbell(p.r, { chip: k.chip, chipCls: k.cls }));
      }
      return c;
    }
  }

  // ---- the concentration swarm ---------------------------------------
  /* One dot per player the field holds, positioned by the field's EO alone.
     Its job is CONTEXT: a 58% EO means nothing until you can see that only
     ~70 players in the game clear 1% at all and that half of them sit under
     4%. Your 15 are filled, everyone else is hollow — the same shape language
     as the field map above — and the wash behind them is the middle half of
     your squad, so "am I playing the template" is a glance, not a sum. */
  function renderSwarm(host2) {
    if (!plotted.length) return;
    host2.appendChild(el("h3", null,
      `Where your 15 sit in everything ${field.short || field.label} holds`));
    host2.appendChild(el("p", "sub",
      `${plotted.length} dots: the ${heldVals.length} players this field holds ` +
      `at ${HELD_FLOOR}% EO or more, plus any of your own below that floor. ` +
      `The other ${all.length - plotted.length} on this page are under it and ` +
      `are not drawn — at under ${HELD_FLOOR}% EO fewer than one manager in a ` +
      `hundred has any exposure to them, so they can be neither template nor ` +
      `a hole worth the name.`));

    const W = 760, H = 208, L = 30, R = 26, T = 34, B = 44;
    const mid = (T + (H - B)) / 2;
    const hi = Math.max(10, ...plotted.map(r => eoOf(r)));
    const dom = Math.ceil(hi / 10) * 10;
    const rt = v => Math.sqrt(Math.max(0, v)) / Math.sqrt(dom);
    const sx = v => L + (W - L - R) * rt(v);
    // drop any tick the domain end would sit on top of — two labels 4px apart
    // is noise, and the end tick is the one that has to survive
    const TICKS = [0, 1, 2, 5, 10, 20, 40, 60, 90, 120, 160]
      .filter(v => v < dom && rt(dom) - rt(v) > 0.05).concat(dom);

    const wrap = el("div", "ttchart");
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "ttswarm", role: "img" });
    svg.appendChild(sv("title", {},
      `${plotted.length} players by ${field.label} effective ownership; ` +
      `your ${squad.length} are filled`));
    svg.appendChild(sv("desc", {},
      `Half your squad sits above the ${ord(quantile(myPctiles, 0.5))} ` +
      `percentile of the ${heldVals.length} players this field holds. ` +
      `Every value is also listed in the ranked rows below.`));

    // the middle half of your squad, as a quiet wash behind the dots
    const q1 = quantile(myEo, 0.25), q3 = quantile(myEo, 0.75), q2 = quantile(myEo, 0.5);
    if (q1 != null && q3 != null && q3 > q1) {
      svg.appendChild(sv("rect", {
        x: sx(q1), y: T, width: Math.max(1, sx(q3) - sx(q1)), height: (H - B) - T,
        class: "ttband",
      }));
      svg.appendChild(sv("line", { x1: sx(q2), x2: sx(q2), y1: T, y2: H - B,
                                   class: "ttmed" }));
      svg.appendChild(sv("text", { x: sx(q2), y: T - 18, class: "ttcap" },
        "your 15 · middle half"));
      svg.appendChild(sv("text", { x: sx(q2), y: T - 6, class: "ttcap sm" },
        `median ${pct(q2)} EO`));
    }

    for (const v of new Set(TICKS)) {
      svg.appendChild(sv("line", { x1: sx(v), x2: sx(v), y1: T, y2: H - B, class: "ttgrid" }));
      svg.appendChild(sv("text", { x: sx(v), y: H - B + 16, class: "tttickt" }, `${v}%`));
    }
    svg.appendChild(sv("text", { x: (L + W - R) / 2, y: H - 8, class: "ttaxist" },
      `${field.label} — effective ownership %`));

    // deterministic beeswarm: fixed slots, no random jitter, so the picture is
    // identical on every render and no two marks land on top of each other
    const SLOT = 9, CAP = Math.floor(((H - B) - T) / 2 / SLOT);
    const cols = new Map();
    const pts = [];
    for (const r of [...plotted].sort((a, b) => eoOf(a) - eoOf(b))) {
      const px = sx(eoOf(r));
      const c = Math.round(px / SLOT);
      const k = cols.get(c) || 0; cols.set(c, k + 1);
      const step = Math.min(Math.ceil(k / 2), CAP);
      const dy = k === 0 ? 0 : (k % 2 ? 1 : -1) * step * SLOT;
      pts.push({ r, x: px, y: mid + dy, t: termOf(r), mine: r.in_squad === true });
    }

    const marks = sv("g", {});
    for (const p of pts) {
      const c = sv("circle", {
        cx: p.x, cy: p.y, r: p.mine ? 5.5 : 4,
        class: "ttdot" + (p.mine ? " mine" : " out"),
      });
      if (p.mine) c.setAttribute("fill", ramp(p.t));
      else { c.setAttribute("fill", "none"); c.setAttribute("stroke", ramp(p.t)); }
      p.node = c;
      marks.appendChild(c);
    }
    svg.appendChild(marks);

    /* Selective direct labels: your extremes and the field's worst holes, and
       nothing else. The occupancy map is seeded with EVERY mark, not only with
       the labels already placed, so a name can never be laid across another
       player's dot — a label that has to be untangled from the marks is worse
       than no label, and the tooltip carries the rest either way. */
    const placed = pts.map(p => ({ x: p.x - 6, y: p.y - 6, w: 12, h: 12 }));
    const free = b => !placed.some(q =>
      !(b.x + b.w < q.x || q.x + q.w < b.x ||
        b.y + b.h < q.y || q.y + q.h < b.y));
    /* Tried in importance order and simply skipped where the swarm is too
       dense to take a name cleanly, which is why the count varies by field.
       The caption says so, and nothing is only reachable this way. */
    const cands = [
      ...pts.filter(p => !p.mine).sort((a, b) => a.t - b.t).slice(0, 6),
      ...pts.filter(p => p.mine).sort((a, b) => Math.abs(b.t) - Math.abs(a.t)).slice(0, 6),
    ];
    for (const p of cands) {
      const w = p.r.name.length * 5.6 + 8;
      /* Four placements, in order of how naturally the eye ties the name to
         the mark: right of it, left of it, then straight above or below —
         which is what saves the dots sitting at the end of a tall column,
         where both horizontal slots are another player's. */
      const tries = [
        { box: p.x + 9, x: p.x + 9, y: p.y + 4, cls: "" },
        { box: p.x - 9 - w, x: p.x - 9, y: p.y + 4, cls: " end" },
        { box: p.x - w / 2, x: p.x, y: p.y - 9, cls: " mid" },
        { box: p.x - w / 2, x: p.x, y: p.y + 16, cls: " mid" },
      ];
      for (const t of tries) {
        if (t.box < L - 6 || t.box + w > W - 2) continue;
        const b = { x: t.box, y: t.y - 11, w, h: 14 };
        if (b.y < 2 || b.y + b.h > H - B + 2) continue;
        if (!free(b)) continue;
        placed.push(b);
        svg.appendChild(sv("text", { x: t.x, y: t.y,
          class: "ttlabel" + t.cls + (p.mine ? " mine" : "") }, p.r.name));
        break;
      }
    }

    /* Nearest-point layer rather than pinpoint hit areas: a 4px dot in a
       9px-slot swarm is far too small to land on, so the whole plot listens
       and resolves to the closest mark within 16 units. */
    const hit = sv("rect", { x: L - 8, y: T, width: W - L - R + 16,
                             height: (H - B) - T, class: "tthit" });
    svg.appendChild(hit);

    wrap.appendChild(svg);
    const tip = el("div", "tttip");
    wrap.appendChild(tip);
    host2.appendChild(wrap);
    host2.appendChild(el("p", "sub",
      `Filled = you own him, hollow = you do not; the tint is the same term ` +
      `the rows below rank by. The axis is square-root scaled — which is why ` +
      `its ticks are unevenly spaced — because half the players this field ` +
      `holds sit under ${pct(quantile(heldVals, 0.5))} EO and a linear axis ` +
      `would pile them into one column. Only the marks with clear space ` +
      `around them are named — a label laid across three other dots is worse ` +
      `than none, and every player here is named in the rows below and on ` +
      `hover. Hover anywhere near a dot for its numbers; click for the full ` +
      `ladder.`));

    let cur = null;
    const clear = () => {
      if (cur) cur.node.classList.remove("on");
      cur = null; tip.classList.remove("on");
    };
    hit.addEventListener("mousemove", e => {
      const box = svg.getBoundingClientRect();
      if (!box.width) return;
      const k = W / box.width;
      const mx = (e.clientX - box.left) * k, my = (e.clientY - box.top) * k;
      let best = null, bd = 16 * 16;
      for (const p of pts) {
        const d = (p.x - mx) ** 2 + (p.y - my) ** 2;
        if (d < bd) { bd = d; best = p; }
      }
      if (!best) { clear(); return; }
      if (best !== cur) { if (cur) cur.node.classList.remove("on"); cur = best; best.node.classList.add("on"); }
      showTip(best, box);
    });
    hit.addEventListener("mouseleave", clear);
    hit.addEventListener("click", () => { if (cur) focus(cur.r.code); });

    function showTip(p, box) {
      tip.textContent = "";
      tip.appendChild(el("b", null, p.r.name));
      tip.appendChild(el("div", "sub",
        [p.r.pos, p.r.team, fmtPrice(p.r.price)].filter(Boolean).join(" · ")));
      const line = (k, v) => {
        const d = el("div", "tl");
        d.append(el("span", "tk", k), el("span", "tv", v));
        tip.appendChild(d);
      };
      const v = eoOf(p.r), pc = pctileOf(v);
      line(`${field.short || field.label} EO`, pct(v));
      line("percentile", pc == null ? `under ${HELD_FLOOR}%` : ord(pc));
      const m = myMult(p.r);
      line("you", p.r.in_squad === true ? (ROLE_NAME[p.r.your_role] || "owned")
                                        : "not owned");
      line("term", p.t == null ? "–" : `${sgn2(p.t)} / point`);
      if (p.r.xpts != null) line(`xPts gw${res.xpts_gw ?? "?"}`, fmt1(p.r.xpts));
      const wb = wrap.getBoundingClientRect();
      const px = box.left - wb.left + (p.x / W) * box.width;
      const py = box.top - wb.top + (p.y / H) * box.height;
      const right = Math.max(4, wb.width - 190);
      tip.style.left = `${Math.min(Math.max(px + 14, 4), right)}px`;
      tip.style.top = `${Math.max(py - 20, 4)}px`;
      tip.classList.add("on");
    }
  }

  // ================================================ card 2: the simulator

  /* The simulator's whole state. It lives here, not at module scope, because
     the seam re-invokes renderTools on every selection change and a swap only
     means anything against one field. */
  let outCode = null, inCode = null, inRole = null, query = "";

  const simTop = el("div");
  const simBody = el("div");

  function renderSim() {
    simCard.textContent = "";
    simCard.appendChild(el("h2", null, "What if — the exposure simulator"));
    simCard.appendChild(el("p", "sub",
      `Take one of your 15 out, put anyone in, and watch the identity's terms ` +
      `move. Everything recomputes here in the page from the payload already ` +
      `loaded: only YOUR side of each subtraction changes, and the field's ` +
      `side is held at the values it was measured with. The rail at the foot ` +
      `of this card is the panel's own statement of what that does and does ` +
      `not cover — read it before you trust a number here.`));
    simCard.append(simTop, simBody);
    renderPickers();
    renderResult();
  }

  function renderPickers() {
    simTop.textContent = "";

    const row1 = el("div", "toolbar");
    row1.appendChild(el("span", "tlabel", "Take out"));
    const outSel = el("select");
    outSel.appendChild(Object.assign(el("option", null, "— pick one of your 15 —"),
                                     { value: "" }));
    for (const r of [...squad].sort((a, b) => (eoOf(b) ?? -1) - (eoOf(a) ?? -1))) {
      const m = myMult(r);
      outSel.appendChild(Object.assign(el("option", null,
        `${r.name} · ${r.pos ?? "?"} · ${ROLE_NAME[r.your_role] || `${mult(m.v)}`}` +
        ` · EO ${pct(eoOf(r))}`), { value: String(r.code) }));
    }
    outSel.value = outCode == null ? "" : String(outCode);
    outSel.onchange = () => {
      outCode = outSel.value ? Number(outSel.value) : null;
      inRole = null;                        // re-inherit from the new outgoing
      renderPickers(); renderResult();
    };
    row1.appendChild(outSel);

    if (outCode != null) {
      const r = uni.get(outCode);
      const m = myMult(r);
      row1.appendChild(el("span", "sub",
        `He is at ${mult(m.v)}${m.assumed ? " (inferred)" : ""} against ` +
        `${field.short || field.label} EO ${pct(eoOf(r))}.`));
    }
    simTop.appendChild(row1);

    const row2 = el("div", "toolbar");
    row2.appendChild(el("span", "tlabel", "Bring in"));
    const q = el("input");
    q.type = "text"; q.placeholder = "search a player…"; q.size = 16; q.value = query;
    q.oninput = () => { query = q.value; renderCands(); };
    row2.appendChild(q);
    if (inCode != null) {
      const r = uni.get(inCode);
      const chip = el("button", "chip s1", `✓ ${r.name} · EO ${pct(eoOf(r))}`);
      chip.title = "clear";
      chip.onclick = () => { inCode = null; renderPickers(); renderResult(); };
      row2.appendChild(chip);
    }
    const reset = el("button", "chip", "reset");
    reset.onclick = () => {
      outCode = inCode = inRole = null; query = "";
      renderPickers(); renderResult();
    };
    row2.appendChild(reset);
    simTop.appendChild(row2);

    // role for the incoming player — a choice, never a silent assumption
    const row3 = el("div", "toolbar");
    row3.appendChild(el("span", "tlabel", "He comes in as"));
    const seg = el("span", "seg");
    const outRole = outCode != null ? (uni.get(outCode) || {}).your_role : null;
    const effective = inRole ?? outRole ?? "start";
    for (const k of ["bench", "start", "captain"]) {
      const b = el("button", k === effective ? "on" : "", ROLE_NAME[k]);
      b.onclick = () => { inRole = k; renderPickers(); renderResult(); };
      seg.appendChild(b);
    }
    row3.appendChild(seg);
    row3.appendChild(el("span", "sub",
      inRole == null && outRole
        ? `Inherited from the player going out. Change it and the arithmetic ` +
          `follows — including the armband, which has to live somewhere and ` +
          `which this page will not move on its own.`
        : `Your choice, not a plan: nothing here checks that the rest of your ` +
          `team can field it.`));
    simTop.appendChild(row3);

    const candWrap = el("div");
    simTop.appendChild(candWrap);
    renderCands();

    function renderCands() {
      candWrap.textContent = "";
      if (inCode != null && !query) return;
      const term = query.trim().toLowerCase();
      let cands = all.filter(r => !squadCodes.has(r.code) && eoOf(r) != null);
      if (term) cands = cands.filter(r =>
        r.name.toLowerCase().includes(term) ||
        String(r.team || "").toLowerCase().includes(term));
      cands.sort((a, b) => eoOf(b) - eoOf(a));
      const head = el("p", "sub", term
        ? `${cands.length} match “${query.trim()}”, heaviest first.`
        : `Nobody picked yet — here are the players ${field.short || field.label} ` +
          `is heaviest on that you do not own. They are your holes, in order.`);
      candWrap.appendChild(head);
      const list = el("div", "ttcands");
      for (const r of cands.slice(0, 12)) {
        const b = el("button", "ttcand");
        b.appendChild(faceImg(r.code, "avatar"));
        const d = el("div");
        d.appendChild(el("div", "n", r.name));
        d.appendChild(el("div", "sub",
          `${r.pos ?? "?"} · ${fmtPrice(r.price)} · EO ${pct(eoOf(r))} · ` +
          `${sgn2(termOf(r))} now`));
        b.appendChild(d);
        b.onclick = () => { inCode = r.code; query = ""; renderPickers(); renderResult(); };
        list.appendChild(b);
      }
      if (!cands.length) candWrap.appendChild(el("p", "sub", "no player matches."));
      candWrap.appendChild(list);
    }
  }

  function renderResult() {
    simBody.textContent = "";
    if (outCode == null || inCode == null) {
      simBody.appendChild(el("p", "sub",
        "Pick one out and one in. Nothing is computed until both are chosen — " +
        "a half-specified swap has no term."));
      caveats(simBody);
      return;
    }
    const out = uni.get(outCode), inn = uni.get(inCode);
    /* Both sides need a stated term or the arithmetic is a guess dressed as a
       result. Refusing here is the same rule the card above follows. */
    const missing = [];
    if (eoOf(out) == null) missing.push(`${field.label} publishes no EO for ${out.name}`);
    if (eoOf(inn) == null) missing.push(`${field.label} publishes no EO for ${inn.name}`);
    if (myMult(out).v == null) missing.push(`your squad read gives no role for ${out.name}`);
    if (missing.length) {
      simBody.appendChild(emptyBox(missing.join("; ") + ".",
        "One side of the subtraction is blank, and blank is not zero. Pick a " +
        "player this field measures, or a different field."));
      caveats(simBody);
      return;
    }
    const outM = myMult(out).v;
    const newM = ROLE_MULT[inRole ?? out.your_role ?? "start"] ?? 1;

    // the counterfactual multiplier for every player, without touching res
    const after = new Map();
    after.set(out.code, 0);
    after.set(inn.code, newM);
    const multAfter = r => after.has(r.code) ? after.get(r.code) : (myMult(r).v ?? null);

    const squadAfter = [...squad.filter(r => r.code !== out.code), inn];
    const sum = (list, f) => list.reduce((a, r) => a + (f(r) ?? 0), 0);
    const swingBefore = sum(squad, r => termOf(r));
    const swingAfter = sum(squadAfter, r => termOf(r, multAfter(r)));
    const topBefore = sum(template, r => termOf(r));
    const topAfter = sum(template, r => termOf(r, multAfter(r)));
    const coverBefore = template.filter(r => squadCodes.has(r.code)).length;
    const coverAfter = template.filter(r =>
      squadAfter.some(s => s.code === r.code)).length;
    const holesAfter = all.map(r => ({ r, t: termOf(r, multAfter(r)) }))
      .filter(p => p.t != null).sort((a, b) => a.t - b.t);
    const worstAfter = holesAfter[0];

    // --- the trade, spelled out one term at a time ---
    simBody.appendChild(el("h3", null, "The trade"));
    const pair = el("div", "ttpair");
    pair.append(
      sideCard("Out", out, myMult(out).v, 0, "heavy"),
      sideCard("In", inn, 0, newM, "light"));
    simBody.appendChild(pair);

    const moved = el("div", "ttmoved");
    moved.appendChild(axisHeader());
    moved.appendChild(dumbbell(out, { mine: 0, ghost: outM,
                                      chip: "out", chipCls: "warn" }));
    moved.appendChild(dumbbell(inn, { mine: newM, ghost: 0,
                                      chip: "in", chipCls: "s1" }));
    simBody.appendChild(moved);
    simBody.appendChild(el("p", "sub",
      `The hollow marker is where your multiplier used to be; the square is ` +
      `where it lands. Both tracks are the same multiplier axis as the card ` +
      `above. The field's ring stays put: this card only ever moves your own ` +
      `side of the subtraction.`));

    // --- what actually changed ---
    simBody.appendChild(el("h3", null, "What that does to your exposure"));
    const tiles = el("div", "stats");
    const delta = (a, b) => sgn2(b - a);
    // uncoloured for the same reason as the card above: moving toward the
    // template and away from it are both defensible, and a status colour
    // would be this tool choosing for him
    tile(tiles, sgn2(swingAfter), `Σ term over your 15 · was ${sgn2(swingBefore)}`,
      null,
      `Σ (your multiplier − ${field.label} EO) across the fifteen you would ` +
      `hold. ${delta(swingBefore, swingAfter)} against now. Higher means your ` +
      `squad sits further from what the field already owns — more swing, in ` +
      `both directions. It is not a points forecast and it is not a verdict.`);
    tile(tiles, `${coverAfter}/${TOP_N}`,
      `of the field's top ${TOP_N} · was ${coverBefore}/${TOP_N}`,
      coverAfter >= coverBefore ? "good" : "bad",
      `Cover of the field's own template. The basis is fixed — the same ` +
      `${TOP_N} players before and after — because the template is a property ` +
      `of the field, not of your squad.`);
    // this one IS sign-carrying: negative means the field's template is ahead
    // of you, which is a direction, not a preference
    tile(tiles, sgn2(topAfter), `Σ term over those ${TOP_N} · was ${sgn2(topBefore)}`,
      topAfter >= 0 ? "good" : "bad",
      `${delta(topBefore, topAfter)} against now. The field's side of this sum ` +
      `is fixed, so it can only move by the multipliers you add to or remove ` +
      `from those ${TOP_N} — which means a like-for-like swap between two of ` +
      `them leaves it exactly where it was. That is the honest answer, not a ` +
      `stuck number.`);
    if (worstAfter) tile(tiles, worstAfter.r.name,
      `worst remaining hole · ${sgn2(worstAfter.t)}`, "bad",
      `The largest single term still running against you after the swap, over ` +
      `every player on this page.`);
    simBody.appendChild(tiles);

    /* Both sums are decomposed out loud. A number that does not move looks
       broken unless the page says why it cannot move, and the reason — the
       field's side of the subtraction is fixed, so only YOUR multipliers can
       shift a sum — is the whole lesson of the identity. */
    const dSwing = swingAfter - swingBefore, dTop = topAfter - topBefore;
    const outIn = templateCodes.has(out.code), innIn = templateCodes.has(inn.code);
    const drop = outIn ? outM : 0, add = innIn ? newM : 0;
    const why = el("p", "sub");
    why.append(
      `Over your own fifteen the sum ` +
      (Math.abs(dSwing) < 0.005
        ? `does not move: the multiplier is the same and the two players' EO ` +
          `agree to two decimals, so there is nothing left to change. `
        : `moves ${sgn2(dSwing)} — ` +
          (newM === outM ? "nothing from the multiplier"
                         : `${sgn2(newM - outM)} from the multiplier`) +
          ` and ${sgn2((eoOf(out) - eoOf(inn)) / 100)} from swapping ` +
          `${pct(eoOf(out))} of field EO for ${pct(eoOf(inn))}. `),
      `Over the field's top ${TOP_N} it ` +
      (Math.abs(dTop) < 0.005
        ? `does not move, because you take ${mult(drop)} out of those ` +
          `${TOP_N} and put ${mult(add)} back. The field's side of that sum ` +
          `is fixed, so only your own multipliers can ever shift it. `
        : `moves ${sgn2(dTop)}: ` +
          [drop ? `${sgn2(-drop)} leaving ${out.name}` : null,
           add ? `${sgn2(add)} arriving on ${inn.name}` : null]
            .filter(Boolean).join(" and ") + ". "),
      `Both are statements about exposure — where your bet sits and how big ` +
      `it is. Neither says whether it will pay.`);
    simBody.appendChild(why);

    caveats(simBody);

    function sideCard(kind, r, before, afterM, cls) {
      const c = el("div", "ttside " + cls);
      const hd = el("div", "hd");
      hd.appendChild(faceImg(r.code, "avatar"));
      const t = el("div");
      t.appendChild(el("div", "n", r.name));
      t.appendChild(el("div", "sub",
        [r.pos, r.team, fmtPrice(r.price),
         r.xpts != null ? `${fmt1(r.xpts)} xPts` : null].filter(Boolean).join(" · ")));
      hd.appendChild(t);
      hd.appendChild(el("span", "chip " + (cls === "heavy" ? "warn" : "s1"), kind));
      c.appendChild(hd);
      const v = eoOf(r);
      for (const [label, m] of [["now", before], ["after", afterM]]) {
        const line = el("div", "tteq" + (label === "after" ? " lead" : ""));
        line.append(el("span", "l", label),
                    el("span", "term mine", mult(m)),
                    el("span", "op", "−"),
                    el("span", "term theirs", mult(v / 100)),
                    el("span", "op", "="),
                    el("span", "res " + (m - v / 100 >= 0 ? "pos" : "neg"),
                       sgn2(m - v / 100)));
        c.appendChild(line);
      }
      return c;
    }
  }

  /* The rail. Not a tooltip, not a footnote: everything this tool does not
     model sits permanently under it, because every one of them is a way a
     reader could take a number here for something it is not.
     THE PANEL IS THE AUTHORITY ON WHAT MAY BE RECOMPUTED HERE. Where the
     payload publishes `whatif.safe_to_recompute` / `not_safe_to_recompute`,
     those sentences are rendered verbatim rather than paraphrased — a UI that
     restates the boundary in its own words is a UI that will drift off it. The
     two things below that the panel cannot know — that this card refuses to
     multiply the term by points, and that it never checked a squad's legality
     — are the view's own to declare. */
  function caveats(host2) {
    const d = el("div", "ttcaveats");
    d.appendChild(el("span", "tlabel", "what this does not model"));
    const item = (b, t) => {
      const s = el("div", "ttcav");
      if (b) s.appendChild(el("b", null, b));
      s.appendChild(el("span", null, t));
      d.appendChild(s);
      return s;
    };
    item("Points.",
      " rank move ≈ Σ term × points, and this card computes the term only. " +
      "It has no view on what anyone will score, so nothing above is a " +
      "projected score, a rank, or a recommendation. The xPts shown are the " +
      "payload's consensus, printed as context and never multiplied in.");
    item("Legality.",
      " budget, selling price, the three-per-club cap and formation are not " +
      "checked here — a swap this card draws may be impossible. The planner " +
      "and the solver own legality.");

    const wi = res.whatif;
    const nope = Array.isArray(wi?.not_safe_to_recompute) ? wi.not_safe_to_recompute : null;
    if (nope && nope.length) {
      item(null, "The panel states what a client may recompute from this " +
        "payload and what it may not. Its own words, unedited:");
      for (const line of nope) item("·", " " + line);
    } else {
      /* No published statement in this payload, so the view says only what it
         can defend, and names the assumption instead of asserting a fact. */
      const you = res.selection?.includes_you;
      item("The field.",
        ` ${field.label} is held fixed at its measured values` +
        (field.gw != null ? ` (gameweek ${field.gw})` : "") + ". " +
        (you === true
          ? `Your own entry is inside this field, so a transfer of yours does ` +
            `move it` + (field.n != null ? ` — by up to 1 manager in ${field.n}` : "") +
            `, and this card does not model that. Compare against a field you ` +
            `are not in.`
          : you === false
          ? `Your entry is not in it, so one transfer of yours cannot move it. ` +
            `If the field makes the same move, none of this holds.`
          : `The payload does not say whether your own entry is inside it. If ` +
            `it is, a transfer of yours moves the field too, and this card ` +
            `does not model that.`));
      item("Another selection or gameweek.",
        " every field number here is measured over one set of managers at one " +
        "gameweek. Nothing on this card can be re-aimed at a different set or " +
        "a different week without refetching the panel.");
    }
    if (anyAssumed || res.squad.has_multipliers === false)
      item("Your multipliers.",
        " the squad read supplies roles, not multipliers, so captain is taken " +
        "as 2× and a triple-captain chip would make it 3×. A bench boost " +
        "would put all fifteen on the pitch and no chip is visible from here.");
    if (field.gw != null && res.squad.gw != null && field.gw !== res.squad.gw)
      item("Two gameweeks.",
        ` the field is a GW${field.gw} read and your squad is a GW${res.squad.gw} ` +
        "read, so the two sides of every subtraction are not stamped the same week.");

    const safe = Array.isArray(wi?.safe_to_recompute) ? wi.safe_to_recompute : null;
    if (safe && safe.length) {
      const det = el("details", "ttsafe");
      det.appendChild(el("summary", null,
        `What the panel says this card MAY recompute in the browser ` +
        `(${safe.length})`));
      for (const line of safe) {
        const s = el("div", "ttcav");
        s.appendChild(el("b", null, "·"));
        s.appendChild(el("span", null, " " + line));
        det.appendChild(s);
      }
      d.appendChild(det);
    }
    host2.appendChild(d);
  }

  renderDiff();
  renderSim();
}

export default renderTools;
