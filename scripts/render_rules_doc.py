"""Render docs/rules.md from the rule registry so the two cannot drift."""

from __future__ import annotations

import json
from pathlib import Path

from fpl_edge.rules import rules

OUT = Path("docs/rules.md")


def fmt(value: object) -> str:
    if isinstance(value, (dict, list)):
        return f"`{json.dumps(value)}`"
    return f"`{value}`"


def main() -> None:
    r = rules()
    lines: list[str] = [
        "# Verified FPL rules",
        "",
        "**Generated file — do not edit by hand.**",
        "Source of truth is `fpl_edge/rules/registry.yaml`;",
        "regenerate with `make rules-doc`.",
        "",
        f"Season: **{r.season}**",
        "",
        "## Sources",
        "",
        "| Key | URL | Fetched (UTC) |",
        "| --- | --- | --- |",
    ]
    for key, meta in r.sources.items():
        lines.append(f"| `{key}` | {meta['url']} | {meta['fetched_at']} |")
    lines.append("")
    for key, meta in r.sources.items():
        if meta.get("note"):
            lines += [f"**{key} note.** {' '.join(meta['note'].split())}", ""]

    unver = r.unverified()
    lines += ["## Verification status", ""]
    lines.append(
        f"{len(r.paths()) - len(unver)} of {len(r.paths())} rules verified against an "
        "authoritative source."
    )
    lines.append("")
    if unver:
        lines += [
            "The following rules are **UNVERIFIED**. Code reading them raises",
            "`UnverifiedRuleError` rather than guessing.",
            "",
            "| Rule | Why it matters |",
            "| --- | --- |",
        ]
        for rule in unver:
            lines.append(f"| `{rule.path}` | {' '.join((rule.note or '').split())} |")
        lines.append("")

    lines += ["## Rules", "", "| Rule | Value | Sources | Note |", "| --- | --- | --- | --- |"]
    for path in r.paths():
        rule = r.rule(path)
        mark = "" if rule.verified else " ⚠️ UNVERIFIED"
        note = " ".join((rule.note or "").split())
        src = ", ".join(rule.source) or "—"
        lines.append(f"| `{path}`{mark} | {fmt(rule.value)} | {src} | {note} |")
    lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT} ({len(lines)} lines, {len(unver)} unverified)")


if __name__ == "__main__":
    main()
