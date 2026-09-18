/* App shell: API client, hash router, theme, shared components.
   Zero-build by decision (DESIGN.md §2.2): ES modules served as-is.
   Panels are the ONLY data path, no SQL leaves this file's runPanel().

   Everything under "shared components" below is the single implementation of
   a thing the app does in more than one place. A view that hand-rolls one of
   them is how nine tabs ended up with four sortable headers, four drawers,
   two club-badge builders and twenty-seven type sizes. */

import { crest } from "/js/components/clubmark.js";
import { sortIcon } from "/js/components/icons.js";

const API = "";

// ---------- api ----------
export async function runPanel(script, params = {}) {
  const r = await fetch(`${API}/api/scripts/${script}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ params }),
  });
  if (!r.ok) throw new Error(`${script}: HTTP ${r.status} ${await r.text()}`);
  return r.json(); // {result, provenance}
}
export async function getJSON(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}
export async function postJSON(path, body) {
  const r = await fetch(API + path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status} ${await r.text()}`);
  return r.json();
}

// ---------- tiny dom ----------
export function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

// ---------- shared components ----------
export function emptyBox(reason, hint) {
  const d = el("div", "empty");
  d.appendChild(el("b", null, "Nothing to show"));
  d.appendChild(document.createTextNode(reason || "No data."));
  if (hint) { d.appendChild(el("div", "sub", hint)); }
  return d;
}

export function errBox(e) { return el("div", "err", String(e.message || e)); }

/* THE GAP STATE. A fact the payload says it does not have, shown as absence
   with the payload's own reason and the action that would fill it. Never
   errBox: a field the panel could not compute is not a failed request, and a
   red box that means both teaches the reader to ignore red. */
export function gapBox(field, reason, action) {
  const d = el("div", "gap");
  d.appendChild(el("b", null, field));
  d.appendChild(document.createTextNode(
    reason || "the payload carries no value and gave no reason"));
  if (action) d.appendChild(el("span", "fix", action));
  return d;
}

/* THE STALE MARKER. Data that exists and is older than its window. One chip
   on the block it qualifies, the age in days, the source and the window in
   the title. A stale block still renders its data; it is not an error. */
export function staleChip(iso, opts = {}) {
  const c = el("span", "chip warn");
  const age = fmtAgeDays(iso);
  let text = "stale";
  if (age) text = `stale · ${age}`;
  c.textContent = text;
  const bits = [];
  if (opts.source) bits.push(`source ${opts.source}`);
  if (opts.window) bits.push(`stale after ${opts.window}`);
  const abs = absInstant(iso);
  if (abs) bits.push(`last good ${abs}`);
  c.title = bits.join("\n");
  return c;
}

/* THE LOADING SURFACE. A skeleton at the final row count and row height, so
   the arrival of data moves nothing below it. */
export function skeleton(rows, cols) {
  const t = el("table", "skel");
  for (let i = 0; i < rows; i++) {
    const tr = el("tr");
    for (let j = 0; j < cols; j++) {
      const td = el("td");
      td.appendChild(el("i"));
      tr.appendChild(td);
    }
    t.appendChild(tr);
  }
  return t;
}

/* THE ROW COUNT under a filtered table: what is shown, out of what, and the
   named filters that removed the rest. */
export function rowCount(shown, total, removedBy = []) {
  const bits = [`${shown} of ${total}`];
  if (removedBy.length) bits.push(`filtered by ${removedBy.join(", ")}`);
  return el("p", "rowcount", bits.join(" · "));
}

export function provenance(prov) {
  if (!prov) return el("span");
  // The age, not the raw ISO microsecond stamp: every tab's footer printed
  // "2026-09-08T05:37:11.501911+00:00" directly under body text saying "read
  // just now". The exact instant stays reachable in the title.
  const bits = [prov.script, (prov.repo_sha || "").slice(0, 7),
                agePhrase(prov.generated_at, "read")].filter(Boolean);
  const d = el("div", "provenance", bits.join(" · "));
  const abs = absInstant(prov.generated_at);
  if (abs) d.title = `generated ${abs}`;
  return d;
}

/* inline magnitude bar: sequential single hue, value printed beside (the
   dataviz contrast-WARN relief obligation: never color alone). */
