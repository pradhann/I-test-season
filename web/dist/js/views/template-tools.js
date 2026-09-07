/* The Move Card — "would this move raise my risk against the field?"
 *
 * Owned separately from template.js so the two halves of the Template tab can
 * be built in parallel. template.js calls renderTools(host, ctx); everything
 * below that call belongs to this module.
 *
 * ctx is the seam, unchanged:
 *   ctx.res      the full ownership_eo payload (fields[], rows[], whatif, ...)
 *   ctx.fieldKey the field the page is currently measuring against
 *   ctx.measure  "eo" | "own"
 *   ctx.onFocus  (code) => void   ask the page to open its player drawer
 *   ctx.dispName (row) => string  the page's namesake-aware display name
 * template.js re-invokes renderTools whenever the selection changes, so this
 * module renders from ctx and holds no cross-render state of its own.
 *
 * ---------------------------------------------------------------------------
 * WHAT THIS CARD IS, AND WHY IT REPLACED THREE OTHERS
 *
 * The page's identity is  rank move ≈ Σ (your multiplier − the field's EO) × pts.
 * The old tools printed that subtraction in four different shapes — a swarm, a
 * holes ranking, a differentials ranking, and a literal "2.00× − 1.65× = +0.35"
 * block — and never once answered the only question a manager actually asks:
 * DOES THIS MOVE RAISE OR LOWER MY RISK? So all of that is gone and one card
 * remains, whose every row carries a direction.
 *
 * THE MEASURE. Tracking exposure = Σ |your multiplier − the field's EO| over
 * the players this field measures. It is a distance, so it has an unambiguous
 * direction: bigger means your 15 sit further from the average rival, which is
 * more swing in both directions. That is the ONE place on this page a colour
 * is allowed to assert up/down, and it is earned.
 *
 * WHY THE RANKING IS CHEAP. The change a swap makes decomposes exactly:
 *
 *     Δ = [ eo(out) − |mult(out) − eo(out)| ]  +  [ |m − eo(in)| − eo(in) ]
 *          \___________ the sell term ______/     \______ the buy term ____/
 *
 * Every other player's term is untouched, so no sum over the universe is
 * needed per candidate and the whole grid is a few thousand additions. It also
 * means the manual swap below the card is priced by the identical formula, so
 * a hand-picked move arrives in the same encoding as a generated one.
 *
 * WHERE THE NUMBERS COME FROM — the discipline this file must keep.
 *   - Every MEASURED number reads `rows[].fields[ctx.fieldKey]`, because the
 *     seam is field-KEYED: the reader can point this card at any field in the
 *     ladder, and the per-field block is the only source that answers for all
 *     of them.
 *   - `whatif.players` is the swap UNIVERSE and nothing else. It lists every
 *     current-season player, which is who may be bought — but its
 *     `field_eo_pct` is keyed to the `selected` field alone, so it cannot
 *     price a swap against a fieldKey the page has switched to. Reading it for
 *     a measurement would blend two populations silently. It is never read for
 *     one here. A candidate the chosen field does not measure is named as
 *     unpriceable, not assumed to be zero.
 *   - `whatif.safe_to_recompute` / `not_safe_to_recompute` are the panel's own
 *     statement of what a client may recompute. They are rendered VERBATIM in
 *     the single disclosure rail at the foot — a UI that restates the boundary
 *     in its own words is a UI that will drift off it.
 */

import { el, emptyBox, faceImg, fmtPrice, fmt1 } from "/js/app.js";

/* ---------------------------------------------------------------- helpers */

