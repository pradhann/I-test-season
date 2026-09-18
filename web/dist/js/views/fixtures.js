/* Fixtures — the split ticker.

   THE GOVERNING IDEA, and it is the whole page:

     Every fixture is two fixtures: one for the attackers, one for the
     defenders, and this page never averages them.

   A single "difficulty" number is the average of two answers to two different
   questions, and the average is never the answer to either one. So every cell
   here is one rectangle divided into two bands: the upper band is what its
   attackers face, the lower band is what its defenders face, both on the same
   diverging scale in the same unit. A cell whose bands disagree is visually
   torn, and torn cells are exactly the fixtures a blended FDR erases.

   THE SECOND CLAIM, which the colour makes and must therefore say out loud:

     Colour holds your own club at league average and asks only what the
     OPPONENT does, at that venue. Two clubs facing the same opponent share a
     cell colour on purpose — this is a fixture view, not a power ranking. The
     fixture-specific number, with your own club's strength in it, is one click
     away in every cell.

   DATA PATH. Panels are the only data path. This view asks `fixture_board`,
   the split panel. (The legacy `fixture_ticker` is deleted; the board carries
   its blended number per cell as the deprecated `legacy_difficulty`.) When
   the split artefact is absent the page REFUSES to draw two bands from that
   one number: it draws a single-band cell and says, loudly, that the split is
   unavailable and why. Inventing a split would be exactly the failure this
   rebuild exists to fix.

   NOTHING IS FABRICATED. A blank gameweek is hatched and says "blank"; a
   fixture the model has no rating for is hatched and says "no fit". They are
   different answers and they look different. The legend, the unit, the domain
   and the calibration line are all payload-led — if the panel does not carry a
   measurement, the page says so rather than printing a number from a design
   document.

   The colour scale, its steps and its validator output are documented in
   fixtures.css, which owns them.
*/

import { runPanel, el, emptyBox, provenance, fmt1, fmt2, daysFromHours,
         fmtAgeDays, makeDrawer, drawerHead, sortableTh } from "/js/app.js";
import { crest } from "/js/components/clubmark.js";
import { icon, sortIcon } from "/js/components/icons.js";

/* FPL's five FDR steps, easiest -> hardest, so a colour here means what the
   same colour means on the FPL site. Defined in CSS (un-themed on purpose);
   named here only so the legend and the cells agree. Index i is FDR i+1. */
const CLASSES = ["fx-d1", "fx-d2", "fx-d3", "fx-d4", "fx-d5"];
const HORIZONS = [3, 5, 6, 8];

/* ------------------------------------------------------------------ utils */

const num = v => (typeof v === "number" && isFinite(v) ? v : null);
const sgn1 = v => (v == null ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1));
/* Every signed ease on this page is FROM THE ROW CLUB'S POINT OF VIEW, and on
   both axes positive means good for that club: its attackers meet an opponent
   that concedes more, or its defenders meet one that scores less. That is
   stated ONCE per surface, in the key above the numbers -- it was briefly
   appended to every value, which made a list of six fixtures read "harder
   harder easier harder" and buried the numbers it was meant to explain. */
/* One opponent label for the whole page. The grid marked away fixtures and the
   drawer and table did not, so the same fixture read two ways depending on
   where you looked at it. Case still carries it too; the marker is what makes
   it survive a glance. */
const oppLabel = c => (c && c.isHome === false ? "@" : "") + (c ? c.label : "");
const sgn2 = v => (v == null ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2));
/* "13th", never "#13": a bare number after a letter is how the owner read
   "A 16" as "Arsenal's attack is 16th". Every rank on this page says what is
   ranked, and reads as a position in a list. */
const ord = n => {
  if (n == null) return "–";
  const v = Math.abs(Math.round(n)), r = v % 100;
  const s = (r >= 11 && r <= 13) ? "th" : (["th", "st", "nd", "rd"][v % 10] || "th");
  return `${v}${s}`;
};
/* The ranks are keyed to an (opponent, venue) pair, and `is_home` is the ROW
   CLUB's venue, so the opponent's venue is the other one: when we host HUL,
   the ranked pair is HUL away. Three sentences read `is_home` as the
   opponent's and named the wrong half of a 40-pair population. */
const oppPair = (opponent, isHome) =>
  `${opponent} ${isHome ? "away" : "at home"}`;
/* Plain words for a rank out of twenty, so the club drawer can open with
   "elite club, tough run" before any number. Bands, not a model. */
const tierClub = r => (r == null ? null : r <= 4 ? "elite" : r <= 8 ? "strong"
  : r <= 12 ? "mid-table" : r <= 16 ? "modest" : "weak");
/* the promoted tag's meaning, printed on the tag and in the legend */
const PROMO_NOTE = "promoted this season: few Premier League matches in the fit, "
  + "so its rating leans on the league average; read its colours gently";
const tierRun = r => (r == null ? null : r <= 5 ? "easy" : r <= 10 ? "fair"
  : r <= 15 ? "tough" : "hard");
const cap = s => (s ? s[0].toUpperCase() + s.slice(1) : s);
/* A BUDGET, not an age: the length of the window a source is allowed to go
   stale in. It is spelled in days above two of them and in hours below, and
   it is the one number on this page that may still say hours, because it is
   a rule the panel serves rather than a measured age (R23, R24). */
const ruleText = h => (h >= 48 ? `${Math.round(h / 24)} day` : `${Math.round(h)} hour`);

/* Skeleton shells while the board call is in flight: a rail column and six
   cell columns as grey blocks, so the page has its shape before its numbers. */
function skeleton() {
  const box = el("div", "fx-sk");
  box.setAttribute("aria-busy", "true");
  box.setAttribute("aria-label", "loading the fixture board");
  for (let i = 0; i < 6; i++) {
    const row = el("div", "fx-sk-row");
    row.appendChild(el("span", "fx-sk-rail"));
    for (let j = 0; j < 6; j++) row.appendChild(el("span", "fx-sk-cell"));
    box.appendChild(row);
  }
  return box;
}

function parseTs(s) {
  if (!s) return null;
  const d = new Date(String(s).replace(" ", "T").replace(/\+00:00$/, "Z"));
  return isNaN(d) ? null : d;
}
function ageHours(s) {
  const d = parseTs(s);
  return d ? (Date.now() - d.getTime()) / 3.6e6 : null;
}
/* AGES ARE WHOLE DAYS (R23), through app.js's `daysFromHours` for a span the
   payload serves in hours and `fmtAgeDays` for a stamp. These two wrap them
   in the word this page puts beside them, because "today old" is not a
   sentence and "4 days old" is. The hours-and-minutes span this file used to
   print is the deadline countdown's alone (R24). */
function oldPhrase(h) {
  const word = daysFromHours(h);
  if (word === "today" || word === "yesterday") return word;
  return `${word} old`;
}
function oldStamp(iso) {
  const word = fmtAgeDays(iso);
  if (word == null) return null;
  if (word === "today" || word === "yesterday") return word;
  return `${word} old`;
}
function kickoffText(s) {
  const d = parseTs(s);
  if (!d) return null;
  /* Kickoffs are served in UTC and the topbar prints UTC, so this prints UTC
     and labels it. Rendering the browser's local zone with no suffix showed
     "Sat 07:00 AM" for a 14:00 UTC kickoff. */
  return d.toLocaleString(undefined, {
    timeZone: "UTC", weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
  }) + " UTC";
}

/* The count of players you own at a club, as a labelled pip. A bare digit
   beside a club abbreviation read as a scoreline ("MUN 2 v mci 1"), so the
   word travels with the number; the legend keys it. `title` only where the
   element has no hover card of its own, so the fact is never printed twice. */
function ownPip(held, title) {
  const pip = el("span", "fx-own", `own ${held.length}`);
  const said = `you hold ${held.length} player${held.length === 1 ? "" : "s"} `
    + `here: ${held.join(", ")}`;
  pip.setAttribute("aria-label", said);
  if (title) pip.title = said;
  return pip;
}

/* A section that has no data says WHICH data and WHY, never whitespace. */
function namedGap(title, body) {
  const d = el("div", "fx-gap");
  d.appendChild(el("b", null, title));
  if (body instanceof Node) d.appendChild(body);
  else d.appendChild(document.createTextNode(body));
  return d;
}
/* A deliberate design choice, not a hole in the data. Same anatomy as a gap so
   the reasoning reads the same way, but a solid rail instead of a dashed
   border -- a decision should not be dressed as a defect. */
function kvNote(title, body) {
  const d = namedGap(title, body);
  d.className = "fx-note";
  return d;
}
function codeSpan(t) { return el("code", null, t); }
function gapText(...parts) {
  const f = document.createDocumentFragment();
  for (const p of parts) f.appendChild(typeof p === "string" ? document.createTextNode(p) : p);
  return f;
}
/* Honest absence, compacted: one quiet line, not a 40-word apology. The
   dashed rail keeps it in the same visual family as fx-gap so "absence" still
   reads as absence — it is just no longer a paragraph. */
function quietGap(text) { return el("p", "fx-quiet", text); }

/* A FAILED FETCH is not absence, and must never wear absence's clothes.
   Solid --bad rail, the server's own reason, and a retry — visually a
   different species from the dashed honest-empty style. */
function errReason(e) {
  const msg = String((e && e.message) || e || "");
  // the structured shape: {error: true, panel, reason} serialized into the
  // thrown message by runPanel's `HTTP 500 <body>` suffix
  const m = msg.match(/\{.*\}/s);
  if (m) {
    try {
      const j = JSON.parse(m[0]);
      if (j && j.error && j.reason) return String(j.reason);
    } catch { /* not JSON; fall through to the raw message */ }
  }
  return msg;
}
function fetchFailBox(what, reason, retry) {
  const d = el("div", "fx-fetchfail");
  d.setAttribute("role", "alert");
  d.appendChild(el("b", null, `Couldn't load ${what}.`));
  d.appendChild(el("span", "why",
    "This is a fetch failure, not missing data" + (reason ? `: ${reason}` : ".")));
  if (retry) {
    const b = el("button", "chip retry", "retry");
    b.onclick = retry;
    d.appendChild(b);
  }
  return d;
}

/* ------------------------------------------------- the hover card ---------
   One floating card for the whole view, replacing native title-only tooltips
   on grid cells, strip chips and scatter marks: the same numbers, instantly,
   styled, and reachable by keyboard focus (Understat's hover panel is the
   reference). It is pointer-transparent and positioned clamped to the
   viewport. The text it shows is exactly what the title used to say. */
function makeHoverCard() {
  document.querySelectorAll(".fx-hover").forEach(n => n.remove());
  const card = el("div", "fx-hover");
  card.hidden = true;
  document.body.appendChild(card);
  let anchor = null;
  const place = (x, y) => {
    const r = card.getBoundingClientRect();
    const px = Math.min(Math.max(8, x + 14), innerWidth - r.width - 8);
    const py = y + 16 + r.height > innerHeight - 8 ? y - r.height - 10 : y + 16;
    card.style.left = `${px}px`;
    card.style.top = `${Math.max(8, py)}px`;
  };
  const show = (elm, text, x, y) => {
    anchor = elm;
    card.textContent = text;
    card.hidden = false;
    if (x != null) place(x, y);
    else {
      const r = elm.getBoundingClientRect();
      place(r.left + r.width / 2, r.bottom - 8);
    }
  };
  const hide = (elm) => { if (anchor === elm || !elm) { card.hidden = true; anchor = null; } };
  return {
    card,
    hide,
    attach(elm, textFn) {
      // aria carries the same content for screen readers; no native title, so
      // the browser's delayed tooltip never doubles the card
      elm.setAttribute("aria-label", textFn());
      elm.addEventListener("mouseenter", e => show(elm, textFn(), e.clientX, e.clientY));
      elm.addEventListener("mousemove", e => { if (anchor === elm) place(e.clientX, e.clientY); });
      elm.addEventListener("mouseleave", () => hide(elm));
      elm.addEventListener("focus", () => show(elm, textFn()));
      elm.addEventListener("blur", () => hide(elm));
    },
    destroy() { card.remove(); },
  };
}

/* Panel call that reports failure as data instead of throwing, so one absent
   script degrades one section rather than blanking the page.

   A script the registry does not know is remembered for the rest of the visit:
   the fixtures rebuild lands before its panels do, and re-probing a 404 on
   every cell click would spam the console and delay every drawer. The memo is
   per-visit, so a panel registered while the app is open is picked up on the
   next reload. */
const MISSING = new Map();   // script -> the Error from the first probe
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

/* ------------------------------------------------------------- the scale */

/* The scale is payload-led. The panel publishes its unit, its anchors and its
   domain so the legend can never drift from the numbers, and so a cell's
   colour means the same thing in GW2 as in GW32 (a min-max normalisation over
   whoever is in the league this season does not have that property).

   When the panel publishes none of that we fall back to the legacy 0–1
   `difficulty`, and we SAY that is what we are showing, including the fact
   that it is min-max normalised and therefore not comparable between seasons. */
function resolveScale(res) {
  const s = res && res.scale;
  const dom = num(s && Array.isArray(s.domain) ? Math.abs(s.domain[1]) : null)
    || num(s && s.domain_max);
  if (s && dom) {
    return {
      dom,
      unit: s.unit || "goals per match vs a league-average fixture",
      anchorAtt: num(s.anchor_attack_xg != null ? s.anchor_attack_xg : s.anchor_attack),
      anchorDef: num(s.anchor_defence_xg != null ? s.anchor_defence_xg : s.anchor_defence),
      clipped: num(s.clipped_pairs),
      population: num(s.population),
      rankConvention: s.rank_convention || null,
      payloadLed: true,
      digits: 2,
      note: null,
    };
  }
  return {
    dom: 0.5,
    unit: "fitted difficulty, 0–1",
    anchorAtt: null, anchorDef: null, clipped: null,
    population: null, rankConvention: null,
    payloadLed: false,
    digits: 2,
    note: "The panel publishes no scale block, so the ramp is anchored on the "
        + "legacy 0–1 difficulty with 0.50 as its midpoint. That number is "
        + "min–max normalised over this season's clubs, so a colour is not "
        + "comparable between seasons: it means “the worst fixture "
        + "currently available”, not a fixed quantity.",
  };
}

/* Five equal classes across [-dom, +dom], matching FPL's FDR 1-5. Positive
   ease = easier = FDR 1. The band edges are at +-3/5 and +-1/5 of the domain,
   so the middle class is the league-average fixture and gets FPL's grey. */
function bucket(ease, dom) {
  if (ease == null) return null;
  const t = Math.max(-1, Math.min(1, ease / dom));
  if (t >= 3 / 5) return 0;            // FDR 1, easiest
  if (t >= 1 / 5) return 1;            // FDR 2
  if (t > -1 / 5) return 2;            // FDR 3, league average
  if (t > -3 / 5) return 3;            // FDR 4
  return 4;                            // FDR 5, hardest
}
/* The FDR number a class carries, for the legend and every tooltip. */
const fdrOf = cls => CLASSES.indexOf(cls) + 1;
/* Robust symmetric domain for a population, by the same method the panel uses
   for its own: cover the bulk and let the tail saturate rather than squashing
   every ordinary fixture to keep two outliers on scale. Returns the domain and
   how many values it clips, because a clip count is a fact worth printing. */
function domainFor(values, floor) {
  const abs = values.filter(v => v != null).map(Math.abs).sort((a, b) => a - b);
  if (!abs.length) return { dom: floor, clipped: 0 };
  const q = abs[Math.min(abs.length - 1, Math.floor(0.95 * abs.length))];
  const dom = Math.max(floor, Math.ceil(q * 10) / 10);
  return { dom, clipped: abs.filter(v => v > dom).length };
}
const cls = (ease, dom) => { const b = bucket(ease, dom); return b == null ? null : CLASSES[b]; };

/* ------------------------------------------------ payload into view model */

/* Reads whichever shape the panel actually returned. Prefers goal rates with
   published anchors (the honest unit), then split 0–1 difficulties, then the
   legacy blended number — and records which of the three it got, because the
   page renders differently for each and must say which it is showing. */
/* fixture_board publishes the two axes nested: `opponent_only` (your club held
   at league average -- the population the colour ramp is calibrated on) and
   `fixture_specific` (your club's own strength added back). Everything below
   this line wants one flat object per opponent, so flatten exactly once, here.
   An older/other panel that already returns a flat shape falls through
   unchanged, which is why every read is `?? o.<name>`. */
function flatten(o) {
  if (!o || typeof o !== "object") return o;
  const only = o.opponent_only || {};
  const spec = o.fixture_specific || {};
  const mkt = o.market || {};
  const pick = (k) => (only[k] != null ? only[k] : o[k]);
  return {
    ...o,
    attack_ease: pick("attack_ease"), defence_ease: pick("defence_ease"),
    attack_xg: pick("attack_xg"), defence_xg: pick("defence_xg"),
    attack_pts: pick("attack_pts"), defence_pts: pick("defence_pts"),
    attack_rank: pick("attack_rank"), defence_rank: pick("defence_rank"),
    p_clean_sheet: pick("p_clean_sheet"),
    p_opponent_clean_sheet: pick("p_opponent_clean_sheet"),
    p_concede_2plus: pick("p_concede_2plus"),
    relative_attack: spec.attack_ease, relative_defence: spec.defence_ease,
    relative_attack_xg: spec.attack_xg, relative_defence_xg: spec.defence_xg,
    relative_p_clean_sheet: spec.p_clean_sheet,
    // These come from the `market` block or not at all. There is deliberately
    // no `?? o.market_age_hours` fallback: no registered panel publishes a flat
    // market field, so such a branch could only ever be dead -- and a dead
    // fallback is what lets a future rename look like a legitimate shape
    // instead of the contract break it is.
    market_state: mkt.state, market_age_hours: mkt.age_hours,
    market_as_of: mkt.as_of, market_reason: mkt.reason, n_books: mkt.n_books,
  };
}

/* fixture_detail keys its per-club blocks by team_code and wraps each in an
   {available, unavailable, ...} envelope. The drawer wants one flat array per
   section, and it must keep the panel's own reason when a section is genuinely
   empty -- a section that says WHY it is empty is information; whitespace is
   not. So: flatten, label each row with the club it belongs to, and return
   null (never []) when the panel reports the section unavailable, because null
   is what makes the drawer print the named gap. */
function flattenDetail(D) {
  if (!D) return null;
  const label = (code) => {
    for (const side of ["home", "away"]) {
      const t = D[side];
      if (t && String(t.team_code) === String(code)) return t.short_name || t.name;
    }
    return null;
  };
  /* by_team is {team_code: [row, ...]}; the club is in the key, so it has to be
     pushed onto each row before the shape is lost. */
  const fromByTeam = (block, map) => {
    if (!block || block.available === false) return null;
    const by = block.by_team || block.set_piece_duty || null;
    if (!by) return null;
    const out = [];
    for (const [code, rows] of Object.entries(by))
      for (const row of (rows || [])) out.push(map(row, label(code), block));
    return out.length ? out : null;
  };

  const news = fromByTeam(D.team_news, (n, club, b) => ({
    player: `${n.web_name || "–"}${club ? " · " + club : ""}`,
    status_text: n.news || n.status || null,
    chance: n.chance_of_playing,
    as_of: b.as_of || null,
  }));

  const xi = fromByTeam(D.predicted_lineups, (r, club) => ({
    name: `${r.web_name || "–"}${club ? " · " + club : ""}`,
    // `certainty` here is a word ("expected", "questionable", "out"), not a
    // probability -- the drawer's certainty branch would print NaN%. And it is
    // NOT the starter flag: a predicted starter can be "questionable". Keep the
    // two separate, because collapsing them turns an eleven into a thirteen.
    certainty: null,
    starts: r.predicted_start !== false,
    role: r.certainty || null,
  }));

  const sp = D.intel && D.intel.available !== false
    ? fromByTeam({ available: true, by_team: D.intel.set_piece_duty, as_of: D.intel.as_of },
        (r, club) => ({
          duty: `${String(r.duty || "duty").replace(/_/g, " ")}${club ? " · " + club : ""}`,
          player: r.order != null ? `${r.player} (${r.order})` : r.player,
        }))
    : null;

  const pm = D.previous_meetings && D.previous_meetings.available !== false
      && Array.isArray(D.previous_meetings.matches) && D.previous_meetings.matches.length
    ? D.previous_meetings.matches.map(m => ({
        season: `${m.season || "–"}${m.venue ? " · " + m.venue : ""}`,
        score: (m.goals_for != null && m.goals_against != null)
          ? `${m.goals_for}–${m.goals_against}` : null,
        xg: (m.xg_for != null && m.xg_against != null)
          ? `${fmt2(m.xg_for)}–${fmt2(m.xg_against)}` : null,
      }))
    : null;

  // `items`, which is what the panel publishes. It also accepts `rows` because
  // the first draft of this adapter guessed that name and shipped a section
  // that rendered its own "nothing here" message over live data -- the exact
  // failure this file spent a day removing.
  const tt = D.creator_team_talk || {};
  const talk = tt.available !== false ? (tt.items || tt.rows || null) : null;

  const presser = D.intel && Array.isArray(D.intel.press_conference)
      && D.intel.press_conference.length ? D.intel.press_conference : null;

  return { ...D, team_news: news, predicted_lineup: xi, set_pieces: sp,
           previous_meetings: pm, creator_talk: talk, press_conference: presser,
           style: buildStyle(D) };
}