export function bar(value, max, text) {
  const span = el("span");
  const b = el("span", "bar");
  b.style.width = `${Math.max(2, Math.round(46 * (value / (max || 1))))}px`;
  span.append(b, document.createTextNode(text ?? String(value)));
  return span;
}

/* ---------- THE SORTABLE HEADER ----------
   One implementation for every table in the app (R11, R12). It carries
   aria-sort, a role, a tabindex, Enter and Space, and a persistent mark: the
   direction on the sorted column, a quiet sort-none on the rest, drawn as
   the same 16px icon everywhere so a block of like columns keeps one width.

   `opts`:
     active   this column is the sorted one
     dir      its current direction, -1 descending or 1 ascending
     first    the direction it opens in from unsorted. A magnitude column
              opens descending, a name or team column ascending. Defaults to
              descending for a numeric column and ascending for a text one,
              which is the rule stated once rather than per call site.
     num      false for a text column (default: numeric)
     title    the qualifier for the WHOLE column, which is the only metadata
              a header may carry (R6)
     onSort   called with the direction to apply next */
export function sortableTh(label, opts = {}) {
  const num = opts.num !== false;
  let first = opts.first;
  if (first == null) {
    first = -1;
    if (!num) first = 1;
  }
  const active = opts.active === true;
  let dir = opts.dir;
  if (dir == null) dir = first;
  let aria = "none";
  if (active && dir === 1) aria = "ascending";
  if (active && dir === -1) aria = "descending";

  let cls = "sortable";
  if (num) cls = "num sortable";
  if (active) cls += " sorted";
  const th = el("th", cls);
  th.setAttribute("role", "columnheader");
  th.setAttribute("aria-sort", aria);
  th.tabIndex = 0;
  if (opts.title) th.title = opts.title;
  const box = el("span", "th-in");
  box.appendChild(el("span", null, label));
  box.appendChild(sortIcon(aria));
  th.appendChild(box);

  const go = () => {
    let next = first;
    if (active) next = -dir;
    if (opts.onSort) opts.onSort(next);
  };
  th.onclick = go;
  th.onkeydown = (e) => {
    if (e.key !== "Enter" && e.key !== " " && e.key !== "Spacebar") return;
    e.preventDefault();              // Space would scroll the page
    go();
  };
  return th;
}

/* ---------- THE CLICKABLE ROW ----------
   One affordance across the whole row: the pointer, the hover background it
   already had, a focus ring, a tab stop and Enter or Space (R18). A row with
   no handler gets none of this, which is the other half of the rule (R19). */
export function rowLink(tr, onOpen, label) {
  tr.classList.add("rowlink");
  tr.tabIndex = 0;
  tr.setAttribute("role", "button");
  if (label) tr.setAttribute("aria-label", label);
  tr.onclick = onOpen;
  tr.onkeydown = (e) => {
    if (e.key !== "Enter" && e.key !== " " && e.key !== "Spacebar") return;
    e.preventDefault();
    onOpen();
  };
  return tr;
}

/* FPL's public image CDN, keyed by the same stable ids the warehouse uses.
   Both verified live: photos p{code}.png, badges t{team_code}.png. Club
   badges go through crest() in components/clubmark.js and are built nowhere
   else (R36). The images are lazy and the browser caches them; serving them
   from this app instead needs a route, which is a backend change. */
const PHOTO = c => `https://resources.premierleague.com/premierleague/photos/players/110x140/p${c}.png`;

/* A friendly placeholder for the players FPL's CDN has no photo for:
   a little jersey figure in theme-agnostic greys (fplreview does the same
   with a cartoon), so the layout never collapses and no card looks broken.
   This is the fallback for the two LARGE faces, the 64px pitch card and the
   56x68 drawer masthead, where a drawn figure reads. The 24px table-row
   avatar falls back to initials instead, because a jersey at 24px round is
   a grey smudge; both fallbacks sit in the identical CSS-sized box. */
