/* Creators — the panel's deadline board.
 *
 * THE GOVERNING SENTENCE, printed at the top of the page in the position
 * where xPoints prints "Numbers are copied from ingested providers, never
 * modelled here" and Template prints its rank-move identity:
 *
 *     Nobody here has earned a weight. Across every scored call the panel
 *     hits BELOW chance. So this is not a forecast. It is the field's
 *     INTENT and what they actually OWN, and the only rows worth your
 *     attention are the ones where those disagree with your squad.
 *
 * Two consequences shape every decision in this file.
 *
 *   1. AGREEMENT IS COMPRESSED, DISAGREEMENT IS EXPANDED. Seven people
 *      saying buy a player you already captain is the least informative row
 *      on the page; it costs shaded background and one number. A split, or a
 *      call against your own squad, costs a card with a verbatim quote, a
 *      timestamp and a link to the source. The old page sorted the other way
 *      round (net desc) and gave twenty equal rows to "nothing to do".
 *
 *   2. AUTHORITY IS NEVER IMPLIED. There is no "most trusted" sort, because
 *      none has been earned. Ordering is volume and recency, and the page
 *      says so where the ordering happens, not in a footnote.
 *
 * TWO MEASURES, NEVER ONE AXIS. Panel intent (a count of people) and your
 * exposure (a role) are different measures with different units, so — per
 * Template's own rule, "like is only ever compared with like" — they do not
 * share an axis. Intent is the one numeric axis. Exposure is FOUR CATEGORICAL
 * LANES. This is deliberately not a 2-D scatter.
 *
 * COLOUR. Two hues only, both from the app's validated series ramp:
 * --s1 (#2a78d6) = the panel is IN, --s2 (#c25322 dark / #eb6834 light) =
 * the panel is OUT. Re-validated for this build with the dataviz six-checks
 * validator, all-pairs, in both modes:
 *   dark  surface #16181b — CVD ΔE 25.9 (protan) · normal 30.4 · contrast PASS
 *   light surface #ffffff — CVD ΔE 24.7 (protan) · normal 33.6 · contrast PASS
 * Captaincy is deliberately NOT a third hue: it is a one-of-N choice, not a
 * direction, so it is a neutral ★ with the count printed as text. Weak
 * evidence is never a hue either — a keyword window is a hollow mark, because
 * "less certain" must read as quieter, not as a different category.
 *
 * EVIDENCE TIERS ARE VISIBLE EVERYWHERE. `llm:` is a considered take with a
 * conviction band and a verbatim quote. `cue` is a keyword landing near a
 * player's name. Three keyword hits must never look like three opinions, so
 * they are drawn hollow and labelled "keyword window" wherever they appear.
 *
 * A SHOW IS NOT A PERSON. The Wire has four hosts with four different squads.
 * Flat show-level identity is used ONLY where a show has exactly one verified
 * person; otherwise SAID sits on a show band and OWN sits on the person rows,
 * and the grid says which is which rather than averaging them together.
 *
 * A WATCH IS AN OBSERVATION. `take.watching[]` is lifted out of the buy lists
 * upstream precisely so this view cannot render it as a recommendation by
 * omission. It gets its own section with its own sentence.
 *
 * WHAT IS NOT IN THE PAYLOAD IS SAID OUT LOUD. Every count reads from the
 * payload; where the panel has not yet published a field this build needs
 * (`scope`, `entry.people[]`, `panel_owned`, `url_basis`, `take.watching[]`),
 * the page names the missing field and what it would show — it never guesses
 * a number and never renders an absence as a zero.
 */

import { runPanel, el, emptyBox, errBox, provenance, faceImg,
         fmtPrice, fmt1 } from "/js/app.js";

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
  return { key: "unknown", label: extractor ? `extractor: ${e}` : "extractor not recorded",
           model: null, note: "the payload does not say how this claim was extracted" };
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

