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
 *
 * FOUR THINGS THE PAGE GREW AFTER THE FIRST CUT
 *
 *   1. SEGMENTS. "The 311 managers in the elite crawl pool" is not one thing:
 *      it is a curated list, the owner's own mini-league, past winners and a
 *      handful of named managers, and the reader should be able to say which of
 *      those he is racing. The chips below the field row do that, and the
 *      default leaves out the mini-league — 49 managers whose picks correlate
 *      with the owner's own, and the one set his own entry is in.
 *
 *      Recomputing EO over a subset is arithmetic only the panel can do (it
 *      holds the picks), so this control is CAPABILITY-GATED and PAYLOAD-LED.
 *      It reads ownership_eo's params schema from /api/panels and only offers
 *      the control when a segment parameter is actually there; where the panel
 *      publishes `segments[]` and `selection`, every label, count, trust
 *      judgement, default and denominator on the row comes from those and the
 *      chip states are read off `selection.segments` rather than remembered
 *      from the request — so the chips describe the numbers, never the wish.
 *      Where it publishes neither, the older `fields[].composition` is still
 *      disclosed and the row says plainly that all of it is in the numbers
 *      above. It never pretends to have re-cut anything.
 *
 *   2. THE SWARM. The map answers "how far is this player from the game"; it
 *      does not answer "what does the template LOOK like". A beeswarm of every
 *      measured player along the field's own axis, one row per position, does:
 *      the template turns out to be a handful of spikes over a dense floor, and
 *      which positions have a floor and which have spikes is the actual answer
 *      to "who is template". Context-aware, in the Opta sense — the tooltip
 *      reports a percentile WITHIN THE POSITION, because 20% EO means something
 *      different for a goalkeeper than for a midfielder.
 *
 *   3. DISAGREEMENT. Two informed fields agreeing tells you nothing you did not
 *      already know. Where they disagree is where the edge is. The compare card
 *      is a dumbbell on ONE shared axis, sorted by the size of the split, so the
 *      long connectors are the story and the short ones are visibly noise.
 *
 *   4. MOMENTUM, HONESTLY. Per-gameweek EO movement needs two observations. As
 *      of this build there is exactly one — every crawled cohort is GW1, and
 *      LiveFPL's GW2 rows are byte-identical re-stamps of GW1, which the panel
 *      measures and reports as `same_values_as_gw`. So the card draws the
 *      OBSERVATION LEDGER rather than a line: which field has been measured at
 *      which gameweek, which of those are re-stamps, and when the next real
 *      point lands. A flat line across one point would be a claim of stability
 *      that nothing in the warehouse supports. The panel's own `momentum` view
 *      (`available`, `reason`, `gws`, `series`) drives it where present — its
 *      reason is printed verbatim, and the slope chart below is live code that
 *      takes over the moment two distinct gameweeks are on the wire.
 */

import { runPanel, getJSON, el, emptyBox, errBox, provenance, faceImg,
         fmtPrice, fmt1, fmt2 } from "/js/app.js";
import { renderTools } from "/js/views/template-tools.js";
// the cross-tab player strip: what the panel owns, said and noticed about him
import { chatterStrip } from "/js/components/chatter.js";

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

/* Average-rank Spearman: the compare card needs "do these two fields order the
   player pool the same way", and a Pearson r on percentages would be dragged
   around by the two or three captain-heavy premiums at the top of the scale.
   Ties get the mean rank so a field that reports a lot of 0.0% does not get a
   spurious ordering out of its own floor. */
function rankOf(vals) {
  const idx = vals.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]);
  const r = new Array(vals.length);
  let i = 0;
  while (i < idx.length) {
    let j = i;
    while (j + 1 < idx.length && idx[j + 1][0] === idx[i][0]) j++;
    const avg = (i + j) / 2 + 1;
    for (let k = i; k <= j; k++) r[idx[k][1]] = avg;
    i = j + 1;
  }
  return r;
}
function spearman(a, b) {
  const n = a.length;
  if (n < 4) return null;
  const ra = rankOf(a), rb = rankOf(b), m = (n + 1) / 2;
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) {
    const x = ra[i] - m, y = rb[i] - m;
    num += x * y; da += x * x; db += y * y;
  }
  return da && db ? num / Math.sqrt(da * db) : null;
}
function median(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b), h = s.length >> 1;
  return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2;
}

/* Beeswarm placement. Points arrive sorted by x; each one takes the lane
   closest to the row's centre line that clears every mark already placed
   within 2r horizontally. `placed` is in x order, so the scan can stop as soon
   as it walks past 2r — that is what keeps ~200 marks a frame-cheap layout
   rather than an O(n²) one. Past the lane cap a mark is allowed to sit on a
   neighbour: the 2px surface ring keeps the pair separable and the caption
   says the row is saturated rather than pretending it is not. */
function beeswarm(xs, r, lanes) {
  const step = r * 2.05, out = new Array(xs.length), placed = [];
  for (let i = 0; i < xs.length; i++) {
    const x = xs[i];
    let y = 0;
    for (let k = 0; k <= lanes * 2; k++) {
      const cand = k === 0 ? 0 : (k % 2 ? 1 : -1) * Math.ceil(k / 2) * step;
      let ok = true;
      for (let j = placed.length - 1; j >= 0; j--) {
        const q = placed[j];
        if (x - q.x >= 2 * r) break;
        if (Math.hypot(x - q.x, cand - q.y) < 2 * r) { ok = false; break; }
      }
      y = cand;
      if (ok) break;
    }
    out[i] = y;
    placed.push({ x, y });
  }
  return out;
}

/* A hover layer every chart on this page shares, so a tooltip means and looks
   like one thing here. Returns show/hide; the caller fills the body. */
function makeTip(wrap) {
  const tip = el("div", "chartip");
  wrap.appendChild(tip);
  return {
    node: tip,
    show(svg, W, H, cx, cy, build) {
      tip.textContent = "";
      build(tip, (k, v) => {
        const d = el("div", "tl");
        d.append(el("span", "tk", k), el("span", "tv", v));
        tip.appendChild(d);
      });
      const box = svg.getBoundingClientRect(), wb = wrap.getBoundingClientRect();
      const px = box.left - wb.left + (cx / W) * box.width;
      const py = box.top - wb.top + (cy / H) * box.height;
      // Before first layout wb.width is 0; a naive min(x, width−190) would park
      // the tooltip off-screen at −190px, so the right edge is floored at 4.
      const right = Math.max(4, wb.width - 200);
      tip.style.left = `${Math.min(Math.max(px + 14, 4), right)}px`;
      tip.style.top = `${Math.max(py - 20, 4)}px`;
      tip.classList.add("on");
    },
    hide() { tip.classList.remove("on"); },
  };
}

/* ---- crawl-source tags -------------------------------------------------
   FALLBACK ONLY. The panel is the authority on what a set is: it publishes
   `segments[]` with `label`, `n`, `trusted`, `untrusted_reason`, `caveat` and
   `in_default`, and `selection` with the resolved union and its denominator.
   Where those are on the wire this table is never consulted.

   It exists because the two halves of this feature ship independently, and a
   build whose panel predates them must still disclose what the older payload
   does carry — `fields[].composition`, which is tags and counts and no meaning
   at all. `warn` is a disclosure the reader has to see wherever the set is in
   play; `off` keeps the set out of the derived default. A tag the crawl invents
   that is not listed here is offered unflagged and included, because inventing
   a warning for a set nobody described would be worse than staying quiet. */
const TAG_INFO = {
  elite_list: {
    why: "Managers on the curated elite list — the intended population of " +
         "this pool and the reason it is worth measuring at all.",
  },
  winner: {
    why: "Past overall winners. Twelve people, so every share they move is " +
         "coarse, but they are unambiguously an informed field.",
  },
  elite_named: {
    why: "Individually named managers added by hand. Eight of these also " +
         "carry another tag, which is why the tags sum above the pool size.",
  },
  mini_league: {
    off: true, warn: true,
    why: "Your own mini-league opponents. Their picks correlate with each " +
         "other and with yours, so including them makes the field look more " +
         "like your squad than it really is. Off by default for that reason — " +
         "turn it on when the question is “am I winning my mini-league”, not " +
         "“what is the elite template”.",
  },
  snowball: {
    off: true, warn: true, danger: true,
    why: "UNTRUSTWORTHY. These entries were reached by walking the leagues of " +
         "seed ids that have since gone stale — the ids no longer identify the " +
         "managers they were recorded as. Whoever is in this set, it is not " +
         "reliably who the crawl says it is. Never in the default field.",
  },
  "(no manager row)": {
    off: true, warn: true,
    why: "Squads stored with no manager row to classify them — a crawl bug, " +
         "counted rather than dropped.",
  },
};
const tagInfo = t => TAG_INFO[t] || {};
/* Payload first, always: the panel's `untrusted_reason` and `caveat` are its
   own account of the set and outrank anything in the table above.

   The panel draws a distinction this page has to keep. `trusted: false` is a
   provenance verdict — the selection rule that put those entries in the pool
   means nothing, so neither does any share over them. `caveat` is the weaker
   signal: a set that is measurable and honest but whose reading needs a
   sentence beside it. Painting both in the warning colour would put an alarm
   next to two sets the panel itself defaults to, and an alarm that fires on
   everything stops being read — which is exactly what would then happen to the
   one set that must not be used. So: DANGER gets the status colour and a box,
   CAVEAT gets a quiet mark and a sentence. */
const tagWhy = c =>
  c.untrusted_reason || c.caveat || c.reason || c.warning || c.note ||
  tagInfo(c.tag).why || null;
const tagDanger = c =>
  c.trusted === false || !!c.untrusted ||
  (c.trusted === undefined && !!tagInfo(c.tag).danger);
const tagCaveat = c =>
  !tagDanger(c) && (!!c.caveat || !!c.warning ||
    (c.caveat === undefined && !!tagInfo(c.tag).warn));
const tagWarns = c => tagDanger(c) || tagCaveat(c);

/* FPL availability status codes. The payload carries a one-letter status and
   no chance-of-playing percentage, so the chip renders a status dot plus the
   WORD — never a bare letter glued to a surname ("Rodon d" read as "Rodond",
   the R3 blocker). If a chance % ever lands on the wire it belongs here. */
const STATUS_WORD = { d: "doubtful", i: "injured", s: "suspended",
                      u: "unavailable", n: "not in squad" };
function availChip(status) {
  if (!status || status === "a") return null;
  const word = STATUS_WORD[status] || `status “${status}”`;
  const chip = el("span", "avail" + (status === "d" ? " warn" : " bad"));
  chip.appendChild(el("span", "adot"));
  chip.appendChild(el("span", "aword", word));
  chip.setAttribute("role", "img");
  chip.setAttribute("aria-label", `availability: ${word}`);
  chip.title = `Availability: ${word} — FPL status flag “${status}”. The ` +
    `payload carries no chance-of-playing percentage, so none is invented.`;
  return chip;
}

/* Cohorts below this many managers are greyed in the selectors, kept out of
   every DEFAULT comparison, and watermark any chart drawn from them: with
   n=4 every share is a multiple of 25% and the bars are quantization noise
   wearing full visual weight (R1+R2+R3, tri-consensus). */
