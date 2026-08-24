/* Transfer planner — the manual multi-GW sandbox (fplreview's idiom).
   Rows = your 15 (+ candidate ins), columns = the next H gameweeks, cells =
   consensus xPts. Click a cell to transfer that player OUT from that GW and
   pick a same-position replacement; the grid recomputes XI totals, bank,
   free transfers and hit costs. No solver here — the Solver view is separate.

   All game rules (FTs per GW, banking cap, hit cost) come from the payload's
   `rules` block, which the panel script reads from the verified rule
   registry — this file never hardcodes a game rule. */

import { runPanel, el, emptyBox, errBox, provenance, faceImg, stat,
         fmtPrice, fmt1, fmt2 } from "/js/app.js";

const PLANS_KEY = "itest-planner-plans-v1";
const MAX_PLANS = 5;
const POS_ORDER = { GKP: 0, DEF: 1, MID: 2, FWD: 3 };

function loadPlans() {
  try { return JSON.parse(localStorage.getItem(PLANS_KEY)) || {}; }
  catch { return {}; }
}
function storePlans(plans) {
  localStorage.setItem(PLANS_KEY, JSON.stringify(plans));
}

export default async function planner(host) {
  const card = el("section", "card");
  card.appendChild(el("h2", null, "Transfer planner"));
  const sub = el("p", "sub");
  card.appendChild(sub);
  host.appendChild(card);

  let horizon = 5;
  let res, prov;

  async function fetchPayload() {
    ({ result: res, provenance: prov } = await runPanel("planner_grid", { horizon }));
  }
  try { await fetchPayload(); }
  catch (e) { card.appendChild(errBox(e)); return; }
  if (res?.empty) { card.appendChild(emptyBox(res.reason)); return; }

  // ---- static bits of the payload ----
  const R = res.rules;                 // {free_per_gw, max_banked, hit_cost}
  const tenths = p => Math.round((p.price ?? 0) * 10);
  let byCode, gridMax;
  function index() {
    byCode = new Map();
    for (const p of res.squad) byCode.set(p.code, p);
    for (const p of res.candidates) if (!byCode.has(p.code)) byCode.set(p.code, p);
    gridMax = 0.001;
    for (const per of Object.values(res.xpts))
      for (const v of Object.values(per)) if (v > gridMax) gridMax = v;
  }
  index();
  const xp = (code, gw) => res.xpts[String(code)]?.[String(gw)] ?? null;
  const sprd = (code, gw) => res.spread[String(code)]?.[String(gw)] ?? null;

  // ---- plan state: an ordered list of {gw, out, in} ----
  let moves = [];
  let picking = null;                  // {gw, out} while choosing a replacement
  let notice = "";

  function squadAt(gw) {               // codes in the squad entering GW `gw`'s moves applied
    const codes = new Set(res.squad.map(p => p.code));
    for (const m of moves) if (m.gw <= gw) { codes.delete(m.out); codes.add(m.in); }
    return codes;
  }
  function bankBefore(gw) {            // bank after all moves in GWs <= gw
    let bank = res.bank_tenths;
    for (const m of moves) if (m.gw <= gw) {
      const o = byCode.get(m.out), n = byCode.get(m.in);
      bank += (o ? tenths(o) : 0) - (n ? tenths(n) : 0);
    }
    return bank;
  }

  /* Best legal XI by xPts (1 GKP, 3-5 DEF, 2-5 MID, 1-3 FWD), captain
     auto-assigned to the highest xPts in the XI and doubled. */
  function xiTotal(codes, gw) {
    const by = { GKP: [], DEF: [], MID: [], FWD: [] };
    for (const c of codes) {
      const p = byCode.get(c);
      if (p && by[p.pos]) by[p.pos].push(xp(c, gw) ?? 0);
    }
    for (const k in by) by[k].sort((a, b) => b - a);
    const gk = by.GKP[0] ?? 0;
    let best = null;
    for (let d = 3; d <= Math.min(5, by.DEF.length); d++)
      for (let m = 2; m <= Math.min(5, by.MID.length); m++) {
        const f = 10 - d - m;
        if (f < 1 || f > 3 || f > by.FWD.length) continue;
        const outfield = [...by.DEF.slice(0, d), ...by.MID.slice(0, m),
                          ...by.FWD.slice(0, f)];
        const sum = gk + outfield.reduce((a, x) => a + x, 0);
        const cap = Math.max(gk, ...outfield);
        if (best === null || sum + cap > best) best = sum + cap;
      }
    if (best !== null) return best;
    // Degenerate squad (should not happen with same-position swaps): top 11.
    const all = Object.values(by).flat().sort((a, b) => b - a).slice(0, 11);
    return all.reduce((a, x) => a + x, 0) + (all[0] ?? 0);
  }

  /* One pass over the horizon: transfers, FTs used/banked, hits, bank, XI. */
  function compute() {
    let ft = res.ft_entering, bank = res.bank_tenths;
    const codes = new Set(res.squad.map(p => p.code));
    const perGw = []; let totalX = 0, totalHits = 0;
    for (const g of res.gws) {
      const mv = moves.filter(m => m.gw === g);
      for (const m of mv) {
        codes.delete(m.out); codes.add(m.in);
        const o = byCode.get(m.out), n = byCode.get(m.in);
        bank += (o ? tenths(o) : 0) - (n ? tenths(n) : 0);
      }
      const ftUsed = Math.min(mv.length, ft);
      const hits = mv.length - ftUsed;
      const x = xiTotal(codes, g);
      perGw.push({ gw: g, transfers: mv.length, ftAvail: ft, ftUsed, hits, bank, xi: x });
      totalX += x; totalHits += hits;
      ft = Math.min(R.max_banked, ft - ftUsed + R.free_per_gw);
    }
    return { perGw, totalX, totalHits,
             net: totalX - totalHits * R.hit_cost };
  }

  /* Drop moves the current payload cannot honour (unknown player, out not in
     squad at that GW, position mismatch) — replayed in order so a cascade of
     dependent moves stays consistent. Returns how many were dropped. */
  function sanitise() {
    const clean = [];
    const keepable = m => {
      const o = byCode.get(m.out), n = byCode.get(m.in);
      if (!o || !n || o.pos !== n.pos) return false;
      if (!res.gws.includes(m.gw)) return false;
      const codes = new Set(res.squad.map(p => p.code));
      for (const prev of clean) if (prev.gw <= m.gw) { codes.delete(prev.out); codes.add(prev.in); }
      return codes.has(m.out) && !codes.has(m.in);
    };
    const dropped = moves.filter(m => !keepable(m) || !clean.push(m)).length;
    moves = clean;
    return dropped;
  }

  // ---- containers ----
  const toolbar = el("div", "filters");
  const summaryBox = el("div");
  const pickerBox = el("div");
  const gridBox = el("div");
  const noteLine = el("p", "sub");
  card.append(toolbar, summaryBox, pickerBox, gridBox, noteLine);
  card.appendChild(provenance(prov));

  sub.textContent =
    `Consensus xPts per GW (cell tint = magnitude; hover for the cross-source spread). ` +
    `Click a cell to transfer that player out from that GW. Captain auto-assigned ` +
    `to the XI's top xPts. Squad: ${res.provenance_source}. ` +
    (res.notes || []).join(" ");

  // ---- toolbar: plan save/load/clear + horizon ----
  function renderToolbar() {
    toolbar.textContent = "";
    const plans = loadPlans();

    const sel = el("select");
    sel.appendChild(el("option", null, "— saved plans —"));
    for (const name of Object.keys(plans)) sel.appendChild(el("option", null, name));
    sel.onchange = () => {
      const name = sel.value;
      if (!plans[name]) return;
      moves = (plans[name].moves || []).map(m => ({ ...m }));
      nameInput.value = name;
      picking = null;
      const dropped = sanitise();
      notice = `Loaded “${name}”` + (dropped ? ` (${dropped} move(s) no longer valid, dropped)` : "");
      render();
    };

    const nameInput = el("input");
    nameInput.type = "text"; nameInput.placeholder = "plan name"; nameInput.size = 12;

    const saveBtn = el("button", null, "Save");
    saveBtn.onclick = () => {
      const name = nameInput.value.trim();
      if (!name) { notice = "Name the plan before saving."; return render(); }
      const all = loadPlans();
      if (!(name in all) && Object.keys(all).length >= MAX_PLANS) {
        notice = `Plan limit is ${MAX_PLANS} — delete one first.`; return render();
      }
      all[name] = { moves, horizon, savedAt: new Date().toISOString() };
      storePlans(all);
      notice = `Saved “${name}” (${moves.length} move(s)).`;
      render();
    };

    const delBtn = el("button", null, "Delete");
    delBtn.onclick = () => {
      const name = nameInput.value.trim() || sel.value;
      const all = loadPlans();
      if (all[name]) { delete all[name]; storePlans(all); notice = `Deleted “${name}”.`; }
      else notice = "No saved plan by that name.";
      render();
    };

    const clearBtn = el("button", null, "Clear moves");
    clearBtn.onclick = () => { moves = []; picking = null; notice = "Moves cleared."; render(); };

    const hzLabel = el("label", null, "horizon");
    const hz = el("select");
    for (const h of [3, 5, 8]) {
      const o = el("option", null, `${h} GWs`); o.value = h;
      if (h === horizon) o.selected = true;
      hz.appendChild(o);
    }
    hz.onchange = async () => {
      horizon = Number(hz.value);
      try {
        await fetchPayload();
        if (res?.empty) { card.textContent = ""; card.appendChild(emptyBox(res.reason)); return; }
        index();
        const dropped = sanitise();
        notice = dropped ? `${dropped} move(s) fell outside the new horizon and were dropped.` : "";
        render();
      } catch (e) { card.appendChild(errBox(e)); }
    };

    toolbar.append(sel, nameInput, saveBtn, delBtn, clearBtn, hzLabel, hz);
  }

  // ---- summary strip ----
  function renderSummary() {
    summaryBox.textContent = "";
    const c = compute();
    const wrap = el("div", "scroll-x");
    const table = el("table", "data");
    const thead = el("thead"); const hr = el("tr");
    hr.appendChild(el("th", null, "plan"));
    for (const g of res.gws) hr.appendChild(el("th", "num", `GW${g}`));
    thead.appendChild(hr); table.appendChild(thead);
    const tbody = el("tbody");
    const row = (label, cell) => {
      const tr = el("tr");
      tr.appendChild(el("td", null, label));
      for (const p of c.perGw) { const td = el("td", "num"); cell(td, p); tr.appendChild(td); }
      tbody.appendChild(tr);
    };
    row("XI xPts (C×2)", (td, p) => td.textContent = fmt1(p.xi));
    row("transfers", (td, p) => td.textContent = String(p.transfers));
    row("FTs used / available", (td, p) => td.textContent = `${p.ftUsed}/${p.ftAvail}`);
    row("hit pts", (td, p) => {
      td.textContent = p.hits ? `−${p.hits * R.hit_cost}` : "0";
      if (p.hits) td.style.color = "var(--bad)";
    });
    row("bank", (td, p) => {
      td.textContent = fmtPrice(p.bank / 10);
      if (p.bank < 0) td.style.color = "var(--bad)";
    });
    table.appendChild(tbody); wrap.appendChild(table); summaryBox.appendChild(wrap);

    const strip = el("div", "stats");
    strip.append(stat(fmt1(c.net), "net xPts", "good"));
    strip.append(stat(fmt1(c.totalX), "gross xPts"));
    strip.append(stat(c.totalHits ? `−${c.totalHits * R.hit_cost}` : "0",
                      "hit points", c.totalHits ? "bad" : ""));
    const endBank = c.perGw.at(-1)?.bank ?? res.bank_tenths;
    strip.append(stat(fmtPrice(endBank / 10), "bank at end",
                      c.perGw.some(p => p.bank < 0) ? "bad" : ""));
    strip.append(stat(String(moves.length), "moves planned"));
    summaryBox.appendChild(strip);
    if (c.perGw.some(p => p.bank < 0))
      summaryBox.appendChild(el("p", "sub")).appendChild(
        el("span", "chip bad", "bank goes negative — plan is not affordable"));
  }

  // ---- candidate picker ----
  function renderPicker() {
    pickerBox.textContent = "";
    if (!picking) return;
    const out = byCode.get(picking.out);
    const box = el("div", "card");
    box.appendChild(el("h2", null,
      `Replace ${out?.name ?? picking.out} (${out?.pos ?? "?"}) from GW${picking.gw}`));
    const funds = bankBefore(picking.gw) + (out ? tenths(out) : 0);
    box.appendChild(el("p", "sub",
      `Funds: ${fmtPrice(funds / 10)} (bank after earlier moves + sale at current price). ` +
      `Same position only.`));

    const search = el("input");
    search.type = "text"; search.placeholder = "search player / team"; search.size = 20;
    box.appendChild(el("div", "filters")).append(search,
      (() => { const b = el("button", null, "Cancel");
               b.onclick = () => { picking = null; render(); }; return b; })());

    const listBox = el("div", "scroll-x");
    box.appendChild(listBox);

    const inSquad = squadAt(picking.gw);
    const remaining = res.gws.filter(g => g >= picking.gw);
    const pool = res.candidates
      .filter(cd => cd.pos === out?.pos && !inSquad.has(cd.code))
      .map(cd => ({ ...cd,
        sum: remaining.reduce((a, g) => a + (xp(cd.code, g) ?? 0), 0) }))
      .sort((a, b) => b.sum - a.sum);

    function renderList() {
      listBox.textContent = "";
      const term = search.value.trim().toLowerCase();
      const rows = pool.filter(cd => !term ||
        cd.name.toLowerCase().includes(term) || (cd.team || "").toLowerCase().includes(term))
        .slice(0, 30);
      if (!rows.length) { listBox.appendChild(emptyBox("no matching candidate")); return; }
      const table = el("table", "data");
      const thead = el("thead"); const hr = el("tr");
      for (const [lbl, num] of [["player", 0], ["team", 0], ["price", 1],
                                ["owned %", 1], [`ΣxPts GW${picking.gw}–${remaining.at(-1)}`, 1], ["", 0]])
        hr.appendChild(el("th", num ? "num" : "", lbl));
      thead.appendChild(hr); table.appendChild(thead);
      const tbody = el("tbody");
      for (const cd of rows) {
        const short = tenths(cd) > funds;
        const tr = el("tr");
        const nameTd = el("td");
        nameTd.appendChild(faceImg(cd.code, "avatar"));
        nameTd.appendChild(document.createTextNode(cd.name));
        tr.appendChild(nameTd);
        tr.appendChild(el("td", null, cd.team ?? "–"));
        tr.appendChild(el("td", "num", fmtPrice(cd.price)));
        tr.appendChild(el("td", "num", cd.own_pct == null ? "–" : fmt1(cd.own_pct)));
        tr.appendChild(el("td", "num", fmt2(cd.sum)));
        const act = el("td");
        if (short) act.appendChild(el("span", "chip bad", "£ short"));
        else {
          const b = el("button", null, "in");
          b.onclick = () => {
            const gw = picking.gw;
            moves.push({ gw, out: picking.out, in: cd.code });
            moves.sort((a, b2) => a.gw - b2.gw);
            picking = null;
            notice = `GW${gw}: planned ${out?.name} → ${cd.name}.`;
            render();
          };
          act.appendChild(b);
        }
        tr.appendChild(act);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody); listBox.appendChild(table);
    }
    search.oninput = renderList;
    renderList();
    pickerBox.appendChild(box);
    search.focus();
  }

  // ---- the grid ----
  function xpCell(td, code, gw) {
    const v = xp(code, gw);
    if (v == null) { td.textContent = "–"; return; }
    const pct = Math.min(65, Math.round(65 * v / gridMax));
    td.textContent = fmt1(v);
    td.style.background = `color-mix(in oklab, var(--s1) ${pct}%, var(--surface))`;
    if (pct > 55) td.style.color = "#fff";
    const s = sprd(code, gw);
    td.title = `xPts ${fmt2(v)}` + (s != null ? ` · spread ${fmt2(s)} across sources` : "");
  }

  function renderGrid() {
    gridBox.textContent = "";
    const outAt = new Map(moves.map(m => [m.out, m]));   // code -> move (out)
    const inAt = new Map(moves.map(m => [m.in, m]));     // code -> move (in)

    const wrap = el("div", "scroll-x");
    const table = el("table", "data sticky-first");
    const thead = el("thead"); const hr = el("tr");
    for (const lbl of ["player", "pos", "team"]) hr.appendChild(el("th", null, lbl));
    hr.appendChild(el("th", "num", "price"));
    for (const g of res.gws) hr.appendChild(el("th", "num", `GW${g}`));
    thead.appendChild(hr); table.appendChild(thead);
    const tbody = el("tbody");

    const removeMove = (m) => {
      // Cascade: a later move selling this move's incoming player dies with it.
      const dead = new Set([m]);
      let grew = true;
      while (grew) {
        grew = false;
        for (const other of moves)
          if (!dead.has(other) && [...dead].some(d => other.out === d.in && other.gw >= d.gw)) {
            dead.add(other); grew = true;
          }
      }
      moves = moves.filter(x => !dead.has(x));
      notice = dead.size > 1 ? `Removed ${dead.size} linked move(s).` : "Move removed.";
      render();
    };

    const playerRow = (p, joinedGw) => {
      const tr = el("tr");
      const nameTd = el("td");
      nameTd.appendChild(faceImg(p.code, "avatar"));
      nameTd.appendChild(document.createTextNode(p.name + (p.is_captain ? " (C)" : "")));
      if (joinedGw != null) {
        nameTd.appendChild(document.createTextNode(" "));
        nameTd.appendChild(el("span", "chip good", `IN GW${joinedGw}`));
      }
      tr.appendChild(nameTd);
      tr.appendChild(el("td", null, p.pos));
      tr.appendChild(el("td", null, p.team ?? "–"));
      tr.appendChild(el("td", "num", fmtPrice(p.price)));
      const outMove = outAt.get(p.code);
      for (const g of res.gws) {
        const td = el("td", "num");
        const joined = joinedGw == null || g >= joinedGw;
        if (!joined) td.textContent = "–";
        else if (outMove && g > outMove.gw) { td.textContent = "–"; td.style.color = "var(--faint)"; }
        else if (outMove && g === outMove.gw) {
          const inn = byCode.get(outMove.in);
          const chip = el("span", "chip bad", `OUT → ${inn?.name ?? outMove.in}`);
          chip.title = "click to undo this transfer";
          chip.style.cursor = "pointer";
          chip.onclick = () => removeMove(outMove);
          td.appendChild(chip);
        } else {
          xpCell(td, p.code, g);
          td.classList.add("clickable");
          td.title = (td.title ? td.title + " · " : "") + `click to transfer out in GW${g}`;
          td.onclick = () => { picking = { gw: g, out: p.code }; render(); };
        }
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    };

    const squadSorted = [...res.squad].sort((a, b) =>
      (POS_ORDER[a.pos] ?? 9) - (POS_ORDER[b.pos] ?? 9) || b.price - a.price);
    for (const p of squadSorted) playerRow(p, null);
    for (const m of moves) {
      const p = byCode.get(m.in);
      if (p && inAt.get(m.in) === m) playerRow(p, m.gw);
    }

    table.appendChild(tbody); wrap.appendChild(table);
    gridBox.appendChild(wrap);
  }

  function render() {
    renderToolbar();
    renderSummary();
    renderPicker();
    renderGrid();
    noteLine.textContent = notice;
  }
  render();
}
