/* chatter.js — the cross-tab player strip.
 *
 * "Every data must connect with everything — so data must be highly
 * accessible across the different tabs." Creator content lived in one tab;
 * this component is the seam that puts it wherever a player is in focus.
 * One panel call (`player_chatter`), one node, mountable in any drawer.
 *
 * THE ORDERING IS THE DESIGN — DID → SAID → NOTICED:
 *   DID     `owned`   — which panel members actually HOLD him. Verified fact,
 *                       so it leads. Empty means "we have not crawled their
 *                       squads" (7 of 15 verified entries, GW1 only), NEVER
 *                       "nobody owns him" — `owned_reason` says which.
 *   SAID    `said`    — creator claims. The aggregate creator record is below
 *                       chance, so this section never implies authority: no
 *                       net, no consensus score, no "5 agree" verdict. A
 *                       `watch` call is an OBSERVATION and is never rendered
 *                       as a recommendation (that bug shipped once and put
 *                       buys in people's mouths they never made).
 *   NOTICED `noticed` — intel_item. MEASURED, not spoken, and never merged
 *                       into the same feed as SAID: 🗣 opinion vs ⚙ computed.
 *
 * THE MODAL STATE IS SILENCE. Creator claims cover 119 of 614 players, so
 * roughly four drawers in five have nothing said. The three rails are
 * therefore ALWAYS rendered, with the payload's own reason on the quiet ones:
 * the strip has the same skeleton every time, so silence reads as a measured
 * answer rather than as breakage.
 *
 * Zero-build: ES module, no libraries, shared helpers from /js/app.js only.
 * Every colour is a token from app.css, so both themes come for free.
 */

import { runPanel, el } from "/js/app.js";

/* ---------------- size budget (chosen, and kept) ----------------
   This renders inside a 460px drawer that already has content above it, so
   the budget is stated in HEIGHT and enforced as a CARD COUNT:
     quiet strip   ≤ 200px  — the modal case, ~4 drawers in 5 (measured 190)
     collapsed     ≤ 500px  — THREE cards total across SAID + NOTICED, never
                              three per section, because a card is ~75–110px
                              and per-section limits blow the budget on the
                              handful of players who have both
     expanded      6 + 6, then a printed "+N more". The DRAWER scrolls; the
                   strip never opens a second scroll context
     quote clamp   2 lines, expand in place on click; reasons clamp the same
                   way — clamped, never edited
   Measured in the 460px drawer: quiet 190px, intel-only 377px, a Haaland-tier
   player with owners + statements + intel 497px.
   The three cards go to SAID first, but NOTICED always keeps at least one
   when it has something — it is the only populated section for most players
   with any content at all, which is exactly why it earns its place. */
const CARDS = 3;
const FULL  = { said: 6, noticed: 6 };
function compactLimits(d) {
  const s = (d.said || []).length, n = (d.noticed || []).length;
  const said = Math.min(s, s && n ? CARDS - 1 : CARDS);
  return { said, noticed: Math.min(n, Math.max(n ? 1 : 0, CARDS - said)) };
}

/* ---------------- session cache ----------------
   One fetch per (code, days) for the life of the page. A failure is NOT
   cached: the panel is being built right now, and a drawer opened after it
   registers must pick it up without a reload. */
const cache = new Map();

export function fetchChatter(code, days = 30) {
  const key = `${code}:${days}`;
  if (!cache.has(key)) {
    const p = runPanel("player_chatter", { code: Number(code), days })
      .then(r => ({ ok: true, data: r.result ?? r, prov: r.provenance }))
      .catch(e => {
        cache.delete(key);                       // retry on the next open
        const m = String(e && e.message || e);
        return /no panel script named|HTTP 404/.test(m)
          ? { ok: false, down: true }
          : { ok: false, error: m };
      });
    cache.set(key, p);
  }
  return cache.get(key);
}
export function prefetchChatter(codes, days = 30) {
  for (const c of [].concat(codes)) fetchChatter(c, days);
}
export function clearChatterCache() { cache.clear(); }

