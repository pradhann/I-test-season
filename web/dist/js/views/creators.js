/* Creators. Three levels, one panel call per click.
 *
 * The owner's sentence: every transcribed episode should be reachable, a show
 * should open into its episodes with a link and a title, an episode should
 * open into the summary that matters for FPL, and the way down should be one
 * click at a time.
 *
 *   level 1  the board          `creator_board {scope: "panel"}`
 *   level 2  one show's archive `creator_episodes {creator, limit: 200,
 *                                                  include_untranscribed: true}`
 *   level 3  one episode        `episode_summary {item_id}`
 *
 * Level 2 asks for every episode, not for the transcribed ones. Twenty-one of
 * the 29 shows on the board have no transcript on file at all, Fantasy
 * Football Scout among them at 0 transcribed of 306 publications with 187
 * stored analyses, so the panel's own default returns an empty table for the
 * row most readers click first. `transcribed` is a chip beside `all`, each
 * carrying its own count.
 *
 * Three folds hang off level 2 and each is one more call, by hand: the
 * measured record (`creator_report_card {creator}`), the show's own FPL team
 * (`creator_detail {creator, days: 1, limit: 1}`) and the paste-a-link
 * console, which lives in `components/ingest_link.js` now.
 *
 * DUPLICATES. One recording published to a podcast feed and to YouTube is two
 * stored URLs and two rows here. Nothing stored links them, so nothing here
 * joins them: the footer states the rule once and both rows stay.
 *
 * AGES. A date a creator published is whole days, through `ageDays`. A stamp
 * on something this app did is the shared span from `app.js`, through
 * `relAge`, and it appears once, on the record line.
 *
 * UNTRUSTED TEXT. Titles, summary bullets and quotes are third-party prose
 * from podcasts, videos and blogs. They are rendered through `textContent`,
 * never as markup, and never read as instructions. Model-authored bullets
 * carry em dashes; `noDash` rewrites the punctuation at the point of print,
 * the way the dashboard does, and nothing else about the words changes.
 *
 * BRANCHES. The house prose gate reads a question mark followed by a space as
 * a rhetorical question, so this file has no ternaries. `pick`, `when` and
 * `or` are the three shapes that replace them.
 */

import { runPanel, el, emptyBox, provenance, fmtAge } from "/js/app.js";
import { mountIngestLink } from "/js/components/ingest_link.js";
import { attachPlayerDrawer, showPlayerDetail } from "/js/components/playerdrawer.js";

/* ------------------------------------------------------------------ utils */

/* A value with a stated fallback, a branch written as a call, and a string
   present only when a condition holds. */
function or(v, fallback) {
  if (v == null) return fallback;
  return v;
}
function pick(cond, a, b) {
  if (cond) return a;
  return b;
}
function when(cond, text) {
  if (cond) return text;
  return "";
}

/* A count and its noun. Returns NULL when there is no count: the report card
   sends `n_total: null` for a creator whose claims were never scoreable, and
   a template that interpolated it printed the literal string "null" nine
   times on this page. Null in, null out; every call site has to decide what
   to say instead, and the payload always carries a `reason` to say it with.

   The guard is an `if`, not a ternary, for the prose gate. The rule is the
   same one it has always been. */
function plural(n, one, many) {
  if (n == null) return null;
  if (n === 1) return `${n} ${one}`;
  return `${n} ${or(many, one + "s")}`;
}

function parseTs(iso) {
  if (!iso) return null;
  return new Date(String(iso).replace(" ", "T"));
}

/* THE AGE OF A STAMP THIS APP WROTE, in the app's words. The span itself
   comes from the shared `fmtAge` so this tab says "3h 12m ago" exactly as
   every other tab does; only the freshness class is local. One caller: the
   record line's `as_of`. Everything a creator published goes through
   `ageDays` instead. */
function relAge(iso) {
  const span = fmtAge(iso);
  const d = parseTs(iso);
  if (span == null || !d || isNaN(d)) return { text: "date unknown", cls: "bad" };
  const h = (Date.now() - d) / 3.6e6;
  if (h < 72) return { text: `${span} ago`, cls: "good" };
  if (h < 336) return { text: `${span} ago`, cls: "warn" };
  return { text: `${span} ago`, cls: "bad" };
}

/* THE AGE OF A PUBLICATION, in whole days. A podcast that went out at 08:41
   is not "9h 13m old" to anyone deciding what to watch tonight; it is from
   today. The boundary is one sentence: a date on a thing the creator
   published is days, a stamp on a thing this app did is the shared span. */
function ageDays(iso) {
  const d = parseTs(iso);
  if (!d || isNaN(d)) return null;
  return Math.max(0, Math.floor((Date.now() - d.getTime()) / 86400000));
}
/* The same number as a word. Split from `ageDays` so every caller that needs
   both the number and the word reads the number itself and does not parse a
   string back out of a label. */
function daysWord(n) {
  if (n == null) return "never";
  if (n === 0) return "today";
  return `${n}d`;
}
/* The publication date itself, UTC, so a 23:40Z upload is not yesterday in
   the reader's zone. */
function pubDate(iso) {
  const d = parseTs(iso);
  if (!d || isNaN(d)) return "not stated";
  return d.toISOString().slice(0, 10);
}

/* Payload prose written by a model carries em dashes; this app prints none
   (prose_style.py's rule), so they are rewritten at the point of print, the
   same way `home.js` does it. Nothing else about the words changes, and the
   payload is not touched. */
function noDash(s) {
  if (s == null) return s;
  const EM = String.fromCharCode(8212);
  return String(s).split(" " + EM + " ").join("; ").split(EM).join(", ");
}

function clock(s) {
  if (s == null || !isFinite(s)) return null;
  const t = Math.max(0, Math.round(s));
  const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), sec = t % 60;
  const pad = n => String(n).padStart(2, "0");
  if (h) return `${h}:${pad(m)}:${pad(sec)}`;
  return `${m}:${pad(sec)}`;
}

function pct(x) {
  if (x == null) return "–";
  return `${Math.round(100 * x)}%`;
}
function signed(x, d) {
  if (x == null) return "–";
  const places = or(d, 1);
  return `${pick(x >= 0, "+", "−")}${Math.abs(x).toFixed(places)}`;
}
function num(x) {
  if (x == null) return "–";
  return String(x);
}

/* Where a stored position came from, and how strong that channel is.
   `players[].claims[]` carries `kind` and `stored_in`: a content_claim row
   with its extractor, or a call inside the stored analysis with the list name
   it sits under. `transfers_suggested[]` and `captain_view[]` carry neither,
   because the list they came from IS the section drawing them, so those two
   sections pass the channel in rather than reading a field that is not there.
   Two channels, named apart, never merged into one word. */
function tier(storedIn, kind) {
  const e = String(or(storedIn, ""));
  if (kind === "call")
    return { key: "call", label: "analysis call", model: null,
             note: `stored in the analysis under ${e}` };
  if (e.startsWith("llm:"))
    return { key: "llm", label: "considered take", model: e.slice(4),
             note: "a language model read the passage and returned a verbatim quote" };
  if (e === "cue")
    return { key: "cue", label: "keyword window", model: null,
             note: "a keyword landed near this player's name; a search hit, not an opinion" };
  return { key: "unknown", label: "method not recorded", model: null,
           note: "how this was extracted is not recorded in the payload"
                 + when(e, ` (stored_in: ${e})`) };
}

/* The report card's verdict vocabulary, and how it is drawn. `vs_coin_flip`
   is the payload's own word; the class picks a status token, never a hue. */
const COIN = {
  below: { cls: "below", word: "below coin flip", short: "below chance" },
  above: { cls: "above", word: "above coin flip", short: "above chance" },
  indistinguishable: { cls: "flip", word: "coin flip", short: "coin flip" },
  unmeasured: { cls: "none", word: "unmeasured", short: "unmeasured" },
};
function coin(v) {
  return or(COIN[v], COIN.unmeasured);
}

/* LOADING AFFORDANCES, one glyph and one capitalisation. The glyph is the
   single character "…", never three dots. Status text that occupies its own
   element is lowercase ("measuring the record…"); a status sentence that
   follows a full stop is sentence case, because that is what a sentence is;
   a button mid-action keeps the case of its own resting label. */

/* A failed panel call arrives as the whole response body, which for a server
   error is a traceback. Interpolated into a sentence it buries the page. The
   first line is the part that says what happened; the rest goes in a fold. */
const statusLine = e => {
  const text = String(or(or(e && e.message, e), "")).trim();
  /* app.js throws `${script}: HTTP ${status} ${body}`, so the status line is
     everything up to the code and the body starts right after it. Anything
     else is cut at its first newline. */
  const http = text.match(/^(.{1,80}?:\s*HTTP\s*\d{3})\b/);
  const first = pick(http, http && http[1], text.split("\n", 1)[0]);
  if (first.length > 140) return first.slice(0, 137) + "…";
  return first;
};

/* The status line, with the full body behind a summary that is not a lie
   about its own length. */
function failFold(e, lead) {
  const text = String(or(or(e && e.message, e), "")).trim();
  const line = statusLine(e);
  const p = el("p", "cx-honest warn");
  p.append(pick(lead, `${lead} ${line}`, line));
  if (text.length > line.length) {
    const d = el("details", "cx-disclose");
    d.appendChild(el("summary", null, "the full response body"));
    d.appendChild(el("pre", "cx-raw", text));
    p.appendChild(d);
  }
  return p;
}