const FACE_FALLBACK = "data:image/svg+xml," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 110 140">
     <rect width="110" height="140" fill="#8a919b" opacity="0.18"/>
     <circle cx="55" cy="52" r="24" fill="#8a919b" opacity="0.55"/>
     <path d="M20 140 v-20 c0-22 16-34 35-34 s35 12 35 34 v20 z"
           fill="#8a919b" opacity="0.55"/>
     <path d="M38 92 l-12 8 v40 h12 z M72 92 l12 8 v40 h-12 z"
           fill="#8a919b" opacity="0.35"/>
   </svg>`);

export function faceImg(code, cls) {
  const img = el("img", cls);
  img.loading = "lazy";
  img.decoding = "async";
  img.alt = "";
  img.src = PHOTO(code);
  img.onerror = () => { img.onerror = null; img.src = FACE_FALLBACK; };
  return img;
}

/* Up to two initials from a player's name, for the avatar fallback. */
function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  const first = parts[0][0];
  if (parts.length === 1) return first.toUpperCase();
  return (first + parts[parts.length - 1][0]).toUpperCase();
}

/* ---------- THE AVATAR ----------
   The 24px round face for a table row (R35). The image and the monogram
   share one box that CSS sizes before the load, so a 404 flips one class and
   moves nothing (R37, R47). Lazy and async-decoded, and the browser caches
   the CDN response, so re-sorting a table costs no request.
   `opts`: { mine } draws the squad ring ON the avatar, never beside it. */
export function avatarEl(code, name, opts = {}) {
  let cls = "avatar";
  if (opts.mine) cls += " mine";
  if (opts.cls) cls += ` ${opts.cls}`;
  const wrap = el("span", cls);
  const img = el("img");
  img.loading = "lazy";
  img.decoding = "async";
  img.alt = "";
  if (code == null) wrap.classList.add("fall");
  else img.src = PHOTO(code);
  img.addEventListener("error", () => wrap.classList.add("fall"));
  wrap.appendChild(img);
  const mono = el("span", "mono", initials(name));
  mono.setAttribute("aria-hidden", "true");
  wrap.appendChild(mono);
  return wrap;
}

/* The pitch card: photo, name, sub-line, captain/vice ribbon, status dot,
   club mark. `p` needs {code, name}; everything else optional. The club mark
   is crest(), the app's one badge builder: the old second builder removed
   itself on error, which is a layout shift by design. */
export function playerCard(p, { mark, sub } = {}) {
  let cls = "pcard";
  if (mark === "C") cls = "pcard cap";
  const d = el("div", cls);
  d.appendChild(faceImg(p.code, "face"));
  if (p.team_code != null) {
    const b = crest(p.team_code, p.team, "s20");
    b.classList.add("badge");
    d.appendChild(b);
  }
  if (mark) {
    let ribbon = "ribbon";
    if (mark === "V") ribbon = "ribbon v";
    d.appendChild(el("div", ribbon, mark));
  }
  if (p.status && p.status !== "a") {
    let dot = "dot";
    if (["i", "s", "u"].includes(p.status)) dot = "dot bad";
    d.appendChild(el("div", dot, "")).title = p.news || `status ${p.status}`;
  }
  d.appendChild(el("div", "nm", p.name));
  const line = sub ?? [p.price != null ? fmtPrice(p.price) : null,
                       p.xpts != null ? fmt1(p.xpts) : null]
    .filter(Boolean).join(" · ");
  d.appendChild(el("div", "sub", line || "–"));
  return d;
}

export function stat(value, label, cls) {
  const d = el("div", "stat" + (cls ? " " + cls : ""));
  d.appendChild(el("div", "v", value));
  d.appendChild(el("div", "k", label));
  return d;
}

export function fmtPrice(p) { return p == null ? "–" : `£${Number(p).toFixed(1)}`; }
export function fmt1(x) { return x == null ? "–" : Number(x).toFixed(1); }
export function fmt2(x) { return x == null ? "–" : Number(x).toFixed(2); }

/* THE age/duration vocabulary, shared by every view: a span in hours reads
   "Nh Mm" under two days, "Nd Nh" from two days, "N days" past fourteen.
   Reachable from the deadline countdown only (R24): inside 24 hours the
   minutes are the decision, and everywhere else an age is days. */
export function fmtSpan(hours) {
  if (hours == null || !isFinite(hours)) return "?";
  const h = Math.max(0, Number(hours));
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m`;
  if (h < 48) {
    let H = Math.floor(h), M = Math.round((h - H) * 60);
    if (M === 60) { H += 1; M = 0; }
    return `${H}h ${M}m`;
  }
  if (h < 14 * 24) {
    let D = Math.floor(h / 24), H = Math.round(h - D * 24);
    if (H === 24) { D += 1; H = 0; }
    return H ? `${D}d ${H}h` : `${D}d`;
  }
  return `${Math.round(h / 24)} days`;
}

