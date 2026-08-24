/* App shell: API client, hash router, theme, shared components.
   Zero-build by decision (DESIGN.md §2.2): ES modules served as-is.
   Panels are the ONLY data path — no SQL leaves this file's runPanel(). */

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

export function provenance(prov) {
  if (!prov) return el("span");
  const bits = [prov.script, (prov.repo_sha || "").slice(0, 7),
                prov.generated_at].filter(Boolean);
  return el("div", "provenance", bits.join(" · "));
}

/* Sortable data table. Sorting re-renders ONLY tbody — the provenance line
   outside survives (the old UI deleted it on first header click). */
export function dataTable(columns, rows, host) {
  const wrap = el("div", "scroll-x");
  const table = el("table", "data");
  const thead = el("thead"); const tbody = el("tbody");
  table.append(thead, tbody); wrap.appendChild(table);
  let sortKey = null, sortDir = -1;

  const tr = el("tr");
  for (const c of columns) {
    const th = el("th", c.num ? "num" : "", c.label);
    th.onclick = () => {
      sortDir = sortKey === c.key ? -sortDir : -1; sortKey = c.key;
      [...thead.querySelectorAll("th")].forEach(h => h.classList.remove("sorted"));
      th.classList.add("sorted");
      renderBody();
    };
    tr.appendChild(th);
  }
  thead.appendChild(tr);

  function renderBody() {
    const data = [...rows];
    if (sortKey) data.sort((a, b) => {
      const x = a[sortKey], y = b[sortKey];
      if (x == null) return 1; if (y == null) return -1;
      return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * -sortDir;
    });
    tbody.textContent = "";
    for (const r of data) {
      const trr = el("tr");
      for (const c of columns) {
        const td = el("td", c.num ? "num" : "");
        if (c.render) { const out = c.render(r); out && td.appendChild(out); }
        else td.textContent = r[c.key] ?? "–";
        trr.appendChild(td);
      }
      tbody.appendChild(trr);
    }
  }
  renderBody();
  if (host) host.appendChild(wrap);
  return wrap;
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

export function fmtPrice(p) { return p == null ? "–" : `£${Number(p).toFixed(1)}`; }
export function fmt1(x) { return x == null ? "–" : Number(x).toFixed(1); }
export function fmt2(x) { return x == null ? "–" : Number(x).toFixed(2); }

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
      const h = Math.floor(ms / 3.6e6), m = Math.floor(ms % 3.6e6 / 6e4);
      node.innerHTML = "";
      node.append(`GW${d.gw} deadline in `);
      const b = el("b", null, `${h}h ${m}m`);
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
