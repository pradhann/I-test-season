/* Creators. The panel's takes for this deadline, held against your squad.
 *
 * Three payloads, nothing else: `creator_board` (what was said, by whom, for
 * which gameweek), `creator_report_card` (how each creator's calls actually
 * scored, in three channels that never merge) and GET /api/content/sources
 * (which feeds are fetched, quiet or empty; the two states that are not feeds,
 * blocked and legacy, stay in the API and never reach the page). Every number
 * on the page is read from one of them; nothing is modelled here.
 *
 * Reading order is the owner's: freshness and control first (the source
 * strip), then the takes that matter against the squad, then the board, the
 * armband, the said-vs-owned matrix, the report cards. Prose is rationed:
 * one line per card, everything else behind a details whose summary states
 * a finding. The report card travels with every claim as a chip.
 *
 * COLOUR. Two direction hues from the app's series ramp: --s1 = the panel is
 * IN, --s2 = the panel is OUT. Evidence strength is a shape (hollow = keyword
 * window), ownership is an outline, captaincy is a star. A record's verdict
 * against a coin flip uses the app's status tokens (good / bad / muted), which
 * are a different axis from direction and never share a mark with it.
 */

import { runPanel, getJSON, postJSON, el, emptyBox, errBox, provenance,
         faceImg, fmtPrice, fmt1 } from "/js/app.js";

/* ------------------------------------------------------------------ utils */
const NS = "http://www.w3.org/2000/svg";
function sv(tag, attrs, text) {
  const n = document.createElementNS(NS, tag);
  for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
  if (text != null) n.textContent = text;
  return n;
}

const parseTs = iso => iso ? new Date(String(iso).replace(" ", "T")) : null;

function relAge(iso) {
  const d = parseTs(iso);
  if (!d || isNaN(d)) return { text: "date unknown", cls: "bad" };
  const h = (Date.now() - d) / 3.6e6;
  const text = h < 1 ? "just now"
    : h < 24 ? `${Math.round(h)}h ago`
    : h < 48 ? "yesterday"
    : h < 720 ? `${Math.round(h / 24)}d ago`
    : `${Math.round(h / 730)}mo ago`;
  return { text, cls: h < 72 ? "good" : h < 336 ? "warn" : "bad" };
}

function clock(s) {
  if (s == null || !isFinite(s)) return null;
  const t = Math.max(0, Math.round(s));
  const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), sec = t % 60;
  const pad = n => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;

/* 353 of 594 stored items have url_basis "enclosure" — the item URL IS the
   .mp3, so "open episode" is a lie for more than half the corpus. Prefer the
   panel's own basis; fall back to reading the extension off the URL, and SAY
   which of the two we did. */