/* ---------------- small helpers ---------------- */
const T = iso => new Date(String(iso).replace(" ", "T"));
function ago(iso) {
  if (!iso) return "";
  const h = (Date.now() - T(iso)) / 3.6e6;
  if (!isFinite(h)) return "";
  if (h < 1) return "just now";
  if (h < 36) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
}
function hms(s) {
  const t = Math.max(0, Math.round(Number(s) || 0));
  const p = n => String(n).padStart(2, "0");
  const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60);
  return h ? `${h}:${p(m)}:${p(t % 60)}` : `${m}:${p(t % 60)}`;
}
function link(url, text, cls) {
  const a = el("a", cls, text);
  a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
  return a;
}
/* A reason the payload gave. Rendered VERBATIM, as an explanation and not as
   breakage — "their squads have not been crawled" is the world working. It is
   clamped, never edited: the full sentence is one click (and the title) away,
   because roughly four drawers in five are made of these. */
function reason(text) {
  const t = text || "no reason given";
  const n = clampable("div", "pc-reason", t);
  n.title = t;
  return n;
}

/* Actions on the two-hue axis. The word always ships beside the glyph. */
const ACTIONS = {
  buy:            { label: "buy",       dir: "pos",  glyph: "▲" },
  sell:           { label: "sell",      dir: "neg",  glyph: "▼" },
  avoid:          { label: "avoid",     dir: "neg",  glyph: "⊘" },
  bench:          { label: "bench",     dir: "neg",  glyph: "▽" },
  hold:           { label: "hold",      dir: "flat", glyph: "=" },
  captain:        { label: "captain",   dir: "cap",  glyph: "★" },
  triple_captain: { label: "triple C",  dir: "cap",  glyph: "★★" },
  watch:          { label: "watching",  dir: "obs",  glyph: "◇" },
};
/* `is_observation` exists so the UI cannot get this wrong by omission — and a
   `watch` is treated as one even if the flag is missing. An observation is
   never coloured as a direction and never uses a recommending verb. */
function actionMeta(s) {
  const obs = s.is_observation === true || s.action === "watch";
  const a = ACTIONS[s.action] || { label: String(s.action || "?"), dir: "flat", glyph: "•" };
  if (!obs) return { ...a, obs: false };
  return {
    glyph: "◇",
    label: s.action === "watch" ? "watching" : `${a.label} — as an observation`,
    dir: "obs", obs: true,
  };
}
/* llm: read semantically, verbatim quote + conviction. cue: a keyword landed
   in a window near the name. Not the same thing, never drawn the same. */
function extractorMeta(x) {
  const s = String(x || "");
  if (s.startsWith("llm")) return {
    // the model name lives in the title: the footer must stay one line
    kind: "llm", label: "considered take",
    title: `read semantically by ${s.slice(4) || "a model"} — the quote is verbatim`,
  };
  if (s === "cue") return {
    kind: "cue", label: "keyword match",
    title: "a keyword landed near this player's name inside show notes. " +
           "A search hit, not a stated opinion — read the window before you trust it.",
  };
  return { kind: "cue", label: s || "unknown source", title: "unrecognised extractor" };
}
/* 353 of 594 items ARE the mp3. The affordance has to say so. */
function deepVerb(s) {
  if (s.link_verb) return s.link_verb;                 // server-written wins
  const at = s.start_s != null ? ` at ${hms(s.start_s)}` : "";
  if (s.url_basis === "enclosure") return `play audio${at}`;
  return s.start_s != null ? `open episode${at}` : "open source";
}
function convPips(band) {
  const n = { high: 3, medium: 2, low: 1 }[String(band || "").toLowerCase()] || 0;
  const w = el("span", "pc-conv");
  const pips = el("span", "pc-pips");
  for (let i = 0; i < 3; i++) pips.appendChild(el("i", i < n ? "on" : ""));
  w.append(pips, el("span", null, band || "unstated conviction"));
  w.title = `conviction: ${band || "unstated"}`;
  return w;
}
/* Quotes are the point, so they clamp rather than truncate: two lines, and
   the whole thing on click. Nothing is ever silently dropped.
   The affordance is added ONLY where the text really overflows, and only
   once layout can answer that — a keyboard stop on every short paragraph in
   a strip made mostly of short paragraphs is noise, not access. */