/* The one parser: ISO, or "YYYY-MM-DD HH:MM", or an already-Z stamp. */
function whenMs(iso) {
  if (!iso) return null;
  const d = new Date(String(iso).replace(" ", "T").replace(/\+00:00$/, "Z"));
  if (isNaN(d)) return null;
  return d.getTime();
}

/* age of a stamp, through fmtSpan; null when unparseable */
export function fmtAge(iso) {
  const ms = whenMs(iso);
  if (ms == null) return null;
  return fmtSpan((Date.now() - ms) / 3.6e6);
}

/* THE AGE VOCABULARY every surface prints (R23). Days, and words under two
   of them: "today", "yesterday", "4 days". "16h 48m" is arithmetic; nobody
   reading a pipeline row needs the minutes, and mixing "3h 13m" beside
   "18 days" in one line of chips made neither readable. The exact instant
   stays reachable in a title, through absInstant, never in the
   visible text (R25). */
export function fmtAgeDays(iso) {
  const ms = whenMs(iso);
  if (ms == null) return null;
  const h = (Date.now() - ms) / 3.6e6;
  if (h < 24) return "today";
  if (h < 48) return "yesterday";
  return `${Math.floor(h / 24)} days`;
}

/* The same age as a sentence fragment: "read today", "read yesterday",
   "read 4 days ago". The word "ago" is wrong on the first two, so the verb
   and the span are built together instead of concatenated at each call. */
export function agePhrase(iso, verb) {
  const text = fmtAgeDays(iso);
  if (text == null) return null;
  const head = verb || "";
  if (text === "today" || text === "yesterday") return `${head} ${text}`.trim();
  return `${head} ${text} ago`.trim();
}

/* The exact instant, for a title. Minute precision: the microseconds in the
   raw stamp are noise nobody reads. */
