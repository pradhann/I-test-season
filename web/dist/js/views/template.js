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
 * WHAT THE PAGE IS NOW, AND WHAT IT STOPPED BEING
 *
 *   MOVES FIRST. The header is one line naming the field, its n, its gameweek,
 *   its as-of and the deadline; the tools half ranks the moves; only then does
 *   the page describe the field. Everything explanatory sits below the fold
 *   behind a summary that states its own finding.
 *
 *   THE TEMPLATE IS DRAWN, NOT DISTRIBUTED. The beeswarm answered "what is the
 *   spread of ownership within a position", a question nobody has at a
 *   deadline. The XV pitch answers "what IS the template" by drawing it, sorted
 *   by started_by / n rather than ownership — the field's spare goalkeeper is
 *   64.9% owned and 3.8% started, and sorting by ownership gives him the shirt.
 *
 *   THREE STATES, NOT TWO. Matched, owned-but-you-bench-him, and missing. Only
 *   the last two cost anything, so only they carry a number.
 *
 *   ONE EXPOSURE EXPLANATION. The meaning of the minus sign is written once,
 *   on the exposure strip, from the selected field's own head counts; every
 *   other multiplier on the page links to it. And the headline is the quantity
 *   that wants to be SMALL — field EO you do not cover — because Σ over your
 *   15 rises when your uncovered exposure rises.
 *
 *   CAPTAINCY, NOT OWNERSHIP MOMENTUM. Ownership moves ~1pp a week and the old
 *   card spent a screen apologising for it. The armband moved 21 points off one
 *   player and onto another in the same window. Only that is drawn.
 *
 *   SEGMENTS ARE NOT A SECOND FILTER. The composer builds exactly one field, so
 *   it lives inside that field's row in the selector and nowhere else.
 */

