/* The SHARED player drawer — one drawer, mounted from any view that puts a
   player in focus (xPoints, the Dashboard). Extracted from the xPoints view
   so the dashboard's pitch cards and the projection matrix open the SAME
   surface: per-source projections, the percentile pizza (player_radar), the
   Understat profile with its fetch-on-demand gap, and the panel chatter
   strip.

   Section order is load-bearing (FINAL_SPEC §9): matrix → pizza → Understat
   → chatter. The last three mount in a `finally` so they appear on every
   path — including the no-projections one — and can never block the matrix.
   The pizza never mentions Understat: two sources, two sections, two clocks.

   Drawer lifecycle is the fixtures idiom: one drawer per visit (re-entering
   a view must not stack a second aside on the body), Escape closes, leaving
   the view unmounts it and its key handler. */

import { runPanel, postJSON, getJSON, el, emptyBox, errBox, faceImg,
         fmtPrice, fmt1, fmt2 } from "/js/app.js";
import { chatterStrip } from "/js/components/chatter.js";
import { radarSection } from "/js/components/radar.js";

/* Mount the drawer for a view. `viewName` is the hash route that owns it —
   navigating anywhere else removes the drawer and its listeners. */
export function attachPlayerDrawer(viewName) {
  document.querySelectorAll("aside.pd-drawer").forEach(n => n.remove());
  const drawer = el("aside", "drawer pd-drawer");
  // dialog semantics: assistive tech must know this is a modal surface, and
  // focus must move IN on open and RETURN on close (Escape already closes)
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-modal", "true");
  drawer.setAttribute("aria-label", "player detail");
  drawer.tabIndex = -1;
  document.body.appendChild(drawer);
  let handles = [];
  let opener = null;           // the element focus returns to on close
  const close = () => {
    drawer.classList.remove("open");
    for (const h of handles) h?.cancel?.();
    handles = [];
    if (opener && opener.isConnected
        && drawer.contains(document.activeElement)) opener.focus();
    opener = null;
  };
  const onKey = (e) => {
    if (!drawer.isConnected) { removeEventListener("keydown", onKey); return; }
    if (e.key === "Escape") close();
    // a minimal focus trap: Tab cycles inside the open dialog
    if (e.key === "Tab" && drawer.classList.contains("open")) {
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
    }
  };
  addEventListener("keydown", onKey);
  const onHash = () => {
    close();
    if ((location.hash || "").slice(1).split("?")[0] !== viewName) {
      drawer.remove();
      removeEventListener("hashchange", onHash);
      removeEventListener("keydown", onKey);
    }
  };
  addEventListener("hashchange", onHash);
  return {
    drawer,
    close,
    open: () => {
      if (!drawer.classList.contains("open"))
        opener = document.activeElement instanceof HTMLElement
          ? document.activeElement : null;
      drawer.classList.add("open");
      drawer.scrollTop = 0;
      drawer.focus();
    },
    setHandles: (hs) => { handles = hs; },
  };
}

/* Show one player. `p` needs {code, name}; pos/team/price/own_pct optional.
   opts: { gw }   — anchor gameweek for the per-source pivot
         { actuals } — {gw: points} map of settled actuals, when the caller
                       has them (the xPoints matrix does; the pitch does not) */
