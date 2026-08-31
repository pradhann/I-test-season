/* Document cards (CHAT_ARCHITECTURE §5, phase 6).

   A ```doc fenced block is a report, not code: it renders as a distinct
   document surface with a masthead (the H1), a sticky mini-outline when the
   doc carries 3+ headings (the Fixtures drawer act-nav pattern), and two
   actions — Export (a self-contained standalone HTML file, charts inlined,
   theme tokens resolved at export time) and Copy markdown.

   buildExportHtml is kept pure-ish (asset fetch and token read injectable)
   so the exporter is testable without a browser. */

import React, { useMemo, useRef, useState } from "react";
import { Markdown } from "./markdown.jsx";
import { CHART_SPLIT, chartIds, RichText } from "./charts.jsx";
import { assetUrl } from "./api.js";

/* ---------------- parsing ---------------- */

const HEADING_RE = /^(#{1,6})\s+(.*)$/;

function slugify(text, taken) {
  let base = String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "section";
  let slug = base;
  let n = 2;
  while (taken.has(slug)) slug = `${base}-${n++}`;
  taken.add(slug);
  return slug;
}

/* -> { title, sections: [{heading: {level,text,id}|null, body}], headingCount }
   The first H1 is the masthead; later headings open sections. Fenced code is
   opaque — a "# comment" inside SQL is not a heading. */
export function parseDoc(source) {
  const lines = String(source ?? "").split("\n");
  const taken = new Set();
  let title = null;
  let headingCount = 0;
  const sections = [];
  let cur = { heading: null, lines: [] };
  let inFence = false;

  const push = () => {
    if (cur.heading || cur.lines.join("").trim()) {
      sections.push({ heading: cur.heading, body: cur.lines.join("\n") });
    }
  };

  for (const line of lines) {
    if (/^```/.test(line)) inFence = !inFence;
    const m = !inFence && !/^```/.test(line) ? HEADING_RE.exec(line) : null;
    if (m) {
      headingCount++;
      const level = m[1].length;
      if (title === null && level === 1) { title = m[2].trim(); continue; }
      push();
      cur = {
        heading: { level, text: m[2].trim(), id: slugify(m[2], taken) },
        lines: [],
      };
      continue;
    }
    cur.lines.push(line);
  }
  push();
  return { title: title || "Document", sections, headingCount };
}

/* ---------------- export ---------------- */

const TOKEN_FALLBACKS = {
  "--bg": "#101214", "--surface": "#16181b", "--raised": "#1d2024",
  "--ink": "#e8eaed", "--muted": "#9aa2ab", "--faint": "#6a727c",
  "--line": "#2a2e33", "--accent": "#3d8b5f", "--s1": "#2a78d6",
  "--mono": 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
};

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

async function defaultFetchAsset(id) {
  try {
    const r = await fetch(assetUrl(id, "svg"));
    if (r.ok) {
      const text = await r.text();
      if (text.includes("<svg")) return { kind: "svg", text };
    }
  } catch { /* fall through to png */ }
  try {
    const r = await fetch(assetUrl(id, "png"));
    if (r.ok) {
      const blob = await r.blob();
      const dataUri = await new Promise((res, rej) => {
        const fr = new FileReader();
        fr.onload = () => res(fr.result);
        fr.onerror = rej;
        fr.readAsDataURL(blob);
      });
      return { kind: "png", dataUri };
    }
  } catch { /* missing chart is reported honestly below */ }
  return null;
}

function defaultReadToken(name) {
  try {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
  } catch { return ""; }
}

/* Charts render as placeholder divs, then string-replaced with the fetched
   SVG — renderToStaticMarkup never sees raw markup, model text stays
   React-escaped throughout. */
function ExportRich({ text }) {
  const parts = String(text ?? "").split(CHART_SPLIT);
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 0
          ? (p.trim() ? <Markdown key={i} text={p} /> : null)
          : <div key={i} data-chart={p} />)}
    </>
  );
}

