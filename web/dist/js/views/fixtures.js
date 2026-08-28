/* Fixtures — the split ticker.

   THE GOVERNING IDEA, and it is the whole page:

     Every fixture is two fixtures — one for your attackers, one for your
     defenders — and this page never averages them.

   A single "difficulty" number is the average of two answers to two different
   questions, and the average is never the answer to either one. So every cell
   here is one rectangle divided into two bands: the upper band is what your
   attackers face, the lower band is what your defenders face, both on the same
   diverging scale in the same unit. A cell whose bands disagree is visually
   torn, and torn cells are exactly the fixtures a blended FDR erases.

   THE SECOND CLAIM, which the colour makes and must therefore say out loud:

     Colour holds your own club at league average and asks only what the
     OPPONENT does, at that venue. Two clubs facing the same opponent share a
     cell colour on purpose — this is a fixture view, not a power ranking. The
     fixture-specific number, with your own club's strength in it, is one click
     away in every cell.

   DATA PATH. Panels are the only data path. This view asks `fixture_board`
   first — the split panel — and falls back to the legacy `fixture_ticker`,
   which carries one blended number per fixture. On the fallback the page
   REFUSES to draw two bands from one number: it draws a single-band cell and
   says, loudly, that the split is unavailable and why. Inventing a split would
   be exactly the failure this rebuild exists to fix.

   NOTHING IS FABRICATED. A blank gameweek is hatched and says "blank"; a
   fixture the model has no rating for is hatched and says "no fit". They are
   different answers and they look different. The legend, the unit, the domain
   and the calibration line are all payload-led — if the panel does not carry a
   measurement, the page says so rather than printing a number from a design
   document.

   The colour scale, its steps and its validator output are documented in
   fixtures.css, which owns them.
*/

import { runPanel, el, emptyBox, errBox, provenance, fmt1, fmt2 } from "/js/app.js";

/* The seven classes of the diverging scale, easy → hard. Defined in CSS, in
   all three theme states; named here only so the legend and the cells agree. */
const CLASSES = ["fx-e3", "fx-e2", "fx-e1", "fx-n0", "fx-h1", "fx-h2", "fx-h3"];
const HORIZONS = [3, 5, 6, 8];

/* ------------------------------------------------------------------ utils */

const num = v => (typeof v === "number" && isFinite(v) ? v : null);
const sgn1 = v => (v == null ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1));
const sgn2 = v => (v == null ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2));

