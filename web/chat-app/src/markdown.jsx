/* A small markdown renderer producing React elements — never raw HTML.
   Model output is data; everything lands in the DOM via React's text
   escaping. Coverage: headings, paragraphs, bold/italic/strikethrough,
   inline code, fenced code blocks, pipe tables, ordered/unordered lists
   (nested), blockquotes, horizontal rules, links.

   [chart:<id>] markers on their own line are handled by the caller (Prose in
   thread.jsx) — this module renders text between them. */

import React from "react";

/* ---------------- inline ---------------- */

const INLINE_PATTERNS = [
  { type: "code", re: /`([^`\n]+)`/ },
  { type: "bold", re: /\*\*([^*]+(?:\*(?!\*)[^*]*)*)\*\*/ },
  { type: "strike", re: /~~([^~\n]+)~~/ },
  { type: "italic", re: /(?<![\w*])\*([^*\n]+)\*(?![\w*])/ },
  { type: "italic", re: /(?<![\w_])_([^_\n]+)_(?![\w_])/ },
  { type: "link", re: /\[([^\]\n]+)\]\(([^)\s]+)\)/ },
];

function safeHref(url) {
  if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) return url;
  if (url.startsWith("/") || url.startsWith("#")) return url;
  return null;
}

export function renderInline(text, keyBase = "i") {
  const out = [];
  let rest = String(text ?? "");
  let k = 0;
  while (rest.length) {
    let best = null;
    for (const p of INLINE_PATTERNS) {
      const m = p.re.exec(rest);
      if (m && (best === null || m.index < best.m.index)) best = { p, m };
    }
    if (!best) { out.push(rest); break; }
    const { p, m } = best;
    if (m.index > 0) out.push(rest.slice(0, m.index));
    const key = `${keyBase}-${k++}`;
    if (p.type === "code") {
      out.push(<code key={key}>{m[1]}</code>);
    } else if (p.type === "bold") {
      out.push(<strong key={key}>{renderInline(m[1], key)}</strong>);
    } else if (p.type === "italic") {
      out.push(<em key={key}>{renderInline(m[1], key)}</em>);
    } else if (p.type === "strike") {
      out.push(<s key={key}>{renderInline(m[1], key)}</s>);
    } else if (p.type === "link") {
      const href = safeHref(m[2]);
      if (href) {
        const external = /^https?:/i.test(href);
        out.push(
          <a key={key} href={href}
             target={external ? "_blank" : undefined}
             rel={external ? "noopener noreferrer" : undefined}>
            {renderInline(m[1], key)}
          </a>
        );
      } else {
        out.push(m[0]); // unsafe scheme: show the literal text
      }
    }
    rest = rest.slice(m.index + m[0].length);
  }
  return out;
}

/* ---------------- blocks ---------------- */

const isTableLine = (l) => {
  const t = l.trim();
  return t.startsWith("|") && t.endsWith("|") && t.length > 2;
};
const isTableSep = (l) => /^\s*\|[\s:|-]+\|\s*$/.test(l) && l.includes("-");
const tableCells = (l) =>
  l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

const LIST_RE = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;

function parseList(lines, start) {
  // Collect contiguous list lines (continuation lines indent under the item).
  const items = []; // {indent, ordered, parts:[text]}
  let i = start;
  while (i < lines.length) {
    const m = LIST_RE.exec(lines[i]);
    if (m) {
      items.push({
        indent: m[1].length,
        ordered: /\d/.test(m[2]),
        parts: [m[3]],
      });
      i++;
    } else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) {
      items[items.length - 1].parts.push(lines[i].trim());
      i++;
    } else {
      break;
    }
  }

  // indent levels -> nested lists; a marker change (- vs 1.) at the same
  // level closes the list and opens a sibling of the other kind
  function build(from, minIndent, keyBase) {
    const lists = [];
    let j = from;
    while (j < items.length && items[j].indent >= minIndent) {
      const ordered = items[j].ordered;
      const children = [];
      while (
        j < items.length && items[j].indent >= minIndent &&
        items[j].ordered === ordered
      ) {
        const it = items[j];
        const sub = [];
        let k = j + 1;
        while (k < items.length && items[k].indent > minIndent) k++;
        if (k > j + 1) {
          const deeper = Math.min(
            ...items.slice(j + 1, k).map((x) => x.indent)
          );
          sub.push(build(j + 1, deeper, `${keyBase}-${j}`));
        }
        children.push(
          <li key={`${keyBase}-li-${j}`}>
            {renderInline(it.parts.join(" "), `${keyBase}-${j}`)}
            {sub}
          </li>
        );
        j = k;
      }
      const Tag = ordered ? "ol" : "ul";
      lists.push(<Tag key={`${keyBase}-l${lists.length}`}>{children}</Tag>);
    }
    return lists.length === 1
      ? lists[0]
      : <React.Fragment key={`${keyBase}-f`}>{lists}</React.Fragment>;
  }

  const minIndent = Math.min(...items.map((x) => x.indent));
  return { node: build(0, minIndent, `list-${start}`), next: i };
}

function parseTable(lines, start, keyBase) {
  let i = start;
  const block = [];
  while (i < lines.length && isTableLine(lines[i])) block.push(lines[i++]);
  let head = null;
  let rows = block;
  if (block.length >= 2 && isTableSep(block[1])) {
    head = tableCells(block[0]);
    rows = block.slice(2);
  }
  const node = (
    <div className="scroll-x" key={keyBase}>
      <table className="md-table">
        {head && (
          <thead>
            <tr>{head.map((h, c) => <th key={c}>{renderInline(h, `${keyBase}-h${c}`)}</th>)}</tr>
          </thead>
        )}
        <tbody>
          {rows.filter((l) => !isTableSep(l)).map((l, r) => (
            <tr key={r}>
              {tableCells(l).map((c, ci) => (
                <td key={ci}>{renderInline(c, `${keyBase}-${r}-${ci}`)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
  return { node, next: i };
}

export function Markdown({ text }) {
  const lines = String(text ?? "").split("\n");
  const out = [];
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${k++}`;

    if (!line.trim()) { i++; continue; }

    // fenced code (unterminated fence = still streaming: render what we have)
    const fence = /^```(\w*)\s*$/.exec(line);
    if (fence) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) buf.push(lines[i++]);
      if (i < lines.length) i++; // closing fence
      out.push(
        <pre className="md-code" key={key}>
          <code data-lang={fence[1] || undefined}>{buf.join("\n")}</code>
        </pre>
      );
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const Tag = `h${Math.min(heading[1].length + 2, 6)}`; // h1 -> h3 visual scale
      out.push(<Tag className="md-h" key={key}>{renderInline(heading[2], key)}</Tag>);
      i++;
      continue;
    }

    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) {
      out.push(<hr className="md-hr" key={key} />);
      i++;
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      out.push(
        <blockquote className="md-quote" key={key}>
          <Markdown text={buf.join("\n")} />
        </blockquote>
      );
      continue;
    }

    if (isTableLine(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const { node, next } = parseTable(lines, i, key);
      out.push(node);
      i = next;
      continue;
    }

    if (LIST_RE.test(line)) {
      const { node, next } = parseList(lines, i);
      out.push(<React.Fragment key={key}>{node}</React.Fragment>);
      i = next;
      continue;
    }

    // paragraph: consume until a blank line or another block opens
    const buf = [line];
    i++;
    while (
      i < lines.length && lines[i].trim() &&
      !/^```/.test(lines[i]) && !/^(#{1,6})\s/.test(lines[i]) &&
      !LIST_RE.test(lines[i]) && !/^\s*>\s?/.test(lines[i]) &&
      !(isTableLine(lines[i]) && i + 1 < lines.length && isTableSep(lines[i + 1]))
    ) {
      buf.push(lines[i]);
      i++;
    }
    out.push(<p className="md-p" key={key}>{renderInline(buf.join(" "), key)}</p>);
  }
  return <>{out}</>;
}
