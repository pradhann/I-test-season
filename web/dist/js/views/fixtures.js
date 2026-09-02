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

import { runPanel, el, emptyBox, errBox, provenance, fmt1, fmt2 } from "/js/app.js";
import { crest } from "/js/components/clubmark.js";

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
      attRankH: num(h.attack_rank), defRankH: num(h.defence_rank),
      rankGap: num(h.rank_gap),
      rating: t.rating || null,
      form: t.form || null,
      priorShare: num((t.rating || {}).prior_share) ?? num(t.rating_prior_share),
    });
  }

  /* Tornness has exactly one owner: the served `divergent[]` list. The panel
     applied its rank-gap rule once, over the same population it ranked, and
     every torn mark on the page — the cell seam, the rail's ⇄, the torn card,
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

  return { scale, gws, teams, anySplit, anyBlend, divergent, tornMap, res };
}

/* ----------------------------------------------------- the form chip ---
   One component, two honest states, keyed on the served `form` block.

   n < 3: a count badge and nothing else. One match is not form, and hollow
   or faded marks positioned by one match of noise are still marks — the
   board refuses to draw them and says why on hover. The served per-game
   rates live in the tooltip and the drawers, where they read as rates.

   n >= 3: two outlined pills carrying the RESIDUALS against the club's own
   fitted rating — the actual form diagnostic — with xg_against flipped so
   positive is always good. Pill tint uses the page's diverging vocabulary at
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
        { cls: attR >= 0 ? "up" : "dn", text: `ATT ${sgn1(attR)}`,
          title: `xG for, vs the fitted rating: ${sgn2(attR)} over ${n} `
            + `match${n === 1 ? "" : "es"}. A residual against the fitted `
            + "rating — it says the colour might be wrong; it never changes "
            + "the colour." },
        { cls: flip >= 0 ? "up" : "dn", text: `DEF ${sgn1(flip)}`,
          title: `xG against, vs the fitted rating (flipped so positive is `
            + `good): ${sgn2(flip)} over ${n} match${n === 1 ? "" : "es"}.` },
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
function formChipEl(form) {
  const spec = formChipSpec(form);
  if (!spec) return null;               // legacy payload: the chip is absent
  if (spec.state === "smalln") {
    const b = el("span", "formchip smalln", spec.text);
    b.title = spec.title;
    return b;
  }
  const box = el("span", "formchip resid");
  for (const ps of spec.pills) {
    const pill = el("b", "pill " + ps.cls, ps.text);
    pill.title = ps.title;
    box.appendChild(pill);
  }
  return box;
}

/* ------------------------------------------------------------------ view */