function ExportBody({ doc, dateLabel }) {
  return (
    <main className="doc">
      <header className="masthead">
        <h1>{doc.title}</h1>
        <p className="meta">i-test · exported {dateLabel}</p>
      </header>
      {doc.sections.map((s, i) => {
        const H = s.heading ? `h${Math.max(2, Math.min(s.heading.level, 4))}` : null;
        return (
          <section key={i}>
            {H && <H id={s.heading.id}>{s.heading.text}</H>}
            <ExportRich text={s.body} />
          </section>
        );
      })}
    </main>
  );
}

function exportCss(t) {
  return `
* { box-sizing: border-box; }
body { margin: 0; background: ${t["--bg"]}; color: ${t["--ink"]};
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
  Helvetica, Arial, sans-serif; }
.doc { max-width: 76ch; margin: 0 auto; padding: 48px 28px 80px;
  background: ${t["--surface"]}; min-height: 100vh;
  border-left: 1px solid ${t["--line"]}; border-right: 1px solid ${t["--line"]}; }
.masthead { border-bottom: 1px solid ${t["--line"]}; margin-bottom: 26px;
  padding-bottom: 14px; }
.masthead h1 { font-size: 27px; line-height: 1.25; margin: 0 0 8px;
  letter-spacing: -.01em; }
.masthead .meta { color: ${t["--faint"]}; font-size: 12px; margin: 0;
  font-family: ${t["--mono"]}; }
h2 { font-size: 19px; margin: 30px 0 10px; }
h3 { font-size: 16px; margin: 22px 0 8px; }
h4 { font-size: 14px; margin: 18px 0 8px; color: ${t["--muted"]};
  text-transform: uppercase; letter-spacing: .05em; }
p { margin: 0 0 12px; }
a { color: ${t["--s1"]}; }
code { font-family: ${t["--mono"]}; font-size: .92em;
  background: ${t["--raised"]}; border: 1px solid ${t["--line"]};
  border-radius: 4px; padding: 1px 4px; }
pre { background: ${t["--raised"]}; border: 1px solid ${t["--line"]};
  border-radius: 8px; padding: 12px 14px; overflow-x: auto;
  font-size: 13px; line-height: 1.5; }
pre code { background: none; border: 0; padding: 0; }
ul, ol { margin: 0 0 12px; padding-left: 24px; }
li { margin: 3px 0; }
blockquote { margin: 0 0 12px; padding: 2px 14px;
  border-left: 3px solid ${t["--line"]}; color: ${t["--muted"]}; }
hr { border: 0; border-top: 1px solid ${t["--line"]}; margin: 20px 0; }
.scroll-x { overflow-x: auto; margin: 0 0 12px; }
table { border-collapse: collapse; font-size: 13.5px; }
th, td { padding: 6px 12px; text-align: left; white-space: nowrap;
  border-bottom: 1px solid ${t["--line"]}; }
th { color: ${t["--muted"]}; font-weight: 600; font-size: 11.5px;
  letter-spacing: .05em; text-transform: uppercase; }
figure.chart { margin: 6px 0 16px; }
figure.chart svg, figure.chart img { max-width: 100%; height: auto;
  border-radius: 8px; }
.chart-missing { color: ${t["--faint"]}; font-family: ${t["--mono"]};
  font-size: 12px; }
@media print { .doc { border: 0; } }
`;
}