function clampable(tag, cls, text) {
  const n = el(tag, cls + " pc-clamp", text);
  const toggle = () => {
    const open = n.classList.toggle("pc-clamp");
    n.setAttribute("aria-expanded", String(!open));
  };
  n.onclick = () => { if (n.classList.contains("pc-cut")) toggle(); };
  n.onkeydown = e => {
    if (!n.classList.contains("pc-cut")) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
  };
  markIfClamped(n);
  return n;
}

/* Measured after the node is in the document: scrollHeight beats clientHeight
   exactly when -webkit-line-clamp actually cut something off. */
function markIfClamped(n) {
  requestAnimationFrame(() => {
    if (!n.isConnected || !n.classList.contains("pc-clamp")) return;
    if (n.scrollHeight - n.clientHeight < 2) return;
    n.classList.add("pc-cut");
    n.tabIndex = 0;
    /* A quote stays a <blockquote>: role="button" would trade its quotation
       semantics for an affordance, and on this page the quote-ness is the
       editorial point. Everything else takes the button role. */
    if (n.tagName !== "BLOCKQUOTE") n.setAttribute("role", "button");
    n.setAttribute("aria-expanded", "false");
    if (!n.title) n.title = "click to show the whole thing";
  });
}

/* ---------------- section renderers ---------------- */

function railNode(key, word, gloss) {
  const r = el("div", "pc-rail");
  r.dataset.ch = key;
  const lab = el("div", "pc-lab");
  lab.appendChild(el("b", null, word));
  lab.appendChild(el("span", null, gloss));
  const body = el("div", "pc-body");
  r.append(lab, body);
  return { node: r, body };
}

function renderDid(body, d) {
  const owned = d.owned || [];
  const c = d.counts || {};
  if (!owned.length) {
    // NEVER "nobody owns him": an uncrawled squad and an absent player are
    // different facts and must not render the same. When the panel wrote a
    // reason, the reason IS the answer — we do not append a second sentence
    // of our own beside it.
    if (d.owned_reason) body.appendChild(reason(d.owned_reason));
    else if (c.squads_known != null && c.panel_size != null)
      // Counts we were given, and no inference on top of them: with no
      // `owned_reason` we do not know whether he is unowned or unread.
      body.appendChild(el("div", "pc-reason",
        `${c.squads_known} of ${c.panel_size} panel squads have been read. ` +
        `The panel gave no reason for the empty list.`));
    else body.appendChild(reason(null));
    return;
  }
  const list = el("div", "pc-owners");
  for (const o of owned) {
    const s = el("span", "pc-own" + (o.role === "captain" ? " cap" : ""));
    s.appendChild(el("b", null, o.person || "unnamed entry"));
    const bits = [];
    if (o.role === "captain") bits.push("★");
    if (o.multiplier != null) bits.push(`×${o.multiplier}`);
    else if (o.role) bits.push(o.role);
    if (bits.length) s.appendChild(el("span", "pc-mult", bits.join("")));
    s.title = [o.person, o.entry_id != null ? `entry ${o.entry_id}` : null,
               o.gw != null ? `GW${o.gw}` : null, o.role].filter(Boolean).join(" · ");
    list.appendChild(s);
  }
  body.appendChild(list);
  const parts = [];
  if (c.owned != null && c.squads_known != null)
    parts.push(`${c.owned} of ${c.squads_known} squads read`);
  if (c.panel_size != null) parts.push(`panel of ${c.panel_size}`);
  const gws = [...new Set(owned.map(o => o.gw).filter(g => g != null))];
  if (gws.length) parts.push(`GW${gws.join("/")}`);
  if (parts.length) body.appendChild(el("div", "pc-note",
    parts.join(" · ") + " — measured picks, not opinions."));
}