import { runPanel, getJSON, el, emptyBox, errBox, provenance, faceImg,
         playerCard, fmtPrice, fmt1, fmt2 } from "/js/app.js";
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
  /* MOVES FIRST. The page's job is a decision, so the thing that changes the
     decision goes at the top: the header line says which field is being read,
     the tools half ranks the moves, and only then does the page describe the
     field it just ranked moves against. Everything that explains rather than
     decides sits below the fold, each behind a summary that states its own
     finding so the fold costs the reader nothing he needed. */
  const head = el("section", "card hdr");
  const toolsHost = el("div", "tools-host");
  const pitchCard = el("section", "card");
  const stripCard = el("section", "card");
  const armCard = el("section", "card");
  const mapCard = el("details", "card fold");
  const compareCard = el("details", "card fold");
  const tableCard = el("details", "card fold");
  const foot = el("div");
  host.append(head, toolsHost, pitchCard, stripCard, armCard,
              mapCard, compareCard, tableCard, foot);
  /* A fold that snaps shut every time a chip is clicked is a fold nobody can
     use, so each one remembers whether the reader opened it. */
  const foldOpen = { map: false, cmp: false, tbl: false };
  for (const [k, c] of [["map", mapCard], ["cmp", compareCard], ["tbl", tableCard]])
    c.addEventListener("toggle", () => { foldOpen[k] = c.open; });

  const drawer = el("aside", "drawer");
  document.body.appendChild(drawer);
  let chatter = null;                    // the player strip's live handle
  const closeDrawer = () => {
    drawer.classList.remove("open");
    chatter?.cancel(); chatter = null;   // a closed drawer stops rendering
  };
  addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

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

  /* The default field is the one the reader composes — the segment selection —
     then any crawl above the floor, then whatever publishes EO. A cohort under
     MIN_N is never handed out; it cannot even be picked.

     The measure is no longer a control. It FOLLOWS the field, because a field
     either publishes effective ownership or it does not, and the old second
     radio only let the two disagree. */
  let fieldKey = (allFields.find(f => f.kind === "segments" && !lowN(f))
                  || allFields.find(f => f.kind === "cohort" && !lowN(f))
                  || allFields.find(f => f.role === "field" && !lowN(f))
                  || allFields[0] || {}).key;
  let measure = (byKey[fieldKey]?.measures || []).includes("eo") ? "eo" : "own";
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
  // ---- header: one line, everything else folded under it ----------------
  /* NAMING BY WHO PRODUCED THE NUMBER. FPL publishes a share, LiveFPL models
     one, and we crawled the rest — so the selector groups by producer and a
     crawled field is named by its n, which is also the only thing that tells
     two crawls apart. "elite" survives ONLY inside LiveFPL's own product name;
     it is never this page's word for a pool it crawled itself.

     The second filter stops being a filter. The seven-segment composer builds
     exactly one field and nothing else, so it lives INSIDE that field's row as
     a `change who` disclosure rather than as a second control the reader has to
     relate to the first. Pick another field and there is no composer, because
     there is nothing it could compose. */
  const LFPL_NAME = { eo_predicted: "LFPL pred EO", eo_top10k: "LFPL 10k EO",
                      eo_elite: "LFPL elite EO" };
  const isCrawl = f => !!f && (f.kind === "cohort" || f.kind === "segments");
  function fieldName(f) {
    if (!f) return "—";
    if (isCrawl(f)) return f.n != null ? `crawl ${f.n}` : `crawl ${f.key}`;
    if (f.provider === "livefpl")
      return LFPL_NAME[f.metric] || LFPL_NAME[f.key] || `LFPL ${f.metric || f.key}`;
    if (f.kind === "fpl") return "FPL own%";
    return f.short || f.label;
  }
  const GROUPS = [
    ["FPL publishes", f => f.kind === "fpl"],
    ["LiveFPL models", f => f.provider === "livefpl"],
    ["We crawled", isCrawl],
  ];
  /* The measure follows the field rather than sitting beside it as a second
     radio: a field either publishes effective ownership or it does not, and
     asking the reader to hold both facts in his head bought nothing. */
  const measureOf = f => (f?.measures || []).includes("eo") ? "eo" : "own";
  const shortDate = iso => {
    const d = iso ? new Date(String(iso).replace(" ", "T")) : null;
    return d && !isNaN(d)
      ? d.toLocaleDateString(undefined, { day: "numeric", month: "short" })
      : "date unknown";
  };
  /* Every row prints n, gw and as-of. A crawl under the floor prints why it is
     not quotable INSTEAD of a count that could be read as one. */
  function fieldStamp(f) {
    return [f.n != null ? (lowN(f) ? `n=${f.n} — too small to quote`
                                   : `${f.n} managers`)
                        : (f.kind === "fpl" ? "share, not a count" : null),
            f.gw != null ? `GW${f.gw}` : "no gameweek stamp",
            shortDate(f.as_of)].filter(Boolean).join(" · ");
  }

  let deadline = null;               // {gw, deadline_utc} once fetched
  let selOpen = false;               // the folded selector's state, kept
  let composerOpen = false;          // across the redraws a recompute causes

  function renderHeader() {
    head.textContent = "";
    const f = byKey[fieldKey];

    const d = el("details", "fieldsel");
    d.open = selOpen;
    d.addEventListener("toggle", () => { selOpen = d.open; });
    const s = el("summary");
    const a = f ? ageInfo(f.as_of) : null;
    s.append(el("span", "tlabel", "Field"),
             el("b", "fnow", fieldName(f)),
             a ? el("span", "freshdot " + a.cls) : "",
             el("span", "hstamp", f ? fieldStamp(f) : "none measurable"));
    if (deadline?.gw != null && deadline.deadline_utc) {
      const w = new Date(deadline.deadline_utc);
      s.appendChild(el("span", "hstamp dl", !isNaN(w)
        ? `GW${deadline.gw} deadline ${w.toUTCString().slice(0, 22)} UTC`
        : `GW${deadline.gw}`));
    }
    d.appendChild(s);

    for (const [title, pred] of GROUPS) {
      const list = allFields.filter(pred);
      if (!list.length) continue;
      d.appendChild(el("div", "fgroup", title));
      for (const g of list) d.appendChild(fieldRowEl(g));
    }
    head.appendChild(d);
  }

  function fieldRowEl(g) {
    const wrap = el("div", "frow" + (g.key === fieldKey ? " on" : ""));
    const small = lowN(g);
    const b = el("button", "fpick" + (small ? " lown" : ""));
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", String(g.key === fieldKey));
    const a = ageInfo(g.as_of);
    b.append(el("span", "fmark", g.key === fieldKey ? "●" : "○"),
             el("span", "fnm", fieldName(g)),
             el("span", "freshdot " + a.cls),
             el("span", "fstamp" + (small ? " bad" : ""), fieldStamp(g)));
    b.title = `${g.label}\n% of: ${g.denominator}\n` +
      `${g.players ?? "?"} players measured · ${a.text}` +
      (small
        ? `\n\nn=${g.n}: below the ${MIN_N}-manager floor — every share is a ` +
          `multiple of ${(100 / g.n).toFixed(0)}%, so this field cannot be ` +
          `quoted and is not selectable.`
        : "") +
      (g.same_values_as_gw != null
        ? `\nValues are byte-identical to GW${g.same_values_as_gw}: the feed ` +
          `re-stamped a settled gameweek, it is not a fresh forecast.`
        : "") +
      (g.mini_league_n
        ? `\n\nIncludes ${g.mini_league_n} of your own mini-league rivals, ` +
          `whose picks correlate with yours and pull every gap towards zero.`
        : "") +
      (g.note ? `\n${g.note}` : "");
    b.setAttribute("aria-label", `${g.label}` +
      (g.n != null ? `, ${g.n} managers` : "") +
      (small ? ", too small to quote, not selectable" : "") +
      (g.key === fieldKey ? ", selected" : ""));
    b.disabled = small;
    if (!small) b.onclick = () => {
      fieldKey = g.key;
      measure = measureOf(g);
      renderAll();
    };
    wrap.appendChild(b);
    if (g.mini_league_n)
      wrap.appendChild(el("span", "cnt ml", `incl. ${g.mini_league_n} mini-league`));

    /* The composer belongs to the one field it composes. */
    const m = segModel();
    if (m && m.key === g.key) {
      const c = composerEl(m, g);
      if (c) wrap.appendChild(c);
    }
    return wrap;
  }

  /* ---- the segment composer, demoted into its own row -----------------
     Three rules it exists to keep, unchanged from when it was a filter:
       - THE DENOMINATOR COMES FROM THE PAYLOAD. The sets OVERLAP, so adding
         their counts gives a number larger than the field; the headline is
         `selection.n` with `selection.denominator` in the panel's own words.
       - THE STATE SHOWN IS THE STATE SERVED, read off `selection.segments`
         rather than remembered from the request.
       - AN UNTRUSTWORTHY SET IS FLAGGED, NEVER QUIETLY DROPPED. */
  function composerEl(m, f) {
    const universe = m.universe;
    if (!universe?.length) return null;
    const sel = selectionFor(m.key);
    const live = !!segParam;
    const served = live && sameSet(sel, m.applied);
    const isOn = c => live ? sel.has(c.tag) : m.applied.has(c.tag);

    const box = el("details", "composer");
    box.open = composerOpen;
    box.addEventListener("toggle", () => { composerOpen = box.open; });
    box.appendChild(el("summary", null, "change who"));
    const body = el("div", "cbody");
    box.appendChild(body);

    const chips = el("div", "toolbar segrow");
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
      chip.disabled = !live;
      if (live) chip.onclick = () => {
        if (sel.has(c.tag)) sel.delete(c.tag); else sel.add(c.tag);
        composerOpen = true; selOpen = true;
        renderHeader();
        applySegments(m.key);
      };
      chips.appendChild(chip);
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
        composerOpen = true; selOpen = true;
        renderHeader(); applySegments(m.key);
      };
      chips.appendChild(reset);
      const all = el("button", "chip seg ghost", "everyone");
      all.title = "Every set the crawl produced, flagged ones included.";
      all.disabled = sel.size === universe.length;
      all.onclick = () => {
        segSel[m.key] = new Set(universe.map(c => c.tag));
        composerOpen = true; selOpen = true;
        renderHeader(); applySegments(m.key);
      };
      chips.appendChild(all);
    }
    body.appendChild(chips);

    const status = el("div", "segstatus");
    if (!live) {
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
    body.appendChild(status);

    if (m.unknown.length)
      body.appendChild(el("div", "segstatus bad",
        `The panel matched no crawl source for ${m.unknown.join(", ")}, so ` +
        `${m.unknown.length === 1 ? "that set is" : "those sets are"} in ` +
        `nothing above.`));

    /* The overlap disclosure sits next to the counts, because the counts are
       exactly what invites the wrong arithmetic. */
    const inField = universe.filter(isOn);
    const sum = m.sumOfSets ?? inField.reduce((a, c) => a + (c.n || 0), 0);
    const n = m.n ?? f.n;
    if (m.overlaps || m.overlap || f.overlaps || (n != null && sum > n))
      body.appendChild(el("p", "sub",
        `The ${inField.length} sets in the field carry ${sum} memberships ` +
        `between them over ${n ?? "an unstated number of"} distinct managers` +
        (m.overlap ? ` — ${m.overlap} entries hold two tags and are counted ` +
                     `under both` : ": an entry can hold two tags and is " +
                     "counted under both") +
        `. The denominator is the distinct count, never the sum.`));

    if (m.includesYou === true)
      body.appendChild(el("div", "segnote",
        "Your own entry is inside this field. You are part of the average you " +
        "are measuring yourself against, which pulls every gap you read here " +
        "towards zero."));
    if (m.unresolved)
      body.appendChild(el("p", "sub",
        `${m.unresolved} entries in the union hold at least one pick this ` +
        `engine could not resolve to a player — a hole in the crawl, counted ` +
        `rather than hidden.`));
    if (m.note) body.appendChild(el("p", "sub", m.note));

    for (const c of universe.filter(x => tagDanger(x) && isOn(x))) {
      const w = el("div", "segnote danger");
      w.append(el("span", "chip warn", (c.label || c.tag) + " is in the field"),
               el("span", null, " " + (tagWhy(c) ||
                 "the crawl marks this set untrustworthy.")));
      body.appendChild(w);
    }
    const caveats = universe.filter(x => tagCaveat(x) && isOn(x) && tagWhy(x));
    if (caveats.length) {
      const cb = el("div", "segcaveats");
      cb.appendChild(el("span", "tlabel", "Read with"));
      for (const c of caveats) {
        const line = el("div", "cav");
        line.append(el("b", null, (c.label || c.tag) + " — "), tagWhy(c));
        cb.appendChild(line);
      }
      body.appendChild(cb);
    }
    body.appendChild(el("p", "sub glyphkey",
      "Marks on the set chips: ✓ = in the field · * = read with a caveat " +
      "(sentence above) · ! = untrustworthy, never in a default."));
    return box;
  }

  /* The old call sites (a segment recompute, and the default reconcile at the
     bottom of the file) still ask for the segment row by name; the row is now
     part of the header, so this is where that name resolves. */
  function renderSegments() { renderHeader(); }
  // ---- the field map --------------------------------------------------
  /* A fold whose summary states its own finding, so closing it costs the reader
     the picture and never the conclusion. */
  function foldHead(card, text) {
    card.textContent = "";
    const s = el("summary", null, text);
    card.appendChild(s);
    return s;
  }

  function renderMap() {
    const f = byKey[fieldKey], b = baseOf(measure);
    if (!f || !b || f.key === b.key) {
      foldHead(mapCard, "Field map: nothing to compare it against");
      mapCard.appendChild(emptyBox(
        "no two comparable fields",
        f && b && f.key === b.key
          ? `${fieldName(f)} IS the baseline under this measure, so plotting ` +
            `it against itself would draw the diagonal and nothing else. Pick ` +
            `a field to compare with the game.`
          : "The map needs a field and a same-measure baseline. Ingest the " +
            "LiveFPL ownership feed or run the manager picks crawl."));
      return;
    }
    const off = sourceRows()
      .filter(r => Math.abs(gapOf(r, fieldKey, measure) ?? 0) >= BAND).length;
    foldHead(mapCard,
      `Field map: ${off} players sit ${BAND}pp+ off the game`);
    mapCard.appendChild(el("p", "sub",
      `Every player, positioned by what the game holds (horizontal) against ` +
      `what ${f.label} holds (vertical) — same measure, same units on both ` +
      `axes. The diagonal is where the two agree; distance from it IS the ` +
      `gap, so the template, the neutral middle and the fades are places on ` +
      `the page rather than numbers to compare.`));
    const key = el("div", "zonekey");
    const zone = (cls, name, text) => {
      const z = el("div", "zone " + cls);
      z.append(el("span", "sw"), el("b", null, name), el("span", null, text));
      return z;
    };
    key.append(
      zone("heavy", "Template",
        `field is ${BAND}pp+ heavier than the game — cover it or carry the risk`),
      zone("mid", "Neutral", "field and game agree — this holding is noise"),
      zone("light", "Fade",
        `field is ${BAND}pp+ lighter — a real differential lives here`));
    mapCard.appendChild(key);

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

  // ---- the template XV, drawn ------------------------------------------
  /* "What IS the template" is a question about a SQUAD, and a squad is a shape
     every manager already reads. So the answer is drawn as one.

     SORTED BY START SHARE, NOT OWNERSHIP. Verbruggen is owned by 64.9% of the
     262 and started by 3.8% of them: he is the field's spare goalkeeper.
     Rank by ownership and he takes the shirt; rank by started_by / n and
     Kinsky takes it, correctly. The share is exact from the payload's own
     head counts, never inferred from EO.

     THREE STATES PER SHIRT, and the third is the point. You match him; you own
     him but leave him on your bench; or you do not own him at all. Only the
     last two cost anything, so only the last two carry a number — three pills
     on a pitch rather than eleven. Calvert-Lewin's −0.81 is unreadable as a
     row in a ranked list and obvious as an amber pill on a shirt you own. */
  const SHAPE  = { GKP: 2, DEF: 5, MID: 5, FWD: 3 };
  const XI_MIN = { GKP: 1, DEF: 3, MID: 2, FWD: 1 };
  const XI_MAX = { GKP: 1, DEF: 5, MID: 5, FWD: 3 };
  const POS_ROWS = ["GKP", "DEF", "MID", "FWD"];
  const CAP_FLOOR = 25;              // pp — below this the field has no armband

  const startShare = (r, key) => {
    const f = r.fields?.[key];
    return f && f.started_by != null && f.n ? 100 * f.started_by / f.n : null;
  };

  function poolFor(key) {
    const seen = new Set(), out = [];
    for (const r of (res.rows || []).concat(res.differentials || [])) {
      if (seen.has(r.code)) continue;
      seen.add(r.code);
      out.push(r);
    }
    return out;
  }

  /* Greedy under the FPL shape: the 15 highest start shares that still make a
     legal squad, then the best legal XI out of those 15. The formation is
     whatever falls out, and it is printed rather than assumed. */
  function buildXV(key) {
    const pool = [];
    for (const r of poolFor(key)) {
      const s = startShare(r, key);
      if (s == null || !SHAPE[r.pos]) continue;
      pool.push({ r, s });
    }
    pool.sort((a, b) => b.s - a.s);
    const squad = [], cnt = { GKP: 0, DEF: 0, MID: 0, FWD: 0 };
    for (const p of pool) {
      if (squad.length === 15) break;
      if (cnt[p.r.pos] >= SHAPE[p.r.pos]) continue;
      cnt[p.r.pos]++; squad.push(p);
    }
    if (squad.length < 15) return null;
    const gk = squad.find(p => p.r.pos === "GKP");
    const xi = [gk], xc = { GKP: 1, DEF: 0, MID: 0, FWD: 0 };
    for (const p of squad) {
      if (xi.length === 11) break;
      if (p === gk || p.r.pos === "GKP") continue;
      if (xc[p.r.pos] >= XI_MAX[p.r.pos]) continue;
      // never spend a slot a still-unmet minimum needs
      const need = ["DEF", "MID", "FWD"].reduce((a, k) => a +
        Math.max(0, XI_MIN[k] - (xc[k] + (k === p.r.pos ? 1 : 0))), 0);
      if (11 - xi.length - 1 < need) continue;
      xc[p.r.pos]++; xi.push(p);
    }
    const inXi = new Set(xi.map(p => p.r.code));
    const bench = squad.filter(p => !inXi.has(p.r.code))
      .sort((a, b) => (a.r.pos === "GKP" ? -1 : b.r.pos === "GKP" ? 1 : b.s - a.s));
    return { squad, xi, bench, xc, formation: `${xc.DEF}-${xc.MID}-${xc.FWD}` };
  }

  /* Your relationship to a shirt is three-valued, not two. */
  function shirtState(r) {
    if (r.in_squad !== true) return "missing";
    return myMult(r).v === 0 ? "benched" : "matched";
  }

  function renderPitch() {
    pitchCard.textContent = "";
    pitchCard.appendChild(el("h2", null, "The template XV"));
    const f = byKey[fieldKey];
    if (!f) { pitchCard.appendChild(emptyBox("no field selected")); return; }

    const xv = buildXV(fieldKey);
    if (!xv) {
      pitchCard.appendChild(emptyBox(
        `${fieldName(f)} publishes no per-manager start counts`,
        `The XV is the field's most-STARTED players, which needs started_by ` +
        `and n on every row. ${f.provider || "This provider"} serves a modelled ` +
        `share and no head counts, so a template drawn from it would be a ` +
        `guess. Pick a crawled field to see the XV.`));
      return;
    }

    const state = new Map(xv.squad.map(p => [p.r.code, shirtState(p.r)]));
    const matched = xv.squad.filter(p => state.get(p.r.code) === "matched").length;
    const benched = xv.squad.filter(p => state.get(p.r.code) === "benched").length;
    const missing = xv.squad.filter(p => state.get(p.r.code) === "missing").length;

    /* The armband, from the field's own captaincy share. */
    const capOf = p => p.r.fields?.[fieldKey]?.cap ?? null;
    const skipper = xv.xi
      .filter(p => capOf(p) != null)
      .sort((a, b) => capOf(b) - capOf(a))[0];
    const armband = skipper && capOf(skipper) >= CAP_FLOOR ? skipper : null;

    pitchCard.appendChild(el("p", "sub capline",
      `Most-started XV of ${fieldName(f)}, ${xv.formation}. ` +
      `You match ${matched}, bench ${benched}, miss ${missing}.`));

    const pitch = el("div", "pitch tmpl-xv");
    for (const pos of POS_ROWS) {
      const line = xv.xi.filter(p => p.r.pos === pos);
      if (!line.length) continue;
      const row = el("div", "row");
      for (const p of line) row.appendChild(shirt(p, armband === p));
      pitch.appendChild(row);
    }
    pitchCard.appendChild(pitch);

    const bench = el("div", "bench");
    bench.appendChild(el("span", "tlabel", "Their bench"));
    for (const p of xv.bench) bench.appendChild(shirt(p, false));
    pitchCard.appendChild(bench);

    if (!armband)
      pitchCard.appendChild(el("p", "sub",
        `No player in this XI is captained by ${CAP_FLOOR}% of the field, so ` +
        `no armband is drawn.`));

    renderShelf(xv);

    function shirt(p, isCap) {
      const st = state.get(p.r.code);
      const e = exposureOf(p.r);
      const capPct = capOf(p);
      const card = playerCard(
        { ...p.r, name: dispName(p.r) },
        { mark: isCap ? "C" : null,
          sub: `${p.s.toFixed(0)} start${isCap && capPct != null
                ? ` · ${capPct.toFixed(0)} C` : ""}` });
      card.classList.add("tst-" + st);
      if (st !== "matched" && e != null) {
        const pill = el("span", "term " + (e < 0 ? "neg" : "pos"), signed(e));
        card.appendChild(pill);
      }
      const av = availChip(p.r.status);
      if (av) card.appendChild(av);
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      const words = st === "matched"
        ? "you start him too, so he costs you nothing"
        : st === "benched"
          ? "you own him and leave him on your bench, so you carry his EO " +
            "without his points"
          : "you do not own him, so every point he scores moves the field ahead";
      card.setAttribute("aria-label",
        `${dispName(p.r)}, ${p.s.toFixed(0)}% of the field start him, ${words}` +
        (e != null ? `, exposure ${signed(e)}` : ""));
      card.title = `${dispName(p.r)}: ${p.s.toFixed(0)}% of ${f.n ?? "?"} ` +
        `start him${capPct != null ? `, ${capPct.toFixed(0)}% captain him` : ""}` +
        `\n${words}.` +
        (e != null ? `\nExposure ${signed(e)} — see the exposure strip below ` +
                     `for what that number means.` : "");
      const open = () => showDetail(p.r);
      card.onclick = open;
      card.onkeydown = ev => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(); }
      };
      return card;
    }
  }

  /* The differential shelf: high-EO players who just missed the XV. Inclusion
     is the field's top 20 by EO minus the fifteen drawn above; the order is the
     captaincy swing first, because that is the channel that actually moved
     between the two gameweeks on the wire, and EO as the tie-break. */
  function renderShelf(xv) {
    const inXv = new Set(xv.squad.map(p => p.r.code));
    const ranked = poolFor(fieldKey)
      .map(r => ({ r, eo: val(r, fieldKey, "eo") }))
      .filter(p => p.eo != null)
      .sort((a, b) => b.eo - a.eo)
      .slice(0, 20)
      .filter(p => !inXv.has(p.r.code));
    if (!ranked.length) return;
    const sw = capSwings();
    const dcap = new Map((sw?.list || []).map(x => [x.code, x.d]));
    const shelf = ranked.map(p => ({ ...p, d: dcap.get(p.r.code) ?? null }))
      .sort((a, b) => (Math.abs(b.d ?? 0) - Math.abs(a.d ?? 0)) || (b.eo - a.eo));

    const lab = el("p", "sub capline",
      `Just outside: the field's top 20 by EO, not in that XV.`);
    lab.title = "Ordered by how far the field's captaincy on each moved " +
      "between the two stored gameweeks, then by EO. Owned or not is marked " +
      "on the card, because the field arriving at a player you already hold " +
      "is good news this page could not previously deliver.";
    pitchCard.appendChild(lab);

    const row = el("div", "shelf");
    row.setAttribute("role", "list");
    for (const p of shelf) {
      const st = shirtState(p.r);
      const c = playerCard({ ...p.r, name: dispName(p.r) },
        { sub: `${p.eo.toFixed(0)} EO` });
      c.classList.add("tst-" + st);
      c.setAttribute("role", "listitem");
      c.tabIndex = 0;
      if (p.d != null && Math.abs(p.d) >= MIN_SWING)
        c.appendChild(el("span", "dcap " + (p.d < 0 ? "neg" : "pos"),
          `${signed(p.d)}pp C`));
      c.title = `${dispName(p.r)}: ${pct(p.eo)} EO in ${fieldName(byKey[fieldKey])}` +
        (p.d != null ? `, captaincy ${signed(p.d)}pp between the two stored ` +
                       `gameweeks` : "") +
        `\n${st === "missing" ? "You do not own him."
                              : "You own him already."}`;
      c.onclick = () => showDetail(p.r);
      c.onkeydown = ev => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault(); showDetail(p.r);
        }
      };
      row.appendChild(c);
    }
    pitchCard.appendChild(row);
  }

  // ---- exposure: one signed strip, and the page's only explanation ------
  /* THE DIRECTION FIX. The page used to headline "Σ (your multiplier − field
     EO) over your 15" as though a bigger number were better. It is not, and the
     identity says why: Σ field EO over ALL players is a constant (12.437 on
     today's wire), any legal squad spends exactly 12.0 multiplier units, so

         Σ over your 15  =  12.0 − (field EO you DO cover)
                         =  −0.437 + (field EO you do NOT cover)

     — it RISES when your uncovered exposure rises. Headlining it rewarded the
     reader for being more exposed. So the headline is now the thing that
     actually wants to be small: the field EO sitting on players you do not
     own. The Σ is kept, behind the fold, carrying that identity.

     THE EXPLANATION APPEARS ONCE. It is attached to the first minus sign on the
     page and repeated nowhere else; every other multiplier on the page links
     back here rather than restating it. */
  function uncoveredBasis() {
    const w = res.whatif;
    if (w && Array.isArray(w.players) && w.players.length && w.field === fieldKey)
      return {
        list: w.players.map(p => ({ eo: p.field_eo_pct, mine: p.in_squad === true })),
        exact: true,
        basis: `all ${w.players.length} current-season players`,
      };
    const seen = new Set(), list = [];
    for (const r of (res.rows || []).concat(res.differentials || [])) {
      if (seen.has(r.code)) continue;
      seen.add(r.code);
      const v = eoVal(r);
      if (v == null) continue;
      list.push({ eo: v, mine: r.in_squad === true });
    }
    return { list, exact: false,
             basis: `the ${list.length} players this panel serves` };
  }

  /* The one explanation, built from the selected field's own head counts and
     the page's own worst hole — never a hard-coded example. */
  function worstHole() {
    const holes = sourceRows()
      .filter(r => r.in_squad === false)
      .map(r => ({ r, f: r.fields?.[fieldKey], e: exposureOf(r) }))
      .filter(p => p.f && p.e != null)
      .sort((a, b) => a.e - b.e);
    /* Prefer a hole this field can explain with head counts, because the count
       IS the explanation. A modelled field publishes a share and no people, so
       it gets the same sentence without the arithmetic it cannot show. */
    return holes.find(p => p.f.n && p.f.captained_by != null) || holes[0] || null;
  }

  function renderStrip() {
    stripCard.textContent = "";
    stripCard.id = "tpl-exposure";
    const f = byKey[fieldKey];
    if (!res.squad?.readable) {
      stripCard.appendChild(el("h2", null, "Your exposure"));
      stripCard.appendChild(emptyBox(
        res.squad?.note || "your squad could not be read",
        "Run `fpl myteam auth` once, or text /setsquad with your 15. Until " +
        "then this page can describe the field but not your position in it."));
      return;
    }
    if (!hasEo()) {
      stripCard.appendChild(el("h2", null, "Your exposure"));
      stripCard.appendChild(emptyBox(
        `${fieldName(f)} publishes no effective ownership`,
        "Exposure is your multiplier minus the field's EO. This field only " +
        "reports head-count ownership, and a multiplier minus a head count " +
        "is not a number — pick a field that publishes EO."));
      return;
    }

    const b = uncoveredBasis();
    const uncovered = b.list.reduce((a, p) => a + (p.mine ? 0 : p.eo), 0) / 100;
    const covered = b.list.reduce((a, p) => a + (p.mine ? p.eo : 0), 0) / 100;

    stripCard.appendChild(el("h2", null, "Field EO you don't cover"));
    const stats = el("div", "stats");
    const t = el("div", "stat " + (uncovered > 3 ? "bad" : "good"));
    t.appendChild(el("div", "v", uncovered.toFixed(2)));
    t.appendChild(el("div", "k", "lower is safer"));
    t.title = `Σ of ${fieldName(f)}'s effective ownership over the players you ` +
      `do NOT hold, measured across ${b.basis}` +
      (b.exact ? "" : ". This field publishes no all-player list, so the " +
                      "sum is over the rows this panel served") +
      `. You cover ${covered.toFixed(2)} of the ${(uncovered + covered).toFixed(2)} ` +
      `the field carries in total.`;
    stats.appendChild(t);
    stripCard.appendChild(stats);

    /* THE minus sign, and the only place its meaning is written down. */
    const w = worstHole();
    if (w) {
      const nm = dispName(w.r), n = w.f.n;
      const benched = !!w.f.benched_by;
      const cnt = benched ? w.f.started_by : w.f.owned_by;
      const s = Math.abs(w.e).toFixed(2);
      const counted = n != null && w.f.captained_by != null && cnt != null;
      stripCard.appendChild(el("p", "sub means",
        (counted
          ? `${cnt} of the ${n} ${benched ? "start" : "own"} ${nm} and ` +
            `${w.f.captained_by} captain him, so the average rival has `
          : `${fieldName(f)} puts ${pct(w.v ?? val(w.r, fieldKey, "eo"))} ` +
            `effective ownership on ${nm}, so the average rival has `) +
        `${s} of a ${nm}. You have none, so his every point moves the field ` +
        `${s} further ahead of you. That is what −${s} means.`));
      const go = el("a", "coverlink", "cover this hole →");
      go.href = "#home";
      go.title = "Opens the Dashboard and focuses the verdict/solver card — " +
                 "the plan that says what to sell to fund him.";
      go.addEventListener("click", focusDashboardPlan);
      stripCard.appendChild(go);
    }

    /* One signed strip: every term of the identity on one axis, zero in the
       middle, sorted from what costs you most to what gains you most. The old
       two-column ledger drew the same numbers twice under two headings. */
    const scored = sourceRows()
      .map(r => ({ r, v: eoVal(r), m: myMult(r), e: exposureOf(r) }))
      .filter(p => p.e != null && Math.abs(p.e) >= 0.08)
      .sort((a, b2) => a.e - b2.e);
    if (!scored.length) {
      stripCard.appendChild(el("p", "sub",
        "No player has both a readable multiplier and an EO on this field."));
      return;
    }
    const max = Math.max(...scored.map(p => Math.abs(p.e)), 0.1);
    const strip = el("div", "xstrip");
    let assumed = 0;
    for (const p of scored) {
      const neg = p.e < 0;
      if (p.m.assumed) assumed++;
      const row = el("div", "xrow " + (neg ? "neg" : "pos"));
      row.appendChild(faceImg(p.r.code, "avatar"));
      row.appendChild(el("span", "xnm", dispName(p.r)));
      const track = el("span", "xtrack");
      const fill = el("span", "xfill " + (neg ? "neg" : "pos"));
      fill.style.width = `${Math.max(2, Math.round(50 * Math.abs(p.e) / max))}%`;
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("span", "xval " + (neg ? "neg" : "pos"), signed(p.e)));
      row.title = `${dispName(p.r)}: you ${p.m.v}×${p.m.assumed ? " (inferred)" : ""}, ` +
        `${fieldName(f)} EO ${pct(p.v)}` +
        (p.r.xpts != null ? `, ${fmt1(p.r.xpts)} xPts` +
          (res.xpts_gw != null ? ` gw${res.xpts_gw}` : "") : "") +
        `\n${neg ? "You lose" : "You gain"} ${Math.abs(p.e).toFixed(2)} for ` +
        `every point he scores.`;
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      row.setAttribute("aria-label", row.title);
      row.onclick = () => showDetail(p.r);
      row.onkeydown = ev => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault(); showDetail(p.r);
        }
      };
      strip.appendChild(row);
    }
    stripCard.appendChild(strip);

    const sum15 = scored.filter(p => p.r.in_squad === true)
      .reduce((a, p) => a + p.e, 0);
    caption(stripCard, null, [
      `Every bar is one term of the rank identity: your multiplier minus ` +
      `${f.label}'s effective ownership, always EO whichever measure the ` +
      `charts below are showing. Squad read via ${res.squad.source}` +
      (res.squad.gw != null ? ` at GW${res.squad.gw}` : "") +
      (res.squad.has_multipliers ? "."
        : ", which supplies roles but not multipliers — captain is taken as 2× " +
          "and a triple-captain chip would make it 3×.") +
      (assumed ? ` ${assumed} multipliers here are inferred from your squad ` +
                 `role rather than read.` : ""),
      `Σ over the players you hold is ${signed(sum15)}. Read it with the ` +
      `identity, not as a score: every legal squad spends exactly 12 ` +
      `multiplier units, so that sum equals 12 minus the field EO you DO ` +
      `cover, and it RISES when the field EO you do not cover rises. The ` +
      `headline above is the half of it that varies in the direction you ` +
      `actually want to watch.`,
      `Bars under 0.08 are left out: at that size the term is smaller than ` +
      `the rounding on the field's own share.`,
    ], "how exposure is computed");
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

  const SPLIT = 20;                  // pp — what counts as a real disagreement
  function renderCompare() {
    const opts = informedFields(measure);
    ensureComparePair();
    const splits = opts.length >= 2 ? sourceRows()
      .map(r => ({ a: val(r, cmpA, measure), b: val(r, cmpB, measure) }))
      .filter(p => p.a != null && p.b != null &&
                   Math.abs(p.a - p.b) >= SPLIT).length : 0;
    foldHead(compareCard, opts.length < 2
      ? "Informed fields: only one publishes this measure"
      : splits
        ? `Informed fields split over ${SPLIT}pp on ${splits} players`
        : `Informed fields agree inside ${SPLIT}pp on every player`);
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

  // ---- the armband swing -----------------------------------------------
  /* OWNERSHIP MOMENTUM IS DEAD, AND MEASURED DEAD. Across the two stored
     gameweeks the largest move in ownership anywhere in this selection is
     1.1pp — noise wearing a trend's clothes, and the old card spent a whole
     screen apologising for it. The CAPTAINCY channel moved 21 points in the
     same window, off one player and onto another, and that is the live
     question at a deadline. So this card draws cap% only, from
     momentum.series[].points[].cap_pct, and states both gameweeks and the
     population every time. */
  const MIN_SWING = 1.0;             // pp — below this it is rounding

  function capSwings() {
    const mo = res.momentum;
    if (!mo || !Array.isArray(mo.series) || !mo.series.length) return null;
    const gws = [...new Set(mo.gws || [])].map(Number)
      .filter(isFinite).sort((a, b) => a - b);
    if (gws.length < 2) return null;
    const a = gws[gws.length - 2], b = gws[gws.length - 1];
    const list = [];
    for (const sr of mo.series) {
      const pa = (sr.points || []).find(p => Number(p.gw) === a);
      const pb = (sr.points || []).find(p => Number(p.gw) === b);
      if (!pa || !pb || pa.cap_pct == null || pb.cap_pct == null) continue;
      list.push({ code: sr.code, name: sr.name || `#${sr.code}`,
                  from: pa.cap_pct, to: pb.cap_pct,
                  d: pb.cap_pct - pa.cap_pct,
                  n: pb.n_managers ?? pa.n_managers ?? null });
    }
    list.sort((x, y) => Math.abs(y.d) - Math.abs(x.d));
    return { a, b, list };
  }

  function renderArmband() {
    armCard.textContent = "";
    armCard.appendChild(el("h2", null, "The armband swing"));
    const sw = capSwings();
    if (!sw) {
      const mo = res.momentum || {};
      armCard.appendChild(emptyBox(
        "one gameweek of stored squads, so nothing has moved yet",
        (mo.reason ? mo.reason + " " : "") +
        `A swing needs two stored gameweeks of the same managers' picks; a ` +
        `flat line across one point would be a claim of stability nothing in ` +
        `the warehouse supports.` +
        (mo.next_gw != null ? ` The next point lands after GW${mo.next_gw}.` : "")));
      return;
    }
    const movers = sw.list.filter(x => Math.abs(x.d) >= MIN_SWING).slice(0, 6);
    const n = sw.list.find(x => x.n != null)?.n ?? null;
    if (!movers.length) {
      armCard.appendChild(el("p", "sub capline",
        `Between GW${sw.a} and GW${sw.b} no player's captaincy share among ` +
        `the ${n ?? "measured"} managers moved as much as ${MIN_SWING}pp.`));
      return;
    }
    const down = movers.filter(x => x.d < 0)[0];
    const up = movers.filter(x => x.d > 0)[0];
    armCard.appendChild(el("p", "sub capline",
      up && down
        ? `GW${sw.a} to GW${sw.b}: the ${n ?? "field's"} managers took the ` +
          `armband off ${down.name} (${signed(down.d)}pp) and put it on ` +
          `${up.name} (${signed(up.d)}pp).`
        : `GW${sw.a} to GW${sw.b}: the field's captaincy moved most on ` +
          `${movers[0].name} (${signed(movers[0].d)}pp of the ` +
          `${n ?? "measured"} managers).`));

    const scale = Math.max(...movers.map(x => Math.max(x.from, x.to)), 10);
    const grid = el("div", "swing");
    for (const x of movers) {
      const row = el("div", "srow");
      row.appendChild(el("span", "snm", x.name));
      const bars = el("span", "sbars");
      for (const [gw, v] of [[sw.a, x.from], [sw.b, x.to]]) {
        const b = el("span", "sbar" + (gw === sw.b ? " now" : ""));
        const fill = el("span");
        fill.style.width = `${Math.max(1, Math.round(100 * v / scale))}%`;
        b.append(el("span", "sgw", `GW${gw}`), fill,
                 el("span", "sv", `${v.toFixed(1)}%`));
        bars.appendChild(b);
      }
      row.appendChild(bars);
      row.appendChild(el("span", "sd " + (x.d < 0 ? "neg" : "pos"),
        `${signed(x.d)}pp`));
      row.title = `${x.name}: captained by ${x.from.toFixed(1)}% of the ` +
        `${n ?? "?"} at GW${sw.a} and ${x.to.toFixed(1)}% at GW${sw.b}. ` +
        `Both bars are the same population measured twice.`;
      const r = rowByCode(x.code);
      if (r) {
        row.tabIndex = 0;
        row.setAttribute("role", "button");
        row.setAttribute("aria-label", row.title);
        row.onclick = () => showDetail(r);
        row.onkeydown = ev => {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault(); showDetail(r);
          }
        };
      }
      grid.appendChild(row);
    }
    armCard.appendChild(grid);

    caption(armCard, null, [
      `Captaincy share only. Over the same window the largest move in ` +
      `OWNERSHIP anywhere in this selection is ` +
      `${(() => {
        const mo = res.momentum;
        let m = 0;
        for (const sr of mo.series || []) {
          const pa = (sr.points || []).find(p => Number(p.gw) === sw.a);
          const pb = (sr.points || []).find(p => Number(p.gw) === sw.b);
          if (pa && pb && pa.own_pct != null && pb.own_pct != null)
            m = Math.max(m, Math.abs(pb.own_pct - pa.own_pct));
        }
        return m.toFixed(1);
      })()}pp, which is why no ownership series is drawn: it would be a ` +
      `chart of rounding. ${res.momentum?.reason || ""}`,
      `Both gameweeks are the same ${n ?? "measured"} managers' stored squads, ` +
      `so the two bars in a row are one population measured twice, never two ` +
      `different fields on one axis.`,
    ], "why captaincy and not ownership");
  }

  // ---- the table ------------------------------------------------------
  const filterRow = el("div", "toolbar");
  const bandRow = el("div", "toolbar");
  const tbody = el("div");
  function renderTableShell() {
    foldHead(tableCard,
      `Every player: ${sourceRows().length} rows, sortable`);
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
      /* The meaning of that minus sign is written down ONCE, on the exposure
         strip. Every other multiplier on the page links to it rather than
         saying it again in slightly different words. */
      const line2 = el("p", "sub");
      line2.append(
        `For every point he scores you ${e >= 0 ? "gain" : "lose"} ` +
        `${Math.abs(e).toFixed(2)}${e >= 0 ? " on " : " to "}${fieldName(f)}.` +
        (m.assumed ? " (Multiplier inferred from your squad role.) " : " "));
      const q = el("a", "coverlink", "what does this mean?");
      q.href = "#template";
      q.onclick = () => {
        closeDrawer();
        document.getElementById("tpl-exposure")
          ?.scrollIntoView({ block: "center" });
      };
      line2.appendChild(q);
      drawer.appendChild(line2);
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
    /* THE ONE DISCLOSURE RAIL. Four copies of this material used to sit beside
       four different charts; nothing is deleted, it is all here, once. */
    const fd = res.field_distinction;
    if (fd?.measured_cohort && fd?.selection) {
      const mc = fd.measured_cohort, sl = fd.selection;
      const line = el("p", "sub");
      line.append(el("b", null, "One box, two populations. "),
        `The FIELD selector picks the measured cohort` +
        (mc.n != null ? ` (${mc.n} managers` +
          (mc.gw != null ? `, GW${mc.gw}` : "") + `)` : "") +
        ` behind every chart and elite column above; the composer inside it ` +
        `sets the segment selection` +
        (sl.n != null ? ` (${sl.n} managers)` : "") +
        ` behind the diff and the what-if simulator. They are different sets ` +
        `— a level from one and a trend from the other never share a sentence.`);
      if (fd.note) body.appendChild(el("p", "sub", fd.note));
      body.appendChild(line);
    }
    const mlF = byKey[fieldKey];
    const mlN = mlF?.mini_league_n ?? fd?.measured_cohort?.mini_league_n;
    if (mlN && isCrawl(mlF))
      body.appendChild(el("p", "sub",
        `The field on screen includes your ${mlN} mini-league rivals — a set ` +
        `the default selection excludes. Their picks correlate with yours, ` +
        `which pulls every gap here towards zero.`));
    body.appendChild(el("p", "sub",
      `A crawled pool under ${MIN_N} managers is not selectable at all: with ` +
      `n=4 every share is a multiple of 25% and the bars are quantization ` +
      `noise wearing full visual weight.`));
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
    renderHeader();
    mountTools();
    renderPitch(); renderStrip(); renderArmband();
    renderMap(); renderCompare();
    renderTableShell(); renderFilters(); renderBands(); renderBody();
    mapCard.open = foldOpen.map;
    compareCard.open = foldOpen.cmp;
    tableCard.open = foldOpen.tbl;
  }
  renderAll();
  renderFoot();

  /* The deadline is a stamp on the header line, so it is fetched after the page
     is up and the line is redrawn if it arrives. */
  getJSON("/api/deadline").then(d => { deadline = d; renderHeader(); })
    .catch(() => { /* the line simply carries no deadline */ });

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
