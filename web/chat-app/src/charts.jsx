/* Chart figures, the lightbox, and RichText (markdown + [chart:<id>]
   markers). Shared by ordinary prose (thread.jsx) and document cards
   (doc.jsx) — kept in its own module so neither imports the other. */

import React, { useEffect, useState } from "react";
import { Markdown } from "./markdown.jsx";
import { assetUrl } from "./api.js";

// Marker ids are uuid-hex; the regex pins the charset so the constructed
// asset URL cannot smuggle a path.
export const CHART_SPLIT = /\[chart:([0-9a-fA-F][0-9a-fA-F-]{7,63})\]/g;

export function chartIds(text) {
  return [...new Set([...String(text ?? "").matchAll(CHART_SPLIT)].map((m) => m[1]))];
}

export function ChartFigure({ id, onOpen }) {
  // SVG preferred (crisp in both themes); PNG fallback; then an honest note.
  const [ext, setExt] = useState("svg");
  const [dead, setDead] = useState(false);
  if (dead) return <div className="chart-missing">chart {id} unavailable</div>;
  const src = assetUrl(id, ext);
  return (
    <figure className="chart-fig">
      <img
        src={src}
        alt="chart"
        loading="lazy"
        onError={() => (ext === "svg" ? setExt("png") : setDead(true))}
        onClick={() => onOpen && onOpen(src)}
      />
    </figure>
  );
}

export function Lightbox({ src, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  if (!src) return null;
  return (
    <div className="lightbox" onClick={onClose} role="dialog" aria-label="chart, full size">
      <img src={src} alt="chart, full size" onClick={(e) => e.stopPropagation()} />
    </div>
  );
}

/* Markdown text where [chart:<id>] markers on their own line become inline
   figures. */
export function RichText({ text, onOpenChart }) {
  const parts = String(text ?? "").split(CHART_SPLIT);
  const nodes = [];
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      if (parts[i].trim()) nodes.push(<Markdown key={`t${i}`} text={parts[i]} />);
    } else {
      nodes.push(<ChartFigure key={`c${i}`} id={parts[i]} onOpen={onOpenChart} />);
    }
  }
  return <>{nodes}</>;
}