export async function buildExportHtml(source, opts = {}) {
  const doc = parseDoc(source);
  const readToken = opts.readToken || defaultReadToken;
  const fetchAsset = opts.fetchAsset || defaultFetchAsset;
  const now = opts.now || new Date();

  const tokens = {};
  for (const [name, fallback] of Object.entries(TOKEN_FALLBACKS)) {
    tokens[name] = readToken(name) || fallback;
  }

  // react-dom/server is only needed at export time; dynamic import keeps it
  // out of the main bundle (Vite splits it into its own chunk).
  const { renderToStaticMarkup } = await import("react-dom/server.browser");
  const dateLabel = now.toISOString().slice(0, 10);
  let bodyHtml = renderToStaticMarkup(<ExportBody doc={doc} dateLabel={dateLabel} />);

  for (const id of chartIds(source)) {
    const asset = await fetchAsset(id);
    let figure;
    if (asset && asset.kind === "svg") {
      const svg = asset.text
        .replace(/<\?xml[^>]*\?>/g, "")
        .replace(/<!DOCTYPE[^>]*>/g, "");
      figure = `<figure class="chart">${svg}</figure>`;
    } else if (asset && asset.kind === "png") {
      figure = `<figure class="chart"><img src="${asset.dataUri}" alt="chart"/></figure>`;
    } else {
      figure = `<figure class="chart"><p class="chart-missing">chart ${escapeHtml(id)} unavailable</p></figure>`;
    }
    bodyHtml = bodyHtml.split(`<div data-chart="${id}"></div>`).join(figure);
  }

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(doc.title)}</title>
<style>${exportCss(tokens)}</style>
</head>
<body>
${bodyHtml}
</body>
</html>
`;
}

export function exportFilename(title, now = new Date()) {
  const kebab = String(title).toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "").slice(0, 60) || "document";
  return `${kebab}-${now.toISOString().slice(0, 10)}.html`;
}

function downloadHtml(filename, html) {
  const blob = new Blob([html], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch { /* fall back to the selected-textarea path */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch { return false; }
}

/* ---------------- the card ---------------- */

export function DocumentCard({ source, onOpenChart }) {
  const doc = useMemo(() => parseDoc(source), [source]);
  const sectionRefs = useRef({});
  const [copied, setCopied] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [actionErr, setActionErr] = useState(null);
  const showOutline = doc.headingCount >= 3 &&
    doc.sections.some((s) => s.heading);

  const onCopy = async () => {
    setActionErr(null);
    const ok = await copyText(source.trim() + "\n");
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } else {
      setActionErr("copy failed in this browser");
    }
  };

  const onExport = async () => {
    setActionErr(null);
    setExporting(true);
    try {
      const html = await buildExportHtml(source);
      downloadHtml(exportFilename(doc.title), html);
    } catch (e) {
      setActionErr(`export failed: ${e.message || e}`);
    } finally {
      setExporting(false);
    }
  };

  const scrollToSection = (id) => {
    sectionRefs.current[id]?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  };

  return (
    <section className="doc-card">
      <header className="doc-head">
        <div className="doc-head-text">
          <div className="doc-eyebrow">Document</div>
          <h1 className="doc-title">{doc.title}</h1>
        </div>
        <div className="doc-actions">
          <button type="button" onClick={onCopy}>
            {copied ? "Copied" : "Copy markdown"}
          </button>
          <button type="button" onClick={onExport} disabled={exporting}>
            {exporting ? "Exporting…" : "Export"}
          </button>
        </div>
      </header>
      {actionErr && <div className="doc-action-err">{actionErr}</div>}
      <div className={"doc-layout" + (showOutline ? " with-outline" : "")}>
        {showOutline && (
          <nav className="doc-outline" aria-label="document outline">
            {doc.sections.filter((s) => s.heading).map((s) => (
              <button
                type="button"
                key={s.heading.id}
                className={`out-l${s.heading.level}`}
                onClick={() => scrollToSection(s.heading.id)}
              >
                {s.heading.text}
              </button>
            ))}
          </nav>
        )}
        <div className="doc-body">
          {doc.sections.map((s, i) => {
            const H = s.heading
              ? `h${Math.max(2, Math.min(s.heading.level + 1, 5))}`
              : null;
            return (
              <section
                key={i}
                ref={(n) => { if (s.heading) sectionRefs.current[s.heading.id] = n; }}
              >
                {H && <H className={`doc-h doc-h${s.heading.level}`}>{s.heading.text}</H>}
                <RichText text={s.body} onOpenChart={onOpenChart} />
              </section>
            );
          })}
        </div>
      </div>
    </section>
  );
}
