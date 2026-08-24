"""Fail-closed robots.txt checking, shared by every provider in this package.

The understat evaluation established the policy for this repo (module since
deleted; parsers live in git history): read the
live ``robots.txt``, and treat an unreachable one as a refusal. This module
generalises it and adds the two distinctions the projection providers actually
forced us to make.

**404 is not the same as unreachable.** RFC 9309 §2.3.1.3 is explicit: a 4xx on
``/robots.txt`` means the crawler "MAY access any resources". A connection
timeout or a 403 means we do not know the policy, and not knowing is a no. Both
cases occur here -- fantasyfootballfix.com serves 404, api.sofascore.com serves
403 on ``/robots.txt`` itself -- and collapsing them would either lock us out of
a site that permits us or let us into one whose rules we cannot read.

**Python's own ``urllib.robotparser`` is not safe here.** It matches rule paths
with ``str.startswith`` and gives ``*`` and ``$`` no special meaning, so it
answers ``True`` for ``/api/leagues`` against FotMob's
``Disallow: /api/*`` -- a path the operator has explicitly closed to everyone
but a handful of named search engines. It also applies first-match-wins rather
than RFC 9309 §2.2.2's longest-match-wins. Both defects fail *open*, which is
the wrong direction for a module whose job is to stop us fetching things. The
matcher below is written out rather than delegated for exactly that reason.

**A group naming an AI crawler is a signal even when it does not name us.**
fplreview.com's file allows ``User-agent: *`` but carries an explicit
``Disallow: /`` for ClaudeBot, GPTBot, CCBot, Google-Extended and friends, plus
``Content-Signal: ai-train=no``. We do not identify as any of those agents, so
the letter of the file permits the fetch. :func:`ai_crawlers_disallowed` surfaces
that the operator has nonetheless drawn a line, so the decision to fetch is a
decision someone made rather than a technicality nobody noticed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from fpl_edge.ingest.http import USER_AGENT

#: Agents whose presence in a robots.txt tells us the operator has thought about
#: automated AI consumption specifically.
AI_AGENT_NAMES = (
    "ClaudeBot", "GPTBot", "CCBot", "Google-Extended", "Applebot-Extended",
    "Bytespider", "Amazonbot", "meta-externalagent", "anthropic-ai",
    "PerplexityBot", "Claude-Web",
)


class RobotsDisallowed(RuntimeError):
    """robots.txt forbids the fetch, or could not be read at all."""


@dataclass(frozen=True, slots=True)
class RobotsPolicy:
    """What a site's robots.txt actually said, and whether we could read it."""

    origin: str
    http_status: int | None
    reachable: bool
    body: str
    error: str | None = None

    @property
    def missing(self) -> bool:
        """4xx other than 401/403: no policy published, so no restriction."""
        return self.http_status is not None and 400 <= self.http_status < 500 \
            and self.http_status not in (401, 403)

    @property
    def readable(self) -> bool:
        """True when we know the policy -- either it was served, or it is absent."""
        return (self.http_status == 200) or self.missing

    def allows(self, path: str, *, user_agent: str = USER_AGENT) -> bool:
        """RFC 9309 evaluation of ``path`` for ``user_agent``.

        Group selection is most-specific-token-wins, then ``*``; within the
        chosen group the longest matching rule wins, and Allow beats Disallow on
        an exact-length tie.
        """
        if not self.readable:
            return False
        if self.missing:
            return True
        rules = self._group_for(user_agent)
        if rules is None:
            return True
        best_len, best_allow = -1, True
        for allow, pattern in rules:
            if not _rule_matches(pattern, path):
                continue
            # A bare "Disallow:" with an empty value means "nothing is
            # disallowed" and must never out-rank a real rule.
            if pattern == "":
                continue
            length = len(pattern)
            if length > best_len or (length == best_len and allow):
                best_len, best_allow = length, allow
        return best_allow

    def _groups(self) -> list[tuple[list[str], list[tuple[bool, str]]]]:
        """``[(agents, [(is_allow, path_pattern), ...]), ...]`` in file order."""
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        started = False
        for raw in self.body.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field, value = field.strip().lower(), value.strip()
            if field == "user-agent":
                if started:
                    groups.append((agents, rules))
                    agents, rules, started = [], [], False
                agents.append(value)
            elif field in ("allow", "disallow"):
                started = True
                rules.append((field == "allow", value))
        if agents:
            groups.append((agents, rules))
        return groups

    def _group_for(self, user_agent: str) -> list[tuple[bool, str]] | None:
        """The rule list that governs ``user_agent``.

        Matched on the product token (``fpl-edge`` out of
        ``fpl-edge/0.1 (...)``) case-insensitively, per RFC 9309 §2.2.1.
        """
        token = re.split(r"[/\s]", user_agent.strip(), maxsplit=1)[0].lower()
        star: list[tuple[bool, str]] | None = None
        named: list[tuple[bool, str]] | None = None
        best = -1
        for agents, rules in self._groups():
            for agent in agents:
                a = agent.lower()
                if a == "*":
                    star = (star or []) + rules
                elif a and (token == a or token.startswith(a)) and len(a) > best:
                    best, named = len(a), rules
        return named if named is not None else star

    @property
    def ai_crawlers_disallowed(self) -> tuple[str, ...]:
        """AI agent names the file gives an explicit ``Disallow: /``.

        Parsed per group rather than by substring: a name in a comment, or one
        that appears with an Allow, must not read as a prohibition.
        """
        if not self.readable or self.missing:
            return ()
        known = {n.lower(): n for n in AI_AGENT_NAMES}
        blocked: set[str] = set()
        for agents, rules in self._groups():
            if not any(allow is False and pattern == "/" for allow, pattern in rules):
                continue
            for agent in agents:
                if agent.lower() in known:
                    blocked.add(known[agent.lower()])
        return tuple(sorted(blocked))

    @property
    def content_signal(self) -> str | None:
        """The ``Content-Signal`` line for ``*``, if the site publishes one."""
        agents: list[str] = []
        for raw in self.body.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field = field.strip().lower()
            if field == "user-agent":
                agents.append(value.strip())
            elif field == "content-signal" and any(a == "*" for a in agents):
                return value.strip()
        return None