export async function showPlayerDetail(dh, p, opts = {}) {
  const { drawer } = dh;
  dh.close();                    // cancel any previous sections' handles
  drawer.textContent = "";
  dh.open();
  drawer.appendChild(el("p", "sub", "loading…"));
  try {
    const { result } = await runPanel("projection_table",
      { gw: opts.gw ?? "next", span: 8, detail_code: p.code, limit: 1 });
    drawer.textContent = "";

    // header: who this is
    const head = el("div", "dhead");
    head.appendChild(faceImg(p.code, "bigface"));
    const id = el("div");
    id.appendChild(el("div", "dname", p.name));
    id.appendChild(el("div", "sub",
      [p.pos, p.team, p.price != null ? fmtPrice(p.price) : null,
       p.own_pct != null ? fmt1(p.own_pct) + "% owned" : null]
        .filter(Boolean).join(" · ")));
    head.appendChild(id);
    const close = el("button", null, "✕");
    close.onclick = dh.close;
    head.appendChild(close);
    drawer.appendChild(head);

    // settled reality first, if the caller carries it
    const acts = opts.actuals || {};
    const settled = Object.keys(acts).sort();
    if (settled.length)
      drawer.appendChild(el("p", "sub", "Settled: " + settled.map(g =>
        `GW${g} → ${Math.round(acts[g])} pts`).join(" · ")));

    // pivot: sources × gameweeks
    const rowsD = (result.detail?.rows) || [];
    if (!rowsD.length) {
      drawer.appendChild(emptyBox("no per-source projections for this player"));
      return;
    }
    const srcs = [...new Set(rowsD.map(x => x.source))];
    const dgws = [...new Set(rowsD.map(x => x.gw))].sort((a, b) => a - b);
    const at = (src, g) =>
      rowsD.find(x => x.source === src && x.gw === g) || {};
    let dmax = 0.001;
    for (const x of rowsD) if (x.xpts > dmax) dmax = x.xpts;

    drawer.appendChild(el("h2", null, "Projected points by source"));
    const wrap = el("div", "scroll-x");
    const t = el("table", "data");
    const hd = el("tr");
    hd.appendChild(el("th", null, "source"));
    for (const g of dgws) hd.appendChild(el("th", "num", `GW${g}`));
    const th_ = el("thead"); th_.appendChild(hd); t.appendChild(th_);
    const tb = el("tbody");
    for (const src of srcs) {
      const tr = el("tr");
      tr.appendChild(el("td", null, src.replace(/^gh_/, "")));
      for (const g of dgws) {
        const v = at(src, g).xpts;
        const td = el("td", "num");
        if (v == null) td.textContent = "–";
        else {
          td.textContent = fmt1(v);
          const pct = Math.min(45, Math.round(45 * v / dmax));
          td.style.background =
            `color-mix(in oklab, var(--s1) ${pct}%, var(--surface))`;
        }
        tr.appendChild(td);
      }
      tb.appendChild(tr);
    }
    // consensus row, bold
    const cr = el("tr");
    cr.appendChild(el("td", null, "consensus"));
    cr.style.fontWeight = "700";
    for (const g of dgws) {
      const vs = srcs.map(sc => at(sc, g).xpts).filter(v => v != null);
      cr.appendChild(el("td", "num",
        vs.length ? fmt1(vs.reduce((a, b) => a + b, 0) / vs.length) : "–"));
    }
    tb.appendChild(cr);
    t.appendChild(tb); wrap.appendChild(t); drawer.appendChild(wrap);

    // appearance odds, where any source publishes them
    const pa = rowsD.filter(x => x.p_appear != null);
    if (pa.length) {
      const first = pa[0];
      drawer.appendChild(el("p", "sub",
        `p(appear) ${fmt2(first.p_appear)}` +
        (first.xp_if_appears != null
          ? ` · ${fmt1(first.xp_if_appears)} xPts if he plays` : "") +
        ` (${pa[0].source.replace(/^gh_/, "")})`));
    }
    drawer.appendChild(el("p", "sub",
      "Row tint = that source's own scale. The gap between rows IS the " +
      "uncertainty."));
  } catch (e) { drawer.textContent = ""; drawer.appendChild(errBox(e)); }
  finally {
    // Mounted last and in `finally` so they appear on every path — including
    // the no-projections one — and never block or break the drawer above.
    // Order per FINAL_SPEC §9: pizza between the matrix and Understat.
    const radar = radarSection(drawer, p.code);
    const profile = profileSection(drawer, p.code);
    const chatter = chatterStrip(drawer, p.code, { name: p.name });
    dh.setHandles([radar, profile, chatter]);
  }
}

/* ---- Understat profile (player_profile panel + fetch-on-demand) ----
   The panel only ever READS the warehouse; an absent profile shows its
   reason plus a fetch button that POSTs the one sanctioned fetch route and
   polls the panel until rows appear or the route reports an error. */