function renderStatement(s) {
  const a = actionMeta(s), ev = extractorMeta(s.extractor);
  const n = el("div", "pc-st");
  n.dataset.dir = a.dir; n.dataset.ev = ev.kind;

  const top = el("div", "pc-st-top");
  const act = el("span", "pc-act");
  act.append(el("span", "pc-glyph", a.glyph), document.createTextNode(a.label));
  if (a.obs) act.title = "an observation — 'keep an eye on him'. Not a recommendation.";
  top.appendChild(act);
  // person_basis matters: a show is not a person. The Wire is four people.
  const who = s.person || s.show || "unattributed";
  const w = el("span", "pc-who", who);
  w.title = s.person
    ? `${s.person}${s.show ? ` on ${s.show}` : ""}` +
      (s.person_basis ? ` · attributed by ${s.person_basis}` : "")
    : "attributed to the show, not to a person — the show may have several hosts";
  if (!s.person) w.appendChild(el("i", "pc-showonly", " (show)"));
  top.appendChild(w);
  if (s.published_at) top.appendChild(el("span", "pc-when", ago(s.published_at)));
  n.appendChild(top);

  if (a.obs) n.appendChild(el("div", "pc-obs", "an observation, not a call"));

  if (s.quote) {
    n.appendChild(ev.kind === "cue"
      ? clampable("div", "pc-cue", s.quote)
      : clampable("blockquote", "pc-q", s.quote));
  } else {
    n.appendChild(el("div", "pc-note", "no quote was stored for this one."));
  }

  const foot = el("div", "pc-st-foot");
  const evc = el("span", "pc-ev", ev.label); evc.title = ev.title;
  foot.appendChild(evc);
  if (ev.kind === "llm") foot.appendChild(convPips(s.conviction));
  if (s.deep_link) foot.appendChild(link(s.deep_link,
    (s.url_basis === "enclosure" ? "▶ " : "↗ ") + deepVerb(s), "pc-deep"));
  else if (s.item_url) foot.appendChild(link(s.item_url, "↗ open source", "pc-deep"));
  else foot.appendChild(el("span", "pc-note", "no link stored"));
  n.appendChild(foot);
  if (s.item_title) n.title = s.item_title;
  return n;
}

function renderSaid(body, d, limit, onMore) {
  const said = d.said || [];
  if (!said.length) { body.appendChild(reason(d.said_reason || d.reason)); return; }
  const c = d.counts || {};
  const obs = said.filter(s => s.is_observation === true || s.action === "watch").length;
  const llm = said.filter(s => String(s.extractor || "").startsWith("llm")).length;
  const nSaid = c.said != null ? c.said : said.length - obs;
  const nObs = c.observations != null ? c.observations : obs;

  // A count, never a verdict. No net, no consensus score — deliberately.
  const sum = el("div", "pc-sum");
  sum.appendChild(el("b", null, `${nSaid} statement${nSaid === 1 ? "" : "s"}`));
  if (nObs) sum.appendChild(el("span", null, ` · ${nObs} observation${nObs === 1 ? "" : "s"}`));
  sum.appendChild(el("span", "pc-split",
    ` · ${llm} considered, ${said.length - llm} keyword`));
  body.appendChild(sum);

  for (const s of said.slice(0, limit)) body.appendChild(renderStatement(s));
  if (said.length > limit) {
    const b = el("button", "pc-more",
      `show ${said.length - limit} more of ${said.length}`);
    b.onclick = onMore;
    body.appendChild(b);
  }
  // The fixed caveat. Same sentence for everyone, because it is true for
  // everyone: the record is measured and it is below chance.
  body.appendChild(el("div", "pc-caveat", d.record_note ||
    "Not a forecast — the panel's record is below chance. This is what was said."));
}

