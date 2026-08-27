/* Template & EO — built around ONE question, not around a table.
 *
 * The engine's objective is P(top-1k), not expected points, and
 * (docs/platform/rank_objectives.md §0–§1)
 *
 *     rank move ≈ Σ over players of (my multiplier − the field's EO) × points
 *
 * Template holdings CANCEL out of that sum. So a high-EO player is insurance,
 * not upside: owning him is neutral, missing him is a hole. The number that
 * actually carries information is the GAP between the field you are racing and
 * the game as a whole — which is why this page is a comparison of two fields
 * everywhere, never a single ownership column.
 *
 * Consequences that shaped every decision below:
 *   - The gap is genuinely diverging around zero, so it gets a diverging
 *     encoding (two hues + neutral gray midpoint) and a y=x reference line that
 *     makes "template", "neutral" and "fade" SPATIAL rather than numeric.
 *   - Like is only ever compared with like. `own` (head count) and `eo`
 *     (Σ multipliers) are separate measures with separate baselines; the page
 *     will refuse to plot one against the other.
 *   - Every percentage names its denominator, from `fields[].denominator`.
 *     311 managers is not a lot, and 49 of them are the owner's own
 *     mini-league — the composition strip says so on the page, not in a doc.
 *   - Nothing is labelled from a hard-coded string. Cohort names, gameweeks,
 *     provider names and freshness all come from the payload, because the
 *     panel can report top1k under the same keys it reports elite under.
 */

import { runPanel, el, emptyBox, errBox, provenance, faceImg,
         fmtPrice, fmt1, fmt2 } from "/js/app.js";

/* ---------------------------------------------------------------- helpers */

const NS = "http://www.w3.org/2000/svg";
function sv(tag, attrs, text) {
  const n = document.createElementNS(NS, tag);
  for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
  if (text != null) n.textContent = text;
  return n;
}