function profileSection(host, code) {
  const box = el("div");
  box.appendChild(el("h2", null, "Profile — Understat"));
  const pbody = el("div");
  box.append(pbody);
  host.appendChild(box);
  let cancelled = false;
  let timer = null;
  const cancel = () => { cancelled = true; if (timer) clearTimeout(timer); };

  function renderProfile(r) {
    pbody.textContent = "";
    // per-match xG/shots sparkbar strip
    const strip = el("div");
    strip.style.cssText =
      "display:flex;align-items:flex-end;gap:3px;height:64px;" +
      "margin:6px 0 2px;overflow-x:auto;";
    const maxXg = Math.max(0.2, ...r.matches.map(m => m.xg));
    for (const m of r.matches) {
      const col = el("div");
      col.style.cssText = "display:flex;flex-direction:column;" +
        "justify-content:flex-end;align-items:center;gap:2px;" +
        "min-width:14px;height:100%;";
      if (m.goals > 0) {
        const dot = el("div");
        dot.style.cssText = "width:6px;height:6px;border-radius:50%;" +
          "background:var(--good);";
        dot.title = `${m.goals} goal(s)`;
        col.appendChild(dot);
      }
      const bar = el("div");
      const h = Math.max(2, Math.round(44 * m.xg / maxXg));
      bar.style.cssText = `width:10px;height:${h}px;background:var(--s1);` +
        "border-radius:2px 2px 0 0;" + (m.started ? "" : "opacity:.45;");
      col.appendChild(bar);
      const shots = el("div", null, String(m.shots));
      shots.style.cssText = "font-size:9px;color:var(--muted);line-height:1;";
      col.appendChild(shots);
      col.title =
        `${m.date}` +
        (m.opponent ? ` · ${m.opponent} (${m.venue})` : "") + "\n" +
        `${m.minutes} min${m.started ? "" : " (sub)"} · ${m.shots} shots · ` +
        `xG ${fmt2(m.xg)} · ${m.goals} goals · xA ${fmt2(m.xa)} · ` +
        `${m.key_passes} KP`;
      strip.appendChild(col);
    }
    pbody.appendChild(strip);
    pbody.appendChild(el("p", "sub",
      "bar = xG per match · number = shots · dot = scored · faded = sub"));

    const fin = r.finishing;
    const luck = el("p", null,
      `Finishing luck: ${fin.goals_minus_xg >= 0 ? "+" : ""}` +
      `${fmt2(fin.goals_minus_xg)} goals vs xG` +
      ` (${fin.npg_minus_npxg >= 0 ? "+" : ""}${fmt2(fin.npg_minus_npxg)}` +
      ` non-penalty)`);
    luck.style.color = fin.goals_minus_xg >= 0 ? "var(--good)" : "var(--bad)";
    pbody.appendChild(luck);
    pbody.appendChild(el("p", "sub", fin.label));
    const mp = r.minutes_pattern;
    pbody.appendChild(el("p", "sub",
      `Minutes: ${mp.starts} start(s) · ${mp.sub_appearances} sub · ` +
      `${mp.full_90s} full 90(s) · avg ${mp.avg_minutes} · ` +
      `last: ${mp.last5_minutes.join(", ")}`));
    pbody.appendChild(el("p", "sub", `${r.note} · as of ${r.as_of}`));
  }

  function renderEmpty(reason) {
    pbody.textContent = "";
    // reader copy carries no raw internals: the panel's reason may name the
    // POST route — the button IS that route, so the mention is stripped here
    const readable = String(reason || "")
      .replace(/\s*\((POST|GET)\s+\/api\/[^)]*\)/g, "")
      .replace(/\s*(POST|GET)\s+\/api\/\S+/g, "")
      .replace(/fetch it via the chat player_profile tool or the drawer's\s+Fetch-profile\s+button/i,
               "fetch it below")
      .trim();
    pbody.appendChild(emptyBox(readable || "no Understat profile cached"));
    const btn = el("button", "chip", "Fetch it now");
    btn.title = "one on-demand fetch from understat.com, cached after that";
    btn.onclick = async () => {
      btn.disabled = true;             // debounce: one click, one fetch
      btn.textContent = "fetching…";
      try {
        await postJSON(`/api/players/${code}/fetch_profile`, {});
        poll(0);
      } catch (e) {
        pbody.appendChild(errBox(e));
        btn.disabled = false; btn.textContent = "Fetch it now";
      }
    };
    pbody.appendChild(btn);
  }

  function poll(tries) {
    if (cancelled) return;
    timer = setTimeout(async () => {
      if (cancelled) return;
      try {
        const st = await getJSON(`/api/players/${code}/fetch_profile`);
        if (st.state === "error") {
          pbody.textContent = "";
          pbody.appendChild(errBox(new Error(st.detail)));
          return;
        }
        const { result } = await runPanel("player_profile", { code });
        if (!result.empty) { renderProfile(result); return; }
        if (st.state === "done") { renderEmpty(result.reason); return; }
      } catch (e) { pbody.textContent = ""; pbody.appendChild(errBox(e)); return; }
      if (tries < 20) poll(tries + 1);
      else pbody.appendChild(el("p", "sub",
        "still fetching — reopen the drawer to check again"));
    }, 2000);
  }

  (async () => {
    pbody.appendChild(el("p", "sub", "loading…"));
    try {
      const { result } = await runPanel("player_profile", { code });
      if (cancelled) return;
      if (result.empty) renderEmpty(result.reason);
      else renderProfile(result);
    } catch (e) {
      if (!cancelled) { pbody.textContent = ""; pbody.appendChild(errBox(e)); }
    }
  })();
  return { cancel };
}