function parseTs(s) {
  if (!s) return null;
  const d = new Date(String(s).replace(" ", "T").replace(/\+00:00$/, "Z"));
  return isNaN(d) ? null : d;
}
function ageHours(s) {
  const d = parseTs(s);
  return d ? (Date.now() - d.getTime()) / 3.6e6 : null;
}
function ageText(h) {
  if (h == null) return "age unknown";
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m old`;
  if (h < 48) return `${Math.round(h)}h old`;
  return `${Math.round(h / 24)}d old`;
}
function kickoffText(s) {
  const d = parseTs(s);
  if (!d) return null;
  return d.toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
  });
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
      payloadLed: true,
      digits: 2,
      note: null,
    };
  }
  return {
    dom: 0.5,
    unit: "fitted difficulty, 0–1",
    anchorAtt: null, anchorDef: null, clipped: null,
    payloadLed: false,
    digits: 2,
    note: "The panel publishes no scale block, so the ramp is anchored on the "
        + "legacy 0–1 difficulty with 0.50 as its midpoint. That number is "
        + "min–max normalised over this season's clubs, so a colour is not "
        + "comparable between seasons — it means “the worst fixture "
        + "currently available”, not a fixed quantity.",
  };
}

/* Seven equal classes across [-dom, +dom]. Positive ease = easier = blue. */
function bucket(ease, dom) {
  if (ease == null) return null;
  const t = Math.max(-1, Math.min(1, ease / dom));
  if (t >= 5 / 7) return 0;
  if (t >= 3 / 7) return 1;
  if (t >= 1 / 7) return 2;
  if (t > -1 / 7) return 3;
  if (t > -3 / 7) return 4;
  if (t > -5 / 7) return 5;
  return 6;
}
const cls = (ease, dom) => { const b = bucket(ease, dom); return b == null ? null : CLASSES[b]; };

/* --------------------------------------------------- payload → view model */

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
    relative_attack: spec.attack_ease != null ? spec.attack_ease : o.relative_attack,
    relative_defence: spec.defence_ease != null ? spec.defence_ease : o.relative_defence,
    relative_attack_xg: spec.attack_xg, relative_defence_xg: spec.defence_xg,
    relative_p_clean_sheet: spec.p_clean_sheet,
    market_state: mkt.state != null ? mkt.state : o.market_state,
    market_age_hours: mkt.age_hours != null ? mkt.age_hours : o.market_age_hours,
    market_as_of: mkt.as_of != null ? mkt.as_of : o.market_as_of,
    market_reason: mkt.reason != null ? mkt.reason : o.market_reason,
    n_books: mkt.n_books != null ? mkt.n_books : o.n_books,
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
    player: `${n.web_name || "—"}${club ? " · " + club : ""}`,
    status_text: n.news || n.status || null,
    chance: n.chance_of_playing,
    as_of: b.as_of || null,
  }));

  const xi = fromByTeam(D.predicted_lineups, (r, club) => ({
    name: `${r.web_name || "—"}${club ? " · " + club : ""}`,
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
        season: `${m.season || "—"}${m.venue ? " · " + m.venue : ""}`,
        score: (m.goals_for != null && m.goals_against != null)
          ? `${m.goals_for}–${m.goals_against}` : null,
        xg: (m.xg_for != null && m.xg_against != null)
          ? `${fmt2(m.xg_for)}–${fmt2(m.xg_against)}` : null,
      }))
    : null;

  const talk = D.creator_team_talk && D.creator_team_talk.available !== false
    ? (D.creator_team_talk.rows || null) : null;

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
  const blended = num(o.difficulty);
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
    pCleanSheet: num(o.p_clean_sheet),
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
      rankGap: num(h.rank_gap),
      rating: t.rating || null,
      form: t.form || null,
      priorShare: num((t.rating || {}).prior_share) ?? num(t.rating_prior_share),
    });
  }

  // "Torn": within the horizon, the two lenses disagree by a full class or
  // more AND in sign. That is the finding a blended number destroys.
  for (const t of teams) {
    t.torn = 0; t.tornWorst = null;
    for (const g of gws) for (const c of t.byGw.get(g).opps) {
      if (c.easeAtt == null || c.easeDef == null) continue;
      const ba = bucket(c.easeAtt, scale.dom), bd = bucket(c.easeDef, scale.dom);
      const gap = Math.abs(ba - bd);
      const opposed = (c.easeAtt > 0) !== (c.easeDef > 0);
      if (gap >= 2 && opposed) {
        t.torn++;
        if (!t.tornWorst || gap > t.tornWorst.gap)
          t.tornWorst = { gap, gw: g, cell: c };
      }
    }
  }

  return { scale, gws, teams, anySplit, anyBlend, res };
}

/* ------------------------------------------------------------------ view */

export default async function fixtures(host) {
  /* One drawer per visit — re-entering the view must not stack a second one
     on the body, and the previous visit's key handler must stop listening. */
  document.querySelectorAll("aside.fx-drawer").forEach(n => n.remove());
  const drawer = el("aside", "drawer fx-drawer");
  document.body.appendChild(drawer);
  const closeDrawer = () => drawer.classList.remove("open");
  const onKey = e => {
    if (!drawer.isConnected) { removeEventListener("keydown", onKey); return; }
    if (e.key === "Escape") closeDrawer();
  };
  addEventListener("keydown", onKey);
  /* The drawer lives on <body>, so leaving the view would otherwise leave it
     hanging over whatever loads next — the cross-links at the bottom of the
     drawer make that a one-click accident. */
  const onHash = () => {
    closeDrawer();
    if ((location.hash || "").slice(1).split("?")[0] !== "fixtures") {
      drawer.remove();
      removeEventListener("hashchange", onHash);
      removeEventListener("keydown", onKey);
    }
  };
  addEventListener("hashchange", onHash);

  const card = el("section", "card");
  card.appendChild(el("h2", null, "Fixture ticker"));

  /* --- the two load-bearing sentences, in the slot xPoints uses for its --- */
  const s1 = el("p", "fx-claim");
  s1.appendChild(el("b", null, "Every fixture is two fixtures"));
  s1.appendChild(document.createTextNode(
    " — one for your attackers, one for your defenders — and this page never "
    + "averages them. The upper band of every cell is what your attackers face; "
    + "the lower band is what your defenders face."));

  const s2 = el("p", "fx-claim");
  s2.appendChild(document.createTextNode("Colour holds "));
  s2.appendChild(el("b", null, "your own club at league average"));
  s2.appendChild(document.createTextNode(
    " and asks only what the opponent does, at that venue. Two clubs facing the "
    + "same opponent get the same cell colour "));
  s2.appendChild(el("b", null, "on purpose"));
  s2.appendChild(document.createTextNode(
    " — this is a fixture view, not a power ranking. The fixture-specific "
    + "number, with your own club's strength in it, is one click away in every "
    + "cell."));

  const calibEl = el("div", "fx-calib");
  calibEl.hidden = true;                 // shown only once it has something to say
  card.appendChild(calibEl);

  /* The two sentences above are the page's method, and the method is worth one
     click, not six lines above every visit. They live in a disclosure with the
     panel's own notes -- which say nearly the same thing -- so the reasoning is
     in exactly one place and the grid is the first thing on screen. */
  const howBox = el("details", "fx-how");
  const howSum = el("summary", null, "How to read this grid");
  howBox.append(howSum, s1, s2);
  const noteBox = el("div", "fx-hownotes");
  howBox.appendChild(noteBox);
  card.appendChild(howBox);

  const freshRow = el("div", "fx-fresh");
  const horizonRow = el("div", "toolbar fx-tb");
  const lensRow = el("div", "toolbar fx-tb");
  const sortRow = el("div", "toolbar fx-tb");
  const controls = el("div", "fx-controls");
  controls.append(horizonRow, lensRow, sortRow);
  const body = el("div");
  const foot = el("div");
  card.append(controls, freshRow, body, foot);
  host.appendChild(card);

  const tornCard = el("section", "card");
  const shapeCard = el("section", "card");
  host.append(tornCard, shapeCard);

  // ---- state ----
  let horizon = 6;
  let fromGw = null;              // null = the panel's own default (next GW)
  let lens = "both";              // both | attack | defence
  let sortKey = "att";            // att | def | torn | az
  let tableView = false;
  let M = null, prov = null, scriptUsed = null, fellBack = false, boardErr = null;

  /* --------------------------------------------------------- data fetch */
  async function load() {
    body.textContent = "";
    body.appendChild(el("p", "sub", "loading…"));
    const params = { horizon };
    if (fromGw != null) params.from_gw = fromGw;

    let r = await tryPanel("fixture_board", params);
    fellBack = false; boardErr = null;
    if (!r.ok) {
      boardErr = r;
      // horizon/from_gw are the legacy panel's own params, so the fallback is
      // a straight retry — but the SPLIT is gone and the page will say so.
      r = await tryPanel("fixture_ticker", params);
      fellBack = true;
      if (!r.ok && fromGw != null) {           // the window shift was refused
        fromGw = null;
        r = await tryPanel("fixture_ticker", { horizon });
      }
    }
    if (!r.ok) {
      body.textContent = "";
      renderCalibration();
      body.appendChild(errBox(r.error));
      body.appendChild(el("p", "sub",
        "Both the split panel and the legacy ticker refused this request, so "
        + "there is nothing to draw. The page shows the failure rather than an "
        + "empty grid, because an empty grid would read as “no fixtures”."));
      tornCard.hidden = shapeCard.hidden = true;
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
      tornCard.hidden = shapeCard.hidden = true;
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
    renderSort();
    renderNotes();
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
       neither has -- the bracket is the honest object. */
    const mLo = model
      ? Math.min(num(model.fixture_swing_attack_pts), num(model.fixture_swing_defence_pts))
      : null;
    const eHi = emp ? num(emp.outfield_fixture_pts_6gw) : null;
    const gws = (model && num(model.horizon_gws)) || (M.gws.length || null);
    const ratios = [model && num(model.ratio_attack), model && num(model.ratio_defence),
                    emp && num(emp.outfield_ratio)].filter(v => v != null);

    if (mLo != null && eHi != null) {
      calibEl.hidden = false;
      const line = el("p", "fx-cal-line");
      line.appendChild(document.createTextNode("Over "));
      line.appendChild(el("b", null, `${gws} gameweeks`));
      line.appendChild(document.createTextNode(", the difference between the best and worst fixture run is worth "));
      line.appendChild(el("b", "fx-cal-hi", `${fmt1(mLo)}–${fmt1(eHi)} points`));
      line.appendChild(document.createTextNode(" per asset. Which club you own is worth "));
      line.appendChild(el("b", "fx-cal-hi",
        `${fmt1(Math.min(...ratios))}–${fmt1(Math.max(...ratios))}×`));
      line.appendChild(document.createTextNode(
        " that. Break ties with this page; do not pick assets with it."));
      calibEl.appendChild(line);

      const why = el("p", "sub");
      why.appendChild(document.createTextNode(
        `The low end is the model, which carries no estimation noise and is `
        + `therefore a floor. The high end is measured on `));
      why.appendChild(el("b", null,
        emp.by_position
          ? `${emp.by_position.reduce((a, r) => a + (num(r.n_starts) || 0), 0).toLocaleString()} starts`
          : "realised starts"));
      why.appendChild(document.createTextNode(
        `${emp.seasons ? " across " + String(emp.seasons).split(",").length + " seasons" : ""}, `
        + `where taking best-minus-worst across twenty estimated effects is biased `
        + `upward by sampling noise and is therefore a ceiling. The truth is inside.`));
      calibEl.appendChild(why);

      if (Array.isArray(emp.by_position) && emp.by_position.length) {
        const strip = el("div", "fx-cal-pos");
        for (const r of emp.by_position) {
          const chip = el("span", "fx-cal-chip");
          chip.appendChild(el("span", "p", String(r.position || "—")));
          chip.appendChild(el("span", "n", `${fmt1(num(r.fixture_pts_6gw))} pts`));
          chip.title = `${(num(r.n_starts) || 0).toLocaleString()} starts; `
            + `team quality ${fmt1(num(r.team_pts_6gw))} pts, ${fmt1(num(r.ratio))}x the fixture effect`;
          strip.appendChild(chip);
        }
        const lab = el("span", "fx-cal-poslab", "measured, by position");
        calibEl.appendChild(lab);
        calibEl.appendChild(strip);
      }
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
      + "from team quality — which would make this a tie-breaker and not an "
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
  function renderFreshness(res) {
    freshRow.textContent = "";
    freshRow.appendChild(el("span", "tlabel", "Inputs"));
    const inputs = Array.isArray(res.inputs) ? res.inputs : null;

    if (inputs && inputs.length) {
      for (const i of inputs) {
        const h = num(i.age_hours) ?? ageHours(i.as_of);
        const state = i.state || (h == null ? "missing" : "fresh");
        const chip = el("span", "fx-inchip"
          + (state === "stale" || state === "failed" ? " stale"
            : state === "degraded" ? " warn"
            : state === "missing" ? " missing" : ""));
        chip.appendChild(el("span", "freshdot "
          + (state === "fresh" ? "good" : state === "degraded" ? "warn" : "bad")));
        chip.appendChild(el("b", null, i.name));
        chip.appendChild(el("span", "age", h == null ? "—" : ageText(h)));
        if (state !== "fresh") chip.appendChild(el("span", "tag", state));
        chip.title = [
          i.as_of ? `as of ${i.as_of}` : null,
          i.rows != null ? `${Number(i.rows).toLocaleString()} rows` : null,
          i.effect_when_stale ? `when stale: ${i.effect_when_stale}` : null,
          i.refresh_job ? `refreshed by ${i.refresh_job}` : null,
          i.last_job_outcome ? `last run: ${i.last_job_outcome}` : null,
        ].filter(Boolean).join("\n");
        freshRow.appendChild(chip);
      }
      return;
    }

    // No inputs[] contract yet. Say exactly what age we DO know, and say that
    // the market's age is unknown — which is why no price is drawn anywhere.
    const h = ageHours(res.as_of);
    const chip = el("span", "fx-inchip" + (h == null ? " missing" : h > 72 ? " stale" : h > 36 ? " warn" : ""));
    chip.appendChild(el("span", "freshdot " + (h == null ? "bad" : h > 72 ? "bad" : h > 36 ? "warn" : "good")));
    chip.appendChild(el("b", null, "fixture list"));
    chip.appendChild(el("span", "age", ageText(h)));
    chip.title = `fact_fixture as of ${res.as_of || "unknown"}`;
    freshRow.appendChild(chip);

    const mk = el("span", "fx-inchip missing");
    mk.appendChild(el("span", "freshdot bad"));
    mk.appendChild(el("b", null, "market odds"));
    mk.appendChild(el("span", "age", "not carried"));
    mk.appendChild(el("span", "tag", "absent"));
    mk.title = "This panel returns no odds and no odds age. A price whose age "
      + "is unknown is never rendered as current, so no market number appears "
      + "anywhere on this page — not in the colour, not in a cell, not in the "
      + "drawer.";
    freshRow.appendChild(mk);

    const note = el("span", "sub");
    note.textContent = "no inputs[] contract on this panel — only the fixture "
      + "table's as_of is knowable here";
    freshRow.appendChild(note);
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
    const back = el("button", null, "◀");
    back.title = "shift the window one gameweek earlier";
    back.onclick = () => { fromGw = (M.gws[0] ?? 1) - 1; if (fromGw < 1) fromGw = 1; load(); };
    back.disabled = (M.gws[0] ?? 1) <= 1;
    const fwd = el("button", null, "▶");
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

  function renderLens() {
    lensRow.textContent = "";
    lensRow.appendChild(el("span", "tlabel", "Lens"));
    const seg = el("span", "seg");
    for (const [k, label, title] of [
      ["both", "Both", "one cell, two bands — attackers above, defenders below"],
      ["attack", "Attackers", "how easy it is to score against this opponent, at this venue"],
      ["defence", "Defenders", "how easy it is to keep them out, at this venue"],
    ]) {
      const b = el("button", k === lens ? "on" : "", label);
      b.title = title;
      b.disabled = !M.anySplit && k !== "both";
      if (b.disabled) b.title = "the split is not in this payload — see the note below";
      b.onclick = () => { lens = k; renderSort(); renderBody(); };
      seg.appendChild(b);
    }
    lensRow.appendChild(seg);

    /* This used to be one chip that toggled. It sat inside the lens group, so
       turning it on left "Both" still lit while the grid was gone, and nothing
       on screen said which control had hidden it. Two mutually exclusive
       buttons in their own labelled slot: the current view is always readable
       off the row, and the way back is the button next to it. */
    lensRow.appendChild(el("span", "tlabel fx-tlabel2", "Shown as"));
    const vseg = el("div", "seg");
    for (const [isTable, label, title] of [
      [false, "Grid", "the colour ticker — six gameweeks per club at a glance"],
      [true, "Table", "the same numbers as a sortable table; colour is never the "
                      + "only way to read this page"],
    ]) {
      const b = el("button", tableView === isTable ? "on" : "", label);
      b.title = title;
      b.setAttribute("aria-pressed", String(tableView === isTable));
      b.onclick = () => {
        if (tableView === isTable) return;
        tableView = isTable; renderLens(); renderBody();
      };
      vseg.appendChild(b);
    }
    lensRow.appendChild(vseg);
  }

  function renderSort() {
    sortRow.textContent = "";
    sortRow.appendChild(el("span", "tlabel", "Order"));
    const opts = [
      ["att", "Easiest for attackers", "sum of attack-ease over the window — a sum, not a mean, so a double gameweek counts twice and a blank counts as nothing"],
      ["def", "Easiest for defenders", "sum of defence-ease over the window, same axis, same unit"],
      ["torn", "Most torn", "clubs whose two lenses disagree most often — the fixtures a blended FDR erases"],
      ["az", "Club A–Z", "alphabetical, for looking one club up"],
    ];
    const seg = el("span", "seg");
    for (const [k, label, title] of opts) {
      const b = el("button", k === sortKey ? "on" : "", label);
      b.title = title;
      b.disabled = !M.anySplit && (k === "torn" || k === "def");
      if (b.disabled) b.title = "needs the split — see the note below";
      b.onclick = () => { sortKey = k; renderBody(); };
      seg.appendChild(b);
    }
    sortRow.appendChild(seg);
  }

  function renderNotes() {
    noteBox.textContent = "";
    // The single most important disclosure on the page: whether the numbers
    // being coloured are actually split.
    if (!M.anySplit) {
      const w = el("div", "empty");
      w.appendChild(el("b", null, "The split is not in this payload."));
      const p = el("div");
      p.appendChild(document.createTextNode(
        fellBack
          ? "This page asked "
          : "This page is reading "));
      p.appendChild(codeSpan(fellBack ? "fixture_board" : scriptUsed));
      if (fellBack) {
        p.appendChild(document.createTextNode(
          boardErr && boardErr.missing
            ? ", which is not registered, and fell back to "
            : ", which failed, and fell back to "));
        p.appendChild(codeSpan("fixture_ticker"));
        p.appendChild(document.createTextNode("."));
      }
      p.appendChild(document.createTextNode(
        " That panel returns ONE blended difficulty per fixture. A blended "
        + "number is the average of the attack question and the defence "
        + "question, and the average is not the answer to either — it is the "
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
    if (!M.scale.payloadLed && M.scale.note) {
      noteBox.appendChild(el("p", "sub", M.scale.note));
    }
    for (const n of M.res.notes || []) noteBox.appendChild(el("p", "sub", n));
  }

  /* ---------------------------------------------------------- the grid */
  function sortedTeams() {
    const t = M.teams.slice();
    const by = (f) => (a, b) => {
      const x = f(a), y = f(b);
      if (x == null && y == null) return a.short.localeCompare(b.short);
      if (x == null) return 1;
      if (y == null) return -1;
      return y - x;
    };
    if (sortKey === "az") t.sort((a, b) => a.short.localeCompare(b.short));
    else if (sortKey === "torn") t.sort((a, b) => (b.torn - a.torn)
      || ((b.tornWorst?.gap || 0) - (a.tornWorst?.gap || 0))
      || a.short.localeCompare(b.short));
    else if (sortKey === "def") t.sort(by(x => x.defSum));
    else t.sort(by(x => M.anySplit ? x.attSum : (x.blendMean == null ? null : x.blendMean * x.nFixtures)));
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
    const rh = el("div", "fx-hcell fx-railhead", "club · horizon");
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
    ordered.forEach((t, i) => {
      grid.appendChild(railCell(t, i + 1, railMax));
      for (const g of M.gws) grid.appendChild(gwCell(t, t.byGw.get(g)));
    });

    wrap.appendChild(grid);
    body.appendChild(wrap);
    body.appendChild(legend());
  }

  function railCell(t, rank, railMax) {
    const d = el("div", "fx-rail");
    d.tabIndex = 0;
    d.title = `${t.name} — ${t.nFixtures} fixture${t.nFixtures === 1 ? "" : "s"} `
      + `in GW${M.gws[0]}–GW${M.gws[M.gws.length - 1]}`
      + (t.nBlanks ? `, ${t.nBlanks} blank` : "")
      + (t.nDoubles ? `, ${t.nDoubles} double` : "")
      + "\nclick for the club's run";
    const nm = el("div", "nm");
    const label = el("span", null, t.short);
    if (t.priorShare != null && t.priorShare > 0.4) {
      label.className = "fx-prior";
      label.title = `${Math.round(t.priorShare * 100)}% of this club's rating is `
        + "prior, not data — newly promoted, so read its colours gently";
    }
    nm.appendChild(label);
    if (t.torn) {
      const z = el("span", "fx-torn", "⚡");
      z.title = `${t.torn} fixture${t.torn === 1 ? "" : "s"} in this window where `
        + "the attack and defence answers point opposite ways";
      nm.appendChild(z);
    }
    d.appendChild(nm);
    d.appendChild(el("div", "rk", `#${rank}`));

    const bars = el("div", "bars");
    if (M.anySplit) {
      bars.appendChild(barRow("att", t.attSum, railMax, "attackers", t.nFixtures));
      bars.appendChild(barRow("def", t.defSum, railMax, "defenders", t.nFixtures));
    } else {
      const v = t.blendMean == null ? null : t.blendMean * t.nFixtures;
      bars.appendChild(barRow("run", v, railMax, "blended (not split)", t.nFixtures));
    }
    d.appendChild(bars);
    d.onclick = () => openClub(t);
    d.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openClub(t); } };
    return d;
  }

  /* A diverging bar on a shared axis: length from a centre line that IS the
     league-average fixture. Length is the strongest quantitative channel and
     it carries no hue dependence, so the summary survives colour blindness,
     print and forced-colours on its own. */
  /* Length from a centre line that IS the league-average fixture. Length is
     the strongest quantitative channel and carries no hue dependence, so the
     summary survives colour blindness, print and forced-colours on its own.
     LENGTH encodes the horizon SUM — which handles doubles and blanks natively,
     because more fixtures really is more chances. COLOUR encodes the per-game
     average, so the bar's tint sits on exactly the same scale as the cells. */
  function barRow(kind, sum, max, what, nFixtures) {
    const row = el("div", "fx-brow");
    row.appendChild(el("span", "k", kind === "att" ? "ATT" : kind === "def" ? "DEF" : "RUN"));
    const track = el("div", "fx-track");
    const perGame = (sum == null || !nFixtures) ? null : sum / nFixtures;
    if (sum != null) {
      const frac = Math.max(-1, Math.min(1, sum / max));
      const fill = el("div", "fx-fill " + (cls(perGame, M.scale.dom) || "fx-n0"));
      if (frac >= 0) { fill.style.left = "50%"; fill.style.width = `${frac * 50}%`; }
      else { fill.style.right = "50%"; fill.style.width = `${-frac * 50}%`; }
      track.appendChild(fill);
    }
    row.appendChild(track);
    row.appendChild(el("span", "v", sum == null ? "–" : sgn1(sum)));
    row.title = sum == null
      ? `no ${what} number for this club in this window`
      : `${what}: ${sgn2(sum)} summed over ${nFixtures} fixture`
        + `${nFixtures === 1 ? "" : "s"} (${sgn2(perGame)} per game) · ${M.scale.unit}`;
    return row;
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
    const hasSplit = c.easeAtt != null && c.easeDef != null;
    const btn = el("button", "fx-cell");
    if (c.marketWeight != null && c.marketWeight > 0) btn.classList.add("priced");

    /* Two bands ONLY when this fixture genuinely has two numbers. Everything
       else is one band, and a fixture with no number at all is hatched — a
       missing rating must never wear a colour. */
    const solo = !(M.anySplit && lens === "both" && hasSplit);
    let showAtt = null, showDef = null;
    if (!solo) { showAtt = c.easeAtt; showDef = c.easeDef; }
    else if (lens === "attack") showAtt = c.easeAtt;
    else if (lens === "defence") showAtt = c.easeDef;
    else showAtt = hasSplit ? c.easeAtt : c.easeBlend;

    const known = solo ? showAtt != null : true;
    if (!known) btn.classList.add("nomodel");
    if (solo) btn.classList.add("solo");

    btn.appendChild(el("span", "fx-band att " + (cls(showAtt, M.scale.dom) || "fx-n0")));
    if (!solo) {
      btn.appendChild(el("span", "fx-band def " + (cls(showDef, M.scale.dom) || "fx-n0")));
      btn.appendChild(el("span", "seam"));
    }
    btn.appendChild(el("span", "opp", c.label));
    if (showAtt != null) btn.appendChild(el("span", "vv att", sgn2(showAtt).replace("0.", ".")));
    if (!solo && showDef != null) btn.appendChild(el("span", "vv def", sgn2(showDef).replace("0.", ".")));
    if (!known) btn.appendChild(el("span", "why",
      M.anySplit && lens === "both" ? "half fitted" : "no fit"));

    const venue = c.isHome ? "home" : "away";
    btn.title = [
      `${t.short} ${c.isHome ? "v" : "at"} ${c.opponent} · GW${slot.gw}${slot.double ? " (double)" : ""}`,
      kickoffText(c.kickoff),
      hasSplit
        ? `attackers ${sgn2(c.easeAtt)} · defenders ${sgn2(c.easeDef)} ${M.scale.unit}`
        : c.easeBlend != null
          ? `blended difficulty ${fmt2(c.blended)} (not split — see the note above)`
          : "no fitted rating for this fixture",
      c.rankAtt != null && c.rankDef != null
        ? `rank ${c.rankAtt} as an attacking fixture · ${c.rankDef} as a defensive one` : null,
      `${venue} · click for the match detail`,
    ].filter(Boolean).join("\n");
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
    for (const k of CLASSES) {
      const s = el("span", "sw " + k);
      ramp.appendChild(s);
    }
    ramp.appendChild(el("span", "lab", " hard"));
    left.appendChild(ramp);
    const d = M.scale.dom, u = M.scale.unit;
    const b = x => (M.scale.digits === 2 ? x.toFixed(2) : x.toFixed(1));
    left.appendChild(el("div", "fx-boundaries",
      `${b(-d)}  ${b(-5 * d / 7)}  ${b(-3 * d / 7)}  ${b(-d / 7)} · 0 · ${b(d / 7)}  `
      + `${b(3 * d / 7)}  ${b(5 * d / 7)}  ${b(d)}`));
    const unit = el("div", "lab");
    unit.appendChild(document.createTextNode("unit: "));
    unit.appendChild(el("b", null, u));
    unit.appendChild(document.createTextNode(
      M.scale.payloadLed
        ? ` · midpoint = a league-average fixture · domain fixed by the panel, so a colour means the same thing in every gameweek`
        : ` · midpoint 0.50 · min–max normalised over this season's clubs, so it is NOT comparable between seasons`));
    if (M.scale.clipped)
      unit.appendChild(document.createTextNode(
        ` · ${M.scale.clipped} (club, venue) pairs saturate the ends`));
    left.appendChild(unit);
    L.appendChild(left);

    const keys = el("div", "fx-keys");
    if (M.anySplit && lens === "both") {
      keys.appendChild(keyItem(null, "upper band = your attackers · lower band = your defenders"));
    } else if (!M.anySplit) {
      keys.appendChild(keyItem(null, "one band = one blended number; the split is unavailable"));
    } else {
      keys.appendChild(keyItem(null, lens === "attack"
        ? "one band = the attack lens only" : "one band = the defence lens only"));
    }
    keys.appendChild(keyItem(null, "CAPS = home · lower case = away"));
    keys.appendChild(keyItem("hatch", "hatched = blank gameweek, or a fixture with no fitted rating — never a colour"));
    keys.appendChild(keyItem(null, "a cell split into two = a double gameweek: two decisions, two marks"));
    keys.appendChild(keyItem(null, "every band prints its own number, so the colour is never the only channel"));
    L.appendChild(keys);
    return L;
  }
  function keyItem(kind, text) {
    const k = el("span", "k");
    if (kind === "hatch") k.appendChild(el("span", "fx-swatch-hatch"));
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
    hr.appendChild(el("th", null, "club"));
    for (const g of M.gws) hr.appendChild(el("th", "num", `GW${g}`));
    hr.appendChild(el("th", "num", M.anySplit ? "Σ att" : "Σ run"));
    if (M.anySplit) hr.appendChild(el("th", "num", "Σ def"));
    thead.appendChild(hr); t.appendChild(thead);
    const tb = el("tbody");
    for (const team of sortedTeams()) {
      const tr = el("tr");
      tr.appendChild(el("td", null, team.short));
      for (const g of M.gws) {
        const slot = team.byGw.get(g);
        const td = el("td", "num");
        if (slot.blank) { td.textContent = "blank"; td.style.color = "var(--faint)"; }
        else {
          const parts = slot.opps.map(c => {
            if (c.easeAtt != null && c.easeDef != null)
              return `${c.label} ${sgn2(c.easeAtt)}/${sgn2(c.easeDef)}`;
            if (c.easeBlend != null) return `${c.label} ${sgn2(c.easeBlend)}`;
            return `${c.label} no fit`;
          });
          const first = slot.opps[0];
          const sw = el("span", "sw " + (cls(
            first.easeAtt != null ? first.easeAtt : first.easeBlend, M.scale.dom) || "fx-n0"));
          td.appendChild(sw);
          td.appendChild(document.createTextNode(parts.join(" · ")));
        }
        tr.appendChild(td);
      }
      tr.appendChild(el("td", "num", M.anySplit ? sgn2(team.attSum)
        : sgn2(team.blendMean == null ? null : team.blendMean * team.nFixtures)));
      if (M.anySplit) tr.appendChild(el("td", "num", sgn2(team.defSum)));
      tb.appendChild(tr);
    }
    t.appendChild(tb); wrap.appendChild(t);
    body.appendChild(wrap);
    body.appendChild(el("p", "sub",
      M.anySplit
        ? `Each cell is attack-ease / defence-ease in ${M.scale.unit}. Positive is easier.`
        : `Each cell is the blended ease in ${M.scale.unit}. Positive is easier. `
          + "It is one number, not two."));
    body.appendChild(legend());
  }

  /* ---------------------------------------- what the blend hides (below) */
  function renderTorn() {
    tornCard.textContent = "";
    tornCard.hidden = false;
    tornCard.appendChild(el("h2", null, "What the blend hides"));
    if (!M.anySplit) {
      tornCard.appendChild(el("p", "sub",
        "This strip finds the fixtures where the attack answer and the defence "
        + "answer point opposite ways — the ones a single FDR number reports as "
        + "“average”, which is the one thing they are not."));
      tornCard.appendChild(namedGap("Needs the split.", gapText(
        "The payload carries one blended number per fixture, so there is no "
        + "disagreement to find. This strip lights up when ",
        codeSpan("fixture_board"),
        " serves attack and defence separately.")));
      return;
    }
    const rows = [];
    for (const t of M.teams) {
      for (const g of M.gws) for (const c of t.byGw.get(g).opps) {
        if (c.easeAtt == null || c.easeDef == null) continue;
        const ba = bucket(c.easeAtt, M.scale.dom), bd = bucket(c.easeDef, M.scale.dom);
        const gap = Math.abs(ba - bd);
        const opposed = (c.easeAtt > 0) !== (c.easeDef > 0);
        if (gap >= 2 && opposed) rows.push({ t, gw: g, c, gap });
      }
    }
    rows.sort((a, b) => b.gap - a.gap
      || Math.abs(b.c.easeAtt - b.c.easeDef) - Math.abs(a.c.easeAtt - a.c.easeDef));
    tornCard.appendChild(el("p", "sub",
      `${rows.length} fixture${rows.length === 1 ? "" : "s"} in GW${M.gws[0]}–`
      + `GW${M.gws[M.gws.length - 1]} where the two lenses point opposite ways. `
      + "These are the fixtures a single difficulty number reports as average, "
      + "which is the one thing they are not."));
    if (!rows.length) {
      tornCard.appendChild(namedGap("No torn fixtures in this window.",
        "Every fixture here has its two lenses pointing the same way. That is a "
        + "real finding, not an empty state: over this window the split does not "
        + "change any decision, and a blended number would have served."));
      return;
    }
    const list = el("div", "fx-torn-list");
    for (const r of rows.slice(0, 8)) {
      const row = el("div", "fx-torn-row");
      row.appendChild(el("div", "gw", `GW${r.gw}`));
      const txt = el("div", "txt");
      const attEasy = r.c.easeAtt > 0;
      txt.appendChild(el("b", null,
        `${r.t.short} ${r.c.isHome ? "v" : "at"} ${r.c.opponent}`));
      txt.appendChild(document.createTextNode(
        ` — ${sgn2(r.c.easeAtt)} for attackers, ${sgn2(r.c.easeDef)} for defenders. `
        + (attEasy
          ? "Goals are on; the clean sheet is not. Buy the attack, not the back line."
          : "The clean sheet is on; the goals are not. Buy the defence, not the attack.")));
      row.appendChild(txt);
      const open = el("button", "chip", "open");
      open.onclick = () => openFixture(r.t, r.t.byGw.get(r.gw), r.c);
      row.appendChild(open);
      list.appendChild(row);
    }
    tornCard.appendChild(list);
    if (rows.length > 8)
      tornCard.appendChild(el("p", "sub", `${rows.length - 8} more — sort the grid by "Most torn".`));
  }

  /* ------------------------------------------------ league shape (below) */
  function renderShape() {
    shapeCard.textContent = "";
    const withRating = M.teams.filter(t => t.rating
      && num(t.rating.attack) != null && num(t.rating.defence) != null);
    if (!withRating.length) { shapeCard.hidden = true; return; }
    shapeCard.hidden = false;
    shapeCard.appendChild(el("h2", null, "League shape"));
    shapeCard.appendChild(el("p", "sub",
      "The fitted attack and defence ratings behind every colour above, with "
      + "the crosshair at league average. The defensive axis is inverted so "
      + "“good at both” is one corner. This is team quality, which is "
      + "what the ticker deliberately holds constant — it is here so you can "
      + "see what the ticker is NOT telling you."));
    const W = 560, H = 320, P = 34;
    const xs = withRating.map(t => t.rating.attack), ys = withRating.map(t => t.rating.defence);
    const xr = [Math.min(...xs), Math.max(...xs)], yr = [Math.min(...ys), Math.max(...ys)];
    const pad = (r) => { const s = (r[1] - r[0]) * 0.12 || 0.1; return [r[0] - s, r[1] + s]; };
    const [x0, x1] = pad(xr), [y0, y1] = pad(yr);
    const sx = v => P + (v - x0) / (x1 - x0) * (W - 2 * P);
    const sy = v => H - P - (1 - (v - y0) / (y1 - y0)) * (H - 2 * P);  // inverted
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("class", "fx-scatter");
    svg.setAttribute("role", "img");
    const mk = (tag, attrs, text) => {
      const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      if (text != null) n.textContent = text;
      return n;
    };
    if (x0 < 0 && x1 > 0) svg.appendChild(mk("line", { class: "ax", x1: sx(0), x2: sx(0), y1: P, y2: H - P }));
    if (y0 < 0 && y1 > 0) svg.appendChild(mk("line", { class: "ax", x1: P, x2: W - P, y1: sy(0), y2: sy(0) }));
    svg.appendChild(mk("text", { x: W - P, y: H - 12, "text-anchor": "end" }, "attack rating →"));
    svg.appendChild(mk("text", { x: 6, y: P - 12 }, "↑ better defence"));
    for (const t of withRating) {
      svg.appendChild(mk("circle", {
        class: "dot", cx: sx(t.rating.attack), cy: sy(t.rating.defence), r: 5,
        fill: "var(--s1)",
      }));
      svg.appendChild(mk("text", {
        class: "lbl", x: sx(t.rating.attack) + 8, y: sy(t.rating.defence) + 3,
      }, t.short));
    }
    shapeCard.appendChild(svg);
  }

  /* ------------------------------------------------------- the drawer --- */
  function drawerHead(title, sub) {
    const head = el("div", "fx-dh");
    const id = el("div");
    id.appendChild(el("div", "dname", title));
    if (sub) id.appendChild(el("div", "sub", sub));
    head.appendChild(id);
    const close = el("button", null, "✕");
    close.onclick = closeDrawer;
    head.appendChild(close);
    return head;
  }

  function lensBars(c) {
    const box = el("div", "fx-lens");
    const one = (k, v, rank) => {
      const r = el("div", "fx-lensrow");
      r.appendChild(el("span", "lk", k));
      const track = el("div", "fx-track");
      if (v != null) {
        const frac = Math.max(-1, Math.min(1, v / M.scale.dom));
        const fill = el("div", "fx-fill " + (cls(v, M.scale.dom) || "fx-n0"));
        if (frac >= 0) { fill.style.left = "50%"; fill.style.width = `${frac * 50}%`; }
        else { fill.style.right = "50%"; fill.style.width = `${-frac * 50}%`; }
        track.appendChild(fill);
      }
      r.appendChild(track);
      r.appendChild(el("span", "lv",
        (v == null ? "–" : sgn2(v)) + (rank != null ? `  #${rank}` : "")));
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
    drawer.textContent = "";
    drawer.classList.add("open");
    drawer.scrollTop = 0;
    drawer.appendChild(drawerHead(
      `${t.short} ${c.isHome ? "v" : "at"} ${c.opponent}`,
      [`GW${slot.gw}`, kickoffText(c.kickoff), c.isHome ? "home" : "away",
       slot.double ? "double gameweek" : null].filter(Boolean).join(" · ")));

    // 1 — the two numbers, which are the finding
    drawer.appendChild(el("h2", null, "The two answers"));
    drawer.appendChild(lensBars(c));
    drawer.appendChild(el("p", "sub",
      (c.easeAtt != null && c.easeDef != null)
        ? `${M.scale.unit}. Positive is easier. Both bars are on the same axis `
          + "as the grid above, and neither is an average of the other."
        : c.easeBlend != null
          ? "One blended number, because that is all the payload carries. It is "
            + "the average of two different questions."
          : "The panel has no fitted rating for this fixture. The schedule is "
            + "still a fact; the difficulty is not known."));

    // 2 — the assumption the cell colour makes, and the number that removes it
    drawer.appendChild(el("h2", null, "What the cell colour assumed"));
    const asm = el("p", "sub");
    asm.textContent = `The grid held ${t.short} at league average and asked only `
      + `what ${c.opponent} does ${c.isHome ? "away" : "at home"}. That is why `
      + `every club visiting ${c.opponent} gets this same colour. The `
      + "fixture-specific number — the one with " + t.short + "'s own strength "
      + "in it — belongs here.";
    drawer.appendChild(asm);

    const rel = c.raw && (c.raw.relative_attack != null || c.raw.relative_defence != null);
    if (rel) {
      drawer.appendChild(lensBars({
        easeAtt: num(c.raw.relative_attack), easeDef: num(c.raw.relative_defence),
        rankAtt: null, rankDef: null, easeBlend: null,
      }));
    } else {
      drawer.appendChild(namedGap("The fixture-specific number is not in this payload.",
        gapText(
          "This is the drawer's job and it cannot do it yet: the panel returns "
          + "opponent-only ease and no relative (own-club-adjusted) figure. It "
          + "would come from the same fit — our own ",
          codeSpan("attack_O"), " and ", codeSpan("defence_O"),
          " added back in place of the league-average anchor — and the page will "
          + "not compute it in the browser, because a number modelled in the UI "
          + "is a number nobody can audit.")));
    }

    // 3 — where the number came from
    drawer.appendChild(el("h2", null, "Where the number came from"));
    const kv = el("div", "fx-kv");
    const addKv = (k, v) => { kv.appendChild(el("span", "k", k)); kv.appendChild(el("span", "v", v)); };
    addKv("panel", scriptUsed);
    if (c.raw.attack_xg != null) addKv("μ_O (attack lens)", fmt2(c.raw.attack_xg) + " goals");
    if (c.raw.defence_xg != null) addKv("λ_O (defence lens)", fmt2(c.raw.defence_xg) + " goals");
    if (c.blended != null) addKv("legacy difficulty", fmt2(c.blended));
    if (c.priorShare != null) addKv("rating from prior", `${Math.round(c.priorShare * 100)}%`);
    drawer.appendChild(kv);
    if (c.marketWeight == null && c.marketState === "priced") {
      // The market is present and dated; it is deliberately not in the colour.
      addKv("market", `${c.nBooks != null ? c.nBooks + " books, " : ""}`
        + `${c.marketAgeH != null ? ageText(c.marketAgeH) : "age unknown"}`);
      drawer.appendChild(kvNote(
        "Priced, and deliberately not blended into the colour.",
        gapText(
          "The quote is here and it is dated, so you can read it against the "
          + "model below. It is not averaged into the number above because the "
          + "blend weight in ", codeSpan("blend.py"),
          " has never been tuned out of sample — a blend on an untuned constant "
          + "is a guess wearing a number's clothes. Model and market are shown "
          + "side by side and the gap between them is left for you to read.")));
    } else if (c.marketWeight == null && c.marketState != null && c.marketState !== "priced") {
      drawer.appendChild(namedGap(`No price for this fixture (${c.marketState}).`,
        gapText(
          c.raw.market_reason
            ? String(c.raw.market_reason) + " "
            : "No book in the pull covers this fixture. ",
          "The number above is the fitted model alone.")));
    } else if (c.marketWeight == null) {
      drawer.appendChild(namedGap("No market leg, and no market age.", gapText(
        "The payload carries no ", codeSpan("market_weight"), " and no ",
        codeSpan("market_age_hours"), ". A price whose age is unknown is not "
        + "rendered here as current, greyed, or at all — so this number is the "
        + "fitted model alone. That is a disclosure, not a defect: the model is "
        + "the part that is auditable today.")));
    } else if (c.marketWeight === 0) {
      drawer.appendChild(namedGap("Market weight 0.00.", gapText(
        c.marketAgeH != null
          ? `The newest quote behind this fixture is ${ageText(c.marketAgeH)}, past the cutoff, `
          : "No usable quote covers this fixture, ",
        "so the market contributes nothing to the colour. The number above is "
        + "the fitted model alone.")));
    } else {
      addKv("market weight", fmt2(c.marketWeight));
      if (c.marketAgeH != null) addKv("newest quote", ageText(c.marketAgeH));
      if (c.nBooks != null) addKv("books", String(c.nBooks));
      if (c.marketResidual != null) addKv("refit residual", fmt2(c.marketResidual));
    }

    // 4 — the match as probabilities
    drawer.appendChild(el("h2", null, "The match, as probabilities"));
    if (c.pCleanSheet != null || c.raw.p_over_2_5 != null) {
      const k2 = el("div", "fx-kv");
      const add2 = (k, v) => { k2.appendChild(el("span", "k", k)); k2.appendChild(el("span", "v", v)); };
      if (c.pCleanSheet != null) add2(`P(${t.short} clean sheet)`, `${Math.round(c.pCleanSheet * 100)}%`);
      if (c.pCleanSheetMkt != null) add2("P(clean sheet), market", `${Math.round(c.pCleanSheetMkt * 100)}%`);
      if (c.raw.p_concede_2plus != null) add2("P(concede 2+)", `${Math.round(c.raw.p_concede_2plus * 100)}%`);
      if (c.raw.p_over_2_5 != null) add2("P(over 2.5)", `${Math.round(c.raw.p_over_2_5 * 100)}%`);
      drawer.appendChild(k2);
      if (c.pCleanSheet != null && c.pCleanSheetMkt != null
          && Math.abs(c.pCleanSheet - c.pCleanSheetMkt) > 0.03)
        drawer.appendChild(el("p", "sub",
          "The model and the market disagree by more than 3 points. They are "
          + "two estimators with different biases, so both are shown and neither "
          + "is averaged — the gap itself is the signal."));
    } else {
      drawer.appendChild(namedGap("No score matrix in this payload.", gapText(
        "Clean-sheet and over/under probabilities come from the score matrix in ",
        codeSpan("fpl_edge/models/team_goals/scoreline.py"),
        ", which the panel does not yet return. The rates that would feed it "
        + "are the same two numbers at the top of this drawer.")));
    }

    await detailSections(t, slot, c);
    crossLinks(t, c);
  }

  /* The this-week detail. Asks `fixture_detail` and renders exactly what comes
     back; every section that has nothing says WHICH table or script is missing,
     because a named gap is information and whitespace is not. */
  async function detailSections(t, slot, c) {
    const load = el("p", "sub", "loading match detail…");
    drawer.appendChild(load);
    const params = {};
    if (c.fixtureId != null) params.fixture_id = c.fixtureId;
    else { params.gw = slot.gw; if (t.code != null) params.team_code = t.code; }
    const r = await tryPanel("fixture_detail", params);
    if (!drawer.classList.contains("open")) return;
    load.remove();
    const D = flattenDetail((r.ok && r.result && !r.result.empty) ? r.result : null);

    section("Team news", D && D.team_news, rows => {
      const box = el("div", "fx-kv");
      for (const n of rows.slice(0, 12)) {
        box.appendChild(el("span", "k", n.player || n.team || "—"));
        const v = el("span", null,
          [n.status_text || n.news, n.as_of ? `(${ageText(ageHours(n.as_of))})` : null]
            .filter(Boolean).join(" "));
        box.appendChild(v);
      }
      return box;
    }, gapText(
      "Availability lives in ", codeSpan("fact_player_state"), " and ",
      codeSpan("intel_item"), " (kind ", codeSpan("availability"),
      "). Nothing in this payload carries it, so nothing is shown — a blank "
      + "here means “not fetched”, never “nobody is injured”."),
      "No team news in this payload.");

    section("Predicted XI", D && D.predicted_lineup, rows => {
      /* A column of twenty-four identical "expected" values is not information.
         Group by club, print the names as a line, and spend the annotation only
         on the players whose status actually differs from the default. */
      const byClub = new Map();
      for (const p of rows) {
        const nm = String(p.name || p.player || "—");
        const cut = nm.lastIndexOf(" · ");
        const club = cut > 0 ? nm.slice(cut + 3) : "";
        const who = cut > 0 ? nm.slice(0, cut) : nm;
        if (!byClub.has(club)) byClub.set(club, { start: [], other: [] });
        const role = p.role && p.role !== "expected" ? String(p.role) : null;
        const starts = p.starts !== false;
        if (starts) byClub.get(club).start.push(role ? `${who} (${role})` : who);
        else if (role) byClub.get(club).other.push(`${who} (${role})`);
      }
      const box = el("div", "fx-xi");
      for (const [club, g] of byClub) {
        const h = el("div", "fx-xi-club");
        h.appendChild(el("b", null, club || "—"));
        h.appendChild(el("span", "n", `${g.start.length} predicted to start`));
        box.appendChild(h);
        if (g.start.length) box.appendChild(el("p", "fx-xi-names", g.start.join(", ")));
        if (g.other.length)
          box.appendChild(el("p", "fx-xi-other", "not starting: " + g.other.join(", ")));
      }
      return box;
    }, gapText(
      codeSpan("fact_predicted_lineup"), " holds predictions for gameweeks the "
      + "provider has published. The panel does not return them, so none are "
      + "drawn. Providers usually publish around T−48h, so an early "
      + "gameweek in the horizon legitimately has none."),
      "No predicted XI in this payload.");

    section("Set pieces", D && D.set_pieces, rows => {
      const box = el("div", "fx-kv");
      for (const s of rows.slice(0, 12)) {
        box.appendChild(el("span", "k", s.duty || s.kind || "duty"));
        box.appendChild(el("span", null, s.player || s.detail || "—"));
      }
      return box;
    }, gapText(
      "Set-piece duty is the highest-value team-level intel in the warehouse (",
      codeSpan("set_piece_duty"), ", ", codeSpan("set_piece_change"),
      ") and nothing in the UI renders it yet. It belongs here as DUTY — who "
      + "takes them — and not as a team trait: set-piece goals-over-expected "
      + "barely persists season to season, while who takes the corner does."),
      "Set-piece duty is in the warehouse and not in this payload.");

    section("Previous meetings", D && D.previous_meetings, rows => {
      const box = el("div", "fx-kv");
      for (const m of rows.slice(0, 8)) {
        box.appendChild(el("span", "k", m.season || m.date || "—"));
        box.appendChild(el("span", "v",
          [m.score, m.xg ? `xG ${m.xg}` : null].filter(Boolean).join("  ")));
      }
      const cav = el("p", "sub",
        "A handful of matches across several seasons, with different managers "
        + "and mostly different players, is not evidence about this one. "
        + "Head-to-head is the most over-read object in fixture analysis.");
      const f = document.createDocumentFragment(); f.append(box, cav); return f;
    }, gapText(
      "Completed meetings would come from ", codeSpan("fact_fixture"),
      " in both orientations. The panel returns none. If these two clubs have "
      + "never met in the Premier League there is nothing to show and nothing "
      + "to infer — but this page cannot currently tell you which of those two "
      + "it is, and it will not guess."),
      "No previous meetings in this payload.");

    section("Creator team-talk", D && D.creator_talk, rows => {
      const box = el("div");
      for (const q of rows.slice(0, 6)) {
        const p = el("p", "sub");
        p.appendChild(el("b", null, `“${q.quote || q.text || ""}” `));
        p.appendChild(document.createTextNode(
          [q.creator, q.published_at ? ageText(ageHours(q.published_at)) : null]
            .filter(Boolean).join(" · ")));
        box.appendChild(p);
      }
      return box;
    }, gapText(
      codeSpan("content_insight"), " is built and holds zero rows: the "
      + "extraction step has no caller in the ingest pipeline, so team-level "
      + "talk that has already been paid for is never written. This section is "
      + "designed and wired; it is empty because the row is missing, not "
      + "because nobody said anything."),
      "Creator team-talk is extracted but never written.");

    section("Press & scout links", D && D.press_conference, rows => {
      const box = el("div");
      for (const q of rows.slice(0, 6)) {
        const line = el("p", "sub");
        const a = q.source_url ? el("a", "chip src", q.headline || "link") : el("b", null, q.headline || "—");
        if (q.source_url) { a.href = q.source_url; a.target = "_blank"; a.rel = "noopener noreferrer";
                            a.style.textDecoration = "none"; }
        line.appendChild(a);
        if (q.age_hours != null)
          line.appendChild(document.createTextNode(` · ${ageText(q.age_hours)}`));
        if (q.confidence) line.appendChild(document.createTextNode(` · ${q.confidence}`));
        box.appendChild(line);
      }
      const cav = el("p", "sub",
        "These are FPL's own scout links, dated to the first poll that carried "
        + "them because FPL publishes no timestamp for the field. Treat the age "
        + "as an upper bound on freshness, not a publication time.");
      const f = document.createDocumentFragment(); f.append(box, cav); return f;
    }, gapText(
      "Press-conference and scout links would come from ", codeSpan("intel_item"),
      ". None reached this fixture."),
      "No press or scout links for this fixture.");

    section("Style", D && D.style, s => {
      const box = el("div", "fx-kv");
      for (const [k, v] of Object.entries(s))
        { box.appendChild(el("span", "k", k)); box.appendChild(el("span", "v", String(v))); }
      return box;
    }, "What this warehouse can honestly say about style is team xG for and "
      + "against, goals versus xG, and clean-sheet rate, split home and away. "
      + "What it cannot say is PPDA, field tilt, sequence types or line height "
      + "— that event data is not here, and inventing it would be the worst "
      + "thing this page could do. Style explains a fixture; it is never "
      + "allowed into the colour.",
      "No style summary in this payload.");

    if (!r.ok) {
      drawer.appendChild(el("p", "sub",
        r.missing
          ? "fixture_detail is not registered, so every section above is a named "
            + "gap rather than a fetch failure."
          : `fixture_detail failed: ${String(r.error.message || r.error)}`));
    }

    function section(title, data, render, gap, gapTitle) {
      drawer.appendChild(el("h2", null, title));
      const has = Array.isArray(data) ? data.length : (data && Object.keys(data).length);
      if (has) drawer.appendChild(render(data));
      else drawer.appendChild(namedGap(gapTitle, gap));
    }
  }

  function crossLinks(t, c) {
    drawer.appendChild(el("h2", null, "Elsewhere"));
    const links = el("div", "fx-links");
    const mk = (href, label, title) => {
      const a = el("a", "chip src", label);
      a.href = href; a.title = title; a.style.textDecoration = "none";
      return a;
    };
    links.appendChild(mk("#xpoints", `${t.short} projections`,
      "the per-player numbers for this club"));
    links.appendChild(mk("#template", `${t.short} ownership`,
      "an easy run everyone can see is priced into the field's transfers — the "
      + "same run on a 2%-owned club is an edge, on a 60%-owned club it is "
      + "insurance"));
    links.appendChild(mk("#creators", "creator coverage",
      "who has said what about these clubs"));
    drawer.appendChild(links);
    drawer.appendChild(el("p", "sub",
      "Difficulty is a fact about football; effective ownership is a fact about "
      + "managers. They are never folded into one number here."));
  }

  async function openClub(t) {
    drawer.textContent = "";
    drawer.classList.add("open");
    drawer.scrollTop = 0;
    drawer.appendChild(drawerHead(t.name || t.short,
      `GW${M.gws[0]}–GW${M.gws[M.gws.length - 1]} · ${t.nFixtures} fixture`
      + `${t.nFixtures === 1 ? "" : "s"}`
      + (t.nBlanks ? ` · ${t.nBlanks} blank` : "")
      + (t.nDoubles ? ` · ${t.nDoubles} double` : "")));

    drawer.appendChild(el("h2", null, "The run"));
    const box = el("div", "fx-lens");
    for (const g of M.gws) {
      const slot = t.byGw.get(g);
      const r = el("div", "fx-lensrow");
      r.appendChild(el("span", "lk", `GW${g}`));
      if (slot.blank) {
        const w = el("span", "sub", "blank — no fixture, which is not a zero");
        r.appendChild(w); r.appendChild(el("span", "lv", "—"));
      } else {
        const c = slot.opps[0];
        const track = el("div", "fx-track");
        const v = c.easeAtt != null ? c.easeAtt : c.easeBlend;
        if (v != null) {
          const frac = Math.max(-1, Math.min(1, v / M.scale.dom));
          const fill = el("div", "fx-fill " + (cls(v, M.scale.dom) || "fx-n0"));
          if (frac >= 0) { fill.style.left = "50%"; fill.style.width = `${frac * 50}%`; }
          else { fill.style.right = "50%"; fill.style.width = `${-frac * 50}%`; }
          track.appendChild(fill);
        }
        r.appendChild(track);
        r.appendChild(el("span", "lv",
          `${c.label} ${c.easeAtt != null && c.easeDef != null
            ? sgn2(c.easeAtt) + "/" + sgn2(c.easeDef) : sgn2(c.easeBlend)}`));
      }
      box.appendChild(r);
    }
    drawer.appendChild(box);
    drawer.appendChild(el("p", "sub",
      M.anySplit
        ? "Bars show the attack lens; the pair beside each is attack / defence. "
          + "Click a cell in the grid for the full match detail."
        : "One blended number per fixture — the split is unavailable in this "
          + "payload."));

    if (t.form && num(t.form.window_matches) != null) {
      drawer.appendChild(el("h2", null, "Form, as a residual"));
      const k = el("div", "fx-kv");
      const add = (a, b) => { k.appendChild(el("span", "k", a)); k.appendChild(el("span", "v", b)); };
      add("matches in window", String(t.form.window_matches));
      if (num(t.form.xg_for_resid) != null) add("xG for, vs its rating", sgn2(t.form.xg_for_resid));
      if (num(t.form.xg_against_resid) != null) add("xG against, vs its rating", sgn2(t.form.xg_against_resid));
      drawer.appendChild(k);
      drawer.appendChild(el("p", "sub",
        t.form.window_matches < 6
          ? `Only ${t.form.window_matches} completed match`
            + `${t.form.window_matches === 1 ? "" : "es"} this season — this is `
            + "noise, and it is printed as a count rather than drawn as a "
            + "confident line."
          : "This is a residual against the fitted rating, not a third input. "
            + "It says the colour might be wrong; it never changes the colour."));
    } else {
      drawer.appendChild(el("h2", null, "Form, as a residual"));
      drawer.appendChild(namedGap("No form residual in this payload.",
        "Team xG for and against over the last few matches, minus what the "
        + "fitted rating expected, is a diagnostic that the colour might be "
        + "wrong. It is deliberately not a third input to the colour, and it "
        + "is not carried here."));
    }
  }

  await load();
}
