/* Creators — a wire, not a directory.

   What this page IS: the FPL content world's positions, in their own words,
   ordered by *when they said it* and *how much they said*, never by fame.
   Three lenses over one payload:

     1. The wire       — newest voice first: their latest item, their
                         summarised take, and where the take is missing, the
                         reason it is missing.
     2. Agreement      — where creators pile onto the same player. Labelled
                         "popularity", because that is all it is.
     3. Track record   — the honest scoreboard. Every earned weight is 0.0,
                         so the page says "nobody has beaten a coin flip"
                         rather than showing a blank leaderboard.

   The one design rule everything else follows: **evidence strength is
   visible.** A semantic (`llm:`) claim carries a verbatim quote and a
   conviction band and is drawn as a real quotation. A `cue` claim is a
   keyword landing near a player's name — it is drawn recessive, hatched,
   and labelled "keyword match", so a search hit can never be mistaken for a
   considered opinion. Same for items: a `transcript` is the full thing, a
   `description` is 1.2KB of show notes and says so.

   Colour: two hues only, from the app's validated series ramp —
   s1 (#2a78d6) = buying/positive, s2 (#c25322 dark / #eb6834 light) =
   selling/negative. Validated all-pairs in BOTH modes against each theme's
   surface: CVD ΔE 25.9 (dark) / 24.7 (light), normal-vision ΔE 30.4 / 33.6,
   contrast >= 3:1. Captain is deliberately NOT a third hue (s2↔s4 fails the
   normal-vision floor at ΔE 11.4) — it is a neutral chip with a ★ glyph.
   Weak evidence is neutral grey + hatch, never a colour. */

import { runPanel, el, emptyBox, errBox, provenance, faceImg,
         fmtPrice, fmt1, fmt2 } from "/js/app.js";

/* ---------------- small helpers ---------------- */

const parseTs = iso => iso ? new Date(String(iso).replace(" ", "T")) : null;

function relAge(iso) {
  const d = parseTs(iso);
  if (!d || isNaN(d)) return { text: "unknown", cls: "bad", hours: Infinity };
  const h = (Date.now() - d) / 3.6e6;
  const text = h < 1 ? "just now"
    : h < 24 ? `${Math.round(h)}h ago`
    : h < 48 ? "yesterday"
    : h < 720 ? `${Math.round(h / 24)}d ago`
    : `${Math.round(h / 730)}mo ago`;
  const cls = h < 72 ? "good" : h < 336 ? "warn" : "bad";
  return { text, cls, hours: h };
}

function clock(s) {
  if (s == null || !isFinite(s)) return null;
  const t = Math.max(0, Math.round(s));
  const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), sec = t % 60;
  const pad = n => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

const KIND_GLYPH = { youtube: "▶", podcast: "🎙", article: "✎", blog: "✎" };
const kindGlyph = k => KIND_GLYPH[String(k || "").toLowerCase()] || "•";

/* Every action the extractor can emit, mapped onto the two-hue axis.
   `dir` drives colour; the label always ships as text beside it. */
const ACTIONS = {
  buy:            { label: "buy",      dir: "pos",  glyph: "▲" },
  sell:           { label: "sell",     dir: "neg",  glyph: "▼" },
  avoid:          { label: "avoid",    dir: "neg",  glyph: "⊘" },
  bench:          { label: "bench",    dir: "neg",  glyph: "▽" },
  hold:           { label: "hold",     dir: "flat", glyph: "=" },
  captain:        { label: "captain",  dir: "cap",  glyph: "★" },
  triple_captain: { label: "triple C", dir: "cap",  glyph: "★★" },
};
const actionMeta = a =>
  ACTIONS[a] || { label: String(a || "?"), dir: "flat", glyph: "•" };

/* transcript = the whole thing was read. description = show notes, roughly a
   kilobyte of marketing copy. The difference decides how much a take is
   worth, so it is a first-class badge and not a tooltip. */
const TEXT_SOURCE = {
  transcript:  { label: "full transcript", cls: "strong",
                 title: "the whole spoken text was read" },
  article:     { label: "article text", cls: "strong",
                 title: "the full written piece was read" },
  description: { label: "show notes only", cls: "weak",
                 title: "only the item's description was available — a " +
                        "summary drawn from it is weak evidence" },
};
const textSourceMeta = t =>
  TEXT_SOURCE[t] || { label: t || "unknown text", cls: "weak",
                      title: "unrecognised text source" };

/* `llm:*` = a model read the passage and returned a verbatim quote plus a
   conviction band. `cue` = a keyword landed inside a window near the player
   name. These are not the same thing and must never look the same. */
function extractorMeta(x) {
  const s = String(x || "");
  if (s.startsWith("llm")) {
    const model = s.slice(4) || "model";
    return { kind: "llm", label: "considered take", cls: "ev-llm",
             title: `read semantically by ${model} — verbatim quote attached` };
  }
  if (s === "cue") {
    return { kind: "cue", label: "keyword match", cls: "ev-cue",
             title: "a keyword landed near this player's name. It is a " +
                    "search hit, not a stated opinion — read the window " +
                    "before you trust it." };
  }
  return { kind: "other", label: s || "unknown source", cls: "ev-cue",
           title: "unrecognised extractor" };
}

const CONVICTION = { high: 3, medium: 2, low: 1 };

/* Conviction / confidence as a 3-pip meter. Never colour-alone: the band
   word sits beside it. */
function pips(filled, total, title) {
  const w = el("span", "cx-pips");
  w.title = title || "";
  for (let i = 0; i < total; i++)
    w.appendChild(el("i", i < filled ? "on" : ""));
  return w;
}

function convictionMeter(band) {
  const n = CONVICTION[String(band || "").toLowerCase()] || 0;
  const w = el("span", "cx-conv");
  w.appendChild(pips(n, 3, `conviction: ${band || "unstated"}`));
  w.appendChild(el("span", "cx-conv-t", band || "unstated"));
  return w;
}

function link(url, text, cls) {
  const a = el("a", cls, text);
  a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
  return a;
}

/* A reason a panel gave for having nothing. Rendered as an explanation, not
   as breakage: some of these ("transfers are public only after the
   deadline") describe the world working correctly. */