/* The panel returns form per club, not a joint "style" object. The drawer's
   style section is honest about what a warehouse without event data can say:
   goal rates and their residual against xG. Build exactly that and nothing
   more -- no PPDA, no field tilt, no invented tempo. */
function buildStyle(D) {
  const f = D && D.form;
  if (!f) return null;
  const out = {};
  for (const side of ["home", "away"]) {
    const t = D[side], v = f[side];
    if (!t || !v || typeof v !== "object") continue;
    const nm = t.short_name || side;
    const bits = [];
    // `unavailable` here withholds the RESIDUAL, not the rates -- the per-game
    // figures are still served and still true. Dropping them on the presence of
    // that string would discard real data to honour a caveat about a different
    // number, so read both.
    if (v.xg_for_pg != null) bits.push(`${fmt2(v.xg_for_pg)} xGF`);
    if (v.xg_against_pg != null) bits.push(`${fmt2(v.xg_against_pg)} xGA`);
    if (v.xg_for_resid != null) bits.push(`${sgn2(v.xg_for_resid)} vs xG`);
    if (v.window_matches != null)
      bits.push(`${v.window_matches} match${v.window_matches === 1 ? "" : "es"}`);
    if (bits.length) out[nm] = bits.join("  ·  ");
    else if (v.unavailable) out[nm] = String(v.unavailable);
  }
  return Object.keys(out).length ? out : null;
}

function readOpponent(raw, scale) {
  const o = flatten(raw);
  const attXg = num(o.attack_xg), defXg = num(o.defence_xg);
  let easeAtt = null, easeDef = null, basis = null;

  const pubAtt = num(o.attack_ease), pubDef = num(o.defence_ease);
  if (pubAtt != null && pubDef != null) {
    // The panel already did this subtraction against its own published
    // anchors. Prefer its arithmetic over ours -- recomputing here would let
    // the two drift apart silently.
    easeAtt = pubAtt; easeDef = pubDef; basis = "goals";
  } else if (attXg != null && defXg != null && scale.anchorAtt != null && scale.anchorDef != null) {
    // mu_O high  => a league-average attack takes more off them => easier
    // lambda_O high => they score more against a league-average defence => harder
    easeAtt = attXg - scale.anchorAtt;
    easeDef = scale.anchorDef - defXg;
    basis = "goals";
  } else {
    const ad = num(o.attack_difficulty), dd = num(o.defence_difficulty);
    if (ad != null && dd != null) {
      easeAtt = (0.5 - ad) * 2 * scale.dom;
      easeDef = (0.5 - dd) * 2 * scale.dom;
      basis = "split01";
    }
  }
  // fixture_board serves the legacy blend per cell as `legacy_difficulty`
  // (deprecated in its schema); the deleted ticker's flat `difficulty`
  // field no longer exists on any registered panel.
  const blended = num(o.legacy_difficulty);
  return {
    opponent: o.opponent, oppCode: num(o.opponent_code),
    isHome: !!o.is_home, kickoff: o.kickoff_utc || null,
    label: o.label || (o.is_home ? String(o.opponent || "").toUpperCase()
                                 : String(o.opponent || "").toLowerCase()),
    fixtureId: num(o.fixture_id),
    easeAtt, easeDef, basis,
    blended,
    easeBlend: blended == null ? null : (0.5 - blended) * 2 * scale.dom,
    rankAtt: num(o.attack_rank), rankDef: num(o.defence_rank),
    marketWeight: num(o.market_weight),
    marketAgeH: num(o.market_age_hours),
    nBooks: num(o.n_books),
    marketResidual: num(o.market_residual),
    priorShare: num(o.rating_prior_share),
    /* The same two eases WITH this club's own attack and defence in them.
       Both bases travel on every cell so the grid can switch without refetching
       and without either number being derived from the other. */
    easeAttClub: num(o.relative_attack),
    easeDefClub: num(o.relative_defence),
    pCleanSheet: num(o.p_clean_sheet),
    /* The clean-sheet number WITH this club's own defence in it. The grid's
       colour deliberately holds the club at league average, which is right for
       comparing fixtures and wrong for "who keeps a clean sheet": Arsenal away
       at Sunderland is 30% for an average defence and 49% for Arsenal's. A
       clean sheet is a fact about a specific defence, so this lens uses the
       fixture-specific figure and says so. */
    pCleanSheetAdj: num(o.relative_p_clean_sheet),
    pCleanSheetMkt: num(o.p_clean_sheet_market),
    marketState: o.market_state || null,
    raw: o,
  };
}

function buildModel(res) {
  const scale = resolveScale(res);
  const gws = (res.gws || []).slice();
  const teams = [];
  let anySplit = false, anyBlend = false;

  for (const t of res.teams || []) {
    const byGw = new Map();
    for (const f of t.fixtures || []) {
      const opps = (f.opponents || []).map(o => readOpponent(o, scale));
      for (const c of opps) {
        if (c.basis) anySplit = true;
        else if (c.blended != null) anyBlend = true;
      }
      byGw.set(f.gw, { gw: f.gw, blank: !!f.blank || !opps.length, double: opps.length > 1, opps });
    }
    for (const g of gws) if (!byGw.has(g)) byGw.set(g, { gw: g, blank: true, double: false, opps: [] });

    const h = t.horizon || {};
    const all = gws.flatMap(g => byGw.get(g).opps);
    const attVals = all.map(c => c.easeAtt).filter(v => v != null);
    const defVals = all.map(c => c.easeDef).filter(v => v != null);
    const blendVals = all.map(c => c.easeBlend).filter(v => v != null);
    const csVals = all.map(c => c.pCleanSheetAdj).filter(v => v != null);
    const attClubVals = all.map(c => c.easeAttClub).filter(v => v != null);
    const defClubVals = all.map(c => c.easeDefClub).filter(v => v != null);
    const sum = a => (a.length ? a.reduce((x, y) => x + y, 0) : null);
    const mean = a => (a.length ? sum(a) / a.length : null);

    teams.push({
      code: num(t.team_code), short: t.short_name, name: t.name || t.short_name,
      byGw,
      nFixtures: all.length,
      nBlanks: gws.filter(g => byGw.get(g).blank).length,
      nDoubles: gws.filter(g => byGw.get(g).double).length,
      attSum: num(h.attack_ease_sum) != null ? num(h.attack_ease_sum)
        : (num(h.attack_xg_sum) != null && scale.anchorAtt != null
            ? h.attack_xg_sum - scale.anchorAtt * all.length : sum(attVals)),
      defSum: num(h.defence_ease_sum) != null ? num(h.defence_ease_sum) : sum(defVals),
      attMean: mean(attVals), defMean: mean(defVals), blendMean: mean(blendVals),
      /* Expected clean sheets over the window: the SUM of per-fixture
         probabilities, which is the expected COUNT (linearity of expectation,
         no independence assumption needed). A blank gameweek contributes
         nothing, which is correct -- you cannot keep a clean sheet in a
         fixture you do not have. n is carried so the mean can be shown too. */
      csSum: csVals.length ? sum(csVals) : null,
      csMean: mean(csVals),
      csN: csVals.length,
      /* Club-basis sums. The panel serves horizon ranks for the opponent-only
         basis only, so the club-basis ranks are computed here from these sums
         and the page says so rather than passing them off as served. */
      attSumClub: attClubVals.length ? sum(attClubVals) : null,
      defSumClub: defClubVals.length ? sum(defClubVals) : null,
      attRankH: num(h.attack_rank), defRankH: num(h.defence_rank),
      rankGap: num(h.rank_gap),
      rating: t.rating || null,
      form: t.form || null,
      priorShare: num((t.rating || {}).prior_share) ?? num(t.rating_prior_share),
    });
  }

  /* Tornness has exactly one owner: the served `divergent[]` list. The panel
     applied its rank-gap rule once, over the same population it ranked, and
     every torn mark on the page: the cell seam, the rail's tag, the torn card,
     the verdict tear row — joins that list by (fixture_id, team_code). The
     client-side bucket-gap rule that used to live here was a second
     definition of "torn" on the same screen, disagreeing quietly with the
     served one; it is deleted, not reconciled. */
  const gwSet = new Set(gws);
  const divergent = (res.divergent || []).filter(d => gwSet.has(d.gw));
  const tornMap = new Map();
  for (const d of divergent) tornMap.set(`${d.fixture_id}|${d.team_code}`, d);
  for (const t of teams)
    t.tornRows = divergent.filter(d => num(d.team_code) === t.code);

  /* Club-basis horizon ranks: the panel serves ranks for the opponent-only
     basis only, so these are computed from the club sums above. 1 = easiest. */
  for (const key of ["attSumClub", "defSumClub"]) {
    const rankKey = key === "attSumClub" ? "attRankClub" : "defRankClub";
    const ordered = teams.filter(t => t[key] != null)
      .sort((a, b) => b[key] - a[key]);
    ordered.forEach((t, i) => { t[rankKey] = i + 1; });
  }

  /* The club basis is a WIDER distribution than the opponent-only one -- the
     panel's own domain_note says so -- so it gets a domain calibrated on its
     own population instead of borrowing one that would saturate every strong
     and weak club alike. Same method the panel uses, applied here and
     labelled as computed here. */
  const clubVals = teams.flatMap(t => gws.flatMap(g => (t.byGw.get(g).opps || [])
    .flatMap(c => [c.easeAttClub, c.easeDefClub])));
  const club = domainFor(clubVals, scale.dom);
  const anyClub = clubVals.some(v => v != null);
  return { scale, gws, teams, anySplit, anyBlend, divergent, tornMap, res,
           clubDom: club.dom, clubClipped: club.clipped, anyClub };
}

/* ----------------------------------------------------- the form chip ---
   One component, two honest states, keyed on the served `form` block.

   n < 3: a count badge and nothing else. One match is not form, and hollow
   or faded marks positioned by one match of noise are still marks — the
   board refuses to draw them and says why on hover. The served per-game
   rates live in the tooltip and the drawers, where they read as rates.

   n >= 3: two outlined pills carrying the RESIDUALS against the club's own
   fitted rating, which is form and not a rating, with xg_against flipped so
   positive is always good. They say "form" on the pill: labelled ATT/DEF they
   sat two lines above the club's ATTACK/DEFENCE rating ranks, two different
   quantities under one pair of names, and the best-rated defence in the
   league read "DEF -1.3". Pill tint uses the page's diverging vocabulary at
   13%, but the text wears --ink and the shape is a bordered pill, so it can
   never be misread as a fixture cell and no ramp colour is ever text ink.

   Split into a pure spec function + a DOM builder so the pill state can be
   unit-checked without a browser: today's payloads only exercise n<3, and
   the other state must not wait for October to be verified. */
function formChipSpec(form) {
  if (!form || typeof form !== "object") return null;
  const n = num(form.window_matches);
  const attR = num(form.xg_for_resid), defR = num(form.xg_against_resid);
  if (n != null && n >= 3 && attR != null && defR != null) {
    const flip = -defR;                 // conceding less than fitted = good
    return {
      state: "resid",
      pills: [
        /* "att form +0.5" never said what the 0.5 was measured against, so it
           read as a rating rather than a gap. The label now carries the
           comparison; the card carries the arithmetic. */
        { cls: attR >= 0 ? "up" : "dn", text: `att ${sgn1(attR)}`,
          full: `att ${sgn1(attR)} vs rating`,
          title: `Scoring ${sgn2(attR)} goals a game more than its own fitted `
            + `rating predicts, over the last ${n} `
            + `match${n === 1 ? "" : "es"}. This is a CHECK on the rating, not `
            + "an input to it: it says the colour may be behind the football. "
            + "It never changes the colour." },
        { cls: flip >= 0 ? "up" : "dn", text: `def ${sgn1(flip)}`,
          full: `def ${sgn1(flip)} vs rating`,
          title: `Conceding ${sgn2(-flip)} goals a game versus its own fitted `
            + `rating over the last ${n} match${n === 1 ? "" : "es"}, shown `
            + "flipped so positive is always good for you. A check on the "
            + "rating, never an input to it." },
      ],
    };
  }
  const bits = [];
  if (num(form.xg_for_pg) != null) bits.push(`xGF ${fmt2(form.xg_for_pg)}`);
  if (num(form.xg_against_pg) != null) bits.push(`xGA ${fmt2(form.xg_against_pg)}`);
  return {
    state: "smalln",
    text: `n=${n == null ? "?" : n}`,
    title: [
      form.unavailable ? String(form.unavailable) : null,
      bits.length ? `Per game: ${bits.join(" · ")}.` : null,
    ].filter(Boolean).join(" "),
  };
}
function formChipEl(form, terse) {
  const spec = formChipSpec(form);
  if (!spec) return null;               // legacy payload: the chip is absent
  if (spec.state === "smalln") {
    const b = el("span", "formchip smalln", spec.text);
    b.title = spec.title;
    return b;
  }
  const box = el("span", "formchip resid");
  for (const ps of spec.pills) {
    /* The rail is 206px and two "att -0.1 vs rating" pills are 240px, so they
       used to run off it and over the first fixture column. "vs rating" is the
       notation, and notation is explained once (the legend does it), not
       repeated on forty pills. The drawers, which have the room, keep it. */
    const pill = el("b", "pill " + ps.cls, terse ? ps.text : (ps.full || ps.text));
    pill.title = ps.title;
    box.appendChild(pill);
  }
  return box;
}

/* ------------------------------------------------------------------ view */