/* What to say about a record with nothing scored. A count is not always
   available; the payload's own `reason` always is, and it is the more useful
   sentence anyway. `tail` is the clause that follows a count when there IS
   one. `short` is for the pill chip, which is one line on a flex row and
   cannot hold a 93-character sentence; every caller that passes it also
   carries the full reason on the element's `title`. */
function unscored(cl, tail, short) {
  const n = plural(cl && cl.n_total, "claim");
  if (n) return `${n}, ${tail}`;
  if (short) return tail;
  return or(cl && cl.reason, "nothing scored, and the payload gives no reason");
}

/* The one-word label every chip carries. `too few` wins under the floor: a
   record with fewer scored claims than `min_scored_claims` is drawn but is
   never read as a rank. Colour still follows the payload's `vs_coin_flip`,
   which is the interval test itself and not a count. */
function verdict(cl, floor) {
  if (!cl || !cl.measured) return { cls: "none", word: "unmeasured", few: false };
  const few = floor != null && cl.n_scored < floor;
  const c = coin(cl.vs_coin_flip);
  return { cls: c.cls + when(few, " few"), word: pick(few, "too few", c.short), few };
}

/* THE TIMESTAMP, AND WHEN IT IS A LINK. Three cases and no fourth.
   `urls.deep_link` returns a real offset link only where the platform has a
   grammar for one, which today is YouTube. Everywhere else it hands back the
   episode URL untouched and still reports `start_s`, so an anchor labelled
   "at 4:53" would land at second zero. So: nothing when there is no offset,
   an anchor when the deep link differs from the episode link, plain text
   when it does not. */
function atMark(ev, episode) {
  const ts = clock(ev && ev.start_s);
  if (!ts) return null;
  const href = ev.deep_link;
  const base = episode && episode.source_url;
  if (href && href !== base) {
    const a = el("a", "cx-at", `at ${ts}`);
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.title = "opens the recording at this moment";
    return a;
  }
  const s = el("span", "cx-at flat", `at ${ts}`);
  s.title = "the offset is stored; this platform has no link grammar for a "
    + "moment, so the episode link above opens at the start";
  return s;
}

/* The four words `transcription_state` can take, and how each is drawn. */
const STATE_WORD = {
  transcribed: { word: "transcribed", cls: "good" },
  queued: { word: "queued", cls: "warn" },
  failed: { word: "failed", cls: "bad" },
  none: { word: "none", cls: "muted" },
};
function stateOf(s) {
  return or(STATE_WORD[s], { word: String(or(s, "unknown")), cls: "muted" });
}
function stateChip(s) {
  const st = stateOf(s);
  const c = el("span", "cx-state " + st.cls);
  c.appendChild(el("span", "cx-statedot"));
  c.append(st.word);
  c.setAttribute("aria-label", `transcript ${st.word}`);
  return c;
}

/* How the archive files a publication, short enough for a column. */
const KIND_SHORT = { podcast: "pod", youtube: "yt", blog: "blog", link: "link" };
function kindShort(k) {
  return or(KIND_SHORT[k], String(or(k, "?")));
}

/* ------------------------------------------------------------ sort, once */

/* One comparator for all three levels. A null sorts last under either
   direction, because "no date on file" is not the freshest thing on the
   board and it is not the stalest either. */
function sortRows(rows, spec, valueOf) {
  if (!spec) return rows;
  const out = rows.slice();
  out.sort((a, b) => {
    const va = valueOf(a, spec.key), vb = valueOf(b, spec.key);
    if (typeof va === "string" || typeof vb === "string")
      return String(va).localeCompare(String(vb)) * spec.dir;
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return (va - vb) * spec.dir;
  });
  return out;
}

/* A sortable header cell, the Fixtures idiom: the header is a button, the
   active one carries `aria-sort` and a persistent arrow. */
function sortHead(spec, key, label, numeric, onSort) {
  const cell = el("th", when(numeric, "num"));
  const on = spec && spec.key === key;
  let ariaSort = "none";
  if (on && spec.dir > 0) ariaSort = "ascending";
  if (on && spec.dir < 0) ariaSort = "descending";
  cell.setAttribute("aria-sort", ariaSort);
  const b = el("button", "cx-th" + when(on, " on"));
  b.appendChild(el("span", null, label));
  let arrow = "";
  if (on && spec.dir > 0) arrow = "▲";
  if (on && spec.dir < 0) arrow = "▼";
  b.appendChild(el("span", "arr", arrow));
  b.title = `sort by ${label}`;
  b.dataset.sortKey = key;
  /* Sorting rebuilds the head, so the button that was clicked is replaced by
     an identical one and a keyboard user loses their place. The key is unique
     across both tables, so the new button is findable and takes the focus
     back. */
  const restore = () => {
    const again = document.querySelector(`[data-sort-key="${key}"]`);
    if (again && again !== b) again.focus();
  };
  b.onclick = () => {
    if (on) onSort({ key, dir: -spec.dir });
    else onSort({ key, dir: pick(numeric, -1, 1) });
    restore();
  };
  cell.appendChild(b);
  return cell;
}

/* ------------------------------------------------------- the hash, parsed
 *
 * `app.js` splits the hash on the query mark only, so a sub-path route falls
 * through to the dashboard. A query keeps every level inside the registered
 * `#creators` route with no shell change: `#creators`, `#creators?c=<show>`
 * and `#creators?e=<item_id>`. Reload lands where it left, and the browser
 * back button walks back up the levels because each one is a history entry.
 */
function readHash() {
  const raw = String(or(location.hash, "")).slice(1);
  const q = raw.split("?")[1];
  if (!q) return { level: 1 };
  const p = new URLSearchParams(q);
  const e = p.get("e");
  if (e) return { level: 3, item: e, creator: p.get("c") };
  const c = p.get("c");
  if (c) return { level: 2, creator: c };
  return { level: 1 };
}
function goto1() { location.hash = "#creators"; }
function goto2(creator) {
  location.hash = "#creators?c=" + encodeURIComponent(creator);
}
function goto3(itemId) {
  location.hash = "#creators?e=" + encodeURIComponent(itemId);
}

/* ------------------------------------------------------------- the caches
 *
 * Module scope, so walking down a level and back up is free. The shell tears
 * the view down on every `hashchange` and re-runs `load()`, which would
 * otherwise re-fetch the board on every click. One call per click means the
 * board is fetched once per page load, not once per level.
 */
let BOARD = null;                       // {result, provenance}
const EPISODES = new Map();             // creator -> Promise<{result, provenance}>
const SUMMARIES = new Map();            // item_id -> Promise<{result, provenance}>

function boardOnce() {
  if (BOARD) return Promise.resolve(BOARD);
  return runPanel("creator_board", { scope: "panel" }).then(r => {
    BOARD = r;
    return r;
  });
}
function episodesFor(creator) {
  if (!EPISODES.has(creator)) {
    const call = runPanel("creator_episodes",
      { creator, limit: 200, include_untranscribed: true });
    call.catch(() => EPISODES.delete(creator));
    EPISODES.set(creator, call);
  }
  return EPISODES.get(creator);
}
function summaryFor(itemId) {
  if (!SUMMARIES.has(itemId)) {
    const call = runPanel("episode_summary", { item_id: itemId });
    call.catch(() => SUMMARIES.delete(itemId));
    SUMMARIES.set(itemId, call);
  }
  return SUMMARIES.get(itemId);
}

/* ==================================================================== view */