export default async function creators(host) {
  /* ---- shell ---------------------------------------------------------- */
  const teachCard = el("section", "card cx");
  const linkCard  = el("section", "card cx");
  const mainCard  = el("section", "card cx");
  host.append(teachCard, linkCard, mainCard);

  const viewRow = el("div", "toolbar");
  const evRow   = el("div", "toolbar");
  const body    = el("div", "cx-body");
  const foot    = el("div");
  mainCard.append(viewRow, evRow, body, foot);

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
  let view = "board";                 // "board" | "grid"
  let consideredOnly = false;         // evidence filter, never a second axis
  let showAgreed = false;
  let gridAll = false;
  const detailCache = new Map();      // creator -> Promise<creator_detail>
  const jobs = [];                    // paste-a-link jobs, newest first
  const pasted = new Map();           // canonical key -> url, this session only
  let linkBody = null;

  /* ---- load ----------------------------------------------------------- */
  body.appendChild(el("p", "sub", "loading the panel…"));
  const boardP = runPanel("creator_board", {});
  const squadP = runPanel("squad_overview", {}).catch(e => ({ error: e }));

  try {
    const r = await boardP;
    res = r.result; prov = r.provenance;
  } catch (e) {
    body.textContent = "";
    renderTeach();          // the governing sentence stands with or without data
    body.appendChild(errBox(e));
    body.appendChild(el("p", "sub",
      "That is the read path. Adding a source below writes through a " +
      "different service and is unaffected by this failure."));
    renderLinkBar();
    return;
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

  function buildRows() {
    return (res.consensus || []).map(c => {
      const buy = grp(c.buy), sell = grp(c.sell), cap = grp(c.captain);
      const use = k => consideredOnly ? k.llm : k.n;
      const nBuy = use(buy), nSell = use(sell), nCap = use(cap);
      const net = nBuy - nSell;
      const lane = laneOf.get(c.code) || "none";
      const split = nBuy > 0 && nSell > 0;
      const capElsewhere = nCap > 0 && lane !== "captain";
      /* THE AGREEMENT PREDICATE, printed on the page verbatim so a reader can
         check the shading against the rule rather than trusting it. */
      const agreed = !split && !capElsewhere && (
        lane === "captain" || lane === "start" ? net >= 0
        : lane === "none" ? net <= 0
        : false);                       // bench is unresolved in both directions
      const voices = new Set([...buy.people, ...sell.people, ...cap.people]);
      const anyCue = buy.cue + sell.cue + cap.cue;
      const anyLlm = buy.llm + sell.llm + cap.llm;
      let reason;
      if (split) reason = `the panel is split — ${buy.n} in, ${sell.n} out`;
      else if (capElsewhere) reason =
        `${plural(cap.n, "panellist")} named him captain and you did not`;
      else if (lane === "bench") reason =
        "he is on your bench — a benched player is unresolved whichever way they lean";
      else if (agreed && lane === "none") reason = "you don't own him and nobody is buying";
      else if (agreed) reason = "you own him and nobody is selling";
      else if (lane === "none") reason = `${plural(net, "net buyer")}; you don't own him`;
      else reason = `${plural(-net, "net seller")}; he is in your squad`;
      return {
        code: c.code, name: c.name, pos: c.pos, team: c.team,
        price: c.price, own_pct: c.own_pct,
        mine: c.mine || null, panel_owned: c.panel_owned || null,
        buy, sell, cap, nBuy, nSell, nCap, net, lane, split, capElsewhere,
        agreed, reason, voices: voices.size, anyCue, anyLlm,
        cueOnly: anyLlm === 0 && anyCue > 0,
      };
    }).filter(r => r.nBuy || r.nSell || r.nCap);
  }

  /* Ordering, stated where it is used: how many people said it, considered
     takes ahead of keyword windows, then the bigger net. NEVER by anyone's
     record, because no record has been earned. */
  const byWeightOfMouth = (a, b) =>
    (b.anyLlm - a.anyLlm) || (b.voices - a.voices) ||
    (Math.abs(b.net) - Math.abs(a.net)) || a.name.localeCompare(b.name);

  /* ---- render --------------------------------------------------------- */
  renderTeach();
  renderLinkBar();
  render();

  function render() {
    renderViewRow();
    renderEvidenceRow();
    body.textContent = "";
    if (view === "board") renderBoard(); else renderGrid();
    foot.textContent = "";
    foot.appendChild(provenance(prov));
  }

  /* ============================================================ the teach */

  function renderTeach() {
    teachCard.textContent = "";
    teachCard.appendChild(el("h2", null, "Creators — the panel's deadline"));

    const lead = el("p", "cx-lead");
    lead.append(
      "Nobody on this page has earned a weight. ",
      el("b", null, "Measured, the panel is below chance"),
      " — so this is not a forecast. It is the field's ",
      el("b", null, "intent"), ", and what they actually ",
      el("b", null, "own"),
      ". The only rows worth your attention are the ones where those " +
      "disagree with your squad.");
    teachCard.appendChild(lead);

    /* the governing expression, in the Template idiom */
    const idn = el("div", "cx-identity");
    idn.appendChild(el("span", "cx-eq-term intent", "panel intent"));
    idn.appendChild(el("span", "cx-eq-op", "×"));
    idn.appendChild(el("span", "cx-eq-term mine", "your exposure"));
    teachCard.appendChild(idn);
    const idsub = el("div", "cx-identity-sub");
    idsub.appendChild(el("span", null, "buy − sell, counted in people"));
    idsub.appendChild(el("span", "cx-eq-op", " "));
    idsub.appendChild(el("span", null, "captain · start · bench · not owned"));
    teachCard.appendChild(idsub);

    const rules = el("div", "cx-rules");
    const r1 = el("div", "cx-rule");
    r1.appendChild(el("span", "cx-sw agree"));
    r1.append(el("b", null, "agree"), " → collapses to a count, and costs you nothing but background");
    const r2 = el("div", "cx-rule");
    r2.appendChild(el("span", "cx-sw dis"));
    r2.append(el("b", null, "disagree"), " → becomes a row with a quote, a timestamp and a link to the source");
    rules.append(r1, r2);
    teachCard.appendChild(rules);

    /* the record, with its own denominator, computed from the payload */
    const rec = el("p", "cx-record");
    if (res) {
      let hits = 0, scored = 0, n = 0;
      for (const c of res.creators || []) {
        const r = c.record || {};
        if (r.scored) { hits += r.hits || 0; scored += r.scored; n++; }
      }
      if (scored) {
        rec.append(el("b", null, `${(100 * hits / scored).toFixed(1)}%`),
          ` — ${hits} hits from ${scored} scored calls across ` +
          `${plural(n, "creator")} in this payload. Below a coin flip. ` +
          `Every earned weight is 0.0.`);
      } else {
        rec.append("No scored calls in this payload, so there is no measured record to show.");
      }
      teachCard.appendChild(rec);
      if (res.record_note) {
        const note = el("p", "cx-note");
        note.appendChild(el("span", "cx-quotemark", "the panel's own words: "));
        note.appendChild(document.createTextNode(res.record_note));
        teachCard.appendChild(note);
      }
    }

    /* the ordering disclosure — stated, not buried */
    const det = el("details", "cx-disclose");
    det.appendChild(el("summary", null, "Why there is no “most trusted” sort"));
    const dp = el("p", "sub");
    dp.textContent =
      "A weight is earned by beating chance over enough calls to be sure it " +
      "was not luck. Nobody has. So this page has no reputation ranking, no " +
      "star ratings and no confidence scores attached to names: every " +
      "ordering on it is volume (how many people said it) and recency (when " +
      "they said it), and each ordering says which it is where it happens. " +
      "The per-creator scoreboard still exists — it is in the roster at the " +
      "bottom of the board — but it is reference material, not a ranking.";
    det.appendChild(dp);
    teachCard.appendChild(det);

    /* scope: 16 people across 6 shows — or an honest account of its absence */
    teachCard.appendChild(renderScope());
  }

  function renderScope() {
    const box = el("div", "cx-scope");
    const s = res && res.scope;
    const num = v => Array.isArray(v) ? v.length : (typeof v === "number" ? v : null);
    const people = s && (num(s.people) ?? num(s.n_people) ?? num(s.panel));
    const shows  = s && (num(s.shows) ?? num(s.n_shows));
    if (people != null || shows != null) {
      const line = el("div", "cx-scope-line");
      line.appendChild(el("b", null,
        `${people != null ? plural(people, "person", "people") : "the panel"}` +
        `${shows != null ? ` across ${plural(shows, "show")}` : ""}`));
      line.append(" — the scope of everything below.");
      box.appendChild(line);
      const ex = s.excluded || [];
      if (ex.length) {
        const d = el("details", "cx-disclose");
        d.appendChild(el("summary", null,
          `${plural(ex.length, "ingested source")} excluded from the panel`));
        const ul = el("ul", "cx-ul");
        for (const e of ex) {
          const li = el("li");
          li.appendChild(el("b", null, e.creator || e.name || e.key || String(e)));
          if (e.reason) li.append(" — ", e.reason);
          ul.appendChild(li);
        }
        d.appendChild(ul);
        box.appendChild(d);
      }
    } else {
      const line = el("div", "cx-scope-line warn");
      line.appendChild(el("b", null, "This payload is not scoped to the panel yet."));
      line.append(
        ` \`creator_board\` returned ${plural((res.creators || []).length, "ingested source")} ` +
        "and no `scope` block, so the counts below are over everything ingestion " +
        "reached — which includes `user-shared`, a bucket of links pasted by hand " +
        "and not a creator at all. The panel is 16 people across 6 shows; when " +
        "`scope` arrives this line will say so and the excluded sources will be " +
        "named here rather than silently counted.");
      box.appendChild(line);
    }
    return box;
  }

  /* ======================================================== the link bar */

  function renderLinkBar() {
    linkCard.textContent = "";
    linkCard.appendChild(el("h2", null, "Add a source"));

    const row = el("div", "toolbar");
    row.appendChild(el("span", "tlabel", "Paste a link"));
    const input = el("input", "cx-url");
    input.type = "url";
    input.placeholder = "a YouTube video or a podcast episode…";
    input.setAttribute("aria-label", "URL of a YouTube video or podcast episode");
    const btn = el("button", "primary", "Add");
    row.append(input, btn);
    linkCard.appendChild(row);

    /* NAME THE PATH BEFORE THE WAIT. Both rates are measured, and the page
       says which measurement it is quoting. */
    const rates = el("p", "cx-rates");
    rates.append(
      "Two paths, and the job names which one it took before the wait starts. ",
      el("b", null, "Published captions"),
      " run at about 286× realtime — a 20-minute video is roughly 4 seconds. ",
      el("b", null, "Local speech-to-text"),
      " runs at about 11.5× — the same video is roughly 105 seconds. Both " +
      "figures are measured from stored runs, not estimated.");
    linkCard.appendChild(rates);

    const err = el("div", "cx-linkerr");
    linkCard.appendChild(err);
    linkBody = el("div", "cx-jobs");
    linkCard.appendChild(linkBody);

    const submit = () => {
      err.textContent = "";
      const raw = input.value.trim();
      if (!raw) return;
      if (!/^https?:\/\/\S+$/i.test(raw)) {
        err.appendChild(failLine(
          "That is not a link.",
          "It needs to start with http:// or https:// and point at a video or " +
          "an episode. Nothing was sent."));
        return;
      }
      const key = canonicalKey(raw);
      if (pasted.has(key)) {
        err.appendChild(failLine(
          "You already pasted this one.",
          `Same video, this session, as ${pasted.get(key)}. Two URL forms of ` +
          "one video are the duplicate the warehouse already holds twice — " +
          "nothing was sent."));
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

  async function startJob(url, key) {
    const job = { url, key, state: "posting", stages: null, stage: null,
                  pct: null, eta: null, error: null, item_id: null,
                  started: Date.now(), timer: null, path: null };
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
    if (payload && payload.duplicate_of) {
      job.state = "duplicate"; job.item_id = payload.duplicate_of;
      renderJobs(); return;
    }
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
    job.timer = setInterval(async () => {
      if (!document.body.contains(linkCard)) { clearInterval(job.timer); return; }
      let r, p = null;
      try {
        r = await fetch(`/api/ingest/link/${encodeURIComponent(job.job_id)}`);
        p = await r.json();
      } catch (e) {
        clearInterval(job.timer);
        job.state = "down";
        job.error = `polling failed: ${String(e.message || e)}`;
        renderJobs(); return;
      }
      if (!r.ok) {
        clearInterval(job.timer);
        job.state = r.status === 404 ? "down" : "failed";
        job.error = (p && (p.detail || p.error)) || `HTTP ${r.status}`;
        renderJobs(); return;
      }
      job.stage = p.stage ?? job.stage;
      job.pct = p.pct ?? job.pct;
      job.eta = p.eta_s ?? job.eta;
      job.item_id = p.item_id ?? job.item_id;
      if (p.path || p.transcript_path) job.path = p.path || p.transcript_path;
      /* The stage detail is where the path shows itself, so read it. */
      const blob = JSON.stringify(p).toLowerCase();
      if (!job.path && /caption/.test(blob)) job.path = "captions";
      else if (!job.path && /(whisper|asr|audio)/.test(blob)) job.path = "asr";
      if (p.error) {
        clearInterval(job.timer);
        job.error = String(p.error);
        job.state = classifyError(job.error);
        renderJobs(); return;
      }
      if (p.done) {
        clearInterval(job.timer);
        job.state = "done";
        renderJobs();
        return;
      }
      renderJobs();
    }, 1200);
  }

  /* The four failures the warehouse already contains, handled BY NAME. */
  function classifyError(msg) {
    const m = String(msg).toLowerCase();
    if (/\b(403|429)\b|forbidden|rate.?limit|too many requests/.test(m)) return "declined";
    if (/duplicate|already (held|ingested|have)|exists/.test(m)) return "duplicate";
    if (/no captions|no audio|no_media|no enclosure|nothing to transcribe/.test(m)) return "nomedia";
    if (/too.?thin|not an? (episode|article|video)|no text|3 characters|league/.test(m)) return "notepisode";
    return "failed";
  }

  function renderJobs() {
    if (!linkBody) return;
    linkBody.textContent = "";
    for (const job of jobs) linkBody.appendChild(renderJob(job));
  }

  function renderJob(job) {
    const d = el("div", "cx-job");
    const hd = el("div", "cx-job-head");
    hd.appendChild(el("span", "cx-job-state " + job.state, jobStateLabel(job)));
    const a = el("a", "cx-job-url", job.url);
    a.href = job.url; a.target = "_blank"; a.rel = "noopener noreferrer";
    hd.appendChild(a);
    const x = el("button", "cx-x", "✕");
    x.title = "remove this row (the server keeps whatever it is doing)";
    x.onclick = () => {
      clearInterval(job.timer);
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
    if (job.state === "duplicate") {
      d.appendChild(failLine("Already held.",
        (job.item_id ? `Stored as ${job.item_id}. ` : "") +
        "The same video under a second URL form is already in the corpus, so " +
        "nothing was ingested twice. Its take is on the board already."));
      return d;
    }
    if (job.state === "notepisode") {
      d.appendChild(failLine("That link is not an episode.",
        `${job.error} — there is no substantive text behind it. An FPL league ` +
        "invite has been ingested this way before and became an article " +
        "titled `a6fgym`. Paste a video or an episode page instead."));
      return d;
    }
    if (job.state === "nomedia") {
      d.appendChild(failLine("No captions published, and no audio file behind the page.",
        `${job.error}. There is nothing to read and nothing to transcribe. If ` +
        "this is a podcast, paste the episode's YouTube link instead — that " +
        "usually has captions and takes about four seconds."));
      return d;
    }
    if (job.state === "failed") {
      d.appendChild(failLine("The job failed.", String(job.error)));
      return d;
    }

    /* the stage ledger */
    const names = job.stages || ["fetch", "transcribe", "analyse", "attribute"];
    const idx = job.stage ? names.indexOf(job.stage) : -1;
    if (job.path) {
      const p = el("div", "cx-job-path");
      p.append(job.path === "captions"
        ? "Captions path — measured at about 286× realtime."
        : job.path === "asr"
          ? "No captions, so local speech-to-text — measured at about 11.5× realtime."
          : `Path: ${job.path}.`);
      d.appendChild(p);
    }
    const ledger = el("div", "cx-stages");
    names.forEach((nm, i) => {
      const state = job.state === "done" ? "done"
        : idx < 0 ? (i === 0 ? "now" : "wait")
        : i < idx ? "done" : i === idx ? "now" : "wait";
      const row = el("div", "cx-stage " + state);
      row.appendChild(el("span", "cx-stage-mark",
        state === "done" ? "✓" : state === "now" ? "◐" : "○"));
      row.appendChild(el("span", "cx-stage-name", nm));
      if (state === "now" && job.pct != null) {
        const barwrap = el("span", "cx-stage-bar");
        const fill = el("span", "cx-stage-fill");
        fill.style.width = `${Math.max(0, Math.min(100, Number(job.pct)))}%`;
        barwrap.appendChild(fill);
        row.appendChild(barwrap);
        row.appendChild(el("span", "cx-stage-pct", `${Math.round(job.pct)}%`));
      }
      if (state === "now" && job.eta != null)
        row.appendChild(el("span", "cx-stage-eta", `~${Math.round(job.eta)}s left`));
      ledger.appendChild(row);
    });
    d.appendChild(ledger);
    if (job.state === "done") {
      const ok = el("div", "cx-job-ok");
      ok.append(el("b", null, "Ingested."),
        job.item_id ? ` Stored as ${job.item_id}. ` : " ",
        "It joins the board on the next panel read — reload to see it.");
      d.appendChild(ok);
    } else {
      d.appendChild(el("div", "sub",
        "This keeps running on the server if you leave the page. This ledger " +
        "does not — it stops updating when the view unmounts."));
    }
    return d;
  }

  const jobStateLabel = j => ({
    posting: "SENDING", running: "WORKING", done: "DONE", down: "UNAVAILABLE",
    declined: "STOPPED", duplicate: "DUPLICATE", nomedia: "NO MEDIA",
    notepisode: "NOT AN EPISODE", failed: "FAILED",
  }[j.state] || j.state);

  /* ======================================================== the toolbars */

  function renderViewRow() {
    viewRow.textContent = "";
    viewRow.appendChild(el("span", "tlabel", "View"));
    const seg = el("span", "seg");
    for (const [k, label, title] of [
      ["board", "Deadline board",
       "one axis of panel intent, four lanes for your exposure"],
      ["grid", "Said vs owned",
       "people × players — hue is what they said, outline is what they own"],
    ]) {
      const b = el("button", k === view ? "on" : "", label);
      b.title = title;
      b.onclick = () => { view = k; render(); };
      seg.appendChild(b);
    }
    viewRow.appendChild(seg);
    const gw = res.gw;
    if (gw != null) {
      const c = el("span", "cx-gw");
      c.append(el("b", null, `GW${gw}`), res.gw_reason ? ` · ${res.gw_reason}` : "");
      viewRow.appendChild(c);
    }
    if (res.window_days != null)
      viewRow.appendChild(el("span", "sub",
        `claims from the last ${plural(res.window_days, "day")}`));
    const age = relAge(res.as_of);
    const asof = el("span", "sub cx-asof");
    asof.appendChild(el("span", "freshdot " + age.cls));
    asof.append(` read ${age.text}`);
    viewRow.appendChild(asof);
  }

  function renderEvidenceRow() {
    evRow.textContent = "";
    evRow.appendChild(el("span", "tlabel", "Evidence"));
    const seg = el("span", "seg");
    for (const [k, label, title] of [
      [false, "All claims",
       "keyword windows included, drawn hollow so they never look like opinions"],
      [true, "Considered takes only",
       "only llm: claims — a model read the passage and returned a verbatim quote"],
    ]) {
      const b = el("button", consideredOnly === k ? "on" : "", label);
      b.title = title;
      b.onclick = () => { consideredOnly = k; render(); };
      seg.appendChild(b);
    }
    evRow.appendChild(seg);
    const key = el("span", "cx-key");
    const solid = el("span", "cx-keyitem");
    solid.appendChild(el("span", "cx-dot solid"));
    solid.append("considered take");
    const hollow = el("span", "cx-keyitem");
    hollow.appendChild(el("span", "cx-dot hollow"));
    hollow.append("keyword window only");
    key.append(solid, hollow);
    evRow.appendChild(key);
  }

  /* ==================================================== THE DEADLINE BOARD */

  function renderBoard() {
    const rows = buildRows();
    if (!rows.length) {
      body.appendChild(emptyBox(
        consideredOnly
          ? "No considered take names a player in this window."
          : "Nobody has named a player in this window.",
        consideredOnly
          ? "Switch Evidence back to “All claims” — there may be keyword windows " +
            "behind this, and they are shown hollow because they are not opinions."
          : "Panellists publish their team-selection content in the day or two " +
            "before a deadline, so an empty board this far out is normal, not broken."));
      return;
    }

    body.appendChild(laneProvenance());
    body.appendChild(drawBoard(rows));
    body.appendChild(boardLegend(rows));
    renderDecisions(rows);
    renderArmband(rows);
    renderWatching();
    renderRoster();
  }

  /* Where the lanes come from — always stated, never assumed. */
  function laneProvenance() {
    const p = el("p", "cx-provline");
    if (squadReady) {
      p.appendChild(el("b", null, "Your lanes"));
      p.append(` are your ${squad.gw != null ? `GW${squad.gw}` : "current"} squad` +
        `${squad.provenance_source ? ` — ${squad.provenance_source}` : ""}` +
        `${squad.as_of ? `, read ${relAge(squad.as_of).text}` : ""}. ` +
        `${squad.captain ? `Captain: ${squad.captain}. ` : ""}` +
        "If that gameweek is behind the one the panel is talking about, the " +
        "lanes are last week's team and this line is how you know.");
    } else {
      p.classList.add("warn");
      p.appendChild(el("b", null, "Your squad could not be read"));
      p.append(`${squadErr ? ` — ${squadErr}` : ""}. The board still works: it ` +
        "shows what the panel is doing. It cannot show what that means for " +
        "you, so every player sits in one lane and nothing is shaded.");
    }
    return p;
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
      "panellists — buy minus sell, one opinion per person"));

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
            x: lx, y: cy + 3.6 }, m.r.name));
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
      tip.appendChild(el("b", null, r.name));
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
    const dot = cls => { const s = el("span", "cx-dot " + cls); return s; };
    box.appendChild(item(dot("solid in"), "the panel is net IN"));
    box.appendChild(item(dot("solid out"), "the panel is net OUT"));
    box.appendChild(item(dot("split"), "split — some in, some out"));
    box.appendChild(item(dot("hollow"), "keyword window only, not an opinion"));
    box.appendChild(item(el("span", "cx-star", "★"), "someone named him captain"));
    if (squadReady)
      box.appendChild(item(el("span", "cx-shadesw"),
        "shaded: you and the panel already agree — nothing to do"));

    const rule = el("details", "cx-disclose");
    rule.appendChild(el("summary", null, "What counts as “already agree”"));
    const ul = el("ul", "cx-ul");
    for (const t of [
      "You own him and the panel is not net selling → agree.",
      "You don't own him and the panel is not net buying → agree.",
      "He is on your bench → never agree. A benched player is unresolved " +
        "whichever way the panel leans, so no part of that lane is shaded.",
      "The panel is split (someone in AND someone out) → never agree, whatever " +
        "the net is. A net of zero from four in and four out is the most " +
        "informative row on the page, not the least.",
      "Somebody captained him and you didn't → never agree. Captaincy is a " +
        "one-of-N choice, not a direction, so it is counted and starred here " +
        "and settled in The armband below.",
    ]) ul.appendChild(el("li", null, t));
    rule.appendChild(ul);
    box.appendChild(rule);

    /* the shape of tonight's board, computed and stated */
    const single = rows.filter(r => r.voices === 1).length;
    const shape = el("p", "cx-shape");
    shape.append(
      el("b", null, `${plural(rows.length, "player")} named`), ". ",
      `${single} of them carry a single voice` +
      `${single > rows.length / 2 ? " — this is a column, not a distribution, and that is what the corpus contains" : ""}. ` +
      `${rows.filter(r => r.split).length} are splits. ` +
      `${rows.filter(r => r.cueOnly).length} rest on keyword windows alone.`);
    box.appendChild(shape);
    return box;
  }

  /* ------------------------------------------------------- the decisions */

  function renderDecisions(rows) {
    const inSquad = rows.filter(r => r.lane !== "none" && !r.agreed).sort(byWeightOfMouth);
    const buys = rows.filter(r => r.lane === "none" && !r.agreed && r.net > 0)
      .sort(byWeightOfMouth);
    const agreed = rows.filter(r => r.agreed);

    const sec = el("div", "cx-sec");
    const h = el("div", "cx-sechead");
    h.appendChild(el("h3", null, "Your decisions"));
    h.appendChild(el("span", "cx-sechint",
      "ordered by weight of mouth: considered takes first, then how many " +
      "people said it, then the size of the gap. Never by anyone's record."));
    sec.appendChild(h);

    /* The DID channel, stated once rather than left as a blank on every card.
       An absent field is not a zero, and saying so here is cheaper and more
       honest than an empty line repeated thirty times below. */
    if (!rows.some(r => r.panel_owned && r.panel_owned.of != null)) {
      const p = el("p", "cx-provline warn");
      p.appendChild(el("b", null, "What the panel actually OWNS is not on these cards yet."));
      p.append(" No row carries `panel_owned`, so this page can tell you what " +
        "they SAID and not how many of them hold the player they are talking " +
        "about. Talk with nobody's squad behind it and a call backed by a " +
        "locked team look identical here until the panel publishes that field. " +
        "It is a missing field, not a count of zero.");
      sec.appendChild(p);
    }

    if (squadReady) {
      sec.appendChild(el("h4", "cx-subhead",
        `Against your own squad (${inSquad.length})`));
      if (!inSquad.length)
        sec.appendChild(el("p", "sub",
          "Nobody argued with a player you own. That is a finding, not a blank."));
      for (const r of inSquad) sec.appendChild(decisionCard(r, true));
    }

    const CUT = 6;
    const strong = buys.filter(r => r.voices > 1);
    const single = buys.filter(r => r.voices <= 1);
    sec.appendChild(el("h4", "cx-subhead",
      `Buys you don't own (${buys.length})`));
    if (!buys.length)
      sec.appendChild(el("p", "sub", "Nobody is buying anything you don't already have."));
    const head = strong.length ? strong : buys.slice(0, CUT);
    for (const r of head.slice(0, CUT)) sec.appendChild(decisionCard(r, false));
    const rest = (strong.length ? single : buys.slice(CUT));
    if (rest.length) {
      const d = el("details", "cx-disclose");
      d.appendChild(el("summary", null,
        `${plural(rest.length, "single-voice call")} — one person, once. Open for the list.`));
      const list = el("div", "cx-thin");
      for (const r of rest.sort(byWeightOfMouth)) {
        const b = el("button", "cx-thinrow");
        b.appendChild(el("span", "cx-dot " + (r.cueOnly ? "hollow" : "solid in")));
        b.appendChild(el("b", null, r.name));
        b.appendChild(el("span", "sub",
          ` ${r.pos || ""} ${r.team || ""} · ${[...r.buy.people].join(", ") || "—"}` +
          `${r.cueOnly ? " · keyword window" : ""}`));
        b.onclick = () => openPlayer(r);
        list.appendChild(b);
      }
      d.appendChild(list);
      sec.appendChild(d);
    }

    /* agreement: a count, not twenty rows */
    const ag = el("div", "cx-agreed");
    const agb = el("button", "cx-agreed-btn");
    agb.append(el("b", null, String(agreed.length)),
      ` player${agreed.length === 1 ? "" : "s"} where you and the panel already agree — nothing to do`);
    const agList = el("div", "cx-agreed-list");
    agList.hidden = !showAgreed;
    agb.onclick = () => { showAgreed = !showAgreed; agList.hidden = !showAgreed; };
    for (const r of agreed.sort(byWeightOfMouth)) {
      const b = el("button", "cx-thinrow");
      b.appendChild(el("b", null, r.name));
      b.appendChild(el("span", "sub", ` — ${r.reason}`));
      b.onclick = () => openPlayer(r);
      agList.appendChild(b);
    }
    ag.append(agb, agList);
    sec.appendChild(ag);
    body.appendChild(sec);
  }

  function decisionCard(r, owned) {
    const c = el("div", "cx-card" + (r.split ? " split" : r.net > 0 ? " in" : r.net < 0 ? " out" : ""));
    const hd = el("div", "cx-card-head");
    hd.appendChild(faceImg(r.code, "cx-face"));
    const idb = el("div", "cx-card-id");
    const verb = r.split ? "SPLIT"
      : r.capElsewhere && r.net === 0 ? "ARMBAND"
      : r.net > 0 ? (owned ? "THEY LIKE HIM" : "BUY")
      : r.net < 0 ? (owned ? "SELL" : "AVOID") : "MIXED";
    const t = el("div", "cx-card-title");
    t.appendChild(el("span", "cx-verb", verb));
    t.appendChild(el("b", null, r.name));
    idb.appendChild(t);
    idb.appendChild(el("div", "sub",
      [r.pos, r.team, r.price != null ? fmtPrice(r.price) : null,
       r.own_pct != null ? `${fmt1(r.own_pct)}% owned` : null,
       owned ? laneLabel(r.lane).toLowerCase() : "not in your squad",
      ].filter(Boolean).join(" · ")));
    hd.appendChild(idb);
    const counts = el("div", "cx-card-counts");
    counts.appendChild(countChip("in", r.nBuy, r.buy));
    counts.appendChild(countChip("out", r.nSell, r.sell));
    if (r.nCap) counts.appendChild(countChip("cap", r.nCap, r.cap));
    hd.appendChild(counts);
    c.appendChild(hd);

    c.appendChild(el("div", "cx-why", r.reason));

    /* who said it, split by evidence tier — always, never merged */
    const ev = el("div", "cx-evline");
    ev.append(`${r.anyLlm} considered take${r.anyLlm === 1 ? "" : "s"}, ` +
      `${r.anyCue} keyword window${r.anyCue === 1 ? "" : "s"}`);
    c.appendChild(ev);
    const who = el("div", "cx-who");
    const namesOf = (g, verbLabel) => {
      if (!g.people.length) return;
      const d = el("div", "cx-wholine");
      d.appendChild(el("span", "cx-wholabel", verbLabel));
      d.append(g.people.join(" · "));
      who.appendChild(d);
    };
    namesOf(r.buy, "in");
    namesOf(r.sell, "out");
    namesOf(r.cap, "captain");
    c.appendChild(who);

    /* THE DISSENT LINE IS ALWAYS PRESENT. "Nobody pushed back" is a finding;
       a blank looks like a bug. */
    const dis = el("div", "cx-dissent");
    dis.appendChild(el("span", "cx-wholabel", "dissent"));
    dis.append(r.split
      ? `${r.sell.people.join(", ")} argued the other way`
      : r.nSell > 0 && r.nBuy === 0 ? "nobody argued for keeping him"
      : "none recorded — no panellist argued the other side");
    c.appendChild(dis);

    /* panel ownership, when the panel publishes it */
    if (r.panel_owned && r.panel_owned.of != null) {
      const po = el("div", "cx-owned");
      po.appendChild(el("span", "cx-wholabel", "actually own him"));
      po.append(`${r.panel_owned.n} of ${r.panel_owned.of}` +
        (r.panel_owned.people && r.panel_owned.people.length
          ? ` — ${r.panel_owned.people.join(", ")}` : ""));
      c.appendChild(po);
    }

    const act = el("div", "cx-actions");
    const q = el("button", "cx-open", "quotes, timestamps and sources");
    q.onclick = () => openPlayer(r);
    act.appendChild(q);
    act.appendChild(crossLink("#xpoints", "xPoints", r));
    act.appendChild(crossLink("#template", "Template", r));
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
    a.title = `Opens the ${label} tab. It does not yet accept a player ` +
      `parameter, so it will not jump to ${r.name} — search for him there.`;
    return a;
  }

  /* --------------------------------------------------------- the armband */

  function renderArmband(rows) {
    const caps = rows.filter(r => r.nCap > 0)
      .sort((a, b) => b.nCap - a.nCap || a.name.localeCompare(b.name));
    const sec = el("div", "cx-sec");
    const h = el("div", "cx-sechead");
    h.appendChild(el("h3", null, "The armband"));
    h.appendChild(el("span", "cx-sechint",
      "Captaincy is a one-of-N choice, not a buy or a sell, so it never " +
      "touches the axis above. Counted, starred, never coloured."));
    sec.appendChild(h);
    if (!caps.length) {
      sec.appendChild(el("p", "sub",
        "Nobody in this window named a captain. Captain calls land in the " +
        "day before a deadline; an empty armband earlier than that is normal."));
      body.appendChild(sec); return;
    }
    const max = caps[0].nCap;
    const list = el("div", "cx-arm");
    for (const r of caps) {
      const row = el("button", "cx-armrow" + (r.lane === "captain" ? " yours" : ""));
      const nm = el("span", "cx-armname");
      nm.appendChild(el("b", null, r.name));
      if (r.lane === "captain") nm.appendChild(el("span", "cx-yours", "yours"));
      else if (r.lane !== "none") nm.appendChild(el("span", "cx-inyours", laneLabel(r.lane).toLowerCase()));
      row.appendChild(nm);
      const bw = el("span", "cx-armbar");
      const f = el("span", "cx-armfill");
      f.style.width = `${Math.max(4, Math.round(100 * r.nCap / max))}%`;
      bw.appendChild(f);
      row.appendChild(bw);
      row.appendChild(el("span", "cx-armn", String(r.nCap)));
      row.appendChild(el("span", "cx-armmeta",
        `${r.cap.llm} considered / ${r.cap.cue} keyword` +
        (r.own_pct != null ? ` · ${fmt1(r.own_pct)}% owned` : "")));
      row.title = r.cap.people.join(", ");
      row.onclick = () => openPlayer(r);
      list.appendChild(row);
    }
    sec.appendChild(list);
    const yours = caps.find(r => r.lane === "captain");
    if (squadReady && !yours && squad.captain)
      sec.appendChild(el("p", "cx-provline",
        `Nobody in this window named ${squad.captain}, who is your captain. ` +
        `That is an absence in the corpus, not a verdict on him.`));
    body.appendChild(sec);
  }

  /* -------------------------------------------- watch calls (observations) */

  function renderWatching() {
    const obs = [];
    for (const c of res.creators || []) {
      for (const w of (c.take && c.take.watching) || [])
        obs.push({ ...w, creator: c.creator, latest: c.latest });
    }
    const sec = el("div", "cx-sec");
    const h = el("div", "cx-sechead");
    h.appendChild(el("h3", null, "Watch calls"));
    h.appendChild(el("span", "cx-sechint",
      "These are OBSERVATIONS — “watch Semenyo”, not “buy Semenyo”. They are " +
      "held out of every count above, because rendering them as buys would " +
      "attribute calls nobody made."));
    sec.appendChild(h);
    if (!obs.length) {
      sec.appendChild(el("p", "sub",
        "This payload carries no `take.watching[]` — either nobody flagged a " +
        "player to watch in this window, or `creator_board` is not publishing " +
        "the field yet. Either way, no watch call has been counted as a buy above."));
      body.appendChild(sec); return;
    }
    const list = el("div", "cx-obslist");
    for (const o of obs) {
      const d = el("div", "cx-obs");
      const t = el("div", "cx-obs-head");
      t.appendChild(el("span", "cx-obsbadge", "observed"));
      t.appendChild(el("b", null, o.name || String(o.code)));
      t.appendChild(el("span", "sub", ` — ${o.creator}`));
      d.appendChild(t);
      if (o.quote) d.appendChild(quoteBlock(o.quote, o, o.latest));
      list.appendChild(d);
    }
    sec.appendChild(list);
    body.appendChild(sec);
  }

  /* --------------------------------------------------------- the roster */

  function renderRoster() {
    const sec = el("div", "cx-sec");
    const det = el("details", "cx-disclose big");
    const cs = (res.creators || []).slice().sort((a, b) =>
      (b.n_claims_window || 0) - (a.n_claims_window || 0));
    det.appendChild(el("summary", null,
      `The roster — ${plural(cs.length, "source")}, their latest item, and the ` +
      `honest scoreboard`));
    const anyPeople = cs.some(c => ((c.entry && c.entry.people) || c.people || []).length);
    const anyEntry = cs.some(c => c.entry);
    const hint = el("p", "sub",
      "Reference material, opened when something looks wrong. Ordered by how " +
      "much each said in the window — volume, not merit. Every earned weight " +
      "here is 0.0; the hit rate column is the measurement that says why.");
    det.appendChild(hint);
    /* One aggregated absence beats thirty identical apologies in the rows. */
    if (!anyPeople || !anyEntry) {
      const miss = el("p", "sub");
      const bits = [];
      if (!anyPeople) bits.push("no source carries `entry.people[]`, so every " +
        "row below is a show and none of them resolves to a named person");
      if (!anyEntry) bits.push("no source carries a verified FPL entry id, so " +
        "no squad can be read for any of them");
      miss.append("In this payload " + bits.join("; ") + ". " +
        (cs.find(c => c.entry_reason)?.entry_reason || ""));
      det.appendChild(miss);
    }
    const wrap = el("div", "scroll-x");
    const t = el("table", "data");
    const thead = el("thead"); const hr = el("tr");
    for (const [l, num] of [["source", 0], ["people", 0], ["latest", 0],
                            ["items 30d", 1], ["claims 30d", 1],
                            ["scored", 1], ["hit rate", 1], ["weight", 1]])
      hr.appendChild(el("th", num ? "num" : "", l));
    thead.appendChild(hr); t.appendChild(thead);
    const tb = el("tbody");
    for (const c of cs) {
      const tr = el("tr");
      const nameTd = el("td");
      nameTd.appendChild(el("b", null, c.creator));
      if (anyEntry && !c.entry)
        nameTd.appendChild(el("div", "cx-tiny", "no verified entry id"));
      tr.appendChild(nameTd);

      const ppl = el("td");
      const people = (c.entry && c.entry.people) || c.people || [];
      if (people.length) {
        ppl.textContent = people.map(p => p.display_name || p.name || p).join(", ");
        if (people.length > 1)
          ppl.appendChild(el("div", "cx-tiny",
            `${people.length} hosts — this show's claims are the show's, not any one host's`));
      } else {
        ppl.appendChild(el("span", "cx-tiny", anyPeople ? "none published" : "–"));
      }
      tr.appendChild(ppl);

      const lt = el("td");
      if (c.latest) {
        const k = linkKind(c.latest.url, c.latest.url_basis);
        const a = el("a", "cx-title", c.latest.title || c.latest.url);
        a.href = c.latest.url; a.target = "_blank"; a.rel = "noopener noreferrer";
        a.title = k.why || "";
        lt.appendChild(a);
        const meta = el("div", "cx-tiny");
        meta.append(`${k.label} · ${relAge(c.latest.published_at).text}`);
        if (c.latest.text_source)
          meta.append(` · ${c.latest.text_source === "transcript"
            ? "full transcript read" : `${c.latest.text_source} only`}`);
        lt.appendChild(meta);
      } else if (c.latest_reason) lt.appendChild(el("span", "cx-tiny", c.latest_reason));
      else lt.appendChild(el("span", "cx-tiny", "nothing in the window"));
      tr.appendChild(lt);

      tr.appendChild(el("td", "num", String(c.n_items_window ?? "–")));
      tr.appendChild(el("td", "num", String(c.n_claims_window ?? "–")));
      const rec = c.record || {};
      tr.appendChild(el("td", "num", rec.scored != null ? String(rec.scored) : "–"));
      const hrTd = el("td", "num",
        rec.hit_rate != null ? `${(100 * rec.hit_rate).toFixed(0)}%` : "–");
      if (rec.reason) hrTd.title = rec.reason;
      tr.appendChild(hrTd);
      tr.appendChild(el("td", "num", rec.weight != null ? rec.weight.toFixed(1) : "–"));
      tb.appendChild(tr);
    }
    t.appendChild(tb); wrap.appendChild(t); det.appendChild(wrap);
    sec.appendChild(det);
    body.appendChild(sec);
  }

  /* ================================================ THE SAID-VS-OWNED GRID */

  function renderGrid() {
    const rows = buildRows();
    if (!rows.length) {
      body.appendChild(emptyBox("Nobody has named a player in this window.",
        "The grid needs somebody to have said something."));
      return;
    }

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
    const nameByCode = new Map(rows.map(r => [r.code, r.name]));
    for (const c of res.creators || [])
      for (const w of (c.take && c.take.watching) || []) {
        if (w.code == null) continue;
        if (w.name && !nameByCode.has(w.code)) nameByCode.set(w.code, w.name);
        if (!said.has(c.creator)) said.set(c.creator, new Map());
        const m = said.get(c.creator);
        const cell = m.get(w.code) || { in: false, out: false, cap: false, cueOnly: false };
        cell.watch = true; m.set(w.code, cell);
        if (!touch.has(w.code)) touch.set(w.code, new Set());
        touch.get(w.code).add(c.creator);
      }

    /* OWN: person -> Set(code), from whatever the panel actually publishes */
    const own = new Map();
    let ownSource = null;
    for (const r of rows) {
      const po = r.panel_owned;
      if (po && Array.isArray(po.people)) {
        ownSource = "panel_owned";
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
          ownSource = ownSource || "entry.people[].owned";
          const key = p.display_name || p.name;
          if (!own.has(key)) own.set(key, new Set());
          for (const x of codes) own.get(key).add(typeof x === "object" ? x.code : x);
        }
      }

    /* ROWS, banded. A show is not a person. */
    const bands = [];
    const byCreator = new Map((res.creators || []).map(c => [c.creator, c]));
    const said1 = [...said.keys()];
    const solo = [], showBand = [];
    for (const who of said1) {
      const c = byCreator.get(who);
      const people = (c && ((c.entry && c.entry.people) || c.people)) || [];
      if (people.length === 1) {
        const p = people[0];
        const nm = p.display_name || p.name || who;
        solo.push({ key: who, label: nm, sub: who, kind: "person",
                    ownKey: nm, note: "the only host on this show, so what the show said is what he said" });
      } else if (people.length > 1) {
        showBand.push({ key: who, label: who, sub: `${people.length} hosts`,
                        kind: "show", ownKey: null,
                        note: "said by the show — this payload does not attribute its claims to a host" });
        for (const p of people) {
          const nm = p.display_name || p.name;
          showBand.push({ key: `__own__${nm}`, label: nm, sub: who, kind: "own-only",
                          ownKey: nm, note: "his squad, on a show whose claims are not attributed to a host" });
        }
      } else {
        showBand.push({ key: who, label: who, sub: null, kind: "show", ownKey: null,
                        note: "no panel person is published for this show" });
      }
    }
    if (solo.length) bands.push({
      title: "One host, so what the show said is what that person said",
      rows: solo });
    if (showBand.length) bands.push({
      title: "Said by the show — this payload attributes no claim to a host, " +
             "so nothing here is any one person's opinion",
      rows: showBand });
    /* A note repeated on every row of a band is clutter, not honesty. Say it
       once in the band header and keep only the rows that differ. */
    for (const b of bands) {
      const counts = new Map();
      for (const r of b.rows) counts.set(r.note, (counts.get(r.note) || 0) + 1);
      const [common, n] = [...counts.entries()].sort((x, y) => y[1] - x[1])[0] || [];
      if (n > 1) { b.common = common; for (const r of b.rows) if (r.note === common) r.note = null; }
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

    /* ---- the honest header about what this view can and cannot show ---- */
    const lead = el("p", "cx-provline" + (own.size ? "" : " warn"));
    lead.appendChild(el("b", null, "Hue is what they SAID. Outline is what they OWN."));
    if (own.size) {
      lead.append(` The own channel is live from \`${ownSource}\`. ` +
        "A ring with no hue is a QUIET holding — a player somebody owns and " +
        "has never mentioned on air, which no transcript can ever surface. A " +
        "column of hue with no rings is talk with nobody's money behind it.");
    } else {
      lead.append(" The own channel is EMPTY for the panel in this payload: " +
        "`creator_board` is not yet publishing `panel_owned` or " +
        "`entry.people[].owned`, so no panel row can carry a ring. Only your " +
        "own row, pinned at the bottom, has a squad to draw. Seven of the " +
        "fifteen verified panel entries have a crawled GW1 squad; until the " +
        "panel serves them, this grid is half of itself — quiet holdings, the " +
        "thing this view exists for, cannot be shown. That is missing data, " +
        "not an absence of holdings.");
    }
    body.appendChild(lead);

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

    for (const band of bands) {
      const btr = el("tr", "cx-bandrow");
      const btd = el("td", "cx-band");
      btd.colSpan = codes.length + 1;
      btd.appendChild(el("span", "cx-bandt", band.title));
      if (band.common) btd.appendChild(el("span", "cx-bandnote", band.common));
      btr.appendChild(btd); tb.appendChild(btr);
      for (const R of band.rows) tb.appendChild(gridRow(R));
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
    body.appendChild(wrap);

    function gridRow(R) {
      const tr = el("tr", "cx-gridrow " + R.kind);
      const g = el("td", "cx-gutter");
      g.appendChild(el("b", null, R.label));
      if (R.sub) g.appendChild(el("span", "cx-gutsub", R.sub));
      if (R.note) g.appendChild(el("div", "cx-tiny", R.note));
      tr.appendChild(g);
      const m = said.get(R.key) || new Map();
      const ownSet = R.ownKey ? own.get(R.ownKey) : null;
      for (const code of codes) {
        const td = el("td");
        const cell = m.get(code);
        const owns = ownSet && ownSet.has(code);
        if (!cell && !owns) { tr.appendChild(td); continue; }
        const cls = ["cx-cell"];
        if (cell) {
          if (cell.in && cell.out) cls.push("split");
          else if (cell.in || cell.cap) cls.push("in");
          else if (cell.out) cls.push("out");
          else if (cell.watch) cls.push("watch");
          if (cell.cueOnly && !cell.watch) cls.push("cue");
        }
        if (owns) cls.push("owns");
        const s = el("span", cls.join(" "));
        const r = rowByCode.get(code);
        const bits = [];  // the cell's own tooltip: said, owned, and neither
        if (cell) {
          if (cell.in) bits.push("said IN");
          if (cell.out) bits.push("said OUT");
          if (cell.cap) bits.push("named captain");
          if (cell.watch) bits.push("watch call — an observation, not a buy");
          if (cell.cueOnly && !cell.watch) bits.push("keyword window only");
        } else bits.push("never mentioned him");
        bits.push(owns ? "OWNS him" : (ownSet ? "does not own him" : "squad not crawled"));
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
    lg.appendChild(it("in", "said IN (buy, hold or captain)"));
    lg.appendChild(it("out", "said OUT (sell, avoid or bench)"));
    lg.appendChild(it("split", "said both across the window"));
    lg.appendChild(it("watch", "a watch call — an observation, not a buy"));
    lg.appendChild(it("cue", "keyword window only"));
    lg.appendChild(it("owns", "OWNS him — a ring, never a hue"));
    lg.appendChild(it("in owns", "said it AND owns him"));
    const note = el("p", "sub");
    note.textContent =
      "A cell is hatched only when EVERY claim behind it in that direction is " +
      "a keyword window. Where a player's claims mix tiers, this payload does " +
      "not say which person is which, so the cell is drawn solid and the " +
      "drawer shows each claim's own tier. Ownership is a shape, never a hue, " +
      "so the two facts stay readable independently and neither depends on " +
      "colour vision.";
    lg.appendChild(note);
    if (TOTAL > codes.length || gridAll) {
      const b = el("button", "cx-more",
        gridAll ? `show only the 22 most-touched players`
                : `show all ${TOTAL} players (${TOTAL - codes.length} more)`);
      b.onclick = () => { gridAll = !gridAll; render(); };
      lg.appendChild(b);
    }
    lg.appendChild(el("p", "sub",
      `Columns are ordered by how many people touched the player — volume, ` +
      `not merit. Rows are grouped by what the payload can honestly say about ` +
      `who is speaking, not ranked.`));
    body.appendChild(lg);
    renderWatching();
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

  async function openPlayer(r) {
    drawer.textContent = "";
    drawer.classList.add("open");
    const head = el("div", "dhead");
    head.appendChild(faceImg(r.code, "bigface"));
    const id = el("div");
    id.appendChild(el("div", "dname", r.name));
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
    sec.appendChild(el("p", "sub",
      "Every claim below is fetched from the creator's own record and links " +
      "back to the episode it came from. Considered takes first, then keyword " +
      "windows; within each, newest first."));
    const list = el("div", "cx-quotes");
    list.appendChild(el("p", "sub", "reading the sources…"));
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
      list.appendChild(emptyBox(
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
      hd.appendChild(el("b", null, creator));
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