function reasonBox(reason, opts = {}) {
  const d = el("div", "cx-reason" + (opts.expected ? " expected" : ""));
  d.appendChild(el("span", "cx-reason-i", opts.expected ? "i" : "?"));
  const t = el("div");
  if (opts.label) t.appendChild(el("b", null, opts.label));
  t.appendChild(el("span", null, reason || "no reason given"));
  if (opts.hint) t.appendChild(el("div", "cx-reason-h", opts.hint));
  d.appendChild(t);
  return d;
}

/* Panels the server has not been given yet are a deployment state, not an
   exception — say so plainly instead of throwing a red HTTP box. */
async function panelSafe(script, params) {
  try {
    return await runPanel(script, params);
  } catch (e) {
    const msg = String(e && e.message || e);
    if (/no panel script named/.test(msg) || /HTTP 40[04]/.test(msg))
      return { notDeployed: true, script,
               error: `The \`${script}\` panel is not registered on this ` +
                      `server yet, so there is nothing to read. This view ` +
                      `renders as soon as the panel ships — no data is ` +
                      `cached or invented in the meantime.` };
    return { error: msg, hard: true };
  }
}

/* ---------------- the view ---------------- */

export default async function creators(host) {
  // ---- state ----
  let days = 30;
  let view = "wire";                  // wire | agreement | record
  let sort = "recent";                // recent | claims | items
  let kinds = new Set();              // empty = all
  let takesOnly = false;
  let search = "";
  let consSort = "net";               // net | buy | sell | captain | own
  let board = null, boardProv = null;
  // Why there is no board, kept so switching lens cannot wipe the
  // explanation off the screen and leave a blank card behind.
  let problem = null;                 // {kind:"empty"|"err", reason, hint}

  const card = el("section", "card cx");
  card.appendChild(el("h2", null, "Content creators"));
  card.appendChild(el("p", "sub",
    "Who is saying what, right now — in their own words. Every line below " +
    "is quoted from an ingested video, podcast or article; nothing here is " +
    "paraphrased by this app, and a creator with no summarised position " +
    "shows the reason instead of a blank. Ordering is by recency and " +
    "volume, never by reputation: see Track record for why authority is " +
    "not an available sort."));

  const winRow = el("div", "toolbar");
  const viewRow = el("div", "toolbar");
  const filterRow = el("div", "toolbar");
  const statRow = el("div", "stats");
  const body = el("div", "cx-body");
  const foot = el("div");
  card.append(winRow, viewRow, filterRow, statRow, body, foot);
  host.appendChild(card);

  // ---- drawer (own element; wider than the shared one) ----
  const scrim = el("div", "cx-scrim");
  const drawer = el("aside", "drawer cx-drawer");
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-label", "Creator evidence");
  document.body.append(scrim, drawer);
  const closeDrawer = () => {
    drawer.classList.remove("open"); scrim.classList.remove("on");
  };
  scrim.onclick = closeDrawer;
  const onKey = e => { if (e.key === "Escape") closeDrawer(); };
  addEventListener("keydown", onKey);
  // The router blows away #view on navigation; take our body-level nodes with
  // it so a second visit does not stack drawers.
  new MutationObserver((_, obs) => {
    if (!host.contains(card)) {
      drawer.remove(); scrim.remove();
      removeEventListener("keydown", onKey); obs.disconnect();
    }
  }).observe(host, { childList: true });

  /* ---------------- toolbars ---------------- */

  function renderWindow() {
    winRow.textContent = "";
    winRow.appendChild(el("span", "tlabel", "Window"));
    for (const d of [14, 30, 60, 90]) {
      const b = el("button", "chip gw" + (d === days ? " on" : ""), `${d} days`);
      b.title = `takes and claims published in the last ${d} days`;
      b.onclick = () => { days = d; load(); };
      winRow.appendChild(b);
    }
    if (board && board.as_of) {
      const a = relAge(board.as_of);
      const s = el("span", "sub cx-asof");
      s.appendChild(el("span", "freshdot " + a.cls));
      s.appendChild(document.createTextNode(
        ` snapshot ${a.text}` +
        (board.gw != null ? ` · takes are about GW${board.gw}`
                          : ` · ${board.gw_reason || "gameweek unknown"}`)));
      if (board.gw != null && board.gw_reason) s.title = board.gw_reason;
      winRow.appendChild(s);
    }
  }

  function renderViews() {
    viewRow.textContent = "";
    viewRow.appendChild(el("span", "tlabel", "Lens"));
    const seg = el("span", "seg");
    const opts = [
      ["wire", "The wire", "newest voice first — latest item and stated position"],
      ["agreement", "Agreement", "where creators land on the same player"],
      ["record", "Track record", "has any of them actually been right?"],
    ];
    for (const [k, label, title] of opts) {
      const b = el("button", k === view ? "on" : "", label);
      b.title = title;
      b.onclick = () => { view = k; renderViews(); renderFilters(); renderBody(); };
      seg.appendChild(b);
    }
    viewRow.appendChild(seg);
    const note = {
      wire: "Ordered by when they last published, not by who they are.",
      agreement: "Agreement is popularity. It is not evidence of being right.",
      record: "Scored against settled gameweeks, with the sample size shown.",
    }[view];
    viewRow.appendChild(el("span", "sub", note));
  }

  function renderFilters() {
    filterRow.textContent = "";
    if (view === "agreement") {
      filterRow.appendChild(el("span", "tlabel", "Rank by"));
      const seg = el("span", "seg");
      for (const [k, l, t] of [
        ["net", "net (buy − sell)", "the sort key the panel provides"],
        ["buy", "buyers", "how many creators said buy"],
        ["sell", "sellers", "how many said sell"],
        ["captain", "captaincy", "how many named him captain"],
        ["own", "ownership", "how much of the field already owns him"],
      ]) {
        const b = el("button", k === consSort ? "on" : "", l);
        b.title = t;
        b.onclick = () => { consSort = k; renderFilters(); renderBody(); };
        seg.appendChild(b);
      }
      filterRow.appendChild(seg);
      return;
    }
    if (view === "record") {
      filterRow.appendChild(el("span", "tlabel", "Scoring"));
      filterRow.appendChild(el("span", "sub",
        "A hit is a claim that a settled gameweek vindicated. Weight is only " +
        "earned when the Wilson 95% lower bound clears 0.50 at n ≥ 25 — " +
        "i.e. when we can say the creator beat a coin flip rather than got " +
        "lucky."));
      return;
    }
    filterRow.appendChild(el("span", "tlabel", "Sort & filter"));
    const seg = el("span", "seg");
    for (const [k, l, t] of [
      ["recent", "latest first", "most recently published"],
      ["claims", "most claims", "most player calls in the window"],
      ["items", "most output", "most items published in the window"],
    ]) {
      const b = el("button", k === sort ? "on" : "", l);
      b.title = t;
      b.onclick = () => { sort = k; renderFilters(); renderBody(); };
      seg.appendChild(b);
    }
    filterRow.appendChild(seg);

    const allKinds = [...new Set((board?.creators || [])
      .flatMap(c => c.kinds || []))].sort();
    for (const k of allKinds) {
      const on = kinds.has(k);
      const b = el("button", "chip src" + (on ? " on" : ""));
      b.append(document.createTextNode(`${on ? "✓ " : ""}${kindGlyph(k)} ${k}`));
      b.title = `show only creators publishing ${k}`;
      b.onclick = () => {
        on ? kinds.delete(k) : kinds.add(k); renderFilters(); renderBody();
      };
      filterRow.appendChild(b);
    }

    const t = el("button", "chip src" + (takesOnly ? " on" : ""),
                 (takesOnly ? "✓ " : "") + "summarised takes only");
    t.title = "hide creators whose latest item has not been summarised yet";
    t.onclick = () => { takesOnly = !takesOnly; renderFilters(); renderBody(); };
    filterRow.appendChild(t);

    const s = el("input");
    s.type = "search"; s.placeholder = "creator…"; s.size = 12; s.value = search;
    s.oninput = () => { search = s.value; renderBody(); };
    filterRow.appendChild(s);
  }

  /* ---------------- headline tiles ---------------- */

  function renderStats() {
    statRow.textContent = "";
    if (!board || !board.creators) return;
    const cs = board.creators;
    const withTake = cs.filter(c => c.take).length;
    const items = cs.reduce((a, c) => a + (c.n_items_window || 0), 0);
    const claims = cs.reduce((a, c) => a + (c.n_claims_window || 0), 0);
    const freshest = cs.map(c => parseTs(c.last_item_at))
      .filter(d => d && !isNaN(d)).sort((a, b) => b - a)[0];
    const earned = cs.filter(c => c.record && c.record.earned).length;

    const tile = (v, k, title, cls) => {
      const d = el("div", "stat" + (cls ? " " + cls : ""));
      d.appendChild(el("div", "v", v));
      d.appendChild(el("div", "k", k));
      if (title) d.title = title;
      return d;
    };
    statRow.append(
      tile(String(cs.length), "creators tracked",
           "every source the ingester currently follows"),
      tile(String(items), `items · ${days}d`,
           "videos, episodes and articles published in the window"),
      tile(String(claims), `player calls · ${days}d`,
           "individual buy/sell/captain claims extracted in the window"),
      tile(`${withTake}/${cs.length}`, "summarised",
           "creators whose latest item has a model-written position. The " +
           "rest show why not — usually because only show notes exist."),
      tile(freshest ? relAge(freshest.toISOString()).text : "–", "freshest",
           "most recent item across every creator"),
      tile(String(earned), "beat chance",
           "creators whose measured hit rate clears a coin flip with " +
           "enough sample to say so"),
    );
  }

  /* ---------------- lens 1: the wire ---------------- */

  function visibleCreators() {
    let cs = [...(board.creators || [])];
    if (kinds.size)
      cs = cs.filter(c => (c.kinds || []).some(k => kinds.has(k)));
    if (takesOnly) cs = cs.filter(c => c.take);
    const term = search.trim().toLowerCase();
    if (term) cs = cs.filter(c => String(c.creator).toLowerCase().includes(term));
    const key = {
      recent: c => parseTs(c.last_item_at)?.getTime() ?? -Infinity,
      claims: c => c.n_claims_window ?? -1,
      items:  c => c.n_items_window ?? -1,
    }[sort];
    return cs.sort((a, b) => key(b) - key(a));
  }

  /* One transfer/captain entry out of a take. Clickable by contract: either
     the panel gave us a real deep link, or we open the creator's evidence
     drawer focused on that player. We never build a URL ourselves. */
  const DIR_WORD = { pos: "transfer in", neg: "transfer out",
                     cap: "captain", diff: "differential" };
  const DIR_GLYPH = { pos: "▲", neg: "▼", cap: "★", diff: "◆" };

  function takeEntry(c, e, dir) {
    const wrap = el("span", "cx-callwrap");
    const n = el("button", "cx-call " + dir);
    n.appendChild(el("span", "g", DIR_GLYPH[dir] || "•"));
    if (e.code != null) n.appendChild(faceImg(e.code, "cx-face"));
    n.appendChild(el("span", "nm", e.name || "unnamed"));
    const band = String(e.conviction || "").toLowerCase();
    if (CONVICTION[band])
      n.appendChild(pips(CONVICTION[band], 3, `conviction: ${band}`));
    n.title = (e.quote ? `“${e.quote}”\n\n` : "") +
      `${DIR_WORD[dir] || dir} · conviction ${band || "unstated"}` +
      (e.start_s != null ? ` · said at ${clock(e.start_s)}` : "") +
      (e.code == null ? "\nthe spoken name did not resolve to one player" : "") +
      "\nclick for the evidence in context";
    n.onclick = () => openCreator(c.creator, e.code);
    wrap.appendChild(n);
    // The panel builds the timestamped link; we only ever follow it.
    if (e.deep_link) {
      const a = link(e.deep_link,
        e.start_s != null ? `▶ ${clock(e.start_s)}` : "▶ source", "cx-jump");
      a.title = e.start_s != null
        ? "open the source at the moment he said it"
        : "open the source item (no timestamp recorded)";
      wrap.appendChild(a);
    }
    return wrap;
  }

  function takeBlock(c) {
    const box = el("div", "cx-take");
    if (!c.take) {
      // Most creators are here today, so this row must stay one line and
      // still carry something specific: the raw call count, which differs
      // per creator and is the actual next click.
      const n = c.n_claims_window ?? 0;
      const line = el("div", "cx-nota");
      line.appendChild(reasonBox(
        c.take_reason || "no summarised position for this creator yet",
        { label: "No summarised take. " }));
      const b = el("button", "cx-more", n
        ? `Read ${n} raw call${n === 1 ? "" : "s"} →`
        : "Open everything held →");
      b.title = n
        ? "the extractor still pulled player calls out of these items — " +
          "read them with their evidence strength shown"
        : "no player calls were extracted in this window either; open to " +
          "see the items themselves";
      b.onclick = () => openCreator(c.creator);
      line.appendChild(b);
      box.appendChild(line);
      return box;
    }
    const t = c.take;
    if (t.summary || (t.summary_bullets || []).length) {
      const p = el("blockquote", "cx-summary");
      if (t.summary) p.appendChild(el("span", null, t.summary));
      if ((t.summary_bullets || []).length) {
        const ul = el("ul", "cx-bullets");
        for (const b of t.summary_bullets) ul.appendChild(el("li", null, b));
        p.appendChild(ul);
      }
      box.appendChild(p);
    }
    const groups = [
      ["pos", "In", t.transfers_in],
      ["neg", "Out", t.transfers_out],
      ["cap", "Captain", t.captain],
      ["diff", "Differentials", t.differentials],
    ];
    for (const [dir, label, arr] of groups) {
      if (!arr || !arr.length) continue;
      const row = el("div", "cx-calls");
      row.appendChild(el("span", "cx-calls-l", label));
      for (const e of arr) row.appendChild(takeEntry(c, e, dir));
      box.appendChild(row);
    }
    if (t.chips && t.chips.length) {
      const row = el("div", "cx-calls");
      row.appendChild(el("span", "cx-calls-l", "Chips"));
      for (const ch of t.chips) {
        const w = el("span", "cx-callwrap");
        const n = el("button", "cx-call flat");
        n.appendChild(el("span", "nm",
          `${ch.chip}${ch.horizon_gw != null ? ` · GW${ch.horizon_gw}` : ""}`));
        if (ch.stance) n.appendChild(el("span", "cx-stance", ch.stance));
        n.title = (ch.quote ? `“${ch.quote}”\n\n` : "") + "click for the evidence";
        n.onclick = () => openCreator(c.creator);
        w.appendChild(n);
        if (ch.deep_link)
          w.appendChild(link(ch.deep_link,
            ch.start_s != null ? `▶ ${clock(ch.start_s)}` : "▶ source", "cx-jump"));
        row.appendChild(w);
      }
      box.appendChild(row);
    }
    const has = (t.transfers_in || []).length + (t.transfers_out || []).length
              + (t.captain || []).length + (t.chips || []).length
              + (t.differentials || []).length;
    if (!has && !t.summary)
      box.appendChild(reasonBox(
        "the summary exists but named no players", { expected: true }));
    const f = el("div", "cx-take-f");
    f.appendChild(el("span", null,
      `summarised by ${t.model || "an unnamed model"} from the item above`));
    const b = el("button", "cx-more", "All evidence →");
    b.onclick = () => openCreator(c.creator);
    f.appendChild(b);
    box.appendChild(f);
    return box;
  }

  function wireRow(c) {
    const a = relAge(c.last_item_at);
    const art = el("article", "cx-wire");

    const rail = el("div", "cx-rail");
    rail.appendChild(el("span", "freshdot " + a.cls));
    rail.appendChild(el("span", "cx-rail-t", a.text));
    art.appendChild(rail);

    const main = el("div", "cx-main");

    const head = el("div", "cx-head");
    const nameBtn = el("button", "cx-name", c.creator);
    nameBtn.title = "open every item and claim we hold for this creator";
    nameBtn.onclick = () => openCreator(c.creator);
    head.appendChild(nameBtn);
    for (const k of c.kinds || [])
      head.appendChild(el("span", "cx-kind", `${kindGlyph(k)} ${k}`));
    const counts = el("span", "cx-counts");
    counts.title = `${c.n_items ?? "?"} items held in total`;
    counts.textContent =
      `${c.n_items_window ?? 0} item${c.n_items_window === 1 ? "" : "s"}` +
      ` · ${c.n_claims_window ?? 0} call${c.n_claims_window === 1 ? "" : "s"}` +
      ` in ${days}d`;
    head.appendChild(counts);
    main.appendChild(head);

    if (c.latest) {
      const it = c.latest;
      const line = el("div", "cx-latest");
      line.appendChild(el("span", "cx-glyph", kindGlyph(it.kind)));
      line.appendChild(it.url
        ? link(it.url, it.title || "(untitled item)", "cx-title")
        : el("span", "cx-title", it.title || "(untitled item)"));
      const ts = textSourceMeta(it.text_source);
      const badge = el("span", "cx-src " + ts.cls, ts.label);
      badge.title = ts.title;
      line.appendChild(badge);
      const pub = relAge(it.published_at);
      const p = el("span", "cx-when", pub.text);
      p.title = it.published_at || "";
      line.appendChild(p);
      main.appendChild(line);
    } else {
      main.appendChild(reasonBox(c.latest_reason ||
        "no item stored for this creator in any window", { expected: false }));
    }

    main.appendChild(takeBlock(c));
    art.appendChild(main);

    const side = el("div", "cx-side");
    side.appendChild(recordBadge(c.record));
    side.appendChild(entryBadge(c));
    art.appendChild(side);
    return art;
  }

  function recordBadge(r) {
    const d = el("div", "cx-badge");
    d.appendChild(el("div", "k", "Track record"));
    if (!r || r.scored == null) {
      d.appendChild(el("div", "v muted", "unmeasured"));
      if (r && r.reason) d.appendChild(el("div", "cx-badge-r", r.reason));
      d.title = (r && r.reason)
        || "no settled gameweek has scored this creator's calls yet";
      return d;
    }
    d.appendChild(el("div", "v",
      `${r.hits ?? "?"}/${r.scored} · ${r.hit_rate == null ? "–" : Math.round(r.hit_rate * 100) + "%"}`));
    const st = el("span", r.earned ? "chip good" : "chip",
                  r.earned ? "earned weight " + fmt2(r.weight)
                           : "not above chance");
    st.title = r.earned
      ? `Wilson 95% lower bound ${fmt2(r.wilson_lo95)} clears 0.50 at n=${r.scored}`
      : `Wilson 95% lower bound ${r.wilson_lo95 == null ? "–" : fmt2(r.wilson_lo95)} ` +
        `at n=${r.scored}: not enough evidence to say this beats a coin flip, ` +
        `so the engine gives it zero weight.`;
    d.appendChild(st);
    return d;
  }

  function entryBadge(c) {
    const d = el("div", "cx-badge");
    d.appendChild(el("div", "k", "Their team"));
    if (c.entry) {
      const e = c.entry;
      const v = el("div", "v");
      v.appendChild(e.source_url
        ? link(e.source_url, e.name || `entry ${e.entry_id}`)
        : el("span", null, e.name || `entry ${e.entry_id}`));
      d.appendChild(v);
      d.appendChild(el("span", e.verified ? "chip good" : "chip warn",
                       e.verified ? "verified id" : "unverified id"));
    } else {
      d.appendChild(el("div", "v muted", "not published"));
      d.appendChild(el("div", "cx-badge-r",
        c.entry_reason || "no entry id found"));
    }
    return d;
  }

  function renderWire() {
    const cs = visibleCreators();
    if (!cs.length) {
      body.appendChild(emptyBox(
        "No creator matches these filters.",
        "Clear the kind chips or the search box."));
      return;
    }
    const list = el("div", "cx-list");
    for (const c of cs) list.appendChild(wireRow(c));
    body.appendChild(list);
    body.appendChild(el("p", "sub",
      `${cs.length} of ${board.creators.length} creators · ` +
      `every name, quote and timestamp comes from a stored row — where a ` +
      `position is missing you are reading the panel's own reason for it, ` +
      `not a placeholder.`));
  }

  /* ---------------- lens 2: agreement ---------------- */

  function creatorChips(names, dir) {
    const w = el("span", "cx-who");
    for (const n of names || []) {
      const b = el("button", "cx-whochip " + dir, n);
      b.title = `open ${n}'s evidence`;
      b.onclick = () => openCreator(n);
      w.appendChild(b);
    }
    return w;
  }

  function renderAgreement() {
    const rows = board.consensus || [];
    if (!rows.length) {
      body.appendChild(emptyBox(
        "No player has been named by a creator in this window.",
        "Widen the window, or wait for the next round of uploads."));
      return;
    }
    body.appendChild(reasonBox(
      "This board counts how many creators landed on the same player. It is " +
      "a popularity measure and nothing more — no creator on this page has " +
      "yet been shown to beat chance, so five people agreeing is five " +
      "people agreeing, not a signal. Its use is knowing what the field " +
      "will do, which moves prices and ownership.",
      { expected: true, label: "Read this as crowd behaviour. " }));

    const key = {
      net: r => r.net ?? 0,
      buy: r => r.buy?.n ?? 0,
      sell: r => r.sell?.n ?? 0,
      captain: r => r.captain?.n ?? 0,
      own: r => r.own_pct ?? -1,
    }[consSort];
    const sorted = [...rows].sort((a, b) => key(b) - key(a));
    const maxSide = Math.max(1, ...rows.flatMap(
      r => [r.buy?.n ?? 0, r.sell?.n ?? 0]));

    const list = el("div", "cx-cons");
    for (const r of sorted) {
      const row = el("div", "cx-consrow");

      const who = el("div", "cx-consid");
      if (r.code != null) who.appendChild(faceImg(r.code, "avatar"));
      const idt = el("div");
      idt.appendChild(el("div", "nm", r.name || "unnamed"));
      idt.appendChild(el("div", "sub", [
        r.pos, r.team, r.price != null ? fmtPrice(r.price) : null,
      ].filter(Boolean).join(" · ")));
      if (r.own_pct != null)
        idt.appendChild(el("div", "sub", `${fmt1(r.own_pct)}% owned`));
      who.appendChild(idt);
      row.appendChild(who);

      /* Diverging bar: one axis, neutral midpoint, same scale both sides.
         Each side is split solid = considered (llm) / hatched = keyword
         (cue), so a pile of five that is really four search hits cannot
         look like four opinions. */
      const nb = r.buy?.n ?? 0, ns = r.sell?.n ?? 0;
      const seg = (side, cls, host) => {
        const n = side?.n ?? 0;
        if (!n) return;
        const llm = side.n_llm, cue = side.n_cue;
        const parts = (llm == null && cue == null)
          ? [[n, ""]] : [[llm ?? 0, ""], [cue ?? 0, " cue"]];
        for (const [v, suffix] of parts) {
          if (!v) continue;
          const i = el("i", cls + suffix);
          i.style.width = `${(v / maxSide) * 100}%`;
          host.appendChild(i);
        }
      };
      const bar = el("div", "cx-div");
      const lft = el("div", "l"), mid = el("div", "m"), rgt = el("div", "r");
      seg(r.sell, "neg", lft);       // sell renders right-aligned by the flex
      seg(r.buy, "pos", rgt);
      bar.append(lft, mid, rgt);
      const split = s => (s?.n_llm == null && s?.n_cue == null) ? ""
        : ` (${s.n_llm ?? 0} considered, ${s.n_cue ?? 0} keyword)`;
      bar.title = `${nb} buy${split(r.buy)} · ${ns} sell${split(r.sell)}` +
        ` · net ${r.net ?? nb - ns}` +
        "\nsolid = considered take, hatched = keyword match";
      row.appendChild(bar);

      const nums = el("div", "cx-consn");
      const cell = (side, label, cls) => {
        const n = side?.n ?? 0;
        const d = el("div", "cx-cell " + cls);
        d.appendChild(el("b", null, String(n)));
        d.appendChild(el("span", null, label));
        if (n && side.n_llm != null)
          d.appendChild(el("em", null, side.n_llm
            ? `${side.n_llm} considered` : "keyword only"));
        d.title = ((side?.creators || []).join(", ") || "nobody") + split(side);
        return d;
      };
      nums.append(
        cell(r.sell, "sell", "neg"),
        cell(r.buy, "buy", "pos"),
        cell(r.captain, "★ captain", "cap"));
      row.appendChild(nums);

      const whoWrap = el("div", "cx-whowrap");
      const groups = [["pos", "buy", r.buy], ["neg", "sell", r.sell],
                      ["cap", "captain", r.captain]];
      for (const [dir, label, g] of groups) {
        if (!g || !g.creators || !g.creators.length) continue;
        const line = el("div", "cx-whoLine");
        line.appendChild(el("span", "cx-whoLabel", label));
        line.appendChild(creatorChips(g.creators, dir));
        whoWrap.appendChild(line);
      }
      row.appendChild(whoWrap);
      list.appendChild(row);
    }
    body.appendChild(list);
    const legend = el("p", "sub cx-legend");
    legend.append(
      "Bar is one axis: sell left, buy right, both to the same scale. ");
    legend.appendChild(el("i", "lg pos"));
    legend.append(" solid = a considered take · ");
    legend.appendChild(el("i", "lg pos cue"));
    legend.append(" hatched = a keyword match, which is a search hit and " +
      "not a stated opinion. Captaincy is counted, never coloured — it is a " +
      "different question from whether to own him. Click any creator to " +
      "read what they actually said.");
    body.appendChild(legend);
  }

  /* ---------------- lens 3: track record ---------------- */

  function renderRecord() {
    const cs = (board.creators || []).filter(c => c.record);
    const earned = cs.filter(c => c.record.earned);
    const scored = cs.filter(c => (c.record.scored ?? 0) > 0);

    const headline = earned.length
      ? `${earned.length} creator${earned.length === 1 ? " has" : "s have"} beaten chance`
      : "No creator has beaten a coin flip yet";
    // The panel writes its own note and may open with the same sentence as
    // the headline; say it once.
    const norm = s => s.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
    let note = board.record_note || "";
    const firstStop = note.indexOf(". ");
    if (firstStop > 0 && norm(note.slice(0, firstStop)) === norm(headline))
      note = note.slice(firstStop + 2);

    const head = el("div", "cx-verdict" + (earned.length ? " ok" : ""));
    head.appendChild(el("div", "h", headline));
    head.appendChild(el("div", "b", note || (earned.length
      ? "Weight is earned, not assumed. Everyone else still counts for zero."
      : "This is a measured result, not a missing one. Every creator here " +
        "has been scored against settled gameweeks; not one has a Wilson " +
        "95% lower bound above 0.50 at n ≥ 25, which is the bar for saying " +
        "\"better than guessing\" rather than \"lucky so far\". Until one " +
        "clears it, the engine weights every creator at 0.0 — their takes " +
        "are context, never inputs.")));
    body.appendChild(head);

    if (!cs.length) {
      body.appendChild(emptyBox(
        "The panel returned no record object for any creator.",
        "Scoring runs after a gameweek settles."));
      return;
    }

    const rows = [...cs].sort((a, b) =>
      (b.record.wilson_lo95 ?? -1) - (a.record.wilson_lo95 ?? -1)
      || (b.record.scored ?? 0) - (a.record.scored ?? 0));

    const wrap = el("div", "scroll-x");
    const t = el("table", "data");
    const thead = el("thead"), hr = el("tr");
    for (const [l, num, title] of [
      ["creator", 0, ""],
      ["calls scored", 1, "claims a settled gameweek could judge"],
      ["hits", 1, "claims the gameweek vindicated"],
      ["hit rate", 1, "hits ÷ scored — the raw, unpenalised number"],
      ["lower bound", 1, "Wilson 95% lower bound: the pessimistic hit rate " +
                         "once sample size is punished. This is the number " +
                         "that has to clear 0.50."],
      ["vs coin flip", 0, "where the lower bound sits against 0.50"],
      ["weight", 1, "what the engine actually gives this creator"],
    ]) {
      const th = el("th", num ? "num" : "", l);
      if (title) th.title = title;
      hr.appendChild(th);
    }
    thead.appendChild(hr); t.appendChild(thead);
    const tb = el("tbody");
    for (const c of rows) {
      const r = c.record;
      const tr = el("tr");
      const nameTd = el("td");
      const nb = el("button", "cx-linkish", c.creator);
      nb.onclick = () => openCreator(c.creator);
      nameTd.appendChild(nb);
      tr.appendChild(nameTd);
      tr.appendChild(el("td", "num", r.scored == null ? "–" : String(r.scored)));
      tr.appendChild(el("td", "num", r.hits == null ? "–" : String(r.hits)));
      tr.appendChild(el("td", "num",
        r.hit_rate == null ? "–" : `${Math.round(r.hit_rate * 100)}%`));
      tr.appendChild(el("td", "num",
        r.wilson_lo95 == null ? "–" : fmt2(r.wilson_lo95)));

      // A single 0..1 track with the 0.50 bar marked. One hue, plus the
      // number itself in the previous column — never colour alone.
      const gapTd = el("td");
      if (r.wilson_lo95 == null) gapTd.textContent = "not scored";
      else {
        const g = el("div", "cx-coin");
        const fill = el("i");
        fill.style.width = `${Math.max(1, Math.min(100, r.wilson_lo95 * 100))}%`;
        if (r.wilson_lo95 >= 0.5) fill.classList.add("over");
        g.appendChild(fill);
        g.appendChild(el("u", null, ""));   // the 0.50 tick
        gapTd.appendChild(g);
        gapTd.appendChild(el("span", "cx-coin-t",
          r.wilson_lo95 >= 0.5 ? "above 0.50" :
          `${fmt2(0.5 - r.wilson_lo95)} short`));
        gapTd.title = `Wilson lower bound ${fmt2(r.wilson_lo95)} against the ` +
                      `0.50 coin-flip line, n=${r.scored ?? "?"}`;
      }
      tr.appendChild(gapTd);
      const wTd = el("td", "num");
      wTd.appendChild(el("span", r.earned ? "chip good" : "chip",
                          fmt2(r.weight ?? 0)));
      tr.appendChild(wTd);
      tb.appendChild(tr);
    }
    t.appendChild(tb); wrap.appendChild(t);
    body.appendChild(wrap);
    body.appendChild(el("p", "sub",
      `${scored.length} of ${cs.length} creators have any scored calls at ` +
      `all. The rest are unmeasured — which is different from bad, and the ` +
      `table says so rather than sorting them to the bottom as zeros.`));
  }

  /* ---------------- the evidence drawer ---------------- */

  let evFilter = "all";     // all | llm | cue
  let drawerCreator = null, drawerFocus = null, drawerData = null;

  async function openCreator(name, focusCode) {
    drawerCreator = name; drawerFocus = focusCode ?? null;
    drawer.textContent = "";
    drawer.classList.add("open"); scrim.classList.add("on");
    drawer.appendChild(drawerHead(name, null));
    drawer.appendChild(el("p", "sub", "loading evidence…"));
    const res = await panelSafe("creator_detail",
      { creator: name, days: Math.max(days, 60), limit: 40 });
    if (drawerCreator !== name) return;      // a newer click won
    drawer.textContent = "";
    drawer.appendChild(drawerHead(name, res.provenance));
    if (res.notDeployed) {
      drawer.appendChild(emptyBox(res.error));
      return;
    }
    if (res.error) { drawer.appendChild(errBox(new Error(res.error))); return; }
    const d = res.result || {};
    if (d.empty) {
      drawer.appendChild(emptyBox(d.reason ||
        "the panel returned nothing for this creator"));
      return;
    }
    drawerData = d;
    renderDrawerBody();
  }

  function drawerHead(name, prov) {
    const h = el("div", "cx-dhead");
    const id = el("div");
    id.appendChild(el("div", "cx-dname", name));
    id.appendChild(el("div", "sub",
      "everything stored for this creator — item by item, quote by quote"));
    h.appendChild(id);
    const x = el("button", null, "✕");
    x.title = "close (Esc)";
    x.onclick = closeDrawer;
    h.appendChild(x);
    const wrap = el("div");
    wrap.appendChild(h);
    if (prov) wrap.appendChild(provenance(prov));
    return wrap;
  }

  function renderDrawerBody() {
    const d = drawerData;
    // clear everything after the head
    while (drawer.children.length > 1) drawer.lastChild.remove();

    if (d.record) {
      const rb = el("div", "cx-drecord");
      rb.appendChild(recordBadge(d.record));
      drawer.appendChild(rb);
    }

    // --- their team ---
    const teamSec = el("section", "cx-dsec");
    teamSec.appendChild(el("h3", null, "Their team"));
    if (d.entry) {
      const p = el("div", "cx-entry");
      p.appendChild(d.entry.source_url
        ? link(d.entry.source_url, d.entry.name || `entry ${d.entry.entry_id}`)
        : el("b", null, d.entry.name || `entry ${d.entry.entry_id}`));
      p.appendChild(el("span", d.entry.verified ? "chip good" : "chip warn",
                        d.entry.verified ? "verified" : "unverified"));
      teamSec.appendChild(p);
    } else {
      teamSec.appendChild(reasonBox(
        d.entry_reason || "no published entry id", { expected: true }));
    }
    if (d.squad && d.squad.length) {
      const grid = el("div", "cx-squad");
      for (const p of d.squad) {
        const cardEl = el("div", "cx-sq" + (p.is_captain ? " cap" : ""));
        if (p.code != null) cardEl.appendChild(faceImg(p.code, "cx-face"));
        cardEl.appendChild(el("span", "nm", p.name));
        cardEl.appendChild(el("span", "sub",
          [p.pos, p.price != null ? fmtPrice(p.price) : null,
           p.multiplier > 1 ? `×${p.multiplier}` : null]
            .filter(Boolean).join(" · ")));
        grid.appendChild(cardEl);
      }
      teamSec.appendChild(grid);
    } else {
      teamSec.appendChild(reasonBox(
        d.squad_reason || "no picks stored", { expected: true }));
    }
    drawer.appendChild(teamSec);

    // --- transfers ---
    const trSec = el("section", "cx-dsec");
    trSec.appendChild(el("h3", null, "Their transfers"));
    if (d.transfers && d.transfers.length) {
      const wrap = el("div", "scroll-x");
      const t = el("table", "data");
      const thead = el("thead"), hr = el("tr");
      for (const l of ["gw", "out", "in", "when"])
        hr.appendChild(el("th", null, l));
      thead.appendChild(hr); t.appendChild(thead);
      const tb = el("tbody");
      for (const x of d.transfers) {
        const tr = el("tr");
        tr.appendChild(el("td", null, `GW${x.gw}`));
        const o = el("td");
        if (x.out_code != null) o.appendChild(faceImg(x.out_code, "avatar"));
        o.appendChild(document.createTextNode(x.out_name || "–"));
        tr.appendChild(o);
        const i = el("td");
        if (x.in_code != null) i.appendChild(faceImg(x.in_code, "avatar"));
        i.appendChild(document.createTextNode(x.in_name || "–"));
        tr.appendChild(i);
        tr.appendChild(el("td", null, relAge(x.time_utc).text));
        tb.appendChild(tr);
      }
      t.appendChild(tb); wrap.appendChild(t); trSec.appendChild(wrap);
    } else {
      trSec.appendChild(reasonBox(
        d.transfers_reason ||
        "a gameweek's transfers become public only after its deadline",
        { expected: true, label: "Nothing to show yet, and that is correct. ",
          hint: "This is how the FPL API works, not a gap in ingestion — " +
                "moves appear here the moment the next deadline passes." }));
    }
    drawer.appendChild(trSec);

    // --- evidence ---
    const evSec = el("section", "cx-dsec");
    const evHead = el("div", "cx-evhead");
    evHead.appendChild(el("h3", null, "What they actually said"));
    const seg = el("span", "seg");
    const counts = { all: 0, llm: 0, cue: 0 };
    for (const it of d.items || [])
      for (const cl of it.claims || []) {
        counts.all++;
        counts[extractorMeta(cl.extractor).kind === "llm" ? "llm" : "cue"]++;
      }
    for (const [k, l] of [["all", `all ${counts.all}`],
                          ["llm", `considered ${counts.llm}`],
                          ["cue", `keyword ${counts.cue}`]]) {
      const b = el("button", k === evFilter ? "on" : "", l);
      b.title = k === "llm"
        ? "a model read the passage and returned a verbatim quote"
        : k === "cue"
          ? "a keyword landed near the player's name — weak evidence"
          : "everything held for this creator";
      b.onclick = () => { evFilter = k; renderDrawerBody(); };
      seg.appendChild(b);
    }
    evHead.appendChild(seg);
    evSec.appendChild(evHead);

    if (counts.all && counts.cue) {
      const mixWrap = el("div", "cx-mix");
      const bar = el("div", "cx-mixbar");
      const a = el("i", "considered");
      a.style.width = `${counts.llm / counts.all * 100}%`;
      const b = el("i", "keyword");
      b.style.width = `${counts.cue / counts.all * 100}%`;
      bar.append(a, b);
      mixWrap.appendChild(bar);
      mixWrap.appendChild(el("div", "cx-mixt",
        `${counts.llm} considered take${counts.llm === 1 ? "" : "s"} · ` +
        `${counts.cue} keyword match${counts.cue === 1 ? "" : "es"} — the ` +
        `hatched share is search noise until you read the window.`));
      evSec.appendChild(mixWrap);
    }

    const items = (d.items || []).filter(it => {
      if (evFilter === "all") return true;
      return (it.claims || []).some(
        cl => (extractorMeta(cl.extractor).kind === "llm") === (evFilter === "llm"));
    });
    if (!items.length) {
      evSec.appendChild(emptyBox(
        evFilter === "all"
          ? "No stored items for this creator in the window."
          : `No ${evFilter === "llm" ? "considered" : "keyword"} evidence in ` +
            `this creator's stored items.`));
    }
    for (const it of items) evSec.appendChild(itemBlock(it));
    drawer.appendChild(evSec);

    if (drawerFocus != null) {
      const target = drawer.querySelector(`[data-code="${drawerFocus}"]`);
      if (target) {
        target.classList.add("focused");
        target.scrollIntoView({ block: "center", behavior: "smooth" });
      }
      // one-shot: switching the evidence filter should not yank the scroll
      drawerFocus = null;
    }
  }

  function itemBlock(it) {
    const box = el("article", "cx-item");
    const head = el("div", "cx-itemhead");
    head.appendChild(el("span", "cx-glyph", kindGlyph(it.kind)));
    head.appendChild(it.url
      ? link(it.url, it.title || "(untitled)", "cx-title")
      : el("span", "cx-title", it.title || "(untitled)"));
    const ts = textSourceMeta(it.text_source);
    const badge = el("span", "cx-src " + ts.cls, ts.label);
    badge.title = ts.title;
    head.appendChild(badge);
    head.appendChild(el("span", "cx-when", relAge(it.published_at).text));
    box.appendChild(head);

    if (it.analysis && (it.analysis.summary
                        || (it.analysis.summary_bullets || []).length)) {
      const q = el("blockquote", "cx-summary");
      if (it.analysis.summary)
        q.appendChild(el("span", null, it.analysis.summary));
      if ((it.analysis.summary_bullets || []).length) {
        const ul = el("ul", "cx-bullets");
        for (const b of it.analysis.summary_bullets)
          ul.appendChild(el("li", null, b));
        q.appendChild(ul);
      }
      box.appendChild(q);
      box.appendChild(el("div", "cx-take-f",
        `summarised by ${it.analysis.model || "an unnamed model"}`));
    } else if (it.analysis_reason) {
      box.appendChild(reasonBox(it.analysis_reason,
        { expected: true, label: "Not summarised. " }));
    }

    const claims = (it.claims || []).filter(cl =>
      evFilter === "all" ||
      (extractorMeta(cl.extractor).kind === "llm") === (evFilter === "llm"));
    if (!claims.length) {
      box.appendChild(el("div", "cx-noclaims",
        it.claims && it.claims.length
          ? "no claims of the selected kind in this item"
          : "no player claims were extracted from this item"));
      return box;
    }
    // Considered takes first: strong evidence should not sit under noise.
    claims.sort((a, b) => {
      const ka = extractorMeta(a.extractor).kind === "llm" ? 0 : 1;
      const kb = extractorMeta(b.extractor).kind === "llm" ? 0 : 1;
      return ka - kb || (b.confidence ?? 0) - (a.confidence ?? 0);
    });
    for (const cl of claims) box.appendChild(claimBlock(cl));
    return box;
  }

  function claimBlock(cl) {
    const ev = extractorMeta(cl.extractor);
    const act = actionMeta(cl.action);
    const row = el("div", `cx-claim ${ev.cls} dir-${act.dir}`);
    if (cl.code != null) row.dataset.code = String(cl.code);

    const top = el("div", "cx-claimtop");
    if (cl.code != null) top.appendChild(faceImg(cl.code, "avatar"));
    top.appendChild(el("span", "nm", cl.name || "unnamed"));
    const pill = el("span", `cx-act ${act.dir}`);
    pill.appendChild(el("span", "g", act.glyph));
    pill.appendChild(el("span", null, act.label));
    top.appendChild(pill);

    const evb = el("span", "cx-ev " + ev.cls, ev.label);
    evb.title = ev.title;
    top.appendChild(evb);

    if (cl.confidence != null) {
      const c = el("span", "cx-conf");
      c.appendChild(pips(Math.max(1, Math.round(cl.confidence * 3)), 3,
        `extractor confidence ${fmt2(cl.confidence)}`));
      c.appendChild(el("span", "cx-conv-t", fmt2(cl.confidence)));
      top.appendChild(c);
    }
    row.appendChild(top);

    if (cl.quote) {
      const q = el("blockquote", "cx-quote " + ev.cls);
      q.appendChild(el("span", null, cl.quote));
      row.appendChild(q);
    } else {
      row.appendChild(el("div", "cx-noquote",
        "no passage was stored for this claim"));
    }

    const foot = el("div", "cx-claimfoot");
    if (cl.deep_link) {
      const a = link(cl.deep_link,
        cl.start_s != null ? `▶ open at ${clock(cl.start_s)}` : "▶ open the source",
        "cx-deep");
      a.title = cl.start_s != null
        ? "opens the source at the moment this was said"
        : "opens the source item (no timestamp was recorded)";
      foot.appendChild(a);
    } else {
      foot.appendChild(el("span", "cx-nodeep",
        "no link recorded for this passage"));
    }
    if (ev.kind === "cue")
      foot.appendChild(el("span", "cx-warnline",
        "keyword window — verify before acting"));
    row.appendChild(foot);
    return row;
  }

  /* ---------------- body switch + load ---------------- */

  function renderBody() {
    body.textContent = "";
    if (!board) {
      if (problem)
        body.appendChild(problem.kind === "err"
          ? errBox(new Error(problem.reason))
          : emptyBox(problem.reason, problem.hint));
      return;
    }
    if (view === "wire") renderWire();
    else if (view === "agreement") renderAgreement();
    else renderRecord();
  }

  async function load() {
    board = null; problem = null;
    renderWindow(); renderViews(); renderFilters();
    statRow.textContent = "";
    body.textContent = "";
    body.appendChild(el("p", "sub", "loading…"));
    foot.textContent = "";

    const res = await panelSafe("creator_board", { days });
    if (res.notDeployed) {
      problem = { kind: "empty", reason: res.error,
        hint: "Nothing on this page is cached or stubbed: when the panel " +
              "registers, this view fills in from it and only from it." };
      renderBody(); return;
    }
    if (res.error) { problem = { kind: "err", reason: res.error }; renderBody(); return; }
    const r = res.result || {};
    boardProv = res.provenance;
    foot.appendChild(provenance(boardProv));
    if (r.empty) {
      problem = { kind: "empty",
        reason: r.reason || "the creator panel returned nothing for this window",
        hint: "Try a wider window — the chips above refetch." };
      renderWindow(); renderBody(); return;
    }
    board = r;
    if (r.window_days && r.window_days !== days) days = r.window_days;
    renderWindow(); renderViews(); renderFilters(); renderStats(); renderBody();
  }

  await load();
}