export default async function creators(host) {
  const route = readHash();

  /* ---- shell ---------------------------------------------------------- */
  const mainCard = el("section", "card cx");
  host.append(mainCard);

  /* One drawer per visit: a re-entered view must not stack a second one on
     the body, and the previous visit's key handler must stop listening. */
  document.querySelectorAll("aside.cx-drawer").forEach(n => n.remove());
  const drawer = el("aside", "drawer cx-drawer");
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-modal", "true");
  drawer.setAttribute("aria-label", "creator detail");
  document.body.appendChild(drawer);
  let inkHandle = null;                 // the paste-a-link console, when open
  let pdHandle = null;                  // the shared player drawer, when used

  const closeDrawer = () => {
    drawer.classList.remove("open");
    try { inkHandle?.stop(); } catch { /* the console may be mid-build */ }
    inkHandle = null;
    if (readHash().level > 1) goto1();
  };
  const onKey = e => {
    if (!drawer.isConnected) { removeEventListener("keydown", onKey); return; }
    if (e.key === "Escape" && drawer.classList.contains("open")) closeDrawer();
  };
  addEventListener("keydown", onKey);

  const openDrawer = () => {
    drawer.textContent = "";
    drawer.classList.add("open");
    drawer.scrollTop = 0;
  };

  /* ---- state ---------------------------------------------------------- */
  let res = null, prov = null;
  let l1sort = { key: "latest", dir: 1 };   // freshest first, nulls last

  /* the report card, fetched one creator at a time from level 2 */
  let rc = null, rcErr = null;
  const rcByCreator = new Map();
  /* entry_id -> the creators whose card carries it. Three cards resolve to
     entry 176749 and each drew the same three gameweeks as if they were
     three independent results. They are one manager, counted three times.
     With one card per fold the map fills a card at a time, so the warning
     can only fire once a second fold has been opened this visit. */
  const cardsByEntry = new Map();
  const LEGACY = new Set(["user-shared"]);   // a pseudo-source, not a creator

  /* Skeleton shells for the panel build. */
  function skeleton(cards) {
    const sk = el("div", "cx-skel");
    sk.setAttribute("role", "status");
    sk.setAttribute("aria-label", "loading");
    sk.appendChild(el("div", "cx-skelrow"));
    sk.appendChild(el("div", "cx-skelblock"));
    for (let i = 0; i < or(cards, 3); i++) sk.appendChild(el("div", "cx-skelcard"));
    return sk;
  }

  /* ================================================== level 1, the board */

  /* WHAT THE BOARD IS READING, and what it is not. `creator_board.scope`
     names the roster it applied and the sources it left out; the page read
     neither, so four sources were dropped silently. `mine_reason` is the
     board's own sentence about the squad it could not read. */
  function scopeLine() {
    const sc = res && res.scope;
    if (!sc && !(res && res.mine_reason)) return null;
    const p = el("p", "cx-note");
    const out = or(sc && sc.excluded, []);
    if (sc) {
      const shows = or(sc.shows, []).length;
      p.append(`Board scope: ${or(sc.applied, "unstated")}`
               + when(shows, `, ${plural(shows, "show")}`) + ".");
      if (sc.reason) p.append(` ${sc.reason}`);
    }
    /* The excluded feeds and the squad the board could not read are both
       reasons rather than findings, and both run to several lines on a phone.
       They fold together, and the summary names whichever of the two is
       actually there. */
    if (!out.length && !res.mine_reason) return p;
    const d = el("details", "cx-disclose");
    let word = `${plural(out.length, "source")} outside it, not read here`;
    if (out.length && res.mine_reason)
      word = `${plural(out.length, "source")} outside it, and the unread squad`;
    if (!out.length) word = "the squad this board could not read";
    d.appendChild(el("summary", null, word));
    if (out.length) d.appendChild(el("div", "cx-thin sub", out.join(", ")));
    if (res.mine_reason) d.appendChild(el("div", "cx-thin sub", res.mine_reason));
    p.appendChild(d);
    return p;
  }

  /* THE RECORD AND THE READ QUEUE, IN THE BOARD'S OWN WORDS, folded.
     Both are the panel's own sentences and both run to eight lines at 390 px,
     which is a third of a phone screen spent before the first row. The fold
     summary says what is inside; the sentences are printed verbatim. */
  function boardNotes() {
    const note = res && res.record_note;
    const cov = res && res.coverage;
    if (!note && !(cov && cov.items)) return null;
    const d = el("details", "cx-disclose");
    d.appendChild(el("summary", null,
      "The record so far, and how much has been read"));
    if (note) d.appendChild(el("p", "cx-note", noDash(note)));
    const cv = coverageLine();
    if (cv) d.appendChild(cv);
    return d;
  }

  /* HOW TO READ THE TABLE, folded. Two rules, neither of them a number: what
     HIT counts over, and what the tag on the latest line means. */
  function readingNotes() {
    const d = el("details", "cx-disclose");
    d.appendChild(el("summary", null, "How to read this table"));
    d.appendChild(el("p", "sub",
      "HIT is the share of this show's scored claims that hit, over n scored "
      + "claims. The floor for reading it as a rank is the report card's, and "
      + "it opens inside a show."));
    d.appendChild(el("p", "sub",
      "LATEST LINE is tagged with the served key it came from: READ is a "
      + "model-written bullet about the newest item, POD, YT or BLOG is the "
      + "item title itself, and NONE is the panel's own reason there is "
      + "neither."));
    return d;
  }

  /* HOW MUCH OF WHAT WE FETCHED HAS BEEN READ. Fetching and analysing run on
     separate budgets, so an item sits in the corpus for days before a claim
     is extracted from it. Without this the board showed a take from four
     days ago beside a show published this morning, and "their latest take is
     old" could not be told apart from "we have not read them yet". */
  function coverageLine() {
    const c = res && res.coverage;
    if (!c || !c.items) return null;
    const p = el("p", "cx-note");
    p.appendChild(document.createTextNode(noDash(c.note)));
    const behind = or(c.by_source, []).filter(r => r.unread > 0);
    if (behind.length) {
      const d = el("details", "cx-disclose");
      d.appendChild(el("summary", null, "which feeds are behind"));
      const list = el("div", "cx-thin sub");
      for (const r of behind) {
        list.appendChild(el("div", null,
          `${r.source_key}: ${plural(r.unread, "item")} queued`
          + when(r.newest_unread,
                  `, newest ${daysWord(ageDays(r.newest_unread))} old`)));
      }
      d.appendChild(list);
      p.appendChild(d);
    }
    return p;
  }

  /* THE LATEST LINE, four rungs, each one a served key with a tag naming
     which. Nothing here is composed from more than one field. */
  function latestRung(c) {
    const bullets = or(c.take && c.take.summary_bullets, []);
    if (bullets.length)
      return { tag: "read", text: noDash(bullets[0]),
               why: `a model read this item`
                    + when(c.take.model, `, ${c.take.model}`) };
    if (c.latest && c.latest.title)
      return { tag: kindShort(c.latest.kind), text: noDash(c.latest.title),
               why: or(c.take_reason, "the latest item on file, not yet read") };
    if (c.latest_reason)
      return { tag: "none", text: noDash(c.latest_reason), why: null };
    return { tag: "none", text: "nothing on file, and the payload gives no reason",
             why: null };
  }

  /* Who is behind the show, from `entry.people`. A count when there are
     several, the name when there is one, the panel's reason when there is
     none. Never a guess. */
  function teamCell(c) {
    const people = or(c.entry && c.entry.people, []);
    if (people.length === 1) return { text: people[0].person, title: null };
    if (people.length > 1)
      return { text: `${people.length} people`,
               title: people.map(p => p.person).join(", ") };
    return { text: "no entry", title: or(c.entry_reason, "") };
  }

  /* Every column is a served key. `record` is the board's own record block,
     which carries the same hit rate and n the report card does without the
     134 KB the full card costs. */
  const L1COLS = [
    { key: "creator", label: "show", numeric: false },
    { key: "feeds", label: "feeds", numeric: false },
    { key: "latest", label: "latest (d)", numeric: true },
    { key: "items", label: "items 30d", numeric: true },
    { key: "claims", label: "claims 30d", numeric: true },
    { key: "arch", label: "archive", numeric: true },
    { key: "hit", label: "hit", numeric: true },
    { key: "n", label: "n", numeric: true },
    { key: "team", label: "team", numeric: false },
    { key: "line", label: "latest line", numeric: false },
  ];

  function l1Value(c, key) {
    if (key === "creator") return c.creator;
    if (key === "feeds") return or(c.kinds, []).map(kindShort).join("+");
    if (key === "latest") return ageDays(c.last_item_at);
    if (key === "items") return c.n_items_window;
    if (key === "claims") return c.n_claims_window;
    if (key === "arch") return c.n_items;
    if (key === "hit") return (c.record || {}).hit_rate;
    if (key === "n") return (c.record || {}).scored;
    if (key === "team") return teamCell(c).text;
    return latestRung(c).text;
  }

  /* The RECORD cell: the hit rate over the scored count, or the payload's
     own reason there is none. The floor is the report card's and is not
     served on the board, so nothing here is drawn as a rank. */
  function recordCells(c, tr) {
    const rec = or(c.record, {});
    const hit = el("td", "num");
    const n = el("td", "num");
    if (rec.scored) {
      hit.textContent = pct(rec.hit_rate);
      n.textContent = String(rec.scored);
      hit.title = `${rec.hits} of ${rec.scored} scored claims hit. `
        + `95% lower bound ${pct(rec.wilson_lo95)}. Weight ${rec.weight}, `
        + pick(rec.earned, "earned", "not earned") + ".";
    } else {
      hit.textContent = "–";
      n.textContent = "–";
      hit.title = unscored(rec, "none scored yet");
    }
    hit.setAttribute("data-k", "hit");
    n.setAttribute("data-k", "n");
    tr.append(hit, n);
  }

  function l1Row(c) {
    const tr = el("tr");

    const show = el("td", "show");
    const b = el("button", "cx-show", c.creator);
    b.title = "open this show's episodes";
    b.onclick = () => goto2(c.creator);
    show.appendChild(b);
    tr.appendChild(show);

    const feeds = el("td", null, or(c.kinds, []).map(kindShort).join("+"));
    feeds.title = or(c.sources, []).map(s => `${s.key} (${s.kind})`).join("\n");
    feeds.setAttribute("data-k", "feeds");
    tr.appendChild(feeds);

    const age = el("td", "num", daysWord(ageDays(c.last_item_at)));
    age.title = `latest item ${pubDate(c.last_item_at)}`;
    age.setAttribute("data-k", "latest");
    tr.appendChild(age);

    for (const spec of [["items", c.n_items_window], ["claims", c.n_claims_window],
                        ["arch", c.n_items]]) {
      const td = el("td", "num", num(spec[1]));
      td.setAttribute("data-k", spec[0]);
      tr.appendChild(td);
    }

    recordCells(c, tr);

    const tm = teamCell(c);
    const team = el("td", "team", tm.text);
    if (tm.title) team.title = tm.title;
    team.setAttribute("data-k", "team");
    tr.appendChild(team);

    const rung = latestRung(c);
    const line = el("td", "line");
    line.appendChild(el("span", "cx-rung", rung.tag));
    line.append(rung.text);
    if (rung.why) line.title = rung.why;
    tr.appendChild(line);
    return tr;
  }

  function l1Table(rows) {
    const wrap = el("div", "scroll-x");
    const t = el("table", "data sticky-first cx-l1");
    const thead = el("thead"), hr = el("tr");
    for (const col of L1COLS)
      hr.appendChild(sortHead(l1sort, col.key, col.label, col.numeric, s => {
        l1sort = s;
        renderLevel1();
      }));
    thead.appendChild(hr);
    t.appendChild(thead);
    const tb = el("tbody");
    for (const c of sortRows(rows, l1sort, l1Value)) tb.appendChild(l1Row(c));
    t.appendChild(tb);
    wrap.appendChild(t);
    return wrap;
  }

  /* THE SHOWS WITH NOTHING ON FILE. Four of the 29 have `last_item_at` null,
     every source probed and nothing yielded. They are not a sort case, they
     are a different answer, so they sit in a fold with their own count and
     the panel's reason beside each name. */
  function quietFold(quiet) {
    const d = el("details", "cx-disclose");
    d.appendChild(el("summary", null,
      `${plural(quiet.length, "show")} with nothing on file`));
    d.addEventListener("toggle", () => {
      if (!d.open || d.dataset.built) return;
      d.dataset.built = "1";
      const box = el("div", "cx-quiet");
      for (const c of quiet) {
        const row = el("div", "cx-quietrow");
        const b = el("button", "cx-show", c.creator);
        b.onclick = () => goto2(c.creator);
        row.appendChild(b);
        row.appendChild(el("span", "sub",
          noDash(or(c.latest_reason, "the payload gives no reason"))));
        box.appendChild(row);
      }
      d.appendChild(box);
    });
    return d;
  }

  /* THE CONSENSUS, folded. Five rows this week, already inside the bytes
     level 1 fetched, so the fold costs no call. The dashboard's captain
     verdict carries a `creator_armband` dissent and drills back to this tab;
     without this block that drill lands on a page that cannot answer it.
     The name opens the shared player drawer, which mounts the chatter strip,
     so one click reaches `player_chatter` and every per-player question the
     old said-versus-owned grid answered in 3,997 px. */
  function consensusFold() {
    const rows = or(res.consensus, []);
    const d = el("details", "cx-disclose");
    d.appendChild(el("summary", null,
      `${plural(rows.length, "player")} the panel is talking about this window`));
    d.addEventListener("toggle", () => {
      if (!d.open || d.dataset.built) return;
      d.dataset.built = "1";
      if (!rows.length) {
        d.appendChild(emptyBox(
          "The board carries no consensus row for this window.",
          "Nobody named a player that more than the threshold of shows agreed on."));
        return;
      }
      const t = el("table", "data cx-cons");
      const hr = el("tr");
      for (const l of ["player", "buy", "sell", "captain", "panel own", "mine"]) {
        hr.appendChild(el("th", when(l !== "player" && l !== "mine", "num"), l));
      }
      t.appendChild(hr);
      for (const r of rows) t.appendChild(consensusRow(r));
      d.appendChild(t);
      const ps = or(res.panel_squads, {});
      if (ps.reason) d.appendChild(el("p", "sub", ps.reason));
    });
    return d;
  }

  function consensusRow(r) {
    const tr = el("tr");
    const name = el("td");
    const b = el("button", "cx-show", or(r.disambiguator, r.name));
    b.title = "open the player: who owns him, who said what, what is measured";
    b.onclick = () => openPlayerDrawer(r);
    name.appendChild(b);
    name.appendChild(el("span", "sub",
      ` ${r.pos} ${r.team} ${num(r.price)} ${num(r.own_pct)}% owned`));
    tr.appendChild(name);
    for (const side of ["buy", "sell", "captain"]) {
      const cell = or(r[side], {});
      const td = el("td", "num", num(cell.n));
      td.title = or(or(cell.creators, []).join(", "), `nobody said ${side}`);
      td.setAttribute("data-k", side);
      tr.appendChild(td);
    }
    const own = or(r.panel_owned, {});
    const po = el("td", "num", `${num(own.n)} of ${num(own.of)}`);
    po.title = or(own.people, []).join(", ");
    po.setAttribute("data-k", "panel own");
    tr.appendChild(po);
    const mine = or(r.mine, {});
    const mt = el("td", null, pick(mine.in_squad, "in your squad", "not owned"));
    if (mine.source) mt.title = `read from ${mine.source}`;
    tr.appendChild(mt);
    return tr;
  }

  function openPlayerDrawer(r) {
    if (!pdHandle) pdHandle = attachPlayerDrawer("creators");
    showPlayerDetail(pdHandle, {
      code: r.code, name: or(r.disambiguator, r.name), pos: r.pos,
      team: r.team, price: r.price, own_pct: r.own_pct,
    }, { gw: res.gw });
  }

  function renderLevel1() {
    mainCard.textContent = "";
    mainCard.appendChild(el("h2", null, "Creators"));
    if (!res) {
      mainCard.appendChild(skeleton(4));
      return;
    }
    const sc = scopeLine();
    if (sc) mainCard.appendChild(sc);
    const bn = boardNotes();
    if (bn) mainCard.appendChild(bn);

    const all = or(res.creators, []);
    if (!all.length) {
      mainCard.appendChild(emptyBox(
        or(res.reason, "The board returned no creator."),
        "Nothing is inferred here; the panel says what it read."));
      return;
    }
    const quiet = all.filter(c => !c.last_item_at);
    const live = all.filter(c => c.last_item_at);
    mainCard.appendChild(l1Table(live));

    /* ONE count of the population, and what the other sets are. The page
       used to print three different numbers for these rows twelve pixels
       apart. This names each set once, from `res.creators.length`. */
    const foot = el("p", "cx-note",
      `${plural(all.length, "show")} on the board, `
      + `${live.length} with an item on file.`);
    mainCard.appendChild(foot);
    if (quiet.length) mainCard.appendChild(quietFold(quiet));
    mainCard.appendChild(consensusFold());
    mainCard.appendChild(readingNotes());
    mainCard.appendChild(provenance(prov));
  }

  /* ============================================ level 2, one show's archive */

  function drawerHead(title, sub) {
    const head = el("div", "dhead");
    const id = el("div");
    id.appendChild(el("div", "dname", title));
    if (sub) id.appendChild(el("div", "sub", sub));
    head.appendChild(id);
    const close = el("button", null, "✕");
    close.title = "close";
    close.onclick = closeDrawer;
    head.appendChild(close);
    return head;
  }

  /* The board's own sentence about why a show has nothing on file. It is on
     `creators[].latest_reason`, so it is only available when the board has
     been read on this visit; a cold deep link to level 2 has not read it and
     the state stands on the panel's counts alone. */
  function boardReasonFor(creator) {
    const row = or(res && res.creators, []).find(c => c.creator === creator);
    return or(row && row.latest_reason, null);
  }

  function peopleWord(d) {
    const people = or(d.entry && d.entry.people, []);
    if (people.length) return people.map(p => p.person).join(", ");
    return or(d.entry_reason, "no verified FPL entry attached");
  }

  /* The board's record block, as a sentence, for the level-2 head. Same
     numbers the level-1 cell prints, spelled out. */
  function recordSentence(rec) {
    if (!rec || !rec.scored)
      return unscored(rec, "none scored yet");
    return `${rec.hits} of ${rec.scored} scored claims hit, ${pct(rec.hit_rate)}, `
      + `95% lower bound ${pct(rec.wilson_lo95)}. Weight ${rec.weight}, `
      + pick(rec.earned, "earned", "not earned") + ".";
  }

  const L2COLS = [
    { key: "published", label: "published", numeric: false },
    { key: "age", label: "age (d)", numeric: true },
    { key: "title", label: "title", numeric: false },
    { key: "where", label: "where", numeric: false },
    { key: "text", label: "transcript", numeric: false },
    { key: "chars", label: "chars", numeric: true },
    { key: "read", label: "read", numeric: false },
    { key: "clm", label: "positions", numeric: true },
    { key: "gw", label: "gw", numeric: true },
  ];

  function l2Value(e, key) {
    if (key === "published") return or(e.published_at, "");
    if (key === "age") return ageDays(e.published_at);
    if (key === "title") return e.title;
    if (key === "where") return e.source_kind;
    if (key === "text") return e.transcription_state;
    if (key === "chars") return e.transcript_chars;
    if (key === "read") return String(e.analysis_present);
    if (key === "clm") return e.claim_count;
    return e.gameweek;
  }

  /* An episode row. Three states the archive actually contains and the page
     has to be honest about: analysed with no transcript (32 of 73 for one
     show, the analysis read the show notes), analysed with zero positions
     (31 of 73, the model read it and found nothing to store), and neither. */
  function l2Row(e) {
    const tr = el("tr");
    const readable = e.analysis_present || e.claim_count > 0;
    if (!readable) tr.className = "thin";

    const pub = el("td", null, pubDate(e.published_at));
    pub.setAttribute("data-k", "published");
    tr.appendChild(pub);

    const age = el("td", "num", daysWord(ageDays(e.published_at)));
    age.setAttribute("data-k", "age");
    tr.appendChild(age);

    const title = el("td", "title");
    const b = el("button", "cx-show", noDash(e.title));
    b.title = `${noDash(e.title)}\nopen the summary for this episode`;
    b.onclick = () => goto3(e.item_id);
    title.appendChild(b);
    if (e.source_url) {
      const a = el("a", "cx-at", "↗");
      a.href = e.source_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.title = `open the episode at ${e.source_url}`;
      a.setAttribute("aria-label", "open the episode at its source");
      title.appendChild(a);
    }
    tr.appendChild(title);

    const where = el("td", null, kindShort(e.source_kind));
    /* ONE RECORDING, TWO ROWS. `siblings` is the panel's own answer to which
       other stored row is the same recording, and `siblings_basis` names the
       rule it used. The rows stay apart, because two stored URLs is what the
       corpus holds; the mark says the other copy exists and opens it. */
    for (const other of or(e.siblings, [])) {
      const b = el("button", "cx-sib", "+1");
      b.title = "the same recording is stored again under another URL. "
        + or(e.siblings_basis, "");
      b.setAttribute("aria-label", "open the other copy of this recording");
      b.onclick = ev => { ev.stopPropagation(); goto3(other); };
      where.appendChild(b);
    }
    where.setAttribute("data-k", "where");
    tr.appendChild(where);

    const text = el("td");
    text.appendChild(stateChip(e.transcription_state));
    text.setAttribute("data-k", "transcript");
    tr.appendChild(text);

    const chars = el("td", "num", num(e.transcript_chars));
    chars.setAttribute("data-k", "chars");
    tr.appendChild(chars);

    const read = el("td", null, analysisWord(e));
    read.title = analysisWhy(e);
    read.setAttribute("data-k", "read");
    tr.appendChild(read);

    const clm = el("td", "num", String(e.claim_count));
    clm.setAttribute("data-k", "positions");
    tr.appendChild(clm);

    const gw = el("td", "num", num(e.gameweek));
    gw.title = e.gw_reason;
    gw.setAttribute("data-k", "gw");
    tr.appendChild(gw);
    return tr;
  }

  function analysisWord(e) {
    if (!e.analysis_present) return "no";
    if (e.claim_count === 0) return "yes, 0 stored";
    return "yes";
  }
  function analysisWhy(e) {
    if (!e.analysis_present)
      return "no stored analysis for this publication. Nothing was read out "
        + "of it, so there is nothing to open.";
    if (e.claim_count === 0)
      return `${or(e.analysis_model, "a model")} read this episode and stored `
        + "no position on any player. The summary is still there to open.";
    return `read by ${e.analysis_model}`;
  }

  /* The filter chips. Every one carries its own count, computed over the
     whole fetched list, so `all` and `transcribed` never disagree, and the
     default is every row. */
  function l2Chips(rows, state, onPick) {
    const bar = el("div", "toolbar");
    bar.appendChild(el("span", "tlabel", "show"));
    const counts = new Map();
    for (const e of rows)
      counts.set(e.transcription_state, or(counts.get(e.transcription_state), 0) + 1);
    const opts = [["all", rows.length]];
    for (const k of ["transcribed", "queued", "failed", "none"])
      if (counts.get(k)) opts.push([k, counts.get(k)]);
    for (const [k, n] of opts) {
      const b = el("button", "chip" + when(state === k, " on"), `${k} ${n}`);
      b.onclick = () => onPick(k);
      bar.appendChild(b);
    }
    return bar;
  }

  async function renderLevel2(creator) {
    openDrawer();
    drawer.appendChild(drawerHead(creator, "reading the archive…"));
    drawer.appendChild(skeleton(3));
    let r;
    try {
      r = await episodesFor(creator);
    } catch (e) {
      drawer.textContent = "";
      drawer.appendChild(drawerHead(creator, "the archive could not be read"));
      drawer.appendChild(failFold(e, "creator_episodes failed:"));
      return;
    }
    const d = r.result;
    if (!d || !d.episodes) {
      drawer.textContent = "";
      drawer.appendChild(drawerHead(creator, "nothing on file"));
      drawer.appendChild(emptyBox(or(d && d.reason, "The panel returned no episodes."),
        "The panel says what it read; nothing is inferred here."));
      return;
    }
    let filter = "all";
    const paint = () => {
      drawer.textContent = "";
      drawer.appendChild(drawerHead(d.creator, peopleWord(d)));
      drawer.appendChild(el("p", "cx-note", recordSentence(d.record)));
      drawer.appendChild(el("p", "cx-note",
        `${plural(d.counts.episodes_total, "publication")}, `
        + `${d.counts.transcribed} with a transcript on file, `
        + `${d.counts.analysed} with a stored analysis.`));

      const rows = d.episodes;
      /* Four of the 29 shows on the board have nothing in the corpus at all.
         That is a different answer from "nothing matches the filter", so it
         gets its own state and no chip bar to filter an empty list with. */
      if (!rows.length) {
        const box = emptyBox(
          "No publication is on file for this show.",
          "counts.episodes_total is 0, so there is nothing to filter and "
          + "nothing to open.");
        const why = boardReasonFor(d.creator);
        if (why) box.appendChild(el("div", "sub", noDash(why)));
        drawer.appendChild(box);
        drawer.appendChild(recordFold(d.creator));
        drawer.appendChild(teamFold(d.creator));
        drawer.appendChild(sourceFold());
        drawer.appendChild(provenance(r.provenance));
        return;
      }
      drawer.appendChild(l2Chips(rows, filter, k => { filter = k; paint(); }));
      let shown = rows;
      if (filter !== "all") shown = rows.filter(e => e.transcription_state === filter);
      if (!shown.length) {
        drawer.appendChild(emptyBox(
          `No publication is in the ${filter} state.`,
          "The counts on the chips are over every publication on file."));
      } else {
        drawer.appendChild(l2Table(shown));
      }
      /* The cap and the corpus are two different numbers and the page owes
         both. Fantasy Football Scout has 306 publications and the panel
         serves the newest 200, so "200 of 200 rows" on its own would read as
         the whole archive. */
      const held = d.counts.episodes_total - rows.length;
      let capLine = `${shown.length} of ${plural(rows.length, "row")} drawn.`;
      if (held > 0)
        capLine = `${shown.length} of the newest ${rows.length} publications `
          + `drawn. The panel's ${d.limit}-row cap is holding back `
          + `${plural(held, "older publication")}.`;
      drawer.appendChild(el("p", "sub", capLine
        + " One recording published to a podcast feed and to YouTube is two "
        + "stored URLs and two rows. Nothing stored links them, so nothing "
        + "here joins them by title; the mark beside the kind opens the other "
        + "copy."));
      drawer.appendChild(recordFold(d.creator));
      drawer.appendChild(teamFold(d.creator));
      drawer.appendChild(sourceFold());
      drawer.appendChild(provenance(r.provenance));
    };
    paint();
  }

  let l2sort = { key: "published", dir: -1 };

  /* The archive table. The head is rebuilt with the body on every sort so the
     arrow and `aria-sort` always describe the order actually drawn. */
  function l2Table(rows) {
    const wrap = el("div", "scroll-x");
    const t = el("table", "data sticky-first cx-l2");
    const thead = el("thead");
    const tb = el("tbody");
    const paint = () => {
      thead.textContent = "";
      const hr = el("tr");
      for (const col of L2COLS)
        hr.appendChild(sortHead(l2sort, col.key, col.label, col.numeric, spec => {
          l2sort = spec;
          paint();
        }));
      thead.appendChild(hr);
      tb.textContent = "";
      for (const e of sortRows(rows, l2sort, l2Value)) tb.appendChild(l2Row(e));
    };
    paint();
    t.append(thead, tb);
    wrap.appendChild(t);
    return wrap;
  }

  /* ---- the three level-2 folds, one call each, opened by hand ---------- */

  /* THE MEASURED RECORD. The wall of 31 tiles is gone; the card that matters
     is the one for the show already open, and it is fetched only when this
     fold is opened. */
  function recordFold(creator) {
    const det = el("details", "cx-disclose cx-method");
    det.appendChild(el("summary", null,
      "How this show's record is measured: interval, by action, by gameweek"));
    det.addEventListener("toggle", async () => {
      if (!det.open || det.dataset.built) return;
      det.dataset.built = "1";
      const body = el("div", "cx-methodbody");
      body.appendChild(el("p", "cx-honest", "measuring the record…"));
      det.appendChild(body);
      try {
        const r = await runPanel("creator_report_card", { creator });
        rc = r.result;
        rcErr = null;
        for (const c of or(rc.cards, [])) {
          rcByCreator.set(c.creator, c);
          for (const p of or(or(c.team, {}).people, [])) {
            if (p.entry_id == null) continue;
            if (!cardsByEntry.has(p.entry_id)) cardsByEntry.set(p.entry_id, new Set());
            cardsByEntry.get(p.entry_id).add(c.creator);
          }
        }
      } catch (e) {
        rcErr = e;
      }
      body.textContent = "";
      if (rcErr) {
        body.appendChild(failFold(rcErr, "The record could not be read:"));
        return;
      }
      body.appendChild(honestyLine());
      body.appendChild(recordLine(creator));
      const card = rcByCreator.get(creator);
      if (!card) {
        body.appendChild(emptyBox(
          or(rc.reason, "The report card holds no row for this show."),
          "A show with no scored claim has no card to draw."));
        return;
      }
      body.appendChild(el("p", "cx-why", card.headline));
      reportBody(card, body);
    });
    return det;
  }

  /* THE SHOW'S OWN FPL TEAM. The quiet holdings: what they own and never
     talk about. One call, the smallest `creator_detail` there is, opened by
     hand, and it is the only caller of a declared panel. */
  function teamFold(creator) {
    const det = el("details", "cx-disclose");
    det.appendChild(el("summary", null, "Their own FPL team, and the transfers behind it"));
    det.addEventListener("toggle", async () => {
      if (!det.open || det.dataset.built) return;
      det.dataset.built = "1";
      const body = el("div", "cx-methodbody");
      body.appendChild(el("p", "cx-honest", "reading their squad…"));
      det.appendChild(body);
      let d;
      try {
        const r = await runPanel("creator_detail", { creator, days: 1, limit: 1 });
        d = r.result;
      } catch (e) {
        body.textContent = "";
        body.appendChild(failFold(e, "creator_detail failed:"));
        return;
      }
      body.textContent = "";
      squadBody(d, body);
    });
    return det;
  }

  function squadBody(d, body) {
    const squad = or(d.squad, []);
    if (!squad.length) {
      body.appendChild(emptyBox(
        or(d.squad_reason, "No crawled squad is stored for this show."),
        "A show with no verified entry id has no squad to serve."));
      return;
    }
    body.appendChild(el("p", "sub",
      `Their GW${num(d.squad_gw)} fifteen, as crawled.`));
    const t = el("table", "data cx-squad");
    const hr = el("tr");
    for (const l of ["pos", "player", "price", "role"])
      hr.appendChild(el("th", when(l === "price", "num"), l));
    t.appendChild(hr);
    for (const p of squad) {
      const tr = el("tr");
      tr.appendChild(el("td", null, p.pos));
      tr.appendChild(el("td", null, p.name));
      tr.appendChild(el("td", "num", num(p.price)));
      let role = "in the fifteen";
      if (p.is_captain) role = "captain";
      else if (p.multiplier === 0) role = "bench";
      tr.appendChild(el("td", null, role));
      t.appendChild(tr);
    }
    body.appendChild(t);
    const moves = or(d.transfers, []);
    if (moves.length) {
      body.appendChild(el("p", "sub", `${plural(moves.length, "transfer")} on record.`));
    } else if (d.transfers_reason) {
      body.appendChild(el("p", "sub", d.transfers_reason));
    }
  }

  /* THE PASTE-A-LINK CONSOLE. An operator control, not part of reading what
     a show said, so it is a component mounted behind a fold. */
  function sourceFold() {
    const det = el("details", "cx-disclose");
    det.appendChild(el("summary", null, "Add a source by pasting a link"));
    det.addEventListener("toggle", () => {
      if (!det.open || det.dataset.built) return;
      det.dataset.built = "1";
      const box = el("div", "cx-inkhost");
      det.appendChild(box);
      inkHandle = mountIngestLink(box);
    });
    return det;
  }

  /* ============================================== level 3, one episode */

  /* THE SUMMARY. The stored bullets, as the list they are written as. */
  function summarySection(s, host2) {
    host2.appendChild(el("h2", null, "What this episode says"));
    const bullets = or(s.summary_bullets, []);
    if (!bullets.length) {
      host2.appendChild(el("p", "sub",
        or(s.summary_reason, "No summary is stored for this episode.")));
      return;
    }
    const ul = el("div", "cx-bullets");
    for (const b of bullets) ul.appendChild(el("p", "cx-bullet", noDash(b)));
    host2.appendChild(ul);
    if (s.analysis_model)
      host2.appendChild(el("p", "sub",
        `Read by ${s.analysis_model}, `
        + `${daysWord(ageDays(s.analysed_at))} ago. The bullets are the stored `
        + "summary, printed as stored."));
  }

  /* A call, drawn once: who, which way, how strongly, the quote, and the
     offset under the timestamp rule. */
  function callRow(c, episode, lead, channel) {
    const row = el("div", "cx-call");
    const head = el("div", "cx-callhead");
    if (lead) head.appendChild(el("span", "cx-rung", lead));
    head.appendChild(el("b", null, or(c.disambiguator, c.display_name)));
    head.appendChild(el("span", "cx-conv", or(c.stance, c.direction)));
    if (c.gameweek != null) head.appendChild(el("span", "cx-conv", `for GW${c.gameweek}`));
    if (!c.resolved)
      head.appendChild(el("span", "cx-conv warn", "name did not resolve to one player"));
    row.appendChild(head);
    if (c.quote) {
      row.appendChild(quoteBlock(noDash(c.quote), c, episode, channel));
    } else {
      row.appendChild(el("p", "sub",
        "No quote is stored with this call, so there is nothing to print and "
        + "no offset to open."));
    }
    return row;
  }

  function transfersSection(s, host2) {
    host2.appendChild(el("h2", null, "Transfers called"));
    const calls = or(s.transfers_suggested, []);
    if (!calls.length) {
      host2.appendChild(el("p", "sub",
        "The stored analysis carries no transfer call for this episode."));
      return;
    }
    const box = el("div", "cx-calls");
    for (const c of calls)
      box.appendChild(callRow(c, s.episode, c.direction,
                              { key: "call", label: `transfers ${c.direction}`,
                                note: "stored in the analysis under "
                                      + `transfers_${c.direction}` }));
    host2.appendChild(box);
    host2.appendChild(el("p", "sub",
      `${plural(calls.length, "call")}, stored as two independent lists. `
      + "Which sale funded which purchase is not recorded, so it is not drawn."));
  }

  /* CAPTAINCY, GROUPED BY THE GAMEWEEK EACH CALL TARGETS. Three of the four
     captain calls on the measured GW4 episode are about GW5, GW7 and GW9.
     Drawing them under the episode's own gameweek would say the show named
     four captains for one week, which it did not. */
  function captainSection(s, host2) {
    host2.appendChild(el("h2", null, "Captaincy"));
    const calls = or(s.captain_view, []);
    if (!calls.length) {
      host2.appendChild(el("p", "sub",
        "The stored analysis carries no captaincy call for this episode."));
      return;
    }
    const byGw = new Map();
    for (const c of calls) {
      const k = or(c.gameweek, "unstated");
      if (!byGw.has(k)) byGw.set(k, []);
      byGw.get(k).push(c);
    }
    const keys = [...byGw.keys()].sort((a, b) => {
      if (a === "unstated") return 1;
      if (b === "unstated") return -1;
      return a - b;
    });
    for (const k of keys) {
      const box = el("div", "cx-calls");
      let label = `GW${k}`;
      if (k === "unstated") label = "gameweek not stated on the call";
      box.appendChild(el("h3", null, label));
      for (const c of byGw.get(k))
        box.appendChild(callRow(c, s.episode, null,
                                { key: "call", label: "captaincy",
                                  note: "stored in the analysis under captaincy" }));
      host2.appendChild(box);
    }
  }

  /* EVERY PLAYER, most talked about first, each one folded. Twenty players
     carrying 45 stored positions is 30 KB of payload; open one at a time. */
  function playersSection(s, host2) {
    const players = or(s.players, []);
    host2.appendChild(el("h2", null,
      `Players named (${players.length})`));
    if (!players.length) {
      host2.appendChild(el("p", "sub",
        "No stored position names a player for this episode."));
      return;
    }
    for (const p of players) {
      const det = el("details", "cx-disclose cx-player");
      const sum = el("summary");
      sum.appendChild(el("b", null, or(p.disambiguator, p.display_name)));
      sum.appendChild(el("span", "sub",
        ` ${or(p.position, "")} ${or(p.team, "")} `
        + `· ${plural(p.claims.length, "position")}`));
      det.appendChild(sum);
      det.addEventListener("toggle", () => {
        if (!det.open || det.dataset.built) return;
        det.dataset.built = "1";
        const box = el("div", "cx-calls");
        if (!p.resolved)
          box.appendChild(el("p", "sub",
            `"${p.name}" is how the show said it. It did not resolve to one `
            + "player, so no code is attached and nothing is joined to it."));
        for (const c of p.claims) box.appendChild(callRow(c, s.episode, c.direction));
        det.appendChild(box);
      });
      host2.appendChild(det);
    }
  }

  function gapsSection(s, host2) {
    const gaps = or(s.gaps, []);
    if (!gaps.length) return;
    const det = el("details", "cx-disclose");
    det.appendChild(el("summary", null,
      `${plural(gaps.length, "empty section")}, and why`));
    const box = el("div", "cx-thin sub");
    for (const g of gaps)
      box.appendChild(el("div", null, `${g.section}: ${noDash(g.gap)}`));
    det.appendChild(box);
    host2.appendChild(det);
  }

  /* One quote, with where it came from and the offset under the timestamp
     rule. `stored_in` names the channel; the extractor tier is read off it
     for a claim and named as a call for an analysis list. */
  function quoteBlock(text, claim, item, channel) {
    const q = el("blockquote", "cx-quote");
    q.appendChild(el("span", "cx-qmark", "“"));
    q.append(text);
    q.appendChild(el("span", "cx-qmark", "”"));
    const foot = el("div", "cx-qfoot");
    const tr = or(channel, tier(claim && claim.stored_in, claim && claim.kind));
    const badge = el("span", "cx-tier " + tr.key, tr.label);
    badge.title = tr.note + when(tr.model, ` (${tr.model})`);
    foot.appendChild(badge);
    if (claim && claim.conviction)
      foot.appendChild(el("span", "cx-conv", `${claim.conviction} conviction`));
    else if (claim && claim.confidence != null)
      foot.appendChild(el("span", "cx-conv",
        `confidence ${Number(claim.confidence).toFixed(2)}`));
    const mark = atMark(claim, item);
    if (mark) foot.appendChild(mark);
    q.appendChild(foot);
    return q;
  }

  function episodeHead(s) {
    const head = el("div", "cx-ephead");
    const e = s.episode;
    head.appendChild(stateChip(e.transcription_state));
    head.append(`${kindShort(e.source_kind)} · published ${pubDate(e.published_at)}, `
                + `${daysWord(ageDays(e.published_at))} ago`);
    if (e.transcript_chars != null)
      head.append(` · ${e.transcript_chars.toLocaleString()} transcript characters`);
    if (e.source_url) {
      const a = el("a", "cx-at", "open the episode ↗");
      a.href = e.source_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      head.appendChild(a);
    }
    const gw = el("p", "sub", `GW${num(s.gameweek)}. ${noDash(s.gw_reason)}`);
    const box = el("div");
    box.append(head, gw);
    const sibs = or(e.siblings, []);
    if (sibs.length) {
      const p = el("p", "sub");
      p.append("The same recording is stored again under another URL, and the "
               + "two rows are kept apart. ");
      for (const other of sibs) {
        const b = el("button", "cx-back", "open the other copy");
        /* The rule that grouped them, verbatim, where a reader who doubts the
           grouping will look for it. It runs to six lines, which is longer
           than the finding it supports. */
        b.title = or(e.siblings_basis, "");
        b.onclick = () => goto3(other);
        p.appendChild(b);
      }
      box.appendChild(p);
    }
    return box;
  }

  async function renderLevel3(itemId) {
    openDrawer();
    drawer.appendChild(drawerHead("one episode", "reading the summary…"));
    drawer.appendChild(skeleton(2));
    let r;
    try {
      r = await summaryFor(itemId);
    } catch (e) {
      drawer.textContent = "";
      drawer.appendChild(drawerHead("one episode", "the summary could not be read"));
      drawer.appendChild(failFold(e, "episode_summary failed:"));
      return;
    }
    const s = r.result;
    drawer.textContent = "";
    if (!s || !s.episode) {
      drawer.appendChild(drawerHead("one episode", "nothing on file"));
      drawer.appendChild(emptyBox(or(s && s.reason, "The panel returned no episode."),
        "The item id in the address does not resolve to a stored publication."));
      return;
    }
    /* The breadcrumb reads the creator off this payload, so a cold load at
       this address costs one call and not two. */
    const back = el("button", "cx-back", `← ${s.creator}`);
    back.title = "back to this show's episodes";
    back.onclick = () => goto2(s.creator);
    const bar = el("div", "cx-backbar");
    bar.appendChild(back);
    drawer.appendChild(bar);
    drawer.appendChild(drawerHead(noDash(s.episode.title), s.creator));
    drawer.appendChild(episodeHead(s));
    summarySection(s, drawer);
    transfersSection(s, drawer);
    captainSection(s, drawer);
    playersSection(s, drawer);
    gapsSection(s, drawer);
    drawer.appendChild(provenance(r.provenance));
  }

  /* ====================================== the report card, kept and folded */

  /* The reusable chip: the show's name, which way they called it here, and
     their measured record from the report card. A legacy pseudo-source says
     so. Drawn inside the record fold, where the card has been fetched. */
  function rcChip(name, dir) {
    // "user-shared" is links pasted by hand, not a person; the owner asked
    // for it removed, not labelled. A comment node appends anywhere and
    // renders nothing, so no caller needs a null check.
    if (LEGACY.has(name)) return document.createComment("legacy pseudo-source omitted");
    const card = rcByCreator.get(name);
    const cl = card && card.claims;
    const v = verdict(cl, rc && rc.min_scored_claims);
    const b = el("button", "cx-rc " + v.cls);
    if (dir) b.appendChild(el("span", "cx-rcdir", dir));
    b.appendChild(el("b", null, name));
    let stat = "card loading";
    if (!card && rc) stat = "no card";
    else if (!card && rcErr) stat = "card unavailable";
    else if (card && !cl.measured) stat = unscored(cl, "none scored", true);
    else if (card) stat = `${pct(cl.hit_rate)} · n ${cl.n_scored} · ${v.word}`;
    b.appendChild(el("span", "cx-rcstat", stat));
    b.title = or(card && card.headline, "the report card has not loaded") + "\n"
      + when(card && cl && !cl.measured && cl.reason, `${cl && cl.reason}\n`)
      + "open this show's episodes";
    b.onclick = e => {
      e.stopPropagation();
      goto2(name);
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
    t.title = `${card.headline}\nsource creator_report_card`;
    return t;
  }

  /* THE HONESTY LINE, once. Computed from the report card, never typed. */
  function honestyLine() {
    if (rcErr) return failFold(rcErr, "The record could not be read:");
    const p = el("p", "cx-honest");
    if (!rc) { p.append("measuring the record…"); return p; }
    const earned = or(rc.cards, []).filter(c => c.claims.earned);
    if (earned.length) {
      p.appendChild(el("b", null,
        `${earned.map(c => c.creator).join(", ")} has earned a weight`));
      p.append(` (${earned.map(c => c.claims.weight.toFixed(2)).join(", ")}).`);
    } else {
      p.appendChild(el("b", null, "Nobody in this card has earned a weight."));
    }
    /* The board writes this sentence itself, from the same rows, and writes
       it better than any arithmetic here. */
    if (res && res.record_note) p.append(` ${noDash(res.record_note)}`);
    p.title = or(rc.note, "");
    return p;
  }

  /* One line: the measured record with its verdict and colour, then the team
     channel where one is measured. Reference material, never a rank. */
  function recordLine(show) {
    const line = el("div", "cx-recline");
    const card = rcByCreator.get(show);
    if (!card) {
      let word = "measuring the record…";
      if (rc) word = "no report card for this show";
      if (rcErr) word = `record unavailable: ${statusLine(rcErr)}`;
      line.appendChild(el("span", "cx-tiny", word));
      return line;
    }
    const cl = card.claims;
    const v = verdict(cl, rc.min_scored_claims);
    let word = unscored(cl, "none scored yet");
    if (cl.measured)
      word = `${pct(cl.hit_rate)} · ${cl.hits} of ${cl.n_scored} scored · ${v.word}`;
    const tag = el("span", "cx-rctag big " + v.cls, word);
    tag.title = or(cl.reason, or(card.headline, ""));
    line.appendChild(tag);
    const gutter = rcTag(show);
    if (gutter) line.appendChild(gutter);
    for (const p of or(or(card.team, {}).people, []).filter(p => p.n_gw)) {
      const t = el("span", "cx-recteam",
        `${p.person}: ${p.points} pts over ${plural(p.n_gw, "GW")}, `
        + `${signed(p.mean_delta)} per GW vs ${baselineWord(card.team)}`);
      t.title = or(p.reason, or(card.team.reason, ""));
      line.appendChild(t);
    }
    line.appendChild(el("span", "cx-tiny",
      `source creator_report_card, read ${relAge(rc.as_of).text}`));
    return line;
  }

  /* The Wilson interval as a range on a 0-100% track with the coin flip
     ticked at 50. The point is the hit rate; the band is what the data can
     actually claim. */
  function rangeBar(cl, big) {
    const w = el("div", "cx-range" + when(big, " big"));
    w.appendChild(el("span", "cx-range-mid"));
    const lo = or(cl.wilson_lo95, 0), hi = or(cl.wilson_hi95, 0);
    const band = el("span", "cx-range-band");
    band.style.left = `${(100 * lo).toFixed(1)}%`;
    band.style.width = `${(100 * Math.max(0, hi - lo)).toFixed(1)}%`;
    const pt = el("span", "cx-range-pt");
    pt.style.left = `${(100 * or(cl.hit_rate, 0)).toFixed(1)}%`;
    w.append(band, pt);
    const label = `hit rate ${pct(cl.hit_rate)}, 95% interval ${pct(lo)} to `
      + `${pct(hi)}; the tick is a coin flip`;
    w.title = label;
    w.setAttribute("role", "img");
    w.setAttribute("aria-label", label);
    return w;
  }

  function gwBars(byGw) {
    const row = el("div", "cx-gwbars");
    for (const g of byGw) {
      const col = el("span", "cx-gwbar" + when(!g.quotable, " thin"));
      const f = el("span", "cx-gwfill");
      f.style.height = `${Math.max(1, Math.round(22 * or(g.hit_rate, 0)))}px`;
      col.appendChild(f);
      col.appendChild(el("span", "cx-gwlab", `GW${g.gw}`));
      col.title = `GW${g.gw}: ${g.hits} of ${g.n} hit (${pct(g.hit_rate)})`
        + when(!g.quotable, "; under the floor");
      row.appendChild(col);
    }
    row.setAttribute("aria-label", "hit rate by gameweek");
    return row;
  }

  /* What the team channel is measured against, in the payload's own words.
     The baseline is a parameter of the card, not a constant, so the label is
     read rather than typed. */
  function baselineWord(tm) {
    const b = or(tm && tm.baseline, or(rc && rc.baseline, {}));
    return or(b.label, or(b.kind, "the baseline"));
  }

  /* THE FLOOR DECIDES THIS, not the boolean. `beats_baseline` can be true or
     false while `quotable` is false, and printing "yes" one line above the
     reason that says three gameweeks is under a ten gameweek floor is the
     card contradicting itself. */
  function baselineFact(p, tm) {
    if (!p.quotable)
      return `not established: ${plural(p.n_gw, "GW")} measured, floor is `
        + `${tm.min_gw_measured}`;
    if (p.beats_baseline == null) return "the interval spans zero, so neither";
    if (p.beats_baseline) return "yes";
    return "no";
  }

  /* The three channels, drawn into `host2`: the full card's body. */
  function reportBody(c, host2) {
    const cl = c.claims;
    host2.appendChild(el("h2", null, "Claims: binary, hit or flop"));
    if (cl.measured) {
      host2.appendChild(rangeBar(cl, true));
      const facts = el("dl", "cx-facts");
      const fact = (k, v) => {
        facts.appendChild(el("dt", null, k));
        facts.appendChild(el("dd", null, v));
      };
      fact("hit rate", `${pct(cl.hit_rate)} (${cl.hits} of ${cl.n_scored} scored`
        + pick(cl.n_total == null, ")", `, ${cl.n_total} recorded)`));
      fact("95% interval", `${pct(cl.wilson_lo95)} to ${pct(cl.wilson_hi95)}`);
      fact("vs coin flip", coin(cl.vs_coin_flip).word);
      fact("weight", `${Number(cl.weight).toFixed(2)} · `
        + pick(cl.earned, "earned", "not earned")
        + ` (floor ${cl.min_scored_claims})`);
      if (cl.first_claim_utc)
        fact("span", `${daysWord(ageDays(cl.first_claim_utc))} ago to `
          + `${daysWord(ageDays(cl.last_claim_utc))} ago`);
      host2.appendChild(facts);
      if (or(cl.by_gw, []).length) {
        host2.appendChild(el("h2", null, "By gameweek"));
        host2.appendChild(gwBars(cl.by_gw));
      }
      if (or(cl.by_action, []).length) {
        host2.appendChild(el("h2", null, "By action"));
        const t = el("table", "data cx-byaction");
        const hr = el("tr");
        for (const l of ["action", "n", "hit", "rate", "interval"])
          hr.appendChild(el("th", when(l !== "action", "num"), l));
        t.appendChild(hr);
        for (const a of cl.by_action) {
          const tr = el("tr", when(!a.quotable, "thin"));
          tr.appendChild(el("td", null, a.action));
          tr.appendChild(el("td", "num", String(a.n)));
          tr.appendChild(el("td", "num", String(a.hits)));
          tr.appendChild(el("td", "num", pct(a.hit_rate)));
          tr.appendChild(el("td", "num", `${pct(a.wilson_lo95)}–${pct(a.wilson_hi95)}`));
          tr.title = when(!a.quotable, "under the floor");
          t.appendChild(tr);
        }
        host2.appendChild(t);
      }
      if (cl.reason) host2.appendChild(el("p", "sub", cl.reason));
    } else {
      host2.appendChild(el("p", "sub", or(cl.reason, "no scored claim")));
    }

    const tm = c.team;
    host2.appendChild(el("h2", null, "Team: measured points, not opinion"));
    if (tm.measured) {
      for (const p of or(tm.people, [])) {
        const box = el("div", "cx-teamrow");
        const t = el("div", "cx-teamhead");
        t.appendChild(el("b", null, p.person));
        if (p.entry_id != null) {
          const a = el("a", "cx-cross", `entry ${p.entry_id} ↗`);
          a.href = `https://fantasy.premierleague.com/entry/${p.entry_id}/history`;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          t.appendChild(a);
          /* The same entry on more than one card is one manager's season
             appearing more than once. Say so beside the number, so the
             repeated points are never read as independent evidence. */
          const also = [...or(cardsByEntry.get(p.entry_id), [])]
            .filter(n => n !== c.creator);
          if (also.length) {
            const w = el("span", "cx-samewho", `also on ${also.join(", ")}`);
            w.title = `entry ${p.entry_id} is attached to `
              + `${plural(also.length + 1, "card")}. The gameweek points below `
              + "are that one manager's, repeated, not separate results.";
            t.appendChild(w);
            /* Each of those shows, with its own measured record, as a way in.
               The chip is the only place the record vocabulary appears with a
               name attached, and the click opens that show's episodes. */
            for (const n of also) t.appendChild(rcChip(n, null));
          }
        }
        box.appendChild(t);
        if (p.n_gw) {
          const facts = el("dl", "cx-facts");
          const fact = (k, v) => {
            facts.appendChild(el("dt", null, k));
            facts.appendChild(el("dd", null, v));
          };
          fact("points", `${p.points} over ${plural(p.n_gw, "GW")} `
            + `(${baselineWord(tm)} ${p.baseline_points})`);
          fact("delta per GW", `${signed(p.mean_delta)}`
            + when(p.delta_ci95, `, 95% ${signed(or(p.delta_ci95, [])[0])} to `
                   + `${signed(or(p.delta_ci95, [])[1])}`));
          fact("beats the baseline", baselineFact(p, tm));
          if (p.latest_overall_rank != null)
            fact("overall rank", p.latest_overall_rank.toLocaleString());
          box.appendChild(facts);
          if (or(p.gws, []).length) {
            const t2 = el("table", "data cx-byaction");
            const hr = el("tr");
            for (const l of ["GW", "pts", "baseline", "delta", "bench", "hits"]) {
              const th = el("th", when(l !== "GW", "num"), l);
              if (l === "baseline") th.title = baselineWord(tm);
              hr.appendChild(th);
            }
            t2.appendChild(hr);
            for (const g of p.gws) {
              const tr = el("tr");
              tr.appendChild(el("td", null, `GW${g.gw}`));
              tr.appendChild(el("td", "num", String(g.points)));
              tr.appendChild(el("td", "num", String(g.baseline_points)));
              tr.appendChild(el("td", "num", signed(g.delta)));
              tr.appendChild(el("td", "num", num(g.bench_points)));
              tr.appendChild(el("td", "num", num(g.hit_cost)));
              t2.appendChild(tr);
            }
            box.appendChild(t2);
          }
        }
        if (p.reason) box.appendChild(el("p", "sub", p.reason));
        host2.appendChild(box);
      }
      if (tm.reason) host2.appendChild(el("p", "sub", tm.reason));
    } else {
      host2.appendChild(el("p", "sub", or(tm.reason, "no verified team")));
    }

    const nu = c.numeric;
    host2.appendChild(el("h2", null, "Numeric: MAE and RMSE"));
    if (nu && nu.measured) {
      const facts = el("dl", "cx-facts");
      const fact = (k, v) => {
        facts.appendChild(el("dt", null, k));
        facts.appendChild(el("dd", null, v));
      };
      fact("MAE", `${nu.mae} (baseline ${nu.baseline_mae})`);
      fact("RMSE", `${nu.rmse} (baseline ${nu.baseline_rmse})`);
      fact("observations", `${nu.n_obs} over ${plural(nu.n_gw, "GW")}`);
      host2.appendChild(facts);
    } else {
      host2.appendChild(el("p", "sub",
        or(nu && nu.reason, "no numeric prediction published")));
    }
  }

  /* =========================================================== the loader */

  /* LEVEL 3 ON A COLD ADDRESS. `episode_summary` carries `creator`, so the
     deepest level costs exactly one call from a reload and the breadcrumb is
     read off the same payload. The board behind it is not fetched: nothing
     on this path needs it, and fetching it would make the deep link cost
     two calls to answer one question. */
  if (route.level === 3) {
    if (BOARD) {
      /* Walked down from level 1, so the board is already in hand and the
         page behind the drawer is the page the reader came from. */
      res = BOARD.result;
      prov = BOARD.provenance;
      renderLevel1();
    } else {
      mainCard.appendChild(el("h2", null, "Creators"));
      mainCard.appendChild(el("p", "cx-note",
        "One episode is open. The board behind it has not been read on this "
        + "visit, because the address asked for the episode."));
      const b = el("button", "cx-show", "read the board");
      b.onclick = goto1;
      mainCard.appendChild(b);
    }
    await renderLevel3(route.item);
    return;
  }

  /* Levels 1 and 2 both draw the board behind them, from the cache after the
     first fetch, so walking down and back up costs one call per click. */
  try {
    const r = await boardOnce();
    res = r.result;
    prov = r.provenance;
  } catch (e) {
    mainCard.appendChild(el("h2", null, "Creators"));
    mainCard.appendChild(failFold(e, "creator_board failed:"));
    return;
  }
  renderLevel1();
  if (route.level === 2) await renderLevel2(route.creator);
}