export default async function fixtures(host) {
  /* THE ONE DRAWER (R20). app.js's `makeDrawer` owns the aside, the width,
     the focus trap, Escape, the click outside and the teardown on leaving
     the view; this file supplies only the selection it has to clear when the
     drawer closes. The hand-rolled aside this file built was one of the four
     the audit counted, at one of the three widths.

     The hover card is this view's own and still has to be destroyed when the
     view is left, so it keeps a hashchange listener of its own. */
  const dw = makeDrawer("fixtures", "fixture detail");
  const drawer = dw.drawer;
  drawer.classList.add("fx-drawer");
  const closeDrawer = () => dw.close();
  /* Every open re-arms the one thing this view has to undo on close: the
     rail input the drawer was opened from stays selected while it is open. */
  const openDrawer = () => {
    dw.setHandles([{ cancel: () => clearInputSel() }]);
    dw.open();
  };
  const hover = makeHoverCard();
  const onHash = () => {
    if ((location.hash || "").slice(1).split("?")[0] === "fixtures") return;
    hover.destroy();
    removeEventListener("hashchange", onHash);
  };
  addEventListener("hashchange", onHash);

  const card = el("section", "card");
  card.appendChild(el("h2", null, "Fixture board"));
  /* ORDER. This is a tool, and the grid is the tool. Everything that explains
     the grid now sits under it: a reader who needs the method can reach it in
     one click, and a reader who came to look at fixtures sees fixtures. The
     old order spent about half the first screen on prose and provenance. */

  /* --- the two load-bearing sentences, in the slot xPoints uses for its --- */
  const s1 = el("p", "fx-claim");
  s1.appendChild(el("b", null, "Every fixture is two fixtures"));
  s1.appendChild(document.createTextNode(
    ": one for the attackers, one for the defenders. This page never "
    + "averages them. The upper band of a cell is what that club's attackers "
    + "face; the lower band is what its defenders face."));

  const calibEl = el("div", "fx-calib");
  calibEl.hidden = true;                 // shown only once it has something to say
  // appended below the grid, with the rest of the method

  /* The sentence above is the page's method, and the method is worth one
     click, not six lines above every visit. The league-average anchor had a
     second, hardcoded paragraph here; the panel's own note says the same
     thing and is printed directly below, so the copy is gone and the served
     note is the one place it lives. */
  const howBox = el("details", "fx-how");
  const howSum = el("summary", null, "How to read this grid");
  howBox.append(howSum, s1);
  const noteBox = el("div", "fx-hownotes");
  howBox.appendChild(noteBox);

  const freshRow = el("div", "fx-fresh");
  const horizonRow = el("div", "toolbar fx-tb");
  const lensRow = el("div", "toolbar fx-tb");
  const controls = el("div", "fx-controls");
  controls.append(horizonRow, lensRow);
  const verdictEl = el("div", "fx-verdict");
  verdictEl.hidden = true;
  const stripEl = el("div", "fx-strip-wrap");
  stripEl.hidden = true;
  const body = el("div");
  const foot = el("div");
  /* Controls, the answer in one line, then the grid. Method, kickoffs and
     provenance follow it. */
  const methodBox = el("div", "fx-method");
  methodBox.append(calibEl, howBox, freshRow);
  card.append(controls, verdictEl, body, stripEl, methodBox, foot);
  host.appendChild(card);

  /* The two appendix cards fold, in the house idiom: a summary that states
     the finding, so closing them costs the reader the picture and never the
     conclusion. Open state survives the re-render the squad fetch causes. */
  const tornCard = el("details", "card fx-fold");
  const shapeCard = el("details", "card fx-fold");
  const foldOpen = { torn: false, shape: false };
  tornCard.addEventListener("toggle", () => { foldOpen.torn = tornCard.open; });
  shapeCard.addEventListener("toggle", () => { foldOpen.shape = shapeCard.open; });
  host.append(tornCard, shapeCard);

  // ---- state ----
  let selInput = null;            // name of the selected inputs[] row, or null
  let freshRes = null;            // the payload renderFreshness last drew
  let horizon = 6;
  let fromGw = null;              // null = the panel's own default (next GW)
  let lens = "both";              // both | attack | defence; and the SORT
  let tableView = false;          // Table shows the last grid state's order
  let azSort = false;             // the look-one-club-up escape hatch
  let tsort = null;               // table view's own sort: {key, dir} or null
  /* Secondary numbers on every row head: the two form residuals and the
     club-strength ranks. Five figures per club across 20 clubs is 100 on
     the scan path before a single fixture cell, and none of them answer
     "who has good fixtures" -- form explicitly never moves the colour.
     Off by default; the row already opens a drawer that carries both, so
     nothing here is hidden, only moved off the glance. */
  let showDetail = false;
  /* Which of the two rank columns orders the rows. null follows the lens,
     which is what the lens buttons have always done; clicking a column
     header sets it explicitly and shows an arrow, because "the lens is
     the sort" is only obvious to whoever wrote it. */
  let rowSort = null;             // null | "att" | "def"
  /* WHICH QUESTION THE GRID IS ANSWERING.
       "opponent" - your club held at league average, so the cell is a property
                    of the opponent at that venue and two clubs facing the same
                    opponent share it. Comparable across clubs and windows,
                    which is what a fixture ticker is for.
       "club"     - your club's own attack and defence added back, so the cell
                    is this club in this fixture. Not comparable across clubs,
                    and on a wider scale (see clubDomain).
     Both are served per cell; neither is derived from the other. */
  let basis = "opponent";
  /* Every grid render goes through these three, so switching basis can never
     leave one surface on the other question. The drawers deliberately do NOT
     use them: they show both bases at once, side by side, which is the whole
     point of a drawer. */
  const eAtt = c => (basis === "club" && c && c.easeAttClub != null
    ? c.easeAttClub : (c ? c.easeAtt : null));
  const eDef = c => (basis === "club" && c && c.easeDefClub != null
    ? c.easeDefClub : (c ? c.easeDef : null));
  const curDom = () => (basis === "club" && M && M.clubDom ? M.clubDom
    : (M ? M.scale.dom : 0.6));
  const tAtt = t => (basis === "club" ? t.attSumClub : t.attSum);
  const tDef = t => (basis === "club" ? t.defSumClub : t.defSum);
  const tAttRank = t => (basis === "club" ? t.attRankClub : t.attRankH);
  const tDefRank = t => (basis === "club" ? t.defRankClub : t.defRankH);
  let M = null, prov = null, scriptUsed = null, boardErr = null;

  /* ------------------------------------------------- my squad, once ----
     One squad_overview fetch per visit. The map is a JOIN of two served
     facts — who you hold (squad_overview) and the board's clubs — never a
     computation: the pips, the dim toggle and the fixture-turn sentence all
     read it. When the squad cannot be read the controls say so and the board
     renders exactly as before (squad-awareness is an overlay, not a gate). */
  let SQ = null;                  // {byClub: Map(code -> [names])} | {failed}
  let myClubsOnly = false;
  (async () => {
    const r = await tryPanel("squad_overview", {});
    if (r.ok && r.result && !r.result.empty) {
      const byClub = new Map();
      for (const p of [...(r.result.starters || []), ...(r.result.bench || [])]) {
        const code = num(p.team_code);
        if (code == null) continue;
        if (!byClub.has(code)) byClub.set(code, []);
        byClub.get(code).push(p.name || "–");
      }
      SQ = { byClub };
    } else {
      SQ = { failed: true, reason: r.ok ? (r.result && r.result.reason) : errReason(r.error) };
    }
    if (M) renderAll();           // overlay the marks once both facts exist
  })();
  const ownedNames = code => (SQ && SQ.byClub && SQ.byClub.get(code)) || null;

  /* --------------------------------------------------------- data fetch */
  async function load() {
    body.textContent = "";
    body.appendChild(skeleton());
    const params = { horizon };
    if (fromGw != null) params.from_gw = fromGw;

    let r = await tryPanel("fixture_board", params);
    boardErr = null;
    if (!r.ok && fromGw != null) {             // the window shift was refused
      fromGw = null;
      r = await tryPanel("fixture_board", { horizon });
    }
    if (!r.ok) {
      boardErr = r;
      body.textContent = "";
      renderCalibration();
      body.appendChild(fetchFailBox("the fixture board", errReason(r.error), load));
      body.appendChild(el("p", "sub",
        "The split panel refused this request, so there is nothing to draw. "
        + "(The legacy blended ticker is deleted; fixture_board carries its "
        + "number as legacy_difficulty, so there is no second panel to ask.) "
        + "The page shows the failure rather than an empty grid, because an "
        + "empty grid would read as “no fixtures”."));
      verdictEl.hidden = stripEl.hidden = tornCard.hidden = shapeCard.hidden = true;
      return;
    }
    scriptUsed = r.script; prov = r.prov;
    const res = r.result;
    foot.textContent = "";
    foot.appendChild(provenance(prov));
    if (res.empty) {
      body.textContent = "";
      renderCalibration();               // a claim about the page, not about the data
      renderFreshness(res);
      body.appendChild(emptyBox(res.reason,
        "The ticker reads the fixture list and the fitted-rating artefact. "
        + "Neither is modelled in the browser: when the warehouse has no "
        + "fixtures for this window there is nothing to draw and nothing to "
        + "infer."));
      verdictEl.hidden = stripEl.hidden = tornCard.hidden = shapeCard.hidden = true;
      return;
    }
    M = buildModel(res);
    renderAll();
  }

  function renderAll() {
    renderCalibration();
    renderFreshness(M.res);
    renderHorizon();
    renderLens();
    renderNotes();
    renderVerdict();
    renderStrip();
    renderBody();
    renderTorn();
    renderShape();
  }

  /* ------------------------------------------------------ calibration ---
     "Fixture swing is worth 2–3 points per asset over six gameweeks and team
     quality about four times that" is the sentence that stops this page being
     over-trusted. It is also a MEASUREMENT, so it is printed only when the
     panel serves one. A number from a design document is not a measurement. */
  function renderCalibration() {
    calibEl.textContent = "";
    const c = (M && M.res.calibration) || null;
    const model = (c && c.model) || null;
    const emp = (c && c.empirical) || null;

    /* Two estimates of the same quantity, and they do not agree: the model has
       no estimation noise in it, and the empirical max-minus-min over twenty
       fitted effects is biased upward by sampling noise. So one is a floor and
       the other a ceiling. Printing the midpoint would invent a precision
       neither has; the bracket is the honest object.

       ONE BRACKET PER SIDE. The single bracket took its floor as a minimum
       across the two model figures and its ceiling from the pooled outfield
       AVERAGE, so it printed 2.8-5.4 while the dot plot one click down showed
       DEF 6.0 and FWD 6.4, both outside it. An attacker and a defender are
       two different questions on this page everywhere else; the calibration
       line is no exception. */
    const gws = (model && num(model.horizon_gws)) || (M.gws.length || null);
    const byPos = (emp && Array.isArray(emp.by_position)) ? emp.by_position : [];
    const measured = names => {
      const vs = byPos.filter(r => names.includes(String(r.position || "")))
        .map(r => num(r.fixture_pts_6gw)).filter(v => v != null);
      return vs.length ? Math.max(...vs)
        : (emp ? num(emp.outfield_fixture_pts_6gw) : null);
    };
    const band = (floor, ceiling) => {
      const vs = [floor, ceiling].filter(v => v != null);
      if (!vs.length) return null;
      return `${fmt1(Math.min(...vs))}–${fmt1(Math.max(...vs))} pts`;
    };
    const attBand = model ? band(num(model.fixture_swing_attack_pts),
                                 measured(["MID", "FWD"])) : null;
    const defBand = model ? band(num(model.fixture_swing_defence_pts),
                                 measured(["GKP", "DEF"])) : null;
    /* every ratio the disclosure shows, so the printed bracket contains the
       numbers a click can find rather than a subset of them */
    const ratios = [model && num(model.ratio_attack), model && num(model.ratio_defence),
                    emp && num(emp.outfield_ratio),
                    ...byPos.map(r => num(r.ratio))].filter(v => v != null);

    if (emp && attBand && defBand && ratios.length) {
      /* ONE always-on line — the essay lives behind the disclosure. On a
         Friday the reader needs the verdict ("tie-breaker"), not the method. */
      calibEl.hidden = false;
      const line = el("p", "fx-cal-line");
      line.appendChild(document.createTextNode("Fixtures are "));
      line.appendChild(el("b", null, "tie-breakers"));
      line.appendChild(document.createTextNode(
        `: over ${gws} GWs the best-minus-worst schedule is worth `));
      line.appendChild(el("b", "fx-cal-hi", attBand));
      line.appendChild(document.createTextNode(" to an attacker and "));
      line.appendChild(el("b", "fx-cal-hi", defBand));
      line.appendChild(document.createTextNode(" to a defender; which club you own is worth "));
      line.appendChild(el("b", "fx-cal-hi",
        `${fmt1(Math.min(...ratios))}–${fmt1(Math.max(...ratios))}×`));
      line.appendChild(document.createTextNode(" that."));
      calibEl.appendChild(line);

      const disc = el("details", "fx-how fx-caldisc");
      disc.appendChild(el("summary", null, "How this was measured"));
      const why = el("p", "sub");
      why.appendChild(document.createTextNode(
        `Each low end is the model's own figure for that side, which carries no `
        + `estimation noise and is therefore a floor. Each high end is the `
        + `largest measured position effect on that side (attackers: MID and `
        + `FWD; defenders: GKP and DEF), measured on `));
      why.appendChild(el("b", null,
        emp.by_position
          ? `${emp.by_position.reduce((a, r) => a + (num(r.n_starts) || 0), 0).toLocaleString()} starts`
          : "realised starts"));
      why.appendChild(document.createTextNode(
        `${emp.seasons ? " across " + String(emp.seasons).split(",").length + " seasons" : ""}, `
        + `where taking best-minus-worst across twenty estimated effects is biased `
        + `upward by sampling noise and is therefore a ceiling. The truth is inside. `
        + `Break ties with this page; do not pick assets with it.`));
      disc.appendChild(why);

      /* MEASURED, BY POSITION as a tiny dot plot on one shared axis, its
         horizon restated — four numbers that were begging to be positions. */
      if (Array.isArray(emp.by_position) && emp.by_position.length) {
        const hGws = num(emp.horizon_gws) || 6;
        disc.appendChild(el("span", "fx-cal-poslab",
          `measured fixture effect by position; pts over ${hGws} GWs`));
        const dmax = Math.max(...emp.by_position.map(r => num(r.fixture_pts_6gw) || 0), 0.01);
        const plot = el("div", "fx-dotplot");
        for (const r of emp.by_position) {
          const v = num(r.fixture_pts_6gw);
          const row = el("div", "dp-row");
          row.appendChild(el("span", "dp-k", String(r.position || "–")));
          const track = el("span", "dp-track");
          if (v != null) {
            const dot = el("i", "dp-dot");
            dot.style.left = `${(v / dmax * 100).toFixed(1)}%`;
            track.appendChild(dot);
          }
          row.appendChild(track);
          row.appendChild(el("span", "dp-v", v == null ? "–" : `${fmt1(v)} pts`));
          row.title = `${(num(r.n_starts) || 0).toLocaleString()} starts; `
            + `team quality ${fmt1(num(r.team_pts_6gw))} pts over the same ${hGws} GWs, `
            + `${fmt1(num(r.ratio))}x the fixture effect`;
          plot.appendChild(row);
        }
        disc.appendChild(plot);
      }
      calibEl.appendChild(disc);
      return;
    }

    if (c && c.headline) {
      calibEl.hidden = false;
      calibEl.appendChild(document.createTextNode(String(c.headline)));
      return;
    }

    calibEl.hidden = false;
    calibEl.appendChild(el("b", null, "No calibration served. "));
    calibEl.appendChild(document.createTextNode(
      "The design for this page states that fixture swing is worth a couple of "
      + "points per asset over six gameweeks against roughly four times that "
      + "from team quality; which would make this a tie-breaker and not an "
      + "asset-picker. This panel does not yet return a measured calibration, "
      + "so the page prints none: a figure copied out of a design document is "
      + "not a measurement. Treat the ranking below as a tie-breaker until the "
      + "panel measures the swing."));
  }

  /* ------------------------------------------------------- freshness ---
     Age is above the numbers, not below them: a reader should know how old a
     claim is before reading it. `provenance()` prints generated_at, which is
     when the PANEL ran and is therefore always "seconds ago" — worthless as a
     freshness signal. Everything here is the age of an INPUT. */
  /* What each input IS, in a manager's words. The served `name` is a table
     name; a reader must be able to tell "this colour is built on a fit from
     ten days ago" without opening anything. */
  const INPUT_WHAT = {
    ratings: "team-strength fit behind every colour",
    schedule: "fixtures and dates",
    market: "bookmaker prices; never in a colour",
    form: "xG since the fit; a check, never a colour",
  };
  function renderFreshness(res) {
    freshRes = res;
    freshRow.textContent = "";
    const inputs = Array.isArray(res.inputs) ? res.inputs : null;

    if (inputs && inputs.length) {
      const head = el("div", "fx-fresh-head");
      head.appendChild(el("span", "tlabel", "Inputs"));
      freshRow.appendChild(head);
      for (const i of inputs) {
        const key = inputKey(i.name);
        const h = num(i.age_hours) ?? ageHours(i.as_of);
        const thr = num(i.stale_after_hours);
        const served = String(i.state || "");
        /* Age is judged against the row's OWN rule, never against the served
           "fresh" word: team form is served fresh at 153h because it has no
           rule, and the honest thing to print there is "no staleness rule". */
        const broken = served === "failed" || served === "missing" || h == null;
        const over = thr != null && h != null && h > thr;
        const tone = broken ? "bad" : over ? "warn" : thr == null ? "none" : "good";
        const on = selInput === i.name;
        const row = el("button", "fx-inrow"
          + (tone === "bad" ? " stale" : tone === "warn" ? " warn" : "")
          + (on ? " on" : ""));
        row.appendChild(el("span", "freshdot " + tone));
        row.appendChild(el("b", null, String(i.name || "input")));
        let what = (key && INPUT_WHAT[key]) || (i.detail ? String(i.detail) : "no description served");
        if (key === "ratings") {
          const d = parseTs(i.as_of);
          if (d) what = "team-strength fit on results to "
            + d.toLocaleDateString(undefined, { day: "numeric", month: "short" })
            + "; behind every colour";
        }
        row.appendChild(el("span", "what", what));
        row.appendChild(el("span", "age",
          h == null ? "age unknown" : oldPhrase(h)));
        row.appendChild(el("span", "rule",
          broken ? (served || "no data")
          : thr == null ? "no staleness rule"
          : over ? `stale: past its ${ruleText(thr)} limit`
          : `fresh, limit ${ruleText(thr)}`));
        row.setAttribute("aria-pressed", String(on));
        hover.attach(row, () => [
          i.as_of ? `as of ${i.as_of}` : null,
          i.rows != null ? `${Number(i.rows).toLocaleString()} rows` : null,
          i.detail ? String(i.detail) : null,
          /* printed verbatim: the panel's own sentences are grammatical now,
             and a blind " -- " to "; " substitution here made them not be */
          i.effect_when_stale ? `when stale: ${String(i.effect_when_stale)}` : null,
          i.refresh_job ? `refreshed by ${i.refresh_job}` : null,
          i.last_job_outcome ? `last run: ${i.last_job_outcome}` : null,
          on ? "click again to close the inspector" : "click to inspect this input",
        ].filter(Boolean).join("\n"));
        row.onclick = () => {
          if (selInput === i.name) { clearInputSel(); closeDrawer(); }
          else selectInput(i);
        };
        freshRow.appendChild(row);
      }
      return;
    }

    // No inputs[] contract yet. Say exactly what age we DO know, and say that
    // the market's age is unknown, which is why no price is drawn anywhere.
    freshRow.appendChild(el("span", "tlabel", "Inputs"));
    const h = ageHours(res.as_of);
    const chip = el("span", "fx-inchip" + (h == null ? " missing" : h > 72 ? " stale" : h > 36 ? " warn" : ""));
    chip.appendChild(el("span", "freshdot " + (h == null ? "bad" : h > 72 ? "bad" : h > 36 ? "warn" : "good")));
    chip.appendChild(el("b", null, "fixture list"));
    chip.appendChild(el("span", "age",
      h == null ? "age unknown" : oldPhrase(h)));
    chip.title = `fact_fixture as of ${res.as_of || "unknown"}`;
    freshRow.appendChild(chip);

    const mk = el("span", "fx-inchip missing");
    mk.appendChild(el("span", "freshdot bad"));
    mk.appendChild(el("b", null, "market odds"));
    mk.appendChild(el("span", "age", "not carried"));
    mk.appendChild(el("span", "tag", "absent"));
    mk.title = "This panel returns no odds and no odds age. A price whose age "
      + "is unknown is never rendered as current, so no market number appears "
      + "anywhere on this page: not in the colour, not in a cell, not in the "
      + "drawer.";
    freshRow.appendChild(mk);

    const note = el("span", "sub");
    note.textContent = "no inputs[] contract on this panel; only the fixture "
      + "table's as_of is knowable here";
    freshRow.appendChild(note);
  }

  /* ------------------------------------------------- the input inspector ---
     A third drawer alongside the fixture and the club: click an input chip
     and the page shows what that input IS, how old it is against its own
     staleness budget, and what on this page reads it — while the fed
     elements carry an edge-mark, so provenance is something you can see
     rather than something you take on faith. Everything in the inspector is
     the served inputs[] row; the only thing the page adds is the feeds map,
     which is a statement about THIS page, not about the data. */
  const INPUT_FEEDS = {
    ratings: "every cell colour and both rail bars; the difficulty itself",
    schedule: "the gameweek columns, and which cells are blanks or doubles",
    market: "cell tooltips and the drawer's Market act; never any colour: "
      + "nothing visible on the board carries a market number, by design, so "
      + "nothing lights up out there",
    form: "the rail form chips and the drawer's Record act; never any colour",
  };
  function inputKey(name) {
    const n = String(name || "").toLowerCase();
    if (/rating/.test(n)) return "ratings";
    if (/schedule|fixture/.test(n)) return "schedule";
    if (/market|odds/.test(n)) return "market";
    if (/form/.test(n)) return "form";
    return null;
  }
  function clearInputSel() {
    if (!selInput) return;
    selInput = null;
    card.classList.remove("fx-sel-ratings", "fx-sel-schedule",
      "fx-sel-market", "fx-sel-form");
    if (freshRes) renderFreshness(freshRes);
  }
  function selectInput(i) {
    clearInputSel();
    selInput = i.name;
    const key = inputKey(i.name);
    if (key) card.classList.add("fx-sel-" + key);
    if (freshRes) renderFreshness(freshRes);
    openInput(i, key);
  }
  function openInput(i, key) {
    drawer.textContent = "";
    openDrawer();
    drawer.appendChild(masthead(null, String(i.name || "input"),
      i.source ? String(i.source) : null, null));

    drawer.appendChild(el("h2", null, "What this input is"));
    if (i.detail) drawer.appendChild(el("p", "sub", String(i.detail)));
    else drawer.appendChild(namedGap("No detail served for this input.",
      gapText("The ", codeSpan("inputs[]"), " row carries no ",
        codeSpan("detail"), " field; its name and the ages here are "
        + "everything the panel said about it.")));

    drawer.appendChild(el("h2", null, "Age, against its own budget"));
    const h = num(i.age_hours) ?? ageHours(i.as_of);
    const thr = num(i.stale_after_hours);
    const kv = el("div", "fx-kv");
    const add = (k, v) => { kv.appendChild(el("span", "k", k)); kv.appendChild(el("span", "v", v)); };
    add("age", h == null ? "unknown" : oldPhrase(h));
    if (i.as_of) add("as of", String(i.as_of));
    if (i.state) add("state", String(i.state));
    if (i.rows != null) add("rows", Number(i.rows).toLocaleString());
    if (thr != null) add("stale after", `${Math.round(thr)}h`);
    drawer.appendChild(kv);
    if (h != null && thr) {
      /* the fraction of the budget spent, as a length — warn past 75% */
      const frac = Math.min(1, h / thr);
      const bar = el("div", "fx-agebar");
      const fill = el("div", "fill" + (frac >= 0.75 ? " warn" : ""));
      fill.style.width = `${(frac * 100).toFixed(1)}%`;
      bar.appendChild(fill);
      bar.title = `${oldPhrase(h)}, against a ${ruleText(thr)} budget`;
      drawer.appendChild(bar);
      drawer.appendChild(el("p", "sub",
        `${oldPhrase(h)}, against a ${ruleText(thr)} staleness budget`
        + (h > thr ? "; over budget" : "")));
    } else if (thr == null) {
      drawer.appendChild(el("p", "sub",
        "No staleness threshold: this input's row explains below why age "
        + "does not degrade it."));
    }
    if (i.effect_when_stale)
      drawer.appendChild(kvNote("When it goes stale.", String(i.effect_when_stale)));

    drawer.appendChild(el("h2", null, "Feeds"));
    drawer.appendChild(el("p", "sub",
      (key && INPUT_FEEDS[key]) || "nothing on this page reads this input directly."));
    if (key && key !== "market")
      drawer.appendChild(el("p", "sub",
        "The parts it feeds are edge-marked on the page while this inspector "
        + "is open."));
  }

  /* ------------------------------------------------------ toolbar rows */
  function renderHorizon() {
    horizonRow.textContent = "";
    horizonRow.appendChild(el("span", "tlabel", "Horizon"));
    for (const n of HORIZONS) {
      const b = el("button", "chip gw" + (n === horizon ? " on" : ""), `${n} GW`);
      b.title = `show the next ${n} gameweeks`;
      b.onclick = () => { horizon = n; load(); };
      horizonRow.appendChild(b);
    }
    const nav = el("span", "fx-nav");
    const back = el("button");
    back.appendChild(icon("chevron-left"));
    back.setAttribute("aria-label", "shift the window one gameweek earlier");
    back.title = "shift the window one gameweek earlier";
    back.onclick = () => { fromGw = (M.gws[0] ?? 1) - 1; if (fromGw < 1) fromGw = 1; load(); };
    back.disabled = (M.gws[0] ?? 1) <= 1;
    const fwd = el("button");
    fwd.appendChild(icon("chevron-right"));
    fwd.setAttribute("aria-label", "shift the window one gameweek later");
    fwd.title = "shift the window one gameweek later";
    fwd.onclick = () => { fromGw = (M.gws[0] ?? 1) + 1; load(); };
    nav.append(back, fwd);
    horizonRow.appendChild(nav);
    if (fromGw != null) {
      const rst = el("button", "chip", "back to next GW");
      rst.onclick = () => { fromGw = null; load(); };
      horizonRow.appendChild(rst);
    }
    horizonRow.appendChild(el("span", "fx-win",
      M.gws.length ? `GW${M.gws[0]}–GW${M.gws[M.gws.length - 1]} · ${M.teams.length} clubs` : ""));
  }

  /* One seg, four states: the lens IS the sort. Choosing "Attackers" both
     shows the attack band solo and orders the rail by the served horizon
     attack rank, so "who do I buy attackers from" is answered by the top of
     the board with zero further gestures. "Both" sorts by attack — the
     defence order is one click away and the rail prints both ranks. The old
     Order row died with this: a sort control that could contradict the lens
     was two controls answering one question. */
  function renderLens() {
    lensRow.textContent = "";
    lensRow.appendChild(el("span", "tlabel", "View"));
    const seg = el("span", "seg");
    for (const [k, isTable, label, title] of [
      ["attack", false, "Attackers",
        "solo attack bands, rows sorted easiest-first for attackers"],
      ["defence", false, "Defenders",
        "solo defence bands, rows sorted easiest-first for defenders"],
      ["both", false, "Both",
        "one cell, two bands; attackers above, defenders below; rows sorted "
        + "by the attack rank"],
      [null, true, "Table",
        "the same numbers as a sortable table, in the grid's current order; "
        + "colour is never the only way to read this page"],
    ]) {
      const on = isTable ? tableView : (!tableView && k === lens);
      const b = el("button", on ? "on" : "", label);
      b.title = title;
      b.disabled = !M.anySplit && !isTable && k !== "both";
      if (b.disabled) b.title = "the split is not in this payload; see the note below";
      b.setAttribute("aria-pressed", String(on));
      b.onclick = () => {
        if (isTable) tableView = true;
        else { lens = k; tableView = false; azSort = false; rowSort = null; }
        renderLens(); renderBody();
      };
      seg.appendChild(b);
    }
    lensRow.appendChild(seg);
    const az = el("button", "chip" + (azSort ? " on" : ""), "A–Z");
    az.title = "alphabetical, for looking one club up; any lens click "
      + "restores the lens order";
    az.setAttribute("aria-pressed", String(azSort));
    az.onclick = () => { azSort = !azSort; renderLens(); renderBody(); };
    lensRow.appendChild(az);

    /* THE BASIS SWITCH. Two genuinely different questions, and the page used
       to answer only the first: "how hard is this fixture for anyone" versus
       "how hard is it for THIS club". Both are served per cell. */
    if (M.anyClub) {
      const bseg = el("span", "seg fx-basis");
      for (const [k, label, title] of [
        ["opponent", "Any club",
          "hold this club at league average, so the cell is a property of the "
          + "opponent at that venue and two clubs facing it share a colour. "
          + "Fixed scale, comparable across clubs and windows."],
        ["club", "This club",
          "add this club's own attack and defence back, so the cell is this "
          + "club in this fixture. Wider scale, and NOT comparable between "
          + "clubs: a strong club's easy fixture and a weak club's easy "
          + "fixture are different quantities."],
      ]) {
        const b = el("button", basis === k ? "on" : "", label);
        b.title = title;
        b.setAttribute("aria-pressed", String(basis === k));
        b.onclick = () => { basis = k; renderLens(); renderBody(); };
        bseg.appendChild(b);
      }
      lensRow.appendChild(bseg);
    }

    /* One switch for every secondary number on the rows, so the default board
       answers one question and the rest is one click away. */
    const det = el("button", "chip" + (showDetail ? " on" : ""), "detail");
    det.title = showDetail
      ? "hide the per-club form and strength numbers; the fixture colours and "
        + "the two schedule ranks stay"
      : "show each club's form residuals and its attack/defence strength rank "
        + "on the row; they are always in the club's drawer too";
    det.setAttribute("aria-pressed", String(showDetail));
    det.onclick = () => { showDetail = !showDetail; renderLens(); renderBody(); };
    lensRow.appendChild(det);

    /* the my-clubs overlay toggle (FFS ticker's my-team pin): DIMS rows where
       you hold nobody — it never removes them, because a row you don't hold
       is still the row your next transfer comes from */
    const mine = el("button", "chip" + (myClubsOnly ? " on" : ""), "my clubs");
    mine.setAttribute("aria-pressed", String(myClubsOnly));
    if (SQ && SQ.byClub && SQ.byClub.size) {
      mine.title = `dim clubs you own nobody from (you hold players at `
        + `${SQ.byClub.size} club${SQ.byClub.size === 1 ? "" : "s"}); rows stay `
        + "; dimmed, never removed";
      mine.onclick = () => { myClubsOnly = !myClubsOnly; renderLens(); renderBody(); };
    } else {
      mine.disabled = true;
      mine.title = SQ && SQ.failed
        ? `squad_overview could not be read${SQ.reason ? `: ${SQ.reason}` : ""}; `
          + "the board renders without the overlay rather than guessing your squad"
        : "reading your squad…";
    }
    lensRow.appendChild(mine);
  }

  /* What the number is and where it comes from: four short answers, every
     one of them read off the payload. The owner asked what the numbers mean
     and how they are derived; the answer belongs on the page, not in a doc.
     Nothing here is hardcoded: if the panel stops publishing a fact, its line
     is dropped rather than replaced with a remembered value. */
  function derivation() {
    const box = el("dl", "fx-deriv");
    const row = (term, def) => {
      if (!def) return;
      box.appendChild(el("dt", null, term));
      box.appendChild(el("dd", null, def));
    };
    const d = M.scale.dom;
    const b = x => x.toFixed(M.scale.digits === 2 ? 2 : 1);

    /* 1. the unit, with a worked example at the top of the easy end */
    row("Whose number it is",
      "Every number on a row belongs to the club in that row, and + is always "
      + "good for it. A club is never described by its own cell: the colour is "
      + "what the OPPONENT does. CAPS opponent = at home, lower case = away.");
    if (M.scale.payloadLed && M.scale.anchorAtt != null) {
      row("The number",
        `${M.scale.unit}. Attack band, +${b(d / 2)}: the opponent concedes `
        + `${b(d / 2)} goals a match more than an average opponent `
        + `(average ${b(M.scale.anchorAtt)}), so this club's attackers have it `
        + `easier. Defence band, +${b(d / 2)}: the opponent SCORES ${b(d / 2)} `
        + `fewer, so its defenders have it easier. A + on the defence band `
        + `never means this club concedes more.`);
    } else {
      row("The number", M.scale.unit);
    }

    /* 2. the model, verbatim from the fitted-ratings input row */
    const fit = (M.res.inputs || []).find(i => i && i.name === "fitted ratings");
    if (fit && fit.detail) {
      /* Stops at the fit. The opponent-only claim is the panel's own note,
         printed a few lines below this list, and saying it twice is how a
         page gets wordy. */
      row("How it is derived",
        `${fit.detail}. Each club gets one attack and one defence strength `
        + `from that fit, and a cell is the goal rate those strengths imply `
        + `for this opponent at this venue.`);
    }

    /* 3. what the colour is, and whose colour it is */
    row("The colour",
      `Five equal steps across ±${b(d)}, in FPL's own FDR colours: step 1 is `
      + `FPL's dark green, step 3 its grey, step 5 its dark red. A colour here `
      + `means what the same colour means on the FPL site.`);

    /* 4. the ranks, which the owner has misread before when unlabelled */
    if (M.scale.rankConvention) {
      /* The panel writes "2N (opponent, venue) pairs"; the page knows what N
         is, and a reader should not have to solve for it. */
      const conv = M.scale.population
        ? M.scale.rankConvention.replace(/\b2N\b/, String(M.scale.population))
        : M.scale.rankConvention;
      row("The ranks", conv.charAt(0).toUpperCase() + conv.slice(1) + ".");
    }
    return box;
  }

  function renderNotes() {
    noteBox.textContent = "";
    // The single most important disclosure on the page: whether the numbers
    // being coloured are actually split.
    if (!M.anySplit) {
      const w = el("div", "empty");
      w.appendChild(el("b", null, "The split is not in this payload."));
      const p = el("div");
      p.appendChild(document.createTextNode("This page is reading "));
      p.appendChild(codeSpan(scriptUsed));
      p.appendChild(document.createTextNode(
        ", whose split artefact is absent, so each cell carries at most the "
        + "deprecated legacy_difficulty; ONE blended number per fixture. A blended "
        + "number is the average of the attack question and the defence "
        + "question, and the average is not the answer to either; it is the "
        + "exact failure this page exists to fix. So the grid below draws a "
        + "single band, not two: splitting one number into two bands would be "
        + "inventing the second answer. The colour, the ordering and the "
        + "“most torn” list all fall back or switch off accordingly."));
      w.appendChild(p);
      if (boardErr && !boardErr.missing) {
        w.appendChild(el("div", "sub", String(boardErr.error && boardErr.error.message || boardErr.error)));
      }
      noteBox.appendChild(w);
    }
    noteBox.appendChild(derivation());
    if (!M.scale.payloadLed && M.scale.note) {
      noteBox.appendChild(el("p", "sub", M.scale.note));
    }
    for (const n of M.res.notes || []) noteBox.appendChild(el("p", "sub", n));
  }

  /* ------------------------------------------------- the verdict strip ---
     The zero-gesture answer element: who to buy attackers from, who to buy
     defenders from, and where the lenses disagree. Every value is a served
     field; every chip is a BOOKMARK into the evidence below — it scrolls the
     board to the club's row rather than opening a second data surface. */
  function renderVerdict() {
    verdictEl.textContent = "";
    verdictEl.hidden = false;
    if (!M.anySplit) {
      verdictEl.appendChild(namedGap("Needs the split.", gapText(
        codeSpan("fixture_board"),
        " is not serving; shortlists and tears are two-lens findings, and "
        + "one blended number cannot answer either.")));
      return;
    }
    const nClubs = M.teams.length;
    const anyRanks = M.teams.some(t => t.attRankH != null || t.defRankH != null);
    const attTop = M.teams.filter(t => t.attRankH != null)
      .sort((a, b) => a.attRankH - b.attRankH).slice(0, 5);
    const defTop = M.teams.filter(t => t.defRankH != null)
      .sort((a, b) => a.defRankH - b.defRankH).slice(0, 5);
    const onBoth = new Set(attTop.filter(t => defTop.includes(t)).map(t => t.code));

    const range = M.gws.length ? `GW${M.gws[0]}–GW${M.gws[M.gws.length - 1]}` : "this window";
    /* Every rank on a chip is the SCHEDULE rank: how easy the club's next
       fixtures are for players you own from it, 1 = easiest of the clubs. The
       chip prints "schedule" so it can never be read as the club's own
       strength, which the hover states separately. */
    const shortlist = (label, clubs, rankOf, sumOf, kind, clubRankOf) => {
      const r = el("div", "vrow");
      r.appendChild(el("span", "vlab", label));
      for (const t of clubs) {
        const chip = el("button", "vchip" + (onBoth.has(t.code) ? " both2" : ""));
        chip.appendChild(crest(t.code, t.short, "s14"));
        chip.appendChild(el("b", null, t.short));
        chip.appendChild(el("span", "rk", ord(rankOf(t))));
        const sum = sumOf(t);
        const pg = (sum != null && t.nFixtures) ? sum / t.nFixtures : null;
        const cr = clubRankOf(t);
        if (onBoth.has(t.code)) chip.appendChild(el("span", "x2", "×2"));
        hover.attach(chip, () => [
          onBoth.has(t.code) ? "on both shortlists: an easy schedule for attackers and defenders" : null,
          `${t.short}: schedule for your ${kind}: ${ord(rankOf(t))} easiest of ${nClubs} clubs over ${range}`,
          `ease summed ${sgn2(sum)} (${sgn2(pg)} a game), ${M.scale.unit}`,
          cr != null ? `club strength, from the fit: ${ord(cr)} best ${kind === "attackers" ? "attack" : "defence"} of ${nClubs}` : null,
          `click to jump to ${t.short}'s row`,
        ].filter(Boolean).join("\n"));
        chip.onclick = () => bookmark(t);
        r.appendChild(chip);
      }
      return r;
    };
    if (anyRanks) {
      verdictEl.appendChild(shortlist("Best for ATT", attTop,
        t => t.attRankH, t => t.attSum, "attackers",
        t => num(t.rating && t.rating.attack_rank)));
      verdictEl.appendChild(shortlist("Best for DEF", defTop,
        t => t.defRankH, t => t.defSum, "defenders",
        t => num(t.rating && t.rating.defence_rank)));
    /* CLEAN SHEETS. A separate shortlist because it answers a separate
       question, and because it is the one number on this page that must NOT
       hold the club at league average: a clean sheet is a fact about a
       specific defence. Arsenal away at Sunderland is 30% for an average
       defence and 49% for Arsenal's. Expected count over the window is the
       SUM of per-fixture probabilities -- linearity of expectation, so no
       independence assumption is smuggled in -- and a blank contributes
       nothing, which is right. */
    const csTop = M.teams.filter(t => t.csSum != null)
      .sort((a, b) => b.csSum - a.csSum).slice(0, 5);
    if (csTop.length) {
      const r = el("div", "vrow");
      r.appendChild(el("span", "vlab", "Clean sheets"));
      for (const t of csTop) {
        const b = el("button", "vchip");
        b.appendChild(crest(t.code, t.short, "s14"));
        b.appendChild(el("b", null, t.short));
        b.appendChild(el("span", "rk", fmt1(t.csSum)));
        hover.attach(b, () => [
          `${t.short}: ${fmt2(t.csSum)} clean sheets expected over ${range}`,
          `${Math.round((t.csMean || 0) * 100)}% a game across ${t.csN} `
            + `fixture${t.csN === 1 ? "" : "s"}`,
          "With this club's own defence in it, not a league-average one.",
          `click to jump to ${t.short}'s row`,
        ].join("\n"));
        b.onclick = () => bookmark(t);
        r.appendChild(b);
      }
      verdictEl.appendChild(r);
    }
    } else {
      verdictEl.appendChild(namedGap("No horizon ranks in this payload.",
        "Sort the rail by lens instead; the shortlists render only from the "
        + "panel's own ranks, never from arithmetic done here."));
    }

    /* the tear headline, grouped by opponent-venue so five near-identical
       sentences about one club collapse into the one finding they are */
    const tearRow = (g, quiet) => {
      const top = g[0];
      const r = el("div", "vrow tear" + (quiet ? " quiet" : ""));
      /* The two rows are the two DIRECTIONS of the split, so the label has to
         name the direction. Calling both "Most split" read as one fact printed
         twice, which is exactly the repetition the compaction was for. */
      r.appendChild(el("span", "vlab",
        (num(top.gap) || 0) > 0 ? "Favours DEF" : "Favours ATT"));
      const txt = el("span", "txt");
      txt.appendChild(el("b", null, oppPair(top.opponent, top.is_home)));
      /* the population is every opponent at both venues (20 x 2 = 40), not
         40 fixtures: the bracket says so where the number is printed */
      const pop = num(M.res.scale && M.res.scale.population) || nClubs * 2;
      txt.appendChild(document.createTextNode(
        `: ATT ${ord(top.attack_rank)}, DEF ${ord(top.defence_rank)} of ${pop}`
        + ` · ` + g.map(d => `${d.short_name} GW${d.gw}`).join(" · ")));
      txt.title = `Ranked over all ${pop} opponent-venue pairs: this opponent `
        + `is ${ord(top.attack_rank)} easiest for a visiting attack and `
        + `${ord(top.defence_rank)} easiest for a visiting defence.`;
      r.appendChild(txt);
      const open = el("button", "chip", "open");
      open.title = `open ${top.short_name} ${top.is_home ? "v" : "at"} `
        + `${top.opponent}, GW${top.gw}`;
      open.onclick = () => openDivergent(top);
      r.appendChild(open);
      return r;
    };
    /* The fixture-turn sentence the dashboard brief knows, surfaced where the
       fixtures live: a served rank joined with a served holding — no number
       is computed here, only the join. Shown only when it applies to YOU. */
    if (SQ && SQ.byClub && SQ.byClub.size) {
      const turns = [];
      for (const t of M.teams) {
        const names = ownedNames(t.code);
        if (!names) continue;
        if (t.attRankH != null && t.attRankH <= 3)
          turns.push({ t, names, rank: t.attRankH, what: "attackers" });
        if (t.defRankH != null && t.defRankH <= 3)
          turns.push({ t, names, rank: t.defRankH, what: "defenders" });
      }
      turns.sort((a, b) => a.rank - b.rank);
      /* One row, not one per club: the label repeated on consecutive lines is
         a heading pretending to be data. Each club is its own chip, so the
         row stays clickable per club. */
      const picked = turns.slice(0, 3);
      if (picked.length) {
        const r = el("div", "vrow fx-turnrow");
        r.appendChild(el("span", "vlab", "You hold"));
        for (const u of picked) {
          const b = el("button", "vchip");
          b.appendChild(crest(u.t.code, u.t.short, "s14"));
          b.appendChild(el("b", null, u.t.short));
          b.appendChild(el("span", "rk",
            `${u.what === "attackers" ? "ATT" : "DEF"} ${ord(u.rank)}`));
          b.title = `${u.t.short}: ${ord(u.rank)} easiest run of ${nClubs} for `
            + `its ${u.what}. You hold ${u.names.join(", ")}.`;
          b.onclick = () => bookmark(u.t);
          r.appendChild(b);
        }
        verdictEl.appendChild(r);
      }
    }

    /* Torn feature rows are squad-aware: with a readable squad, they show
       only when a torn fixture belongs to a club you hold — otherwise the
       finding is trivia here and lives in the collapsed list below. Without
       a readable squad the page cannot know, so it shows the biggest tear
       as before rather than guessing. */
    const allGroups = tornGroups();
    const relevantOf = gs => (SQ && SQ.byClub && SQ.byClub.size)
      ? gs.filter(g => g.some(d => ownedNames(num(d.team_code))))
      : gs;
    const groups = relevantOf(allGroups);
    if (!allGroups.length) {
      const r = el("div", "vrow tear");
      r.appendChild(el("span", "vlab", "Split"));
      r.appendChild(el("span", "txt",
        "no torn fixtures in this window: a real finding, not an empty "
        + "state; the split changes no decision here"));
      verdictEl.appendChild(r);
    } else if (!groups.length) {
      verdictEl.appendChild(quietGap(
        `${allGroups.length} torn opponent-venue${allGroups.length === 1 ? "" : "s"} `
        + "in this window, none at a club you hold; folded below, each marked "
        + "on its cell's seam"));
    } else {
      verdictEl.appendChild(tearRow(groups[0], false));
      const sign = (groups[0][0].gap || 0) > 0;
      const other = groups.find(g => ((g[0].gap || 0) > 0) !== sign);
      if (other) verdictEl.appendChild(tearRow(other, true));
    }

    /* no caveat line here: the calibration band is the header's one line, and
       "schedule rank, 1st = easiest of the 20 clubs" is in the legend; saying either a
       second time costs the reader words and tells him nothing new */
  }

  /* ---------------------------------------------- the next-GW strip ---
     One compact row: THIS week's fixtures, hardest to easiest through the
     attack lens, kickoff printed — the captain sanity check (the official
     app's plain fixture list is faster than a 6-GW matrix for this one
     question). Every number is the board's own; each side's swatch is that
     club's attack-lens colour, so the strip and the grid can never disagree. */
  function renderStrip() {
    stripEl.textContent = "";
    stripEl.hidden = true;
    if (!M.anySplit || !M.gws.length) return;
    const gw0 = M.gws[0];
    const byId = new Map();     // fixture_id -> {home:{t,c}, away:{t,c}}
    for (const t of M.teams) {
      const slot = t.byGw.get(gw0);
      if (!slot || slot.blank) continue;
      for (const c of slot.opps) {
        if (c.fixtureId == null) continue;
        if (!byId.has(c.fixtureId)) byId.set(c.fixtureId, {});
        byId.get(c.fixtureId)[c.isHome ? "home" : "away"] = { t, c, slot };
      }
    }
    const rows = [...byId.values()].filter(f => f.home && f.away);
    if (!rows.length) return;
    const best = f => {
      const vals = [f.home.c.easeAtt, f.away.c.easeAtt].filter(v => v != null);
      return vals.length ? Math.max(...vals) : null;
    };
    rows.sort((a, b) => {
      const va = best(a), vb = best(b);
      if (va == null && vb == null) return 0;
      if (va == null) return -1;
      if (vb == null) return 1;
      if (va !== vb) return va - vb;               // hardest first, easiest last
      return String(a.home.c.kickoff || "").localeCompare(String(b.home.c.kickoff || ""));
    });

    stripEl.hidden = false;
    const lab = el("span", "vlab",
      `GW${gw0} · hardest to easiest · kickoffs UTC`);
    lab.title = "this week's fixtures through the attack lens; the captain "
      + "sanity check; each club's swatch is its own attack-lens colour";
    stripEl.appendChild(lab);
    const strip = el("div", "fx-strip scroll-x");
    for (const f of rows) {
      const { home, away } = f;
      const chip = el("button", "fx-stripchip");
      const side = (x, caps) => {
        const s = el("span", "side");
        s.appendChild(el("i", "sw " + (x.c.easeAtt == null ? "nofit"
          : (cls(eAtt(x.c), curDom()) || "fx-d3"))));
        s.appendChild(el("b", null,
          caps ? x.t.short.toUpperCase() : x.t.short.toLowerCase()));
        if (ownedNames(x.t.code))
          s.appendChild(ownPip(ownedNames(x.t.code)));
        return s;
      };
      chip.appendChild(side(home, true));
      chip.appendChild(el("span", "v", "v"));
      chip.appendChild(side(away, false));
      const ko = parseTs(home.c.kickoff);
      if (ko) chip.appendChild(el("span", "ko",
        ko.toLocaleString(undefined, { timeZone: "UTC", weekday: "short",
          hour: "2-digit", minute: "2-digit" })));
      hover.attach(chip, () => [
        `${home.t.short} v ${away.t.short} · GW${gw0}`,
        kickoffText(home.c.kickoff),
        `${home.t.short} attackers ${sgn2(home.c.easeAtt)} · `
          + `${away.t.short} attackers ${sgn2(away.c.easeAtt)}`,
        `${M.scale.unit}; positive is easier`,
        ownedNames(home.t.code) ? `you hold ${ownedNames(home.t.code).join(", ")} (${home.t.short})` : null,
        ownedNames(away.t.code) ? `you hold ${ownedNames(away.t.code).join(", ")} (${away.t.short})` : null,
        "click for the match detail",
      ].filter(Boolean).join("\n"));
      chip.onclick = () => openFixture(home.t, home.slot, home.c);
      strip.appendChild(chip);
    }
    stripEl.appendChild(strip);
  }

  /* A chip click is a bookmark, not a claim: scroll the board to the club's
     row and flash its rail. Under prefers-reduced-motion the scroll still
     happens, instantly, and nothing flashes. */
  function bookmark(t) {
    if (tableView) { tableView = false; renderLens(); renderBody(); }
    const rail = body.querySelector(`.fx-rail[data-club="${t.code}"]`);
    if (!rail) return;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    rail.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
    if (!reduce) {
      rail.classList.add("flash");
      setTimeout(() => rail.classList.remove("flash"), 900);
    }
  }

  /* ---------------------------------------------------------- the grid */
  /* The lens IS the sort. The rank fields are the panel's own ordering
     (1 = easiest); the sums are the fallback so a payload without horizon
     ranks still orders rather than freezing. Under the legacy fallback there
     is one blended number and it is the only order on offer. */
  function sortedTeams() {
    const t = M.teams.slice();
    if (azSort) { t.sort((a, b) => a.short.localeCompare(b.short)); return t; }
    const byRank = (rank, sum) => (a, b) => {
      const ra = rank(a), rb = rank(b);
      if (ra != null && rb != null && ra !== rb) return ra - rb;
      if (ra != null && rb == null) return -1;
      if (ra == null && rb != null) return 1;
      const sa = sum(a), sb = sum(b);
      if (sa == null && sb == null) return a.short.localeCompare(b.short);
      if (sa == null) return 1;
      if (sb == null) return -1;
      return sb - sa;
    };
    if (!M.anySplit)
      t.sort(byRank(() => null,
        x => (x.blendMean == null ? null : x.blendMean * x.nFixtures)));
    else if ((rowSort || lens) === "def" || (rowSort || lens) === "defence")
      t.sort(byRank(x => tDefRank(x), x => tDef(x)));
    else t.sort(byRank(x => tAttRank(x), x => tAtt(x)));
    return t;
  }

  function renderBody() {
    body.textContent = "";
    if (!M.teams.length) {
      body.appendChild(emptyBox("No club has a fixture in this window."));
      return;
    }
    if (tableView) { renderTable(); return; }

    const wrap = el("div", "fx-gridwrap scroll-x");
    const grid = el("div", "fx-grid");
    grid.style.setProperty("--fx-cols", String(M.gws.length));

    // header row
    const rh = el("div", "fx-hcell fx-railhead");
    rh.appendChild(el("b", "fx-rhtitle", "club"));
    /* Two columns, two sort buttons, and the words that used to be repeated on
       all 20 rows live here once. */
    const sorts = el("span", "fx-rhsorts");
    const mk = (key, label, what) => {
      const on = (rowSort || (lens === "defence" ? "def" : "att")) === key;
      const b = el("button", "fx-rhsort" + (on ? " on" : ""));
      b.appendChild(el("span", null, label));
      // the pressed state is the mark; the shared sort-up icon says which
      // direction the pressed one is in
      if (on) b.appendChild(sortIcon("ascending"));
      b.title = `sort by how easy this run is for a club's ${what}; `
        + `1 = easiest of ${M.teams.length} clubs over ${
          `GW${M.gws[0]}–GW${M.gws[M.gws.length - 1]}`}`;
      b.setAttribute("aria-pressed", String(on));
      b.onclick = () => { rowSort = key; azSort = false; renderLens(); renderBody(); };
      return b;
    };
    sorts.append(mk("att", "ATT", "attackers"), mk("def", "DEF", "defenders"));
    rh.appendChild(sorts);
    grid.appendChild(rh);
    for (const g of M.gws) {
      const h = el("div", "fx-hcell");
      h.appendChild(document.createTextNode(`GW${g}`));
      const anyK = M.teams.flatMap(t => t.byGw.get(g).opps).map(c => c.kickoff).filter(Boolean).sort()[0];
      const d = parseTs(anyK);
      if (d) h.appendChild(el("span", "d",
        d.toLocaleDateString(undefined, { day: "numeric", month: "short" })));
      grid.appendChild(h);
    }

    // magnitude for the rail bars: one shared axis across the whole board
    let railMax = 0.001;
    for (const t of M.teams) for (const v of [t.attSum, t.defSum,
      (M.anySplit ? null : (t.blendMean == null ? null : t.blendMean * t.nFixtures))])
      if (v != null) railMax = Math.max(railMax, Math.abs(v));

    const ordered = sortedTeams();
    ordered.forEach((t) => {
      /* the my-clubs overlay DIMS, never removes: an unowned row is still the
         row your next transfer comes from */
      const dim = myClubsOnly && SQ && SQ.byClub && !ownedNames(t.code);
      const rail = railCell(t, railMax);
      if (dim) rail.classList.add("fx-dim");
      grid.appendChild(rail);
      for (const g of M.gws) {
        const cell = gwCell(t, t.byGw.get(g));
        if (dim) cell.classList.add("fx-dim");
        grid.appendChild(cell);
      }
    });

    wrap.appendChild(grid);
    body.appendChild(wrap);
    body.appendChild(legend());
  }

  /* One club, one glance: crest, name, tornness, the form answer, and the
     rank PAIR with its bars. The single sort-position number died here — it
     re-encoded whatever the sort key happened to be and lied under A–Z; the
     payload's own attack/defence ranks never change meaning under any sort. */
  function railCell(t, railMax) {
    const d = el("div", "fx-rail");
    if (t.code != null) d.dataset.club = String(t.code);
    d.tabIndex = 0;
    d.setAttribute("role", "button");
    const range = `GW${M.gws[0]}–GW${M.gws[M.gws.length - 1]}`;
    const n = M.teams.length;
    const rt = t.rating || {};
    const held = ownedNames(t.code);
    /* The prior mark: more than 40% of the fitted rating is shrinkage to the
       league prior, or the panel flags the club promoted (the same fact, when
       the share is not served). A word, not a dotted underline: an underline
       reads as a link. */
    const promoted = !!rt.is_promoted || (t.priorShare != null && t.priorShare > 0.4);
    const clubLine = num(rt.attack_rank) != null || num(rt.defence_rank) != null;
    hover.attach(d, () => [
      `${t.name}: ${t.nFixtures} fixture${t.nFixtures === 1 ? "" : "s"} in ${range}`
        + (t.nBlanks ? `, ${t.nBlanks} blank` : "")
        + (t.nDoubles ? `, ${t.nDoubles} double` : ""),
      M.anySplit && (t.attRankH != null || t.defRankH != null)
        ? `run for its attackers: ${ord(t.attRankH)} easiest of ${n}; `
          + `for its defenders: ${ord(t.defRankH)} easiest of ${n}`
        : null,
      t.attSum != null
        ? `ease summed: attackers ${sgn2(t.attSum)}, defenders ${sgn2(t.defSum)} (${M.scale.unit})`
        : null,
      clubLine
        ? `club strength, from the fit: ${ord(rt.attack_rank)} best attack, `
          + `${ord(rt.defence_rank)} best defence of ${n}`
        : null,
      promoted
        ? (t.priorShare != null
            ? `promoted this season: ${Math.round(t.priorShare * 100)}% of its rating is the league average, not its own results; read its colours gently`
            : "promoted this season: few Premier League matches in the fit, so its rating leans on the league average; read its colours gently")
        : null,
      t.tornRows && t.tornRows.length
        ? `${t.tornRows.length} torn fixture${t.tornRows.length === 1 ? "" : "s"}: attack and defence answers point opposite ways`
        : null,
      held ? `you hold ${held.length} here: ${held.join(", ")}` : null,
      "click for the club's schedule",
    ].filter(Boolean).join("\n"));
    d.appendChild(crest(t.code, t.short, "s20"));
    const nm = el("span", "nm");
    nm.appendChild(el("span", null, t.short));
    if (promoted) nm.appendChild(promoTag());
    /* the ownership pip: a count, because "you own 2 here" is the fact the
       rusher cross-references from memory today (FFS ticker's my-team pin).
       The count carries the word "own": beside a club abbreviation a bare
       digit read as a scoreline ("MUN 2 v mci 1"). The legend keys it. */
    if (held) {
      d.classList.add("own");
      nm.appendChild(ownPip(held));
    }
    if (t.tornRows && t.tornRows.length) {
      const z = el("i", "fx-torn2", "torn");
      z.setAttribute("aria-label", `${t.tornRows.length} torn fixture${t.tornRows.length === 1 ? "" : "s"}`);
      nm.appendChild(z);
    }
    d.appendChild(nm);
    const chip = showDetail ? formChipEl(t.form, true) : null;
    if (chip) {
      /* the chip's native titles would double the rail's hover card; fold
         them into one styled card on the chip itself */
      const notes = [chip.title, ...[...chip.querySelectorAll("[title]")].map(p => p.title)]
        .filter(Boolean);
      chip.removeAttribute("title");
      chip.querySelectorAll("[title]").forEach(p => p.removeAttribute("title"));
      if (notes.length) hover.attach(chip, () => notes.join("\n"));
      d.appendChild(chip);
    }
    /* club strength beside run difficulty, so "elite club, hard run" is a
       visible fact on the row and not an inference across two surfaces */
    if (clubLine && showDetail)
      d.appendChild(el("span", "fx-club",
        `club rank: ${ord(rt.attack_rank)} attack, ${ord(rt.defence_rank)} defence`));

    /* LENGTH encodes the horizon SUM from a centre line that IS the
       league-average fixture; doubles and blanks handled natively, because
       more fixtures really is more chances. COLOUR is the per-game average,
       so the tint sits on exactly the cells' scale. Length survives colour
       blindness, print and forced-colours on its own. */
    /* Two rank chips, wearing the grid's own FDR colour for the club's
       per-game ease. The stacked label + number + hairline bar that used to
       sit here was three visual weights for two facts, and the bars were
       unreadable at 40px: rank already orders the clubs, and the chip's
       colour carries the magnitude the bar was drawing. One row of two
       chips, aligned across all 20 rows, is scannable in a way the old
       stack was not. */
    const ranks = el("div", "fx-ranks");
    const rankChip = (tag, what, rank, sum) => {
      const c = el("span", "fx-rk");
      const perGame = sum != null && t.nFixtures ? sum / t.nFixtures : null;
      const band = perGame == null ? null : cls(perGame, curDom());
      if (band) c.classList.add(band);
      c.appendChild(el("i", null, tag));
      c.appendChild(el("b", null, rank == null ? "–" : String(rank)));
      if (rank != null) {
        c.setAttribute("aria-label",
          `run for this club's ${what}: ${ord(rank)} easiest of ${n} clubs`);
        hover.attach(c, () => `${t.short}: ${ord(rank)} easiest run of ${n} `
          + `clubs for its ${what} over ${range}`
          + (sum == null ? "" : `, ${sgn2(sum)} goals across the window`));
      }
      return c;
    };
    if (M.anySplit) {
      ranks.append(rankChip("ATT", "attackers", tAttRank(t), tAtt(t)),
                   rankChip("DEF", "defenders", tDefRank(t), tDef(t)));
    } else {
      const v = t.blendMean == null ? null : t.blendMean * t.nFixtures;
      ranks.appendChild(rankChip("RUN", "players", null, v));
    }
    d.appendChild(ranks);
    d.onclick = () => openClub(t);
    d.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openClub(t); } };
    return d;
  }

  function railTrack(sum, max, nFixtures) {
    const track = el("div", "fx-track");
    if (sum != null && max) {
      const perGame = nFixtures ? sum / nFixtures : null;
      const frac = Math.max(-1, Math.min(1, sum / max));
      const fill = el("div", "fx-fill " + (cls(perGame, curDom()) || "fx-d3"));
      if (frac >= 0) { fill.style.left = "50%"; fill.style.width = `${frac * 50}%`; }
      else { fill.style.right = "50%"; fill.style.width = `${-frac * 50}%`; }
      track.appendChild(fill);
    }
    return track;
  }

  function gwCell(t, slot) {
    if (slot.blank) {
      const c = el("div", "fx-cell blank");
      c.appendChild(el("span", "why", "blank"));
      c.title = `${t.short} has no fixture in GW${slot.gw}. A blank is not a `
        + "zero: there is nothing to score and nothing to keep out.";
      return c;
    }
    if (slot.double) {
      const box = el("div", "fx-dgw");
      for (const c of slot.opps) box.appendChild(oneCell(t, slot, c));
      return box;
    }
    return oneCell(t, slot, slot.opps[0]);
  }

  function oneCell(t, slot, c) {
    const hasSplit = eAtt(c) != null && eDef(c) != null;
    const btn = el("button", "fx-cell");
    const torn = (c.fixtureId != null && t.code != null)
      ? M.tornMap.get(`${c.fixtureId}|${t.code}`) : null;

    /* Two bands ONLY when this fixture genuinely has two numbers. Everything
       else is one band, and a fixture with no number at all is hatched — a
       missing rating must never wear a colour. */
    const solo = !(M.anySplit && lens === "both" && hasSplit);
    let showAtt = null, showDef = null;
    if (!solo) { showAtt = eAtt(c); showDef = eDef(c); }
    else if (lens === "attack") showAtt = eAtt(c);
    else if (lens === "defence") showAtt = eDef(c);
    else showAtt = hasSplit ? eAtt(c) : c.easeBlend;

    const known = solo ? showAtt != null : true;
    if (!known) btn.classList.add("nomodel");
    if (solo) btn.classList.add("solo");

    btn.appendChild(el("span", "fx-band att " + (cls(showAtt, curDom()) || "fx-d3")));
    if (!solo) {
      btn.appendChild(el("span", "fx-band def " + (cls(showDef, curDom()) || "fx-d3")));
      btn.appendChild(el("span", "seam" + (torn ? " torn" : "")));
    }
    /* Venue was carried by letter case alone (MUN home, mun away). That is
       too quiet to hold a load this heavy: the colour is a property of the
       OPPONENT AT A VENUE, so United at home and United away are different
       cells, and a reader who misses the case reads the difference as a bug.
       The away marker is explicit now. One character, and it is the character
       that makes two cells with the same three letters legible. */
    const oppEl = el("span", "opp");
    if (!c.isHome) oppEl.appendChild(el("i", "at", "@"));
    oppEl.appendChild(document.createTextNode(c.label));
    btn.appendChild(oppEl);
    /* The Both lens carries no resident numerals: ~240 signed numbers on the
       scan path taxed the very glance the two-band cell was bought for. A solo
       lens is arithmetic, so its one number returns; the rest live in the
       tooltip, the Table view and the drawer. */
    if (solo && known)
      btn.appendChild(el("span", "vv", sgn2(showAtt).replace("0.", ".")));
    if (!known) btn.appendChild(el("span", "why",
      M.anySplit && lens === "both" ? "half fitted" : "no fit"));

    /* the styled hover/focus card replaces the native title: the same
       numbers, instantly, reachable by keyboard (the cell is a button) */
    const pop = num(M.res.scale && M.res.scale.population) || M.teams.length * 2;
    hover.attach(btn, () => [
      `${t.short} ${c.isHome ? "v" : "at"} ${c.opponent} · GW${slot.gw}${slot.double ? " (double)" : ""}`,
      kickoffText(c.kickoff),
      hasSplit
        ? `attackers ${sgn2(eAtt(c))} · defenders ${sgn2(eDef(c))} ${M.scale.unit}`
        : c.easeBlend != null
          ? `blended difficulty ${fmt2(c.blended)} (not split; see the note above)`
          : "no fitted rating for this fixture",
      c.rankAtt != null && c.rankDef != null
        ? `ranked as ${oppPair(c.opponent, c.isHome)}: ${ord(c.rankAtt)} easiest of `
          + `${pop} for its attackers, ${ord(c.rankDef)} easiest of ${pop} for its `
          + "defenders (every opponent, at each venue)"
        : null,
      torn ? String(torn.sentence || "") : null,
      c.marketState
        ? `market: ${c.marketState}`
          + (c.nBooks != null ? ` · ${c.nBooks} books` : "")
          + (c.marketAgeH != null ? ` · ${oldPhrase(c.marketAgeH)}` : "")
        : null,
      ownedNames(t.code)
        ? `you hold ${ownedNames(t.code).length} at ${t.short}` : null,
      `${t.short} ${c.isHome ? "at home" : "away"} · click for the match detail`,
    ].filter(Boolean).join("\n"));
    btn.onclick = () => openFixture(t, slot, c);
    return btn;
  }

  /* --------------------------------------------------------- the legend
     Payload-led: the unit, the midpoint and the class boundaries are read off
     the scale the panel published, never from a string in this file. */
  function legend() {
    const L = el("div", "fx-legend");
    const left = el("div");
    const ramp = el("div", "fx-ramp");
    ramp.appendChild(el("span", "lab", "easy "));
    /* Each swatch carries its FDR number, so the legend states what the
       colour means instead of leaving the reader to count steps. */
    for (const k of CLASSES) {
      const sw = el("span", "sw " + k, String(fdrOf(k)));
      sw.title = `FDR ${fdrOf(k)}: the colour FPL prints for difficulty `
               + `${fdrOf(k)}`;
      ramp.appendChild(sw);
    }
    ramp.appendChild(el("span", "lab", " hard"));
    left.appendChild(ramp);
    const d = curDom(), u = M.scale.unit;
    const b = x => (M.scale.digits === 2 ? x.toFixed(2) : x.toFixed(1));
    left.appendChild(el("div", "fx-boundaries",
      `${b(-d)}  ${b(-3 * d / 5)}  ${b(-d / 5)} · 0 · ${b(d / 5)}  `
      + `${b(3 * d / 5)}  ${b(d)}`));
    left.appendChild(el("div", "lab",
      "the five colours are FPL's own FDR steps; the numbers below them are "
      + "ours, and the unit is on the next line"));
    /* Which question is on screen, and on whose scale. The club basis borrows
       nothing from the served domain, so saying "fixed domain" under it would
       be false. */
    const bl = el("div", "lab");
    bl.appendChild(el("b", null, basis === "club" ? "This club: " : "Any club: "));
    bl.appendChild(document.createTextNode(basis === "club"
      ? `this club's own attack and defence are in the number. Scale ±${
          b(curDom())}, computed from the clubs on screen${
          M.clubClipped ? `, ${M.clubClipped} values clipped` : ""
        }, so colours are NOT comparable between clubs or windows.`
      : "this club is held at league average, so the colour is the opponent "
        + "at that venue and every club facing them shares it."));
    left.appendChild(bl);
    const unit = el("div", "lab");
    unit.appendChild(document.createTextNode("unit: "));
    unit.appendChild(el("b", null, u));
    /* "fixed domain" and the served clip count are facts about the
       OPPONENT-ONLY calibration. Printing them under the club basis would be
       a stale claim: that domain is computed here and clips a different
       number of values, both of which the basis line above states. */
    if (basis === "club") {
      unit.appendChild(document.createTextNode(
        " · scale computed from this window, so not comparable across windows"));
    } else {
      unit.appendChild(document.createTextNode(
        M.scale.payloadLed
          ? " · fixed domain, so a colour means the same in every GW"
          : " · midpoint 0.50 · normalised this season; not comparable across seasons"));
      if (M.scale.clipped)
        unit.appendChild(document.createTextNode(
          ` · ${M.scale.clipped} pairs clipped`));
    }
    left.appendChild(unit);
    L.appendChild(left);

    const keys = el("div", "fx-keys");
    /* The polarity is the one thing a reader cannot guess and cannot work
       around, so it leads the key and is never behind a disclosure. */
    keys.appendChild(keyItem(null,
      "+ = easier for the club in the row · − = harder · both bands"));
    if (M.anySplit && lens === "both") {
      keys.appendChild(keyItem(null, "upper band = attackers · lower band = defenders"));
    } else if (!M.anySplit) {
      keys.appendChild(keyItem(null, "one band = one blended number; the split is unavailable"));
    } else {
      keys.appendChild(keyItem(null, lens === "attack"
        ? "one band = the attack lens only" : "one band = the defence lens only"));
    }
    keys.appendChild(keyItem(null, "@ before an opponent = away · CAPS = home, lower case = away"));
    keys.appendChild(keyItem("hatch", "hatched = blank GW or no fit"));
    keys.appendChild(keyItem(null, "split cell = double gameweek"));
    if (M.anySplit)
      keys.appendChild(keyItem("seam", "dashed seam = torn: lenses disagree"));
    if (M.anySplit)
      keys.appendChild(keyItem(null, `schedule rank = how easy this club's next ${M.gws.length} fixtures are for players you own from it; 1st = easiest of ${M.teams.length} clubs`));
    if (M.anySplit)
      keys.appendChild(keyItem(null, `club rank = the club's own fitted strength; 1st = best of ${M.teams.length}`));
    if (SQ && SQ.byClub && SQ.byClub.size)
      keys.appendChild(keyItem(null,
        "own 2 = you hold 2 players at that club"));
    keys.appendChild(keyItem("promo", PROMO_NOTE));
    keys.appendChild(keyItem(null, "hover a cell for its numbers"));
    L.appendChild(keys);
    return L;
  }
  /* the promoted tag, with its meaning on the tag itself: the owner asked
     what the word meant, so the word now answers */
  function promoTag() {
    const s = el("span", "fx-promo", "promoted");
    s.title = PROMO_NOTE;
    s.setAttribute("aria-label", PROMO_NOTE);
    return s;
  }
  function keyItem(kind, text) {
    const k = el("span", "k");
    if (kind === "hatch") k.appendChild(el("span", "fx-swatch-hatch"));
    if (kind === "seam") k.appendChild(el("span", "fx-swatch-seam"));
    if (kind === "promo") k.appendChild(promoTag());
    k.appendChild(document.createTextNode(text));
    return k;
  }

  /* --------------------------------------------------- the table view ---
     The diverging midpoint sits below 3:1 against the card surface — inherent
     to a diverging scale, and the dataviz rule is that such a WARN obligates a
     relief channel rather than being dismissable. This is that channel, along
     with the printed number in every band. */
  function renderTable() {
    const wrap = el("div", "scroll-x");
    const t = el("table", "data sticky-first fx-tableview");
    const thead = el("thead"), hr = el("tr");

    /* REAL sort — the toggle's tooltip promised a sortable table, so the
       headers are buttons, the active one carries aria-sort and a persistent
       arrow (FBRef's headers are the reference). Sort values are the same
       eases the cells print; a blank sorts last under either direction. */
    const anyCs = M.teams.some(t => t.csSum != null);
    const gwVal = (team, g) => {
      const slot = team.byGw.get(g);
      if (!slot || slot.blank || !slot.opps.length) return null;
      const c = slot.opps[0];
      return eAtt(c) != null ? eAtt(c) : c.easeBlend;
    };
    const keyVal = (team, key) => {
      if (key === "club") return team.short;
      if (key === "att") return M.anySplit ? tAtt(team)
        : (team.blendMean == null ? null : team.blendMean * team.nFixtures);
      if (key === "def") return tDef(team);
      if (key === "cs") return team.csSum;
      return gwVal(team, Number(key.slice(3)));       // "gw:<n>"
    };
    /* app.js's one sortable header (R11, R12): the role, the tabindex, the
       keys and the sort mark, with the first direction declared per column
       (a schedule column opens easiest first, the club column A to Z). */
    const th = (key, label, numeric) => sortableTh(label, {
      active: !!(tsort && tsort.key === key),
      dir: tsort && tsort.key === key ? tsort.dir : null,
      num: numeric,
      first: numeric ? -1 : 1,              // numeric: easiest first
      title: numeric
        ? `sort by ${label}; first click puts the easiest schedule on top`
        : "sort by club name",
      onSort: dir => { tsort = { key, dir }; renderBody(); },
    });
    hr.appendChild(th("club", "club", false));
    for (const g of M.gws) hr.appendChild(th(`gw:${g}`, `GW${g}`, true));
    hr.appendChild(th("att", M.anySplit ? "Σ att" : "Σ schedule", true));
    if (M.anySplit) hr.appendChild(th("def", "Σ def", true));
    /* Expected clean sheets, sortable, because "who keeps a clean sheet" is a
       question the ease columns cannot answer: they hold the club at league
       average and a clean sheet belongs to a specific defence. */
    if (anyCs) hr.appendChild(th("cs", "xCS", true));
    thead.appendChild(hr); t.appendChild(thead);

    let rows = sortedTeams();                          // the lens order
    if (tsort) {
      rows = rows.slice().sort((a, b) => {
        const va = keyVal(a, tsort.key), vb = keyVal(b, tsort.key);
        if (typeof va === "string" || typeof vb === "string")
          return String(va).localeCompare(String(vb)) * tsort.dir;
        if (va == null && vb == null) return 0;
        if (va == null) return 1;                      // blanks last, always
        if (vb == null) return -1;
        return (va - vb) * tsort.dir;
      });
    }
    const tb = el("tbody");
    for (const team of rows) {
      const tr = el("tr");
      const tdc = el("td", null, team.short);
      const held = ownedNames(team.code);
      if (held) tdc.appendChild(ownPip(held, true));
      tr.appendChild(tdc);
      for (const g of M.gws) {
        const slot = team.byGw.get(g);
        const td = el("td", "num");
        if (slot.blank) { td.textContent = "blank"; td.style.color = "var(--faint)"; }
        else {
          const parts = slot.opps.map(c => {
            if (eAtt(c) != null && eDef(c) != null)
              return `${oppLabel(c)} A${sgn2(eAtt(c))} D${sgn2(eDef(c))}`;
            if (c.easeBlend != null) return `${oppLabel(c)} ${sgn2(c.easeBlend)}`;
            return `${oppLabel(c)} no fit`;
          });
          const first = slot.opps[0];
          const sw = el("span", "sw " + (cls(
            eAtt(first) != null ? eAtt(first) : first.easeBlend, curDom()) || "fx-d3"));
          td.appendChild(sw);
          td.appendChild(document.createTextNode(parts.join(" · ")));
        }
        tr.appendChild(td);
      }
      tr.appendChild(el("td", "num", M.anySplit ? sgn2(tAtt(team))
        : sgn2(team.blendMean == null ? null : team.blendMean * team.nFixtures)));
      if (M.anySplit) tr.appendChild(el("td", "num", sgn2(tDef(team))));
      if (anyCs) {
        const td = el("td", "num", team.csSum == null ? "–" : fmt2(team.csSum));
        if (team.csSum != null)
          td.title = `${fmt2(team.csSum)} clean sheets expected over the window `
            + `(${Math.round((team.csMean || 0) * 100)}% a game across `
            + `${team.csN} fixture${team.csN === 1 ? "" : "s"}), with this `
            + "club's own defence in it";
        tr.appendChild(td);
      }
      tb.appendChild(tr);
    }
    t.appendChild(tb); wrap.appendChild(t);
    body.appendChild(wrap);
    body.appendChild(el("p", "sub",
      M.anySplit
        ? `Each cell is attack-ease / defence-ease in ${M.scale.unit}. Positive is easier.`
        : `Each cell is the blended ease in ${M.scale.unit}. Positive is easier. `
          + "It is one number, not two."));
    body.appendChild(el("p", "sub",
      "Σ att / Σ def sum the club's schedule over the window: how easy its "
      + "fixtures are for its own players, not how good the club is. xCS is "
      + "expected clean sheets over the window, and it is the one column that "
      + "does include the club's own defence."));
    body.appendChild(legend());
  }

  /* ---------------------- where the lenses disagree (the appendix card)
     Fed ONLY by the served divergent[] list — see buildModel. Grouped by
     (opponent, venue) because the tear belongs to the opponent-venue, not to
     the visiting club: the served list is |gap|-sorted and its top dozen rows
     are all visits to the same club, so an ungrouped top-5 spends four of its
     five slots repeating one finding. */
  function renderTorn() {
    tornCard.textContent = "";
    tornCard.hidden = false;
    tornCard.open = foldOpen.torn;
    const range = M.gws.length ? `GW${M.gws[0]}–GW${M.gws[M.gws.length - 1]}` : "this window";
    if (!M.anySplit) {
      tornCard.appendChild(el("summary", null, "Torn fixtures: needs the split"));
      tornCard.appendChild(el("p", "sub",
        "This strip finds the fixtures where the attack answer and the defence "
        + "answer point opposite ways: the ones a single FDR number reports as "
        + "average, which is the one thing they are not."));
      tornCard.appendChild(namedGap("Needs the split.", gapText(
        "The payload carries one blended number per fixture, so there is no "
        + "disagreement to find. This strip lights up when ",
        codeSpan("fixture_board"),
        " serves attack and defence separately.")));
      return;
    }
    const rows = M.divergent;
    const groups = tornGroups();
    if (!rows.length) {
      tornCard.appendChild(el("summary", null,
        `Torn fixtures: none in ${range}`));
      tornCard.appendChild(namedGap("No torn fixtures in this window.",
        "Every fixture here has its two lenses pointing the same way. That is a "
        + "real finding, not an empty state: over this window the split does not "
        + "change any decision, and a blended number would have served."));
      return;
    }
    tornCard.appendChild(el("summary", null,
      `Torn fixtures: ${rows.length} `
      + `in ${range}, at ${groups.length} opponent-venue${groups.length === 1 ? "" : "s"}`));
    tornCard.appendChild(el("p", "sub",
      "Fixtures where the attack and defence answers point opposite ways; a "
      + "single difficulty number calls them average. Each is marked on its "
      + "cell's seam above. The five biggest:"));
    const list = el("div", "fx-torn-list");
    for (const g of groups.slice(0, 5)) {
      const top = g[0];
      const row = el("div", "fx-torn-row");
      const head = el("div", "hd");
      head.appendChild(crest(top.opponent_code, top.opponent, "s14"));
      head.appendChild(el("b", null,
        `${top.is_home ? "hosting" : "visiting"} ${top.opponent}`));
      row.appendChild(head);
      /* The panel's own sentence is the finding, verbatim; the rows in a
         group share the same ranks, so the top row speaks for all of them. */
      row.appendChild(el("div", "txt", String(top.sentence || "")));
      const who = el("div", "who");
      for (const d of g) {
        const chip = el("button", "chip", `${d.short_name} GW${d.gw}`);
        chip.title = `open ${d.short_name} ${d.is_home ? "v" : "at"} `
          + `${d.opponent}, GW${d.gw}`;
        chip.onclick = () => openDivergent(d);
        who.appendChild(chip);
      }
      row.appendChild(who);
      list.appendChild(row);
    }
    tornCard.appendChild(list);
    if (groups.length > 5)
      tornCard.appendChild(el("p", "sub",
        `${groups.length - 5} more torn opponent-venues in this window; every one `
        + "is marked on its own cell's seam above."));
  }

  /* divergent[] grouped by (opponent, venue), largest |gap| first. The rows
     inside a group keep the panel's served order. */
  function tornGroups() {
    const groups = new Map();
    for (const d of M.divergent) {
      const key = `${d.opponent_code}|${d.is_home}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(d);
    }
    return [...groups.values()].sort((a, b) =>
      Math.max(...b.map(d => Math.abs(d.gap || 0)))
      - Math.max(...a.map(d => Math.abs(d.gap || 0))));
  }

  /* A divergent row names its fixture by ids; the drawer wants the view-model
     objects, so join back through the board. */
  function openDivergent(d) {
    const t = M.teams.find(x => x.code === num(d.team_code));
    if (!t) return;
    const slot = t.byGw.get(d.gw);
    const c = slot && slot.opps.find(o => o.fixtureId === num(d.fixture_id));
    if (c) openFixture(t, slot, c);
  }

  /* -------------------------------------------- league shape (kept) ---
     TEAM QUALITY — the thing the board deliberately holds constant, drawn so
     nobody mistakes a fixture run for a good team. The occlusion argument
     that killed the fixture-ease map does not bite here: the fitted ratings
     span ~0.85 goals on each axis, so twenty 20px marks sit in a 560px frame
     with room. The defensive axis is flipped so "good at both" is one
     corner; club identity IS the mark, because a scatter mark stands for the
     entity — the never-in-cells rule is about the data field, not this. */
  function renderShape() {
    shapeCard.textContent = "";
    shapeCard.hidden = false;
    shapeCard.open = foldOpen.shape;
    const withRating = M.teams.filter(t => t.rating
      && num(t.rating.attack) != null && num(t.rating.defence) != null);
    if (!withRating.length) {
      shapeCard.appendChild(el("summary", null, "League shape: no fitted ratings in this payload"));
      shapeCard.appendChild(namedGap("No fitted ratings in this payload.",
        gapText(
          "The map draws ", codeSpan("rating.attack"), " and ",
          codeSpan("rating.defence"), " per club, which ride along with ",
          codeSpan("fixture_board"), ". A payload without a stored fit carries neither, so "
          + "there is no quality to place; and the page will not infer one "
          + "from blended difficulties.")));
      return;
    }

    const cal = M.res.calibration || {};
    const model = cal.model || null, emp = cal.empirical || null;
    const ratios = [model && num(model.ratio_attack), model && num(model.ratio_defence),
                    emp && num(emp.outfield_ratio)].filter(v => v != null);
    /* asset-picking context, not deadline flow: the card itself is the fold,
       and its summary carries the one claim a closed card owes the reader */
    shapeCard.appendChild(el("summary", null,
      "League shape: club strength, club by club"));
    const disc = shapeCard;
    disc.appendChild(el("p", "sub",
      "The fitted attack and defence ratings behind every colour above: team "
      + "quality, which the board deliberately holds constant. This is the map "
      + "you pick assets on; the board is the tie-breaker. "
      + (ratios.length
        ? ""
        : "The calibration that would size it against the fixture swing is "
          + "not served, so no ratio is claimed here.")));

    const W = 560, H = 340, P = 40;
    const xs = withRating.map(t => num(t.rating.attack));
    const ys = withRating.map(t => -num(t.rating.defence));  // up = tighter
    const ext = a => {
      const lo = Math.min(...a, 0), hi = Math.max(...a, 0);
      const pad = (hi - lo) * 0.14 || 0.1;
      return [lo - pad, hi + pad];
    };
    const [x0, x1] = ext(xs), [y0, y1] = ext(ys);
    const sx = v => P + (v - x0) / (x1 - x0) * (W - 2 * P);
    const sy = v => H - P - (v - y0) / (y1 - y0) * (H - 2 * P);

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label",
      "fitted attack rating against fitted defence rating, one mark per club");
    const mk = (tag, attrs, text) => {
      const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      if (text != null) n.textContent = text;
      return n;
    };
    /* very faint washes on the two pure quadrants only — ≤4% mixes of the
       page's own diverging hues, so they survive both themes */
    svg.appendChild(mk("rect", { class: "wash good", x: sx(0), y: P,
      width: Math.max(0, W - P - sx(0)), height: Math.max(0, sy(0) - P) }));
    svg.appendChild(mk("rect", { class: "wash bad", x: P, y: sy(0),
      width: Math.max(0, sx(0) - P), height: Math.max(0, H - P - sy(0)) }));
    /* the crosshair IS league average; everything else stays recessive */
    svg.appendChild(mk("line", { class: "ax", x1: sx(0), x2: sx(0), y1: P, y2: H - P }));
    svg.appendChild(mk("line", { class: "ax", x1: P, x2: W - P, y1: sy(0), y2: sy(0) }));
    for (const v of [-0.25, 0.25]) {
      if (v > x0 && v < x1) {
        svg.appendChild(mk("line", { class: "tick", x1: sx(v), x2: sx(v),
          y1: H - P, y2: H - P + 4 }));
        svg.appendChild(mk("text", { class: "ticklab", x: sx(v), y: H - P + 13,
          "text-anchor": "middle" }, (v > 0 ? "+" : "−") + "0.25"));
      }
      if (v > y0 && v < y1) {
        svg.appendChild(mk("line", { class: "tick", x1: P - 4, x2: P,
          y1: sy(v), y2: sy(v) }));
        /* same quantity, same chart, ONE number format — "−0.25" on both
           axes; the y-axis's clipped "−.25" was a second format for free */
        svg.appendChild(mk("text", { class: "ticklab", x: P - 6, y: sy(v) + 3,
          "text-anchor": "end" }, (v > 0 ? "+" : "−") + "0.25"));
      }
    }
    const q = (x, y, anchor, text) =>
      svg.appendChild(mk("text", { class: "quad", x, y, "text-anchor": anchor }, text));
    q(W - P - 4, P + 12, "end", "STRONG ATTACK · TIGHT DEFENCE");
    q(P + 4, P + 12, "start", "BLUNT · TIGHT");
    q(W - P - 4, H - P - 6, "end", "STRONG ATTACK · LEAKY");
    q(P + 4, H - P - 6, "start", "BLUNT · LEAKY");
    svg.appendChild(mk("text", { class: "axlab", x: W - P, y: H - 6,
      "text-anchor": "end" }, "attack strengthens to the right"));
    svg.appendChild(mk("text", { class: "axlab", x: 6, y: P - 8 },
      "defence tightens upward"));

    const wrap = el("div", "fx-shape");
    wrap.appendChild(svg);

    /* label collision: keep every crest, drop only the LABEL of the
       lower-ranked club — deterministic by fitted rank sum */
    const rankSum = t => (num(t.rating.attack_rank) ?? 99)
                       + (num(t.rating.defence_rank) ?? 99);
    const keepLabel = new Set(); const placed = [];
    for (const t of withRating.slice().sort((a, b) => rankSum(a) - rankSum(b))) {
      const x = sx(num(t.rating.attack)), y = sy(-num(t.rating.defence));
      if (!placed.some(pt => Math.abs(pt.x - x) < 56 && Math.abs(pt.y - y) < 17)) {
        keepLabel.add(t.code); placed.push({ x, y });
      }
    }
    /* marks in attack-rank order, so tab order reads best-attack first */
    const ordered = withRating.slice().sort((a, b) =>
      (num(a.rating.attack_rank) ?? 99) - (num(b.rating.attack_rank) ?? 99));
    for (const t of ordered) {
      const x = sx(num(t.rating.attack)), y = sy(-num(t.rating.defence));
      const b = el("button", "mark" + (t.rating.is_promoted ? " promoted" : ""));
      b.style.left = `${(x / W * 100).toFixed(2)}%`;
      b.style.top = `${(y / H * 100).toFixed(2)}%`;
      b.appendChild(crest(t.code, t.short, "s20"));
      if (keepLabel.has(t.code)) b.appendChild(el("span", "lbl", t.short));
      /* the styled hover card is the instant, keyboard-reachable fallback for
         every mark — the label-collision pass drops LABELS, never data */
      hover.attach(b, () => [
        t.name,
        `attack ${sgn2(num(t.rating.attack))} · defence ${sgn2(num(t.rating.defence))} `
          + "goals vs league average, per match",
        num(t.rating.attack_rank) != null
          ? `club strength: ${ord(t.rating.attack_rank)} best attack, `
            + `${ord(t.rating.defence_rank)} best defence of ${withRating.length}` : null,
        num(t.rating.matches_seen) != null
          ? `${t.rating.matches_seen} matches in the fit` : null,
        t.rating.is_promoted
          ? "promoted this season: few Premier League matches in the fit, so its rating leans on the league average; read this mark gently" : null,
        "click for the club's schedule",
      ].filter(Boolean).join("\n"));
      b.onclick = () => openClub(t);
      wrap.appendChild(b);
    }
    disc.appendChild(wrap);
    disc.appendChild(el("p", "sub",
      "Both axes are goals versus a league-average opponent, per match, from "
      + "the same fit as every colour above. The crosshair is league average; "
      + "a dashed ring is a club promoted this season, whose rating leans on the league average because few matches are in the fit."));
  }

  /* ------------------------------------------------------- the drawer ---
     A match preview in five acts behind a sticky chip-nav that SCROLLS,
     never hides — hiding a hard-won section behind a tab is how sections
     die. Verdict decides the transfer; Market shows the gap; People and
     Record explain it; Provenance says where every number came from. */

  function mastFreshChip(label, text, missing, title) {
    const chip = el("span", "fx-inchip" + (missing ? " missing" : ""));
    chip.appendChild(el("b", null, label));
    chip.appendChild(el("span", "age", text));
    if (title) chip.title = title;
    return chip;
  }

  /* Masthead: identity, then age ABOVE the numbers, as everywhere else on
     the page, through app.js's one `drawerHead`. The "✕" this file drew is
     the shared Close control now (R41). `rightCrest` is the second 34px
     crest for a fixture, or null for the club drawer; the shared head has no
     slot for it, so it is inserted beside the title rather than forking the
     component. */
  function masthead(leftCrest, title, sub, rightCrest) {
    const head = drawerHead(title, sub,
      { face: leftCrest, onClose: closeDrawer });
    head.classList.add("fx-dh");
    if (rightCrest) {
      const dh = head.querySelector(".dhead");
      dh.insertBefore(rightCrest, dh.lastElementChild);
    }
    return head;
  }

  function lensBars(c, unramped, who) {
    const box = el("div", "fx-lens" + (unramped ? " unramped" : ""));
    const one = (k, v, rank) => {
      const r = el("div", "fx-lensrow");
      /* The row is named for the club these numbers belong to. Without it the
         sentence led with the OPPONENT ("LEE at home is 25th easiest...") and
         read as the opponent's number, which is the opposite of what it is. */
      r.appendChild(el("span", "lk", who ? `${who} ${k}` : k));
      const track = el("div", "fx-track");
      if (v != null) {
        const frac = Math.max(-1, Math.min(1, v / M.scale.dom));
        const fill = el("div", "fx-fill "
          + (unramped ? "" : (cls(v, M.scale.dom) || "fx-d3")));
        if (frac >= 0) { fill.style.left = "50%"; fill.style.width = `${frac * 50}%`; }
        else { fill.style.right = "50%"; fill.style.width = `${-frac * 50}%`; }
        track.appendChild(fill);
      }
      r.appendChild(track);
      const pop = num(M.res.scale && M.res.scale.population) || M.teams.length * 2;
      /* No "easier"/"harder" per row: the key above says once what the sign
         means, and repeating it on every line is the wordiness the key was
         supposed to remove, not add to. */
      r.appendChild(el("span", "lv",
        (v == null ? "–" : sgn2(v))
        + (rank != null ? ` · ${oppPair(c.opponent, c.isHome)} ${ord(rank)}/${pop}` : "")));
      return r;
    };
    if (c.easeAtt != null || c.easeDef != null) {
      box.appendChild(one("attackers", c.easeAtt, c.rankAtt));
      box.appendChild(one("defenders", c.easeDef, c.rankDef));
    } else {
      box.appendChild(one("blended", c.easeBlend, null));
    }
    return box;
  }

  async function openFixture(t, slot, c) {
    clearInputSel();
    drawer.textContent = "";
    openDrawer();

    drawer.appendChild(masthead(
      crest(t.code, t.short, "s34"),
      `${t.short} ${c.isHome ? "v" : "at"} ${c.opponent}`,
      [`GW${slot.gw}`, kickoffText(c.kickoff), c.isHome ? "home" : "away",
       slot.double ? "double gameweek" : null].filter(Boolean).join(" · "),
      crest(c.oppCode, c.opponent, "s34")));

    const fresh = el("div", "fx-mastfresh");
    const ratings = (M.res.inputs || []).find(i => /rating/i.test(String(i.name || "")));
    if (ratings) {
      const h = num(ratings.age_hours) ?? ageHours(ratings.as_of);
      const fd = parseTs(ratings.as_of);
      fresh.appendChild(mastFreshChip("ratings",
        (h == null ? "age unknown" : oldPhrase(h))
        + (fd ? ` · fit includes matches through ${fd.toLocaleDateString(undefined,
              { day: "numeric", month: "short" })}` : ""),
        false, String(ratings.detail || "")));
    }
    fresh.appendChild(mastFreshChip("market",
      c.marketState !== "priced"
        ? (c.marketState || "absent")
        : [c.marketAgeH != null ? oldPhrase(c.marketAgeH) : "age unknown",
           c.nBooks != null ? `${c.nBooks} books` : null].filter(Boolean).join(" · "),
      c.marketState !== "priced",
      "the market is never blended into any difficulty; see the Market act"));
    drawer.appendChild(fresh);

    const acts = {};
    const nav = el("nav", "fx-actnav");
    for (const [id, label] of [["verdict", "Verdict"], ["market", "Market"],
        ["people", "People"], ["record", "Record"], ["prov", "Provenance"]]) {
      const b = el("button", null, label);
      b.onclick = () => acts[id] && acts[id].scrollIntoView({
        block: "start",
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto" : "smooth",
      });
      nav.appendChild(b);
    }
    drawer.appendChild(nav);
    const act = (id, label) => {
      const sec = el("section", "fx-act");
      sec.appendChild(el("h2", "fx-acthead", label));
      acts[id] = sec;
      drawer.appendChild(sec);
      return sec;
    };

    /* ---- Act 1 · VERDICT — decides the transfer; board payload, so it
       renders before the detail fetch returns ---- */
    const A1 = act("verdict", "Verdict");
    /* A FIXTURE HAS TWO SIDES, so this shows two sides. Naming the club fixed
       "whose number is this" but left "what about the other one", and putting
       that behind a swap button still made the reader click and hold one set
       of numbers in their head to compare. Both clubs, both lenses, one view.
       Nothing is derived here: the opposite side is the opposite club's own
       cell for this gameweek, read straight off the same payload. */
    const foe = M.teams.find(x => x.short === c.opponent);
    const foeSlot = foe && foe.byGw && foe.byGw.get(slot.gw);
    const foeCell = foeSlot && !foeSlot.blank
      && (foeSlot.opps || []).find(o => o.opponent === t.short);

    A1.appendChild(el("h2", null, "The two answers, for both clubs"));
    /* The three numbers a reader opened this drawer for, on one line, before
       any prose. Everything below is the same three with their working shown;
       this is so the drawer answers before it explains. */
    const head = el("div", "fx-headline");
    const hv = (lab, val, title) => {
      const b = el("span", "hv");
      b.appendChild(el("i", null, lab));
      b.appendChild(el("b", null, val));
      if (title) b.title = title;
      head.appendChild(b);
    };
    head.appendChild(el("span", "who", t.short));
    if (eAtt(c) != null) hv("ATT", sgn2(eAtt(c)), `${t.short}'s attackers`);
    if (eDef(c) != null) hv("DEF", sgn2(eDef(c)), `${t.short}'s defenders`);
    if (c.pCleanSheet != null)
      hv("CS", `${Math.round(c.pCleanSheet * 100)}%`,
        `chance ${t.short} keep a clean sheet`);
    if (head.children.length > 1) A1.appendChild(head);
    if (c.easeAtt != null || c.easeDef != null) {
      const k = el("p", "fx-povkey");
      k.appendChild(el("b", "pos", "+ easier"));
      k.appendChild(document.createTextNode(" · "));
      k.appendChild(el("b", "neg", "− harder"));
      k.appendChild(document.createTextNode(
        ", for the club it is listed under. The two sides do not mirror: each "
        + "faces a different opponent."));
      A1.appendChild(k);
    }
    const side = (club, cell, venueWord) => {
      const h = el("div", "fx-sidehead");
      h.appendChild(crest(club.code, club.short, "s16"));
      h.appendChild(el("b", null, club.name || club.short));
      h.appendChild(el("span", "v", venueWord));
      A1.appendChild(h);
      A1.appendChild(lensBars(cell, false, club.short));
    };
    side(t, c, c.isHome ? "at home" : "away");
    if (foeCell && foe) {
      side(foe, foeCell, foeCell.isHome ? "at home" : "away");
    } else if (foe) {
      A1.appendChild(el("p", "sub",
        `${foe.short}'s own cell for this gameweek is not in this payload, so `
        + "their side is not shown rather than inferred from this one."));
    }
    A1.appendChild(el("p", "sub",
      (c.easeAtt != null && c.easeDef != null)
        ? `${M.scale.unit}. Same axis as the grid; neither bar is an average `
          + "of the other."
        : c.easeBlend != null
          ? "One blended number, because that is all the payload carries. It is "
            + "the average of two different questions."
          : "The panel has no fitted rating for this fixture. The schedule is "
            + "still a fact; the difficulty is not known."));

    /* Three sentences to make one point is how a drawer becomes unreadable.
       The claim is: the colour ignores your club. Say that, then stop. */
    A1.appendChild(el("h2", null, "With this club's own strength added"));
    const asm = el("p", "sub");
    asm.textContent = `The colour ignores ${t.short} and asks only what `
      + `${c.opponent} does ${c.isHome ? "away" : "at home"}, so every club `
      + `${c.isHome ? "visiting" : "hosting"} ${c.opponent} shares it. These `
      + `numbers add ${t.short} back.`;
    A1.appendChild(asm);

    const rel = c.raw && (c.raw.relative_attack != null || c.raw.relative_defence != null);
    if (rel) {
      /* UNRAMPED on purpose: the payload's domain_note says the ramp's domain
         is calibrated on the opponent-only population, and fixture_specific
         is a wider distribution. Colouring it with the grid's ramp borrowed
         a calibration it does not have. */
      A1.appendChild(lensBars({
        easeAtt: num(c.raw.relative_attack), easeDef: num(c.raw.relative_defence),
        rankAtt: null, rankDef: null, easeBlend: null,
      }, true, t.short));
      A1.appendChild(el("p", "sub",
        `with ${t.short}'s strength added back; a different number, `
        + "deliberately not on the grid's ramp (its domain is calibrated on "
        + "the opponent-only population)."));
    } else {
      A1.appendChild(namedGap("The fixture-specific number is not in this payload.",
        gapText(
          "This is the drawer's job and it cannot do it yet: the panel returns "
          + "opponent-only ease and no relative (own-club-adjusted) figure. It "
          + "would come from the same fit, with our own ",
          codeSpan("attack_O"), " and ", codeSpan("defence_O"),
          " added back in place of the league-average anchor. The page will not "
          + "compute it in the browser, because a number modelled in the UI is a "
          + "number nobody can audit.")));
    }

    A1.appendChild(el("h2", null, "The match, as probabilities"));
    if (c.pCleanSheet != null || c.raw.p_over_2_5 != null) {
      const k2 = el("div", "fx-kv");
      const add2 = (k, v) => { k2.appendChild(el("span", "k", k)); k2.appendChild(el("span", "v", v)); };
      if (c.pCleanSheet != null) add2(`P(${t.short} clean sheet)`, `${Math.round(c.pCleanSheet * 100)}%`);
      if (c.pCleanSheetMkt != null) add2("P(clean sheet), market", `${Math.round(c.pCleanSheetMkt * 100)}%`);
      if (c.raw.p_concede_2plus != null) add2("P(concede 2+)", `${Math.round(c.raw.p_concede_2plus * 100)}%`);
      if (c.raw.p_over_2_5 != null) add2("P(over 2.5)", `${Math.round(c.raw.p_over_2_5 * 100)}%`);
      A1.appendChild(k2);
      if (c.pCleanSheet != null && c.pCleanSheetMkt != null
          && Math.abs(c.pCleanSheet - c.pCleanSheetMkt) > 0.03)
        A1.appendChild(el("p", "sub",
          "The model and the market disagree by more than 3 points. They are "
          + "two estimators with different biases, so both are shown and neither "
          + "is averaged; the gap itself is the signal."));
    } else {
      A1.appendChild(namedGap("No score matrix in this payload.", gapText(
        "Clean-sheet and over/under probabilities come from the score matrix in ",
        codeSpan("fpl_edge/models/team_goals/scoreline.py"),
        ", which the panel does not yet return. The rates that would feed it "
        + "are the same two numbers at the top of this drawer.")));
    }

    /* ---- Act 2 · MARKET — the gap is the signal. The board's market-state
       branches render now; the detail's disagreement rows join on arrival. */
    const A2 = act("market", "Market");
    const A2detail = el("div");        // filled by detailActs
    A2.appendChild(A2detail);
    if (c.marketWeight == null && c.marketState === "priced") {
      // The market is present and dated; it is deliberately not in the colour.
      const kvm = el("div", "fx-kv");
      kvm.appendChild(el("span", "k", "market"));
      kvm.appendChild(el("span", "v",
        `${c.nBooks != null ? c.nBooks + " books, " : ""}`
        + `${c.marketAgeH != null ? oldPhrase(c.marketAgeH) : "age unknown"}`));
      A2.appendChild(kvm);
      A2.appendChild(kvNote(
        "Priced, and deliberately not blended into the colour.",
        gapText(
          "Not averaged into the colour: the blend weight in ", codeSpan("blend.py"),
          " has never been tuned out of sample. Model and market sit side by "
          + "side and the gap is the finding.")));
    } else if (c.marketWeight == null && c.marketState != null && c.marketState !== "priced") {
      A2.appendChild(namedGap(`No price for this fixture (${c.marketState}).`,
        gapText(
          c.raw.market_reason
            ? String(c.raw.market_reason) + " "
            : "No book in the pull covers this fixture. ",
          "The number above is the fitted model alone.")));
    } else if (c.marketWeight == null) {
      A2.appendChild(namedGap("No market leg, and no market age.", gapText(
        "The payload carries no ", codeSpan("market_weight"), " and no ",
        codeSpan("market_age_hours"), ". A price whose age is unknown is not "
        + "rendered here as current, greyed, or at all; so this number is the "
        + "fitted model alone. That is a disclosure, not a defect: the model is "
        + "the part that is auditable today.")));
    } else if (c.marketWeight === 0) {
      A2.appendChild(namedGap("Market weight 0.00.", gapText(
        c.marketAgeH != null
          ? `The newest quote behind this fixture is ${oldPhrase(c.marketAgeH)}, past the cutoff, `
          : "No usable quote covers this fixture, ",
        "so the market contributes nothing to the colour. The number above is "
        + "the fitted model alone.")));
    } else {
      const kvm = el("div", "fx-kv");
      const addM = (k, v) => { kvm.appendChild(el("span", "k", k)); kvm.appendChild(el("span", "v", v)); };
      addM("market weight", fmt2(c.marketWeight));
      if (c.marketAgeH != null) addM("newest quote", oldPhrase(c.marketAgeH));
      if (c.nBooks != null) addM("books", String(c.nBooks));
      if (c.marketResidual != null) addM("refit residual", fmt2(c.marketResidual));
      A2.appendChild(kvm);
    }

    const A3 = act("people", "People");
    const A4 = act("record", "Record");

    /* ---- Act 5 · PROVENANCE — where the number came from ---- */
    const A5 = act("prov", "Provenance");
    A5.appendChild(el("h2", null, "Where the number came from"));
    const kv = el("div", "fx-kv");
    const addKv = (k, v) => { kv.appendChild(el("span", "k", k)); kv.appendChild(el("span", "v", v)); };
    addKv("panel", scriptUsed);
    /* "mu_O" and "lambda_O" are the symbols in the model file, not words. The
       reader wants to know which club's goals each number is. */
    if (c.raw.attack_xg != null)
      addKv("goals this club is expected to score", fmt2(c.raw.attack_xg));
    if (c.raw.defence_xg != null)
      addKv("goals the opponent is expected to score", fmt2(c.raw.defence_xg));
    if (c.blended != null)
      addKv("the old single-number difficulty", fmt2(c.blended)
        + " (0-1, kept only so this page can prove it is not using it)");
    if (c.priorShare != null) addKv("rating from prior", `${Math.round(c.priorShare * 100)}%`);
    A5.appendChild(kv);

    await detailActs(t, slot, c, { A2: A2detail, A3, A4, A5 });
  }

  /* The this-week detail. Asks `fixture_detail` and renders exactly what
     comes back into the acts; every section that has nothing says WHICH
     table or script is missing, because a named gap is information and
     whitespace is not. The flattened shape feeds the row renderers; the raw
     result feeds the fields the adapter never carried (market meta,
     disagreement, per-club form, inputs). */
  async function detailActs(t, slot, c, A) {
    const load = el("p", "sub", "loading match detail…");
    drawer.appendChild(load);
    const params = {};
    if (c.fixtureId != null) params.fixture_id = c.fixtureId;
    else { params.gw = slot.gw; if (t.code != null) params.team_code = t.code; }
    const r = await tryPanel("fixture_detail", params);
    if (!drawer.classList.contains("open")) return;
    load.remove();

    /* A FAILED FETCH never wears absence's clothes: when the panel 500s the
       sections say "couldn't load — retry", not "nothing filed" — presenting
       an error as honest absence is the one lie this page must never tell. */
    if (!r.ok && !r.missing) {
      A.A2.appendChild(fetchFailBox("the match detail", errReason(r.error),
        () => openFixture(t, slot, c)));
      A.A3.appendChild(el("p", "fx-quiet fail",
        "not loaded; the match-detail fetch failed; retry in the Market act"));
      A.A4.appendChild(el("p", "fx-quiet fail",
        "not loaded; the match-detail fetch failed; retry in the Market act"));
      crossLinks(A.A5, t, c);
      return;
    }

    const raw = (r.ok && r.result && !r.result.empty) ? r.result : null;
    const D = flattenDetail(raw);

    marketAct(A.A2, raw);
    peopleAct(A.A3, D, raw);
    recordAct(A.A4, D, raw);

    /* provenance extras the detail carries */
    /* A note about upper-case selections in fact_odds versus lower-case in the
       de-vigger is a maintenance fact, not something a reader of a fixture
       needs in prose. It stays -- it is live in this payload and deleting it
       would hide a real workaround -- but folded. */
    if (raw && raw.market && raw.market.casing_workaround) {
      const cw = el("details", "fx-how");
      cw.appendChild(el("summary", null, "A workaround is active in this payload"));
      cw.appendChild(el("p", "sub", String(raw.market.casing_workaround)));
      A.A5.appendChild(cw);
    }
    if (raw && Array.isArray(raw.inputs) && raw.inputs.length) {
      A.A5.appendChild(el("h2", null, "Inputs"));
      const kv = el("div", "fx-kv");
      for (const i of raw.inputs) {
        kv.appendChild(el("span", "k", String(i.name || "–")));
        const h = num(i.age_hours) ?? ageHours(i.as_of);
        kv.appendChild(el("span", "v",
          [h == null ? "age unknown" : oldPhrase(h),
           i.detail ? String(i.detail) : null].filter(Boolean).join(" · ")));
      }
      A.A5.appendChild(kv);
    }
    crossLinks(A.A5, t, c);

    if (!r.ok && r.missing) {
      drawer.appendChild(el("p", "sub",
        "fixture_detail is not registered, so every section above is a named "
        + "gap rather than a fetch failure."));
    }
  }

  /* Act 2 fill — the gap is the signal. `disagreement[]` is served today
     and was never rendered before this rebuild; every row carries BOTH
     estimators for the same quantity, so the dumbbell draws the gap as a
     length instead of asking the reader to subtract. This is the page's ONLY
     dumbbell: the metaphor means model-vs-market and nothing else. Age sits
     in the header, above the numbers, as everywhere. */
  function marketAct(host, raw) {
    const mk = raw && raw.market && raw.market.available !== false
      ? raw.market : null;
    if (mk) {
      const meta = [
        mk.state ? `state ${mk.state}` : null,
        mk.n_books != null ? `${mk.n_books} books` : null,
        num(mk.age_hours) != null ? oldPhrase(num(mk.age_hours)) : null,
        mk.devig_method ? `devig ${mk.devig_method}` : null,
        num(mk.overround_h2h) != null ? `overround ${fmt2(mk.overround_h2h)}` : null,
      ].filter(Boolean).join(" · ");
      if (meta) host.appendChild(el("p", "fx-mktmeta", meta));
    }

    const dis = raw && Array.isArray(raw.disagreement) && raw.disagreement.length
      ? raw.disagreement : null;
    if (!dis) {
      host.appendChild(quietGap(
        "market: nothing fetched for this fixture; the state note below "
        + "still says what the board knew"));
      return;
    }

    const box = el("div", "fx-gaplines");
    for (const d of dis) {
      const m = num(d.model), k = num(d.market);
      if (m == null || k == null) continue;
      const row = el("div", "gapline" + (d.flagged ? " flagged" : ""));
      row.appendChild(el("span", "k", String(d.metric || "–")));
      const db = el("span", "db");
      db.style.setProperty("--m", String(m * 100));
      db.style.setProperty("--k", String(k * 100));
      const dm = el("i", "model");
      dm.title = `model ${(m * 100).toFixed(1)}%`;
      const dk = el("i", "market");
      dk.title = `market ${(k * 100).toFixed(1)}%`
        + (num(d.market_age_hours) != null
            ? ` · ${oldPhrase(num(d.market_age_hours))}` : "");
      db.append(dm, dk);
      row.appendChild(db);
      const gp = num(d.gap_pp);
      row.appendChild(el("span", "v",
        gp == null ? "–" : `${gp >= 0 ? "+" : "−"}${Math.abs(gp).toFixed(1)}pp`));
      box.appendChild(row);
    }
    host.appendChild(box);
    const key = el("p", "fx-gapkey");
    key.append(el("i", "dot m"), document.createTextNode(" model · "),
               el("i", "dot k"), document.createTextNode(
                 " market; never averaged; the gap is the finding"));
    host.appendChild(key);
    if (raw.derived_clean_sheet && raw.derived_clean_sheet.warning)
      host.appendChild(el("p", "sub", String(raw.derived_clean_sheet.warning)));

    /* the match result as two thin stacked bars, model above market — one
       shared 0–100% axis, labels on the segments, nothing averaged */
    const hda = ["P(home win)", "P(draw)", "P(away win)"]
      .map(name => dis.find(d => d.metric === name));
    if (hda.every(d => d && num(d.model) != null && num(d.market) != null)) {
      const hn = raw.home ? raw.home.short_name : "home";
      const an = raw.away ? raw.away.short_name : "away";
      const bar = (which, probs) => {
        const row = el("div", "fx-hda");
        row.appendChild(el("span", "k", which));
        const track = el("span", "bar");
        const seg = (v, cls2, label) => {
          const sg = el("span", "seg " + cls2);
          sg.style.width = `${(v * 100).toFixed(1)}%`;
          sg.title = `${label} ${(v * 100).toFixed(1)}% (${which})`;
          if (v >= 0.14) sg.textContent = `${label} ${Math.round(v * 100)}%`;
          return sg;
        };
        track.append(seg(probs[0], "h", hn), seg(probs[1], "d", "draw"),
                     seg(probs[2], "a", an));
        row.appendChild(track);
        return row;
      };
      host.appendChild(bar("model", hda.map(d => num(d.model))));
      host.appendChild(bar("market", hda.map(d => num(d.market))));
    }
  }

  /* Act 3 · PEOPLE — two columns, home | away, stacking narrow. The rows
     come out of flattenDetail labelled "name · CLUB"; the label was pushed on
     when the by_team shape was flattened, so splitting on it here loses
     nothing the payload did not already say. */
  function peopleAct(host, D, raw) {
    const names = {
      home: raw && raw.home ? (raw.home.short_name || raw.home.name) : null,
      away: raw && raw.away ? (raw.away.short_name || raw.away.name) : null,
    };
    const bySide = (rows, key) => {
      const out = { home: [], away: [] };
      for (const row of rows || []) {
        const v = String(row[key] || "");
        for (const side of ["home", "away"]) {
          const suffix = " · " + names[side];
          if (names[side] && v.endsWith(suffix)) {
            out[side].push({ ...row, [key]: v.slice(0, -suffix.length) });
          }
        }
      }
      return out;
    };
    const news = D && D.team_news ? bySide(D.team_news, "player") : null;
    const xi = D && D.predicted_lineup ? bySide(D.predicted_lineup, "name") : null;
    const sp = D && D.set_pieces ? bySide(D.set_pieces, "duty") : null;

    if (news || xi || sp) {
      const grid = el("div", "fx-people");
      for (const side of ["home", "away"]) {
        const col = el("div", "col");
        col.appendChild(el("b", "club", names[side] || side));
        if (news) col.appendChild(peopleNews(news[side], names[side]));
        if (xi) col.appendChild(peopleXi(xi[side], names[side]));
        if (sp) col.appendChild(peopleSp(sp[side], names[side]));
        grid.appendChild(col);
      }
      host.appendChild(grid);
      if (sp && raw && raw.intel && raw.intel.framing)
        host.appendChild(el("p", "sub", String(raw.intel.framing)));
    }

    /* honest absence, one quiet line per section — the WHY is still true and
       still said, just no longer a 40-word apology on the scan path */
    if (!news) host.appendChild(quietGap(
      "team news: nothing fetched for this fixture; not fetched, never "
      + "“nobody is injured”"));
    if (!xi) host.appendChild(quietGap(
      raw && raw.predicted_lineups && raw.predicted_lineups.unavailable
        ? `predicted XI: ${String(raw.predicted_lineups.unavailable)}`
        : "predicted XI: none published for this fixture yet (providers "
          + "publish ~T−48h)"));
    if (!sp) host.appendChild(quietGap(
      "set-piece duty: in the warehouse, not in this payload yet"));
  }

  function peopleBlock(label, rows, clubName, renderRows) {
    const box = el("div", "pb");
    box.appendChild(el("span", "pl", label));
    if (!rows || !rows.length)
      box.appendChild(el("p", "sub", `nothing filed for ${clubName || "this club"}`));
    else box.appendChild(renderRows(rows));
    return box;
  }
  function peopleNews(rows, clubName) {
    return peopleBlock("Team news", rows, clubName, rs => {
      const box = el("div", "fx-newslist");
      for (const n of rs.slice(0, 8)) {
        const row = el("div", "nrow");
        row.appendChild(el("b", null, n.player || "–"));
        if (n.chance != null) row.appendChild(el("span", "chance", `${n.chance}%`));
        const meta = [n.status_text || null,
          oldStamp(n.as_of)].filter(Boolean).join(" · ");
        if (meta) row.appendChild(el("span", "meta", meta));
        box.appendChild(row);
      }
      return box;
    });
  }
  function peopleXi(rows, clubName) {
    return peopleBlock("Predicted XI", rows, clubName, rs => {
      /* A column of identical "expected" values is not information: names as
         a line, annotation only where the status differs from the default. */
      const start = [], other = [];
      for (const p of rs) {
        const role = p.role && p.role !== "expected" ? String(p.role) : null;
        const nm = role ? `${p.name} (${role})` : p.name;
        if (p.starts !== false) start.push(nm);
        else if (role) other.push(nm);
      }
      const box = el("div", "fx-xi");
      const h = el("div", "fx-xi-club");
      h.appendChild(el("span", "n", `${start.length} predicted to start`));
      box.appendChild(h);
      if (start.length) box.appendChild(el("p", "fx-xi-names", start.join(", ")));
      if (other.length)
        box.appendChild(el("p", "fx-xi-other", "not starting: " + other.join(", ")));
      return box;
    });
  }
  function peopleSp(rows, clubName) {
    return peopleBlock("Set pieces", rows, clubName, rs => {
      const box = el("div", "fx-kv");
      for (const d of rs.slice(0, 8)) {
        box.appendChild(el("span", "k", d.duty || "duty"));
        box.appendChild(el("span", null, d.player || "–"));
      }
      return box;
    });
  }

  /* Act 4 · RECORD — what these clubs have actually done lately. */
  function recordAct(host, D, raw) {
    host.appendChild(el("h2", null, "Form"));
    if (raw && raw.form && (raw.form.home || raw.form.away)) {
      const box = el("div", "fx-formrec");
      for (const side of ["home", "away"]) {
        const f = raw.form[side], team = raw[side];
        if (!f || typeof f !== "object" || !team) continue;
        const row = el("div", "row");
        row.appendChild(el("b", null, team.short_name || side));
        const chip = formChipEl(f);
        if (chip) row.appendChild(chip);
        /* The rates print even when the residual is withheld: `unavailable`
           withholds the RESIDUAL, not the rates — the per-game figures are
           served and true, and dropping them to honour a caveat about a
           different number would discard real data. */
        const bits = [];
        if (num(f.xg_for_pg) != null) bits.push(`${fmt2(f.xg_for_pg)} xGF/gm`);
        if (num(f.xg_against_pg) != null) bits.push(`${fmt2(f.xg_against_pg)} xGA/gm`);
        if (num(f.window_matches) != null)
          bits.push(`${f.window_matches} match${f.window_matches === 1 ? "" : "es"}`);
        row.appendChild(el("span", "rates", bits.join(" · ")));
        box.appendChild(row);
      }
      host.appendChild(box);
      host.appendChild(el("p", "sub",
        "Style here is xG for and against and clean-sheet rate, split home and "
        + "away. No PPDA, field tilt or line height: that event data is not in "
        + "this warehouse. Style explains a fixture; it never enters the colour."));
    } else {
      host.appendChild(quietGap(
        "form: no style summary in this payload; and no PPDA or field tilt "
        + "anywhere, because that event data is not in this warehouse"));
    }

    host.appendChild(el("h2", null, "Previous meetings"));
    if (D && D.previous_meetings) {
      const box = el("div", "fx-kv");
      for (const m of D.previous_meetings.slice(0, 8)) {
        box.appendChild(el("span", "k", m.season || m.date || "–"));
        box.appendChild(el("span", "v",
          [m.score, m.xg ? `xG ${m.xg}` : null].filter(Boolean).join("  ")));
      }
      host.appendChild(box);
      host.appendChild(el("p", "sub",
        raw && raw.previous_meetings && raw.previous_meetings.caution
          ? String(raw.previous_meetings.caution)
          : "A handful of matches across several seasons, with different managers "
            + "and mostly different players, is not evidence about this one. "
            + "Head-to-head is the most over-read object in fixture analysis."));
    } else {
      host.appendChild(quietGap(
        "previous meetings: none in this payload; “never met” and “not "
        + "fetched” are indistinguishable here, and the page will not guess"));
    }

    host.appendChild(el("h2", null, "Creator team-talk"));
    if (D && D.creator_talk && D.creator_talk.length) {
      /* Two clubs are on screen, so every line names the one it is about --
         an unattributed opinion in a two-club drawer is worse than none. The
         claim is the summary and the quote is the receipt under it. */
      const box = el("div", "fx-talk");
      for (const q of D.creator_talk.slice(0, 8)) {
        const row = el("div", "fx-talk-row");
        const head = el("div", "fx-talk-head");
        if (q.entity_name) head.appendChild(el("b", null, String(q.entity_name)));
        if (q.topic)
          head.appendChild(el("span", "tag", String(q.topic).replace(/_/g, " ")));
        const meta = [q.creator,
                      oldStamp(q.published_at)]
          .filter(Boolean).join(" · ");
        if (meta) head.appendChild(el("span", "who", meta));
        row.appendChild(head);
        if (q.claim_text) row.appendChild(el("p", "fx-talk-claim", String(q.claim_text)));
        const said = q.quote || q.text;
        if (said) row.appendChild(el("p", "fx-talk-quote", `\u201C${said}\u201D`));
        box.appendChild(row);
      }
      host.appendChild(box);
    } else {
      host.appendChild(quietGap(
        "creator team-talk: nothing filed on either club in this window"));
    }

    host.appendChild(el("h2", null, "Press & scout links"));
    if (D && D.press_conference && D.press_conference.length) {
      const box = el("div");
      for (const q of D.press_conference.slice(0, 6)) {
        const line = el("p", "sub");
        const a = q.source_url ? el("a", "chip src", q.headline || "link") : el("b", null, q.headline || "–");
        if (q.source_url) { a.href = q.source_url; a.target = "_blank"; a.rel = "noopener noreferrer";
                            a.style.textDecoration = "none"; }
        line.appendChild(a);
        if (q.age_hours != null)
          line.appendChild(document.createTextNode(` · ${oldPhrase(q.age_hours)}`));
        if (q.confidence) line.appendChild(document.createTextNode(` · ${q.confidence}`));
        box.appendChild(line);
      }
      host.appendChild(box);
      host.appendChild(el("p", "sub",
        "FPL's own scout links. FPL publishes no timestamp, so the age is dated "
        + "to the first poll that carried them: an upper bound."));
    } else {
      host.appendChild(quietGap(
        "press & scout links: none reached this fixture"));
    }
  }

  function crossLinks(host, t, c) {
    host.appendChild(el("h2", null, "Elsewhere"));
    const links = el("div", "fx-links");
    const mk = (href, label, title) => {
      const a = el("a", "chip src", label);
      a.href = href; a.title = title; a.style.textDecoration = "none";
      return a;
    };
    links.appendChild(mk("#xpoints", `${t.short} projections`,
      "the per-player numbers for this club"));
    links.appendChild(mk("#template", `${t.short} ownership`,
      "an easy schedule everyone can see is priced into the field's transfers; the "
      + "same schedule on a 2%-owned club is an edge, on a 60%-owned club it is "
      + "insurance"));
    links.appendChild(mk("#creators", "creator coverage",
      "who has said what about these clubs"));
    host.appendChild(links);
    host.appendChild(el("p", "sub",
      "Difficulty is a fact about football; effective ownership is a fact about "
      + "managers. They are never folded into one number here."));
  }

  async function openClub(t) {
    clearInputSel();
    drawer.textContent = "";
    openDrawer();
    const n = M.teams.length;
    const rt = t.rating || {};
    const range = `GW${M.gws[0]}–GW${M.gws[M.gws.length - 1]}`;
    drawer.appendChild(masthead(
      crest(t.code, t.short, "s34"),
      t.name || t.short,
      `${range} · ${t.nFixtures} fixture`
      + `${t.nFixtures === 1 ? "" : "s"}`
      + (t.nBlanks ? ` · ${t.nBlanks} blank` : "")
      + (t.nDoubles ? ` · ${t.nDoubles} double` : ""),
      null));

    /* THE ONE SENTENCE a manager needs first: club strength and run difficulty
       side by side, in words, then the ranks that back them. Both ranks are
       served; the words are bands over them, and say which rank they mean. */
    const ar = num(rt.attack_rank), dr = num(rt.defence_rank);
    const hasClub = ar != null || dr != null;
    const hasRun = M.anySplit && (t.attRankH != null || t.defRankH != null);
    if (hasClub || hasRun) {
      const clubMean = hasClub ? ((ar ?? dr) + (dr ?? ar)) / 2 : null;
      const runMean = hasRun ? ((t.attRankH ?? t.defRankH) + (t.defRankH ?? t.attRankH)) / 2 : null;
      const lead = el("p", "fx-lead");
      const words = [hasClub ? `${tierClub(clubMean)} club` : null,
                     hasRun ? `${tierRun(runMean)} schedule` : null].filter(Boolean).join(", ");
      lead.appendChild(el("b", null, cap(words) + ": "));
      const parts = [];
      if (hasClub) parts.push(`${ord(ar)} best attack and ${ord(dr)} best defence of ${n} in the fit`);
      if (hasRun) parts.push(`${hasClub ? "facing " : ""}the ${ord(t.attRankH)} easiest schedule of ${n} `
        + `for its attackers and ${ord(t.defRankH)} easiest for its defenders over ${range}`);
      lead.appendChild(document.createTextNode(parts.join(", ") + "."));
      drawer.appendChild(lead);
    }
    const promoted = !!rt.is_promoted || (t.priorShare != null && t.priorShare > 0.4);
    if (promoted)
      drawer.appendChild(el("p", "sub",
        (t.priorShare != null
          ? `Promoted this season: ${Math.round(t.priorShare * 100)}% of this rating is the league average, not this club's own results`
          : "Promoted this season: few Premier League matches in the fit, so this rating leans on the league average")
        + "; read its colours gently."));

    drawer.appendChild(el("h2", null, "The schedule"));
    /* The owner read this list three times and still could not tell whose
       numbers they were. A legend elsewhere on the page does not fix that:
       the answer has to sit ON the numbers. So the club is named, the sign is
       spelled out, and each half of every pair wears its own tag. */
    const key = el("p", "fx-povkey");
    key.appendChild(document.createTextNode("All numbers are for "));
    key.appendChild(el("b", null, t.name || t.short));
    key.appendChild(document.createTextNode(". "));
    key.appendChild(el("b", "pos", "+ easier"));
    key.appendChild(document.createTextNode(" · "));
    key.appendChild(el("b", "neg", "− harder"));
    key.appendChild(document.createTextNode(" · ATT their attackers, DEF their defenders."));
    drawer.appendChild(key);
    const box = el("div", "fx-lens");
    for (const g of M.gws) {
      const slot = t.byGw.get(g);
      const r = el("div", "fx-lensrow");
      r.appendChild(el("span", "lk", `GW${g}`));
      if (slot.blank) {
        const w = el("span", "sub", "blank: no fixture, which is not a zero");
        r.appendChild(w); r.appendChild(el("span", "lv", "–"));
      } else {
        const c = slot.opps[0];
        const track = el("div", "fx-track");
        const v = c.easeAtt != null ? c.easeAtt : c.easeBlend;
        if (v != null) {
          const frac = Math.max(-1, Math.min(1, v / M.scale.dom));
          const fill = el("div", "fx-fill " + (cls(v, M.scale.dom) || "fx-d3"));
          if (frac >= 0) { fill.style.left = "50%"; fill.style.width = `${frac * 50}%`; }
          else { fill.style.right = "50%"; fill.style.width = `${-frac * 50}%`; }
          track.appendChild(fill);
        }
        r.appendChild(track);
        const lv = el("span", "lv");
        lv.appendChild(el("b", "opp", oppLabel(c)));
        if (c.easeAtt != null && c.easeDef != null) {
          lv.appendChild(el("i", "pair", `ATT ${sgn2(c.easeAtt)}`));
          lv.appendChild(el("i", "pair", `DEF ${sgn2(c.easeDef)}`));
        } else {
          lv.appendChild(el("i", "pair", sgn2(c.easeBlend)));
        }
        r.appendChild(lv);
      }
      box.appendChild(r);
    }
    drawer.appendChild(box);
    drawer.appendChild(el("p", "sub",
      M.anySplit
        ? `Bar: the attack number. Unit: ${M.scale.unit}. `
          + "A grid cell opens that match."
        : "One blended number per fixture; the split is unavailable in this "
          + "payload."));

    /* form and the fit both fold; each summary states its own finding */
    if (t.form && num(t.form.window_matches) != null) {
      const wm = t.form.window_matches;
      const fdisc = el("details", "fx-how");
      fdisc.appendChild(el("summary", null,
        `Form: ${wm} match${wm === 1 ? "" : "es"} since the fit`
        + (num(t.form.xg_for_pg) != null ? ` · xG for ${fmt2(t.form.xg_for_pg)}` : "")
        + (num(t.form.xg_against_pg) != null ? ` · against ${fmt2(t.form.xg_against_pg)} a game` : "")
        + (wm < 3 ? " · too few for a residual" : "")));
      const chip = formChipEl(t.form);
      if (chip) {
        const line = el("div", "fx-formrec");
        const row = el("div", "row");
        row.appendChild(chip);
        line.appendChild(row);
        fdisc.appendChild(line);
      }
      const k = el("div", "fx-kv");
      const add = (a, b) => { k.appendChild(el("span", "k", a)); k.appendChild(el("span", "v", b)); };
      add("matches in window", String(t.form.window_matches));
      if (num(t.form.xg_for_pg) != null) add("xG for, per game", fmt2(t.form.xg_for_pg));
      if (num(t.form.xg_against_pg) != null) add("xG against, per game", fmt2(t.form.xg_against_pg));
      if (num(t.form.xg_for_resid) != null) add("xG for, vs its rating", sgn2(t.form.xg_for_resid));
      if (num(t.form.xg_against_resid) != null) add("xG against, vs its rating", sgn2(t.form.xg_against_resid));
      fdisc.appendChild(k);
      fdisc.appendChild(el("p", "sub",
        wm < 6
          ? `Only ${wm} completed match${wm === 1 ? "" : "es"} this season: `
            + "noise, printed as a count rather than drawn as a line."
          : "A residual against the fitted rating, not a third input. "
            + "It says the colour might be wrong; it never changes the colour."));
      drawer.appendChild(fdisc);
    } else {
      drawer.appendChild(namedGap("No form residual in this payload.",
        "Team xG for and against over the last few matches, minus what the "
        + "fitted rating expected, is a diagnostic that the colour might be "
        + "wrong. It is deliberately not a third input to the colour, and it "
        + "is not carried here."));
    }

    /* the fit itself, behind a fold whose summary already states it */
    if (num(rt.attack) != null || num(rt.defence) != null) {
      const ratings = (M.res.inputs || []).find(i => /rating/i.test(String(i.name || "")));
      const fd = ratings ? parseTs(ratings.as_of) : null;
      const fh = ratings ? (num(ratings.age_hours) ?? ageHours(ratings.as_of)) : null;
      const disc = el("details", "fx-how");
      /* GOALS, served by the panel. The stored parameters are log multipliers
         and the page has no business exponentiating them: "+0.43" is not
         "+0.43 goals" (it multiplies by 1.54), and the defence parameter counts
         goals CONCEDED, so its sign runs opposite to every ease number here.
         Goals per game carry no convention to remember and no sign to invert. */
      const sc = num(rt.scores_pg), cd = num(rt.concedes_pg), lg = num(rt.league_pg);
      const gp = v => (v == null ? "–" : fmt2(v));
      disc.appendChild(el("summary", null,
        `The fit: scores ${gp(sc)} · concedes ${gp(cd)} goals a game`
        + (lg != null ? ` · league ${gp(lg)}` : "")
        + (num(rt.matches_seen) != null ? ` · ${rt.matches_seen} matches` : "")
        + (fh != null ? ` · fitted ${oldPhrase(fh)}` : "")
        + (fd ? `, on results to ${fd.toLocaleDateString(undefined, { day: "numeric", month: "short" })}` : "")));
      const kv = el("div", "fx-kv");
      const add = (a, b) => { kv.appendChild(el("span", "k", a)); kv.appendChild(el("span", "v", b)); };
      if (sc != null) add("scores", `${gp(sc)} a game against an average `
        + `opponent${lg != null ? `, league average ${gp(lg)}` : ""}`);
      if (cd != null) add("concedes", `${gp(cd)} a game against an average `
        + `opponent${lg != null ? `, league average ${gp(lg)}` : ""}`);
      if (ar != null) add("attack rank", `${ord(ar)} best of ${n}`);
      if (dr != null) add("defence rank", `${ord(dr)} best of ${n}`);
      if (num(rt.matches_seen) != null) add("matches in the fit", String(rt.matches_seen));
      if (t.priorShare != null) add("share from the prior", `${Math.round(t.priorShare * 100)}%`);
      add("promoted", promoted ? "yes" : "no");
      if (fd) add("fit as of", fd.toLocaleString(undefined,
        { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }));
      if (ratings && ratings.detail) add("method", String(ratings.detail));
      disc.appendChild(kv);
      disc.appendChild(el("p", "sub",
        "These are the club's own strength, which every cell colour holds constant: "
        + "a colour asks only what the opponent does at that venue. The schedule ranks "
        + "above are how easy this club's fixtures are for its own players."));
      drawer.appendChild(disc);
    }
  }

  await load();
}