def _rule_matches(pattern: str, path: str) -> bool:
    """RFC 9309 §2.2.3 path matching, with ``*`` and ``$``.

    ``*`` matches any run of characters (including none); a trailing ``$``
    anchors the end. Everything else is a literal prefix match.
    """
    if pattern == "":
        return False
    if not path.startswith("/"):
        path = "/" + path
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.match(regex + ("$" if anchored else ""), path) is not None


def fetch_policy(url: str, *, timeout: float = 15.0,
                 user_agent: str = USER_AGENT) -> RobotsPolicy:
    """Read ``<origin>/robots.txt``. Never raises; the failure is the answer."""
    parts = urlparse(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    try:
        resp = httpx.get(
            f"{origin}/robots.txt",
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001 -- any transport failure is "unknown"
        return RobotsPolicy(origin, None, False, "", f"{type(exc).__name__}: {exc}")
    return RobotsPolicy(origin, resp.status_code, True,
                        resp.text if resp.status_code == 200 else "")


def require_allowed(url: str, *, user_agent: str = USER_AGENT) -> RobotsPolicy:
    """Raise :class:`RobotsDisallowed` unless the live policy permits ``url``.

    Called before every network fetch in this package. There is no bypass flag
    that forges a User-Agent or a TLS fingerprint.
    """
    policy = fetch_policy(url, user_agent=user_agent)
    if not policy.readable:
        raise RobotsDisallowed(
            f"{policy.origin}/robots.txt is unreadable "
            f"(status={policy.http_status}, error={policy.error}). An unreadable "
            f"crawl policy means no, never 'assume yes'."
        )
    if not policy.allows(urlparse(url).path or "/", user_agent=user_agent):
        raise RobotsDisallowed(
            f"{policy.origin}/robots.txt disallows {urlparse(url).path!r} "
            f"for {user_agent!r}."
        )
    return policy