const pct = v => v == null ? "–" : `${Number(v).toFixed(1)}%`;
const sgn2 = v => v == null ? "–"
  : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}`;
const mult = v => v == null ? "–" : `${Number(v).toFixed(2)}×`;
const multS = v => v == null ? "–" : `${Number(v)}×`;
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
   result is FLAGGED as assumed. "Not owned" is a measured 0. */
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
   at or below this value". Ties resolve upward. */
function pctileIn(sorted, v) {
  if (v == null || !sorted.length) return null;
  let lo = 0, hi = sorted.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (sorted[m] <= v) lo = m + 1; else hi = m; }
  return 100 * lo / sorted.length;
}

const HELD_FLOOR = 1;      // % EO — under this, fewer than 1 rival in 100 has any
const SHOW = 3;            // rows per direction
const IN_CAP = 2;          // no buy is named more than twice on the card

/* ------------------------------------------------------------------ view */

export function renderTools(host, ctx) {
  host.textContent = "";
  if (!ctx || !ctx.res) return;

  const res = ctx.res;
  const fields = res.fields || [];
  const byKey = Object.fromEntries(fields.map(f => [f.key, f]));
  const field = byKey[ctx.fieldKey];
  const focus = typeof ctx.onFocus === "function" ? ctx.onFocus : () => {};
  const dName = typeof ctx.dispName === "function" ? ctx.dispName
                                                   : (r => r.name);

  const card = el("section", "card tt rm");
  const railCard = el("section", "card tt");
  host.append(card, railCard);

  const head = () => card.appendChild(el("h2", null, "Would this move raise your risk?"));

  // ---- guards ---------------------------------------------------------
  if (!field) {
    head();
    card.appendChild(emptyBox("no field is selected",
      "This card prices a move against one field. Pick a field above."));
    return;
  }
  if (!res.squad || !res.squad.readable) {
    head();
    card.appendChild(emptyBox(res.squad?.note || "your squad could not be read",
      "Run `fpl myteam auth` once, or text /setsquad with your 15. Until then " +
      "this page can describe the field but not your position in it."));
    return;
  }
  if (!(field.measures || []).includes("eo")) {
    head();
    card.appendChild(emptyBox(`${field.label} publishes no effective ownership`,
      "Exposure is your multiplier minus the field's effective ownership. This " +
      "field only reports head-count ownership, and a multiplier minus a head " +
      "count is not a number. Pick a field that publishes EO. This card " +
      "refuses rather than guesses."));
    return;
  }

  // ---- the measured set -----------------------------------------------
  /* rows ∪ differentials, deduped by code: the page above toggles between the
     two, and a move must not appear and vanish with a toolbar the reader has
     forgotten about. THIS is the measured population — every EO below comes
     from `fields[field.key]` on one of these rows. */
  const uni = new Map();
  for (const r of [...(res.rows || []), ...(res.differentials || [])])
    if (r && r.code != null && !uni.has(r.code)) uni.set(r.code, r);
  const all = [...uni.values()];

  /* The swap UNIVERSE, and only that: who exists to be bought. Identity, not
     measurement — see the header note. */
  const wiPlayers = Array.isArray(res.whatif?.players) ? res.whatif.players : null;
  const wiCodes = wiPlayers ? new Set(wiPlayers.map(p => p.code)) : null;
  const inUniverse = r => wiCodes ? wiCodes.has(r.code) : true;

  const fOf = r => (r && r.fields && r.fields[field.key]) || null;
  const eoOf = r => { const f = fOf(r); return f && f.eo != null ? f.eo : null; };

  const squadAll = all.filter(r => r.in_squad === true);
  if (!all.length || !squadAll.length) {
    head();
    card.appendChild(emptyBox(
      all.length ? "none of your 15 appears in these rows"
                 : "this panel returned no players",
      all.length
        ? `The panel returned ${all.length} players and none is flagged as ` +
          `yours, even though the squad read succeeded` +
          (res.squad.n != null ? ` with ${res.squad.n} players` : "") +
          ". This card can only see your squad through those flags, so it " +
          "refuses rather than report a squad of nobody."
        : "There is nothing to lay your squad against."));
    return;
  }

  // priced = measured on this field AND with a known multiplier of yours
  const priced = all.filter(r => eoOf(r) != null && myMult(r).v != null);
  const mySquad = priced.filter(r => r.in_squad === true);
  const cands = priced.filter(r => r.in_squad !== true && inUniverse(r));

  if (!mySquad.length || !cands.length) {
    head();
    card.appendChild(emptyBox(
      `${field.label} measures too few of these players to price a move`,
      `It gives an effective ownership for ${priced.length} of the ` +
      `${all.length} on this page: ${mySquad.length} of yours and ` +
      `${cands.length} you could buy. Blank is not zero, so no move can be ` +
      `priced. Pick a field with wider coverage.`));
    return;
  }

  // the field's held population — the denominator every percentile names
  const heldVals = all.map(eoOf).filter(v => v != null && v >= HELD_FLOOR)
    .sort((a, b) => a - b);
  const pctileOf = v => v == null || v < HELD_FLOOR ? null : pctileIn(heldVals, v);

  /* Tracking exposure: Σ |your multiplier − the field's EO| over the players
     this field measures. Named basis, no blend, no players silently read as
     zero. */
  const gap = (r, over) => {
    const v = eoOf(r);
    const m = over === undefined ? myMult(r).v : over;
    return (v == null || m == null) ? null : Math.abs(m - v / 100);
  };
  const level = priced.reduce((a, r) => a + gap(r), 0);

  /* The two halves of Δ. sell() is what letting a man go does on its own;
     buy() is what taking a man in at multiplier m does on its own. Their sum
     is exact — every other player's term is untouched by the swap. */
  const sellTerm = r => eoOf(r) / 100 - gap(r);
  const buyTerm = (r, m) => Math.abs(m - eoOf(r) / 100) - eoOf(r) / 100;
  /* The incoming player takes the outgoing player's multiplier — or 1× if that
     man was benched, because nobody buys a replacement in order to bench him.
     One rule, printed on the card. */
  const incoming = out => (myMult(out).v === 0 ? 1 : myMult(out).v);

  const termOf = (r, over) => {
    const v = eoOf(r);
    const m = over === undefined ? myMult(r).v : over;
    return (v == null || m == null) ? null : m - v / 100;
  };

  // multiplier axis shared by the two dumbbells in the manual swap
  const AX = Math.max(2, Math.ceil(Math.max(
    0, ...all.map(r => (eoOf(r) ?? 0) / 100)) * 10) / 10);
  const axPos = v => `${(100 * Math.min(Math.max(v, 0), AX) / AX).toFixed(2)}%`;
  card.style.setProperty("--ax1", axPos(1));

  const anyAssumed = mySquad.some(r => myMult(r).assumed);

  // ---- the ranking ----------------------------------------------------
  /* Every (one of yours out) × (anyone of the SAME POSITION this field
     measures that you do not own) at the inherited multiplier. A transfer
     cannot change a slot's position, so a MID never pairs with a GKP. One row
     per man going out, and no buy named more than twice, so the list cannot
     collapse into six near-clones of the same arithmetic. */
  const samePos = (a, b) => a.pos != null && b.pos != null && a.pos === b.pos;
  const moves = [];
  for (const out of mySquad) {
    const m = incoming(out), s = sellTerm(out);
    for (const inn of cands) {
      if (!samePos(out, inn)) continue;
      moves.push({ out, inn, m, d: s + buyTerm(inn, m) });
    }
  }
  function take(list) {
    const usedOut = new Set(), usedIn = new Map(), keep = [];
    for (const mv of list) {
      if (usedOut.has(mv.out.code)) continue;
      if ((usedIn.get(mv.inn.code) || 0) >= IN_CAP) continue;
      usedOut.add(mv.out.code);
      usedIn.set(mv.inn.code, (usedIn.get(mv.inn.code) || 0) + 1);
      keep.push(mv);
      if (keep.length === SHOW) break;
    }
    return keep;
  }
  const lowers = take(moves.filter(m => m.d < -0.005).sort((a, b) => a.d - b.d));
  const raises = take(moves.filter(m => m.d > 0.005).sort((a, b) => b.d - a.d));

  // ---- the card -------------------------------------------------------
  head();

  const lede = el("p", "rm-lede");
  lede.append(
    "Your tracking exposure is ",
    Object.assign(el("b", "big", mult(level)), {
      title: `Σ |your multiplier − ${field.label} effective ownership| over ` +
             `the ${priced.length} players on this page it measures. It is a ` +
             `distance from the average rival, not a points forecast.`,
    }),
    ". Lower means you track the field more closely; higher is a bigger bet " +
    "in both directions.");
  card.appendChild(lede);

  const basis = el("div", "toolbar");
  basis.appendChild(el("span", "tlabel", "Basis"));
  basis.appendChild(Object.assign(el("span", "sub",
    `${field.short || field.label}` +
    (field.n != null ? ` · n=${field.n}` : "") +
    (field.gw != null ? ` · GW${field.gw}` : "") +
    ` · ${priced.length} of ${wiPlayers ? wiPlayers.length : all.length} priced`),
    { title: `${field.label}: ${field.denominator}.\n\n` + (wiPlayers
        ? `The panel lists ${wiPlayers.length} players, which is who may be ` +
          `bought. ${field.label} publishes an effective ownership for ` +
          `${priced.length} of them on this page; the rest cannot be priced ` +
          `against it, and blank is not zero.`
        : `${field.label} publishes an effective ownership for ` +
          `${priced.length} of the ${all.length} players on this page.`) }));
  basis.appendChild(Object.assign(el("span", "chip",
    `your ${mySquad.length}` +
    (res.squad.n != null && mySquad.length !== res.squad.n ? ` of ${res.squad.n}` : "") +
    ` via ${res.squad.source}` + (res.squad.gw != null ? ` at GW${res.squad.gw}` : "")),
    { title: res.squad.n != null && mySquad.length !== res.squad.n
        ? "The rest are outside the rows this panel returned or unmeasured by " +
          "this field, so they are absent from the exposure above. It is a " +
          "coverage hole, not a zero."
        : "Your squad as this page read it." }));
  if (anyAssumed)
    basis.appendChild(Object.assign(el("span", "chip warn", "multipliers inferred"),
      { title: "The squad read supplies roles but not multipliers, so captain " +
               "is taken as 2× and a triple-captain chip would make it 3×." }));
  if (field.gw != null && res.squad.gw != null && field.gw !== res.squad.gw)
    basis.appendChild(Object.assign(el("span", "chip warn",
      `field GW${field.gw} · you GW${res.squad.gw}`),
      { title: "The two sides of every subtraction are not stamped the same week." }));
  if (ctx.measure === "own")
    basis.appendChild(Object.assign(el("span", "chip warn", "priced on EO"),
      { title: "The measure above is set to head-count ownership; this card " +
               "stays on effective ownership, because a multiplier minus a " +
               "head count is not a number." }));
  card.appendChild(basis);

  // ---- the table ------------------------------------------------------
  const tbl = el("table", "rm-moves");
  const thead = el("thead");
  const hr = el("tr");
  for (const [t, c] of [["Move", ""], ["After", "n"], ["Change", "n"]])
    hr.appendChild(el("th", c, t));
  thead.appendChild(hr);
  tbl.appendChild(thead);
  const tb = el("tbody");
  tbl.appendChild(tb);

  function whoLine(r, m) {
    /* The people-count, which is the explanation. Captaincy is quoted when it
       is the thing carrying the number; otherwise the owner count. Fields that
       publish no counts get the EO they do publish, never an invented head. */
    const f = fOf(r) || {};
    if (f.n != null && f.owned_by != null) {
      if (m === 2 && f.captained_by != null && f.captained_by > 0)
        return { s: `${f.captained_by}/${f.n} captain`, t:
          `${f.captained_by} of the ${f.n} managers in ${field.label} captain ` +
          `him, and ${f.owned_by} own him, which is ${pct(f.eo)} effective ` +
          `ownership.` };
      return { s: `${f.owned_by}/${f.n} own`, t:
        `${f.owned_by} of the ${f.n} managers in ${field.label} own him` +
        (f.captained_by ? `, ${f.captained_by} captain him` : "") +
        `, which is ${pct(f.eo)} effective ownership.` };
    }
    return { s: `EO ${pct(eoOf(r))}`, t:
      `${field.label} publishes ${pct(eoOf(r))} effective ownership for him ` +
      `and no manager count, so no head count is quoted.` };
  }

  function sepRow(text) {
    const tr = el("tr", "rm-sep");
    tr.appendChild(Object.assign(el("td", null, text), { colSpan: 3 }));
    tb.appendChild(tr);
  }

  function moveRow(mv, dir, mine) {
    const tr = el("tr", "rm-" + dir + (mine ? " rm-mine" : ""));
    const cell = el("td", "rm-move");
    const line = el("div", "rm-names");
    const nameBtn = (r, cls, m) => {
      const b = el("button", "rm-p " + cls);
      b.appendChild(faceImg(r.code, "avatar"));
      b.appendChild(el("span", "n", dName(r)));
      b.appendChild(el("span", "m", multS(m)));
      b.title = [r.pos, r.team, fmtPrice(r.price),
                 r.xpts != null ? `${fmt1(r.xpts)} xPts` : null]
                .filter(Boolean).join(" · ") + " · open his ladder";
      b.onclick = e => { e.stopPropagation(); focus(r.code); };
      return b;
    };
    line.append(nameBtn(mv.out, "out", myMult(mv.out).v),
                el("span", "arw", "→"),
                nameBtn(mv.inn, "in", mv.m));
    cell.appendChild(line);
    const wo = whoLine(mv.out, myMult(mv.out).v), wi = whoLine(mv.inn, mv.m);
    const who = el("div", "rm-who");
    who.append(Object.assign(el("span", null, wo.s), { title: wo.t }),
               el("span", "dot", "·"),
               Object.assign(el("span", null, wi.s), { title: wi.t }));
    cell.appendChild(who);
    tr.appendChild(cell);

    tr.appendChild(Object.assign(el("td", "n", mult(level + mv.d)),
      { title: `Tracking exposure after the move, on the same ` +
               `${priced.length}-player basis as the ${mult(level)} above.` }));

    const dc = el("td", "n d");
    /* A swap that moves nothing must not claim a direction. Bench for bench is
       the honest zero: neither multiplier ever applies, so no exposure moves. */
    const flat = Math.abs(mv.d) < 0.005;
    if (flat) dc.append(el("span", "mag", "0.00"), el("span", "w", "no change"));
    else dc.append(el("span", "arrow", mv.d > 0 ? "▲" : "▼"),
                   el("span", "mag", Math.abs(mv.d).toFixed(2)),
                   el("span", "w", mv.d > 0 ? "higher" : "lower"));
    dc.title =
      `${sgn2(mv.d)} tracking exposure. Letting ${dName(mv.out)} go is ` +
      `${sgn2(sellTerm(mv.out))} on its own; taking ${dName(mv.inn)} in at ` +
      `${multS(mv.m)} is ${sgn2(buyTerm(mv.inn, mv.m))}. Every other player's ` +
      `term is untouched, so those two are the whole change.`;
    tr.appendChild(dc);
    tb.appendChild(tr);
    return tr;
  }

  if (!lowers.length && !raises.length) {
    card.appendChild(el("p", "sub",
      "No swap this field can price moves your exposure at all."));
  } else {
    if (lowers.length) {
      sepRow("moves that lower it");
      for (const mv of lowers) moveRow(mv, "down");
    }
    if (raises.length) {
      sepRow("moves that raise your risk");
      for (const mv of raises) moveRow(mv, "up");
    }
    card.appendChild(tbl);
  }

  card.appendChild(el("p", "rm-gen",
    "One row per player you sell, against every player of the same position " +
    "this field measures that you do not own, at his multiplier. Same position " +
    "only: a transfer cannot change a slot's position."));

  const howto = el("details", "howto");
  howto.appendChild(el("summary", null, "How this ranking is built"));
  howto.appendChild(el("p", "sub",
    `Tracking exposure is Σ |your multiplier − ${field.label} effective ` +
    `ownership| over the ${priced.length} players on this page it measures: ` +
    `a distance from the average rival in that field, and the reason it has a ` +
    `direction at all. The change a swap makes splits exactly in two: letting ` +
    `a man go is eo − |your multiplier − eo| for him, taking a man in at ` +
    `multiplier m is |m − eo| − eo for him, and nobody else's term moves. So ` +
    `every pair is two additions, all ${moves.length} of them are computed on ` +
    `load, and the manual swap below is priced by the same formula.`));
  howto.appendChild(el("p", "sub",
    `The incoming player takes the outgoing player's multiplier, or 1× if that ` +
    `man was benched. A benched slot priced at 0× can never move anything, ` +
    `and nobody buys a replacement in order to bench him. The armband is ` +
    `therefore only ever moved by selling the man who has it; this card will ` +
    `not re-captain your squad on its own. Only one row per man going out is ` +
    `shown, and no buy is named more than ${IN_CAP} times, because without ` +
    `that the list fills with six near-identical ways to spend the same ` +
    `multiplier on somebody the field does not own.`));
  howto.appendChild(el("p", "sub",
    `Who may be bought comes from the panel's what-if universe` +
    (wiPlayers ? `, all ${wiPlayers.length} current-season players` : "") +
    `. What each of them COSTS you comes from this page's own per-field rows, ` +
    `because the what-if block's effective ownership is keyed to one field ` +
    `only and cannot answer for the one you have selected. A player that ` +
    `field does not measure is left out rather than read as zero, which is ` +
    `why the basis line prints ${priced.length} and not ` +
    `${wiPlayers ? wiPlayers.length : all.length}.`));
  card.appendChild(howto);

  card.appendChild(el("p", "rm-guard",
    "Not legality checked. Not a points forecast."));

  // ---- the manual swap ------------------------------------------------
  /* The old simulator, kept in mechanism and re-encoded: its answer arrives as
     a row in the SAME table above, marked as yours, instead of as a block of
     "2.00× − 1.65× = +0.35" arithmetic. */
  let outCode = null, inCode = null, inRole = null, query = "";
  const man = el("details", "rm-manual");
  man.appendChild(el("summary", null, "Try a move of your own"));
  const manTop = el("div"), manBody = el("div");
  man.append(manTop, manBody);
  card.appendChild(man);

  function renderPickers() {
    manTop.textContent = "";

    const row1 = el("div", "toolbar");
    row1.appendChild(el("span", "tlabel", "Take out"));
    const outSel = el("select");
    outSel.appendChild(Object.assign(el("option", null, "pick one of your 15"),
                                     { value: "" }));
    for (const r of [...mySquad].sort((a, b) => (eoOf(b) ?? -1) - (eoOf(a) ?? -1)))
      outSel.appendChild(Object.assign(el("option", null,
        `${dName(r)} · ${r.pos ?? "?"} · ${ROLE_NAME[r.your_role] || multS(myMult(r).v)}` +
        ` · EO ${pct(eoOf(r))}`), { value: String(r.code) }));
    outSel.value = outCode == null ? "" : String(outCode);
    outSel.onchange = () => {
      outCode = outSel.value ? Number(outSel.value) : null;
      inRole = null;                        // re-inherit from the new outgoing
      // an incoming man of another position cannot fill the new slot
      if (inCode != null && outCode != null
          && !samePos(uni.get(outCode) || {}, uni.get(inCode) || {})) inCode = null;
      renderPickers(); renderResult();
    };
    row1.appendChild(outSel);
    manTop.appendChild(row1);

    const row2 = el("div", "toolbar");
    row2.appendChild(el("span", "tlabel", "Bring in"));
    const q = el("input");
    q.type = "text"; q.placeholder = "search a player…"; q.size = 16; q.value = query;
    q.oninput = () => { query = q.value; renderCands(); };
    row2.appendChild(q);
    if (inCode != null) {
      const r = uni.get(inCode);
      const chip = el("button", "chip s1", `✓ ${dName(r)} · EO ${pct(eoOf(r))}`);
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
    manTop.appendChild(row2);

    // role for the incoming player — a choice, never a silent assumption
    const row3 = el("div", "toolbar");
    row3.appendChild(el("span", "tlabel", "He comes in as"));
    const seg = el("span", "seg");
    const outR = outCode != null ? (uni.get(outCode) || {}).your_role : null;
    const eff = inRole ?? (outR === "bench" ? "start" : outR) ?? "start";
    for (const k of ["bench", "start", "captain"]) {
      const b = el("button", k === eff ? "on" : "", ROLE_NAME[k]);
      b.onclick = () => { inRole = k; renderPickers(); renderResult(); };
      seg.appendChild(b);
    }
    row3.appendChild(seg);
    row3.appendChild(el("span", "sub",
      inRole == null
        ? "Inherited from the player going out, and started if he was benched."
        : "Your choice, not a plan: nothing here checks that the rest of your " +
          "team can field it."));
    manTop.appendChild(row3);

    const candWrap = el("div");
    manTop.appendChild(candWrap);
    renderCands();

    function renderCands() {
      candWrap.textContent = "";
      if (inCode != null && !query) return;
      const term = query.trim().toLowerCase();
      // only the outgoing man's position is offered: a transfer keeps the slot
      const outP = outCode != null ? (uni.get(outCode) || {}).pos : null;
      const pool = outP ? cands.filter(r => r.pos === outP) : cands;
      let list = pool.slice();
      if (term) list = list.filter(r =>
        String(r.name || "").toLowerCase().includes(term) ||
        String(r.team || "").toLowerCase().includes(term));
      list.sort((a, b) => eoOf(b) - eoOf(a));
      const posNote = outP ? ` (${outP} only, the position of the man going out)` : "";
      candWrap.appendChild(el("p", "sub", term
        ? `${list.length} of the ${pool.length} this field can price` + posNote +
          ` match “${query.trim()}”, heaviest first.`
        : `The players ${field.short || field.label} is heaviest on that you ` +
          `do not own` + posNote + `.`));
      const grid = el("div", "ttcands");
      for (const r of list.slice(0, 12)) {
        const b = el("button", "ttcand");
        b.appendChild(faceImg(r.code, "avatar"));
        const d = el("div");
        d.appendChild(el("div", "n", dName(r)));
        d.appendChild(el("div", "sub",
          `${r.pos ?? "?"} · ${fmtPrice(r.price)} · EO ${pct(eoOf(r))}`));
        b.appendChild(d);
        b.onclick = () => { inCode = r.code; query = ""; renderPickers(); renderResult(); };
        grid.appendChild(b);
      }
      if (!list.length) candWrap.appendChild(el("p", "sub", "no player matches."));
      candWrap.appendChild(grid);
    }
  }

  function renderResult() {
    manBody.textContent = "";
    // a previous answer must not linger under a half-specified swap
    for (const old of [...tb.querySelectorAll("tr.rm-mine, tr.rm-mysep")]) old.remove();
    if (outCode == null || inCode == null) {
      manBody.appendChild(el("p", "sub",
        "Pick one out and one in. Nothing is computed until both are chosen. " +
        "a half-specified swap has no term."));
      return;
    }
    const out = uni.get(outCode), inn = uni.get(inCode);
    const missing = [];
    if (eoOf(out) == null) missing.push(`${field.label} publishes no EO for ${out.name}`);
    if (eoOf(inn) == null) missing.push(`${field.label} publishes no EO for ${inn.name}`);
    if (myMult(out).v == null) missing.push(`your squad read gives no role for ${out.name}`);
    if (!samePos(out, inn)) missing.push(
      `${out.name} is a ${out.pos ?? "?"} and ${inn.name} a ${inn.pos ?? "?"}; a transfer cannot change a slot's position`);
    if (missing.length) {
      manBody.appendChild(emptyBox(missing.join("; ") + ".",
        "One side of the subtraction is blank, and blank is not zero. Pick a " +
        "player this field measures, or a different field."));
      return;
    }

    const outM = myMult(out).v;
    const newM = ROLE_MULT[inRole ?? (out.your_role === "bench" ? "start" : out.your_role) ?? "start"] ?? 1;
    const mv = { out, inn, m: newM, d: sellTerm(out) + buyTerm(inn, newM) };

    // the answer, in the same encoding as the generated rows
    const sep = el("tr", "rm-sep rm-mysep");
    sep.appendChild(Object.assign(el("td", null, "your move"), { colSpan: 3 }));
    tb.appendChild(sep);
    const tr = moveRow(mv, Math.abs(mv.d) < 0.005 ? "flat"
                             : mv.d > 0 ? "up" : "down", true);
    tr.scrollIntoView({ block: "nearest" });

    // and where the two multipliers land, drawn rather than spelled out
    const moved = el("div", "ttmoved");
    moved.appendChild(axisHeader());
    moved.appendChild(dumbbell(out, { mine: 0, ghost: outM, chip: "out", chipCls: "warn" }));
    moved.appendChild(dumbbell(inn, { mine: newM, ghost: 0, chip: "in", chipCls: "s1" }));
    manBody.appendChild(moved);
    manBody.appendChild(el("p", "sub",
      "Your move is in the table above. The hollow square is where your " +
      "multiplier was; the filled one is where it lands. The field's ring does " +
      "not move: this card only ever changes your own side."));
    markLegend(manBody);
  }

  renderPickers();
  renderResult();

  /* One dumbbell row. Both marks live on the same 0–AX× axis: a hollow ring for
     the field's EO, a filled square for your multiplier, and the segment
     between them is the term. Shape carries identity, never colour alone. */
  function dumbbell(r, opts = {}) {
    const v = eoOf(r), m = opts.mine === undefined ? myMult(r).v : opts.mine;
    const t = (v == null || m == null) ? null : m - v / 100;
    const row = el("div", "ttrow");
    row.appendChild(faceImg(r.code, "avatar" + (r.in_squad === true ? " mine" : "")));

    const id = el("div", "ttid");
    const nm = el("div", "ttname");
    nm.appendChild(el("span", "n", dName(r)));
    if (opts.chip) nm.appendChild(el("span", "chip " + (opts.chipCls || ""), opts.chip));
    id.appendChild(nm);
    id.appendChild(el("div", "sub",
      [r.pos, r.team, fmtPrice(r.price)].filter(Boolean).join(" · ")));
    const pc = pctileOf(v);
    const ctxLine = el("div", "sub ctx");
    ctxLine.textContent = `EO ${pct(v)} · ` + (pc == null
      ? `under ${HELD_FLOOR}%, outside the ${heldVals.length} held`
      : `${ord(pc)} pct of ${heldVals.length} held`);
    ctxLine.title = v == null
      ? `${field.label} publishes no effective ownership for him.`
      : `${field.label} effective ownership ${pct(v)}` + (pc == null
        ? `, below the ${HELD_FLOOR}% floor, outside the ` +
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
      link.style.background = t == null ? "var(--ttl-mid)"
        : t >= 0 ? "var(--ttl-light)" : "var(--ttl-heavy)";
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
      : `your ${mult(m)} against the field's ${mult(v / 100)} is ${sgn2(t)} per ` +
        `point he scores`;
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
    h.appendChild(el("span", ""));
    h.appendChild(el("span", ""));
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
    host2.appendChild(leg);
  }

  // ================================================= the one honesty rail
  /* THE PANEL IS THE AUTHORITY ON WHAT MAY BE RECOMPUTED HERE. Where the
     payload publishes `whatif.safe_to_recompute` / `not_safe_to_recompute`,
     those sentences are rendered verbatim rather than paraphrased. The things
     the panel cannot know — that this card refuses to multiply a term by
     points, and that it never checked a squad's legality — are the view's own
     to declare. This is the only rail left on the page. */
  const rail = el("details", "ttrail");
  rail.appendChild(el("summary", null, "What these numbers do not cover"));
  const d = el("div", "ttcaveats");
  const item = (b, t) => {
    const s = el("div", "ttcav");
    if (b) s.appendChild(el("b", null, b));
    s.appendChild(el("span", null, t));
    d.appendChild(s);
  };
  item("Points.",
    " rank move ≈ Σ (your multiplier − the field's EO) × points, and this card " +
    "computes the multiplier side only. It has no view on what anyone will " +
    "score, so nothing above is a projected score, a rank, or a " +
    "recommendation. The xPts shown are the payload's consensus, printed as " +
    "context and never multiplied in.");
  item("Legality.",
    " budget, selling price, the three-per-club cap and formation are not " +
    "checked here, so a swap this card prices may be impossible. The planner and " +
    "the solver own legality.");

  const wi = res.whatif;
  const nope = Array.isArray(wi?.not_safe_to_recompute) ? wi.not_safe_to_recompute : null;
  if (nope && nope.length) {
    item(null, "The panel states what a client may recompute from this payload " +
      "and what it may not. Its own words, unedited:");
    for (const line of nope) item("·", " " + line);
  } else {
    const you = res.selection?.includes_you;
    item("The field.",
      ` ${field.label} is held fixed at its measured values` +
      (field.gw != null ? ` (gameweek ${field.gw})` : "") + ". " +
      (you === true
        ? `Your own entry is inside this field, so a transfer of yours does ` +
          `move it` + (field.n != null ? `, by up to 1 manager in ${field.n}` : "") +
          `, and this card does not model that. Compare against a field you ` +
          `are not in.`
        : you === false
        ? `Your entry is not in it, so one transfer of yours cannot move it. ` +
          `If the field makes the same move, none of this holds.`
        : `The payload does not say whether your own entry is inside it. If it ` +
          `is, a transfer of yours moves the field too, and this card does not ` +
          `model that.`));
    item("Another selection or gameweek.",
      " every field number here is measured over one set of managers at one " +
      "gameweek. Nothing on this card can be re-aimed at a different set or a " +
      "different week without refetching the panel.");
  }
  item("Coverage.",
    ` this card prices ${priced.length} players` +
    (wiPlayers ? ` of the ${wiPlayers.length} the panel lists` : "") +
    `, because those are the ones ${field.label} publishes an effective ` +
    `ownership for on this page. The rest are not zero, they are unmeasured, ` +
    `so the exposure above is a sum over a named set and not a whole-game ` +
    `total.`);
  if (anyAssumed || res.squad.has_multipliers === false)
    item("Your multipliers.",
      " the squad read supplies roles, not multipliers, so captain is taken as " +
      "2× and a triple-captain chip would make it 3×. A bench boost would put " +
      "all fifteen on the pitch and no chip is visible from here.");
  if (field.gw != null && res.squad.gw != null && field.gw !== res.squad.gw)
    item("Two gameweeks.",
      ` the field is a GW${field.gw} read and your squad is a GW${res.squad.gw} ` +
      "read, so the two sides of every subtraction are not stamped the same week.");

  const safe = Array.isArray(wi?.safe_to_recompute) ? wi.safe_to_recompute : null;
  if (safe && safe.length) {
    const det = el("details", "ttsafe");
    det.appendChild(el("summary", null,
      `What the panel says this card MAY recompute in the browser (${safe.length})`));
    for (const line of safe) {
      const s = el("div", "ttcav");
      s.appendChild(el("b", null, "·"));
      s.appendChild(el("span", null, " " + line));
      det.appendChild(s);
    }
    d.appendChild(det);
  }
  rail.appendChild(d);
  railCard.appendChild(rail);
}

export default renderTools;