const pct = v => v == null ? "–" : `${Number(v).toFixed(1)}%`;
const signed = v => v == null ? "–"
  : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}`;

/* Same freshness vocabulary as the xPoints view, so a dot means one thing
   across the app. Unparseable timestamps say "?" rather than guessing. */
function ageInfo(iso) {
  if (!iso) return { cls: "bad", text: "age unknown" };
  const h = (Date.now() - new Date(String(iso).replace(" ", "T"))) / 3.6e6;
  if (!isFinite(h)) return { cls: "bad", text: "age unknown" };
  if (h < 36) return { cls: "good", text: h < 1.5 ? "fresh" : `${Math.round(h)}h old` };
  if (h < 72) return { cls: "warn", text: `${Math.round(h)}h old` };
  return { cls: "bad", text: `${Math.round(h / 24)}d old` };
}

const MEASURE = {
  eo: {
    label: "Effective ownership",
    short: "EO",
    blurb: "Share × FPL multiplier, so captaincy counts and 100%+ is normal. " +
           "This is the exact term that cancels out of your rank move.",
  },
  own: {
    label: "Ownership",
    short: "own",
    blurb: "Head-count share, no captaincy. Cruder than EO, but it answers " +
           "“do they actually hold him” without the armband blurring it.",
  },
};

/* My side of the identity. A read that carried a real multiplier is used as
   given; otherwise the role is converted with the standard weights and the
   result is FLAGGED as assumed — a triple captain would make it 3 and this
   page cannot see chips. "Not owned" is a measured 0, never an assumption. */
const ROLE_MULT = { captain: 2, start: 1, bench: 0 };
function myMult(r) {
  if (r.your_mult != null) return { v: r.your_mult, assumed: false };
  if (r.your_role && ROLE_MULT[r.your_role] != null)
    return { v: ROLE_MULT[r.your_role], assumed: true };
  if (r.in_squad === false) return { v: 0, assumed: false };
  return { v: null, assumed: false };   // owned but role unknown, or unreadable
}

function roleChip(r) {
  if (r.in_squad == null) return el("span", "chip", "unknown");
  if (r.in_squad === false) return el("span", "chip dim", "not owned");
  const role = r.your_role;
  if (role === "captain") return el("span", "chip good", "captain 2×");
  if (role === "bench") return el("span", "chip warn", "benched 0×");
  if (role === "start") return el("span", "chip good", "starting 1×");
  return el("span", "chip good", "owned");
}

/* ------------------------------------------------------------------ view */

export default async function view(host) {
  // ---- shell ----------------------------------------------------------
  const head = el("section", "card");
  const mapCard = el("section", "card");
  const ledgerCard = el("section", "card");
  const tableCard = el("section", "card");
  const foot = el("div");
  host.append(head, mapCard, ledgerCard, tableCard, foot);

  const drawer = el("aside", "drawer");
  document.body.appendChild(drawer);
  const closeDrawer = () => drawer.classList.remove("open");
  addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

  head.appendChild(el("h2", null, "The field you're racing"));
  const teach = el("div", "teach");
  head.appendChild(teach);
  const measureRow = el("div", "toolbar");
  const fieldRow = el("div", "toolbar");
  const compRow = el("div", "toolbar comp");
  const tiles = el("div", "stats");
  head.append(measureRow, fieldRow, compRow, tiles);

  let res, prov;
  try {
    ({ result: res, provenance: prov } = await runPanel("ownership_eo", { limit: 200 }));
  } catch (e) { head.appendChild(errBox(e)); return; }
  if (res?.empty) { head.appendChild(emptyBox(res.reason)); return; }

  const allFields = res.fields || [];
  const byKey = Object.fromEntries(allFields.map(f => [f.key, f]));

  // ---- state ----------------------------------------------------------
  const has = (f, m) => (f.measures || []).includes(m);
  const pickable = m => allFields.filter(f => f.role === "field" && has(f, m));
  const baseOf = m => allFields.find(f => f.role === "baseline" && has(f, m));

  let measure = pickable("eo").length && baseOf("eo") ? "eo"
              : pickable("own").length && baseOf("own") ? "own" : "eo";
  let fieldKey = (pickable(measure).find(f => f.kind === "cohort")
                  || pickable(measure)[0] || {}).key;
  let rowset = "template";           // template | diff
  let pos = "", team = "", search = "", mineOnly = false, band = "all";
  let sortBy = { kind: "gap" }, sortDir = -1;

  const BAND = 10;                   // percentage points — stated, not implied

  // ---- derived --------------------------------------------------------
  const val = (r, key, m) => {
    const f = r.fields && r.fields[key];
    const v = f ? f[m] : null;
    return v == null ? null : v;
  };
  /* EXPOSURE IS ALWAYS EO. The identity's second term is effective ownership
     — Σ multipliers — and subtracting a head-count share from a multiplier is
     a units error, not a simplification. So the map's gap follows whichever
     measure the reader picked, but every exposure number on the page reads the
     field's `eo` and says EO in its label. A field with no EO gets an explained
     empty state instead of a wrong number. */
  const eoVal = r => val(r, fieldKey, "eo");
  const hasEo = () => (byKey[fieldKey]?.measures || []).includes("eo");
  const exposureOf = r => {
    const m = myMult(r), v = eoVal(r);
    return m.v == null || v == null ? null : m.v - v / 100;
  };
  const gapOf = (r, key, m) => {
    const b = baseOf(m);
    if (!b) return null;
    const x = val(r, b.key, m), y = val(r, key, m);
    return x == null || y == null ? null : y - x;
  };
  const sourceRows = () =>
    (rowset === "diff" ? res.differentials : res.rows) || [];

  /* Diverging ramp: two hues with a NEUTRAL GRAY midpoint, mixed in oklab.
     Both poles are app tokens (--s2 / --s1) so they are defined in both
     themes; the midpoint is the muted ink, which is also both-theme. */
  /* Anchored at the 90th percentile of |gap|, not the maximum: one
     captain-heavy premium at ±80pp would otherwise flatten every ordinary
     ±15pp gap to the same neutral gray. Values past the anchor saturate, and
     the legend says so — a clamped scale that admits it is honest, a scale
     silently dominated by one outlier is not. */
  const rampScale = () => {
    const gs = sourceRows().map(r => gapOf(r, fieldKey, measure))
      .filter(g => g != null).map(Math.abs).sort((a, b) => a - b);
    if (!gs.length) return 10;
    return Math.max(8, gs[Math.floor(gs.length * 0.9)] ?? gs[gs.length - 1]);
  };
  const rampColor = (g, scale) => {
    if (g == null) return "var(--tpl-mid)";
    const t = Math.min(1, Math.abs(g) / (scale || 1));
    const pole = g >= 0 ? "var(--tpl-heavy)" : "var(--tpl-light)";
    return `color-mix(in oklab, ${pole} ${Math.round(100 * t)}%, var(--tpl-mid))`;
  };

  // ---- header ---------------------------------------------------------
  function renderTeach() {
    teach.textContent = "";
    const eq = el("div", "identity");
    eq.append(
      el("span", "eq-lead", "rank move"),
      el("span", "eq-op", "≈"),
      el("span", "eq-sum", "Σ"),
      el("span", "eq-term mine", "your multiplier"),
      el("span", "eq-op", "−"),
      el("span", "eq-term theirs", "the field's EO"),
      el("span", "eq-op", "×"),
      el("span", "eq-lead", "points"));
    teach.appendChild(eq);
    teach.appendChild(el("p", "sub",
      "Template holdings cancel out of that sum. A player the field is loaded " +
      "on is insurance, not upside — owning him moves you almost nothing, " +
      "missing him is ruinous. So the number that carries information is not " +
      "ownership: it is the GAP between the field you are racing and the game " +
      "as a whole."));

    const key = el("div", "zonekey");
    const zone = (cls, name, text) => {
      const d = el("div", "zone " + cls);
      d.appendChild(el("span", "sw"));
      d.appendChild(el("b", null, name));
      d.appendChild(el("span", null, text));
      return d;
    };
    key.append(
      zone("heavy", "Template",
        `field is ${BAND}pp+ heavier than the game — cover it or carry the risk`),
      zone("mid", "Neutral", "field and game agree — this holding is noise"),
      zone("light", "Fade",
        `field is ${BAND}pp+ lighter — a real differential lives here`));
    teach.appendChild(key);
  }

  function renderMeasure() {
    measureRow.textContent = "";
    measureRow.appendChild(el("span", "tlabel", "Measure"));
    const seg = el("span", "seg");
    for (const m of ["eo", "own"]) {
      const usable = pickable(m).length && baseOf(m);
      const b = el("button", m === measure ? "on" : "", MEASURE[m].label);
      b.title = usable ? MEASURE[m].blurb
        : `No field publishes ${MEASURE[m].label.toLowerCase()} in this warehouse yet.`;
      if (!usable) b.disabled = true;
      else b.onclick = () => {
        measure = m;
        if (!pickable(m).some(f => f.key === fieldKey))
          fieldKey = (pickable(m)[0] || {}).key;
        renderAll();
      };
      seg.appendChild(b);
    }
    measureRow.appendChild(seg);
    measureRow.appendChild(el("span", "sub", MEASURE[measure].blurb));
  }

  function renderFields() {
    fieldRow.textContent = "";
    fieldRow.appendChild(el("span", "tlabel", "Field"));
    const options = pickable(measure);
    if (!options.length) {
      fieldRow.appendChild(el("span", "sub",
        "No field beyond the whole game can be measured yet."));
      return;
    }
    for (const f of options) {
      const on = f.key === fieldKey;
      const a = ageInfo(f.as_of);
      const chip = el("button", "chip src" + (on ? " on" : ""));
      chip.append(on ? "✓ " : "", el("span", "freshdot " + a.cls),
                  ` ${f.short || f.label}`);
      if (f.n != null) chip.appendChild(el("span", "cnt", `n=${f.n}`));
      chip.title =
        `${f.label}\n% of: ${f.denominator}\n` +
        (f.gw != null ? `gameweek ${f.gw}` : "no gameweek stamp") +
        ` · ${f.players ?? "?"} players measured · ${a.text}` +
        (f.same_values_as_gw != null
          ? `\nValues are byte-identical to GW${f.same_values_as_gw}: the feed ` +
            `re-stamped a settled gameweek, it is not a fresh forecast.`
          : "") +
        (f.note ? `\n${f.note}` : "");
      chip.onclick = () => { fieldKey = f.key; renderAll(); };
      fieldRow.appendChild(chip);
    }
    const b = baseOf(measure);
    const bf = ageInfo(b?.as_of);
    const baseline = el("span", "baseline");
    baseline.append("compared against ", el("b", null, b ? b.label : "—"),
                    " ", el("span", "freshdot " + bf.cls));
    if (b) baseline.title = `% of: ${b.denominator}` +
      (b.gw != null ? ` · gameweek ${b.gw}` : "") + ` · ${bf.text}`;
    fieldRow.appendChild(baseline);
  }

  /* Composition is a disclosure, not decoration: a cohort that is 16% the
     owner's own mini-league opponents is not an independent read of the
     field, and the page has to say so where the number is used. */
  function renderComposition() {
    compRow.textContent = "";
    const f = byKey[fieldKey];
    if (!f) return;
    compRow.appendChild(el("span", "tlabel", "Who is in it"));
    compRow.appendChild(el("span", "sub", f.denominator + "."));

    const gws = [];
    const b = baseOf(measure);
    if (f.gw != null && b && b.gw != null && f.gw !== b.gw)
      gws.push(`field is GW${f.gw}, baseline is GW${b.gw} — different gameweeks`);
    if (f.same_values_as_gw != null)
      gws.push(`values identical to GW${f.same_values_as_gw} (a re-stamped feed)`);
    for (const g of gws) compRow.appendChild(el("span", "chip warn", g));

    if (!f.composition || !f.composition.length) return;
    /* Emphasis, not a four-colour breakdown: the story is not "here are the
       tag proportions", it is "this share of the cohort is a conflict of
       interest". Flagged tags take the warning token — a status colour with
       a label beside it — and everything else stays neutral. */
    const FLAG = {
      mini_league: "Your own mini-league opponents. Their picks correlate with " +
        "each other and with yours, so this cohort is not an independent read " +
        "of the field.",
      "(no manager row)": "Squads stored with no manager row to classify them " +
        "— a crawl bug, counted rather than dropped.",
    };
    const total = f.composition.reduce((a, c) => a + c.n, 0) || 1;
    const strip = el("div", "compstrip");
    strip.title = "the crawl tags behind this cohort, by share of the tag total";
    for (const c of f.composition) {
      const seg = el("span", "cseg" + (FLAG[c.tag] ? " flag" : ""));
      seg.style.width = `${(100 * c.n / total).toFixed(2)}%`;
      seg.title = `${c.n} — ${c.label || c.tag}`;
      strip.appendChild(seg);
    }
    compRow.appendChild(strip);
    for (const c of f.composition) {
      const flag = FLAG[c.tag];
      const chip = el("span", "chip comp" + (flag ? " warn" : ""));
      chip.appendChild(el("span", "cdot" + (flag ? " flag" : "")));
      chip.append(` ${c.n} ${c.label || c.tag}`);
      if (flag) chip.title = flag;
      compRow.appendChild(chip);
    }
    if (f.overlaps) compRow.appendChild(el("span", "sub",
      `Tags overlap — one manager can carry two, so these sum above ${f.n}.`));
  }

  /* Tiles answer the ten-second question. Every one of them states the basis
     it is computed over; none of them is a rate with an unnamed denominator. */
  const TOP_N = 20;
  function topField() {
    return [...sourceRows()]
      .filter(r => val(r, fieldKey, measure) != null)
      .sort((a, b) => val(b, fieldKey, measure) - val(a, fieldKey, measure))
      .slice(0, TOP_N);
  }
  function renderTiles() {
    tiles.textContent = "";
    const f = byKey[fieldKey];
    const top = topField();
    if (!top.length) {
      tiles.appendChild(el("p", "sub",
        "No player has a value on this field yet, so nothing can be summarised."));
      return;
    }
    const tile = (v, k, cls, title) => {
      const d = el("div", "stat" + (cls ? " " + cls : ""));
      d.appendChild(el("div", "v", v));
      d.appendChild(el("div", "k", k));
      if (title) d.title = title;
      tiles.appendChild(d);
      return d;
    };

    if (res.squad?.readable) {
      const owned = top.filter(r => r.in_squad === true).length;
      tile(`${owned}/${top.length}`, `top-${TOP_N} template you own`,
           owned >= top.length * 0.6 ? "good" : "bad",
           `Of the ${TOP_N} players with the highest ${MEASURE[measure].label}` +
           ` in ${f.label}, you hold ${owned}.`);

      // Net exposure over that same stated basis, in the identity's own units.
      let net = 0, known = 0, assumed = 0;
      for (const r of top) {
        const e = exposureOf(r);
        if (e == null) continue;
        net += e; known++; if (myMult(r).assumed) assumed++;
      }
      if (known) tile(signed(net), `net EO exposure over those ${known}`,
           net >= 0 ? "good" : "bad",
           "Σ (your multiplier − field EO) over the same top-" + TOP_N +
           " basis — always EO, never ownership, because a multiplier minus a " +
           "head-count share is not a number. Negative means the field is " +
           "ahead of you on the template: for every point those players score " +
           "you lose " + Math.abs(net).toFixed(1) + " to it." +
           (assumed ? `\n${assumed} of these use a multiplier inferred from ` +
                      "your squad role, not one the read supplied." : ""));

      const holes = top.filter(r => r.in_squad === false);
      if (holes.length) {
        const worst = holes[0];
        tile(worst.name, `biggest hole · ${pct(val(worst, fieldKey, measure))}`,
             "bad",
             `The highest-${MEASURE[measure].short} player in ${f.label} that ` +
             `you do not own. If he hauls, the field gains and you do not.`);
      } else {
        tile("none", `top-${TOP_N} template fully covered`, "good",
             "You hold every player in the stated basis.");
      }

      const bets = [...sourceRows()]
        .filter(r => r.in_squad === true && exposureOf(r) != null)
        .sort((a, b) => exposureOf(b) - exposureOf(a));
      if (bets.length) {
        const b0 = bets[0];
        tile(b0.name, `furthest ahead · ${signed(exposureOf(b0))}`, "good",
             "Where your multiplier most exceeds the field's EO, over every " +
             "player shown. It is not necessarily a differential — a captain " +
             "the field also owns can land here.");
      }
    } else {
      tile("unreadable", "your squad", "bad", res.squad?.note || "");
    }

    const heavy = top.filter(r => (gapOf(r, fieldKey, measure) ?? 0) >= BAND).length;
    tile(String(heavy), `of top ${TOP_N} are ${BAND}pp+ above the game`, null,
         `How concentrated this field is relative to ${baseOf(measure)?.label}.`);
  }

  // ---- the field map --------------------------------------------------
  function renderMap() {
    mapCard.textContent = "";
    const f = byKey[fieldKey], b = baseOf(measure);
    mapCard.appendChild(el("h2", null, "The field map"));
    if (!f || !b) {
      mapCard.appendChild(emptyBox(
        "no two comparable fields",
        "The map needs a field and a same-measure baseline. Ingest the " +
        "LiveFPL ownership feed or run the manager picks crawl."));
      return;
    }
    mapCard.appendChild(el("p", "sub",
      `Every player, positioned by what the game holds (horizontal) against ` +
      `what ${f.label} holds (vertical) — same measure, same units on both ` +
      `axes. The diagonal is where the two agree; distance from it IS the ` +
      `gap, so the template, the neutral middle and the fades are places on ` +
      `the page rather than numbers to compare.`));

    const pts = sourceRows()
      .map(r => ({ r, x: val(r, b.key, measure), y: val(r, f.key, measure) }))
      .filter(p => p.x != null && p.y != null);
    if (!pts.length) {
      mapCard.appendChild(emptyBox(
        `no player has both a ${b.short || b.label} and a ${f.short || f.label} value`));
      return;
    }
    const FLOOR = 2;
    const shown = pts.filter(p => Math.max(p.x, p.y) >= FLOOR);
    const hidden = pts.length - shown.length;

    /* SQUARE-ROOT AXES, the same transform on both. A linear square domain is
       unreadable here: one captain-heavy premium runs to ~150% EO while two
       thirds of the board sits under 20%, so the interesting cluster collapses
       into a corner. √ is monotone and applied identically to x and y, so the
       y = x reference line is still exactly the diagonal and "above the line"
       still means exactly what it meant — only the spacing changes. The ticks
       are deliberately unevenly spaced so the nonlinearity is visible rather
       than smuggled in, and the caption says it in words. */
    const W = 760, H = 470, L = 56, R = 20, T = 18, B = 48;
    const hi = Math.max(20, ...shown.map(p => Math.max(p.x, p.y)));
    const dom = Math.ceil(hi / 20) * 20;                 // square domain
    const rt = v => Math.sqrt(Math.max(0, v)) / Math.sqrt(dom);
    const sx = v => L + (W - L - R) * rt(v);
    const sy = v => H - B - (H - B - T) * rt(v);
    const TICKS = [0, 5, 10, 20, 40, 60, 80, 120, 160, 240]
      .filter(v => v <= dom).concat(dom);

    const wrap = el("div", "chartwrap");
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "fieldmap",
                            role: "img" });
    svg.appendChild(sv("title", {},
      `${f.label} against ${b.label}, ${shown.length} players`));

    // grid — solid hairlines, one shade off the surface, never dashed
    for (const v of new Set(TICKS)) {
      svg.appendChild(sv("line", { x1: sx(v), x2: sx(v), y1: T, y2: H - B,
                                   class: "grid" }));
      svg.appendChild(sv("line", { x1: L, x2: W - R, y1: sy(v), y2: sy(v),
                                   class: "grid" }));
      svg.appendChild(sv("text", { x: sx(v), y: H - B + 16, class: "tick" },
                         `${v}%`));
      if (v) svg.appendChild(sv("text", { x: L - 8, y: sy(v) + 4,
                                          class: "tick end" }, `${v}%`));
    }
    // the reference line: where the field matches the game
    svg.appendChild(sv("line", { x1: sx(0), y1: sy(0), x2: sx(dom), y2: sy(dom),
                                 class: "diag" }));
    svg.appendChild(sv("text",
      { x: sx(dom) - 6, y: sy(dom) + 16, class: "diaglabel end" },
      "field = game"));
    svg.appendChild(sv("text", { x: L + 12, y: T + 18, class: "zonelabel" },
      "TEMPLATE — the field is heavier here"));
    svg.appendChild(sv("text", { x: W - R - 12, y: H - B - 12,
                                 class: "zonelabel end" },
      "FADE — the field is lighter here"));
    svg.appendChild(sv("text", { x: (L + W - R) / 2, y: H - 8, class: "axis" },
      `the game — ${b.short || b.label} ${MEASURE[measure].short} %`));
    svg.appendChild(sv("text", { x: 0, y: 0, class: "axis",
                                 transform: `translate(15 ${(T + H - B) / 2}) rotate(-90)` },
      `the field — ${f.short || f.label} ${MEASURE[measure].short} %`));

    const scale = rampScale();
    const marks = sv("g", {});
    for (const p of shown) {
      const g = p.y - p.x;
      const mine = p.r.in_squad === true;
      const c = sv("circle", {
        cx: sx(p.x), cy: sy(p.y), r: mine ? 5.5 : 4.5,
        class: "mark" + (mine ? " mine" : " out"),
      });
      // Redundant with position (distance from the diagonal) and with the
      // printed number in the tooltip — colour is never the only channel.
      if (mine) c.setAttribute("fill", rampColor(g, scale));
      else { c.setAttribute("fill", "none"); c.setAttribute("stroke", rampColor(g, scale)); }
      c.addEventListener("mouseenter", () => showTip(p, g));
      c.addEventListener("mouseleave", hideTip);
      c.addEventListener("click", () => showDetail(p.r));
      marks.appendChild(c);
    }
    svg.appendChild(marks);

    // selective direct labels: the extremes, and your own squad among them
    const placed = [];
    const fits = (x, y, w) => {
      const box = { x, y: y - 7, w, h: 14 };
      for (const q of placed)
        if (!(box.x + box.w < q.x || q.x + q.w < box.x ||
              box.y + box.h < q.y || q.y + q.h < box.y)) return false;
      placed.push(box); return true;
    };
    const cands = [...shown].sort((a, b2) =>
      Math.abs(b2.y - b2.x) - Math.abs(a.y - a.x)).slice(0, 16);
    let labelled = 0;
    for (const p of cands) {
      if (labelled >= 9) break;
      const left = sx(p.x) > W - 150;
      const w = p.r.name.length * 5.8 + 10;
      const x = left ? sx(p.x) - 9 - w : sx(p.x) + 9;
      if (x < L || x + w > W - 2) continue;
      if (!fits(x, sy(p.y), w)) continue;
      svg.appendChild(sv("text", {
        x: left ? sx(p.x) - 9 : sx(p.x) + 9, y: sy(p.y) + 4,
        class: "plabel" + (left ? " end" : "") +
               (p.r.in_squad === true ? " mine" : ""),
      }, p.r.name));
      labelled++;
    }

    wrap.appendChild(svg);
    const tip = el("div", "chartip");
    wrap.appendChild(tip);
    mapCard.appendChild(wrap);

    function showTip(p, g) {
      tip.textContent = "";
      tip.appendChild(el("b", null, p.r.name));
      tip.appendChild(el("div", "sub",
        [p.r.pos, p.r.team, fmtPrice(p.r.price)].filter(Boolean).join(" · ")));
      const line = (k, v) => {
        const d = el("div", "tl");
        d.append(el("span", "tk", k), el("span", "tv", v));
        tip.appendChild(d);
      };
      const ms = MEASURE[measure].short;
      line(`${b.short || b.label} ${ms}`, pct(p.x));
      line(`${f.short || f.label} ${ms}`, pct(p.y));
      line("gap", `${signed(g)}pp`);
      const m = myMult(p.r);
      line("you", p.r.in_squad == null ? "unknown"
        : p.r.in_squad === false ? "not owned"
        : (p.r.your_role || "owned"));
      if (m.v != null)
        line("exposure", `${signed(m.v - p.y / 100)} per point`);
      const box = svg.getBoundingClientRect();
      const wb = wrap.getBoundingClientRect();
      const px = box.left - wb.left + (sx(p.x) / W) * box.width;
      const py = box.top - wb.top + (sy(p.y) / H) * box.height;
      // Guard the clamp: before first layout wb.width is 0, and a naive
      // min(x, width-190) then parks the tooltip off-screen at -190px.
      const right = Math.max(4, wb.width - 190);
      tip.style.left = `${Math.min(Math.max(px + 14, 4), right)}px`;
      tip.style.top = `${Math.max(py - 20, 4)}px`;
      tip.classList.add("on");
    }
    function hideTip() { tip.classList.remove("on"); }

    // scale legend — a diverging encoding always ships one
    const leg = el("div", "maplegend");
    const ramp = el("div", "ramp");
    for (let i = -6; i <= 6; i++) {
      const s = el("span");
      s.style.background = rampColor(i / 6 * scale, scale);
      ramp.appendChild(s);
    }
    leg.append(
      el("span", "tlabel", "gap"),
      el("span", "sub", `≤ −${scale.toFixed(0)}pp`), ramp,
      el("span", "sub", `≥ +${scale.toFixed(0)}pp`),
      el("span", "legkey mine-key", ""), el("span", "sub", "you own him"),
      el("span", "legkey out-key", ""), el("span", "sub", "you do not"));
    leg.title = "colour saturates at the 90th percentile of |gap| so ordinary " +
                "gaps are still distinguishable next to one extreme one";
    mapCard.appendChild(leg);
    mapCard.appendChild(el("p", "sub",
      `${shown.length} players plotted` +
      (hidden ? `; ${hidden} under ${FLOOR}% on both axes are not drawn — they ` +
                `would sit on top of each other at the origin` : "") +
      `. Both axes are square-root scaled by the same transform, which is why ` +
      `the ticks are unevenly spaced: it spreads the crowded low end without ` +
      `moving the diagonal, so “above the line” still means exactly “the field ` +
      `is heavier than the game”. Hover any mark for its numbers, click for ` +
      `the full ladder.`));
  }

  // ---- your exposure ledger ------------------------------------------
  function renderLedger() {
    ledgerCard.textContent = "";
    ledgerCard.appendChild(el("h2", null, "Your exposure"));
    const f = byKey[fieldKey];
    if (!res.squad?.readable) {
      ledgerCard.appendChild(emptyBox(
        res.squad?.note || "your squad could not be read",
        "Run `fpl myteam auth` once, or text /setsquad with your 15. Until " +
        "then this page can describe the field but not your position in it."));
      return;
    }
    if (!hasEo()) {
      ledgerCard.appendChild(emptyBox(
        `${f.label} publishes no effective ownership`,
        "Exposure is your multiplier minus the field's EO. This field only " +
        "reports head-count ownership, and a multiplier minus a head count " +
        "is not a number — pick a field that publishes EO."));
      return;
    }
    ledgerCard.appendChild(el("p", "sub",
      `Each bar is one term of the rank identity: your multiplier minus ` +
      `${f.label}'s effective ownership — always EO, whichever measure the ` +
      `map above is showing. Negative means you concede that much for every ` +
      `point he scores; positive means you gain it. Squad read via ${res.squad.source}` +
      (res.squad.gw != null ? ` at GW${res.squad.gw}` : "") +
      (res.squad.has_multipliers ? "."
        : ", which supplies roles but not multipliers — captain is taken as " +
          "2× and a triple-captain chip would make it 3×. Every multiplier " +
          "inferred that way is marked ×*.")));

    const scored = sourceRows()
      .map(r => ({ r, v: eoVal(r), m: myMult(r), e: exposureOf(r) }))
      .filter(p => p.e != null);
    if (!scored.length) {
      ledgerCard.appendChild(emptyBox(
        `no player has a ${f.short || f.label} EO and a readable squad role`));
      return;
    }
    const max = Math.max(...scored.map(p => Math.abs(p.e)), 0.1);
    const holes = scored.filter(p => p.e < 0).sort((a, b) => a.e - b.e).slice(0, 8);
    const bets = scored.filter(p => p.e > 0).sort((a, b) => b.e - a.e).slice(0, 8);

    const cols = el("div", "ledger");
    /* Titled by the SIGN of the term, not by ownership: a captain the field
       is also loaded on still lands on the "ahead" side, and calling that
       column "your differentials" would be a lie about what the bar shows. */
    cols.append(
      column("Behind the field — it is heavier on him than you", holes, "heavy",
             "Sorted by how much you concede for every point he scores."),
      column("Ahead of the field — you are heavier on him than it", bets, "light",
             "Sorted by how much you gain for every point he scores."));
    ledgerCard.appendChild(cols);

    function column(title, list, cls, why) {
      const c = el("div", "lcol");
      c.appendChild(el("h3", null, title));
      c.appendChild(el("p", "sub", why));
      if (!list.length) {
        c.appendChild(el("p", "sub", "none — nothing on this side of zero."));
        return c;
      }
      for (const p of list) {
        const row = el("div", "lrow " + cls);
        row.appendChild(faceImg(p.r.code, "avatar"));
        const id = el("div", "lid");
        id.appendChild(el("div", "lname", p.r.name));
        id.appendChild(el("div", "sub",
          `${p.r.pos ?? "?"} · ${p.r.team ?? "?"} · ${fmtPrice(p.r.price)} · ` +
          `${f.short || f.label} EO ${pct(p.v)}` +
          ` · you ${p.m.v}×${p.m.assumed ? "*" : ""}`));
        if (p.m.assumed) id.title = "multiplier inferred from your squad role";
        row.appendChild(id);
        const barwrap = el("div", "lbar");
        const bar = el("span");
        bar.style.width = `${Math.max(3, Math.round(100 * Math.abs(p.e) / max))}%`;
        barwrap.appendChild(bar);
        row.appendChild(barwrap);
        row.appendChild(el("div", "lval", signed(p.e)));
        row.onclick = () => showDetail(p.r);
        c.appendChild(row);
      }
      return c;
    }
  }

  // ---- the table ------------------------------------------------------
  const filterRow = el("div", "toolbar");
  const bandRow = el("div", "toolbar");
  const tbody = el("div");
  function renderTableShell() {
    tableCard.textContent = "";
    tableCard.appendChild(el("h2", null, "Every player"));
    tableCard.appendChild(el("p", "sub",
      "Sort by any column, filter down to the question you actually have, " +
      "click a name for where every field has him."));
    tableCard.append(filterRow, bandRow, tbody);
  }

  function renderFilters() {
    filterRow.textContent = "";
    filterRow.appendChild(el("span", "tlabel", "Show"));
    const rs = el("span", "seg");
    for (const [k, label, title] of [
      ["template", "Template", "ranked by the live EO metric — the field's core"],
      ["diff", "Differentials",
       `low-owned players with the best consensus xPts` +
       (res.xpts_gw != null ? ` at GW${res.xpts_gw}` : "")],
    ]) {
      const b = el("button", rowset === k ? "on" : "", label);
      b.title = title;
      b.onclick = () => { rowset = k; renderAll(); };
      rs.appendChild(b);
    }
    filterRow.appendChild(rs);

    /* The `on` class is updated here rather than by a re-render: rebuilding
       the toolbar would blow away the search box's focus and caret mid-type.
       (The first cut called renderBody() alone, which filtered correctly and
       left the segment showing the wrong selection — a filter you cannot see
       the state of is worse than no filter.) */
    const ps = el("span", "seg");
    for (const v of ["", "GKP", "DEF", "MID", "FWD"]) {
      const b = el("button", v === pos ? "on" : "", v || "All");
      b.onclick = () => {
        pos = v;
        [...ps.children].forEach(c => c.classList.toggle("on", c === b));
        renderBody();
      };
      ps.appendChild(b);
    }
    filterRow.appendChild(ps);

    const teams = [...new Set(sourceRows().map(r => r.team).filter(Boolean))].sort();
    const sel = el("select");
    sel.appendChild(Object.assign(el("option", null, "all teams"), { value: "" }));
    for (const t of teams)
      sel.appendChild(Object.assign(el("option", null, t), { value: t }));
    sel.value = team;
    sel.onchange = () => { team = sel.value; renderBody(); };

    const s = el("input");
    s.type = "search"; s.placeholder = "player…"; s.size = 12; s.value = search;
    s.oninput = () => { search = s.value; renderBody(); };

    const mine = el("label", "chk");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = mineOnly;
    cb.disabled = !res.squad?.readable;
    cb.onchange = () => { mineOnly = cb.checked; renderBody(); };
    mine.append(cb, " my squad only");

    filterRow.append(sel, s, mine);
  }

  function renderBands() {
    bandRow.textContent = "";
    bandRow.appendChild(el("span", "tlabel", "Gap"));
    const f = byKey[fieldKey], b = baseOf(measure);
    const opts = [
      ["all", "All", "every row in the current set"],
      ["heavy", `Template · +${BAND}pp or more`,
       `${f?.short || "field"} is at least ${BAND} points above ${b?.short || "the game"}`],
      ["mid", `Neutral · within ${BAND}pp`, "the field and the game agree"],
      ["light", `Fade · −${BAND}pp or less`,
       `${f?.short || "field"} is at least ${BAND} points below ${b?.short || "the game"}`],
    ];
    for (const [k, label, title] of opts) {
      const chip = el("button", "chip gw" + (band === k ? " on" : ""), label);
      chip.title = title;
      chip.onclick = () => { band = k; renderBands(); renderBody(); };
      bandRow.appendChild(chip);
    }
  }

  const sameSort = spec => JSON.stringify(spec) === JSON.stringify(sortBy);
  function th(label, spec, opts = {}) {
    const h = el("th", (opts.num === false ? "" : "num") +
                       (sameSort(spec) ? " sorted" : ""), label);
    if (sameSort(spec)) h.dataset.dir = sortDir === -1 ? "▼" : "▲";
    if (opts.title) h.title = opts.title;
    h.onclick = () => {
      sortDir = sameSort(spec) ? -sortDir : -1;
      sortBy = spec; renderBody();
    };
    return h;
  }

  function renderBody() {
    tbody.textContent = "";
    const f = byKey[fieldKey], b = baseOf(measure);
    let rows = sourceRows();
    const term = search.trim().toLowerCase();
    if (pos) rows = rows.filter(r => r.pos === pos);
    if (team) rows = rows.filter(r => r.team === team);
    if (term) rows = rows.filter(r => r.name.toLowerCase().includes(term));
    if (mineOnly) rows = rows.filter(r => r.in_squad === true);
    if (band !== "all") rows = rows.filter(r => {
      const g = gapOf(r, fieldKey, measure);
      if (g == null) return false;
      return band === "heavy" ? g >= BAND
           : band === "light" ? g <= -BAND : Math.abs(g) < BAND;
    });

    const scale = rampScale();
    const keyOf = r => {
      switch (sortBy.kind) {
        case "gap": return gapOf(r, fieldKey, measure);
        case "field": return val(r, fieldKey, measure);
        case "base": return b ? val(r, b.key, measure) : null;
        case "cap": return (r.fields?.[fieldKey] || {}).cap;
        case "exposure": return exposureOf(r);
        default: return r[sortBy.key];
      }
    };
    rows = [...rows].sort((x, y) => {
      const a = keyOf(x), c = keyOf(y);
      if (a == null && c == null) return 0;
      if (a == null) return 1;
      if (c == null) return -1;
      if (typeof a === "string" || typeof c === "string")
        return String(a).localeCompare(String(c)) * sortDir;
      return (c - a) * -sortDir;
    });

    if (!rows.length) {
      tbody.appendChild(emptyBox("no players match these filters"));
      return;
    }

    const wrap = el("div", "scroll-x");
    const table = el("table", "data sticky-first eotable");
    const thead = el("thead"), hr = el("tr");
    hr.append(
      th("player", { kind: "col", key: "name" }, { num: false }),
      th("pos", { kind: "col", key: "pos" }, { num: false }),
      th("team", { kind: "col", key: "team" }, { num: false }),
      th("£", { kind: "col", key: "price" }),
      th(b ? (b.short || b.label) : "game", { kind: "base" },
         { title: b ? `% of: ${b.denominator}` : "" }),
      th(f ? (f.short || f.label) : "field", { kind: "field" },
         { title: f ? `% of: ${f.denominator}` : "" }),
      th("gap", { kind: "gap" },
         { title: "field minus game, in percentage points — the whole point " +
                  "of the page" }),
      th("cap %", { kind: "cap" },
         { title: "share of that cohort captaining him; blank where the " +
                  "field publishes no captaincy" }),
      th("held by", { kind: "field" },
         { title: "the counts behind the percentage", num: false }),
      th(res.xpts_gw != null ? `xPts gw${res.xpts_gw}` : "xPts",
         { kind: "col", key: "xpts" }),
      th("you", { kind: "exposure" },
         { title: "your multiplier − field EO, per point he scores" }),
    );
    thead.appendChild(hr); table.appendChild(thead);
    const tb = el("tbody");
    for (const r of rows.slice(0, 150)) {
      const g = gapOf(r, fieldKey, measure);
      const tr = el("tr");
      const nameTd = el("td", "clickable");
      nameTd.appendChild(faceImg(r.code, "avatar" +
        (r.in_squad === true ? " mine" : "")));
      nameTd.appendChild(document.createTextNode(r.name));
      if (r.status && r.status !== "a")
        nameTd.appendChild(el("span", "chip warn", ` ${r.status}`));
      nameTd.title = "click for every field's read on him";
      nameTd.onclick = () => showDetail(r);
      tr.append(nameTd, el("td", null, r.pos ?? "–"),
                el("td", null, r.team ?? "–"),
                el("td", "num", fmtPrice(r.price)),
                el("td", "num", pct(b ? val(r, b.key, measure) : null)),
                el("td", "num", pct(val(r, fieldKey, measure))));

      // diverging bar, centred on zero, with the number always printed
      const gapTd = el("td", "num");
      const gb = el("span", "gapbar");
      const fill = el("span", "gf");
      if (g != null) {
        const w = Math.min(50, Math.round(50 * Math.abs(g) / (scale || 1)));
        fill.style.width = `${w}%`;
        fill.style.background = rampColor(g, scale);
        fill.style.left = g >= 0 ? "50%" : `${50 - w}%`;
      }
      gb.appendChild(fill);
      gapTd.append(gb, el("span", "gv", g == null ? "–" : `${signed(g)}`));
      tr.appendChild(gapTd);

      const cap = (r.fields?.[fieldKey] || {}).cap;
      tr.appendChild(el("td", "num", cap == null ? "–" : pct(cap)));

      const m = r.fields?.[fieldKey] || {};
      const heldTd = el("td", "held");
      if (m.owned_by != null && f?.n != null) {
        heldTd.appendChild(el("span", "frac", `${m.owned_by}/${f.n}`));
        if (m.captained_by) heldTd.appendChild(
          el("span", "chip s1", `${m.captained_by} C`));
        if (m.benched_by) heldTd.appendChild(
          el("span", "chip warn", `${m.benched_by} benched`));
      } else heldTd.textContent = f?.kind === "cohort" ? "–" : "no counts";
      tr.appendChild(heldTd);

      tr.appendChild(el("td", "num", fmt1(r.xpts)));

      const youTd = el("td");
      youTd.appendChild(roleChip(r));
      const e = exposureOf(r);
      if (e != null) {
        const x = el("span", "expo", signed(e));
        x.title = "your multiplier − this field's EO, per point he scores";
        youTd.appendChild(x);
      }
      tr.appendChild(youTd);
      tb.appendChild(tr);
    }
    table.appendChild(tb); wrap.appendChild(table);
    tbody.appendChild(wrap);
    tbody.appendChild(el("p", "sub",
      `${rows.length} rows${rows.length > 150 ? ", showing the first 150" : ""} · ` +
      `ring on a photo = in your squad · bar tint = the gap, and the number is ` +
      `always printed beside it · “held by” is the count behind the ` +
      `percentage, not a second estimate of it.`));
  }

  // ---- drawer: every field's read on one player -----------------------
  function showDetail(r) {
    drawer.textContent = "";
    drawer.classList.add("open");
    const hd = el("div", "dhead");
    hd.appendChild(faceImg(r.code, "bigface"));
    const id = el("div");
    id.appendChild(el("div", "dname", r.name));
    id.appendChild(el("div", "sub", [r.pos, r.team, fmtPrice(r.price),
      r.xpts != null ? `${fmt1(r.xpts)} xPts` : null].filter(Boolean).join(" · ")));
    hd.appendChild(id);
    const x = el("button", null, "✕");
    x.onclick = closeDrawer;
    hd.appendChild(x);
    drawer.appendChild(hd);

    if (r.status && r.status !== "a")
      drawer.appendChild(el("p", "sub", `availability flag: ${r.status}`));

    drawer.appendChild(el("h2", null, "Where every field has him"));
    drawer.appendChild(el("p", "sub",
      "One row per measurable field, each with the denominator its percentage " +
      "is a percentage of. Blank means that field does not publish that " +
      "measure — never zero."));
    const wrap = el("div", "scroll-x");
    const t = el("table", "data");
    const th_ = el("thead"), hr = el("tr");
    for (const [l, num] of [["field", 0], ["own %", 1], ["EO %", 1],
                            ["cap %", 1], ["held by", 0], ["gw", 1]])
      hr.appendChild(el("th", num ? "num" : "", l));
    th_.appendChild(hr); t.appendChild(th_);
    const tb = el("tbody");
    for (const f of allFields) {
      const m = r.fields?.[f.key];
      const tr = el("tr");
      if (f.key === fieldKey) tr.className = "sel";
      // The FULL label here, never `short`: the whole game appears twice on
      // this ladder (FPL's own ownership and LiveFPL's modelled EO of the same
      // population) and both abbreviate to the same word.
      const fd = el("td");
      fd.appendChild(el("b", null, f.label));
      const a = ageInfo(f.as_of);
      fd.appendChild(el("span", "freshdot " + a.cls));
      fd.title = `${f.label}\n% of: ${f.denominator}` +
        (f.note ? `\n${f.note}` : "") + `\n${a.text}`;
      tr.appendChild(fd);
      tr.append(el("td", "num", pct(m?.own)), el("td", "num", pct(m?.eo)),
                el("td", "num", pct(m?.cap)));
      tr.appendChild(el("td", null,
        m?.owned_by != null && f.n != null ? `${m.owned_by} of ${f.n}` : "–"));
      tr.appendChild(el("td", "num",
        f.gw == null ? "–" : `${f.gw}${f.same_values_as_gw != null ? "*" : ""}`));
      tb.appendChild(tr);
    }
    t.appendChild(tb); wrap.appendChild(t); drawer.appendChild(wrap);
    if (allFields.some(f => f.same_values_as_gw != null))
      drawer.appendChild(el("p", "sub",
        "* that field's values are byte-identical to an earlier gameweek — " +
        "the provider re-stamped a settled week, it is not a new forecast."));

    // the identity, spelled out for this one player
    const f = byKey[fieldKey], v = eoVal(r), m = myMult(r);
    drawer.appendChild(el("h2", null, "Your position on him"));
    if (v == null) {
      drawer.appendChild(el("p", "sub",
        `${f.label} publishes no effective ownership for him, so no exposure ` +
        `can be stated. (Exposure is always EO — a multiplier minus a ` +
        `head-count share would not mean anything.)`));
    } else if (m.v == null) {
      drawer.appendChild(el("p", "sub",
        r.in_squad === true
          ? "You own him, but the squad read supplied no role, so the " +
            "multiplier — and therefore the exposure — is unknown."
          : "Your squad is unreadable, so your side of the identity is unknown."));
    } else {
      const e = m.v - v / 100;
      const line = el("div", "identity small");
      line.append(
        el("span", "eq-term mine", `${m.v}×`), el("span", "eq-op", "−"),
        el("span", "eq-term theirs", `${(v / 100).toFixed(2)}`),
        el("span", "eq-op", "="),
        el("span", "eq-res " + (e >= 0 ? "pos" : "neg"),
           `${e > 0 ? "+" : e < 0 ? "−" : ""}${Math.abs(e).toFixed(2)}`));
      drawer.appendChild(line);
      drawer.appendChild(el("p", "sub",
        e >= 0
          ? `For every point he scores you gain ${e.toFixed(2)} on ${f.label}.`
          : `For every point he scores you lose ${Math.abs(e).toFixed(2)} to ` +
            `${f.label}. That is the cost of not matching them` +
            (r.in_squad === false ? "" : " at their multiplier") + `.` +
        (m.assumed ? " (Multiplier inferred from your squad role.)" : "")));
    }
    if (r.xpts != null)
      drawer.appendChild(el("p", "sub",
        `Consensus ${fmt1(r.xpts)} xPts` +
        (r.xpts_spread != null ? ` ± ${fmt2(r.xpts_spread)} across sources` : "") +
        (r.n_sources ? ` (${r.n_sources} sources)` : "") +
        (res.xpts_gw != null ? ` for GW${res.xpts_gw}` : "")));
    if (f?.composition?.length)
      drawer.appendChild(el("p", "sub",
        `${f.label} is ` +
        f.composition.map(c => `${c.n} ${c.label || c.tag}`).join(", ") + "."));
  }

  // ---- the honest footer ---------------------------------------------
  function renderFoot() {
    foot.textContent = "";
    const d = el("details", "card");
    const s = el("summary", null, "Provenance, metric coverage and last season");
    s.style.cursor = "pointer";
    d.appendChild(s);
    const body = el("div");
    d.appendChild(body);

    const chips = el("div", "toolbar");
    chips.appendChild(el("span", "tlabel", "Metric coverage"));
    for (const c of res.gws_covered || [])
      chips.appendChild(el("span", c.live ? "chip good" : "chip warn",
        `${c.metric} · ${c.season} GW${c.gw} · ${c.players ?? "?"} players · ` +
        (c.live ? "live" : "last season")));
    body.appendChild(chips);
    if (res.metrics_note) body.appendChild(el("p", "sub", res.metrics_note));
    if (res.cohort_note) body.appendChild(el("p", "sub", res.cohort_note));
    if (res.squad_note) body.appendChild(el("p", "sub", res.squad_note));

    const ls = res.last_season;
    if (ls?.rows?.length) {
      body.appendChild(el("h2", null,
        `Last season's final template — ${ls.season} GW${ls.gw}`));
      body.appendChild(el("p", "sub",
        "The old season's end state, NOT current EO. It is shown here, behind " +
        "a fold, precisely so it can never be read as this week's field."));
      const wrap = el("div", "scroll-x");
      const t = el("table", "data");
      const th_ = el("thead"), hr = el("tr");
      for (const [l, num] of [["player", 0], ["pos", 0], ["team", 0],
                              ["EO top10k", 1], ["EO elite", 1]])
        hr.appendChild(el("th", num ? "num" : "", l));
      th_.appendChild(hr); t.appendChild(th_);
      const tb = el("tbody");
      const LS_MAX = 40;
      for (const r of ls.rows.slice(0, LS_MAX)) {
        const tr = el("tr");
        tr.append(el("td", null, r.name), el("td", null, r.pos ?? "–"),
                  el("td", null, r.team ?? "–"),
                  el("td", "num", pct(r.eo_top10k_pct)),
                  el("td", "num", pct(r.eo_elite_pct)));
        tb.appendChild(tr);
      }
      t.appendChild(tb); wrap.appendChild(t); body.appendChild(wrap);
      if (ls.rows.length > LS_MAX) body.appendChild(el("p", "sub",
        `Top ${LS_MAX} of ${ls.rows.length} — it is context, not a working set.`));
    }
    body.appendChild(provenance(prov));
    foot.appendChild(d);
  }

  // ---- go -------------------------------------------------------------
  function renderAll() {
    renderTeach(); renderMeasure(); renderFields(); renderComposition();
    renderTiles(); renderMap(); renderLedger();
    renderTableShell(); renderFilters(); renderBands(); renderBody();
  }
  renderAll();
  renderFoot();
}