function linkKind(url, basis) {
  const u = String(url || "");
  if (basis === "enclosure")
    return { label: "play audio", why: "the panel reports this URL as the audio enclosure" };
  if (basis === "link")
    return { label: "open episode", why: "the panel reports this URL as an episode page" };
  if (/\.(mp3|m4a|aac|ogg|oga|wav)(\?|#|$)/i.test(u))
    return { label: "play audio",
             why: "the panel did not report url_basis; the stored URL ends in an audio file extension" };
  return { label: "open episode", why: null };
}

/* Every extractor the warehouse can stamp on a claim, and how strong it is. */
function tier(extractor) {
  const e = String(extractor || "");
  if (e.startsWith("llm:"))
    return { key: "llm", label: "considered take", model: e.slice(4),
             note: "a language model read the passage and returned a verbatim quote" };
  if (e === "cue")
    return { key: "cue", label: "keyword window", model: null,
             note: "a keyword landed near this player's name — not an opinion, a search hit" };
  return { key: "unknown",
           label: "method not recorded",
           model: null,
           note: "how this claim was extracted is not recorded in the payload"
                 + (extractor ? ` (extractor: ${e})` : "") };
}

const LANES = [
  { key: "captain", label: "You captain", agreeSide: +1 },
  { key: "start",   label: "You start",   agreeSide: +1 },
  { key: "bench",   label: "You bench",   agreeSide: 0 },
  { key: "none",    label: "Not owned",   agreeSide: -1 },
];
const laneLabel = k => (LANES.find(l => l.key === k) || {}).label || k;

/* Canonical key for "is this the same video". Two URL forms of one YouTube
   video are the duplicate case the warehouse already contains twice; the
   paste bar catches it locally before it can happen a third time. */
function canonicalKey(raw) {
  let u;
  try { u = new URL(raw); } catch { return String(raw || "").trim().toLowerCase(); }
  const host = u.hostname.replace(/^www\./, "").toLowerCase();
  if (/(^|\.)youtube\.com$/.test(host)) {
    const v = u.searchParams.get("v");
    if (v) return `yt:${v}`;
    const m = u.pathname.match(/\/(shorts|embed|live)\/([\w-]+)/);
    if (m) return `yt:${m[2]}`;
  }
  if (host === "youtu.be") return `yt:${u.pathname.slice(1)}`;
  return `${host}${u.pathname}`.replace(/\/+$/, "").toLowerCase();
}

/* ==================================================================== view */

const pct = x => x == null ? "–" : `${Math.round(100 * x)}%`;
const signed = (x, d = 1) => x == null ? "–" : `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(d)}`;
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* The report card's verdict vocabulary, and how it is drawn. `vs_coin_flip`
   is the payload's own word; the class picks a status token, never a hue. */
const COIN = {
  below: { cls: "below", word: "below coin flip", short: "below chance" },
  above: { cls: "above", word: "above coin flip", short: "above chance" },
  indistinguishable: { cls: "flip", word: "coin flip", short: "coin flip" },
  unmeasured: { cls: "none", word: "unmeasured", short: "unmeasured" },
};
const coin = v => COIN[v] || COIN.unmeasured;

/* The one-word label every chip carries. `too few` wins under the floor: a
   record with fewer scored claims than `min_scored_claims` is drawn but is
   never read as a rank. Colour still follows the payload's `vs_coin_flip`,
   which is the interval test itself and not a count. */
function verdict(cl, floor) {
  if (!cl || !cl.measured) return { cls: "none", word: "unmeasured", few: false };
  const few = floor != null && cl.n_scored < floor;
  const c = coin(cl.vs_coin_flip);
  return { cls: c.cls + (few ? " few" : ""), word: few ? "too few" : c.short, few };
}

/* Source states in the order the strip draws them: the ones that produce
   items first, the ones that cannot be fetched last. Two states never reach
   the page: `blocked` (a policy refusal, not a feed) and `legacy` (links
   pasted by hand, not a feed). They stay in the API; the strip, the table and
   the segment counts all drop them. */
const STATE_ORDER = ["live", "quiet", "stale", "empty", "failing", "unprobed", "disabled"];
const HIDDEN_STATES = new Set(["blocked", "legacy"]);

/* ==================================================================== view */

export default async function creators(host) {
  /* ---- shell ---------------------------------------------------------- */
  const topCard  = el("section", "card cx");     // record + the source strip
  const mainCard = el("section", "card cx");     // the takes, the board, the cards
  const linkCard = el("section", "card cx");     // paste a link
  host.append(topCard, mainCard, linkCard);

  const factRow = el("div", "toolbar cx-factrow");
  const gwRow   = el("div", "toolbar");
  const gwNote  = el("div", "cx-gwnote");
  const body    = el("div", "cx-body");
  const foot    = el("div");
  mainCard.append(factRow, gwRow, gwNote, body, foot);

  /* One drawer per visit: a re-entered view must not stack a second one on
     the body, and the previous visit's key handler must stop listening. */
  document.querySelectorAll("aside.cx-drawer").forEach(n => n.remove());
  const drawer = el("aside", "drawer cx-drawer");
  document.body.appendChild(drawer);
  let chatterHandle = null;
  const closeDrawer = () => {
    drawer.classList.remove("open");
    try { chatterHandle?.cancel(); } catch { /* component may be mid-build */ }
    chatterHandle = null;
  };
  const onKey = e => {
    if (!drawer.isConnected) { removeEventListener("keydown", onKey); return; }
    if (e.key === "Escape") closeDrawer();
  };
  addEventListener("keydown", onKey);

  /* ---- state ---------------------------------------------------------- */
  let res = null, prov = null, squad = null, squadErr = null;
  let consideredOnly = true;          // default: considered takes; keyword mentions are a toggle
  let last48Only = null;              // null = auto: the 48h window when it has content, else the full window
  let showAgreed = false;
  let gridAll = false;
  let gridEmptyShown = false;         // matrix rows with no cell and no ring
  let gridOpen = false;               // the matrix details, remembered across redraws
  const detailCache = new Map();      // creator -> Promise<creator_detail>
  const jobs = [];                    // paste-a-link jobs, newest first
  const pasted = new Map();           // canonical key -> url, this session only
  let linkBody = null;

  /* the report card: one payload, keyed by creator, carried by every chip */
  let rc = null, rcErr = null;
  const rcByCreator = new Map();
  const LEGACY = new Set(["user-shared"]);   // a pseudo-source, not a creator

  /* the source strip */
  let src = null, srcErr = null, srcOpen = false, srcFilter = null;
  const fetching = new Map();         // source key -> fetch_state payload, or a refusal
  let seq = null;                     // the "fetch the main shows" run
  let analyse = null;                 // the content_analyse trigger

  /* One name per player: the payload's `disambiguator` ("C. Palmer (CHE)")
     wins wherever a bare name renders. */
  const dn = r => (r && r.disambiguator) || (r && r.name) || "";

  /* `person` is the curated display name and is the namespace panel_owned
     uses; `name` is the FPL account name. Joining on the wrong one matched 3
     of 7 people once already, so both readers go through here. Declared as
     functions so the first render, which runs before the tail of this body,
     can reach them. */
  function personName(p) {
    return !p ? null : (typeof p === "string" ? p : (p.person || p.display_name || p.name || null));
  }
  function personsOf(c) { return (c && ((c.entry && c.entry.people) || c.people)) || []; }

  /* ---- claim recency, at row level ------------------------------------
     The board's consensus carries counts, not timestamps; the per-creator
     records carry every claim WITH its published_at. Read once, indexed by
     player code, so the deadline window can be measured. */
  const claimsIdx = new Map();        // code -> [{t: ms, gw}]
  let freshState = "idle";            // idle | loading | done | failed
  function loadRecency() {
    if (freshState !== "idle") return;
    freshState = "loading";
    const names = (res.creators || []).map(c => c.creator);
    Promise.all(names.map(n => detailFor(n))).then(ds => {
      for (const d of ds) {
        if (!d || d.__error) continue;
        for (const item of d.items || [])
          for (const c of item.claims || []) {
            if (c.code == null) continue;
            const t = parseTs(c.published_at || item.published_at);
            if (!t || isNaN(t)) continue;
            if (!claimsIdx.has(c.code)) claimsIdx.set(c.code, []);
            claimsIdx.get(c.code).push({ t: +t, gw: c.gameweek ?? null });
          }
      }
      freshState = "done";
      if (mainCard.isConnected) render();
    }).catch(() => { freshState = "failed"; if (mainCard.isConnected) render(); });
  }
  const freshFor = code => {
    let best = null;
    for (const c of claimsIdx.get(code) || [])
      if (c.gw == null || c.gw === res.gw) if (best == null || c.t > best) best = c.t;
    return best;
  };
  const agoText = t => t == null ? null : relAge(new Date(t).toISOString()).text;
  const H48 = 48 * 3.6e6;

  /* Skeleton shells for the 3-4s panel build. */
  function skeleton(cards = 3) {
    const sk = el("div", "cx-skel");
    sk.setAttribute("role", "status");
    sk.setAttribute("aria-label", "loading");
    sk.appendChild(el("div", "cx-skelrow"));
    sk.appendChild(el("div", "cx-skelblock"));
    for (let i = 0; i < cards; i++) sk.appendChild(el("div", "cx-skelcard"));
    return sk;
  }

  /* the OWN channel: creator_detail.squad, read once the matrix is opened */
  const squadByCreator = new Map();   // creator -> {squad, reason, entry}
  let squadsAsked = false, squadsPending = 0;

  let defaultGw = null;               // the panel's own next-gameweek default
  let gwSel = null;
  const boardCache = new Map();       // gw -> {result, provenance}
  const gwCount = new Map();          // gw -> integer | null (measured empty)
  const gwProbing = new Set();
  const BACK = 4, FWD = 2;

  /* ---- load ----------------------------------------------------------- */
  body.appendChild(skeleton());
  renderTop();
  renderLinkBar();
  loadSources();
  loadReportCard();
  const boardP = runPanel("creator_board", {});
  const squadP = runPanel("squad_overview", {}).catch(e => ({ error: e }));

  try {
    const r = await boardP;
    res = r.result; prov = r.provenance;
  } catch (e) {
    body.textContent = "";
    body.appendChild(errBox(e));
    return;
  }
  defaultGw = res.gw ?? null;
  gwSel = defaultGw;
  if (defaultGw != null) {
    boardCache.set(defaultGw, { result: res, provenance: prov });
    gwCount.set(defaultGw, (res.consensus || []).length);
  }
  const sq = await squadP;
  if (sq && sq.error) squadErr = String(sq.error.message || sq.error);
  else if (sq && sq.result && !sq.result.empty) squad = sq.result;
  else if (sq && sq.result) squadErr = sq.result.reason || "your squad is empty";

  /* ---- derived: your lanes -------------------------------------------- */
  const laneOf = new Map();           // code -> lane key
  const mine = new Map();             // code -> squad row
  if (squad) {
    for (const p of squad.starters || []) {
      laneOf.set(p.code, p.is_captain ? "captain" : "start"); mine.set(p.code, p);
    }
    for (const p of squad.bench || []) { laneOf.set(p.code, "bench"); mine.set(p.code, p); }
  }
  const squadReady = laneOf.size > 0;

  /* ---- derived: rows --------------------------------------------------- */
  function grp(g) {
    g = g || {};
    return { n: g.n || 0, cue: g.n_cue || 0, llm: g.n_llm || 0,
             people: (g.creators || g.people || []).slice() };
  }

  function buildRows(considered = consideredOnly) {
    return (res.consensus || []).map(c => {
      const buy = grp(c.buy), sell = grp(c.sell), cap = grp(c.captain);
      const use = k => considered ? k.llm : k.n;
      const nBuy = use(buy), nSell = use(sell), nCap = use(cap);
      const net = nBuy - nSell;
      const lane = laneOf.get(c.code) || "none";
      const split = nBuy > 0 && nSell > 0;
      const capElsewhere = nCap > 0 && lane !== "captain";
      /* THE AGREEMENT PREDICATE: shading follows this rule and nothing else. */
      const agreed = !split && !capElsewhere && (
        lane === "captain" || lane === "start" ? net >= 0
        : lane === "none" ? net <= 0
        : false);                       // bench is unresolved in both directions
      const voices = new Set([...buy.people, ...sell.people, ...cap.people]);
      const anyCue = buy.cue + sell.cue + cap.cue;
      const anyLlm = buy.llm + sell.llm + cap.llm;
      let reason;
      if (split) reason = `split: ${nBuy} in, ${nSell} out`;
      else if (capElsewhere) reason = `${plural(nCap, "captain call")}; not your captain`;
      else if (lane === "bench") reason = "on your bench";
      else if (agreed && lane === "none") reason = "not owned, nobody buying";
      else if (agreed) reason = "you own him, nobody selling";
      else if (lane === "none") reason = `${plural(net, "net buyer")}`;
      else reason = `${plural(-net, "net seller")}`;
      return {
        code: c.code, name: c.name, pos: c.pos, team: c.team,
        disambiguator: c.disambiguator || null,
        resolved: c.resolved !== false,
        price: c.price, own_pct: c.own_pct,
        mine: c.mine || null, panel_owned: c.panel_owned || null,
        buy, sell, cap, nBuy, nSell, nCap, net, lane, split, capElsewhere,
        agreed, reason, voices: voices.size, anyCue, anyLlm,
        cueOnly: anyLlm === 0 && anyCue > 0,
      };
    }).filter(r => r.nBuy || r.nSell || r.nCap);
  }

  /* Ordering: considered takes ahead of keyword windows, then how many
     people said it, then the size of the gap. Never by anyone's record. */
  const byWeightOfMouth = (a, b) =>
    (b.anyLlm - a.anyLlm) || (b.voices - a.voices) ||
    (Math.abs(b.net) - Math.abs(a.net)) || a.name.localeCompare(b.name);

  /* the deadline window: rows whose freshest claim for this GW is inside 48h */
  const inWindow = r => { const t = freshFor(r.code); return t != null && Date.now() - t < H48; };
  const wantH48 = () => last48Only === null
    ? (freshState === "done" && buildRows().some(inWindow))
    : last48Only;
  const windowed = (rows, h48 = wantH48()) =>
    (h48 && freshState === "done") ? rows.filter(inWindow) : rows;
  const currentRows = () => windowed(buildRows());

  /* ---- render --------------------------------------------------------- */
  render();
  probeGameweeks();
  loadRecency();

  function render() {
    renderGwRow();
    const rows = currentRows();
    renderFactRow(rows);
    body.textContent = "";
    renderBoard(rows);
    foot.textContent = "";
    foot.appendChild(provenance(prov));
  }

  /* ==================================================== the gameweek axis */

  function gwCandidates() {
    if (defaultGw == null) return [];
    const lo = Math.max(1, defaultGw - BACK), hi = Math.min(38, defaultGw + FWD);
    const out = [];
    for (let g = lo; g <= hi; g++) out.push(g);
    return out;
  }

  /* Measure the neighbours by asking for them, three at a time, after the
     first paint. A failed probe leaves the chip unmeasured, never zero. */
  function probeGameweeks() {
    const todo = gwCandidates().filter(g => !gwCount.has(g) && !gwProbing.has(g));
    let live = 0;
    const pump = () => {
      while (live < 3 && todo.length) {
        const g = todo.shift();
        gwProbing.add(g); live++;
        runPanel("creator_board", { gw: g })
          .then(r => {
            boardCache.set(g, { result: r.result, provenance: r.provenance });
            gwCount.set(g, ((r.result || {}).consensus || []).length);
          })
          .catch(() => { /* leave it unmeasured */ })
          .finally(() => {
            gwProbing.delete(g); live--;
            if (mainCard.isConnected) renderGwRow();
            pump();
          });
      }
    };
    pump();
  }

  async function selectGw(g) {
    if (g === gwSel) return;
    gwSel = g;
    const hit = boardCache.get(g);
    if (hit) { res = hit.result; prov = hit.provenance; render(); return; }
    renderGwRow();
    body.textContent = "";
    body.appendChild(skeleton(2));
    try {
      const r = await runPanel("creator_board", { gw: g });
      if (gwSel !== g) return;                 // a later click already won
      boardCache.set(g, { result: r.result, provenance: r.provenance });
      gwCount.set(g, ((r.result || {}).consensus || []).length);
      res = r.result; prov = r.provenance;
      render();
    } catch (e) {
      if (gwSel !== g) return;
      renderGwRow();
      body.textContent = "";
      body.appendChild(errBox(e));
    }
  }

  function renderGwRow() {
    gwRow.textContent = "";
    gwNote.textContent = "";
    const cands = gwCandidates();
    gwRow.appendChild(el("span", "tlabel", "Gameweek"));
    if (!cands.length) {
      const s = el("span", "cx-tiny", "the board returned no gameweek");
      s.title = res.gw_reason || "source: creator_board.gw";
      gwRow.appendChild(s);
      return;
    }
    for (const g of cands) {
      const n = gwCount.get(g);
      const measured = gwCount.has(g);
      const empty = measured && n === 0;
      const on = g === gwSel;
      const chip = el("button", "chip gw" +
        (on ? " on" : "") + (empty && !on ? " off" : ""));
      chip.appendChild(document.createTextNode(`GW${g}`));
      if (g === defaultGw) chip.appendChild(el("span", "cx-gwnext", "next"));
      const badge = el("span", "cx-gwn", measured ? (n === 0 ? "none" : String(n)) : "…");
      badge.setAttribute("aria-hidden", "true");
      chip.appendChild(badge);
      chip.setAttribute("aria-label", `GW${g}` +
        (measured ? (n === 0 ? ", nobody named" : `, ${plural(n, "player")} named`)
                  : ", still counting") +
        (g === defaultGw ? ", the next deadline" : ""));
      chip.title = measured
        ? (n === 0 ? "no claim names this gameweek" : `${plural(n, "player")} named`)
        : "counting";
      if (empty && !on) chip.disabled = true;
      else chip.onclick = () => selectGw(g);
      gwRow.appendChild(chip);
    }
    if (defaultGw != null && gwSel !== defaultGw) {
      const p = el("p", "cx-provline");
      p.append(`Reading GW${gwSel}; the next deadline is GW${defaultGw}. ` +
               "Your squad lanes are not re-wound.");
      gwNote.appendChild(p);
    }
  }

  /* ============================================ the window, stated as fact
   *
   * One object: considered takes, for the gameweek on screen, said in the
   * last 48 hours. Two toggles widen it, and each prints what it would add.
   * There is no "View" and no "Evidence": the board, the takes and the
   * matrix are all drawn from the same rows.
   */

  function renderFactRow(rows) {
    factRow.textContent = "";
    const takes = rows.reduce((a, r) => a + r.nBuy + r.nSell + r.nCap, 0);
    const shows = new Set();
    for (const r of rows) for (const g of [r.buy, r.sell, r.cap]) for (const p of g.people) shows.add(p);

    const fact = el("span", "cx-fact");
    const gwb = el("b", null, gwSel != null ? `GW${gwSel} deadline window` : "Deadline window");
    if (res.gw_reason) gwb.title = res.gw_reason;
    fact.appendChild(gwb);
    fact.append(` · ${plural(takes, "take")} on ${plural(rows.length, "player")} ` +
                `from ${plural(shows.size, "show")}`);
    const h48 = wantH48();
    const win = el("span", "cx-factwin");
    if (h48 && freshState === "done") win.append("said in the last 48h");
    else if (freshState === "loading") win.append("measuring recency…");
    else if (freshState === "done" && last48Only === null) {
      win.classList.add("warn");
      win.append(`nothing said in the last 48h; showing ${plural(res.window_days ?? 0, "day")}`);
    } else if (res.window_days != null) win.append(`last ${plural(res.window_days, "day")}`);
    fact.appendChild(win);
    const age = relAge(res.as_of);
    const asof = el("span", "cx-asof");
    asof.appendChild(el("span", "freshdot " + age.cls));
    asof.append(`read ${age.text}`);
    fact.appendChild(asof);
    factRow.appendChild(fact);

    const togs = el("span", "cx-togs");
    /* keyword mentions: the cue windows this window holds, not opinions */
    const cueTotal = windowed(buildRows(false)).reduce((a, r) => a + r.anyCue, 0);
    const b1 = toggle(!consideredOnly, "include keyword mentions",
      consideredOnly ? `+${cueTotal}` : String(cueTotal),
      () => { consideredOnly = !consideredOnly; render(); });
    b1.title = "a keyword landing near a player's name: a search hit, not an opinion; drawn hollow";
    if (consideredOnly && !cueTotal) b1.disabled = true;
    /* all history: everything in the panel's window for this gameweek */
    const older = windowed(buildRows(), false).length - windowed(buildRows(), true).length;
    const b2 = toggle(!h48, "all history",
      freshState !== "done" ? "…"
        : h48 ? `+${plural(older, "player")}`
        : `${plural(res.window_days ?? 0, "day")}`,
      () => { last48Only = !h48; render(); });
    if (freshState !== "done") {
      b2.disabled = true;
      b2.title = freshState === "failed" ? "claim timestamps could not be read"
                                          : "measuring claim recency";
    } else {
      b2.title = "drop the 48h cut and show every claim for this gameweek in the panel's window";
      if (h48 && !older) b2.disabled = true;
    }
    togs.append(b1, b2);
    factRow.appendChild(togs);
  }

  function toggle(on, label, badge, onclick) {
    const b = el("button", "chip gw cx-tog" + (on ? " on" : ""));
    b.setAttribute("aria-pressed", String(on));
    b.append(label);
    if (badge) b.appendChild(el("span", "cx-gwn", badge));
    b.onclick = onclick;
    return b;
  }

  /* ============================================= the record and the sources
   *
   * The top card: one line of measured truth about the panel's record, then
   * the source strip. The strip replaces the old "N ingested sources
   * excluded" list, which called working feeds excluded. Here every source
   * has exactly one measured state, and the two that cannot be fetched say
   * why in the API's own words.
   */

  async function loadSources() {
    try { src = await getJSON("/api/content/sources"); srcErr = null; }
    catch (e) { srcErr = String(e.message || e); }
    if (topCard.isConnected) renderTop();
  }
  /* the sources the page shows: everything the API lists except the two
     states that are not feeds at all */
  function visibleSources() {
    return (src ? src.sources : []).filter(s => !HIDDEN_STATES.has(s.state));
  }
  async function reloadSources() {
    try { src = await getJSON("/api/content/sources"); }
    catch { /* keep the last good list */ }
    if (topCard.isConnected) renderTop();
  }
  /* The report card is a registered script; it is called by route so the
     panel list (which does not declare it yet) is not the contract here. */
  async function loadReportCard() {
    try {
      const r = await runPanel("creator_report_card", {});
      rc = r.result; rcErr = null;
      rcByCreator.clear();
      for (const c of rc.cards || []) rcByCreator.set(c.creator, c);
    } catch (e) { rcErr = String(e.message || e); }
    if (topCard.isConnected) { renderTop(); if (res) render(); }
  }

  function renderTop() {
    topCard.textContent = "";
    topCard.appendChild(el("h2", null, "Creators"));
    topCard.appendChild(honestyLine());
    topCard.appendChild(sourceStrip());
  }

  /* THE HONESTY LINE, once. Computed from the report card, never typed. */
  function honestyLine() {
    const p = el("p", "cx-honest");
    if (rcErr) { p.classList.add("warn"); p.append(`The record could not be read: ${rcErr}`); return p; }
    if (!rc) { p.append("Measuring the record…"); return p; }
    const cards = (rc.cards || []).filter(c => !LEGACY.has(c.creator) && c.claims.measured);
    let hits = 0, scored = 0, below = 0, above = 0;
    for (const c of cards) {
      hits += c.claims.hits; scored += c.claims.n_scored;
      if (c.claims.vs_coin_flip === "below") below++;
      if (c.claims.vs_coin_flip === "above") above++;
    }
    const earned = (rc.cards || []).filter(c => c.claims.earned);
    if (earned.length) {
      p.appendChild(el("b", null,
        `${earned.map(c => c.creator).join(", ")} ${earned.length === 1 ? "has" : "have"} earned a weight`));
      p.append(` (${earned.map(c => c.claims.weight.toFixed(2)).join(", ")}); ` +
               `the other ${(rc.cards || []).filter(c => !LEGACY.has(c.creator)).length - earned.length} sit at 0.`);
    } else {
      p.appendChild(el("b", null, "Nobody has earned a weight."));
    }
    if (scored) p.append(` ${pct(hits / scored)} of ${scored} scored calls hit across ` +
      `${plural(cards.length, "creator")}: ${below} below a coin flip, ${above} above.`);
    p.title = rc.note || "";
    return p;
  }

  function mainShows() {
    return (src ? src.sources : [])
      .filter(s => s.can_fetch_now && (s.tier === "nightly" || s.state === "live"));
  }

  function sourceStrip() {
    const box = el("div", "cx-sources");
    const row = el("div", "cx-srcrow");
    box.appendChild(row);
    if (srcErr) {
      row.appendChild(el("span", "cx-provline warn", `sources: ${srcErr}`));
      return box;
    }
    if (!src) { row.appendChild(el("span", "cx-skelrow")); return box; }
    const labelOf = Object.fromEntries((src.states || []).map(s => [s.state, s.label]));

    const shown = visibleSources();
    const counts = {};
    for (const s of shown) counts[s.state] = (counts[s.state] || 0) + 1;
    const segs = el("div", "cx-segbar");
    segs.setAttribute("role", "group");
    segs.setAttribute("aria-label", "sources by state");
    for (const st of STATE_ORDER) {
      const n = counts[st] || 0;
      if (!n) continue;
      const b = el("button", `cx-segb ${st}` + (srcFilter === st ? " on" : ""));
      b.appendChild(el("b", null, String(n)));
      b.append(` ${st}`);
      b.title = labelOf[st] || st;
      b.setAttribute("aria-pressed", String(srcFilter === st));
      b.onclick = () => {
        if (srcFilter === st) { srcFilter = null; }
        else { srcFilter = st; srcOpen = true; }
        renderTop();
      };
      segs.appendChild(b);
    }
    row.appendChild(segs);

    const probes = shown.map(s => parseTs(s.last_probe_utc)).filter(d => d && !isNaN(d));
    const meta = el("span", "cx-srcmeta");
    if (probes.length) {
      const a = relAge(new Date(Math.max(...probes)).toISOString());
      meta.appendChild(el("span", "freshdot " + a.cls));
      meta.append(`last probe ${a.text}`);
    }
    row.appendChild(meta);

    const acts = el("span", "cx-srcacts");
    const list = el("button", "cx-srclist" + (srcOpen ? " on" : ""),
      srcOpen ? "hide the list" : `all ${shown.length} sources`);
    list.setAttribute("aria-expanded", String(srcOpen));
    list.onclick = () => { srcOpen = !srcOpen; if (!srcOpen) srcFilter = null; renderTop(); };
    acts.appendChild(list);
    const mains = mainShows();
    if (!seq || seq.done) {
      const go = el("button", "primary cx-fetchall", `fetch the main shows (${mains.length})`);
      go.title = "every source with tier nightly or state live, one after another";
      go.disabled = !mains.length;
      go.onclick = fetchMainShows;
      acts.appendChild(go);
    }
    row.appendChild(acts);

    if (seq) box.appendChild(seqLine());
    if (analyse) box.appendChild(analyseLine());
    if (srcOpen) box.appendChild(sourceTable(labelOf));
    return box;
  }

  function sourceTable(labelOf) {
    const wrap = el("div", "scroll-x cx-srcwrap");
    const t = el("table", "data cx-srctable");
    const thead = el("thead"); const hr = el("tr");
    for (const [l, cls] of [["creator"], ["kind"], ["state"], ["last probe"],
                            ["items", "num"], [""]])
      hr.appendChild(el("th", cls || "", l));
    thead.appendChild(hr); t.appendChild(thead);
    const tb = el("tbody");
    const rows = visibleSources()
      .filter(s => !srcFilter || s.state === srcFilter)
      .sort((a, b) => STATE_ORDER.indexOf(a.state) - STATE_ORDER.indexOf(b.state) ||
                      a.creator.localeCompare(b.creator) || a.kind.localeCompare(b.kind));
    for (const s of rows) {
      const tr = el("tr", "cx-srcline " + s.state);
      tr.title = s.reason || "";
      const nm = el("td");
      nm.appendChild(el("b", null, s.creator));
      if (s.tier) nm.appendChild(el("span", "cx-tier-tag", s.tier));
      tr.appendChild(nm);
      tr.appendChild(el("td", "cx-kind", s.kind));
      const st = el("td");
      const dot = el("span", "cx-state " + s.state);
      dot.appendChild(el("span", "cx-statedot"));
      dot.append(s.state);
      dot.title = labelOf[s.state] || s.state;
      st.appendChild(dot);
      tr.appendChild(st);
      const pr = el("td", "cx-probe");
      if (s.last_probe_utc) {
        pr.append(relAge(s.last_probe_utc).text);
        if (s.last_http_status != null) pr.appendChild(el("span", "cx-tiny", ` ${s.last_http_status}`));
      } else pr.append("never");
      if (s.last_error) { pr.appendChild(el("span", "cx-tiny cx-srcbad", " error")); pr.title = s.last_error; }
      tr.appendChild(pr);
      const it = el("td", "num");
      it.append(s.last_items == null ? "–" : String(s.last_items));
      it.title = `${s.last_items ?? "–"} on the last fetch · ${s.n_items_window ?? 0} in ${src.window_days}d · ${s.n_items ?? 0} stored`;
      if (s.n_items != null) it.appendChild(el("span", "cx-tiny", ` / ${s.n_items}`));
      tr.appendChild(it);
      tr.appendChild(actionCell(s, labelOf));
      tb.appendChild(tr);
    }
    t.appendChild(tb); wrap.appendChild(t);
    return wrap;
  }

  /* The per-source action, and what the last press got. */
  function actionCell(s, labelOf) {
    const td = el("td", "cx-srcact");
    const f = fetching.get(s.key);
    if (f && f.state === "running") {
      td.appendChild(el("span", "cx-job-state running", "fetching…"));
      return td;
    }
    if (f && f.state === "done") {
      const r = f.result || {};
      const ok = el("span", "cx-srcgot");
      ok.append(r.new_items ? `+${plural(r.new_items, "item")}` : "nothing new");
      if (r.seconds != null) ok.append(` · ${Math.round(r.seconds)}s`);
      ok.title = [r.newest_title ? `newest: ${r.newest_title}` : null,
                  r.newest_published ? `published ${relAge(r.newest_published).text}` : null,
                  r.http_status != null ? `http ${r.http_status}` : null]
        .filter(Boolean).join("\n");
      td.appendChild(ok);
    } else if (f && f.state === "error") {
      const bad = el("span", "cx-srcbad", "failed");
      bad.title = f.detail || (f.result && f.result.last_error) || "";
      td.appendChild(bad);
    } else if (f && f.state === "refused") {
      const bad = el("span", "cx-srcbad", "refused");
      bad.title = f.detail || "";
      td.appendChild(bad);
    }
    if (!s.can_fetch_now) {
      const sp = el("span", "cx-tiny", labelOf[s.state] || s.state);
      sp.title = s.reason || "";
      td.appendChild(sp);
      return td;
    }
    const b = el("button", "cx-fetch1", f ? "again" : "fetch latest");
    b.disabled = !!(seq && !seq.done);
    b.onclick = () => fetchSource(s.key);
    td.appendChild(b);
    return td;
  }

  /* POST the fetch, poll fetch_state until it leaves "running", keep the
     final payload for the row, then re-read the source list so the state
     column moves with it. Returns the final state, or null on refusal. */
  async function fetchSource(key) {
    fetching.set(key, { state: "running" });
    renderTop();
    try { await postJSON(`/api/content/sources/${encodeURIComponent(key)}/fetch`, {}); }
    catch (e) {
      fetching.set(key, { state: "refused", detail: refusal(e) });
      renderTop();
      return null;
    }
    let st = null;
    for (;;) {
      await sleep(2000);
      if (!topCard.isConnected) return null;
      try { st = await getJSON(`/api/content/sources/${encodeURIComponent(key)}/fetch_state`); }
      catch { continue; }
      if (st.state !== "running") break;
    }
    fetching.set(key, st);
    await reloadSources();
    return st;
  }
  function refusal(e) {
    const m = String(e.message || e);
    const i = m.indexOf("{");
    if (i >= 0) { try { return JSON.parse(m.slice(i)).detail || m; } catch { /* not json */ } }
    return m;
  }

  /* The main shows, in sequence, with a progress line and a stop. */
  async function fetchMainShows() {
    if (seq && !seq.done) return;
    const list = mainShows();
    seq = { i: 0, total: list.length, current: null, stop: false, done: false,
            newItems: 0, failed: [] };
    renderTop();
    for (const s of list) {
      if (seq.stop) break;
      seq.i++; seq.current = s.creator;
      renderTop();
      const st = await fetchSource(s.key);
      if (!topCard.isConnected) return;
      if (st && st.result) seq.newItems += st.result.new_items || 0;
      if (!st || st.state === "error") seq.failed.push(s.creator);
    }
    seq.done = true;
    renderTop();
  }

  function seqLine() {
    const p = el("div", "cx-seq");
    if (!seq.done) {
      const bar = el("span", "cx-seqbar");
      const fill = el("span", "cx-seqfill");
      fill.style.width = `${Math.round(100 * seq.i / Math.max(1, seq.total))}%`;
      bar.appendChild(fill);
      p.appendChild(bar);
      p.appendChild(el("span", "cx-seqtext", `${seq.i} of ${seq.total} · ${seq.current || ""}`));
      const stop = el("button", "cx-x", seq.stop ? "stopping…" : "stop");
      stop.disabled = seq.stop;
      stop.onclick = () => { seq.stop = true; renderTop(); };
      p.appendChild(stop);
      return p;
    }
    p.appendChild(el("span", "cx-seqtext",
      `${seq.stop ? "stopped after" : "fetched"} ${seq.i} of ${seq.total} · ` +
      `${plural(seq.newItems, "new item")}` +
      (seq.failed.length ? ` · failed: ${seq.failed.join(", ")}` : "")));
    if (seq.newItems && !analyse) {
      const b = el("button", null, "extract claims from the new items");
      b.title = "runs the content_analyse pipeline; claims appear on the board after it finishes";
      b.onclick = () => runAnalyse(false);
      p.appendChild(b);
    }
    const x = el("button", "cx-x", "✕");
    x.setAttribute("aria-label", "dismiss");
    x.onclick = () => { seq = null; renderTop(); };
    p.appendChild(x);
    return p;
  }

  /* Fetching stores items; claims come from content_analyse. Same route the
     Pipelines tab uses, same confirm gate, same poll. */
  async function runAnalyse(confirmed) {
    analyse = { state: "starting" };
    renderTop();
    let resp;
    try { resp = await postJSON("/api/pipelines/content_analyse/run", { confirm: !!confirmed }); }
    catch (e) { analyse = { state: "error", detail: refusal(e) }; renderTop(); return; }
    if (resp.needs_confirm) { analyse = { state: "confirm", resp }; renderTop(); return; }
    if (!(resp.started && resp.run_id)) {
      analyse = { state: "error", detail: JSON.stringify(resp).slice(0, 200) };
      renderTop(); return;
    }
    analyse = { state: "running", run_id: resp.run_id };
    renderTop();
    for (;;) {
      await sleep(3000);
      if (!topCard.isConnected) return;
      let st;
      try { st = await getJSON("/api/pipelines/content_analyse/run_state"); }
      catch { continue; }
      const terminal =
        (st.run_id === resp.run_id && (st.state === "done" || st.state === "error"))
        || (st.last_run && st.last_run.run_id === resp.run_id);
      if (!terminal) continue;
      const last = st.last_run || st;
      analyse = { state: last.state === "error" || last.status === "error" ? "error" : "done",
                  detail: last.detail || last.outcome || "" };
      renderTop();
      return;
    }
  }

  function analyseLine() {
    const p = el("div", "cx-seq cx-analyse");
    const a = analyse;
    if (a.state === "starting") p.appendChild(el("span", "cx-seqtext", "starting content_analyse…"));
    else if (a.state === "running") p.appendChild(el("span", "cx-seqtext", "content_analyse running…"));
    else if (a.state === "confirm") {
      const r = a.resp || {};
      p.appendChild(el("span", "cx-seqtext",
        `content_analyse is metered: ~${r.credits_estimate ?? "?"} credits` +
        (r.month_spend != null ? ` · ${r.month_spend}${r.month_cap ? `/${r.month_cap}` : ""} used this month` : "")));
      const go = el("button", "primary", "confirm");
      go.onclick = () => runAnalyse(true);
      const no = el("button", "cx-x", "cancel");
      no.onclick = () => { analyse = null; renderTop(); };
      p.append(go, no);
    } else if (a.state === "done") {
      p.appendChild(el("span", "cx-seqtext", `content_analyse finished${a.detail ? `: ${a.detail}` : ""}`));
      const re = el("button", null, "reload the board");
      re.onclick = () => location.reload();
      p.appendChild(re);
    } else {
      const bad = el("span", "cx-seqtext cx-srcbad", `content_analyse: ${a.detail || "failed"}`);
      p.appendChild(bad);
      const x = el("button", "cx-x", "✕");
      x.onclick = () => { analyse = null; renderTop(); };
      p.appendChild(x);
    }
    return p;
  }

  /* ======================================================== the link bar
   *
   * POST /api/ingest/link runs one page fetch and parks at a preview with
   * `awaiting_decision: true`. Accept is the only call that spends GPU
   * seconds; decline spends none. The job machinery below renders the
   * preview, the two actions, the expiry and the stage ladder.
   */

  const LINK_STAGES = ["fetch", "preview", "transcribe", "analyse", "attribute"];

  function renderLinkBar() {
    linkCard.textContent = "";
    linkCard.appendChild(el("h2", null, "Add a source"));

    const row = el("div", "toolbar");
    row.appendChild(el("span", "tlabel", "Paste a link"));
    const input = el("input", "cx-url");
    input.type = "url";
    input.placeholder = "a YouTube video or a podcast episode";
    input.setAttribute("aria-label", "URL of a YouTube video or podcast episode");
    const btn = el("button", "primary", "Add");
    row.append(input, btn);
    linkCard.appendChild(row);

    const gate = el("p", "cx-tiny cx-gate");
    gate.append("Preview first; nothing is transcribed until you accept.");
    gate.title = "captions run at about 286x realtime (a 20-minute video in about 4s); " +
      "local speech-to-text at about 11.5x (about 105s). Both measured from stored runs.";
    linkCard.appendChild(gate);

    const err = el("div", "cx-linkerr");
    linkCard.appendChild(err);
    linkBody = el("div", "cx-jobs");
    linkCard.appendChild(linkBody);

    const submit = () => {
      err.textContent = "";
      const raw = input.value.trim();
      if (!raw) return;
      if (!/^https?:\/\/\S+$/i.test(raw)) {
        err.appendChild(failLine("That is not a link.",
          "It needs to start with http:// or https://. Nothing was sent."));
        return;
      }
      const key = canonicalKey(raw);
      if (pasted.has(key)) {
        err.appendChild(failLine("Already pasted this session.",
          `Same video as ${pasted.get(key)}. Nothing was sent.`));
        return;
      }
      pasted.set(key, raw);
      input.value = "";
      startJob(raw, key);
    };
    btn.onclick = submit;
    input.onkeydown = e => { if (e.key === "Enter") submit(); };
    renderJobs();
  }

  function failLine(head, detail, kind) {
    const d = el("div", "cx-fail" + (kind ? " " + kind : ""));
    d.appendChild(el("b", null, head));
    d.appendChild(el("div", "sub", detail));
    return d;
  }

  /* ---------------------------------------------------------- job driving */

  function stopJob(job) {
    clearInterval(job.timer); clearTimeout(job.timer); job.timer = null;
    clearInterval(job.tick);  clearTimeout(job.tick);  job.tick = null;
  }

  /* A job that stored nothing may be pasted again — the session's duplicate
     guard exists to stop a second INGEST, and a declined, expired or
     cancelled job did not ingest anything. */
  const STORED_NOTHING = new Set(["passed", "expired", "cancelled", "failed",
                                  "down", "robots", "nomedia", "notepisode",
                                  "declined"]);

  async function startJob(url, key) {
    const job = { url, key, state: "posting", stages: null, stage: null,
                  pct: null, eta: null, error: null, item_id: null,
                  started: Date.now(), timer: null, tick: null, path: null,
                  path_reason: null, eta_basis: null, eta_reason: null,
                  preview: null, expires: null, accepted: false, aborting: false,
                  busy: null, conflict: null, actErr: null, descOpen: false,
                  dupes: [], note: null, p: null };
    jobs.unshift(job);
    renderJobs();
    /* Raw fetch, not postJSON: the STATUS CODE is the thing that tells a
       not-deployed endpoint apart from a rejected link, and postJSON throws
       it away into a message string. */
    let r;
    try {
      r = await fetch("/api/ingest/link", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
    } catch (e) {
      job.state = "down";
      job.error = `the request never reached the server (${String(e.message || e)})`;
      renderJobs(); return;
    }
    if (r.status === 404 || r.status === 405 || r.status === 501) {
      job.state = "down";
      job.error = `POST /api/ingest/link returned HTTP ${r.status}`;
      renderJobs(); return;
    }
    if (r.status === 403 || r.status === 429) {
      job.state = "declined";
      job.error = `the server answered HTTP ${r.status}`;
      renderJobs(); return;
    }
    let payload = null;
    try { payload = await r.json(); } catch { /* body may be empty */ }
    if (!r.ok) {
      job.state = "failed";
      job.error = (payload && (payload.detail || payload.error)) || `HTTP ${r.status}`;
      renderJobs(); return;
    }
    job.job_id = payload && payload.job_id;
    job.stages = (payload && payload.stages) || null;
    if (!job.job_id) {
      job.state = "failed";
      job.error = "the endpoint accepted the link but returned no job_id";
      renderJobs(); return;
    }
    job.state = "running";
    renderJobs();
    poll(job);
  }

  function poll(job) {
    stopJob(job);
    const once = async () => {
      if (!document.body.contains(linkCard)) { stopJob(job); return; }
      let r, p = null;
      try {
        r = await fetch(`/api/ingest/link/${encodeURIComponent(job.job_id)}`);
        p = await r.json();
      } catch (e) {
        stopJob(job);
        job.state = "down";
        job.error = `polling failed: ${String(e.message || e)}`;
        renderJobs(); return;
      }
      if (!r.ok) {
        stopJob(job);
        job.state = r.status === 404 ? "down" : "failed";
        job.error = (p && (p.detail || p.error)) || `HTTP ${r.status}`;
        renderJobs(); return;
      }
      applyPoll(job, p);
      renderJobs();
    };
    job.timer = setInterval(once, 900);
    once();
  }

  /* Every terminal answer the server can give, BY ITS OWN CODE. The regex
     classifier below stays as the fallback for a server that predates
     `error_code`, but a code is exact and a message is a guess about a
     message. */
  const ERR_STATE = {
    declined: "passed",                    // the OWNER said no, at the halt
    preview_expired: "expired",
    cancelled: "cancelled",
    not_an_episode: "notepisode",
    source_refused: "declined",            // the SOURCE said no (403 / 429)
    robots_disallow: "robots",
    no_transcript_source: "nomedia",
    no_asr_route_for_pasted_media: "nomedia",
  };

  function errState(code, msg) {
    if (code && ERR_STATE[code]) return ERR_STATE[code];
    if (code) return "failed";
    return classifyError(msg);
  }

  function applyPoll(job, p) {
    if (!p || typeof p !== "object") return;
    job.p = p;
    if (Array.isArray(p.stages) && p.stages.length) job.stages = p.stages;
    job.stage = p.stage ?? job.stage;
    job.pct = p.pct ?? job.pct;
    job.item_id = p.item_id ?? job.item_id;
    /* NOT sticky. The server nulls the ETA when the job ends and says why in
       `eta_reason`; carrying the old number forward would show a countdown
       for work that is not going to happen. */
    job.eta = p.eta_s ?? null;
    job.eta_basis = p.eta_basis ?? null;
    job.eta_reason = p.eta_reason ?? null;
    if (p.transcript_path) job.path = p.transcript_path;
    if (p.path_reason) job.path_reason = p.path_reason;
    if (p.preview) job.preview = p.preview;
    if (p.preview_expires_utc) job.expires = p.preview_expires_utc;
    job.note = p.note ?? job.note;

    if (p.awaiting_decision) {
      /* PARKED IS NOT RUNNING. Nothing advances until a button is pressed, so
         the poll stops here — what changes from now on is the clock, and the
         clock is local. */
      stopJob(job);
      job.state = "preview";
      startExpiryTicker(job);
      return;
    }
    if (p.error) {
      stopJob(job);
      job.error = String(p.error);
      job.state = errState(p.error_code, job.error);
      if (STORED_NOTHING.has(job.state)) pasted.delete(job.key);
      return;
    }
    if (p.done) {
      stopJob(job);
      job.dupes = p.duplicate_of || [];
      /* Finished without an accept and without an error means the server
         SKIPPED the halt, and only one thing does that on a clean run: the
         video is already in the warehouse. No decision was owed because
         nothing would have been transcribed either way. */
      job.state = (!job.accepted && (job.dupes.length || p.note))
        ? "duplicate" : "done";
      return;
    }
    job.state = job.aborting ? "stopping" : "running";
  }

  /* The four failures the warehouse already contains, handled BY NAME. */
  function classifyError(msg) {
    const m = String(msg).toLowerCase();
    if (/\b(403|429)\b|forbidden|rate.?limit|too many requests/.test(m)) return "declined";
    // `exists` alone matched the not-an-episode refusal, whose own sentence
    // ends "that is what this refusal exists to prevent" -- so pasting an FPL
    // league invite reported "Already held. Its take is on the board already",
    // asserting a stored item that does not exist and swallowing the real
    // reason. Order matters too: the specific refusal is tested first.
    if (/too.?thin|not an? (episode|article|video)|no text|3 characters|league/
        .test(m)) return "notepisode";
    if (/duplicate|already (held|ingested|have)|already exists/.test(m)) {
      return "duplicate";
    }
    if (/no captions|no audio|no_media|no enclosure|nothing to transcribe/.test(m)) return "nomedia";
    return "failed";
  }

  /* ------------------------------------------------------------- the gate */

  function expiryLeft(job) {
    if (!job.expires) return null;
    const t = Date.parse(String(job.expires).replace(" ", "T"));
    return isFinite(t) ? (t - Date.now()) / 1000 : null;
  }

  function expiryText(job) {
    const left = expiryLeft(job);
    if (left == null) return "the payload carried no preview_expires_utc";
    if (left <= 0) return "the 30 minutes are up — asking the server";
    const m = Math.floor(left / 60), s = Math.floor(left % 60);
    return `${m}m ${String(s).padStart(2, "0")}s left`;
  }

  function startExpiryTicker(job) {
    stopJob(job);
    if (!job.expires) return;
    job.tick = setInterval(() => {
      if (!document.body.contains(linkCard)) { stopJob(job); return; }
      if (job.state !== "preview") { stopJob(job); return; }
      const left = expiryLeft(job);
      if (left != null && left <= 0) { stopJob(job); confirmExpiry(job); return; }
      if (job.expEl && job.expEl.isConnected) job.expEl.textContent = expiryText(job);
    }, 1000);
  }

  /* The server expires a preview LAZILY, on the next poll. So a countdown
     reaching zero is not the answer — it is the reason to go and ask for one.
     Until the server agrees, this keeps saying "parked", because inventing an
     expiry the server has not applied is inventing state. */
  async function confirmExpiry(job) {
    let r, p = null;
    try {
      r = await fetch(`/api/ingest/link/${encodeURIComponent(job.job_id)}`);
      p = await r.json();
    } catch { /* the retry below is the handler */ }
    if (r && r.ok && p) { applyPoll(job, p); renderJobs(); }
    if (job.state === "preview") job.tick = setTimeout(() => confirmExpiry(job), 5000);
  }

  async function decide(job, verb) {
    if (job.busy) return;
    job.busy = verb; job.conflict = null; job.actErr = null;
    renderJobs();
    let r, p = null;
    try {
      r = await fetch(
        `/api/ingest/link/${encodeURIComponent(job.job_id)}/${verb}`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "" }) });
      p = await r.json();
    } catch (e) {
      job.busy = null;
      job.actErr = `the ${verb} never reached the server (${String(e.message || e)}). ` +
        "Nothing has changed on the server either way.";
      renderJobs(); return;
    }
    job.busy = null;
    if (r.status === 409) {
      job.conflict = (p && p.detail) ||
        "the server says this job is no longer waiting for a decision";
      if (p && p.job) applyPoll(job, p.job);
      renderJobs(); return;
    }
    if (!r.ok) {
      job.actErr = (p && (p.detail || p.error)) || `HTTP ${r.status}`;
      renderJobs(); return;
    }
    if (verb === "accept") job.accepted = true;
    applyPoll(job, p);
    renderJobs();
    if (verb === "accept") poll(job);
  }

  async function abortJob(job) {
    if (job.busy) return;
    job.busy = "abort"; job.aborting = true; job.conflict = null; job.actErr = null;
    renderJobs();
    let r, p = null;
    try {
      r = await fetch(`/api/ingest/link/${encodeURIComponent(job.job_id)}`,
                      { method: "DELETE" });
      p = await r.json();
    } catch (e) {
      job.busy = null; job.aborting = false;
      job.actErr = `the abort never reached the server (${String(e.message || e)}). ` +
        "The job is still running there.";
      renderJobs(); return;
    }
    job.busy = null;
    if (r.status === 409) {
      job.aborting = false;
      job.conflict = (p && p.detail) || `the server answered HTTP ${r.status}`;
      if (p && p.job) applyPoll(job, p.job);
      renderJobs(); return;
    }
    if (!r.ok) {
      job.aborting = false;
      job.actErr = (p && (p.detail || p.error)) || `HTTP ${r.status}`;
      renderJobs(); return;
    }
    applyPoll(job, p);
    renderJobs();
    /* A cancel is a REQUEST. The worker applies it at its next checkpoint, and
       the one write has no checkpoint inside it, so keep polling until the
       server says which of the two happened. */
    if (!(job.p && job.p.done)) poll(job);
  }

  function repaste(job) {
    pasted.set(job.key, job.url);
    startJob(job.url, job.key);
  }

  /* --------------------------------------------------------- job renderer */

  function renderJobs() {
    if (!linkBody) return;
    linkBody.textContent = "";
    for (const job of jobs) linkBody.appendChild(renderJob(job));
  }

  function renderJob(job) {
    const d = el("div", "cx-job" + (job.state === "preview" ? " parked" : ""));
    const hd = el("div", "cx-job-head");
    hd.appendChild(el("span", "cx-job-state " + job.state, jobStateLabel(job)));
    const a = el("a", "cx-job-url", job.url);
    a.href = job.url; a.target = "_blank"; a.rel = "noopener noreferrer";
    hd.appendChild(a);
    const x = el("button", "cx-x", "✕");
    x.title = "remove this row (the server keeps whatever it is doing)";
    x.onclick = () => {
      stopJob(job);
      if (STORED_NOTHING.has(job.state) || job.state === "preview")
        pasted.delete(job.key);
      jobs.splice(jobs.indexOf(job), 1); renderJobs();
    };
    hd.appendChild(x);
    d.appendChild(hd);

    if (job.state === "down") {
      d.appendChild(failLine("Link ingestion is not deployed on this server.",
        `${job.error}. The board above is a different service and is ` +
        "unaffected — nothing on this page is stale because of it. When the " +
        "endpoint lands, this bar starts working with no change here."));
      return d;
    }
    if (job.state === "declined") {
      d.appendChild(failLine("The source declined the request.",
        `${job.error}. That is the site saying no — private, age-gated or ` +
        "rate-limited. This has stopped and will NOT retry: retrying a 403 " +
        "or a 429 is how a source starts refusing everything.", "stop"));
      return d;
    }
    if (job.state === "robots") {
      d.appendChild(failLine("robots.txt disallows this URL for our fetcher.",
        `${job.error} Nothing was fetched beyond the robots file, and nothing ` +
        "was stored. This is our own rule, obeyed rather than worked around.",
        "stop"));
      return d;
    }
    if (job.state === "duplicate") {
      d.appendChild(failLine("Already held.",
        (job.item_id ? `Stored as ${job.item_id}. ` : "") +
        "The same video under a second URL form is already in the corpus, so " +
        "nothing was ingested twice. Its take is on the board already."));
      if (job.note) d.appendChild(el("div", "cx-verbatim", job.note));
      if (job.dupes && job.dupes.length > 1)
        d.appendChild(el("div", "sub",
          `The warehouse holds it under ${plural(job.dupes.length, "item id")}: ` +
          job.dupes.join(", ") + ". watch?v=, youtu.be/ and /live/ are one video."));
      d.appendChild(el("div", "sub",
        "No decision was owed here: a duplicate skips the preview halt " +
        "because nothing would have been transcribed either way."));
      return d;
    }
    if (job.state === "notepisode") {
      d.appendChild(failLine("That link is not an episode.",
        `${job.error} — there is no substantive text behind it. An FPL league ` +
        "invite has been ingested this way before and became an article " +
        "titled `a6fgym`. Paste a video or an episode page instead."));
      d.appendChild(el("div", "sub",
        "This skipped the preview halt too: a refusal is an answer, not a " +
        "spend to decide about."));
      return d;
    }
    if (job.state === "nomedia") {
      d.appendChild(failLine("No captions published, and no audio file behind the page.",
        `${job.error}. There is nothing to read and nothing to transcribe. If ` +
        "this is a podcast, paste the episode's YouTube link instead — that " +
        "usually has captions and takes about four seconds."));
      return d;
    }
    if (job.state === "passed") {
      const box = el("div", "cx-pass");
      box.appendChild(el("b", null, "Declined. Nothing was spent."));
      box.appendChild(el("div", "sub", String(job.error ||
        "the server recorded the decline but returned no sentence about it")));
      box.appendChild(againRow(job, "Paste it again"));
      d.appendChild(box);
      return d;
    }
    if (job.state === "expired") {
      const box = el("div", "cx-fail");
      box.appendChild(el("b", null, "The preview expired before you decided."));
      box.appendChild(el("div", "sub", String(job.error ||
        "the preview aged out; the server returned no sentence about it")));
      box.appendChild(el("div", "sub",
        "Expiring writes nothing and undoes nothing, because nothing had been " +
        "written. A fresh paste fetches the page again and parks again."));
      box.appendChild(againRow(job, "Paste it again"));
      d.appendChild(box);
      return d;
    }
    if (job.state === "cancelled") {
      const late = !!(job.p && job.p.cancelled_after_write);
      const box = el("div", "cx-fail" + (late ? " stop" : ""));
      box.appendChild(el("b", null, late
        ? "Stopped, but the cancel arrived after the write."
        : "Stopped before anything was written."));
      box.appendChild(el("div", "sub", String(job.error ||
        "the server recorded the cancel but returned no sentence about it")));
      if (late) {
        const id = job.p && job.p.discarded_item_id;
        box.appendChild(el("div", "sub",
          "That is not the same as never having run. The transcription and " +
          "analysis finished and " + (id ? `item ${id} was written, then ` : "what they wrote was ") +
          "discarded — hidden from every read path, not deleted. GPU seconds " +
          "were spent. Restoring it is possible; the archive still has it."));
      }
      box.appendChild(againRow(job, "Paste it again"));
      d.appendChild(box);
      return d;
    }
    if (job.state === "failed") {
      d.appendChild(failLine("The job failed.", String(job.error)));
      if (job.p && job.p.error_code)
        d.appendChild(el("div", "sub", `error_code: ${job.p.error_code}`));
      d.appendChild(againRow(job, "Try it again"));
      return d;
    }

    /* ---- the live states: parked, running, stopping, done -------------- */

    if (job.state === "preview") {
      d.appendChild(renderPreview(job));
      d.appendChild(el("div", "cx-ledger-cap", "What yes would run"));
    } else if (job.path) {
      const p = el("div", "cx-job-path");
      p.append(job.path === "captions"
        ? "Captions path — measured at about 286× realtime."
        : job.path === "asr"
          ? "No captions, so local speech-to-text — measured at about 11.5× realtime."
          : `Path: ${job.path}.`);
      d.appendChild(p);
    }

    d.appendChild(renderLedger(job));

    if (job.conflict) d.appendChild(failLine("The server disagreed.", job.conflict));
    if (job.actErr) d.appendChild(failLine("That request did not land.", job.actErr));

    if (job.state === "done") {
      const ok = el("div", "cx-job-ok");
      ok.append(el("b", null, "Ingested."),
        job.item_id ? ` Stored as ${job.item_id}. ` : " ",
        "It joins the board on the next panel read — reload to see it.");
      d.appendChild(ok);
      d.appendChild(renderTake(job));
    } else {
      if (job.state === "running" || job.state === "stopping") {
        const acts = el("div", "cx-job-acts");
        const stop = el("button", "", job.state === "stopping"
          ? "Stopping…" : "Stop this");
        stop.disabled = job.state === "stopping" || job.busy === "abort";
        stop.onclick = () => abortJob(job);
        acts.appendChild(stop);
        acts.appendChild(el("span", "sub",
          job.stage === "transcribe" || job.stage === "analyse"
            ? "The transcribe/analyse call cannot be interrupted mid-way. A " +
              "stop now lets it finish and discards what it wrote — that costs " +
              "the seconds either way, and this row will say so."
            : "Nothing has been written yet, so stopping now is clean by " +
              "ordering rather than by cleanup."));
        d.appendChild(acts);
      }
      d.appendChild(el("div", "sub",
        "This keeps running on the server if you leave the page. This ledger " +
        "does not — it stops updating when the view unmounts."));
    }
    return d;
  }

  function againRow(job, label) {
    const row = el("div", "cx-job-acts");
    const b = el("button", "", label);
    b.onclick = () => { jobs.splice(jobs.indexOf(job), 1); repaste(job); };
    row.appendChild(b);
    row.appendChild(el("span", "sub",
      "Nothing was stored for this url, so pasting it again is a fresh page " +
      "fetch and a fresh preview."));
    return row;
  }

  /* ---- the preview card: everything needed to judge relevance, once ---- */

  function renderPreview(job) {
    const pv = job.preview || {};
    const box = el("div", "cx-pv");

    box.appendChild(el("h3", "cx-pv-title", pv.title ||
      "the page stated no title"));

    /* WHO. The resolved identity, an honest "we do not track this one", or an
       honest absence — never a placeholder standing in for a name. */
    const who = el("div", "cx-pv-who");
    const name = pv.creator || pv.channel || null;
    if (name) {
      who.appendChild(el("b", null, name));
      if (pv.creator == null)
        who.appendChild(el("span", "chip warn", "unresolved — this is the channel name"));
      else if (pv.tracked === true)
        who.appendChild(el("span", "chip s1", "on your panel"));
      else if (pv.tracked === false)
        who.appendChild(el("span", "chip warn", "a real channel you do not track"));
      else
        who.appendChild(el("span", "chip", "the payload did not say whether it is tracked"));
    } else {
      who.appendChild(el("b", null, "Nobody is named on this page"));
      who.appendChild(el("span", "chip warn", "creator unresolved"));
    }
    if (pv.channel && pv.creator && pv.channel !== pv.creator)
      who.appendChild(el("span", "cx-pv-chan", `channel: ${pv.channel}`));
    box.appendChild(who);
    if (pv.creator_reason) box.appendChild(el("div", "cx-pv-why", pv.creator_reason));
    else if (pv.creator_basis)
      box.appendChild(el("div", "cx-pv-why",
        `resolved by ${pv.creator_basis}; the payload carried no creator_reason.`));
    else
      box.appendChild(el("div", "cx-pv-why",
        "the payload carried no creator_basis and no creator_reason, so how " +
        "this name was arrived at is not recorded."));

    /* WHEN, and therefore WHICH WEEK — with the basis attached to the week,
       never separated from it. An inferred week rendered bare is a guess
       presented as a fact. */
    const facts = el("dl", "cx-pv-facts");
    const fact = (k, v, why, chip) => {
      facts.appendChild(el("dt", null, k));
      const dd = el("dd");
      dd.appendChild(el("span", "cx-pv-v", v));
      if (chip) dd.appendChild(chip);
      if (why) dd.appendChild(el("div", "cx-pv-why", why));
      facts.appendChild(dd);
    };

    const when = fmtWhen(pv.published_at);
    fact("Published",
      when ? `${when} · ${relAge(pv.published_at).text}` : "not stated",
      pv.published_basis || null);

    const gw = pv.gameweek;
    if (gw && gw.label) {
      fact("Gameweek", gw.label, gw.reason || null,
        gw.is_guess ? el("span", "chip warn", "a guess") :
        gw.basis === "stated" ? el("span", "chip s1", "stated in the content") : null);
    } else {
      fact("Gameweek", "none derived",
        "the payload carried no gameweek block for this preview — with no " +
        "publication date there is no deadline to place it against, and none " +
        "is invented.");
    }

    fact("Length", clock(pv.media_seconds) ||
      "the source did not state a duration");

    box.appendChild(facts);

    /* WHAT IT SAYS IT IS ABOUT. Verbatim, and clamped rather than cut, so the
       judgement is made on the source's own words. */
    if (pv.description) {
      const desc = el("p", "cx-pv-desc" + (job.descOpen ? " open" : ""),
        String(pv.description));
      box.appendChild(desc);
      if (String(pv.description).length > 260) {
        const more = el("button", "cx-link-btn",
          job.descOpen ? "less" : "more of the description");
        more.onclick = () => { job.descOpen = !job.descOpen; renderJobs(); };
        box.appendChild(more);
      }
    } else {
      box.appendChild(el("p", "cx-pv-desc empty-note",
        "The page carried no description, so there is nothing here to read " +
        "except the title."));
    }

    if (Array.isArray(pv.duplicate_of) && pv.duplicate_of.length)
      box.appendChild(el("div", "cx-pv-why",
        `The warehouse already holds ${plural(pv.duplicate_of.length, "row")} ` +
        `for this url shape (${pv.duplicate_of.join(", ")}), but none of them ` +
        "is this video's stored item."));

    /* WHAT YES COSTS. The measured ETA for THIS video, or the payload's own
       sentence about why there is not one. Never a number we made up. */
    const cost = el("div", "cx-pv-cost");
    const line = el("div", "cx-pv-costline");
    if (pv.eta_s != null) {
      line.append("Saying yes runs ", el("b", null, secs(pv.eta_s)),
                  " of transcription on this machine.");
    } else {
      line.append(el("b", null, "No ETA for this one."),
        " " + (pv.eta_reason || "the payload gave no eta_s and no eta_reason, " +
               "so there is nothing to quote — no number is invented here."));
    }
    cost.appendChild(line);
    const path = el("div", "cx-pv-why");
    path.append(pathLabel(pv.transcript_path),
      pv.path_reason ? ` — ${pv.path_reason}` : " — the payload carried no path_reason.");
    cost.appendChild(path);
    if (pv.eta_s != null && pv.eta_basis)
      cost.appendChild(el("div", "cx-pv-why", `measured basis: ${pv.eta_basis}`));
    box.appendChild(cost);

    /* TWO ACTIONS. The free one says it is free where the button is. */
    const acts = el("div", "cx-pv-acts");
    const yes = el("button", "primary", job.busy === "accept"
      ? "Starting…" : "Transcribe it");
    yes.disabled = !!job.busy;
    yes.onclick = () => decide(job, "accept");
    const no = el("button", "cx-no", job.busy === "decline"
      ? "Declining…" : "Not relevant");
    no.disabled = !!job.busy;
    no.onclick = () => decide(job, "decline");
    acts.append(yes, no);
    box.appendChild(acts);
    box.appendChild(el("div", "cx-pv-freeline",
      "Declining costs nothing: the page has already been fetched, and " +
      "nothing is transcribed, analysed or stored unless you say yes."));

    if (job.conflict) box.appendChild(failLine("The server disagreed.", job.conflict));
    if (job.actErr) box.appendChild(failLine("That request did not land.", job.actErr));

    /* THE CLOCK. A parked job that has aged out must not look live. */
    const exp = el("div", "cx-pv-exp");
    const until = fmtWhen(job.expires);
    job.expEl = el("b", null, expiryText(job));
    exp.append(job.expEl, until
      ? ` to decide — this preview is held until ${until}, then it expires and you paste the link again.`
      : ". The payload carried no preview_expires_utc, so when this parks out is unknown to this page.");
    box.appendChild(exp);
    return box;
  }

  function pathLabel(path) {
    if (path === "captions") return "Published captions, about 286× realtime";
    if (path === "asr") return "Local speech-to-text, about 11.5× realtime";
    if (path === "text") return "Article text, nothing to transcribe";
    if (!path) return "No transcription path stated in the payload";
    return `Path: ${path}`;
  }

  /* Seconds, kept in seconds where seconds is what makes two decisions
     comparable: "about 4 seconds" and "about 105 seconds" must be readable as
     the same unit at a glance. */
  function secs(s) {
    const n = Number(s);
    if (!isFinite(n)) return "an unstated amount";
    if (n < 1) return "under a second";
    if (n < 300) return `about ${Math.round(n)} seconds`;
    return `about ${Math.round(n / 60)} minutes`;
  }

  function fmtWhen(iso) {
    const d = parseTs(iso);
    if (!d || isNaN(d)) return null;
    return d.toLocaleString(undefined, { year: "numeric", month: "short",
      day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  /* ---- the stage ladder ------------------------------------------------ */

  function renderLedger(job) {
    const names = job.stages || LINK_STAGES;
    const idx = job.stage ? names.indexOf(job.stage) : -1;
    const parked = job.state === "preview";
    const ledger = el("div", "cx-stages");
    names.forEach((nm, i) => {
      let state = job.state === "done" ? "done"
        : idx < 0 ? (i === 0 ? "now" : "wait")
        : i < idx ? "done" : i === idx ? "now" : "wait";
      const halt = parked && state === "now";
      const row = el("div", "cx-stage " + state + (halt ? " halt" : ""));
      row.appendChild(el("span", "cx-stage-mark",
        halt ? "◆" : state === "done" ? "✓" : state === "now" ? "◐" : "○"));
      row.appendChild(el("span", "cx-stage-name", nm));
      if (halt) row.appendChild(el("span", "cx-stage-note", "parked — your call"));
      if (!parked && state === "now" && job.pct != null) {
        const barwrap = el("span", "cx-stage-bar");
        const fill = el("span", "cx-stage-fill");
        fill.style.width = `${Math.max(0, Math.min(100, Number(job.pct)))}%`;
        barwrap.appendChild(fill);
        row.appendChild(barwrap);
        row.appendChild(el("span", "cx-stage-pct", `${Math.round(job.pct)}%`));
      }
      /* `eta_s` is the ESTIMATE FOR THE WHOLE TRANSCRIPTION, set once from a
         measured rate and a measured duration. The server never decrements
         it, so calling it "left" was a countdown that never counted down. */
      if (!parked && state === "now" && nm === "transcribe" && job.eta != null)
        row.appendChild(el("span", "cx-stage-eta",
          `≈${Math.round(job.eta)}s for this video`));
      ledger.appendChild(row);
    });
    return ledger;
  }

  /* ---- what it turned into --------------------------------------------- */

  /* The take's own bucket names, which are NOT the analysis's: `_take` renames
     `captaincy` to `captain` and `chip_advice` to `chips` on the way out, and
     lifts every "watch" call into `watching` so a watch is never counted as a
     transfer in. Counting the wrong keys here would print "no calls at all"
     over a take that has twelve. */
  const TAKE_BUCKETS = [["transfers_in", "in"], ["transfers_out", "out"],
                        ["captain", "captain"], ["differentials", "differential"],
                        ["chips", "chip"], ["watching", "watching"]];

  function renderTake(job) {
    const p = job.p || {}, r = p.result || null;
    const box = el("div", "cx-take");
    const who = p.creator || (r && r.creator);
    if (who) {
      const w = el("div", "cx-take-row");
      w.appendChild(el("span", "cx-take-k", "Creator"));
      w.appendChild(el("span", null, who));
      if (p.tracked === false) w.appendChild(el("span", "chip warn", "not tracked"));
      else if (p.tracked === true) w.appendChild(el("span", "chip s1", "on your panel"));
      box.appendChild(w);
      if (p.creator_reason) box.appendChild(el("div", "cx-pv-why", p.creator_reason));
    }
    const gw = p.gameweek;
    if (gw && gw.label) {
      const g = el("div", "cx-take-row");
      g.appendChild(el("span", "cx-take-k", "Gameweek"));
      g.appendChild(el("span", null, gw.label));
      box.appendChild(g);
      if (gw.reason) box.appendChild(el("div", "cx-pv-why", gw.reason));
    }
    if (!r) {
      box.appendChild(el("div", "sub",
        "The payload carried no `result` block, so what this became is not " +
        "readable from here — open the board to see it."));
      return box;
    }
    if (!r.take) {
      box.appendChild(el("div", "sub", r.reason ||
        "there is no take and the payload gave no reason for its absence"));
      return box;
    }
    const counts = TAKE_BUCKETS
      .map(([k, label]) => [((r.take[k] || []).length), label])
      .filter(([n]) => n > 0)
      .map(([n, label]) => `${n} ${label}`);
    const c = el("div", "cx-take-row");
    c.appendChild(el("span", "cx-take-k", "Calls"));
    c.appendChild(el("span", null, counts.length ? counts.join(" · ")
      : "the analysis returned no calls at all"));
    box.appendChild(c);
    const bullets = (r.take.summary_bullets || []).slice(0, 2);
    for (const b of bullets) box.appendChild(el("div", "cx-take-b", String(b)));
    if (r.take.model)
      box.appendChild(el("div", "cx-pv-why", `read by ${r.take.model}` +
        (r.n_segments ? `, over ${plural(r.n_segments, "transcript segment")}` : "")));
    return box;
  }

  const jobStateLabel = j => ({
    posting: "SENDING", running: "WORKING", done: "DONE", down: "UNAVAILABLE",
    declined: "STOPPED", duplicate: "DUPLICATE", nomedia: "NO MEDIA",
    notepisode: "NOT AN EPISODE", failed: "FAILED",
    preview: "YOUR CALL", passed: "NOT TRANSCRIBED", expired: "PREVIEW EXPIRED",
    cancelled: "CANCELLED", stopping: "STOPPING", robots: "ROBOTS SAY NO",
  }[j.state] || j.state);

  /* ======================================================== the toolbars */

  /* ===================================================== the content area
   *
   * Order is the owner's: the takes that matter against the squad first,
   * then the board, the armband, the matrix (collapsed, drawn on open), watch
   * calls where any exist, and the report cards.
   */

  function renderBoard(rows) {
    if (!rows.length) {
      const full = windowed(buildRows(false), false).length;
      body.appendChild(emptyBox(
        wantH48() && freshState === "done" && full
          ? "No take in the last 48h names a player for this gameweek."
          : "Nobody has named a player for this gameweek.",
        full ? `${plural(full, "player")} sit behind the two toggles above.`
             : "Takes land in the day or two before a deadline."));
      renderReportCards();
      return;
    }
    renderMainTakes(rows);

    const bsec = el("div", "cx-sec");
    const h = el("div", "cx-sechead");
    h.appendChild(el("h3", null, "Panel intent, your exposure"));
    if (squadReady) {
      const lanes = el("span", "cx-tiny");
      lanes.append(`lanes: your ${squad.gw != null ? `GW${squad.gw}` : "current"} squad` +
        (squad.as_of ? `, read ${relAge(squad.as_of).text}` : ""));
      lanes.title = squad.provenance_source || "";
      h.appendChild(lanes);
    }
    bsec.appendChild(h);
    if (!squadReady) {
      const p = el("p", "cx-provline warn");
      p.append(`Your squad could not be read${squadErr ? `: ${squadErr}` : ""}. ` +
               "Every player sits in one lane.");
      bsec.appendChild(p);
    }
    bsec.appendChild(drawBoard(rows));
    bsec.appendChild(boardLegend(rows));
    body.appendChild(bsec);

    renderArmband(rows);
    renderMatrix(rows);
    renderWatching();
    renderReportCards();
  }

  function drawBoard(rows) {
    const wrap = el("div", "cx-chartwrap");
    const tip = el("div", "cx-tip");

    const W = 1000, GUT = 152, PADR = 34, PLOT = W - GUT - PADR;
    const maxAbs = Math.max(2, ...rows.map(r => Math.abs(r.net)));
    const x = v => GUT + ((v + maxAbs) / (2 * maxAbs)) * PLOT;
    const DOT = 12;                        // packing pitch
    const lanes = squadReady ? LANES : [{ key: "none", label: "Everyone", agreeSide: 0 }];

    /* pack each lane's rows into columns at their integer net */
    const packed = lanes.map(L => {
      const mineRows = rows.filter(r => (squadReady ? r.lane : "none") === L.key);
      const cols = new Map();
      for (const r of mineRows) {
        const k = r.net;
        if (!cols.has(k)) cols.set(k, []);
        cols.get(k).push(r);
      }
      let maxRows = 1;
      const marks = [];
      for (const [v, list] of cols) {
        list.sort(byWeightOfMouth);
        const nCol = Math.ceil(list.length / 7);
        const nRow = Math.ceil(list.length / nCol);
        maxRows = Math.max(maxRows, nRow);
        list.forEach((r, i) => {
          const c = Math.floor(i / nRow), rr = i % nRow;
          marks.push({ r, cx: x(v) + (c - (nCol - 1) / 2) * DOT, row: rr, v });
        });
      }
      return { L, rows: mineRows, marks, height: maxRows * DOT + 30 };
    });

    const TOP = 40;
    const H = TOP + packed.reduce((a, p) => a + p.height, 0) + 16;
    const svg = sv("svg", { class: "cx-board", viewBox: `0 0 ${W} ${H}`,
                            role: "img" });
    svg.appendChild(sv("title", null,
      "Panel intent along one axis, your squad exposure as four lanes"));

    /* hatch for keyword-only marks is a stroke, not a fill — see legend */
    const defs = sv("defs");
    svg.appendChild(defs);

    /* axis header */
    svg.appendChild(sv("text", { class: "cx-axlabel", x: GUT, y: 14 },
      "◄ THE PANEL IS OUT"));
    svg.appendChild(sv("text", { class: "cx-axlabel end", x: GUT + PLOT, y: 14 },
      "THE PANEL IS IN ►"));
    for (let v = -maxAbs; v <= maxAbs; v++) {
      svg.appendChild(sv("line", { class: "cx-grid", x1: x(v), x2: x(v),
                                   y1: TOP - 8, y2: H - 12 }));
      svg.appendChild(sv("text", { class: "cx-tick", x: x(v), y: TOP - 14 },
        v === 0 ? "0" : (v > 0 ? `+${v}` : `−${-v}`)));
    }
    svg.appendChild(sv("line", { class: "cx-zero", x1: x(0), x2: x(0),
                                 y1: TOP - 8, y2: H - 12 }));
    svg.appendChild(sv("text", { class: "cx-axunit", x: GUT + PLOT / 2, y: H - 1 },
      "people: buy minus sell"));

    let y = TOP;
    for (const P of packed) {
      const laneTop = y, laneBot = y + P.height;
      const base = laneBot - 16;

      /* the shaded wedge: you and the panel already agree */
      if (squadReady && P.L.agreeSide !== 0) {
        const x0 = P.L.agreeSide > 0 ? x(0) : GUT;
        const w = P.L.agreeSide > 0 ? GUT + PLOT - x(0) : x(0) - GUT;
        svg.appendChild(sv("rect", { class: "cx-shade", x: x0, y: laneTop + 2,
                                     width: w, height: P.height - 6 }));
      }
      svg.appendChild(sv("line", { class: "cx-lanerule", x1: 8, x2: GUT + PLOT,
                                   y1: laneTop, y2: laneTop }));

      /* lane gutter: label, count, and the agreed count if any */
      const nAgree = P.rows.filter(r => r.agreed).length;
      svg.appendChild(sv("text", { class: "cx-lanelabel", x: 8, y: laneTop + 18 },
        P.L.label));
      svg.appendChild(sv("text", { class: "cx-lanecount", x: 8, y: laneTop + 34 },
        `${plural(P.rows.length, "player")}` +
        (nAgree ? ` · ${nAgree} agreed` : "")));

      for (const m of P.marks) {
        const cy = base - m.row * DOT;
        const g = sv("g", { class: "cx-mark" + (m.r.agreed ? " agreed" : "") });
        const hue = m.r.split ? "split"
          : m.r.net > 0 ? "in" : m.r.net < 0 ? "out" : "flat";
        if (m.r.split) {
          /* a split is two half-discs: the two sides, not an average */
          g.appendChild(sv("path", { class: "cx-half out",
            d: `M ${m.cx} ${cy - 4.8} A 4.8 4.8 0 0 0 ${m.cx} ${cy + 4.8} Z` }));
          g.appendChild(sv("path", { class: "cx-half in",
            d: `M ${m.cx} ${cy - 4.8} A 4.8 4.8 0 0 1 ${m.cx} ${cy + 4.8} Z` }));
        } else {
          g.appendChild(sv("circle", {
            class: `cx-node ${hue} ${m.r.cueOnly ? "hollow" : "solid"}`,
            cx: m.cx, cy, r: 4.8 }));
        }
        if (m.r.nCap > 0)
          g.appendChild(sv("text", { class: "cx-capstar", x: m.cx + 7.5, y: cy + 3.6 }, "★"));

        /* Direct labels, selectively: your own players always (they are the
           point), plus the extreme mark in each lane. Everything else is a
           dot with a tooltip and a card below. */
        const isExtreme = m.r === P.marks.reduce(
          (best, k) => Math.abs(k.r.net) > Math.abs(best.r.net) ? k : best, P.marks[0]).r;
        if ((m.r.lane !== "none" || isExtreme || m.r.split) && !m.r.agreed) {
          const lx = m.cx + (m.r.nCap > 0 ? 17 : 9);
          g.appendChild(sv("text", {
            class: "cx-plabel" + (m.r.lane !== "none" ? " mine" : ""),
            x: lx, y: cy + 3.6 }, dn(m.r)));
        }
        g.addEventListener("mouseenter", ev => showTip(ev, m.r));
        g.addEventListener("mousemove", ev => moveTip(ev));
        g.addEventListener("mouseleave", () => tip.classList.remove("on"));
        g.addEventListener("click", () => openPlayer(m.r));
        g.setAttribute("tabindex", "0");
        g.addEventListener("keydown", e => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openPlayer(m.r); }
        });
        svg.appendChild(g);
      }
      y = laneBot;
    }

    function showTip(ev, r) {
      tip.textContent = "";
      tip.appendChild(el("b", null, dn(r)));
      tip.appendChild(el("div", "sub",
        [r.pos, r.team, r.price != null ? fmtPrice(r.price) : null,
         r.own_pct != null ? `${fmt1(r.own_pct)}% owned` : null]
          .filter(Boolean).join(" · ")));
      const line = (k, v) => {
        const d = el("div", "cx-tl");
        d.appendChild(el("span", "cx-tk", k));
        d.appendChild(el("span", "cx-tv", v));
        tip.appendChild(d);
      };
      line("in", `${r.nBuy}`);
      line("out", `${r.nSell}`);
      if (r.nCap) line("captain", `${r.nCap}`);
      line("evidence", `${r.anyLlm} considered / ${r.anyCue} keyword`);
      line("your lane", laneLabel(r.lane));
      tip.appendChild(el("div", "cx-tipreason", r.reason));
      tip.classList.add("on");
      moveTip(ev);
    }
    function moveTip(ev) {
      const b = wrap.getBoundingClientRect();
      const px = ev.clientX - b.left, py = ev.clientY - b.top;
      tip.style.left = `${Math.min(Math.max(8, px + 14), b.width - 200)}px`;
      tip.style.top = `${Math.max(4, py - 12)}px`;
    }

    /* At narrow widths a 1000-unit viewBox squeezed into 340px makes every
       label unreadable, so the board scrolls inside its own container rather
       than shrinking. The tooltip stays parented to the outer wrapper, and
       positions from viewport coordinates, so inner scroll never moves it. */
    const scroller = el("div", "cx-chartscroll");
    scroller.appendChild(svg);
    wrap.append(scroller, tip);
    return wrap;
  }

  function boardLegend(rows) {
    const box = el("div", "cx-legend");
    const item = (swatch, text) => {
      const d = el("div", "cx-legitem");
      d.appendChild(swatch);
      d.append(text);
      return d;
    };
    const dot = cls => el("span", "cx-dot " + cls);
    box.appendChild(item(dot("solid in"), "net in"));
    box.appendChild(item(dot("solid out"), "net out"));
    box.appendChild(item(dot("split"), "split"));
    box.appendChild(item(dot("hollow"), "keyword only"));
    box.appendChild(item(el("span", "cx-star", "★"), "captain call"));
    if (squadReady) {
      const sh = item(el("span", "cx-shadesw"), "already agree");
      sh.title = "you own him and the panel is not net selling, or you do not and it is not net buying; " +
        "a split, a bench player or a captain call you did not make is never shaded";
      box.appendChild(sh);
    }
    const single = rows.filter(r => r.voices === 1).length;
    const shape = el("span", "cx-tiny cx-shape");
    shape.append(`${plural(rows.length, "player")} · ${single} single-voice · ` +
      `${rows.filter(r => r.split).length} split · ${rows.filter(r => r.cueOnly).length} keyword-only`);
    box.appendChild(shape);
    return box;
  }

  /* ------------------------------------------------------- the main takes */

  function renderMainTakes(rows) {
    const inSquad = rows.filter(r => r.lane !== "none" && !r.agreed).sort(byWeightOfMouth);
    const buys = rows.filter(r => r.lane === "none" && !r.agreed && r.net > 0)
      .sort(byWeightOfMouth);
    const agreed = rows.filter(r => r.agreed);

    const sec = el("div", "cx-sec cx-maintakes");
    const h = el("div", "cx-sechead");
    const h3 = el("h3", null, "Main takes");
    h3.title = "ordered by considered takes, then how many people said it, then the gap; never by record";
    h.appendChild(h3);
    sec.appendChild(h);

    if (squadReady) {
      sec.appendChild(el("h4", "cx-subhead", `Against your squad (${inSquad.length})`));
      if (!inSquad.length)
        sec.appendChild(el("p", "sub", "Nobody argued with a player you own."));
      for (const r of inSquad) sec.appendChild(decisionCard(r, true));
    }

    const CUT = 6;
    const strong = buys.filter(r => r.voices > 1);
    const single = buys.filter(r => r.voices <= 1);
    sec.appendChild(el("h4", "cx-subhead", `Buys you don't own (${buys.length})`));
    if (!buys.length)
      sec.appendChild(el("p", "sub", "Nobody is buying anything you don't hold."));
    const head = strong.length ? strong : buys.slice(0, CUT);
    for (const r of head.slice(0, CUT)) sec.appendChild(decisionCard(r, false));
    const rest = (strong.length ? single : buys.slice(CUT));
    if (rest.length) {
      const d = el("details", "cx-disclose");
      d.appendChild(el("summary", null, `${plural(rest.length, "single-voice call")}`));
      const list = el("div", "cx-thin");
      for (const r of rest.sort(byWeightOfMouth)) {
        const b = el("button", "cx-thinrow");
        b.appendChild(el("span", "cx-dot " + (r.cueOnly ? "hollow" : "solid in")));
        b.appendChild(el("b", null, dn(r)));
        b.appendChild(el("span", "sub",
          ` ${r.pos || ""} ${r.team || ""} · ${[...r.buy.people].join(", ") || "–"}` +
          `${r.cueOnly ? " · keyword only" : ""}`));
        b.onclick = () => openPlayer(r);
        list.appendChild(b);
      }
      d.appendChild(list);
      sec.appendChild(d);
    }

    /* agreement: a count, not twenty rows */
    const ag = el("div", "cx-agreed");
    const agb = el("button", "cx-agreed-btn");
    agb.setAttribute("aria-expanded", String(showAgreed));
    agb.append(el("b", null, String(agreed.length)),
      ` player${agreed.length === 1 ? "" : "s"} where you and the panel already agree`);
    const agList = el("div", "cx-agreed-list");
    agList.hidden = !showAgreed;
    agb.onclick = () => {
      showAgreed = !showAgreed; agList.hidden = !showAgreed;
      agb.setAttribute("aria-expanded", String(showAgreed));
    };
    for (const r of agreed.sort(byWeightOfMouth)) {
      const b = el("button", "cx-thinrow");
      b.appendChild(el("b", null, dn(r)));
      b.appendChild(el("span", "sub", ` · ${r.reason}`));
      b.onclick = () => openPlayer(r);
      agList.appendChild(b);
    }
    ag.append(agb, agList);
    sec.appendChild(ag);
    sec.appendChild(recordStrip());
    body.appendChild(sec);
  }

  /* One take: who, which way, how fresh, and each voice with its record. */
  function decisionCard(r, owned) {
    const c = el("div", "cx-card" + (r.split ? " split" : r.net > 0 ? " in" : r.net < 0 ? " out" : ""));
    const hd = el("div", "cx-card-head");
    /* THE PLAYER IS THE CLICK: face and name open the quotes drawer, the same
       drawer a matrix cell opens, with a timestamped link on every quote. */
    const pl = el("button", "cx-card-player");
    pl.appendChild(faceImg(r.code, "cx-face"));
    const idb = el("span", "cx-card-id");
    const verb = r.split ? "Split"
      : r.capElsewhere && r.net === 0 ? "Armband"
      : r.net > 0 ? (owned ? "They like him" : "Buy")
      : r.net < 0 ? (owned ? "Sell" : "Avoid") : "Mixed";
    const t = el("span", "cx-card-title");
    t.appendChild(el("span", "cx-verb", verb));
    t.appendChild(el("b", null, dn(r)));
    if (freshState === "done") {
      const ft = freshFor(r.code);
      if (ft != null) {
        const fr = el("span", "cx-fresh" + (Date.now() - ft < H48 ? " hot" : ""),
          `latest ${agoText(ft)}`);
        fr.title = "the freshest claim naming him for this gameweek";
        t.appendChild(fr);
      }
    }
    t.appendChild(el("span", "cx-qcue", "quotes ›"));
    idb.appendChild(t);
    idb.appendChild(el("span", "sub",
      [r.pos, r.team, r.price != null ? fmtPrice(r.price) : null,
       r.own_pct != null ? `${fmt1(r.own_pct)}% owned` : null,
       owned ? laneLabel(r.lane).toLowerCase() : "not owned",
      ].filter(Boolean).join(" · ")));
    pl.appendChild(idb);
    pl.title = `${dn(r)}: what was said, with timestamps`;
    pl.onclick = () => openPlayer(r);
    hd.appendChild(pl);
    const counts = el("div", "cx-card-counts");
    counts.appendChild(countChip("in", r.nBuy, r.buy));
    counts.appendChild(countChip("out", r.nSell, r.sell));
    if (r.nCap) counts.appendChild(countChip("cap", r.nCap, r.cap));
    if (r.cueOnly) {
      const k = el("span", "cx-count cue", "keyword only");
      k.title = "every call on him is a keyword window, not a considered take";
      counts.appendChild(k);
    }
    if (r.panel_owned && r.panel_owned.of != null) {
      const po = el("span", "cx-count own");
      po.append(el("b", null, `${r.panel_owned.n} of ${r.panel_owned.of}`), " hold him");
      po.title = (r.panel_owned.people || []).join(", ") || "no names published";
      counts.appendChild(po);
    }
    hd.appendChild(counts);
    c.appendChild(hd);

    c.appendChild(el("div", "cx-why", r.reason));

    /* every voice, once, with its direction and its report card */
    const marks = new Map();
    const add = (who, m) => { if (!marks.has(who)) marks.set(who, []); marks.get(who).push(m); };
    for (const p of r.buy.people) add(p, "in");
    for (const p of r.sell.people) add(p, "out");
    for (const p of r.cap.people) add(p, "★");
    const who = el("div", "cx-rcrow");
    for (const [name, ms] of marks) who.appendChild(rcChip(name, ms.join(" ")));
    c.appendChild(who);

    const act = el("div", "cx-actions");
    act.appendChild(crossLink("#xpoints", "Projections", r));
    act.appendChild(crossLink("#template", "EliteFPL", r));
    c.appendChild(act);
    return c;
  }

  function countChip(kind, n, g) {
    const s = el("span", "cx-count " + kind);
    s.appendChild(el("b", null, String(n)));
    s.append(kind === "in" ? " in" : kind === "out" ? " out" : " ★ C");
    s.title = `${g.llm} considered, ${g.cue} keyword` +
      (g.people.length ? `\n${g.people.join(", ")}` : "");
    return s;
  }

  function crossLink(hash, label, r) {
    const a = el("a", "cx-cross", label + " ↗");
    a.href = `${hash}?code=${r.code}`;
    a.title = `open the ${label} tab and search for ${dn(r)} there`;
    return a;
  }

  /* ------------------------------------------------ the report-card chip
   *
   * The reusable chip. Any claim row carries it: the creator's name, which
   * way they called it here, and their measured record from the report
   * card. Click opens the full card. A legacy pseudo-source says so.
   */
  function rcChip(name, dir) {
    // "user-shared" is links pasted by hand, not a person; the owner asked
    // for it removed, not labelled. A comment node appends anywhere and
    // renders nothing, so no caller needs a null check.
    if (LEGACY.has(name)) return document.createComment("legacy pseudo-source omitted");
    const card = rcByCreator.get(name);
    const cl = card && card.claims;
    const legacy = LEGACY.has(name);
    const v = verdict(cl, rc && rc.min_scored_claims);
    const b = el("button", "cx-rc " + v.cls + (legacy ? " legacy" : ""));
    if (dir) b.appendChild(el("span", "cx-rcdir", dir));
    b.appendChild(el("b", null, name));
    let stat;
    if (legacy) stat = "legacy pseudo-source";
    else if (!card) stat = rc ? "no card" : rcErr ? "card unavailable" : "card loading";
    else if (!cl.measured) stat = `${cl.n_total} claims, none scored`;
    else stat = `${pct(cl.hit_rate)} · n ${cl.n_scored} · ${v.word}`;
    b.appendChild(el("span", "cx-rcstat", stat));
    b.title = (card ? card.headline : "the report card has not loaded") + "\n" +
      (legacy ? "links pasted by hand; there is no person behind this label"
              : "open the creator: squad, transfers, record");
    b.onclick = e => {
      e.stopPropagation();
      if (legacy) { if (card) openCard(card); }
      else openCreator(name);
    };
    return b;
  }

  /* The same verdict without the name, for a gutter that already prints it. */
  function rcTag(name) {
    const card = rcByCreator.get(name);
    const cl = card && card.claims;
    if (!cl || !cl.measured) return null;
    const v = verdict(cl, rc && rc.min_scored_claims);
    const t = el("span", "cx-rctag " + v.cls, `${pct(cl.hit_rate)} · ${v.word}`);
    t.title = `${card.headline}\nsource creator_report_card, read ${relAge(rc.as_of).text}`;
    return t;
  }

  /* THE RECORD, USED. Under the takes: who has cleared the floor and sits
     above chance, who sits below. Anyone under `min_scored_claims` is absent,
     never ranked low. Ordered by the Wilson lower bound, which is what the
     data can actually claim. */
  function recordStrip() {
    const box = el("div", "cx-recstrip");
    if (!rc) {
      box.appendChild(el("span", "cx-tiny",
        rcErr ? `record unavailable: ${rcErr}` : "measuring the record…"));
      return box;
    }
    const floor = rc.min_scored_claims;
    const pool = (rc.cards || []).filter(c =>
      !LEGACY.has(c.creator) && c.claims.measured && c.claims.n_scored >= floor);
    const lead = pool.filter(c => c.claims.vs_coin_flip === "above")
      .sort((a, b) => b.claims.wilson_lo95 - a.claims.wilson_lo95).slice(0, 3);
    const lag = pool.filter(c => c.claims.vs_coin_flip === "below")
      .sort((a, b) => a.claims.wilson_lo95 - b.claims.wilson_lo95).slice(0, 3);
    const group = (label, list, empty) => {
      const g = el("span", "cx-recgroup");
      g.appendChild(el("span", "cx-reclabel", label));
      if (!list.length) g.appendChild(el("span", "cx-tiny", empty));
      for (const c of list) g.appendChild(rcChip(c.creator));
      return g;
    };
    box.appendChild(group("Record leaders", lead, "nobody above chance"));
    box.appendChild(group("Laggards", lag, "nobody below chance"));
    const m = el("span", "cx-tiny cx-recmeta");
    m.append(`${plural(pool.length, "creator")} clear the floor of ${floor} scored calls; ` +
      "a leader's or laggard's 95% interval excludes a coin flip; ordered by Wilson lower bound. " +
      `Source creator_report_card, read ${relAge(rc.as_of).text}.`);
    m.title = rc.note || "";
    box.appendChild(m);
    return box;
  }

  /* --------------------------------------------------------- the armband */

  function renderArmband(rows) {
    const caps = rows.filter(r => r.nCap > 0)
      .sort((a, b) => b.nCap - a.nCap || a.name.localeCompare(b.name));
    const sec = el("div", "cx-sec");
    const h = el("div", "cx-sechead");
    const h3 = el("h3", null, "The armband");
    h3.title = "captaincy is a one-of-N choice, not a buy or a sell; it never touches the axis above";
    h.appendChild(h3);
    sec.appendChild(h);
    if (!caps.length) {
      sec.appendChild(el("p", "sub", "No captain call in this window."));
      body.appendChild(sec); return;
    }
    const max = caps[0].nCap;
    const list = el("div", "cx-arm");
    for (const r of caps) {
      const row = el("button", "cx-armrow" + (r.lane === "captain" ? " yours" : ""));
      const nm = el("span", "cx-armname");
      nm.appendChild(el("b", null, dn(r)));
      if (r.lane === "captain") nm.appendChild(el("span", "cx-yours", "yours"));
      else if (r.lane !== "none") nm.appendChild(el("span", "cx-inyours", laneLabel(r.lane).toLowerCase()));
      row.appendChild(nm);
      const bw = el("span", "cx-armbar");
      const f = el("span", "cx-armfill");
      f.style.width = `${Math.max(4, Math.round(100 * r.nCap / max))}%`;
      bw.appendChild(f);
      row.appendChild(bw);
      row.appendChild(el("span", "cx-armn", String(r.nCap)));
      const am = el("span", "cx-armmeta", r.own_pct != null ? `${fmt1(r.own_pct)}% owned` : "");
      am.title = `${r.cap.llm} considered, ${r.cap.cue} keyword`;
      row.appendChild(am);
      row.title = r.cap.people.join(", ");
      row.onclick = () => openPlayer(r);
      list.appendChild(row);
    }
    sec.appendChild(list);
    const yours = caps.find(r => r.lane === "captain");
    if (squadReady && !yours && squad.captain)
      sec.appendChild(el("p", "cx-tiny", `Nobody named your captain, ${squad.captain}.`));
    body.appendChild(sec);
  }

  /* -------------------------------------------- watch calls (observations) */

  function renderWatching() {
    const obs = [];
    for (const c of res.creators || []) {
      for (const w of (c.take && c.take.watching) || [])
        obs.push({ ...w, creator: c.creator, latest: c.latest });
    }
    if (!obs.length) return;                 // nothing flagged: no section
    const sec = el("div", "cx-sec");
    const h = el("div", "cx-sechead");
    const h3 = el("h3", null, "Watch calls");
    h3.title = "observations, never counted as buys";
    h.appendChild(h3);
    sec.appendChild(h);
    const list = el("div", "cx-obslist");
    for (const o of obs) {
      const d = el("div", "cx-obs");
      const t = el("div", "cx-obs-head");
      t.appendChild(el("span", "cx-obsbadge", "observed"));
      t.appendChild(el("b", null, o.disambiguator || o.display_name ||
                                    o.name || String(o.code)));
      t.appendChild(rcChip(o.creator));
      d.appendChild(t);
      if (o.quote) d.appendChild(quoteBlock(o.quote, o, o.latest));
      list.appendChild(d);
    }
    sec.appendChild(list);
    body.appendChild(sec);
  }

  /* --------------------------------------------- the matrix, behind a fold */

  function renderMatrix(rows) {
    const shows = new Set();
    for (const r of rows) for (const g of [r.buy, r.sell, r.cap]) for (const p of g.people) shows.add(p);
    const sec = el("div", "cx-sec");
    const det = el("details", "cx-disclose big cx-matrix");
    det.open = gridOpen;
    const people = new Set();
    for (const c of res.creators || [])
      for (const p of personsOf(c)) { const n = personName(p); if (n) people.add(n); }
    const sm = el("summary", null,
      `Said vs owned: ${plural(shows.size, "show")}, ${plural(people.size, "person", "people")} × ${plural(rows.length, "player")}`);
    sm.title = "hue is what they said, a ring is what each person owns; a hatched row has no crawled squad yet; click a name for the squad, a cell for the quotes";
    det.appendChild(sm);
    const gridHost = el("div", "cx-gridhost");
    det.appendChild(gridHost);
    det.addEventListener("toggle", () => {
      gridOpen = det.open;
      if (det.open && !gridHost.childElementCount) renderGrid(rows, gridHost);
    });
    if (gridOpen) renderGrid(rows, gridHost);
    sec.appendChild(det);
    body.appendChild(sec);
  }
  function redrawGrid(rows, gridHost) {
    if (!gridHost.isConnected) return;
    gridHost.textContent = "";
    renderGrid(rows, gridHost);
  }

  /* -------------------------------------------------------- report cards
   *
   * One compact card per creator from `creator_report_card`. Three channels,
   * never one score: claims (binary, hit or flop, with a Wilson interval
   * drawn as a range), team (measured points, separate), numeric (MAE/RMSE,
   * empty today and said so in the drawer). Nothing here is a rank.
   */

  function renderReportCards() {
    const sec = el("div", "cx-sec");
    const h = el("div", "cx-sechead");
    const h3 = el("h3", null, "Report cards");
    h3.title = "ordered by claims scored, not by merit";
    h.appendChild(h3);
    if (rc) {
      const m = el("span", "cx-tiny");
      m.append(`${plural((rc.cards || []).length, "creator")} · floor ${rc.min_scored_claims} scored · read ${relAge(rc.as_of).text}`);
      m.title = "a record under the floor is drawn but never quotable as a rank";
      h.appendChild(m);
    }
    sec.appendChild(h);
    if (!rc && !rcErr) sec.appendChild(skeleton(1));
    else if (rcErr) sec.appendChild(errBox(rcErr));
    else {
      const cards = (rc.cards || []).slice().sort((a, b) =>
        (LEGACY.has(a.creator) - LEGACY.has(b.creator)) ||
        (b.claims.n_scored - a.claims.n_scored) || a.creator.localeCompare(b.creator));
      const shown = cards.filter(c => c.claims.measured || c.team.measured);
      const rest = cards.filter(c => !(c.claims.measured || c.team.measured));
      const grid = el("div", "cx-rcgrid");
      for (const c of shown) grid.appendChild(reportCard(c));
      sec.appendChild(grid);
      if (rest.length) {
        const d = el("details", "cx-disclose");
        d.appendChild(el("summary", null, `${plural(rest.length, "creator")} with nothing scored yet`));
        const list = el("div", "cx-thin");
        for (const c of rest) {
          const b = el("button", "cx-thinrow");
          b.appendChild(el("span", "cx-dot hollow"));
          b.appendChild(el("b", null, c.creator));
          b.appendChild(el("span", "sub", ` · ${plural(c.claims.n_total, "claim")}, none scoreable yet`));
          b.title = c.headline;
          b.onclick = () => openCard(c);
          list.appendChild(b);
        }
        d.appendChild(list);
        sec.appendChild(d);
      }
      const m = el("details", "cx-disclose");
      m.appendChild(el("summary", null, "Scoring method and gaps"));
      if (rc.note) m.appendChild(el("p", "sub", rc.note));
      if (rc.baseline && rc.baseline.label)
        m.appendChild(el("p", "sub", `Team baseline: ${rc.baseline.label}. ${rc.baseline.reason || ""}`));
      if ((rc.gaps || []).length) {
        const ul = el("ul", "cx-ul");
        for (const g of rc.gaps) {
          const li = el("li");
          li.appendChild(el("b", null, g.key));
          li.append(` ${g.what} `);
          li.appendChild(el("i", null, `Fix: ${g.fix}`));
          ul.appendChild(li);
        }
        m.appendChild(ul);
      }
      sec.appendChild(m);
    }
    body.appendChild(sec);
  }

  function reportCard(c) {
    // Not a creator, not a card: the owner asked for the pseudo-source
    // removed, not labelled. The honesty line already excludes it.
    if (LEGACY.has(c.creator)) return document.createComment("legacy pseudo-source omitted");
    const cl = c.claims, tm = c.team;
    const legacy = false;
    const card = el("button", "cx-rcard " + coin(cl.vs_coin_flip).cls + (legacy ? " legacy" : ""));
    const nm = el("div", "cx-rcname");
    nm.appendChild(el("b", null, c.creator));
    if (legacy) nm.appendChild(el("span", "cx-legacy", "legacy pseudo-source"));
    nm.appendChild(el("span", "cx-rccoin", cl.measured ? coin(cl.vs_coin_flip).word : "no scored claim"));
    card.appendChild(nm);
    if (cl.measured) {
      card.appendChild(rangeBar(cl));
      const st = el("div", "cx-rcstat");
      st.append(el("b", null, pct(cl.hit_rate)), ` · ${cl.hits} of ${cl.n_scored} scored`);
      if (cl.earned) st.append(` · weight ${Number(cl.weight).toFixed(2)}`);
      card.appendChild(st);
      if ((cl.by_gw || []).length) card.appendChild(gwBars(cl.by_gw));
    } else {
      card.appendChild(el("div", "cx-rcstat", `${plural(cl.n_total, "claim")}, none scored yet`));
    }
    if (tm.measured) card.appendChild(teamLine(tm));
    card.title = c.headline;
    card.onclick = () => openCard(c);
    return card;
  }

  /* The Wilson interval as a range on a 0-100% track with the coin flip
     ticked at 50. The point is the hit rate; the band is what the data can
     actually claim. */
  function rangeBar(cl, big) {
    const w = el("div", "cx-range" + (big ? " big" : ""));
    w.appendChild(el("span", "cx-range-mid"));
    const lo = cl.wilson_lo95 ?? 0, hi = cl.wilson_hi95 ?? 0;
    const band = el("span", "cx-range-band");
    band.style.left = `${(100 * lo).toFixed(1)}%`;
    band.style.width = `${(100 * Math.max(0, hi - lo)).toFixed(1)}%`;
    const pt = el("span", "cx-range-pt");
    pt.style.left = `${(100 * (cl.hit_rate ?? 0)).toFixed(1)}%`;
    w.append(band, pt);
    const label = `hit rate ${pct(cl.hit_rate)}, 95% interval ${pct(lo)} to ${pct(hi)}; the tick is a coin flip`;
    w.title = label;
    w.setAttribute("role", "img");
    w.setAttribute("aria-label", label);
    return w;
  }

  function gwBars(byGw) {
    const row = el("div", "cx-gwbars");
    for (const g of byGw) {
      const col = el("span", "cx-gwbar" + (g.quotable ? "" : " thin"));
      const f = el("span", "cx-gwfill");
      f.style.height = `${Math.max(1, Math.round(22 * (g.hit_rate || 0)))}px`;
      col.appendChild(f);
      col.appendChild(el("span", "cx-gwlab", `GW${g.gw}`));
      col.title = `GW${g.gw}: ${g.hits} of ${g.n} hit (${pct(g.hit_rate)})` +
        (g.quotable ? "" : "; under the floor");
      row.appendChild(col);
    }
    row.setAttribute("aria-label", "hit rate by gameweek");
    return row;
  }

  /* The team channel, drawn apart from the claims and never merged. */
  function teamLine(tm) {
    const d = el("div", "cx-rcteam");
    d.appendChild(el("span", "cx-rcchan", "team"));
    const ppl = (tm.people || []).filter(p => p.n_gw);
    d.append(ppl.map(p =>
      `${p.person}: ${p.points} pts / ${plural(p.n_gw, "GW")}, ${signed(p.mean_delta)} per GW vs cohort`)
      .join(" · ") || tm.reason);
    d.title = tm.reason || "";
    return d;
  }

  /* The full card, in the drawer. Three labelled channels. */
  function openCard(c) {
    try { chatterHandle?.cancel(); } catch { /* may be mid-build */ }
    chatterHandle = null;
    drawer.textContent = "";
    drawer.classList.add("open");
    drawer.scrollTop = 0;
    const legacy = LEGACY.has(c.creator);
    drawer.appendChild(drawerHead(c.creator,
      legacy ? "legacy pseudo-source: links pasted by hand, not a creator"
        : (c.people || []).length
          ? (c.people || []).map(p => p.person).join(", ")
          : "no verified FPL entry attached"));
    drawer.appendChild(el("p", "cx-why", c.headline));

    reportBody(c, drawer);
  }

  /* The three channels, drawn into `host`: the full card's body, and the
     same body folded under a person's drawer. */
  function reportBody(c, host) {
    const cl = c.claims;
    host.appendChild(el("h2", null, "Claims: binary, hit or flop"));
    if (cl.measured) {
      host.appendChild(rangeBar(cl, true));
      const facts = el("dl", "cx-facts");
      const fact = (k, v) => { facts.appendChild(el("dt", null, k)); facts.appendChild(el("dd", null, v)); };
      fact("hit rate", `${pct(cl.hit_rate)} (${cl.hits} of ${cl.n_scored} scored, ${cl.n_total} recorded)`);
      fact("95% interval", `${pct(cl.wilson_lo95)} to ${pct(cl.wilson_hi95)}`);
      fact("vs coin flip", coin(cl.vs_coin_flip).word);
      fact("weight", `${Number(cl.weight).toFixed(2)} · ${cl.earned ? "earned" : "not earned"} (floor ${cl.min_scored_claims})`);
      if (cl.first_claim_utc) fact("span", `${relAge(cl.first_claim_utc).text} to ${relAge(cl.last_claim_utc).text}`);
      host.appendChild(facts);
      if ((cl.by_gw || []).length) {
        host.appendChild(el("h2", null, "By gameweek"));
        host.appendChild(gwBars(cl.by_gw));
      }
      if ((cl.by_action || []).length) {
        host.appendChild(el("h2", null, "By action"));
        const t = el("table", "data cx-byaction");
        const hr = el("tr");
        for (const l of ["action", "n", "hit", "rate", "interval"]) hr.appendChild(el("th", l === "action" ? "" : "num", l));
        t.appendChild(hr);
        for (const a of cl.by_action) {
          const tr = el("tr" , a.quotable ? "" : "thin");
          tr.appendChild(el("td", null, a.action));
          tr.appendChild(el("td", "num", String(a.n)));
          tr.appendChild(el("td", "num", String(a.hits)));
          tr.appendChild(el("td", "num", pct(a.hit_rate)));
          tr.appendChild(el("td", "num", `${pct(a.wilson_lo95)}–${pct(a.wilson_hi95)}`));
          tr.title = a.quotable ? "" : "under the floor";
          t.appendChild(tr);
        }
        host.appendChild(t);
      }
      if (cl.reason) host.appendChild(el("p", "sub", cl.reason));
    } else {
      host.appendChild(el("p", "sub", cl.reason || "no scored claim"));
    }

    const tm = c.team;
    host.appendChild(el("h2", null, "Team: measured points, not opinion"));
    if (tm.measured) {
      for (const p of tm.people || []) {
        const box = el("div", "cx-teamrow");
        const t = el("div", "cx-teamhead");
        t.appendChild(el("b", null, p.person));
        if (p.entry_id != null) {
          const a = el("a", "cx-cross", `entry ${p.entry_id} ↗`);
          a.href = `https://fantasy.premierleague.com/entry/${p.entry_id}/history`;
          a.target = "_blank"; a.rel = "noopener noreferrer";
          t.appendChild(a);
        }
        box.appendChild(t);
        if (p.n_gw) {
          const facts = el("dl", "cx-facts");
          const fact = (k, v) => { facts.appendChild(el("dt", null, k)); facts.appendChild(el("dd", null, v)); };
          fact("points", `${p.points} over ${plural(p.n_gw, "GW")} (cohort ${p.baseline_points})`);
          fact("delta per GW", `${signed(p.mean_delta)}` +
            (p.delta_ci95 ? `, 95% ${signed(p.delta_ci95[0])} to ${signed(p.delta_ci95[1])}` : ""));
          fact("beats cohort", p.beats_baseline == null ? `under the ${tm.min_gw_measured}-GW floor` : p.beats_baseline ? "yes" : "no");
          if (p.latest_overall_rank != null) fact("overall rank", p.latest_overall_rank.toLocaleString());
          box.appendChild(facts);
          if ((p.gws || []).length) {
            const t2 = el("table", "data cx-byaction");
            const hr = el("tr");
            for (const l of ["GW", "pts", "cohort", "delta", "bench", "hits"]) hr.appendChild(el("th", l === "GW" ? "" : "num", l));
            t2.appendChild(hr);
            for (const g of p.gws) {
              const tr = el("tr");
              tr.appendChild(el("td", null, `GW${g.gw}`));
              tr.appendChild(el("td", "num", String(g.points)));
              tr.appendChild(el("td", "num", String(g.baseline_points)));
              tr.appendChild(el("td", "num", signed(g.delta)));
              tr.appendChild(el("td", "num", String(g.bench_points ?? "–")));
              tr.appendChild(el("td", "num", String(g.hit_cost ?? "–")));
              t2.appendChild(tr);
            }
            box.appendChild(t2);
          }
        }
        if (p.reason) box.appendChild(el("p", "sub", p.reason));
        host.appendChild(box);
      }
      if (tm.reason) host.appendChild(el("p", "sub", tm.reason));
    } else {
      host.appendChild(el("p", "sub", tm.reason || "no verified team"));
    }

    const nu = c.numeric;
    host.appendChild(el("h2", null, "Numeric: MAE and RMSE"));
    if (nu && nu.measured) {
      const facts = el("dl", "cx-facts");
      const fact = (k, v) => { facts.appendChild(el("dt", null, k)); facts.appendChild(el("dd", null, v)); };
      fact("MAE", `${nu.mae} (baseline ${nu.baseline_mae})`);
      fact("RMSE", `${nu.rmse} (baseline ${nu.baseline_rmse})`);
      fact("observations", `${nu.n_obs} over ${plural(nu.n_gw, "GW")}`);
      host.appendChild(facts);
    } else {
      host.appendChild(el("p", "sub", (nu && nu.reason) || "no numeric prediction published"));
    }
  }

  function renderGrid(rows, gridHost) {
    if (!rows.length) {
      gridHost.appendChild(emptyBox("Nobody has named a player in this window."));
      return;
    }
    /* The ring channel needs the squads, and the squads are one call per show.
       Fired here rather than on page load: it costs nothing until somebody
       actually asks for this view, and the grid redraws when they land. */
    loadSquads(() => redrawGrid(rows, gridHost));

    /* SAID: creator (show) -> code -> {in, out, cap, cueOnly} */
    const said = new Map();
    const touch = new Map();     // code -> Set(creator)
    const put = (who, code, k, cueOnly) => {
      if (!said.has(who)) said.set(who, new Map());
      const m = said.get(who);
      const cell = m.get(code) || { in: false, out: false, cap: false, cueOnly: true };
      cell[k] = true;
      cell.cueOnly = cell.cueOnly && cueOnly;
      m.set(code, cell);
      if (!touch.has(code)) touch.set(code, new Set());
      touch.get(code).add(who);
    };
    for (const r of rows) {
      for (const who of r.buy.people)  put(who, r.code, "in",  r.buy.llm === 0);
      for (const who of r.sell.people) put(who, r.code, "out", r.sell.llm === 0);
      for (const who of r.cap.people)  put(who, r.code, "cap", r.cap.llm === 0);
    }
    /* watch calls are a third, neutral state — never a buy */
    const nameByCode = new Map(rows.map(r => [r.code, dn(r)]));
    for (const c of res.creators || [])
      for (const w of (c.take && c.take.watching) || []) {
        if (w.code == null) continue;
        /* the resolver's canonical name, so "Martin Odegaard" (a raw watch
           string) and "Ødegaard" (the board row) become ONE column label —
           they already share a code, the label must not split them */
        const wName = w.disambiguator || w.display_name || w.name;
        if (wName && !nameByCode.has(w.code)) nameByCode.set(w.code, wName);
        if (!said.has(c.creator)) said.set(c.creator, new Map());
        const m = said.get(c.creator);
        const cell = m.get(w.code) || { in: false, out: false, cap: false, cueOnly: false };
        cell.watch = true; m.set(w.code, cell);
        if (!touch.has(w.code)) touch.set(w.code, new Set());
        touch.get(w.code).add(c.creator);
      }

    /* OWN: person -> Set(code), from whatever the panel actually publishes */
    const own = new Map();
    /* WHOLE SQUAD vs SEEN ON THIS BOARD. `panel_owned` names who holds each
       player ON the board, so a set built from it is bounded by the columns —
       it is NOT that person's squad size, and printing "8 owned" beside a
       full 15 would silently compare two different quantities. `ownFull`
       marks the sets that really are a complete locked 15. */
    const ownFull = new Set();
    const ownSources = new Set();
    const byCreator = new Map((res.creators || []).map(c => [c.creator, c]));
    for (const r of rows) {
      const po = r.panel_owned;
      if (po && Array.isArray(po.people)) {
        ownSources.add("panel_owned");
        for (const p of po.people) {
          if (!own.has(p)) own.set(p, new Set());
          own.get(p).add(r.code);
        }
      }
    }
    for (const c of res.creators || [])
      for (const p of (c.entry && c.entry.people) || c.people || []) {
        const codes = p.owned || p.squad;
        if (Array.isArray(codes) && codes.length) {
          ownSources.add("entry.people[].owned");
          const key = p.display_name || p.name;
          if (!own.has(key)) own.set(key, new Set());
          for (const x of codes) own.get(key).add(typeof x === "object" ? x.code : x);
        }
      }
    /* THE THIRD SOURCE, AND THE ONE THAT IS ACTUALLY POPULATED TODAY.
       `creator_detail.squad` is a person's locked 15. It is attached ONLY to a
       show with exactly one host, because that is the only case where the
       show's entry id and a person are the same thing — a four-host show's
       `entry_id` is null and there is no team to hang on it. */
    for (const [creator, s] of squadByCreator) {
      if (!s || !Array.isArray(s.squad) || !s.squad.length) continue;
      const people = personsOf(byCreator.get(creator));
      if (people.length !== 1) continue;
      const nm = personName(people[0]) || creator;
      ownSources.add("creator_detail.squad");
      if (!own.has(nm)) own.set(nm, new Set());
      for (const p of s.squad) own.get(nm).add(p.code);
      ownFull.add(nm);
    }
    const ownSource = [...ownSources].join(" + ") || null;

    /* ROWS, banded. A show is not a person. EVERY panel show gets its rows,
       said or not, so a person whose squad lands later has a row waiting;
       empty rows fold by default and are counted, never dropped. */
    const bands = [];
    const said1 = [...new Set([...(res.creators || []).map(c => c.creator), ...said.keys()])];
    const solo = [], showBand = [];
    /* the ring channel's tri-state per row: read (a ring or its absence is a
       fact), unread (no crawled squad yet, drawn hatched), or n/a for a show */
    const squadKnown = nm => !!nm && (ownFull.has(nm) || panelSquadWord(nm).known === true);
    for (const who of said1) {
      const c = byCreator.get(who);
      const people = personsOf(c);
      if (people.length === 1) {
        const p = people[0];
        // `person` is the curated display name and is the SAME namespace
        // panel_owned.people uses. `display_name` does not exist on this
        // object, so this fell through to `name` -- the FPL API's account
        // name -- and only 3 of 7 people matched: the cards said "Andy owns
        // him" while the grid said Andy's squad was never crawled, on one
        // page. Join on the key both sides actually share.
        const nm = personName(p) || who;
        /* the show under the person's name, EXCEPT where they are the same
           string — "FPL Raptor / FPL Raptor" says nothing twice */
        solo.push({ key: who, label: nm, sub: nm === who ? null : who, kind: "person",
                    ownKey: nm, show: who, person: p, people,
                    note: "sole host" });
      } else if (people.length > 1) {
        showBand.push({ key: who, label: who, sub: `${people.length} hosts`,
                        kind: "show", ownKey: null, show: who, people,
                        note: "said by the show, not a host" });
        for (const p of people) {
          const nm = personName(p);
          showBand.push({ key: `__own__${nm}`, label: nm, sub: who, kind: "own-only",
                          ownKey: nm, show: who, person: p, people,
                          note: "his squad; the show's claims are not his" });
        }
      } else {
        showBand.push({ key: who, label: who, sub: null, kind: "show", ownKey: null,
                        show: who, people: [],
                        note: "no panel person is published for this show" });
      }
    }
    for (const r of [...solo, ...showBand]) r.read = r.ownKey ? squadKnown(r.ownKey) : null;
    /* Band titles are one-liners; the sentence they shared moves to ONE
       footnote under the grid instead of shouting per group (R3). */
    if (solo.length) bands.push({
      title: "One host", rows: solo });
    if (showBand.length) bands.push({
      title: "Said by the show", foot: true, rows: showBand });
    /* A note repeated on every row of a band is clutter, not honesty. Say it
       once in the band header and keep only the rows that differ. */
    for (const b of bands) {
      const counts = new Map();
      for (const r of b.rows) counts.set(r.note, (counts.get(r.note) || 0) + 1);
      const [common, n] = [...counts.entries()].sort((x, y) => y[1] - x[1])[0] || [];
      // Lift a note into the band header ONLY when it is true of every row in
      // the band. Lifting the merely most-common one stated "no panel person
      // is published for this show" across a band that included the FPL
      // Wire's four named hosts -- a header speaks for the whole band, so a
      // note that is false for even one row must stay on the rows it fits.
      // Deduplicating clutter is not worth asserting something untrue.
      if (common && n === b.rows.length && n > 1) {
        b.common = common;
        for (const r of b.rows) r.note = null;
      }
    }

    /* COLUMNS */
    const cname = code => nameByCode.get(code) || `player ${code}`;
    let codes = [...touch.entries()]
      .sort((a, b) => b[1].size - a[1].size ||
        cname(a[0]).localeCompare(cname(b[0])))
      .map(e => e[0]);
    const TOTAL = codes.length;
    if (!gridAll) codes = codes.slice(0, 22);
    const rowByCode = new Map(rows.map(r => [r.code, r]));

    /* one line: what hue and ring mean, and whether any panel squad is here */
    const lead = el("p", "cx-provline" + (own.size ? "" : " warn"));
    lead.appendChild(el("b", null, "Hue is what they said. A ring is what they own."));
    if (own.size) lead.title = `own-channel sources: ${ownSource}`;
    else if (squadsPending > 0) lead.append(" Reading squads…");
    else {
      const sq = res.panel_squads || {};
      lead.append(" No panel squad in this payload" +
        (sq.known != null && sq.with_entry != null
          ? ` (${sq.known} of ${sq.with_entry} verified entries crawled)` : "") +
        "; only your row carries rings.");
    }
    gridHost.appendChild(lead);

    /* coverage, from the board's own ledger: who has a crawled squad */
    const ps = res.panel_squads || null;
    if (ps && ps.with_entry != null) {
      const cover = el("p", "cx-tiny");
      cover.append(`${ps.known ?? 0} of ${plural(ps.with_entry, "verified person", "verified people")} ` +
        `have a crawled squad${ps.gw != null ? ` (GW${ps.gw})` : ""}; the rest are drawn hatched, ` +
        `no crawled squad yet, never as owning nothing. Source creator_board.panel_squads, read ${relAge(res.as_of).text}.`);
      cover.title = ps.reason || "";
      gridHost.appendChild(cover);
    }

    const wrap = el("div", "scroll-x");
    const t = el("table", "cx-grid");
    const thead = el("thead"); const hr = el("tr");
    hr.appendChild(el("th", "cx-gutter", ""));
    for (const code of codes) {
      const th = el("th", "cx-colh");
      th.appendChild(el("span", "cx-colname", cname(code)));
      th.title = `${cname(code)} — ` +
        `${plural(touch.get(code).size, "person", "people")} touched him` +
        (rowByCode.has(code) ? "" : "\nonly a watch call; he is on nobody's buy or sell list");
      hr.appendChild(th);
    }
    thead.appendChild(hr); t.appendChild(thead);
    const tb = el("tbody");

    /* Empty rows collapse by default: half the matrix was whitespace whose
       only content was "nothing said, nothing ringed here" (R3). A hidden
       row is COUNTED and recoverable — never silently dropped. */
    const rowEmpty = R => {
      const m = said.get(R.key);
      const o = R.ownKey ? own.get(R.ownKey) : null;
      return !codes.some(code => (m && m.has(code)) || (o && o.has(code)));
    };
    let hiddenEmpty = 0;
    for (const band of bands) {
      const visible = gridEmptyShown ? band.rows
                                     : band.rows.filter(R => !rowEmpty(R));
      hiddenEmpty += band.rows.length - visible.length;
      if (!visible.length) continue;
      const btr = el("tr", "cx-bandrow");
      const btd = el("td", "cx-band");
      btd.colSpan = codes.length + 1;
      btd.appendChild(el("span", "cx-bandt", band.title));
      if (band.common) btd.appendChild(el("span", "cx-bandnote", band.common));
      btr.appendChild(btd); tb.appendChild(btr);
      for (const R of visible) tb.appendChild(gridRow(R));
    }
    /* your row, pinned, under a rule */
    if (squadReady) {
      const btr = el("tr", "cx-bandrow");
      const btd = el("td", "cx-band");
      btd.colSpan = codes.length + 1;
      btd.textContent = "You";
      btr.appendChild(btd); tb.appendChild(btr);
      const tr = el("tr", "cx-you");
      const g = el("td", "cx-gutter");
      g.appendChild(el("b", null, squad.team_name || "your squad"));
      g.appendChild(el("div", "cx-tiny",
        `${squad.gw != null ? `GW${squad.gw}` : "current"} · ${squad.provenance_source || "source not stated"}`));
      tr.appendChild(g);
      for (const code of codes) {
        const td = el("td");
        const lane = laneOf.get(code);
        if (lane) {
          const cell = el("span", "cx-cell own-" + lane);
          cell.title = `${cname(code)} — ${laneLabel(lane).toLowerCase()}`;
          td.appendChild(cell);
        }
        tr.appendChild(td);
      }
      tb.appendChild(tr);
    }
    t.appendChild(tb); wrap.appendChild(t);
    gridHost.appendChild(wrap);

    function gridRow(R) {
      const unread = !!R.ownKey && !R.read;
      const tr = el("tr", "cx-gridrow " + R.kind + (unread ? " unread" : ""));
      const g = el("td", "cx-gutter");
      /* THE ROW HEAD IS THE WAY IN TO THE PERSON: squad, transfers, record.
         A show head opens its hosts instead of pretending to have a squad. */
      const nameBtn = el("button", "cx-gutname" + (R.kind === "show" ? " show" : ""));
      nameBtn.appendChild(el("b", null, R.label));
      if (R.sub) nameBtn.appendChild(el("span", "cx-gutsub", R.sub));
      const owned = R.ownKey ? (own.get(R.ownKey) || (R.read ? new Set() : null)) : null;
      const full = !!R.ownKey && ownFull.has(R.ownKey);
      nameBtn.title = R.kind === "show"
        ? `${R.label}: a show, not a person. Open for its hosts and their squads.`
        : `${R.label}: open the squad, transfers and record`;
      nameBtn.onclick = () => openPerson(R);
      g.appendChild(nameBtn);
      const tags = el("div", "cx-guttags");
      const asOf = relAge(res.as_of).text;
      if (R.ownKey) {
        if (unread) {
          const w = panelSquadWord(R.ownKey);
          const noEntry = ((res.panel_squads || {}).no_entry_people || []).includes(R.ownKey);
          const s = el("span", "cx-gutown unread", noEntry ? "no verified entry id" : "no crawled squad yet");
          s.title = `${w.text}; no ring can be drawn on this row. Source creator_board.panel_squads, read ${asOf}`;
          tags.appendChild(s);
        } else {
          const s = el("span", "cx-gutown" + (full ? "" : " part"),
            full ? `locked ${owned.size}` : `holds ${owned.size} of these`);
          s.title = full
            ? `the whole locked squad was read; the rings are the members of it in these columns. Source creator_detail.squad, read ${asOf}`
            : `the board's ownership field names this person on ${plural(owned.size, "player")} in these columns; ` +
              `the rest of the squad is not served here. Source creator_board.panel_owned, read ${asOf}`;
          tags.appendChild(s);
        }
      }
      if (R.kind !== "own-only") { const tg = rcTag(R.show); if (tg) tags.appendChild(tg); }
      if (tags.childElementCount) g.appendChild(tags);
      if (R.note) g.appendChild(el("div", "cx-tiny", R.note));
      tr.appendChild(g);
      const m = said.get(R.key) || new Map();
      for (const code of codes) {
        const td = el("td", unread ? "unread" : "");
        const cell = m.get(code);
        const owns = !!(owned && owned.has(code));
        if (!cell && !owns) {
          if (unread) td.title = `${R.label} · ${cname(code)}\nno crawled squad yet; unread is not "does not own"`;
          tr.appendChild(td); continue;
        }
        const cls = ["cx-cell"];
        if (cell) {
          if (cell.in && cell.out) cls.push("split");
          else if (cell.in || cell.cap) cls.push("in");
          else if (cell.out) cls.push("out");
          else if (cell.watch) cls.push("watch");
          if (cell.cueOnly && !cell.watch) cls.push("cue");
        }
        if (owns) cls.push("owns");
        else if (unread) cls.push("unread");
        const s = el("span", cls.join(" "));
        const r = rowByCode.get(code);
        const bits = [];  // the cell's own tooltip: said, owned, and neither
        if (cell) {
          if (cell.in) bits.push("said IN");
          if (cell.out) bits.push("said OUT");
          if (cell.cap) bits.push("named captain");
          if (cell.watch) bits.push("watch call: an observation, not a buy");
          if (cell.cueOnly && !cell.watch) bits.push("keyword window only");
        } else bits.push("never mentioned him");
        bits.push(owns ? "OWNS him"
          : unread ? "no crawled squad yet, so no ring can be drawn"
          : R.ownKey ? ((full || rowByCode.has(code)) ? "does not own him" : "ownership not published for this column")
          : "said by the show; ownership sits on the host rows");
        s.title = `${R.label} · ${cname(code)}\n${bits.join("\n")}`;
        s.tabIndex = 0;
        s.onclick = () => r && openPlayer(r);
        s.onkeydown = e => {
          if ((e.key === "Enter" || e.key === " ") && r) { e.preventDefault(); openPlayer(r); }
        };
        td.appendChild(s);
        tr.appendChild(td);
      }
      return tr;
    }

    /* legend + the expansion */
    const lg = el("div", "cx-legend");
    const it = (cls, text) => {
      const d = el("div", "cx-legitem");
      d.appendChild(el("span", "cx-cell " + cls));
      d.append(text); return d;
    };
    lg.appendChild(it("in", "said in"));
    lg.appendChild(it("out", "said out"));
    lg.appendChild(it("split", "both"));
    lg.appendChild(it("watch", "watch call"));
    lg.appendChild(it("cue", "keyword only"));
    lg.appendChild(it("owns", "owns"));
    lg.appendChild(it("in owns", "said and owns"));
    lg.appendChild(it("unread", "no crawled squad yet"));
    if (TOTAL > codes.length || gridAll) {
      const b = el("button", "cx-more",
        gridAll ? `show only the 22 most-touched players`
                : `show all ${TOTAL} players (${TOTAL - codes.length} more)`);
      b.onclick = () => { gridAll = !gridAll; redrawGrid(rows, gridHost); };
      lg.appendChild(b);
    }
    if (hiddenEmpty || gridEmptyShown) {
      const b = el("button", "cx-more",
        gridEmptyShown
          ? "hide the empty rows again"
          : `show ${plural(hiddenEmpty, "empty row")}: nothing said, no ` +
            `ring among these columns`);
      b.onclick = () => { gridEmptyShown = !gridEmptyShown; redrawGrid(rows, gridHost); };
      lg.appendChild(b);
    }
    gridHost.appendChild(lg);
  }
  /* ================================================= the evidence drawer */

  function detailFor(creator) {
    if (!detailCache.has(creator)) {
      detailCache.set(creator,
        runPanel("creator_detail", { creator })
          .then(r => r.result)
          .catch(e => ({ __error: String(e.message || e) })));
    }
    return detailCache.get(creator);
  }

  /* Ask every show for its squad, once. The answer is `creator_detail.squad`
     — the locked 15 with a multiplier and a captain flag — plus the panel's
     own `squad_reason` for the shows that have none. Nothing is inferred
     from an empty list: a show with no verified entry id HAS no squad to
     serve, and its reason says so in the panel's words. */
  function loadSquads(after) {
    /* `after` is the COMPLETION EDGE, not a "call me back whatever the state".
       Firing it on an already-finished load re-entered renderGrid from inside
       renderGrid and blew the stack; a second caller needs nothing, because
       the first one's callback is what redraws. */
    if (squadsAsked) return;
    squadsAsked = true;
    const creators = (res.creators || []).map(c => c.creator);
    squadsPending = creators.length;
    if (!squadsPending) { after && after(); return; }
    for (const creator of creators) {
      detailFor(creator).then(d => {
        if (d && !d.__error) {
          squadByCreator.set(creator, {
            squad: Array.isArray(d.squad) ? d.squad : [],
            reason: d.squad_reason || null,
            entry: d.entry || null,
            window_days: d.window_days ?? null,
          });
        } else {
          squadByCreator.set(creator, {
            squad: [], reason: null,
            error: (d && d.__error) || "creator_detail could not be read",
          });
        }
      }).finally(() => {
        squadsPending--;
        if (squadsPending === 0 && after) after();
      });
    }
  }

  /* What the panel itself says about a named person's squad, verbatim where
     it can. `panel_squads` names who has no stored picks; anybody verified
     and NOT on that list is counted in its `known` total. */
  function panelSquadWord(name) {
    const ps = res.panel_squads || {};
    if ((ps.no_entry_people || []).includes(name))
      return { known: false, text: "the panel publishes no verified entry id for him" };
    if ((ps.unknown_people || []).includes(name))
      return { known: false, text: "the panel's squad ledger lists him under the people no picks are stored for" };
    if (ps.known != null)
      return { known: true, text:
        `the panel's squad ledger counts him inside its ${ps.known} crawled ` +
        `squad${ps.known === 1 ? "" : "s"}` + (ps.gw != null ? ` (GW${ps.gw})` : "") };
    return { known: null, text: "the payload does not say whether his squad has been crawled" };
  }

  function quoteBlock(text, claim, item) {
    const q = el("blockquote", "cx-quote");
    q.appendChild(el("span", "cx-qmark", "“"));
    q.append(text);
    q.appendChild(el("span", "cx-qmark", "”"));
    const foot2 = el("div", "cx-qfoot");
    const tr = tier(claim && claim.extractor);
    const badge = el("span", "cx-tier " + tr.key, tr.label);
    badge.title = tr.note + (tr.model ? ` (${tr.model})` : "");
    foot2.appendChild(badge);
    if (claim && claim.conviction)
      foot2.appendChild(el("span", "cx-conv", `${claim.conviction} conviction`));
    else if (claim && claim.confidence != null)
      foot2.appendChild(el("span", "cx-conv",
        `confidence ${Number(claim.confidence).toFixed(2)}`));
    if (claim && claim.gameweek != null)
      foot2.appendChild(el("span", "cx-conv", `for GW${claim.gameweek}`));
    /* the link to the source, always, with the right verb on it */
    const href = (claim && claim.deep_link) || (item && item.url);
    if (href) {
      const k = linkKind(item ? item.url : href, item && item.url_basis);
      const a = el("a", "cx-src-link");
      a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
      const ts = clock(claim && claim.start_s);
      a.textContent = ts ? `${k.label} at ${ts}` : k.label;
      a.title = [item && item.title, k.why,
                 claim && claim.start_s == null
                   ? "no timestamp on this claim — the link opens at the start"
                   : null].filter(Boolean).join("\n");
      foot2.appendChild(a);
    } else {
      foot2.appendChild(el("span", "cx-tiny", "no link on this claim"));
    }
    q.appendChild(foot2);
    return q;
  }

  /* ------------------------------------------- a PERSON, and their team */
  /* The question this view exists to answer is "what do they own that they
     never talk about", and until now clicking a person opened a PLAYER. This
     opens the person: their locked 15 from `creator_detail.squad`, the
     captain inside it, and the holdings no claim in the record ever names. */

  const POS_ORDER = { GKP: 0, DEF: 1, MID: 2, FWD: 3 };

  function drawerHead(title, sub) {
    const head = el("div", "dhead");
    const id = el("div");
    id.appendChild(el("div", "dname", title));
    if (sub) id.appendChild(el("div", "sub", sub));
    head.appendChild(id);
    const close = el("button", null, "✕");
    close.onclick = closeDrawer;
    head.appendChild(close);
    return head;
  }

  /* A creator chip opens the PERSON, not the arithmetic. One drawer for the
     matrix's row head and for every creator chip on the page: the record in
     one line, the squad, the transfers, and how the record was computed
     behind a fold at the bottom. */
  function openCreator(name) {
    const people = personsOf(byCreatorOf(name));
    if (people.length === 1) {
      const nm = personName(people[0]) || name;
      return openPerson({ key: name, label: nm, sub: nm === name ? null : name,
                          kind: "person", ownKey: nm, show: name,
                          person: people[0], people });
    }
    return openPerson({ key: name, label: name, kind: "show", ownKey: null,
                        show: name, people });
  }

  /* One line: the measured record with its verdict and colour, then the team
     channel where one is measured. Reference material, never a rank. */
  function recordLine(show) {
    const line = el("div", "cx-recline");
    const card = rcByCreator.get(show);
    if (!card) {
      line.appendChild(el("span", "cx-tiny",
        rc ? "no report card for this creator" : rcErr ? `record unavailable: ${rcErr}` : "measuring the record…"));
      return line;
    }
    const cl = card.claims;
    const v = verdict(cl, rc.min_scored_claims);
    const tag = el("span", "cx-rctag big " + v.cls,
      cl.measured ? `${pct(cl.hit_rate)} · ${cl.hits} of ${cl.n_scored} scored · ${v.word}`
                  : `${plural(cl.n_total, "claim")}, none scored yet`);
    tag.title = cl.reason || card.headline || "";
    line.appendChild(tag);
    for (const p of ((card.team || {}).people || []).filter(p => p.n_gw)) {
      const t = el("span", "cx-recteam",
        `${p.person}: ${p.points} pts over ${plural(p.n_gw, "GW")}, ` +
        `${signed(p.mean_delta)} per GW vs cohort` +
        (p.latest_overall_rank != null ? `, rank ${p.latest_overall_rank.toLocaleString()}` : ""));
      t.title = p.reason || card.team.reason || "";
      line.appendChild(t);
    }
    line.appendChild(el("span", "cx-tiny",
      `source creator_report_card, read ${relAge(rc.as_of).text}`));
    return line;
  }

  /* The public transfers behind a squad, or the panel's reason there are none. */
  function transfersSection(d) {
    const sec = el("div");
    sec.appendChild(el("h2", null, "Transfers"));
    const list = Array.isArray(d.transfers) ? d.transfers : [];
    if (!list.length) {
      sec.appendChild(el("p", "sub",
        (d.transfers_reason || "the payload carries no transfers and gives no reason.") +
        ` Source creator_detail, read ${relAge(d.as_of).text}.`));
      return sec;
    }
    const ul = el("div", "cx-quietlist");
    for (const t of list) {
      const row = el("div", "cx-thinrow static");
      row.appendChild(el("span", "cx-tiny", `GW${t.gw ?? "?"}`));
      row.appendChild(el("b", null, t.in_name || (t.in_code != null ? `player ${t.in_code}` : "?")));
      row.append(" in for ");
      row.appendChild(el("b", null, t.out_name || (t.out_code != null ? `player ${t.out_code}` : "?")));
      if (t.time_utc) row.appendChild(el("span", "sub", ` · ${relAge(t.time_utc).text}`));
      ul.appendChild(row);
    }
    sec.appendChild(ul);
    sec.appendChild(el("p", "sub",
      `${plural(list.length, "transfer")} on record. Source creator_detail, read ${relAge(d.as_of).text}.`));
    return sec;
  }

  /* The arithmetic, folded: interval, by action, by gameweek, the team and
     numeric channels, exactly as the full card draws them. */
  function methodFold(show) {
    const card = rcByCreator.get(show);
    if (!card) return null;
    const det = el("details", "cx-disclose cx-method");
    det.appendChild(el("summary", null,
      "How the record is measured: interval, by action, by gameweek"));
    const inner = el("div", "cx-methodbody");
    inner.appendChild(el("p", "cx-why", card.headline));
    reportBody(card, inner);
    det.appendChild(inner);
    return det;
  }

  /* The board's ownership field, sliced to one person: the players ON THIS
     BOARD they hold. Bounded by the columns, so never a squad size. */
  function heldOnBoard(name) {
    return (res.consensus || []).filter(c =>
      ((c.panel_owned || {}).people || []).includes(name));
  }

  async function openPerson(R) {
    try { chatterHandle?.cancel(); } catch { /* may be mid-build */ }
    chatterHandle = null;
    drawer.textContent = "";
    drawer.classList.add("open");
    drawer.scrollTop = 0;

    const people = R.people || [];
    drawer.appendChild(drawerHead(R.label,
      R.kind === "show"
        ? `a show${people.length ? `, ${plural(people.length, "host")}` : ""}`
        : [R.show && R.show !== R.label ? R.show : null,
           R.person && R.person.entry_id != null ? `entry ${R.person.entry_id}` : null]
            .filter(Boolean).join(" · ") || "a person on the panel"));
    drawer.appendChild(recordLine(R.show));

    const ps = res.panel_squads || {};
    const asOf = relAge(res.as_of).text;
    const nBoard = (res.consensus || []).length;

    /* A SHOW IS NOT A PERSON: no squad to open. Each host gets one line with
       what the board knows they hold, and the panel's reason where nothing
       has been crawled. */
    if (R.kind === "show") {
      drawer.appendChild(el("h2", null, "Squads, one per host"));
      drawer.appendChild(el("p", "sub", people.length > 1
        ? `${R.label} has ${plural(people.length, "host")} and no entry id of its own: its claims are the show's, and each squad below is a host's.`
        : "No panel person is published for this show, so there is nobody whose squad this could be."));
      if (people.length) {
        const list = el("div", "cx-people-list");
        for (const p2 of people) {
          const nm = personName(p2);
          const row = el("div", "cx-personrow");
          const t = el("div", "cx-personname");
          t.appendChild(el("b", null, nm || "unnamed"));
          if (p2.entry_id != null) {
            const a = el("a", "cx-cross", `entry ${p2.entry_id} ↗`);
            a.href = p2.source_url || `https://fantasy.premierleague.com/api/entry/${p2.entry_id}/`;
            a.target = "_blank"; a.rel = "noopener noreferrer";
            t.appendChild(a);
          }
          const w = panelSquadWord(nm);
          const tag = el("span", "cx-gutown" + (w.known ? "" : " unread"),
            w.known ? `crawled${ps.gw != null ? ` GW${ps.gw}` : ""}` : "no crawled squad yet");
          tag.title = `${w.text}; source creator_board.panel_squads, read ${asOf}`;
          t.appendChild(tag);
          row.appendChild(t);
          const held = nm ? heldOnBoard(nm) : [];
          const note = el("div", "cx-tiny");
          if (held.length) {
            note.append(`holds ${held.length} of the ${plural(nBoard, "player")} on this board: `);
            held.forEach((c, i) => {
              const b = el("button", "cx-linkish", c.name);
              b.onclick = () => openPlayerByCode(c.code, c.name);
              note.appendChild(b);
              if (i < held.length - 1) note.append(", ");
            });
          } else note.append(w.known ? "holds none of the players on this board." : `${w.text}.`);
          row.appendChild(note);
          list.appendChild(row);
        }
        drawer.appendChild(list);
      }
      const sq = squadByCreator.get(R.show);
      if (sq && sq.reason) drawer.appendChild(el("p", "sub", sq.reason));
      const fold = methodFold(R.show);
      if (fold) drawer.appendChild(fold);
      return;
    }

    const load = el("div");
    load.appendChild(el("p", "cx-loading", "reading the squad…"));
    load.appendChild(skeleton(1));
    drawer.appendChild(load);
    const d = await detailFor(R.show);
    if (!drawer.classList.contains("open")) return;
    load.remove();
    if (!d || d.__error) {
      drawer.appendChild(errBox(new Error((d && d.__error) || "creator_detail could not be read")));
      return;
    }

    const squad = Array.isArray(d.squad) ? d.squad : [];
    const solo = personsOf(byCreatorOf(R.show)).length === 1;

    /* every claim this record holds for this source, across its whole window */
    const mentioned = new Map();       // code -> [claim]
    for (const item of d.items || [])
      for (const c of item.claims || []) {
        if (c.code == null) continue;
        if (!mentioned.has(c.code)) mentioned.set(c.code, []);
        mentioned.get(c.code).push({ ...c, item });
      }

    if (squad.length && solo) {
      const capName = (squad.find(p => p.is_captain) || {}).name;
      const benched = squad.filter(p => p.multiplier === 0).length;
      drawer.appendChild(el("h2", null, `Squad, locked GW${d.squad_gw ?? "?"}`));
      const prov = el("p", "cx-provline");
      prov.appendChild(el("b", null, `${squad.length} picks`));
      prov.append((capName ? `, captain ${capName}` : "") +
        (benched ? `, ${benched} benched` : ", bench not marked in this payload") +
        `. Source creator_detail, read ${relAge(d.as_of).text}.`);
      prov.title = d.squad_reason || "";
      drawer.appendChild(prov);

      const grid = el("div", "cx-sq");
      const ordered = squad.slice().sort((a, b) =>
        (POS_ORDER[a.pos] ?? 9) - (POS_ORDER[b.pos] ?? 9) ||
        (b.price ?? 0) - (a.price ?? 0) ||
        String(a.name).localeCompare(String(b.name)));
      for (const p of ordered) {
        const said = mentioned.get(p.code) || [];
        const cell = el("button", "cx-sqcard" + (said.length ? " said" : " quiet") +
                                  (p.multiplier === 0 ? " bench" : ""));
        cell.appendChild(faceImg(p.code, "cx-sqface"));
        const nm = el("div", "cx-sqname");
        if (p.is_captain) nm.appendChild(el("span", "cx-sqcap", "★"));
        nm.append(p.name);
        cell.appendChild(nm);
        cell.appendChild(el("div", "cx-sqmeta",
          [p.pos, p.price != null ? fmtPrice(p.price) : null,
           p.multiplier != null ? `×${p.multiplier}` : null].filter(Boolean).join(" · ")));
        cell.appendChild(el("div", "cx-sqsay",
          said.length ? `${plural(said.length, "claim")}` : "never mentioned"));
        if (laneOf.has(p.code))
          cell.appendChild(el("span", "cx-sqyours", laneLabel(laneOf.get(p.code))));
        cell.title = `${p.name}${p.is_captain ? ", his captain" : ""}\n` +
          (said.length
            ? said.map(c => `${c.action || "claim"}${c.gameweek != null ? ` · GW${c.gameweek}` : ""}`).join("\n")
            : `no claim in ${R.show}'s record names him`) +
          (laneOf.has(p.code) ? `\nyou: ${laneLabel(laneOf.get(p.code)).toLowerCase()}` : "");
        cell.onclick = () => openPlayerByCode(p.code, p.name);
        grid.appendChild(cell);
      }
      drawer.appendChild(grid);

      const quiet = ordered.filter(p => !mentioned.has(p.code));
      drawer.appendChild(el("h2", null, "Quiet holdings: owns him, never mentions him"));
      drawer.appendChild(el("p", "sub", quiet.length
        ? `${quiet.length} of ${squad.length}, against every claim in ${R.show}'s record` +
          (d.window_days != null ? ` (${plural(d.window_days, "day")})` : "") + "."
        : `None: every one of the ${squad.length} has been named at least once.`));
      if (quiet.length) {
        const ql = el("div", "cx-quietlist");
        for (const p of quiet) {
          const b = el("button", "cx-thinrow");
          b.appendChild(el("span", "cx-dot hollow"));
          b.appendChild(el("b", null, p.name));
          b.appendChild(el("span", "sub",
            ` ${p.pos || ""} ${p.price != null ? fmtPrice(p.price) : ""}` +
            (p.is_captain ? " · his captain" : "") +
            (laneOf.has(p.code) ? ` · ${laneLabel(laneOf.get(p.code)).toLowerCase()}` : "")));
          b.onclick = () => openPlayerByCode(p.code, p.name);
          ql.appendChild(b);
        }
        drawer.appendChild(ql);
      }

      const codes = new Set(squad.map(p => p.code));
      const talkedNotOwned = [...mentioned.entries()].filter(([c]) => !codes.has(c));
      drawer.appendChild(el("h2", null, "Talked about, does not own"));
      drawer.appendChild(el("p", "sub", talkedNotOwned.length
        ? `${plural(talkedNotOwned.length, "player")} named and not picked; a squad holds fifteen and a show discusses more.`
        : "Nobody: every player named is in the fifteen."));
      if (talkedNotOwned.length) {
        const tl = el("div", "cx-quietlist");
        for (const [code, cl] of talkedNotOwned.sort((a, b) => b[1].length - a[1].length)) {
          const nm = cl[0].name || `player ${code}`;
          const b = el("button", "cx-thinrow");
          b.appendChild(el("span", "cx-dot solid"));
          b.appendChild(el("b", null, nm));
          b.appendChild(el("span", "sub",
            ` ${plural(cl.length, "claim")} · ` +
            [...new Set(cl.map(c => c.action).filter(Boolean))].join(", ")));
          b.onclick = () => openPlayerByCode(code, nm);
          tl.appendChild(b);
        }
        drawer.appendChild(tl);
      }
    } else {
      /* NO SQUAD TO DRAW: one line, the payload's reason, never a blank. A
         crawled squad on a multi-host show is a different sentence from an
         uncrawled one, and the two must not share it. */
      const w = panelSquadWord(R.label);
      drawer.appendChild(el("h2", null, "Squad"));
      const p = el("p", "cx-provline" + (w.known && !solo ? "" : " warn"));
      if (w.known && !solo) {
        p.appendChild(el("b", null,
          `${R.label}'s squad is crawled${ps.gw != null ? ` (GW${ps.gw})` : ""} but not served here.`));
        p.append(` Squads come one per show and ${R.show} has ${plural(people.length, "host")}; ` +
          `the slice below is what the board's ownership field names. Source creator_board.panel_squads, read ${asOf}.`);
      } else {
        const why = solo ? (d.squad_reason || w.text) : w.text;
        p.appendChild(el("b", null, `No crawled squad for ${R.label} yet.`));
        p.append(` ${why}. Source ${solo ? "creator_detail.squad_reason" : "creator_board.panel_squads"}, read ${asOf}.`);
      }
      p.title = ps.reason || "";
      drawer.appendChild(p);

      const held = heldOnBoard(R.label);
      if (held.length) {
        drawer.appendChild(el("h2", null, "What the board does know he holds"));
        drawer.appendChild(el("p", "sub",
          `${held.length} of the ${plural(nBoard, "player")} on this board, from its ownership field; a slice, never the whole fifteen.`));
        const hl = el("div", "cx-quietlist");
        for (const c of held) {
          const said = mentioned.get(c.code) || [];
          const b = el("button", "cx-thinrow");
          b.appendChild(el("span", "cx-dot " + (said.length ? "solid" : "hollow")));
          b.appendChild(el("b", null, c.name));
          b.appendChild(el("span", "sub",
            ` ${c.pos || ""} ${c.price != null ? fmtPrice(c.price) : ""} · ` +
            (said.length ? `${plural(said.length, "claim")} on ${R.show}` : `never named on ${R.show}`)));
          b.onclick = () => openPlayerByCode(c.code, c.name);
          hl.appendChild(b);
        }
        drawer.appendChild(hl);
      }
    }

    drawer.appendChild(transfersSection(d));
    const fold = methodFold(R.show);
    if (fold) drawer.appendChild(fold);
  }

  const byCreatorOf = k => (res.creators || []).find(c => c.creator === k) || null;

  /* Reach a player from a squad card. The board row is the rich one; where a
     quiet holding is on nobody's buy or sell list there is no board row at
     all, so a minimal one is built from what the squad card knows and the
     drawer says which of the two it is. */
  function openPlayerByCode(code, name) {
    const r = buildRows().find(x => x.code === code);
    if (r) { openPlayer(r); return; }
    /* `buildRows` is filtered by the Evidence switch, so "not in it" is not
       the same as "nobody named him". A player the switch dropped IS on the
       board — read him from the raw consensus with the filter off rather than
       telling the reader he was never mentioned. */
    const raw = (res.consensus || []).find(c => c.code === code);
    if (raw) {
      const was = consideredOnly;
      consideredOnly = false;
      const r2 = buildRows().find(x => x.code === code);
      consideredOnly = was;
      if (r2) { openPlayer(r2); return; }
    }
    openPlayer({
      code, name, pos: null, team: null, price: null, own_pct: null,
      buy: grp(null), sell: grp(null), cap: grp(null),
      nBuy: 0, nSell: 0, nCap: 0, net: 0,
      lane: laneOf.get(code) || "none", split: false, capElsewhere: false,
      agreed: false, voices: 0, anyCue: 0, anyLlm: 0, cueOnly: false,
      __unnamed: true,
      reason: `Nobody on the panel named ${name} for GW${res.gw} in this ` +
        `window — he is here because somebody owns him, which is the one ` +
        `thing a transcript can never tell you.`,
    });
  }

  async function openPlayer(r) {
    drawer.textContent = "";
    drawer.classList.add("open");
    const head = el("div", "dhead");
    head.appendChild(faceImg(r.code, "bigface"));
    const id = el("div");
    id.appendChild(el("div", "dname", dn(r)));
    id.appendChild(el("div", "sub",
      [r.pos, r.team, r.price != null ? fmtPrice(r.price) : null,
       r.own_pct != null ? `${fmt1(r.own_pct)}% owned` : null,
       squadReady ? laneLabel(r.lane).toLowerCase() : null]
        .filter(Boolean).join(" · ")));
    head.appendChild(id);
    const close = el("button", null, "✕");
    close.onclick = closeDrawer;
    head.appendChild(close);
    drawer.appendChild(head);

    drawer.appendChild(el("p", "cx-why", r.reason));

    const sec = el("div");
    sec.appendChild(el("h2", null, "What was said, and where"));
    sec.appendChild(el("p", "sub", "Considered takes first, then keyword windows; newest first."));
    const list = el("div", "cx-quotes");
    list.appendChild(skeleton(1));
    sec.appendChild(list);
    drawer.appendChild(sec);

    /* the shared cross-tab strip: what the panel OWNS, said and noticed */
    const strip = el("div", "cx-strip");
    drawer.appendChild(strip);
    try {
      const mod = await import("/js/components/chatter.js");
      if (drawer.classList.contains("open") && mod.chatterSection)
        chatterHandle = mod.chatterSection(strip, r.code);
    } catch { strip.remove(); }

    const names = [...new Set([...r.buy.people, ...r.sell.people, ...r.cap.people])];
    const details = await Promise.all(names.map(n =>
      detailFor(n).then(d => ({ creator: n, d }))));
    if (!drawer.classList.contains("open")) return;
    list.textContent = "";

    const claims = [];
    const failed = [];
    for (const { creator, d } of details) {
      if (!d || d.__error) { failed.push({ creator, why: d && d.__error }); continue; }
      for (const item of d.items || [])
        for (const c of item.claims || [])
          if (c.code === r.code) claims.push({ creator, item, c });
    }
    claims.sort((a, b) =>
      (tier(b.c.extractor).key === "llm") - (tier(a.c.extractor).key === "llm") ||
      (parseTs(b.c.published_at) - parseTs(a.c.published_at)));

    if (!claims.length) {
      /* Two different silences, and they must not share a sentence. A player
         on the board with no quoted claim is a window mismatch; a player who
         reached this drawer from somebody's SQUAD was never on the board at
         all, and saying "counted above" about him would be false. */
      list.appendChild(r.__unnamed
        ? emptyBox(
            "Nobody on the panel named him.",
            `He is here because he is in somebody's fifteen. No claim in this ` +
            `window names him for GW${res.gw}, so there is nothing said to ` +
            "show — that is the quiet holding, not a failed lookup.")
        : emptyBox(
            "No stored claim for this player carries a quote.",
            "He is counted above from the consensus rollup, but the per-creator " +
            "record for these sources holds no quoted claim on him inside its own " +
            "window — the two windows differ. Nothing has been invented to fill " +
            "the gap."));
    }
    for (const { creator, item, c } of claims) {
      const d = el("div", "cx-qcard");
      const hd = el("div", "cx-qhead");
      const act = String(c.action || "").toLowerCase();
      hd.appendChild(el("span", "cx-act " +
        (act === "buy" || act === "hold" ? "in"
         : act === "sell" || act === "avoid" || act === "bench" ? "out"
         : act === "captain" ? "cap" : "flat"), act || "claim"));
      hd.appendChild(rcChip(creator));
      hd.appendChild(el("span", "sub", ` · ${relAge(c.published_at).text}`));
      d.appendChild(hd);
      if (item && item.title) {
        const it = el("div", "cx-qitem");
        const a = el("a", "cx-title", item.title);
        a.href = item.url; a.target = "_blank"; a.rel = "noopener noreferrer";
        it.appendChild(a);
        if (item.text_source)
          it.appendChild(el("span", "cx-src " + item.text_source,
            item.text_source === "transcript" ? "full transcript" : item.text_source));
        d.appendChild(it);
      }
      if (c.quote) d.appendChild(quoteBlock(c.quote, c, item));
      else d.appendChild(el("p", "sub",
        "This claim carries no quote — it is a keyword window, and there is " +
        "no sentence to show you."));
      list.appendChild(d);
    }
    if (failed.length) {
      const f = el("p", "sub");
      f.append("Could not read: " + failed.map(x => x.creator).join(", ") +
        (failed[0].why ? ` (${failed[0].why})` : ""));
      list.appendChild(f);
    }
  }
}