const INTEL = {
  out_of_position: "out of position", set_piece: "set piece",
  availability: "availability", press_conference: "press conference",
};
function renderNoticed(body, d, limit, onMore) {
  const items = d.noticed || [];
  if (!items.length) { body.appendChild(reason(d.noticed_reason || d.reason)); return; }
  for (const it of items.slice(0, limit)) {
    const n = el("div", "pc-in");
    // kind, source and age share one line: the card stays inside the budget
    const top = el("div", "pc-in-top");
    top.appendChild(el("span", "pc-kind", INTEL[it.kind] || it.kind || "intel"));
    if (it.source_url)
      top.appendChild(link(it.source_url, it.source || "source", "pc-deep"));
    else if (it.source) top.appendChild(el("span", "pc-in-src", it.source));
    if (it.confidence != null)
      top.appendChild(el("span", "pc-in-src",
        `confidence ${Number(it.confidence).toFixed(2)}`));
    if (it.published_at) top.appendChild(el("span", "pc-when", ago(it.published_at)));
    n.appendChild(top);
    if (it.headline) n.appendChild(el("div", "pc-in-h", it.headline));
    if (it.body) n.appendChild(clampable("div", "pc-in-b", it.body));
    body.appendChild(n);
  }
  if (items.length > limit) {
    const b = el("button", "pc-more",
      `show ${items.length - limit} more of ${items.length}`);
    b.onclick = onMore;
    body.appendChild(b);
  }
}

/* ---------------- the mount ---------------- */

function mount(host, code, opts, limitsFor) {
  const root = el("section", "pc" + (opts.variant === "section" ? " pc-full" : ""));
  const days = opts.days ?? 30;
  let cancelled = false, lim = null;      // null = use the budget for this payload

  const head = el("div", "pc-head");
  const title = el("h3", "pc-title",
    opts.name ? `The panel on ${opts.name}` : "The panel");
  head.append(title, el("span", "pc-win", `${days} days`));
  root.appendChild(head);
  const rails = el("div", "pc-rails");
  root.appendChild(rails);
  rails.appendChild(el("div", "pc-load", "reading the panel…"));
  host.appendChild(root);

  function draw(d) {
    rails.textContent = "";
    const L = lim || limitsFor(d);
    if (d.name && !opts.name) title.textContent = `The panel on ${d.name}`;
    const quiet = !(d.owned || []).length && !(d.said || []).length
      && !(d.noticed || []).length;
    root.dataset.state = quiet ? "quiet" : "ok";
    // The modal state, designed first: one headline sentence, then each rail
    // keeps its own verbatim reason. Same skeleton as a full strip, so silence
    // reads as an answer rather than as something that failed to load.
    if (quiet) rails.appendChild(el("div", "pc-quiet",
      "Nothing owned, said or noticed here — the usual answer, not a gap."));

    const did = railNode("did", "DID", "owns");
    renderDid(did.body, d);
    const said = railNode("said", "SAID", "🗣 spoken");
    renderSaid(said.body, d, L.said,
      () => { lim = { ...L, said: L.said >= FULL.said ? 1e9 : FULL.said }; draw(d); });
    const noticed = railNode("noticed", "NOTICED", "⚙ measured");
    renderNoticed(noticed.body, d, L.noticed,
      () => { lim = { ...L, noticed: L.noticed >= FULL.noticed ? 1e9 : FULL.noticed }; draw(d); });
    rails.append(did.node, said.node, noticed.node);
  }

  fetchChatter(code, days).then(r => {
    if (cancelled || !root.isConnected) return;
    if (r.ok) {
      const d = r.data || {};
      if (d.empty) {                       // the panel's own empty envelope
        root.dataset.state = "quiet";
        rails.textContent = ""; rails.appendChild(reason(d.reason));
        return;
      }
      draw(d);
      return;
    }
    root.dataset.state = "down";
    rails.textContent = "";
    rails.appendChild(el("div", "pc-down", r.down
      ? "The `player_chatter` panel is not registered on this server yet, so " +
        "there is nothing to read. This strip renders as soon as it ships — " +
        "nothing is cached or invented in the meantime."
      : `player_chatter could not be read — ${r.error}`));
  });

  return {
    node: root,
    cancel() { cancelled = true; },
    remove() { cancelled = true; root.remove(); },
  };
}

/* Compact — for a drawer, mounted under the host's own content.
   Returns a handle; call cancel() when the drawer closes. */
export function chatterStrip(host, code, opts = {}) {
  return mount(host, code, { ...opts, variant: "strip" },
               opts.limits ? () => opts.limits : compactLimits);
}
/* Fuller — for a page column or a wide panel. Same data, more of it. */
export function chatterSection(host, code, opts = {}) {
  return mount(host, code, { ...opts, variant: "section" },
               () => opts.limits || FULL);
}
export default chatterStrip;
