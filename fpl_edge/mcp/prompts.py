"""One prompt, derived from what actually registered.

The old server shipped three. Two of them described the YouTube tools, which
are gone: a video this corpus has not ingested cannot be summarised here, and
a prompt that says otherwise teaches a capability that does not exist. The
third listed six semantic-layer tools by name, and four of those names no
longer exist either.

So this one is built from ``server.tool_names()`` rather than written out.
A tool that stops registering stops being advertised, which is the failure
mode the old prompt had: it named ``get_player_history``, which no module has
ever registered, and the model could read that as a capability all season.
"""

from __future__ import annotations

from fpl_edge.mcp.server import mcp

#: The rules that are true of every tool here, so they are stated once.
_CONTRACT = """
Every tool returns the same envelope: ok, tool, panel, result, provenance,
budget, gap.

- result is a panel's payload verbatim. It is the same payload the web UI
  renders for the same run, and provenance carries the repo sha that produced
  it. Quote what it says a number is; do not rename one number to another.
- gap is not null when there is no data. Report that as not available, quote
  the reason, and do not substitute another source, another season or an
  estimate. A per-section gap inside a populated result works the same way.
- ok false with an error is a failure to load, which is a different thing from
  an absence of data. Say the read failed.
- ok false with refused is a request this server will not serve. The reason
  says what to do instead; follow it rather than guessing past it.
- budget.performance is over_budget when a read took longer than ten seconds.
  The payload is still the real payload.

as_of, where a tool takes one, is an ISO-8601 instant that must carry a
timezone. Everything is then read as it was knowable at that instant.

The FPL entry is the one this server was started for. No tool takes an entry
id, so there is no way to ask for another manager's own squad.
""".strip()

#: One line per tool, for the model's index. Only the tools that need a note
#: beyond their own description appear; the rest are listed by name.
_NOTES = {
    "query": "the escape hatch for a question no other tool shapes, over the "
             "sem_ macros under the same read-only guard the web uses.",
    "player": "the deepest read: sixteen sections about one player, each with "
              "a body or the reason it is empty.",
    "brief": "start here for what changed, then follow it into the tool that "
             "owns the number.",
    "solve_start": "starts a solve that takes one to five minutes. Poll "
                   "solve_status, then read transfer_plan.",
}


def _index() -> str:
    # Imported inside the function, not at module scope: server.py imports
    # this module last, while it is still initialising, so the name does not
    # exist yet at import time. It does by the time a prompt is read.
    from fpl_edge.mcp.server import tool_names

    names = tool_names()
    lines = [f"This server registers {len(names)} tools."]
    for name in names:
        note = _NOTES.get(name)
        lines.append(f"- {name}" + (f": {note}" if note else ""))
    return "\n".join(lines)


@mcp.prompt()
def fpl_tool_guidance() -> str:
    """How to read what this server returns, and which tool answers what.

    Read this before the first tool call in a session. It states the envelope
    every tool shares, what a gap means and how it differs from an error, and
    lists every registered tool. The list is generated from what registered,
    so it cannot advertise a tool this server does not have.
    """
    return f"{_CONTRACT}\n\n{_index()}"