export default async function fixtures(host) {
  /* One drawer per visit — re-entering the view must not stack a second one
     on the body, and the previous visit's key handler must stop listening. */
  document.querySelectorAll("aside.fx-drawer").forEach(n => n.remove());
  const drawer = el("aside", "drawer fx-drawer");
  document.body.appendChild(drawer);
  const closeDrawer = () => { drawer.classList.remove("open"); clearInputSel(); };
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
  card.appendChild(el("h2", null, "Fixture board"));

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
  const controls = el("div", "fx-controls");
  controls.append(horizonRow, lensRow);
  const verdictEl = el("div", "fx-verdict");
  verdictEl.hidden = true;
  const body = el("div");
  const foot = el("div");
  card.append(freshRow, controls, verdictEl, body, foot);
  host.appendChild(card);

  const tornCard = el("section", "card");
  const shapeCard = el("section", "card");
  host.append(tornCard, shapeCard);

  // ---- state ----
  let selInput = null;            // name of the selected inputs[] row, or null
  let freshRes = null;            // the payload renderFreshness last drew
  let horizon = 6;
  let fromGw = null;              // null = the panel's own default (next GW)
  let lens = "both";              // both | attack | defence — and the SORT
  let tableView = false;          // Table shows the last grid state's order
  let azSort = false;             // the look-one-club-up escape hatch
  let M = null, prov = null, scriptUsed = null, boardErr = null;

  /* --------------------------------------------------------- data fetch */
  async function load() {
    body.textContent = "";
    body.appendChild(el("p", "sub", "loading…"));
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
      body.appendChild(errBox(r.error));
      body.appendChild(el("p", "sub",
        "The split panel refused this request, so there is nothing to draw. "
        + "(The legacy blended ticker is deleted — fixture_board carries its "
        + "number as legacy_difficulty, so there is no second panel to ask.) "
        + "The page shows the failure rather than an empty grid, because an "
        + "empty grid would read as “no fixtures”."));
      verdictEl.hidden = tornCard.hidden = shapeCard.hidden = true;
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
      verdictEl.hidden = tornCard.hidden = shapeCard.hidden = true;
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
    freshRes = res;
    freshRow.textContent = "";
    freshRow.appendChild(el("span", "tlabel", "Inputs"));
    const inputs = Array.isArray(res.inputs) ? res.inputs : null;

    if (inputs && inputs.length) {
      for (const i of inputs) {
        const h = num(i.age_hours) ?? ageHours(i.as_of);
        const state = i.state || (h == null ? "missing" : "fresh");
        const on = selInput === i.name;
        const chip = el("button", "fx-inchip"
          + (state === "stale" || state === "failed" ? " stale"
            : state === "degraded" ? " warn"
            : state === "missing" ? " missing" : "")
          + (on ? " on" : ""));
        chip.appendChild(el("span", "freshdot "
          + (state === "fresh" ? "good" : state === "degraded" ? "warn" : "bad")));
        chip.appendChild(el("b", null, i.name));
        chip.appendChild(el("span", "age", h == null ? "—" : ageText(h)));
        if (state !== "fresh") chip.appendChild(el("span", "tag", state));
        chip.setAttribute("aria-pressed", String(on));
        chip.title = [
          i.as_of ? `as of ${i.as_of}` : null,
          i.rows != null ? `${Number(i.rows).toLocaleString()} rows` : null,
          i.effect_when_stale ? `when stale: ${i.effect_when_stale}` : null,
          i.refresh_job ? `refreshed by ${i.refresh_job}` : null,
          i.last_job_outcome ? `last run: ${i.last_job_outcome}` : null,
          on ? "click again to close the inspector" : "click to inspect this input",
        ].filter(Boolean).join("\n");
        chip.onclick = () => {
          if (selInput === i.name) { clearInputSel(); closeDrawer(); }
          else selectInput(i);
        };
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

  /* ------------------------------------------------- the input inspector ---
     A third drawer alongside the fixture and the club: click an input chip
     and the page shows what that input IS, how old it is against its own
     staleness budget, and what on this page reads it — while the fed
     elements carry an edge-mark, so provenance is something you can see
     rather than something you take on faith. Everything in the inspector is
     the served inputs[] row; the only thing the page adds is the feeds map,
     which is a statement about THIS page, not about the data. */
  const INPUT_FEEDS = {
    ratings: "every cell colour and both rail bars — the difficulty itself",
    schedule: "the gameweek columns, and which cells are blanks or doubles",
    market: "cell tooltips and the drawer's Market act — never any colour: "
      + "nothing visible on the board carries a market number, by design, so "
      + "nothing lights up out there",
    form: "the rail form chips and the drawer's Record act — never any colour",
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
    drawer.classList.add("open");
    drawer.scrollTop = 0;
    drawer.appendChild(masthead(null, String(i.name || "input"),
      i.source ? String(i.source) : null, null));

    drawer.appendChild(el("h2", null, "What this input is"));
    if (i.detail) drawer.appendChild(el("p", "sub", String(i.detail)));
    else drawer.appendChild(namedGap("No detail served for this input.",
      gapText("The ", codeSpan("inputs[]"), " row carries no ",
        codeSpan("detail"), " field — its name and the ages here are "
        + "everything the panel said about it.")));

    drawer.appendChild(el("h2", null, "Age, against its own budget"));
    const h = num(i.age_hours) ?? ageHours(i.as_of);
    const thr = num(i.stale_after_hours);
    const kv = el("div", "fx-kv");
    const add = (k, v) => { kv.appendChild(el("span", "k", k)); kv.appendChild(el("span", "v", v)); };
    add("age", h == null ? "unknown" : ageText(h));
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
      bar.title = `${ageText(h)} of a ${Math.round(thr)}h budget`;
      drawer.appendChild(bar);
      drawer.appendChild(el("p", "sub",
        `${ageText(h)} of a ${Math.round(thr)}h staleness budget`
        + (h > thr ? " — over budget" : "")));
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
        "one cell, two bands — attackers above, defenders below; rows sorted "
        + "by the attack rank"],
      [null, true, "Table",
        "the same numbers as a sortable table, in the grid's current order; "
        + "colour is never the only way to read this page"],
    ]) {
      const on = isTable ? tableView : (!tableView && k === lens);
      const b = el("button", on ? "on" : "", label);
      b.title = title;
      b.disabled = !M.anySplit && !isTable && k !== "both";
      if (b.disabled) b.title = "the split is not in this payload — see the note below";
      b.setAttribute("aria-pressed", String(on));
      b.onclick = () => {
        if (isTable) tableView = true;
        else { lens = k; tableView = false; azSort = false; }
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
        + "deprecated legacy_difficulty — ONE blended number per fixture. A blended "
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
        " is not serving — shortlists and tears are two-lens findings, and "
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

    const shortlist = (label, clubs, rankOf, sumOf, kind) => {
      const r = el("div", "vrow");
      r.appendChild(el("span", "vlab", label));
      for (const t of clubs) {
        const chip = el("button", "vchip" + (onBoth.has(t.code) ? " both2" : ""));
        chip.appendChild(crest(t.code, t.short, "s14"));
        chip.appendChild(el("b", null, t.short));
        chip.appendChild(el("span", "rk", `#${rankOf(t)}`));
        const sum = sumOf(t);
        const pg = (sum != null && t.nFixtures) ? sum / t.nFixtures : null;
        chip.title = `${kind} ease ${sgn2(sum)} over ${M.gws.length} GWs`
          + ` · ${sgn2(pg)}/gm · rank ${rankOf(t)} of ${nClubs}`
          + `\nclick to jump to ${t.short}'s row on the board`;
        if (onBoth.has(t.code)) {
          /* the ring alone is colour-only signalling, so it never travels
             without the printed suffix and the words */
          chip.appendChild(el("span", "x2", "×2"));
          chip.title = "on both shortlists — easy for attackers and defenders\n"
            + chip.title;
        }
        chip.onclick = () => bookmark(t);
        r.appendChild(chip);
      }
      return r;
    };
    if (anyRanks) {
      verdictEl.appendChild(shortlist("buy attackers from", attTop,
        t => t.attRankH, t => t.attSum, "attack"));
      verdictEl.appendChild(shortlist("buy defenders from", defTop,
        t => t.defRankH, t => t.defSum, "defence"));
    } else {
      verdictEl.appendChild(namedGap("No horizon ranks in this payload.",
        "Sort the rail by lens instead — the shortlists render only from the "
        + "panel's own ranks, never from arithmetic done here."));
    }

    /* the tear headline, grouped by opponent-venue so five near-identical
       sentences about one club collapse into the one finding they are */
    const tearRow = (g, quiet) => {
      const top = g[0];
      const r = el("div", "vrow tear" + (quiet ? " quiet" : ""));
      r.appendChild(el("span", "vlab", quiet ? "and the other way" : "the torn opponent"));
      const txt = el("span", "txt");
      txt.appendChild(el("b", null,
        `${top.is_home ? "hosting" : "visiting"} ${top.opponent}`));
      const pop = num(M.res.scale && M.res.scale.population) || nClubs * 2;
      txt.appendChild(document.createTextNode(
        `: ${top.attack_rank}/${pop} attacking, ${top.defence_rank}/${pop} `
        + `defensive — ${g.map(d => `${d.short_name} GW${d.gw}`).join(" · ")}`));
      r.appendChild(txt);
      const open = el("button", "chip", "open");
      open.title = `open ${top.short_name} ${top.is_home ? "v" : "at"} `
        + `${top.opponent}, GW${top.gw}`;
      open.onclick = () => openDivergent(top);
      r.appendChild(open);
      return r;
    };
    const groups = tornGroups();
    if (!groups.length) {
      const r = el("div", "vrow tear");
      r.appendChild(el("span", "vlab", "the torn opponent"));
      r.appendChild(el("span", "txt",
        "no torn fixtures in this window — a real finding, not an empty "
        + "state: over this window the split does not change any decision"));
      verdictEl.appendChild(r);
    } else {
      verdictEl.appendChild(tearRow(groups[0], false));
      const sign = (groups[0][0].gap || 0) > 0;
      const other = groups.find(g => ((g[0].gap || 0) > 0) !== sign);
      if (other) verdictEl.appendChild(tearRow(other, true));
    }

    /* the caveat is part of the component, and its band is read off the
       served calibration — never hardcoded */
    const cal = M.res.calibration || null;
    const model = cal && cal.model, emp = cal && cal.empirical;
    const ratios = [model && num(model.ratio_attack), model && num(model.ratio_defence),
                    emp && num(emp.outfield_ratio)].filter(v => v != null);
    const range = M.gws.length ? `GW${M.gws[0]}–GW${M.gws[M.gws.length - 1]}` : "this window";
    verdictEl.appendChild(el("p", "vcaveat",
      ratios.length
        ? `ranks are ease sums over ${range} · tie-breakers only — club `
          + `quality is ${fmt1(Math.min(...ratios))}–${fmt1(Math.max(...ratios))}× this`
        : `ranks are ease sums over ${range} · no calibration served — treat `
          + "them as tie-breakers, not asset-pickers"));
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
    else if (lens === "defence") t.sort(byRank(x => x.defRankH, x => x.defSum));
    else t.sort(byRank(x => x.attRankH, x => x.attSum));
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
    ordered.forEach((t) => {
      grid.appendChild(railCell(t, railMax));
      for (const g of M.gws) grid.appendChild(gwCell(t, t.byGw.get(g)));
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
    d.title = `${t.name} — ${t.nFixtures} fixture${t.nFixtures === 1 ? "" : "s"} `
      + `in GW${M.gws[0]}–GW${M.gws[M.gws.length - 1]}`
      + (t.nBlanks ? `, ${t.nBlanks} blank` : "")
      + (t.nDoubles ? `, ${t.nDoubles} double` : "")
      + "\nclick for the club's run";
    d.appendChild(crest(t.code, t.short, "s20"));
    const nm = el("span", "nm");
    const label = el("span", null, t.short);
    if (t.priorShare != null && t.priorShare > 0.4) {
      label.className = "fx-prior";
      label.title = `${Math.round(t.priorShare * 100)}% of this club's rating is `
        + "prior, not data — newly promoted, so read its colours gently";
    }
    nm.appendChild(label);
    if (t.tornRows && t.tornRows.length) {
      const z = el("i", "fx-torn2", "⇄");
      z.title = `${t.tornRows.length} torn fixture`
        + `${t.tornRows.length === 1 ? "" : "s"} in this window — the attack `
        + "and defence answers point opposite ways";
      nm.appendChild(z);
    }
    d.appendChild(nm);
    const chip = formChipEl(t.form);
    if (chip) d.appendChild(chip);

    /* LENGTH encodes the horizon SUM from a centre line that IS the
       league-average fixture — doubles and blanks handled natively, because
       more fixtures really is more chances. COLOUR is the per-game average,
       so the tint sits on exactly the cells' scale. Length survives colour
       blindness, print and forced-colours on its own. */
    const rr = (letter, rank, sum, what) => {
      const row = el("span", "rr");
      row.appendChild(el("i", null, letter));
      row.appendChild(el("b", null, rank != null ? `#${rank}` : "–"));
      row.appendChild(railTrack(sum, railMax, t.nFixtures));
      row.title = sum == null
        ? `no ${what} number for this club in this window`
        : `${what}: ${sgn2(sum)} summed over ${t.nFixtures} fixture`
          + `${t.nFixtures === 1 ? "" : "s"} (${sgn2(sum / t.nFixtures)} per game)`
          + ` · ${M.scale.unit}`
          + (rank != null ? ` · rank ${rank} of ${M.teams.length}` : "");
      return row;
    };
    if (M.anySplit) {
      d.appendChild(rr("A", t.attRankH, t.attSum, "attackers"));
      d.appendChild(rr("D", t.defRankH, t.defSum, "defenders"));
    } else {
      const v = t.blendMean == null ? null : t.blendMean * t.nFixtures;
      d.appendChild(rr("R", null, v, "blended (not split)"));
    }
    d.onclick = () => openClub(t);
    d.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openClub(t); } };
    return d;
  }

  function railTrack(sum, max, nFixtures) {
    const track = el("div", "fx-track");
    if (sum != null && max) {
      const perGame = nFixtures ? sum / nFixtures : null;
      const frac = Math.max(-1, Math.min(1, sum / max));
      const fill = el("div", "fx-fill " + (cls(perGame, M.scale.dom) || "fx-n0"));
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
    const hasSplit = c.easeAtt != null && c.easeDef != null;
    const btn = el("button", "fx-cell");
    const torn = (c.fixtureId != null && t.code != null)
      ? M.tornMap.get(`${c.fixtureId}|${t.code}`) : null;

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
      btn.appendChild(el("span", "seam" + (torn ? " torn" : "")));
    }
    btn.appendChild(el("span", "opp", c.label));
    /* The Both lens carries no resident numerals: ~240 signed numbers on the
       scan path taxed the very glance the two-band cell was bought for. A solo
       lens is arithmetic, so its one number returns; the rest live in the
       tooltip, the Table view and the drawer. */
    if (solo && known)
      btn.appendChild(el("span", "vv", sgn2(showAtt).replace("0.", ".")));
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
      torn ? String(torn.sentence || "") : null,
      c.marketState
        ? `market: ${c.marketState}`
          + (c.nBooks != null ? ` · ${c.nBooks} books` : "")
          + (c.marketAgeH != null ? ` · ${ageText(c.marketAgeH)}` : "")
        : null,
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
    if (M.anySplit)
      keys.appendChild(keyItem("seam", "a dashed seam = a torn fixture — the attack and defence answers point opposite ways"));
    keys.appendChild(keyItem(null, "hover any cell for its numbers · single-lens and table views print them"));
    L.appendChild(keys);
    return L;
  }
  function keyItem(kind, text) {
    const k = el("span", "k");
    if (kind === "hatch") k.appendChild(el("span", "fx-swatch-hatch"));
    if (kind === "seam") k.appendChild(el("span", "fx-swatch-seam"));
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

  /* ---------------------- where the lenses disagree (the appendix card)
     Fed ONLY by the served divergent[] list — see buildModel. Grouped by
     (opponent, venue) because the tear belongs to the opponent-venue, not to
     the visiting club: the served list is |gap|-sorted and its top dozen rows
     are all visits to the same club, so an ungrouped top-5 spends four of its
     five slots repeating one finding. */
  function renderTorn() {
    tornCard.textContent = "";
    tornCard.hidden = false;
    tornCard.appendChild(el("h2", null, "Where the lenses disagree"));
    if (!M.anySplit) {
      tornCard.appendChild(el("p", "sub",
        "This strip finds the fixtures where the attack answer and the defence "
        + "answer point opposite ways — the ones a single FDR number reports as "
        + "\u201Caverage\u201D, which is the one thing they are not."));
      tornCard.appendChild(namedGap("Needs the split.", gapText(
        "The payload carries one blended number per fixture, so there is no "
        + "disagreement to find. This strip lights up when ",
        codeSpan("fixture_board"),
        " serves attack and defence separately.")));
      return;
    }
    const rows = M.divergent;
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
    for (const g of tornGroups().slice(0, 5)) {
      const top = g[0];
      const row = el("div", "fx-torn-row");
      const head = el("div", "hd");
      head.appendChild(crest(top.opponent_code, top.opponent, "s14"));
      head.appendChild(el("b", null,
        `${top.is_home ? "hosting" : "visiting"} ${top.opponent}`));
      row.appendChild(head);
      /* The panel's own sentence is the finding, verbatim — the rows in a
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
    const nGroups = tornGroups().length;
    if (nGroups > 5)
      tornCard.appendChild(el("p", "sub",
        `${nGroups - 5} more torn opponent-venues in this window — every one `
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
    shapeCard.appendChild(el("h2", null, "League shape"));
    const withRating = M.teams.filter(t => t.rating
      && num(t.rating.attack) != null && num(t.rating.defence) != null);
    if (!withRating.length) {
      shapeCard.appendChild(namedGap("No fitted ratings in this payload.",
        gapText(
          "The map draws ", codeSpan("rating.attack"), " and ",
          codeSpan("rating.defence"), " per club, which ride along with ",
          codeSpan("fixture_board"), ". A payload without a stored fit carries neither, so "
          + "there is no quality to place — and the page will not infer one "
          + "from blended difficulties.")));
      return;
    }

    const cal = M.res.calibration || {};
    const model = cal.model || null, emp = cal.empirical || null;
    const ratios = [model && num(model.ratio_attack), model && num(model.ratio_defence),
                    emp && num(emp.outfield_ratio)].filter(v => v != null);
    shapeCard.appendChild(el("p", "sub",
      "The fitted attack and defence ratings behind every colour above — team "
      + "quality, which is what the board deliberately holds constant. "
      + (ratios.length
        ? `Club quality is worth ${fmt1(Math.min(...ratios))}–`
          + `${fmt1(Math.max(...ratios))}× the fixture swing; this map is the `
          + `${fmt1(Math.max(...ratios))}×.`
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
        svg.appendChild(mk("text", { class: "ticklab", x: P - 6, y: sy(v) + 3,
          "text-anchor": "end" }, (v > 0 ? "+" : "−") + ".25"));
      }
    }
    const q = (x, y, anchor, text) =>
      svg.appendChild(mk("text", { class: "quad", x, y, "text-anchor": anchor }, text));
    q(W - P - 4, P + 12, "end", "STRONG ATTACK · TIGHT DEFENCE");
    q(P + 4, P + 12, "start", "BLUNT · TIGHT");
    q(W - P - 4, H - P - 6, "end", "STRONG ATTACK · LEAKY");
    q(P + 4, H - P - 6, "start", "BLUNT · LEAKY");
    svg.appendChild(mk("text", { class: "axlab", x: W - P, y: H - 6,
      "text-anchor": "end" }, "stronger attack →"));
    svg.appendChild(mk("text", { class: "axlab", x: 6, y: P - 8 },
      "↑ tighter defence"));

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
      b.title = [
        t.name,
        `attack ${sgn2(num(t.rating.attack))} · defence ${sgn2(num(t.rating.defence))} `
          + "goals vs league average, per match",
        num(t.rating.attack_rank) != null
          ? `fitted #${t.rating.attack_rank} attack · #${t.rating.defence_rank} `
            + `defence of ${withRating.length}` : null,
        num(t.rating.matches_seen) != null
          ? `${t.rating.matches_seen} matches in the fit` : null,
        t.rating.is_promoted
          ? "promoted — the fit leans on a prior, so read this mark gently" : null,
        "click for the club's run",
      ].filter(Boolean).join("\n");
      b.onclick = () => openClub(t);
      wrap.appendChild(b);
    }
    shapeCard.appendChild(wrap);
    shapeCard.appendChild(el("p", "sub",
      "Both axes are goals versus a league-average opponent, per match, from "
      + "the same fit as every colour above. The crosshair is league average. "
      + "This is the map you pick assets on; the board above is the "
      + "tie-breaker."));
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
     the page. `right` is the second 34px crest for a fixture, or null for
     the club drawer. */
  function masthead(leftCrest, title, sub, rightCrest) {
    const head = el("div", "fx-dh");
    const mh = el("div", "fx-mast");
    if (leftCrest) mh.appendChild(leftCrest);
    const mid = el("div", "mid");
    mid.appendChild(el("div", "dname", title));
    if (sub) mid.appendChild(el("div", "sub", sub));
    mh.appendChild(mid);
    if (rightCrest) mh.appendChild(rightCrest);
    head.appendChild(mh);
    const close = el("button", null, "✕");
    close.onclick = closeDrawer;
    head.appendChild(close);
    return head;
  }

  function lensBars(c, unramped) {
    const box = el("div", "fx-lens" + (unramped ? " unramped" : ""));
    const one = (k, v, rank) => {
      const r = el("div", "fx-lensrow");
      r.appendChild(el("span", "lk", k));
      const track = el("div", "fx-track");
      if (v != null) {
        const frac = Math.max(-1, Math.min(1, v / M.scale.dom));
        const fill = el("div", "fx-fill "
          + (unramped ? "" : (cls(v, M.scale.dom) || "fx-n0")));
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
    clearInputSel();
    drawer.textContent = "";
    drawer.classList.add("open");
    drawer.scrollTop = 0;

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
      fresh.appendChild(mastFreshChip("ratings",
        h == null ? "age unknown" : ageText(h), false,
        String(ratings.detail || "")));
    }
    fresh.appendChild(mastFreshChip("market",
      c.marketState !== "priced"
        ? (c.marketState || "absent")
        : [c.marketAgeH != null ? ageText(c.marketAgeH) : "age unknown",
           c.nBooks != null ? `${c.nBooks} books` : null].filter(Boolean).join(" · "),
      c.marketState !== "priced",
      "the market is never blended into any difficulty — see the Market act"));
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
    A1.appendChild(el("h2", null, "The two answers"));
    A1.appendChild(lensBars(c));
    A1.appendChild(el("p", "sub",
      (c.easeAtt != null && c.easeDef != null)
        ? `${M.scale.unit}. Positive is easier. Both bars are on the same axis `
          + "as the grid above, and neither is an average of the other."
        : c.easeBlend != null
          ? "One blended number, because that is all the payload carries. It is "
            + "the average of two different questions."
          : "The panel has no fitted rating for this fixture. The schedule is "
            + "still a fact; the difficulty is not known."));

    A1.appendChild(el("h2", null, "What the cell colour assumed"));
    const asm = el("p", "sub");
    asm.textContent = `The grid held ${t.short} at league average and asked only `
      + `what ${c.opponent} does ${c.isHome ? "away" : "at home"}. That is why `
      + `every club visiting ${c.opponent} gets this same colour. The `
      + "fixture-specific number — the one with " + t.short + "'s own strength "
      + "in it — belongs here.";
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
      }, true));
      A1.appendChild(el("p", "sub",
        `with ${t.short}'s strength added back — a different number, `
        + "deliberately not on the grid's ramp (its domain is calibrated on "
        + "the opponent-only population)."));
    } else {
      A1.appendChild(namedGap("The fixture-specific number is not in this payload.",
        gapText(
          "This is the drawer's job and it cannot do it yet: the panel returns "
          + "opponent-only ease and no relative (own-club-adjusted) figure. It "
          + "would come from the same fit — our own ",
          codeSpan("attack_O"), " and ", codeSpan("defence_O"),
          " added back in place of the league-average anchor — and the page will "
          + "not compute it in the browser, because a number modelled in the UI "
          + "is a number nobody can audit.")));
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
          + "is averaged — the gap itself is the signal."));
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
        + `${c.marketAgeH != null ? ageText(c.marketAgeH) : "age unknown"}`));
      A2.appendChild(kvm);
      A2.appendChild(kvNote(
        "Priced, and deliberately not blended into the colour.",
        gapText(
          "The quote is here and it is dated, so you can read it against the "
          + "model below. It is not averaged into the number above because the "
          + "blend weight in ", codeSpan("blend.py"),
          " has never been tuned out of sample — a blend on an untuned constant "
          + "is a guess wearing a number's clothes. Model and market are shown "
          + "side by side and the gap between them is left for you to read.")));
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
        + "rendered here as current, greyed, or at all — so this number is the "
        + "fitted model alone. That is a disclosure, not a defect: the model is "
        + "the part that is auditable today.")));
    } else if (c.marketWeight === 0) {
      A2.appendChild(namedGap("Market weight 0.00.", gapText(
        c.marketAgeH != null
          ? `The newest quote behind this fixture is ${ageText(c.marketAgeH)}, past the cutoff, `
          : "No usable quote covers this fixture, ",
        "so the market contributes nothing to the colour. The number above is "
        + "the fitted model alone.")));
    } else {
      const kvm = el("div", "fx-kv");
      const addM = (k, v) => { kvm.appendChild(el("span", "k", k)); kvm.appendChild(el("span", "v", v)); };
      addM("market weight", fmt2(c.marketWeight));
      if (c.marketAgeH != null) addM("newest quote", ageText(c.marketAgeH));
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
    if (c.raw.attack_xg != null) addKv("μ_O (attack lens)", fmt2(c.raw.attack_xg) + " goals");
    if (c.raw.defence_xg != null) addKv("λ_O (defence lens)", fmt2(c.raw.defence_xg) + " goals");
    if (c.blended != null) addKv("legacy difficulty", fmt2(c.blended));
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
    const raw = (r.ok && r.result && !r.result.empty) ? r.result : null;
    const D = flattenDetail(raw);

    marketAct(A.A2, raw);
    peopleAct(A.A3, D, raw);
    recordAct(A.A4, D, raw);

    /* provenance extras the detail carries */
    if (raw && raw.market && raw.market.casing_workaround)
      A.A5.appendChild(kvNote("Casing workaround, live in this payload.",
        String(raw.market.casing_workaround)));
    if (raw && Array.isArray(raw.inputs) && raw.inputs.length) {
      A.A5.appendChild(el("h2", null, "Inputs"));
      const kv = el("div", "fx-kv");
      for (const i of raw.inputs) {
        kv.appendChild(el("span", "k", String(i.name || "—")));
        const h = num(i.age_hours) ?? ageHours(i.as_of);
        kv.appendChild(el("span", "v",
          [h == null ? "age unknown" : ageText(h),
           i.detail ? String(i.detail) : null].filter(Boolean).join(" · ")));
      }
      A.A5.appendChild(kv);
    }
    crossLinks(A.A5, t, c);

    if (!r.ok) {
      drawer.appendChild(el("p", "sub",
        r.missing
          ? "fixture_detail is not registered, so every section above is a named "
            + "gap rather than a fetch failure."
          : `fixture_detail failed: ${String(r.error.message || r.error)}`));
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
        num(mk.age_hours) != null ? ageText(num(mk.age_hours)) : null,
        mk.devig_method ? `devig ${mk.devig_method}` : null,
        num(mk.overround_h2h) != null ? `overround ${fmt2(mk.overround_h2h)}` : null,
      ].filter(Boolean).join(" · ");
      if (meta) host.appendChild(el("p", "fx-mktmeta", meta));
    }

    const dis = raw && Array.isArray(raw.disagreement) && raw.disagreement.length
      ? raw.disagreement : null;
    if (!dis) {
      host.appendChild(namedGap("No model/market comparison in this payload.",
        gapText(
          codeSpan("fixture_detail"), "'s ", codeSpan("disagreement"),
          " rows carry both estimators for the same quantities. None arrived, "
          + "so there is no gap to draw — the market-state note below still "
          + "says what the board knew.")));
      return;
    }

    const box = el("div", "fx-gaplines");
    for (const d of dis) {
      const m = num(d.model), k = num(d.market);
      if (m == null || k == null) continue;
      const row = el("div", "gapline" + (d.flagged ? " flagged" : ""));
      row.appendChild(el("span", "k", String(d.metric || "—")));
      const db = el("span", "db");
      db.style.setProperty("--m", String(m * 100));
      db.style.setProperty("--k", String(k * 100));
      const dm = el("i", "model");
      dm.title = `model ${(m * 100).toFixed(1)}%`;
      const dk = el("i", "market");
      dk.title = `market ${(k * 100).toFixed(1)}%`
        + (num(d.market_age_hours) != null
            ? ` · ${ageText(num(d.market_age_hours))}` : "");
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
                 " market — never averaged; the gap is the finding"));
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

    /* whole sections with nothing keep their named gaps, full width */
    if (!news) host.appendChild(namedGap("No team news in this payload.", gapText(
      "Availability lives in ", codeSpan("fact_player_state"), " and ",
      codeSpan("intel_item"), " (kind ", codeSpan("availability"),
      "). Nothing in this payload carries it, so nothing is shown — a blank "
      + "here means “not fetched”, never “nobody is injured”.")));
    if (!xi) host.appendChild(namedGap(
      raw && raw.predicted_lineups && raw.predicted_lineups.unavailable
        ? "No predicted XI yet."
        : "No predicted XI in this payload.",
      raw && raw.predicted_lineups && raw.predicted_lineups.unavailable
        ? String(raw.predicted_lineups.unavailable)
        : gapText(
            codeSpan("fact_predicted_lineup"), " holds predictions for gameweeks the "
            + "provider has published. The panel does not return them, so none are "
            + "drawn. Providers usually publish around T−48h, so an early "
            + "gameweek in the horizon legitimately has none.")));
    if (!sp) host.appendChild(namedGap(
      "Set-piece duty is in the warehouse and not in this payload.", gapText(
      "Set-piece duty is the highest-value team-level intel in the warehouse (",
      codeSpan("set_piece_duty"), ", ", codeSpan("set_piece_change"),
      ") and nothing in the UI renders it yet. It belongs here as DUTY — who "
      + "takes them — and not as a team trait: set-piece goals-over-expected "
      + "barely persists season to season, while who takes the corner does.")));
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
        row.appendChild(el("b", null, n.player || "—"));
        if (n.chance != null) row.appendChild(el("span", "chance", `${n.chance}%`));
        const meta = [n.status_text || null,
          n.as_of ? ageText(ageHours(n.as_of)) : null].filter(Boolean).join(" · ");
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
        box.appendChild(el("span", null, d.player || "—"));
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
        "What this warehouse can honestly say about style is team xG for and "
        + "against, goals versus xG, and clean-sheet rate, split home and away. "
        + "What it cannot say is PPDA, field tilt, sequence types or line height "
        + "— that event data is not here, and inventing it would be the worst "
        + "thing this page could do. Style explains a fixture; it is never "
        + "allowed into the colour."));
    } else {
      host.appendChild(namedGap("No style summary in this payload.",
        "What this warehouse can honestly say about style is team xG for and "
        + "against, goals versus xG, and clean-sheet rate, split home and away. "
        + "What it cannot say is PPDA, field tilt, sequence types or line height "
        + "— that event data is not here, and inventing it would be the worst "
        + "thing this page could do. Style explains a fixture; it is never "
        + "allowed into the colour."));
    }

    host.appendChild(el("h2", null, "Previous meetings"));
    if (D && D.previous_meetings) {
      const box = el("div", "fx-kv");
      for (const m of D.previous_meetings.slice(0, 8)) {
        box.appendChild(el("span", "k", m.season || m.date || "—"));
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
      host.appendChild(namedGap("No previous meetings in this payload.", gapText(
        "Completed meetings would come from ", codeSpan("fact_fixture"),
        " in both orientations. The panel returns none. If these two clubs have "
        + "never met in the Premier League there is nothing to show and nothing "
        + "to infer — but this page cannot currently tell you which of those two "
        + "it is, and it will not guess.")));
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
                      q.published_at ? ageText(ageHours(q.published_at)) : null]
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
      host.appendChild(namedGap("No creator has said anything about either club.",
        gapText(
          codeSpan("content_insight"), " carries team-level observations, and none "
          + "of them is about either of these clubs at this instant. Insights are "
          + "written by both analysis paths now; ",
          codeSpan("fpl-content backfill-insights"),
          " recovers them from analyses already stored, without a model call.")));
    }

    host.appendChild(el("h2", null, "Press & scout links"));
    if (D && D.press_conference && D.press_conference.length) {
      const box = el("div");
      for (const q of D.press_conference.slice(0, 6)) {
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
      host.appendChild(box);
      host.appendChild(el("p", "sub",
        "These are FPL's own scout links, dated to the first poll that carried "
        + "them because FPL publishes no timestamp for the field. Treat the age "
        + "as an upper bound on freshness, not a publication time."));
    } else {
      host.appendChild(namedGap("No press or scout links for this fixture.", gapText(
        "Press-conference and scout links would come from ", codeSpan("intel_item"),
        ". None reached this fixture.")));
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
      "an easy run everyone can see is priced into the field's transfers — the "
      + "same run on a 2%-owned club is an edge, on a 60%-owned club it is "
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
    drawer.classList.add("open");
    drawer.scrollTop = 0;
    drawer.appendChild(masthead(
      crest(t.code, t.short, "s34"),
      t.name || t.short,
      `GW${M.gws[0]}–GW${M.gws[M.gws.length - 1]} · ${t.nFixtures} fixture`
      + `${t.nFixtures === 1 ? "" : "s"}`
      + (t.nBlanks ? ` · ${t.nBlanks} blank` : "")
      + (t.nDoubles ? ` · ${t.nDoubles} double` : ""),
      null));

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

    /* the League Shape card's surviving payload: team quality, printed as a
       line so nobody mistakes this tab for a power ranking */
    if (t.rating && (num(t.rating.attack_rank) != null || num(t.rating.defence_rank) != null)) {
      drawer.appendChild(el("p", "fx-fitted",
        `fitted: #${t.rating.attack_rank ?? "–"} attack · `
        + `#${t.rating.defence_rank ?? "–"} defence of ${M.teams.length} — team `
        + "quality, which is what the ticker deliberately holds constant"));
    }

    drawer.appendChild(el("h2", null, "Form, as a residual"));
    if (t.form && num(t.form.window_matches) != null) {
      const chip = formChipEl(t.form);
      if (chip) {
        const line = el("div", "fx-formrec");
        const row = el("div", "row");
        row.appendChild(chip);
        line.appendChild(row);
        drawer.appendChild(line);
      }
      const k = el("div", "fx-kv");
      const add = (a, b) => { k.appendChild(el("span", "k", a)); k.appendChild(el("span", "v", b)); };
      add("matches in window", String(t.form.window_matches));
      if (num(t.form.xg_for_pg) != null) add("xG for, per game", fmt2(t.form.xg_for_pg));
      if (num(t.form.xg_against_pg) != null) add("xG against, per game", fmt2(t.form.xg_against_pg));
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
      drawer.appendChild(namedGap("No form residual in this payload.",
        "Team xG for and against over the last few matches, minus what the "
        + "fitted rating expected, is a diagnostic that the colour might be "
        + "wrong. It is deliberately not a third input to the colour, and it "
        + "is not carried here."));
    }
  }

  await load();
}