export function absInstant(iso) {
  const ms = whenMs(iso);
  if (ms == null) return null;
  return `${new Date(ms).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/* ---------- THE BREADCRUMB ----------
   The fractal path, one segment per level, every segment but the last a
   button back to that level in the same drawer (R21). `segments` is
   [{label, go}]; the last entry's `go` is ignored, because you are there. */
export function crumbEl(segments) {
  const nav = el("nav", "crumb");
  nav.setAttribute("aria-label", "path");
  segments.forEach((seg, i) => {
    const last = i === segments.length - 1;
    if (i > 0) nav.appendChild(el("span", null, "/"));
    if (last || !seg.go) {
      nav.appendChild(el("span", "here", seg.label));
      return;
    }
    const b = el("button", null, seg.label);
    b.type = "button";
    b.onclick = seg.go;
    nav.appendChild(b);
  });
  return nav;
}

/* ---------- THE DRAWER ----------
   ONE drawer for every level of the fractal (R20): one width, opening from
   the right, Escape and a click outside close it, focus moves in on open and
   returns to the element that opened it, and Tab cycles inside while it is
   open. Leaving the view that owns it unmounts the aside and its listeners,
   so re-entering never stacks a second one on the body.

   `viewName` is the hash route that owns the drawer. Returns
   { drawer, open, close, setHandles }. */
export function makeDrawer(viewName, label = "detail") {
  document.querySelectorAll(`aside.drawer[data-view="${viewName}"]`)
    .forEach(n => n.remove());
  const drawer = el("aside", "drawer");
  drawer.dataset.view = viewName;
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-modal", "true");
  drawer.setAttribute("aria-label", label);
  drawer.tabIndex = -1;
  document.body.appendChild(drawer);

  let handles = [];
  let opener = null;
  const close = () => {
    drawer.classList.remove("open");
    for (const h of handles) h?.cancel?.();
    handles = [];
    if (opener && opener.isConnected
        && drawer.contains(document.activeElement)) opener.focus();
    opener = null;
  };
  const isOpen = () => drawer.classList.contains("open");
  const onKey = (e) => {
    if (!drawer.isConnected) { removeEventListener("keydown", onKey); return; }
    if (e.key === "Escape") { close(); return; }
    if (e.key !== "Tab" || !isOpen()) return;
    const focusables = drawer.querySelectorAll(
      "button, a[href], input, select, textarea, [tabindex]:not([tabindex='-1'])");
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    } else if (!drawer.contains(document.activeElement)) {
      e.preventDefault(); first.focus();
    }
  };
  addEventListener("keydown", onKey);
  // a click outside closes: the drawer is a modal surface, and reaching for
  // the page behind it is the commonest way to dismiss one
  const onClick = (e) => {
    if (!drawer.isConnected || !isOpen()) return;
    if (drawer.contains(e.target)) return;
    close();
  };
  addEventListener("click", onClick, true);
  const onHash = () => {
    close();
    if ((location.hash || "").slice(1).split("?")[0] === viewName) return;
    drawer.remove();
    removeEventListener("hashchange", onHash);
    removeEventListener("keydown", onKey);
    removeEventListener("click", onClick, true);
  };
  addEventListener("hashchange", onHash);

  return {
    drawer,
    close,
    open: () => {
      if (!isOpen()) {
        opener = null;
        if (document.activeElement instanceof HTMLElement)
          opener = document.activeElement;
      }
      drawer.classList.add("open");
      drawer.scrollTop = 0;
      drawer.focus();
    },
    setHandles: (hs) => { handles = hs; },
  };
}

/* The drawer's masthead: the face, the name, the sub-line and the one close
   control. `opts.face` is an element (an avatar or a big face), `opts.crumb`
   the breadcrumb segments for crumbEl. */
export function drawerHead(title, sub, opts = {}) {
  const box = el("div");
  if (opts.crumb && opts.crumb.length) box.appendChild(crumbEl(opts.crumb));
  const head = el("div", "dhead");
  if (opts.face) head.appendChild(opts.face);
  const id = el("div");
  id.appendChild(el("div", "dname", title));
  if (sub) id.appendChild(el("div", "sub", sub));
  head.appendChild(id);
  if (opts.onClose) {
    const b = el("button", null, "Close");
    b.type = "button";
    b.onclick = opts.onClose;
    head.appendChild(b);
  }
  box.appendChild(head);
  return box;
}

// ---------- theme (explicit choice wins; else OS preference) ----------
const THEME_KEY = "itest-theme";
export function initTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved) document.documentElement.dataset.theme = saved;
}
export function toggleTheme() {
  const cur = document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem(THEME_KEY, next);
}

// ---------- deadline chip ----------
export async function mountDeadline(node) {
  try {
    const d = await getJSON("/api/deadline");
    const when = new Date(d.deadline_utc);
    const tick = () => {
      const ms = when - Date.now();
      if (ms <= 0) { node.textContent = `GW${d.gw} deadline passed`; return; }
      const hours = ms / 3.6e6;
      // urgency register: the countdown changes voice inside 24h. This is the
      // one surface that keeps hours and minutes (R24): inside a day, the
      // minutes are the decision.
      node.classList.toggle("urgent", hours < 24);
      node.innerHTML = "";
      node.append(`GW${d.gw} deadline in `);
      const b = el("b", null, fmtSpan(hours));
      node.append(b, ` · ${when.toUTCString().slice(0, 22)} UTC`);
    };
    tick(); setInterval(tick, 30_000);
  } catch { node.textContent = "deadline unavailable"; }
}

// ---------- router ----------
const routes = {};   // name -> {title, load: (host) => Promise<void>}
export function register(name, title, load) { routes[name] = { title, load }; }

export async function navigate() {
  const name = (location.hash || "#home").slice(1).split("?")[0];
  const route = routes[name] || routes.home;
  document.querySelectorAll(".rail a").forEach(a =>
    a.classList.toggle("active", a.getAttribute("href") === `#${name}`));
  const host = document.getElementById("view");
  host.textContent = "";
  document.getElementById("view-title").textContent = route.title;
  try { await route.load(host); }
  catch (e) { host.appendChild(errBox(e)); }
}

export function start() {
  initTheme();
  document.getElementById("theme-btn").onclick = toggleTheme;
  mountDeadline(document.getElementById("deadline"));
  addEventListener("hashchange", navigate);
  navigate();
}