const MIN_N = 25;
const lowN = f => f != null && f.n != null && f.n < MIN_N;

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
  /* Order is an argument: the field, then its shape, then YOUR position in it,
     then the two comparisons that need all three, then the raw table, then the
     tools that act on what the reader just concluded. */
  const head = el("section", "card");
  const mapCard = el("section", "card");
  const swarmCard = el("section", "card");
  const ledgerCard = el("section", "card");
  const compareCard = el("section", "card");
  const momentumCard = el("section", "card");
  const tableCard = el("section", "card");
  const toolsHost = el("div", "tools-host");
  const foot = el("div");
  host.append(head, mapCard, swarmCard, ledgerCard, compareCard, momentumCard,
              tableCard, toolsHost, foot);

  const drawer = el("aside", "drawer");
  document.body.appendChild(drawer);
  let chatter = null;                    // the player strip's live handle
  const closeDrawer = () => {
    drawer.classList.remove("open");
    chatter?.cancel(); chatter = null;   // a closed drawer stops rendering
  };
  addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

  head.appendChild(el("h2", null, "The field you're racing"));
  const teach = el("div", "teach");
  head.appendChild(teach);
  const measureRow = el("div", "toolbar");
  const fieldRow = el("div", "toolbar");
  const segRow = el("div", "toolbar segrow");
  /* The FIELD radio (the measured cohort every chart reads) and WHO IS IN IT
     (the segment selection behind diff/what-if) are two populations answering
     to one word — the payload's `field_distinction` names both, and this box
     draws them as ONE control group so a level from one and a trend from the
     other can never be read as the same population (R2). */
  const fieldGroup = el("div", "fieldgroup");
  const fgCap = el("div", "fgcap");
  fieldGroup.append(fieldRow, segRow, fgCap);
  const compRow = el("div", "toolbar comp");
  const tiles = el("div", "stats");
  head.append(measureRow, fieldGroup, compRow, tiles);

  const PARAMS = { limit: 200 };

  /* Does THIS build's panel take a segment selection? Asked of the schema the
     server publishes, not assumed from a version number: the two halves of this
     feature ship independently and the page has to be correct on either side of
     that. The names are candidates because the parameter is the panel's to name;
     the first array-typed one wins, and null means "no such control exists". */
  const SEG_PARAM_CANDIDATES = ["segments", "segment", "include_segments",
                                "cohort_tags", "include_tags", "tags", "sources"];
  async function detectSegParam() {
    try {
      const raw = await getJSON("/api/panels");
      const list = Array.isArray(raw) ? raw : (raw.panels || []);
      const p = list.find(x => x.script === "ownership_eo");
      const props = p?.params_schema?.properties || {};
      for (const name of SEG_PARAM_CANDIDATES) {
        const s = props[name];
        if (!s) continue;
        const t = Array.isArray(s.type) ? s.type : [s.type];
        if (t.includes("array")) return name;
      }
    } catch { /* no schema endpoint: treat the control as unavailable */ }
    return null;
  }

  let res, prov, segParam = null;
  try {
    const [panel, sp] = await Promise.all([
      runPanel("ownership_eo", PARAMS), detectSegParam(),
    ]);
    ({ result: res, provenance: prov } = panel);
    segParam = sp;
  } catch (e) { head.appendChild(errBox(e)); return; }
  if (res?.empty) { head.appendChild(emptyBox(res.reason)); return; }

  let allFields = res.fields || [];
  let byKey = Object.fromEntries(allFields.map(f => [f.key, f]));

  /* Namesakes. Two players named "Palmer" (CHE MID and IPS GKP) render as one
     word wherever a name stands alone, and the reader assumes Cole. The
     payload's own `disambiguator` wins when present; otherwise a name shared
     by two rows gets its club appended — from the row itself, never guessed. */
  let dupNames = new Set();
  function relearnNames() {
    const seen = new Map(), codes = new Set();
    for (const r of (res.rows || []).concat(res.differentials || [])) {
      if (codes.has(r.code)) continue;      // rows ∪ differentials overlap —
      codes.add(r.code);                    // one player is never a namesake
      seen.set(r.name, (seen.get(r.name) || 0) + 1);
    }
    dupNames = new Set([...seen].filter(([, n]) => n > 1).map(([k]) => k));
  }
  relearnNames();
  const dispName = r => r.disambiguator ||
    (dupNames.has(r.name) && r.team ? `${r.name} (${r.team})` : r.name);

  function reindex() {
    allFields = res.fields || [];
    byKey = Object.fromEntries(allFields.map(f => [f.key, f]));
    relearnNames();
  }

  // ---- state ----------------------------------------------------------
  const has = (f, m) => (f.measures || []).includes(m);
  const pickable = m => allFields.filter(f => f.role === "field" && has(f, m));
  const baseOf = m => allFields.find(f => f.role === "baseline" && has(f, m));

  let measure = pickable("eo").length && baseOf("eo") ? "eo"
              : pickable("own").length && baseOf("own") ? "own" : "eo";
  /* The default field skips any cohort under MIN_N: a 4-manager sample must
     be asked for, never handed out. */
  let fieldKey = (pickable(measure).find(f => f.kind === "cohort" && !lowN(f))
                  || pickable(measure).find(f => f.kind === "cohort")
                  || pickable(measure)[0] || {}).key;
  let rowset = "template";           // template | diff
  let pos = "", team = "", search = "", mineOnly = false, band = "all";
  let sortBy = { kind: "gap" }, sortDir = -1;
  let showAllRows = false;           // the 150-row cut, with a control on it

  const BAND = 10;                   // percentage points — stated, not implied

  /* ---- segment state -------------------------------------------------
     ONE model, two possible sources, and the panel always wins.

     PANEL SOURCE (`res.segments` + `res.selection`). Every set is a first-class
     descriptor with a label, a squad-backed count, a trust judgement and a
     reason; the selection reports the union it resolved to, the DISTINCT
     manager count behind it, and the sentence that count is a share of. The
     page reads all of that and computes none of it. In particular the applied
     selection is READ FROM THE PAYLOAD rather than remembered here, so the
     chips can never claim a cut the numbers were not computed over.

     COMPOSITION SOURCE (`fields[].composition`). All an older panel publishes
     is tags and counts. The selector is then a disclosure only, and says so.

     `tagUniverse` is learned from the FIRST response and held: a segmented
     response reports the composition of what was asked for, and a selector
     whose own options disappear as you deselect them is unusable. */
  const tagUniverse = {};            // cohort key -> [{tag, n, label, ...}]
  const segSel = {};                 // cohort key -> Set<tag> the reader wants
  let segApplied = null;             // fallback bookkeeping when the panel
  let segAppliedKey = null;          // publishes no `selection`
  let segBusy = false, segError = null;

  function learnTags() {
    for (const f of allFields)
      if (f.kind === "cohort" && f.composition?.length && !tagUniverse[f.key])
        tagUniverse[f.key] = f.composition.map(c => ({ ...c }));
  }
  learnTags();

  const sameSet = (a, b) =>
    !!a && !!b && a.size === b.size && [...a].every(t => b.has(t));

  /* The set descriptors the panel published, normalised onto the shape the
     renderer wants. `tag` rather than `key` only because the composition
     fallback calls it that and one renderer serves both. */
  function panelSegments() {
    const list = res.segments;
    if (!Array.isArray(list) || !list.length) return null;
    return list.map(s => ({
      tag: s.key, label: s.label, n: s.n, n_pool: s.n_pool, gw: s.gw,
      trusted: s.trusted, untrusted_reason: s.untrusted_reason,
      caveat: s.caveat, in_default: s.in_default, selected: s.selected,
      cohorts: s.cohorts,
    }));
  }

  /* Which field the segment selection actually rebuilt. The panel marks it by
     listing the sets that compose it; older payloads have no such field and the
     selector falls back to whichever crawled cohort is on screen. */
  function segFieldKey() {
    const f = allFields.find(x => Array.isArray(x.segments));
    if (f) return f.key;
    return byKey[fieldKey]?.kind === "cohort" ? fieldKey : null;
  }

  /* The single model every part of the segment UI reads. */
  function segModel() {
    const panel = panelSegments();
    const sel = res.selection;
    const key = segFieldKey();
    if (panel) {
      const applied = new Set(
        sel?.segments || panel.filter(s => s.selected).map(s => s.tag));
      const def = new Set(
        sel?.default || panel.filter(s => s.in_default).map(s => s.tag));
      return {
        source: "panel", key, universe: panel, applied, def,
        n: sel?.n ?? null,
        denominator: sel?.denominator ?? byKey[key]?.denominator ?? null,
        isDefault: sel?.is_default ?? sameSet(applied, def),
        unknown: sel?.unknown || [],
        includesYou: sel?.includes_you ?? null,
        sumOfSets: sel?.n_sum_of_sets ?? null,
        overlap: sel?.overlap ?? null,
        overlaps: sel?.overlaps ?? null,
        unresolved: sel?.unresolved_pick_entries ?? null,
        note: sel?.note || null,
      };
    }
    if (!key || !tagUniverse[key]) return null;
    const universe = tagUniverse[key];
    const def = new Set(universe.filter(c => !tagInfo(c.tag).off).map(c => c.tag));
    const f = byKey[key];
    return {
      source: "composition", key, universe,
      applied: segApplied && segAppliedKey === key
        ? segApplied : new Set(universe.map(c => c.tag)),
      def: def.size ? def : new Set(universe.map(c => c.tag)),
      n: f?.n ?? null, denominator: f?.denominator ?? null,
      isDefault: null, unknown: [], includesYou: null,
      sumOfSets: null, overlap: null, overlaps: f?.overlaps ?? null,
      unresolved: null, note: null,
    };
  }

  /* What the reader currently wants, which is what was served until he clicks. */
  function selectionFor(key) {
    const m = segModel();
    if (!m || m.key !== key) return null;
    if (!segSel[key]) segSel[key] = new Set(m.applied);
    return segSel[key];
  }

  /* Ask the panel to rebuild the crawled cohorts over the selected sets. Only
     the panel can do this — it holds the picks — so when there is no parameter
     to send, nothing is sent and nothing is claimed. The previous render is
     held at reduced opacity rather than replaced by a skeleton.

     Two things this has to get right, both found by clicking fast:
       - The REQUEST is snapshotted. `selectionFor` hands back the live Set, so
         reading it again when the response lands attributes whatever the reader
         has clicked since to numbers that were computed for something else.
       - Only the LATEST request may land. A token drops stale responses, so
         four quick clicks leave the page showing the fourth selection rather
         than whichever fetch happened to finish last. */
  let segToken = 0;
  const EMPTY_SEL = "\0empty-selection";     // sentinel, rendered as its own state
  /* Tags are storage keys; the reader is owed the label the payload gave them. */
  function nameOf(model, tag) {
    return model?.universe.find(c => c.tag === tag)?.label || tag;
  }
  function nameList(model, tags) {
    return tags && tags.size ? [...tags].map(t => nameOf(model, t)).join(", ")
                             : "the whole pool";
  }

  async function applySegments(key) {
    const m = segModel();
    const sel = selectionFor(key);
    if (!segParam || !m || !sel) return;
    if (sameSet(sel, m.applied)) return;
    if (!sel.size) {
      segError = EMPTY_SEL; segBusy = false; renderSegments(); return;
    }
    const want = [...sel];
    const token = ++segToken;
    segBusy = true; segError = null; renderSegments();
    host.classList.add("refetching");
    let out = null, err = null;
    try {
      out = await runPanel("ownership_eo", { ...PARAMS, [segParam]: want });
    } catch (e) {
      // A 4xx here means the parameter exists but this selection was refused.
      err = String(e.message || e);
    }
    if (token !== segToken) return;         // a newer selection is in flight
    if (!err && out?.result?.empty)
      err = out.result.reason || "the panel returned nothing for that selection";
    if (err) {
      segError = err;
    } else {
      res = out.result; prov = out.provenance;
      reindex(); learnTags();
      /* Bookkeeping for the composition fallback only. When the panel serves a
         `selection`, `segModel()` reads the applied union straight off it —
         which is the whole point: the chips then describe the numbers rather
         than the request. Re-seed the pending set from what came back so a
         panel that widened or narrowed the request is visible immediately. */
      segApplied = new Set(want); segAppliedKey = key;
      const m2 = segModel();
      if (m2) segSel[m2.key] = new Set(m2.applied);
      if (!byKey[fieldKey]) fieldKey = (pickable(measure)[0] || {}).key;
    }
    segBusy = false;
    host.classList.remove("refetching");
    renderAll(); renderFoot();
  }

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

  // ---- shared craft helpers -------------------------------------------
  /* Caption tiering (R1+R3): every chart carries ONE always-on line, and the
     methodology moves behind the drawer's existing "how this is computed"
     disclosure pattern. Nothing is deleted — it is re-shelved. */
  function caption(host2, line, paras, label) {
    if (line) host2.appendChild(el("p", "sub capline", line));
    const texts = (paras || []).filter(Boolean);
    if (!texts.length) return;
    const d = el("details", "howto");
    d.appendChild(el("summary", null, label || "how this is computed"));
    for (const p of texts) d.appendChild(el("p", "sub", p));
    host2.appendChild(d);
  }

  /* What the baseline column actually IS under the current measure — under
     "Effective ownership" it is LiveFPL's predicted EO (with the feed's own
     capture instant from `eo_pred_captured`), under "Ownership" it is FPL's
     own%. Same header word, two different numbers, so the header says which
     (R2's "ALL FPL silently switches" finding). */
  function baselineDesc(b) {
    if (!b) return null;
    if (b.key === "eo_predicted") {
      const cap = res.eo_pred_captured || {};
      const d = cap.as_of ? new Date(String(cap.as_of).replace(" ", "T")) : null;
      const ds = d && !isNaN(d)
        ? d.toLocaleDateString(undefined, { day: "numeric", month: "short" })
        : null;
      const bits = [ds ? `captured ${ds}` : null,
                    cap.gw != null ? `GW${cap.gw}` : null].filter(Boolean);
      return `predicted EO${bits.length ? ` (${bits.join(", ")})` : ""}`;
    }
    if (b.key === "global") return "own%";
    return null;
  }
  const baselineLabel = b => {
    if (!b) return "game";
    const d = baselineDesc(b);
    return `${b.short || b.label}${d ? ` — ${d}` : ""}`;
  };

  /* The low-n watermark: the n is already printed, but printing n is not the
     same as protecting the reader — a chart drawn from 4 managers says so
     ACROSS the marks (R3). */
  function watermark(svg, W, H, f) {
    if (!lowN(f)) return;
    svg.appendChild(sv("text", {
      x: W / 2, y: H / 2, class: "lownwm", "text-anchor": "middle",
      "aria-hidden": "true",
    }, `n=${f.n} — quantized`));
  }

  /* An accessible mark: <title>, tabindex, aria-label, and keyboard open —
     the promised hover/click was mouse-only and invisible to assistive tech
     on 102 circles (R3). Focus shows the same tooltip hover does. */
  function accessMark(c, label, onOpen, onShow, onHide) {
    c.appendChild(sv("title", {}, label));
    c.setAttribute("tabindex", "0");
    c.setAttribute("role", "button");
    c.setAttribute("aria-label", label);
    if (onShow) c.addEventListener("focus", onShow);
    if (onHide) c.addEventListener("blur", onHide);
    c.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); }
    });
  }

  /* "cover this hole →": the plan that says what to sell to fund him lives on
     the Dashboard's verdict/solver card, and nothing connected them (R1).
     Simple tab + focus navigation — it IS a cross-tab action. */
  function focusDashboardPlan() {
    let tries = 0;
    const seek = () => {
      const t = document.querySelector(
        "section.card.verdict, [data-card='verdict'], section.card.solver");
      if (t) {
        t.setAttribute("tabindex", "-1");
        t.focus({ preventScroll: true });
        t.scrollIntoView({ block: "start" });
      } else if (++tries < 12) setTimeout(seek, 250);
    };
    setTimeout(seek, 250);
  }

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
    caption(teach,
      "Template holdings cancel out of that sum — the number that carries " +
      "information is the GAP between the field you are racing and the game.",
      ["A player the field is loaded on is insurance, not upside: owning him " +
       "moves you almost nothing, missing him is ruinous. That is why this " +
       "page is a comparison of two fields everywhere, never a single " +
       "ownership column — and why every chart below positions players by " +
       "the gap rather than by raw ownership."],
      "why the gap, not ownership");

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
      const small = lowN(f);
      const chip = el("button", "chip src" + (on ? " on" : "") +
                      (small ? " lown" : ""));
      chip.append(on ? "✓ " : "", el("span", "freshdot " + a.cls),
                  ` ${f.short || f.label}`);
      if (f.n != null) chip.appendChild(el("span", "cnt" + (small ? " bad" : ""),
        small ? `n=${f.n} — too small to quote` : `n=${f.n}`));
      if (f.mini_league_n)
        chip.appendChild(el("span", "cnt ml", `incl. ${f.mini_league_n} mini-league`));
      chip.title =
        `${f.label}\n% of: ${f.denominator}\n` +
        (f.gw != null ? `gameweek ${f.gw}` : "no gameweek stamp") +
        ` · ${f.players ?? "?"} players measured · ${a.text}` +
        (small
          ? `\nn=${f.n}: below the ${MIN_N}-manager floor — every share is a ` +
            `multiple of ${(100 / f.n).toFixed(0)}%, so it is excluded from ` +
            `every default view. Pick it and the charts watermark themselves.`
          : "") +
        (f.same_values_as_gw != null
          ? `\nValues are byte-identical to GW${f.same_values_as_gw}: the feed ` +
            `re-stamped a settled gameweek, it is not a fresh forecast.`
          : "") +
        (f.note ? `\n${f.note}` : "");
      chip.setAttribute("aria-label", `${f.label}` +
        (f.n != null ? `, ${f.n} managers` : "") +
        (small ? ", too small to quote" : "") +
        (f.mini_league_n ? `, includes ${f.mini_league_n} of your ` +
                           `mini-league rivals` : "") +
        (on ? ", selected" : ""));
      chip.onclick = () => {
        fieldKey = f.key;
        renderAll();
        /* When the selector is scoped to a pool rather than global — the older
           composition fallback — moving to another pool has to carry that
           pool's own selection with it, or the row sits there saying the
           numbers were served for some other pool's tags: true, and useless.
           A no-op when the panel takes no segment parameter, and a no-op when
           the selection is global (the panel's own `segments` contract). */
        const m = segModel();
        if (segParam && m && m.key === f.key) applySegments(f.key);
      };
      fieldRow.appendChild(chip);
    }
    const b = baseOf(measure);
    const bf = ageInfo(b?.as_of);
    const baseline = el("span", "baseline");
    /* The stamp travels WITH the label: under EO the baseline is LiveFPL's
       predicted EO with its own capture instant, under own% it is FPL's
       marginal ownership — the header must change when the measure does. */
    /* The label already names the measure ("Whole game — predicted EO"), so
       only the capture stamp is appended here — never the measure twice. */
    const bd = b ? baselineDesc(b) : null;
    const stamp = bd && bd.includes("(") ? bd.slice(bd.indexOf("(")) : null;
    baseline.append("compared against ", el("b", null, b ? b.label : "—"),
                    stamp ? ` ${stamp}` : "",
                    " ", el("span", "freshdot " + bf.cls));
    if (b) baseline.title = `% of: ${b.denominator}` +
      (b.gw != null ? ` · gameweek ${b.gw}` : "") + ` · ${bf.text}`;
    fieldRow.appendChild(baseline);
  }

  /* The caption that ties the group together: field_distinction, verbatim
     numbers, and the mini-league conflict named where the number is used. */
  function renderFieldGroupCap() {
    fgCap.textContent = "";
    const fd = res.field_distinction;
    const f = byKey[fieldKey];
    if (fd && fd.measured_cohort && fd.selection) {
      const mc = fd.measured_cohort, sel = fd.selection;
      const line = el("p", "sub fgline");
      line.append(el("b", null, "One box, two populations. "),
        `The FIELD radio is the measured cohort` +
        (mc.n != null ? ` (${mc.n} managers` +
          (mc.gw != null ? `, GW${mc.gw}` : "") + `)` : "") +
        ` behind every chart and elite column above; WHO IS IN IT is the ` +
        `segment selection` +
        (sel.n != null ? ` (${sel.n} managers)` : "") +
        ` behind the diff and the what-if simulator below. They are ` +
        `different sets — a level from one and a trend from the other never ` +
        `share a sentence.`);
      if (fd.note) line.title = fd.note;
      fgCap.appendChild(line);
    }
    const mlN = f?.mini_league_n ?? fd?.measured_cohort?.mini_league_n;
    if (mlN && f?.kind === "cohort")
      fgCap.appendChild(el("p", "sub fgml",
        `This measured cohort includes your ${mlN} mini-league rivals — a ` +
        `set the default selection excludes. Their picks correlate with ` +
        `yours, which pulls every gap here towards zero.`));
    fgCap.appendChild(el("p", "sub glyphkey",
      "Marks on the set chips: ✓ = in the field · * = read with a caveat " +
      "(sentence below) · ! = untrustworthy, never in a default."));
  }

  /* ---- the segment selector ------------------------------------------
     "The 311 managers in the elite crawl pool" is several different populations
     wearing one number. This row breaks them out and lets the reader choose
     which of them he is racing.

     Three rules it exists to keep:
       - THE DENOMINATOR COMES FROM THE PAYLOAD. Per-set counts are shown, and
         the sets OVERLAP — entries carry two tags — so adding them up gives a
         number larger than the field. The headline is `selection.n` (DISTINCT
         managers) with `selection.denominator` in the panel's own words, and
         when the panel reports the overlap explicitly that is what is printed.
       - THE STATE SHOWN IS THE STATE SERVED. A chip is "in" when the numbers on
         screen were computed with it in — read off `selection.segments`, not
         remembered from the request. While a recompute is in flight the row
         says so; if the panel takes no segment parameter, the chips are inert
         and the row says every set is inside every number above.
       - AN UNTRUSTWORTHY SET IS FLAGGED, NEVER QUIETLY DROPPED. A set with
         `trusted: false` is offered, marked, and carries the panel's own
         reason. A missing checkbox teaches nobody why not to click it. */
  function renderSegments() {
    segRow.textContent = "";
    const f = byKey[fieldKey];
    if (!f) return;
    const m = segModel();

    if (!m || (f.kind !== "cohort" && m.key !== fieldKey)) {
      segRow.appendChild(el("span", "tlabel", "Sets"));
      segRow.appendChild(el("span", "sub", f.kind === "cohort"
        ? "This pool reports no crawl-source breakdown, so there is nothing " +
          "to select between."
        : `${f.label} is ${f.provider || "the provider"}'s own sample, defined ` +
          `and drawn on their side. It cannot be re-cut here — pick a crawled ` +
          `pool to choose who is in the field.`));
      return;
    }
    const universe = m.universe;
    const sel = selectionFor(m.key);
    const live = !!segParam;
    const served = live && sameSet(sel, m.applied);
    /* When the page cannot re-cut, a chip's state is simply "is this set in the
       numbers" — and it is, all of them. */
    const isOn = c => live ? sel.has(c.tag) : m.applied.has(c.tag);

    segRow.appendChild(el("span", "tlabel", "Who is in it"));

    for (const c of universe) {
      const danger = tagDanger(c), caveat = tagCaveat(c), on = isOn(c);
      const chip = el("button",
        "chip seg" + (on ? " on" : "") + (danger ? " flagged danger" : "") +
        (caveat ? " caveated" : ""));
      chip.appendChild(el("span", "segbox", on ? "✓" : ""));
      chip.append(` ${c.label || c.tag}`);
      if (c.n != null) chip.appendChild(el("span", "cnt", String(c.n)));
      if (danger) chip.appendChild(el("span", "segwarn", "!"));
      else if (caveat) chip.appendChild(el("span", "segcav", "*"));
      const why = tagWhy(c);
      /* `n` is managers WITH a stored squad — the only count that can enter a
         denominator. `n_pool` is how many carry the tag at all, and the gap
         between them is the part of the set nothing here can measure. */
      chip.title = `${c.label || c.tag}` +
        (c.n != null ? ` — ${c.n} with a stored squad` : "") +
        (c.n_pool != null && c.n != null && c.n_pool !== c.n
          ? ` of ${c.n_pool} tagged (${c.n_pool - c.n} have no squad on file, ` +
            `so they are in no denominator here)` : "") +
        (danger ? "\n\nNOT TRUSTWORTHY." : "") +
        (why ? `\n\n${why}` : "") +
        (live ? `\n\nClick to ${on ? "take out of" : "put into"} the field.`
              : "\n\nThis build cannot re-cut the pool, so the set is in the " +
                "numbers above whether or not you want it there.");
      /* Deliberately NOT disabled while a recompute is in flight. Locking the
         chips for the ~1s the panel takes made the second and third click of a
         quick edit silently do nothing; the request token is what makes fast
         clicking safe, and the status line plus the dimmed cards say a
         recompute is running. */
      chip.disabled = !live;
      if (live) chip.onclick = () => {
        if (sel.has(c.tag)) sel.delete(c.tag); else sel.add(c.tag);
        renderSegments();
        applySegments(m.key);
      };
      segRow.appendChild(chip);
    }

    if (live) {
      const reset = el("button", "chip seg ghost", "curated elite (default)");
      const defNames = [...m.def].map(t => nameOf(m, t)).join(", ");
      reset.title =
        `The panel's own default selection: ${defNames}. It leaves out your ` +
        `mini-league — people you happen to play rather than a selected elite, ` +
        `and the one set that contains your own entry — and anything the crawl ` +
        `marks untrustworthy.`;
      reset.disabled = sameSet(sel, m.def);
      reset.onclick = () => {
        segSel[m.key] = new Set(m.def);
        renderSegments(); applySegments(m.key);
      };
      segRow.appendChild(reset);
      const all = el("button", "chip seg ghost", "everyone");
      all.title = "Every set the crawl produced, flagged ones included.";
      all.disabled = sel.size === universe.length;
      all.onclick = () => {
        segSel[m.key] = new Set(universe.map(c => c.tag));
        renderSegments(); applySegments(m.key);
      };
      segRow.appendChild(all);
    }

    /* Status line. This is the sentence that stops the page lying about which
       managers are behind the numbers. */
    const status = el("div", "segstatus");
    if (!live) {
      // Name the sets the reader would most want out, from the payload's own
      // descriptors — this pool may have no mini-league in it at all.
      const flag = universe.filter(tagWarns).map(c => c.label || c.tag);
      status.className = "segstatus warn";
      status.append(el("b", null, "Sets are disclosed, not selectable. "),
        `This build's ownership_eo panel publishes no segment parameter ` +
        `(checked against /api/panels), so all ${universe.length} sets above` +
        (flag.length ? ` — ${flag.join(" and ")} included` : "") +
        ` are inside every number on this page. Recutting EO over a subset is ` +
        `arithmetic only the panel can do: it holds the picks, the browser ` +
        `holds only the totals.`);
    } else if (segBusy) {
      status.className = "segstatus busy";
      status.append(el("span", "spin"),
        `recomputing over ${sel.size} set${sel.size === 1 ? "" : "s"}…`);
    } else if (segError) {
      const stale = nameList(m, m.applied);
      status.className = "segstatus bad";
      if (segError === EMPTY_SEL)
        status.append(el("b", null, "Nothing selected. "),
          "A field of nobody has no ownership to measure, so nothing was " +
          `asked for — the numbers on screen are still the ones served for ` +
          `${stale}. Put a set back.`);
      else
        status.append(el("b", null, "That selection was refused. "),
          `${segError} — the numbers on screen are still the ones served for ` +
          `${stale}.`);
    } else if (!served) {
      status.className = "segstatus warn";
      status.append("Selection not applied yet — the numbers on screen were " +
        `served for ${nameList(m, m.applied)}.`);
    } else {
      status.append(
        el("b", null, `${m.n ?? f.n ?? "?"} managers `),
        `in the field: ${m.denominator || f.denominator}.` +
        (m.isDefault === true ? " This is the default selection." : ""));
    }
    segRow.appendChild(status);

    /* Names the panel could not match. Reported, never silently dropped: a
       request that quietly narrows the field is how a reader ends up comparing
       himself against the wrong people. */
    if (m.unknown.length)
      segRow.appendChild(el("div", "segstatus bad",
        `The panel matched no crawl source for ${m.unknown.join(", ")}, so ` +
        `${m.unknown.length === 1 ? "that set is" : "those sets are"} in ` +
        `nothing above.`));

    /* The overlap disclosure sits next to the counts, because the counts are
       exactly what invites the wrong arithmetic. The panel's own numbers are
       used where it publishes them; otherwise the sets in the field are summed
       here purely to show that the sum is NOT the denominator. */
    const inField = universe.filter(isOn);
    const sum = m.sumOfSets ?? inField.reduce((a, c) => a + (c.n || 0), 0);
    const n = m.n ?? f.n;
    if (m.overlaps || m.overlap || f.overlaps || (n != null && sum > n))
      segRow.appendChild(el("span", "sub",
        `The ${inField.length} sets in the field carry ${sum} memberships ` +
        `between them over ${n ?? "an unstated number of"} distinct managers` +
        (m.overlap ? ` — ${m.overlap} entries hold two tags and are counted ` +
                     `under both` : ": an entry can hold two tags and is " +
                     "counted under both") +
        `. The denominator is the distinct count, never the sum.`));

    if (m.includesYou === true)
      segRow.appendChild(el("div", "segnote",
        "Your own entry is inside this field. You are part of the average you " +
        "are measuring yourself against, which pulls every gap you read here " +
        "towards zero."));
    if (m.unresolved)
      segRow.appendChild(el("span", "sub",
        `${m.unresolved} entries in the union hold at least one pick this ` +
        `engine could not resolve to a player — a hole in the crawl, counted ` +
        `rather than hidden.`));
    if (m.note) segRow.appendChild(el("span", "sub", m.note));

    /* An untrustworthy set that is IN the field gets the loud box, because the
       reader has to be stopped. Everything else that merely needs a sentence
       gets the sentence, quietly, under one heading. */
    for (const c of universe.filter(x => tagDanger(x) && isOn(x))) {
      const w = el("div", "segnote danger");
      w.append(el("span", "chip warn", (c.label || c.tag) + " is in the field"),
               el("span", null, " " + (tagWhy(c) ||
                 "the crawl marks this set untrustworthy.")));
      segRow.appendChild(w);
    }
    const caveats = universe.filter(x => tagCaveat(x) && isOn(x) && tagWhy(x));
    if (caveats.length) {
      const box = el("div", "segcaveats");
      box.appendChild(el("span", "tlabel", "Read with"));
      for (const c of caveats) {
        const line = el("div", "cav");
        line.append(el("b", null, (c.label || c.tag) + " — "), tagWhy(c));
        box.appendChild(line);
      }
      segRow.appendChild(box);
    }
  }

  /* Composition is a disclosure, not decoration: a cohort that is 16% the
     owner's own mini-league opponents is not an independent read of the
     field, and the page has to say so where the number is used. */
  function renderComposition() {
    compRow.textContent = "";
    const f = byKey[fieldKey];
    if (!f) return;

    /* The two mismatches that would otherwise be read as a real difference:
       a field measured at a different gameweek from its baseline, and a feed
       that re-published a settled week under a new number. */
    const gws = [];
    const b = baseOf(measure);
    if (f.gw != null && b && b.gw != null && f.gw !== b.gw)
      gws.push(`field is GW${f.gw}, baseline is GW${b.gw} — different gameweeks`);
    if (f.same_values_as_gw != null)
      gws.push(`values identical to GW${f.same_values_as_gw} (a re-stamped feed)`);
    if (gws.length) compRow.appendChild(el("span", "tlabel", "Mind"));
    for (const g of gws) compRow.appendChild(el("span", "chip warn", g));

    /* The proportion strip. Emphasis, not a four-colour breakdown: the story is
       not "here are the tag proportions", it is "this share of the pool is a
       conflict of interest", so flagged tags take the warning token — a status
       colour with a label beside it — and everything else stays neutral. The
       tag NAMES and counts live in the selector above; this is the shape only.
       Segments the reader has taken out are drawn hollow, so the strip shows
       what was removed as well as what is left. */
    const m = segModel();
    const comp = (m && m.key === f.key ? m.universe : null)
                 || tagUniverse[f.key] || f.composition;
    if (!comp?.length) return;
    const sel = m && m.key === f.key ? selectionFor(f.key) : null;
    const live = !!segParam && !!sel;
    const total = comp.reduce((a, c) => a + (c.n || 0), 0) || 1;
    const strip = el("div", "compstrip");
    strip.title = "the crawl tags behind this pool, by share of the tag total";
    for (const c of comp) {
      const inField = !live || sel.has(c.tag);
      const seg = el("span", "cseg" + (tagWarns(c) ? " flag" : "") +
                             (inField ? "" : " out"));
      seg.style.width = `${(100 * (c.n || 0) / total).toFixed(2)}%`;
      seg.title = `${c.n} — ${c.label || c.tag}` +
                  (inField ? "" : " — not in the field");
      strip.appendChild(seg);
    }
    compRow.appendChild(strip);
    if (live && [...comp].some(c => !sel.has(c.tag)))
      compRow.appendChild(el("span", "sub", "hollow = taken out of the field"));
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
        const t = tile(dispName(worst),
             `biggest hole · ${pct(val(worst, fieldKey, measure))}`,
             "bad",
             `The highest-${MEASURE[measure].short} player in ${f.label} that ` +
             `you do not own. If he hauls, the field gains and you do not.`);
        /* The bridge to the action: what to sell to fund him lives on the
           Dashboard's verdict/solver card, and nothing connected them (R1). */
        const go = el("a", "coverlink", "cover this hole →");
        go.href = "#home";
        go.title = "Opens the Dashboard and focuses the verdict/solver card " +
                   "— the plan that says what to sell to fund him.";
        go.addEventListener("click", focusDashboardPlan);
        t.appendChild(go);
      } else {
        tile("none", `top-${TOP_N} template fully covered`, "good",
             "You hold every player in the stated basis.");
      }

      const bets = [...sourceRows()]
        .filter(r => r.in_squad === true && exposureOf(r) != null)
        .sort((a, b) => exposureOf(b) - exposureOf(a));
      if (bets.length) {
        const b0 = bets[0];
        tile(dispName(b0), `furthest ahead · ${signed(exposureOf(b0))}`, "good",
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

    // grid — solid hairlines, one shade off the surface, never dashed.
    // The x labels render as one run and the y labels as another (each run
    // aria-hidden): interleaving them per-tick read "0% 5% 5% 10% 10%…" in
    // the text layer, two axes shuffled into one nonsense sequence (R3).
    const tickVals = [...new Set(TICKS)];
    for (const v of tickVals) {
      svg.appendChild(sv("line", { x1: sx(v), x2: sx(v), y1: T, y2: H - B,
                                   class: "grid" }));
      svg.appendChild(sv("line", { x1: L, x2: W - R, y1: sy(v), y2: sy(v),
                                   class: "grid" }));
    }
    const xTicks = sv("g", { "aria-hidden": "true" });
    for (const v of tickVals)
      xTicks.appendChild(sv("text", { x: sx(v), y: H - B + 16, class: "tick" },
                            `${v}%`));
    const yTicks = sv("g", { "aria-hidden": "true" });
    for (const v of tickVals)
      if (v) yTicks.appendChild(sv("text", { x: L - 8, y: sy(v) + 4,
                                             class: "tick end" }, `${v}%`));
    svg.append(xTicks, yTicks);
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
      accessMark(c,
        `${dispName(p.r)}: ${b.short || b.label} ${pct(p.x)}, ` +
        `${f.short || f.label} ${pct(p.y)}, gap ${signed(g)}pp` +
        (mine ? ", in your squad" : ""),
        () => showDetail(p.r), () => showTip(p, g), hideTip);
      marks.appendChild(c);
    }
    svg.appendChild(marks);
    watermark(svg, W, H, f);

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
      const nm = dispName(p.r);
      const left = sx(p.x) > W - 150;
      const w = nm.length * 5.8 + 10;
      const x = left ? sx(p.x) - 9 - w : sx(p.x) + 9;
      if (x < L || x + w > W - 2) continue;
      if (!fits(x, sy(p.y), w)) continue;
      svg.appendChild(sv("text", {
        x: left ? sx(p.x) - 9 : sx(p.x) + 9, y: sy(p.y) + 4,
        class: "plabel" + (left ? " end" : "") +
               (p.r.in_squad === true ? " mine" : ""),
      }, nm));
      labelled++;
    }

    wrap.appendChild(svg);
    const tip = el("div", "chartip");
    wrap.appendChild(tip);
    mapCard.appendChild(wrap);

    function showTip(p, g) {
      tip.textContent = "";
      tip.appendChild(el("b", null, dispName(p.r)));
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
    caption(mapCard,
      `${shown.length} players plotted · sqrt axes` +
      (hidden ? ` · ${hidden} under ${FLOOR}% not drawn` : "") +
      ` · hover or focus any mark for its numbers, click for the ladder.`,
      [`Both axes are square-root scaled by the same transform, which is why ` +
       `the ticks are unevenly spaced: it spreads the crowded low end without ` +
       `moving the diagonal, so “above the line” still means exactly “the ` +
       `field is heavier than the game”.` +
       (hidden ? ` The ${hidden} players under ${FLOOR}% on both axes would ` +
                 `sit on top of each other at the origin, so they are left ` +
                 `out rather than drawn as one blob.` : "")]);
  }

  // ---- the swarm: what the template LOOKS like ------------------------
  /* The map answers "how far is this player from the game". It does not answer
     "what shape is the template", because a scatter of 180 players against a
     diagonal hides the one fact that decides a squad: the field's weight is not
     spread evenly across the positions. One row per position, every measured
     player as a mark on the field's own axis, packed sideways so nothing hides
     behind anything, and the answer is legible in a second — a couple of spikes
     over a dense floor, and which positions HAVE a spike.

     Opta's radars work because a number is placed against its own population
     rather than shown raw; the same idea applies here at the level of a single
     mark, so the tooltip reports the player's percentile WITHIN HIS POSITION on
     this field. 20% EO is unremarkable for a midfielder and enormous for a
     goalkeeper, and a reader should not have to know that already.

     Colour is the same diverging gap encoding as the map, anchored at the same
     90th percentile, so warm still means "heavier than the game" everywhere on
     the page. Fill still means "you own him". Neither channel is alone: the
     position on the axis is the value, and the tooltip prints every number. */
  const POS_ORDER = ["GKP", "DEF", "MID", "FWD"];
  const SPIKE = 30;                  // pp — the "this is template" line, stated

  function renderSwarm() {
    swarmCard.textContent = "";
    const f = byKey[fieldKey], b = baseOf(measure);
    swarmCard.appendChild(el("h2", null, "The shape of the template"));
    if (!f) {
      swarmCard.appendChild(emptyBox("no field selected"));
      return;
    }
    const ms = MEASURE[measure].short;
    const all = sourceRows()
      .map(r => ({ r, v: val(r, fieldKey, measure), g: gapOf(r, fieldKey, measure) }))
      .filter(p => p.v != null);
    if (!all.length) {
      swarmCard.appendChild(emptyBox(
        `${f.label} publishes no ${MEASURE[measure].label.toLowerCase()} for any ` +
        `player in this set`,
        "Pick another field or another measure — the row set on screen and the " +
        "field have to overlap before a distribution exists."));
      return;
    }

    const groups = POS_ORDER
      .map(p => ({ pos: p, pts: all.filter(x => x.r.pos === p) }))
      .filter(gp => gp.pts.length);
    const unknown = all.filter(x => !POS_ORDER.includes(x.r.pos));
    if (unknown.length) groups.push({ pos: "?", pts: unknown });

    const spikes = all.filter(p => p.v >= SPIKE).length;
    swarmCard.appendChild(el("p", "sub",
      `Every player ${f.label} measures, along that field's ` +
      `${MEASURE[measure].label.toLowerCase()} axis, one row per position. ` +
      `${spikes} of ${all.length} sit at ${SPIKE}% or more — those are the ` +
      `holdings that cancel out of your rank move. The rest is the ` +
      `floor, and a position with a wide floor and no spike is a position where ` +
      `a differential costs you almost nothing to take.`));

    const W = 840, L = 54, R = 172, T = 30, B = 46;
    const rowH = Math.max(72, Math.min(100, 352 / groups.length));
    const H = T + B + rowH * groups.length;
    const hi = Math.max(20, ...all.map(p => p.v));
    const dom = Math.ceil(hi / 20) * 20;
    const rt = v => Math.sqrt(Math.max(0, v)) / Math.sqrt(dom);
    const sx = v => L + (W - L - R) * rt(v);
    const TICKS = [0, 5, 10, 20, 40, 60, 80, 120, 160, 240]
      .filter(v => v <= dom).concat(dom);

    const wrap = el("div", "chartwrap");
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "fieldmap swarm",
                            role: "img" });
    svg.appendChild(sv("title", {},
      `${all.length} players by ${ms} in ${f.label}, split by position`));

    for (const v of new Set(TICKS)) {
      svg.appendChild(sv("line", { x1: sx(v), x2: sx(v), y1: T - 8, y2: H - B,
                                   class: "grid" }));
      svg.appendChild(sv("text", { x: sx(v), y: H - B + 16, class: "tick" },
                         `${v}%`));
    }
    svg.appendChild(sv("text", { x: (L + W - R) / 2, y: H - 8, class: "axis" },
      `${f.short || f.label} ${ms} %  ·  square-root spaced, so the crowded ` +
      `low end is readable`));

    // The stated template line, drawn once and labelled in words.
    if (SPIKE <= dom) {
      svg.appendChild(sv("line", { x1: sx(SPIKE), x2: sx(SPIKE), y1: T - 8,
                                   y2: H - B, class: "diag" }));
      svg.appendChild(sv("text", { x: sx(SPIKE) + 5, y: T - 14,
                                   class: "diaglabel" },
        `${SPIKE}% — template from here right`));
    }

    const scale = rampScale();
    const tip = makeTip(wrap);
    const labels = [];               // {x, y, w, text, mine} placed last

    groups.forEach((gp, i) => {
      const cy = T + rowH * i + rowH / 2;
      const half = rowH / 2 - 8;
      svg.appendChild(sv("line", { x1: L, x2: W - R, y1: cy, y2: cy,
                                   class: "swarmbase" }));
      svg.appendChild(sv("text", { x: L - 8, y: cy + 4, class: "swarmpos end" },
                         gp.pos));

      const sorted = [...gp.pts].sort((a, c) => a.v - c.v);
      const xs = sorted.map(p => sx(p.v));
      const r = 4.2;
      const lanes = Math.max(1, Math.floor(half / (r * 2.05)));
      const ys = beeswarm(xs, r, lanes);

      // Median tick: where half this position sits, printed as a number too.
      const med = median(sorted.map(p => p.v));
      if (med != null) {
        svg.appendChild(sv("line", { x1: sx(med), x2: sx(med),
                                     y1: cy - half - 2, y2: cy + half + 2,
                                     class: "swarmmed" }));
      }

      sorted.forEach((p, k) => {
        const mine = p.r.in_squad === true;
        const cyy = cy + Math.max(-half, Math.min(half, ys[k]));
        const c = sv("circle", {
          cx: xs[k], cy: cyy, r: mine ? r + 1.2 : r,
          class: "mark" + (mine ? " mine" : " out"),
        });
        if (mine) c.setAttribute("fill", rampColor(p.g, scale));
        else {
          c.setAttribute("fill", "none");
          c.setAttribute("stroke", rampColor(p.g, scale));
        }
        const pctile = Math.round(100 * k / Math.max(1, sorted.length - 1));
        const showT = () =>
          tip.show(svg, W, H, xs[k], cyy, (t, line) => {
            t.appendChild(el("b", null, dispName(p.r)));
            t.appendChild(el("div", "sub",
              [p.r.pos, p.r.team, fmtPrice(p.r.price)].filter(Boolean).join(" · ")));
            line(`${f.short || f.label} ${ms}`, pct(p.v));
            if (b) line(`${b.short || b.label} ${ms}`, pct(val(p.r, b.key, measure)));
            if (p.g != null) line("gap", `${signed(p.g)}pp`);
            line(`among ${gp.pos}`, `${pctile}th pctile`);
            line("you", p.r.in_squad == null ? "unknown"
              : p.r.in_squad === false ? "not owned" : (p.r.your_role || "owned"));
          });
        c.addEventListener("mouseenter", showT);
        c.addEventListener("mouseleave", tip.hide);
        c.addEventListener("click", () => showDetail(p.r));
        accessMark(c,
          `${dispName(p.r)}: ${f.short || f.label} ${ms} ${pct(p.v)}, ` +
          `${pctile}th percentile among ${gp.pos}` +
          (p.r.in_squad === true ? ", in your squad" : ""),
          () => showDetail(p.r), showT, tip.hide);
        svg.appendChild(c);
      });

      /* Row annotation, direct-labelled at the right rather than legended.
         Three short lines rather than one long one: at 375px the chart scales
         down with its viewBox, and a single 44-character string would be the
         thing that decides the plot's width. */
      const nSpike = gp.pts.filter(p => p.v >= SPIKE).length;
      const held = gp.pts.filter(p => p.r.in_squad === true).length;
      const ax = W - R + 12;
      svg.appendChild(sv("text", { x: ax, y: cy - 8, class: "swarmann" },
        nSpike ? `${nSpike} at ${SPIKE}%+` : `no ${SPIKE}%+ player`));
      svg.appendChild(sv("text", { x: ax, y: cy + 6, class: "swarmsub" },
        `${gp.pts.length} measured`));
      svg.appendChild(sv("text", { x: ax, y: cy + 19, class: "swarmsub" },
        `median ${med == null ? "–" : med.toFixed(1)}%` +
        (res.squad?.readable ? ` · you ${held}` : "")));

      // Name the spikes: the two or three that carry the position.
      const top = [...gp.pts].sort((a, c) => c.v - a.v).slice(0, 3);
      for (const p of top) {
        if (p.v < SPIKE) break;
        const k = sorted.indexOf(p);
        labels.push({ x: sx(p.v), y: cy - half - 5, text: dispName(p.r),
                      my: cy + Math.max(-half, Math.min(half, ys[k])),
                      mine: p.r.in_squad === true });
      }
    });

    watermark(svg, W, H, f);

    /* Selective direct labels, collision-tested, each with a hairline leader
       back to its own mark — the label sits above the row and the mark can be
       anywhere in the band, so without the leader the reader has to guess which
       dot it belongs to. Never a name on every mark. */
    const placed = [];
    for (const lb of labels) {
      const w = lb.text.length * 5.8 + 8;
      const left = lb.x > W - R - 80;
      const x0 = left ? lb.x - w : lb.x;
      if (x0 < L || x0 + w > W - R + 2) continue;
      const box = { x: x0, y: lb.y - 10, w, h: 13 };
      if (placed.some(q => !(box.x + box.w < q.x || q.x + q.w < box.x ||
                             box.y + box.h < q.y || q.y + q.h < box.y))) continue;
      placed.push(box);
      svg.appendChild(sv("line", { x1: lb.x, y1: lb.y + 3, x2: lb.x,
                                   y2: lb.my - 6, class: "swarmlead" }));
      svg.appendChild(sv("text",
        { x: lb.x, y: lb.y, class: "plabel" + (left ? " end" : "") +
                                    (lb.mine ? " mine" : "") }, lb.text));
    }

    wrap.appendChild(svg);
    swarmCard.appendChild(wrap);

    const leg = el("div", "maplegend");
    const ramp = el("div", "ramp");
    for (let i = -6; i <= 6; i++) {
      const s = el("span");
      s.style.background = rampColor(i / 6 * scale, scale);
      ramp.appendChild(s);
    }
    leg.append(
      el("span", "tlabel", "gap vs the game"),
      el("span", "sub", `≤ −${scale.toFixed(0)}pp`), ramp,
      el("span", "sub", `≥ +${scale.toFixed(0)}pp`),
      el("span", "legkey mine-key", ""), el("span", "sub", "you own him"),
      el("span", "legkey out-key", ""), el("span", "sub", "you do not"),
      el("span", "legkey med-key", ""), el("span", "sub", "position median"));
    swarmCard.appendChild(leg);
    caption(swarmCard,
      "Horizontal position is the whole of the value · hover or focus a mark " +
      "for its numbers and within-position percentile · every value is also " +
      "in the table below.",
      [`Marks are nudged off the row's centre line only enough to stop them ` +
       `covering each other — the vertical position carries nothing. Where a ` +
       `row is too crowded for even that, near the floor, marks do overlap ` +
       `and the ring around each one is what keeps them countable; that ` +
       `crowding is itself the finding.`]);
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
        id.appendChild(el("div", "lname", dispName(p.r)));
        /* xPts beside the exposure term: "you concede −0.7 per point" is half
           a multiplication — the expected points finish the thought (R1). The
           value is the table's own consensus xpts column, no new joins; the
           spread rides along because it is already on the row. */
        id.appendChild(el("div", "sub",
          `${p.r.pos ?? "?"} · ${p.r.team ?? "?"} · ${fmtPrice(p.r.price)} · ` +
          `${f.short || f.label} EO ${pct(p.v)}` +
          ` · you ${p.m.v}×${p.m.assumed ? "*" : ""}` +
          (p.r.xpts != null
            ? ` · ${fmt1(p.r.xpts)} xPts` +
              (p.r.xpts_spread != null ? `±${fmt1(p.r.xpts_spread)}` : "") +
              (res.xpts_gw != null ? ` gw${res.xpts_gw}` : "")
            : "")));
        const idTitle = [
          p.m.assumed ? "multiplier inferred from your squad role" : null,
          p.r.xpts != null
            ? `consensus xPts across ${p.r.n_sources ?? "?"} sources — ` +
              `the same column the table below shows`
            : null,
        ].filter(Boolean).join("\n");
        if (idTitle) id.title = idTitle;
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

  // ---- cohort vs cohort: where the informed fields disagree -----------
  /* Two informed fields agreeing is not information — it is the same consensus
     twice. The edge is where they split, so this card is sorted by the size of
     the split and nothing else. A dumbbell puts both fields on ONE shared axis:
     the two dots give the levels, the connector between them IS the
     disagreement, and a short connector is visibly noise without anyone having
     to read a number.

     Axis note: this chart is LINEAR where the map and the swarm are
     square-root. That is deliberate. On those two the reader is placing a point;
     here he is comparing LENGTHS, and a square-root axis makes an identical
     20pp split look long at the bottom of the scale and short at the top. The
     transform has to serve the comparison the chart is asking for. */
  let cmpA = null, cmpB = null, cmpTouched = false;
  const CMP_ROWS = 14;

  function informedFields(m) {
    return allFields.filter(f => f.role === "field" && has(f, m));
  }
  function ensureComparePair() {
    const opts = informedFields(measure);
    const keys = opts.map(f => f.key);
    /* Quotable = at or above the MIN_N floor (a field with no countable n is
       a provider feed, not a small sample). The DEFAULT pair is the largest
       two quotable fields; a below-floor field is still selectable, and the
       chart watermarks itself when one is picked (tri-consensus n-guard). */
    const ok = f => !lowN(f);
    const bySize = list => [...list].sort((x, y) => (y.n ?? -1) - (x.n ?? -1));
    /* A follows the field chosen at the top of the page — the reader who
       switches fields up there means "study this one" — until he picks A here
       himself, at which point this card is his and stops being steered. */
    if (!cmpTouched && keys.includes(fieldKey) && ok(byKey[fieldKey]))
      cmpA = fieldKey;
    if (!keys.includes(cmpA))
      cmpA = (bySize(opts.filter(ok))[0] || opts[0] || {}).key
             || (keys.includes(fieldKey) ? fieldKey : keys[0]);
    if (!keys.includes(cmpB) || cmpB === cmpA) {
      // Prefer the other CRAWLED pool — two observed cohorts disagreeing is a
      // sharper read than an observed cohort against a modelled one — then
      // fall back to whatever else publishes the same measure. Never a
      // below-floor cohort by default: n=4 quantizes every share to 25%.
      const a = byKey[cmpA];
      const cands = opts.filter(f => f.key !== cmpA);
      cmpB = (cands.find(f => ok(f) && f.kind === "cohort" &&
                              f.cohort !== a?.cohort)
              || bySize(cands.filter(ok))[0]
              || cands[0] || {}).key || null;
    }
  }

  function renderCompare() {
    compareCard.textContent = "";
    compareCard.appendChild(el("h2", null, "Where the informed fields disagree"));
    const opts = informedFields(measure);
    if (opts.length < 2) {
      compareCard.appendChild(emptyBox(
        `only ${opts.length} field publishes ${MEASURE[measure].label.toLowerCase()}`,
        "This comparison needs two fields measured the same way. Ingest a " +
        "second ownership feed, or crawl a second cohort."));
      return;
    }
    ensureComparePair();
    const A = byKey[cmpA], B2 = byKey[cmpB];

    const bar = el("div", "toolbar cmp");
    const picker = (which, cur, other) => {
      bar.appendChild(el("span", "tlabel", which));
      const sel = el("select");
      for (const f of opts) {
        const o = el("option", null,
          lowN(f) ? `${f.label} — n=${f.n}, too small to quote` : f.label);
        o.value = f.key;
        o.disabled = f.key === other;
        if (lowN(f)) o.className = "lown";
        sel.appendChild(o);
      }
      sel.value = cur;
      sel.onchange = () => {
        if (which === "Field A") { cmpA = sel.value; cmpTouched = true; }
        else cmpB = sel.value;
        ensureComparePair(); renderCompare();
      };
      bar.appendChild(sel);
    };
    picker("Field A", cmpA, cmpB);
    picker("Field B", cmpB, cmpA);
    const swap = el("button", "chip src", "⇄ swap");
    swap.title = "Swap the two fields. The warm pole always means “A is heavier”, " +
                 "so swapping mirrors the colours as well as the dots.";
    swap.onclick = () => {
      const t = cmpA; cmpA = cmpB; cmpB = t; cmpTouched = true; renderCompare();
    };
    bar.appendChild(swap);
    compareCard.appendChild(bar);

    if (!A || !B2) { compareCard.appendChild(emptyBox("field not found")); return; }

    const pairs = sourceRows()
      .map(r => ({ r, a: val(r, cmpA, measure), b: val(r, cmpB, measure) }))
      .filter(p => p.a != null && p.b != null)
      .map(p => ({ ...p, d: p.a - p.b }));
    if (pairs.length < 4) {
      compareCard.appendChild(emptyBox(
        `only ${pairs.length} player has a value on both fields`,
        "The two fields have to overlap on the same players before a " +
        "disagreement can be measured."));
      return;
    }

    // Honesty band: the two mismatches that would be read as disagreement.
    const warns = [];
    if (A.gw != null && B2.gw != null && A.gw !== B2.gw)
      warns.push(`${A.short || A.label} is GW${A.gw}, ${B2.short || B2.label} is ` +
                 `GW${B2.gw} — part of every split below is just the week apart.`);
    for (const f of [A, B2])
      if (f.same_values_as_gw != null)
        warns.push(`${f.label} is stamped GW${f.gw} but is byte-identical to ` +
                   `GW${f.same_values_as_gw}: a re-published settled week, not a ` +
                   `fresh read.`);
    for (const w of warns) compareCard.appendChild(el("p", "warnline", w));

    const absd = pairs.map(p => Math.abs(p.d));
    const med = median(absd);
    const rho = spearman(pairs.map(p => p.a), pairs.map(p => p.b));
    const wide = pairs.filter(p => Math.abs(p.d) >= BAND).length;
    const top = [...pairs].sort((x, y) => Math.abs(y.d) - Math.abs(x.d));

    const tl = el("div", "stats");
    const tile = (v, k, cls, title) => {
      const d = el("div", "stat" + (cls ? " " + cls : ""));
      d.appendChild(el("div", "v", v));
      d.appendChild(el("div", "k", k));
      if (title) d.title = title;
      tl.appendChild(d);
    };
    tile(String(pairs.length), "players both fields measure", null,
         `${A.label}: ${A.denominator}\n${B2.label}: ${B2.denominator}`);
    tile(med == null ? "–" : `${med.toFixed(1)}pp`, "median split between them", null,
         "Half the shared players are further apart than this, half closer.");
    tile(String(wide), `split by ${BAND}pp or more`,
         wide > pairs.length * 0.25 ? "bad" : null,
         `Counted over the ${pairs.length} players both fields measure.`);
    // No Greek in a tile key: `.stat .k` is uppercased, and "ρ" comes out "Ρ".
    tile(rho == null ? "–" : rho.toFixed(2), "spearman rank agreement",
         rho != null && rho < 0.8 ? "bad" : "good",
         "Spearman rank correlation over the shared players: 1.00 means the " +
         "two fields order the pool identically, 0 means they order it " +
         "independently. Computed on ranks, so the two or three captain-heavy " +
         "premiums at the top of the scale cannot carry it on their own.");
    compareCard.appendChild(tl);

    compareCard.appendChild(el("p", "sub",
      `The ${CMP_ROWS} players these two fields most disagree about, both on one ` +
      `${MEASURE[measure].label.toLowerCase()} axis. The dot is where each field has him; the bar between them is ` +
      `the disagreement, and its colour says which way. ` +
      `${A.label} is ${A.denominator}. ${B2.label} is ${B2.denominator}.`));

    const shown = top.slice(0, CMP_ROWS);
    /* R leaves room for three printed columns AND clearance: the heaviest dot
       lands at the plot's right edge, and a 5px radius on top of a 36px number
       is exactly how the two collide. */
    const W = 880, L = 152, R = 176, T = 44, B = 36, ROW = 27;
    const NUMX = [W - 124, W - 74, W - 12];
    const H = T + B + ROW * shown.length;
    const hi = Math.max(10, ...shown.flatMap(p => [p.a, p.b]));
    const dom = Math.ceil(hi / 10) * 10;
    const sx = v => L + (W - L - R) * (Math.max(0, v) / dom);
    const step = dom > 120 ? 40 : dom > 60 ? 20 : 10;

    // The disagreement ramp: same diverging pair, same 90th-percentile anchor
    // rule as the rest of the page, but scaled to THIS distribution.
    const ds = absd.slice().sort((a, c) => a - c);
    const dScale = Math.max(5, ds[Math.floor(ds.length * 0.9)] ?? ds[ds.length - 1]);

    const wrap = el("div", "chartwrap");
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "fieldmap dumbbell",
                            role: "img" });
    svg.appendChild(sv("title", {},
      `${A.label} against ${B2.label}: the ${shown.length} biggest splits`));

    for (let v = 0; v <= dom; v += step) {
      svg.appendChild(sv("line", { x1: sx(v), x2: sx(v), y1: T - 12,
                                   y2: H - B + 2, class: "grid" }));
      svg.appendChild(sv("text", { x: sx(v), y: H - B + 18, class: "tick" },
                         `${v}%`));
    }
    svg.appendChild(sv("text", { x: (L + W - R) / 2, y: H - 6, class: "axis" },
      `${MEASURE[measure].label} %  ·  one linear axis, so bar length is the split`));

    const tip = makeTip(wrap);
    shown.forEach((p, i) => {
      const cy = T + ROW * i + ROW / 2;
      const xa = sx(p.a), xb = sx(p.b);
      const col = rampColor(p.d, dScale);

      const hit = sv("rect", { x: 0, y: cy - ROW / 2, width: W, height: ROW,
                               class: "rowhit" });
      hit.addEventListener("mouseenter", () =>
        tip.show(svg, W, H, Math.max(xa, xb), cy, (t, line) => {
          t.appendChild(el("b", null, dispName(p.r)));
          t.appendChild(el("div", "sub",
            [p.r.pos, p.r.team, fmtPrice(p.r.price)].filter(Boolean).join(" · ")));
          line(A.short || A.label, pct(p.a));
          line(B2.short || B2.label, pct(p.b));
          line("split", `${signed(p.d)}pp`);
          line("you", p.r.in_squad == null ? "unknown"
            : p.r.in_squad === false ? "not owned" : (p.r.your_role || "owned"));
        }));
      hit.addEventListener("mouseleave", tip.hide);
      hit.addEventListener("click", () => showDetail(p.r));
      svg.appendChild(hit);

      svg.appendChild(sv("line", { x1: xa, y1: cy, x2: xb, y2: cy,
                                   class: "dbar", stroke: col }));
      // B is the hollow ring, A the filled dot: shape carries identity so hue
      // is left entirely to the direction of the split.
      const rb = sv("circle", { cx: xb, cy, r: 5, class: "dend b" });
      const ra = sv("circle", { cx: xa, cy, r: 5, class: "dend a", fill: col });
      svg.append(rb, ra);

      const nm = sv("text", { x: L - 12, y: cy + 4,
                              class: "dname end" +
                                     (p.r.in_squad === true ? " mine" : "") },
                    dispName(p.r));
      svg.appendChild(nm);
      svg.appendChild(sv("text", { x: NUMX[0], y: cy + 4, class: "dnum end" },
                         pct(p.a)));
      svg.appendChild(sv("text", { x: NUMX[1], y: cy + 4, class: "dnum end" },
                         pct(p.b)));
      svg.appendChild(sv("text", { x: NUMX[2], y: cy + 4,
                                   class: "dnum end strong" }, `${signed(p.d)}`));

      // Direct labels on the first row only — the legend below carries the
      // rest, and a label on every dot would be 28 labels of noise.
      if (i === 0) {
        svg.appendChild(sv("text", { x: xa, y: cy - 12, class: "dkey",
                                     "text-anchor": "middle" }, A.short || A.label));
        svg.appendChild(sv("text", { x: xb, y: cy - 12, class: "dkey",
                                     "text-anchor": "middle" }, B2.short || B2.label));
      }
    });
    svg.appendChild(sv("text", { x: NUMX[0], y: T - 18, class: "dhead end" },
                       A.short || A.label));
    svg.appendChild(sv("text", { x: NUMX[1], y: T - 18, class: "dhead end" },
                       B2.short || B2.label));
    svg.appendChild(sv("text", { x: NUMX[2], y: T - 18, class: "dhead end" },
                       "split"));
    watermark(svg, W, H, lowN(A) ? A : B2);

    wrap.appendChild(svg);
    compareCard.appendChild(wrap);
    if (lowN(A) || lowN(B2)) {
      const s = lowN(A) ? A : B2;
      compareCard.appendChild(el("p", "warnline",
        `${s.label} is ${s.n} managers — every one of its shares is a ` +
        `multiple of ${(100 / s.n).toFixed(0)}%, so the biggest “splits” ` +
        `here are quantization, not disagreement. It is never a default; ` +
        `you picked it, and the chart is watermarked while it is on.`));
    }

    const leg = el("div", "maplegend");
    const ramp = el("div", "ramp");
    for (let i = -6; i <= 6; i++) {
      const s = el("span");
      s.style.background = rampColor(i / 6 * dScale, dScale);
      ramp.appendChild(s);
    }
    leg.append(
      el("span", "legkey a-key", ""), el("span", "sub", A.short || A.label),
      el("span", "legkey b-key", ""), el("span", "sub", B2.short || B2.label),
      el("span", "tlabel", "split"),
      el("span", "sub", `${B2.short || B2.label} heavier`), ramp,
      el("span", "sub", `${A.short || A.label} heavier`));
    compareCard.appendChild(leg);

    const agree = [...pairs].sort((x, y) => Math.abs(x.d) - Math.abs(y.d))
      .filter(p => Math.max(p.a, p.b) >= BAND).slice(0, 6);
    if (agree.length) {
      const line = el("p", "sub");
      line.append("Both fields already agree on ",
        el("b", null, agree.map(p => dispName(p.r)).join(", ")),
        ` — held at ${BAND}%+ by both and within ` +
        `${Math.max(...agree.map(p => Math.abs(p.d))).toFixed(1)}pp. Those are ` +
        `insurance, not a decision.`);
      compareCard.appendChild(line);
    }
    caption(compareCard,
      `Every number is printed beside its row · hover a row for the full ` +
      `read, click it for the ladder.`,
      [`Colour saturates at ±${dScale.toFixed(0)}pp, the 90th percentile of ` +
       `the split across all ${pairs.length} shared players, so one extreme ` +
       `case cannot flatten the rest. The axis is linear where the map above ` +
       `is square-root, because here the reader compares LENGTHS and a ` +
       `nonlinear axis would make identical splits look different sizes.`]);
  }

  // ---- ownership momentum ---------------------------------------------
  /* Per-gameweek EO movement, when there is more than one gameweek.

     As of this build there is not. Every crawled cohort holds GW1 squads and
     nothing else; LiveFPL's GW2 rows are byte-identical re-stamps of GW1, which
     the panel measures rather than assumes and reports as `same_values_as_gw`.
     One observation has no direction, so this card draws the OBSERVATION LEDGER
     — which field has been measured at which gameweek, which of those are
     copies, and when the next real point lands — instead of a line. A flat line
     across a single point is a claim of stability, and nothing in the warehouse
     supports that claim.

     The movement path below is live code, not a placeholder: as soon as a field
     ships per-gameweek values on the rows, the card switches to the slope
     chart. It reads several plausible shapes because the key that will carry
     them does not exist yet. */
  let deadline = null;               // {gw, deadline_utc} once fetched
  let momOpen = false;               // the folded ledger's open state, kept
                                     // across redraws (deadline fetch redraws)

  /* The panel's own momentum view when it publishes one: `available`, a
     `reason` in its words, the gameweeks it has, and one series per player with
     `own_pct` / `eo_pct` per gameweek. Normalised to [{r, s:[{gw, v}]}] so the
     slope chart does not care which source it came from. */
  function panelSeries() {
    const mo = res.momentum;
    if (!mo || !Array.isArray(mo.series) || !mo.series.length) return null;
    const key = measure === "own" ? "own_pct" : "eo_pct";
    const out = [];
    for (const sr of mo.series) {
      const pts = (sr.points || [])
        .map(p => ({ gw: Number(p.gw), v: p[key] }))
        .filter(p => isFinite(p.gw) && p.v != null)
        .sort((a, b) => a.gw - b.gw);
      if (pts.length < 2) continue;
      out.push({ r: rowByCode(sr.code) ||
                    { code: sr.code, name: sr.name || `#${sr.code}` }, s: pts });
    }
    return out.length ? out : null;
  }

  function eoSeries(r, key) {
    const m = r.fields?.[key] || {};
    const raw = m.by_gw || m.series || m.history ||
                (r.series && r.series[key]) || (r.by_gw && r.by_gw[key]) || null;
    if (!raw) return null;
    let pts = [];
    if (Array.isArray(raw)) {
      pts = raw.map(o => ({
        gw: Number(o.gw ?? o.gameweek),
        v: o[measure] ?? o.value ?? (typeof o === "number" ? o : null),
      }));
    } else if (typeof raw === "object") {
      pts = Object.entries(raw).map(([gw, o]) => ({
        gw: Number(gw),
        v: typeof o === "number" ? o : (o?.[measure] ?? o?.value ?? null),
      }));
    }
    pts = pts.filter(p => isFinite(p.gw) && p.v != null)
             .sort((a, b) => a.gw - b.gw);
    return pts.length >= 2 ? pts : null;
  }

  function renderMomentum() {
    momentumCard.textContent = "";
    momentumCard.appendChild(el("h2", null, "Ownership momentum"));
    const f = byKey[fieldKey];
    if (!f) { momentumCard.appendChild(emptyBox("no field selected")); return; }

    const moving = panelSeries() || sourceRows()
      .map(r => ({ r, s: eoSeries(r, fieldKey) }))
      .filter(p => p.s);
    if (moving.length >= 4) {
      /* A trend exists — but of 38 gameweeks it may be ONE delta between the
         first two, mostly fringe players. Until four gameweeks are observed
         the section collapses to a line and the slopes live behind it
         (R1+R3); at 4+ observations it opens itself for good. */
      const gwObs = new Set(moving.flatMap(p => p.s.map(x => x.gw)));
      if (gwObs.size >= 4) { renderSlopes(f, moving); return; }
      const fold = el("details", "momfold");
      fold.open = momOpen;
      fold.addEventListener("toggle", () => { momOpen = fold.open; });
      fold.appendChild(el("summary", null,
        `${gwObs.size} of 4 gameweek observations — one early delta, thin ` +
        `evidence of direction. Collapsed until four gameweeks are on file; ` +
        `open for the early movers.`));
      const mom = el("div");
      fold.appendChild(mom);
      momentumCard.appendChild(fold);
      renderSlopes(f, moving, mom);
      return;
    }

    /* ---- the honest state: an observation ledger ---------------------- */
    const mo = res.momentum || null;
    const season = res.season;
    const cov = (res.gws_covered || []).filter(c => c.season === season);
    /* One lane per field the page can actually measure. A field's observed
       gameweeks come from `gws_covered` where the panel names a metric for it,
       and from its own `gw` stamp otherwise — the crawled cohorts have no
       coverage row because they are not a provider feed. */
    const lanes = allFields.map(fd => {
      const rows = fd.metric
        ? cov.filter(c => c.metric === fd.metric &&
                          (!fd.provider || !c.provider || c.provider === fd.provider))
        : [];
      const gws = [...new Set(rows.map(c => c.gw)
        .concat(fd.gw != null ? [fd.gw] : []))].sort((a, b) => a - b);
      return { f: fd, gws };
    }).filter(l => l.gws.length);
    if (!lanes.length) {
      momentumCard.appendChild(emptyBox(
        "no field carries a gameweek stamp",
        "Movement is measured between gameweeks; nothing on this page is " +
        "stamped with one yet."));
      return;
    }

    /* Two different scarcities, and conflating them was the first mistake this
       card made. The WAREHOUSE may hold several gameweeks of a provider feed;
       the PAYLOAD carries one value per player per field — the latest — so
       there is nothing on the wire to difference against. Separately, the
       crawled pools genuinely have one gameweek, because that is all that has
       been played and stored. Both are stated, neither is dressed as the
       other. */
    const distinct = new Set();
    for (const l of lanes)
      for (const g of l.gws)
        if (!(l.f.same_values_as_gw != null && g === l.f.gw)) distinct.add(g);
    const crawlGws = [...new Set(lanes.filter(l => l.f.kind === "cohort")
      .flatMap(l => l.gws))].sort((a, b) => a - b);
    const restamped = lanes.filter(l => l.f.same_values_as_gw != null);
    const nextGw = mo?.next_gw ?? deadline?.gw ?? (Math.max(...distinct, 0) + 1);
    /* The panel's own deadline for that gameweek outranks the app-wide chip. */
    const nextDeadline = mo?.next_deadline_utc ||
      (deadline && deadline.gw === nextGw ? deadline.deadline_utc : null);
    const needed = mo?.min_gws_for_a_trend ?? 2;
    const have = mo?.gws?.length ?? 1;
    const maxGw = Math.max(nextGw, ...lanes.flatMap(l => l.gws));
    const minGw = Math.min(...lanes.flatMap(l => l.gws));

    /* Under 4 observed movers there is no trend to draw, so the SECTION
       COLLAPSES to one honest line (R1+R3): of 38 gameweeks this is one
       delta at best, and a full card this early is distraction wearing a
       chart. The observation ledger survives, behind the fold, unchanged. */
    const fold = el("details", "momfold");
    fold.open = momOpen;
    fold.addEventListener("toggle", () => { momOpen = fold.open; });
    fold.appendChild(el("summary", null,
      `No trend yet — ${have} of ${needed} gameweeks measured. This card ` +
      `becomes a movement chart at the second distinct observation` +
      (nextGw != null ? ` (GW${nextGw})` : "") +
      `; open for the observation ledger.`));
    const mom = el("div");
    fold.appendChild(mom);
    momentumCard.appendChild(fold);

    const tl = el("div", "stats");
    const tile = (v, k, cls, title) => {
      const d = el("div", "stat" + (cls ? " " + cls : ""));
      d.appendChild(el("div", "v", v));
      d.appendChild(el("div", "k", k));
      if (title) d.title = title;
      tl.appendChild(d);
    };
    tile(`${have} of ${needed}`, "gameweeks the field can be measured at", "bad",
         "A direction needs two values for the same player on the same field. " +
         "There is one, so there is nothing to difference against — whatever " +
         "the warehouse holds behind it.");
    tile(crawlGws.length
      ? (crawlGws.length === 1 ? `GW${crawlGws[0]}` : `GW${crawlGws[0]}–${crawlGws[crawlGws.length - 1]}`)
      : "none", "gameweeks of crawled squads",
         crawlGws.length < 2 ? "bad" : null,
         "Stored squads are the only observed field on this page; the rest are " +
         "provider models. There is one week of them.");
    tile(String(distinct.size), "gameweeks in the warehouse", null,
         restamped.length
           ? `Re-stamps are not counted: ${restamped[0].f.label} is stamped ` +
             `GW${restamped[0].f.gw} but republishes ` +
             `GW${restamped[0].f.same_values_as_gw}'s values unchanged, which ` +
             `is one observation wearing two numbers.`
           : "Counted across every field the page can measure.");
    tile(`GW${nextGw}`, "next crawled point", null,
         "When squads for this gameweek lock and the crawl stores them, this " +
         "card becomes a movement chart on its own.");
    mom.appendChild(tl);

    /* The panel's own account of why, verbatim, when it gives one — it knows
       what it looked for and did not find. The page adds only the argument for
       drawing nothing at all rather than a line through a single point. */
    const warn = el("p", "warnline");
    warn.append(el("b", null, "No trend is drawn here. "),
      mo?.reason
        ? mo.reason + " "
        : "This payload carries a single value per player per field, so no " +
          "difference can be taken — and the crawled pools have only ever " +
          "stored GW1 squads, while the LiveFPL series stamped GW2 carry GW1's " +
          "values unchanged, which the panel checks byte for byte rather than " +
          "trusting the stamp. ",
      "A flat line across one observation would say “ownership is stable”; " +
      "nothing here supports that claim, so nothing here draws it.");
    mom.appendChild(warn);
    mom.appendChild(el("p", "sub",
      "What can be shown honestly instead: every gameweek each field has " +
      "actually been measured at. Solid means a distinct observation; hollow " +
      "with a tie-back means the feed republished an earlier week under a new " +
      "number; the ring marks the values this page is using right now; dashed " +
      "means not measured yet."));

    const W = 860, L = 236, R = 34, T = 40, B = 44, ROW = 30;
    const H = T + B + ROW * lanes.length;
    const span = Math.max(1, maxGw - minGw);
    const sx = g => L + (W - L - R) * ((g - minGw) / span);

    const wrap = el("div", "chartwrap");
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "fieldmap ledgerchart",
                            role: "img" });
    svg.appendChild(sv("title", {},
      `gameweeks each field has been measured at, ${season}`));

    for (let g = minGw; g <= maxGw; g++) {
      svg.appendChild(sv("line", { x1: sx(g), x2: sx(g), y1: T - 14,
                                   y2: H - B + 2, class: "grid" }));
      svg.appendChild(sv("text", { x: sx(g), y: H - B + 18, class: "tick" },
                         `GW${g}`));
    }
    svg.appendChild(sv("text", { x: (L + W - R) / 2, y: H - 6, class: "axis" },
      `gameweek · ${season}`));

    /* The future column: where the next observation lands. Drawn when at least
       one lane is still missing that gameweek, and never as a data mark — it is
       the one dashed rule on the page and it means exactly "not yet". */
    if (lanes.some(l => !l.gws.includes(nextGw)) && nextGw <= maxGw) {
      svg.appendChild(sv("line", { x1: sx(nextGw), x2: sx(nextGw), y1: T - 14,
                                   y2: H - B + 2, class: "futureline" }));
      svg.appendChild(sv("text", { x: sx(nextGw), y: T - 20, class: "diaglabel end" },
        nextDeadline
          ? `GW${nextGw} squads lock ${new Date(nextDeadline)
              .toUTCString().slice(5, 22)} UTC`
          : `GW${nextGw} — not measured yet`));
    }

    lanes.forEach((l, i) => {
      const cy = T + ROW * i + ROW / 2;
      svg.appendChild(sv("line", { x1: L, x2: W - R, y1: cy, y2: cy,
                                   class: "swarmbase" }));
      const nm = sv("text", { x: L - 12, y: cy + 4, class: "dname end" },
                    l.f.short || l.f.label);
      nm.appendChild(sv("title", {}, `${l.f.label}\n% of: ${l.f.denominator}`));
      svg.appendChild(nm);

      const stamped = l.f.gw, copy = l.f.same_values_as_gw;
      for (const g of l.gws) {
        const isCopy = copy != null && g === stamped;
        if (isCopy && l.gws.includes(copy))
          svg.appendChild(sv("line", { x1: sx(copy), x2: sx(g), y1: cy, y2: cy,
                                       class: "copyline" }));
        const c = sv("circle", { cx: sx(g), cy, r: 5,
                                 class: "obs" + (isCopy ? " copy" : "") });
        c.appendChild(sv("title", {}, isCopy
          ? `${l.f.label}: stamped GW${g}, values byte-identical to GW${copy} — ` +
            `a republished settled week, not a new observation`
          : `${l.f.label}: measured at GW${g}`));
        svg.appendChild(c);
        if (g === stamped)
          svg.appendChild(sv("circle", { cx: sx(g), cy, r: 9, class: "served" }));
      }
      // A crawled pool will get its next point once GW squads lock.
      if (l.f.kind === "cohort" && !l.gws.includes(nextGw))
        svg.appendChild(sv("circle", { cx: sx(nextGw), cy, r: 5,
                                       class: "obs future" }));
    });

    wrap.appendChild(svg);
    mom.appendChild(wrap);

    const leg = el("div", "maplegend");
    leg.append(
      el("span", "legkey obs-key", ""), el("span", "sub", "distinct observation"),
      el("span", "legkey copy-key", ""), el("span", "sub", "re-stamp of an earlier week"),
      el("span", "legkey served-key", ""), el("span", "sub", "the values on screen"),
      el("span", "legkey future-key", ""), el("span", "sub", "not measured yet"));
    mom.appendChild(leg);

    const dl = el("p", "sub");
    dl.append("This card turns into a movement chart the moment a second " +
      "distinct observation exists on the same field. ");
    if (nextDeadline) {
      const when = new Date(nextDeadline);
      dl.append("The GW", el("b", null, String(nextGw)),
        " deadline is ", el("b", null, when.toUTCString().slice(0, 22) + " UTC"),
        "; squads lock then and the crawl can store a second week.");
    } else {
      dl.append("No deadline is on the wire, so no date is claimed here.");
    }
    mom.appendChild(dl);
    mom.appendChild(el("p", "sub",
      `One thing the ledger shows that the rest of the page cannot: the ` +
      `warehouse already holds earlier gameweeks for the provider feeds, but ` +
      `this panel serves only the latest value per player, so the earlier ones ` +
      `are not on the wire. Per-gameweek values on rows[].fields[…] would light ` +
      `this card up without waiting for GW${nextGw}.`));
  }

  /* The live path. One line per player between the last two gameweeks he was
     measured at, coloured by the direction of the move, names direct-labelled
     at the end each line arrives at — a slope chart, not a spaghetti of 200. */
  function renderSlopes(f, moving, host2 = momentumCard) {
    const ms = MEASURE[measure].short;
    const items = moving.map(p => {
      const s = p.s, a = s[s.length - 2], b2 = s[s.length - 1];
      return { r: p.r, from: a, to: b2, d: b2.v - a.v };
    }).filter(x => isFinite(x.d));
    const top = [...items].sort((a, b2) => Math.abs(b2.d) - Math.abs(a.d))
      .slice(0, 14);
    const gwA = top[0].from.gw, gwB = top[0].to.gw;

    host2.appendChild(el("p", "sub",
      `How ${f.label}'s ${MEASURE[measure].label.toLowerCase()} moved between ` +
      `GW${gwA} and GW${gwB}, for the ${top.length} players it moved most. A ` +
      `player the field is piling into is a hole opening up; a player it is ` +
      `leaving is a differential becoming cheap.`));

    const W = 800, L = 90, R = 190, T = 30, B = 34;
    const H = 420;
    const hi = Math.max(10, ...top.flatMap(x => [x.from.v, x.to.v]));
    const dom = Math.ceil(hi / 10) * 10;
    const sy = v => H - B - (H - B - T) * (Math.max(0, v) / dom);
    const xA = L + 40, xB = W - R - 40;
    /* Anchored at the 90th percentile of the DISPLAYED rows, not of all
       movers: anchored on the full set, every displayed row (they are the
       biggest movers by construction) sat past saturation and the colour
       channel carried nothing (R3). */
    const ds = top.map(x => Math.abs(x.d)).sort((a, b2) => a - b2);
    const dScale = Math.max(2, ds[Math.floor(ds.length * 0.9)] ?? ds[ds.length - 1]);

    const wrap = el("div", "chartwrap");
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "fieldmap slopes",
                            role: "img" });
    const step = dom > 120 ? 40 : dom > 60 ? 20 : 10;
    for (let v = 0; v <= dom; v += step) {
      svg.appendChild(sv("line", { x1: L, x2: W - R, y1: sy(v), y2: sy(v),
                                   class: "grid" }));
      svg.appendChild(sv("text", { x: L - 8, y: sy(v) + 4, class: "tick end" },
                         `${v}%`));
    }
    svg.appendChild(sv("text", { x: xA, y: T - 10, class: "dhead",
                                 "text-anchor": "middle" }, `GW${gwA}`));
    svg.appendChild(sv("text", { x: xB, y: T - 10, class: "dhead",
                                 "text-anchor": "middle" }, `GW${gwB}`));
    svg.appendChild(sv("text", { x: 0, y: 0, class: "axis",
      transform: `translate(15 ${(T + H - B) / 2}) rotate(-90)` },
      `${f.short || f.label} ${ms} %`));

    const tip = makeTip(wrap);
    const placed = [];
    for (const x of [...top].sort((a, b2) => b2.to.v - a.to.v)) {
      const col = rampColor(x.d, dScale);
      const l = sv("line", { x1: xA, y1: sy(x.from.v), x2: xB, y2: sy(x.to.v),
                             class: "slope", stroke: col });
      l.addEventListener("mouseenter", () =>
        tip.show(svg, W, H, xB, sy(x.to.v), (t, line) => {
          t.appendChild(el("b", null, dispName(x.r)));
          line(`GW${gwA}`, pct(x.from.v));
          line(`GW${gwB}`, pct(x.to.v));
          line("move", `${signed(x.d)}pp`);
        }));
      l.addEventListener("mouseleave", tip.hide);
      l.addEventListener("click", () => showDetail(x.r));
      svg.appendChild(l);
      svg.appendChild(sv("circle", { cx: xA, cy: sy(x.from.v), r: 4,
                                     class: "dend b" }));
      svg.appendChild(sv("circle", { cx: xB, cy: sy(x.to.v), r: 4.5,
                                     class: "dend a", fill: col }));
      let y = sy(x.to.v) + 4;
      while (placed.some(q => Math.abs(q - y) < 13)) y += 13;
      placed.push(y);
      svg.appendChild(sv("text", { x: xB + 10, y,
        class: "plabel" + (x.r.in_squad === true ? " mine" : "") },
        `${dispName(x.r)}  ${signed(x.d)}`));
    }
    wrap.appendChild(svg);
    host2.appendChild(wrap);
    caption(host2,
      `The number is printed beside every name — colour is never the only ` +
      `channel.`,
      [`Colour saturates at ±${dScale.toFixed(0)}pp, the 90th percentile of ` +
       `the move across the ${top.length} rows DRAWN (of ${items.length} ` +
       `players with two observations) — rescaled to the displayed set so ` +
       `the biggest movers, the only rows on screen, still differ in tint.`]);
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
  /* Sortable headers that SAY so: aria-sort for assistive tech, a persistent
     glyph on every sortable column (the sorted one gets the direction, the
     rest a quiet ⇅), and keyboard operation — a cursor:pointer with no
     affordance was a promised interaction that wasn't real (R3). */
  function th(label, spec, opts = {}) {
    const on = sameSort(spec);
    const h = el("th", (opts.num === false ? "" : "num") +
                       (on ? " sorted" : " sortable"), label);
    h.dataset.dir = on ? (sortDir === -1 ? "▼" : "▲") : "⇅";
    h.setAttribute("aria-sort",
      on ? (sortDir === -1 ? "descending" : "ascending") : "none");
    h.setAttribute("role", "columnheader");
    h.tabIndex = 0;
    if (opts.title) h.title = opts.title;
    const go = () => {
      sortDir = sameSort(spec) ? -sortDir : -1;
      sortBy = spec; renderBody();
    };
    h.onclick = go;
    h.onkeydown = e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
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
      /* The baseline header names its MEASURE and its capture instant: under
         the EO toggle this column is LiveFPL's predicted EO (captured before
         the previous deadline), under own% it is FPL ownership. Without the
         suffix the same words held two numbers 2× apart (R2). */
      th(baselineLabel(b), { kind: "base" },
         { title: b ? `% of: ${b.denominator}` +
             (b.key === "eo_predicted" && res.eo_pred_captured?.as_of
               ? `\nLiveFPL capture instant: ${res.eo_pred_captured.as_of}` +
                 (res.eo_pred_captured.gw != null
                   ? ` (GW${res.eo_pred_captured.gw})` : "")
               : "") : "" }),
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
    const CUT = 150;
    const shown2 = showAllRows ? rows : rows.slice(0, CUT);
    const tb = el("tbody");
    for (const r of shown2) {
      const g = gapOf(r, fieldKey, measure);
      const tr = el("tr");
      const nameTd = el("td", "clickable");
      nameTd.appendChild(faceImg(r.code, "avatar" +
        (r.in_squad === true ? " mine" : "")));
      nameTd.appendChild(document.createTextNode(dispName(r)));
      const av = availChip(r.status);
      if (av) { nameTd.appendChild(document.createTextNode(" ")); nameTd.appendChild(av); }
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

      /* Three facts, three separated badges — "270/309240 C" in the text
         layer was three values glued into one unpunctuated cell (R1). */
      const m = r.fields?.[fieldKey] || {};
      const heldTd = el("td", "held");
      if (m.owned_by != null && f?.n != null) {
        heldTd.appendChild(el("span", "frac", `${m.owned_by}/${f.n}`));
        if (m.captained_by) {
          heldTd.appendChild(el("span", "hsep", "·"));
          heldTd.appendChild(el("span", "chip s1", `${m.captained_by} C`));
        }
        if (m.benched_by) {
          heldTd.appendChild(el("span", "hsep", "·"));
          heldTd.appendChild(el("span", "chip warn", `${m.benched_by} benched`));
        }
        heldTd.title = `${m.owned_by} of ${f.n} own him` +
          (m.captained_by ? ` · ${m.captained_by} captain him` : "") +
          (m.benched_by ? ` · ${m.benched_by} bench him` : "");
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
    const footLine = el("p", "sub");
    footLine.append(
      `${rows.length} rows` +
      (rows.length > CUT
        ? showAllRows ? ", all shown" : `, showing the first ${CUT}`
        : "") +
      ` · ring on a photo = in your squad · bar tint = the gap, and the ` +
      `number is always printed beside it · “held by” is the count behind ` +
      `the percentage, not a second estimate of it.`);
    if (rows.length > CUT) {
      const more = el("button", "chip", showAllRows
        ? `show only the first ${CUT}` : `show all ${rows.length} rows`);
      more.onclick = () => { showAllRows = !showAllRows; renderBody(); };
      footLine.appendChild(more);
    }
    tbody.appendChild(footLine);
  }

  // ---- drawer: every field's read on one player -----------------------
  function showDetail(r) {
    chatter?.cancel(); chatter = null;
    drawer.textContent = "";
    drawer.classList.add("open");
    const hd = el("div", "dhead");
    hd.appendChild(faceImg(r.code, "bigface"));
    const id = el("div");
    id.appendChild(el("div", "dname", dispName(r)));
    id.appendChild(el("div", "sub", [r.pos, r.team, fmtPrice(r.price),
      r.xpts != null ? `${fmt1(r.xpts)} xPts` : null].filter(Boolean).join(" · ")));
    hd.appendChild(id);
    const x = el("button", null, "✕");
    x.onclick = closeDrawer;
    hd.appendChild(x);
    drawer.appendChild(hd);

    const av = availChip(r.status);
    if (av) {
      const line = el("p", "sub availline");
      line.append("availability: ");
      line.appendChild(av);
      drawer.appendChild(line);
    }

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

    // Below "Your position on him": what the panel owns, said and noticed
    // about him. Async and self-contained — it never blocks the drawer above.
    chatter = chatterStrip(drawer, r.code, { name: r.name });
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

  // ---- the seam to the tools half -------------------------------------
  /* template-tools.js owns the squad-vs-field diff and the what-if simulator.
     It renders from ctx and holds no cross-render state, so it is re-invoked on
     every selection change alongside everything else. The four documented keys
     come first; the rest are conveniences it may ignore. A throw from that
     module is contained here — a broken tool must not take the page with it. */
  function rowByCode(code) {
    const all = (res.rows || []).concat(res.differentials || []);
    return all.find(r => r.code === code) || null;
  }
  function mountTools() {
    toolsHost.textContent = "";
    try {
      renderTools(toolsHost, {
        res,
        fieldKey,
        measure,
        onFocus: code => { const r = rowByCode(code); if (r) showDetail(r); },
        // extras, clearly named and safe to ignore
        dispName,
        baselineKey: baseOf(measure)?.key ?? null,
        rows: sourceRows(),
        // The sets actually behind the numbers in `res`, straight off the
        // payload's own `selection` where the panel publishes one.
        segments: (() => {
          const m = segModel();
          return m ? [...m.applied] : null;
        })(),
        segmentParam: segParam,
        provenance: prov,
      });
    } catch (e) {
      toolsHost.textContent = "";
      const c = el("section", "card");
      c.appendChild(el("h2", null, "Squad tools"));
      c.appendChild(errBox(e));
      toolsHost.appendChild(c);
    }
  }

  // ---- go -------------------------------------------------------------
  function renderAll() {
    renderTeach(); renderMeasure(); renderFields(); renderSegments();
    renderFieldGroupCap();
    renderComposition(); renderTiles();
    renderMap(); renderSwarm(); renderLedger();
    renderCompare(); renderMomentum();
    renderTableShell(); renderFilters(); renderBands(); renderBody();
    mountTools();
  }
  renderAll();
  renderFoot();

  /* The deadline only decorates the momentum ledger, so it is fetched after the
     page is up and the card is redrawn if it arrives. */
  getJSON("/api/deadline").then(d => { deadline = d; renderMomentum(); })
    .catch(() => { /* the card already says no date is claimed */ });

  /* The default field is the curated elite WITHOUT the owner's own mini-league.
     A panel that publishes `selection` has already applied its own default on
     the first call, and `selection.is_default` says so — nothing to do, and no
     second round trip. Only the composition fallback needs a nudge, and it
     cannot have one: recutting is arithmetic that build's panel will not do, so
     the selector says plainly that every set is in the numbers rather than the
     page showing the whole pool under a curated label. */
  (function reconcileDefault() {
    const m = segModel();
    if (!m || !segParam) return;
    if (m.source === "panel") { renderSegments(); return; }
    const sel = selectionFor(m.key);
    if (sel && sel.size && !sameSet(sel, m.applied)) applySegments(m.key);
  })();
}
