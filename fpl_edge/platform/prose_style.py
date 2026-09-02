"""The house prose rule for model-authored analysis, and its enforcement.

The owner's report, verbatim, on a chat answer that shipped:

    "Your team is not bad. Your process is idle. You have made zero transfers
    in two gameweeks, and in that time your squad has drifted about 22
    expected points behind what the same money could buy. Nothing here asks
    you to gamble, wildcard, or chase. Two of the fixes cost nothing - not a
    hit, not a penny - and you already have the transfers to make them."

Five sentences, one number, and that number ("about 22 expected points behind
what the same money could buy") names no source and cannot be checked. The
rest is rhetoric: a judgment about the manager rather than the squad, a
negation triad, an anaphoric aside, a motivational close. It reads as
insight and carries none.

Two levers, because instructions alone are not enforcement:

* :data:`STYLE_RULES` goes into every prompt that produces analysis prose
  (the chat analyst's charter, the briefing meta-prompt), so one edit moves
  both surfaces and they cannot drift apart.
* :func:`slop_findings` and :func:`normalize_prose` run over model output
  before it is stored. Formatting tics are rewritten losslessly; rhetorical
  constructions are a rejection, counted and reported like any other
  validator drop. The harness owns the voice, not the model's good manners.

Scope: text a MODEL writes for the owner to read. Not this repo's own
comments and docstrings, which are written by hand for a different reader.
"""

from __future__ import annotations

import re

#: The rule, as the prompt states it. Written as instructions to the model.
STYLE_RULES = """
## How to write (enforced, not advisory)

Analysis prose is checked mechanically before it is stored. Violations are
rejected and counted.

1. Lead with the number, then what it means. "Haaland 70.1% owned, you do
   not hold him" not "There is a significant ownership gap in your squad".
2. Every sentence carries a number, a player, or a named source. A sentence
   carrying none of the three is deleted, not rewritten.
3. Never characterise the manager, their process, their discipline, or their
   week. Describe the squad, the fixtures, the market. "Two transfers
   available, none used since GW1" is a fact. "Your process is idle" is an
   insult wearing a fact's clothes.
4. No rhetorical constructions. Specifically: no negation-for-emphasis
   ("Nothing here asks you to..."), no triads, no repeated-phrase asides
   ("not a hit, not a penny"), no rhetorical questions, no one-sentence
   paragraphs written for punch.
5. No em-dashes. A comma, a period, or a table column does the same work
   without the drama.
6. No filler intensifiers or throat-clearing: genuinely, simply, quietly,
   frankly, actually, importantly, worth noting, it is worth, the truth is,
   here is the thing, make no mistake, at the end of the day, let us be
   clear.
7. No unfalsifiable comparisons. "22 points behind what the same money could
   buy" needs the alternative squad, the gameweeks, and the source, or it
   does not get written.
8. Two sentences maximum per item body. If it needs more, it needs a table
   or a chart instead.
9. Prefer a table or a chart to a paragraph whenever the content is more
   than two numbers. The reader is scanning, not reading.

Plain, short, checkable. A reader should be able to verify every clause
against a panel or dismiss the item.
""".strip()

#: Rhetorical constructions that are a REJECTION. Each is a substring or
#: pattern that does not appear in plain grounded analysis, so the false
#: positive rate is near zero. Case-insensitive.
_SLOP_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bnothing here (?:asks|says|requires|demands|suggests)\b",
     "negation-for-emphasis opener"),
    (r"\bnot a \w+,\s*not a \w+", "repeated-phrase aside"),
    (r"\byour (?:process|approach|discipline|instinct|mindset|patience)\b",
     "judgment about the manager rather than the squad"),
    (r"\byou have (?:been|made zero|done nothing)\b",
     "judgment about the manager rather than the squad"),
    ((r"\b(?:worth noting|it is worth noting|the truth is|make no mistake"
      r"|here is the thing|here's the thing|at the end of the day"
      r"|let us be clear|let's be clear|to be clear)\b"),
     "throat-clearing filler"),
    (r"\b(?:genuinely|quietly|frankly|simply put|make no mistake)\b",
     "filler intensifier"),
    (r"\bthis (?:is not|isn't) (?:about|a) \w+[,.]", "rhetorical reframe"),
    (r"\?(?:\s|$)", "rhetorical question"),
)

_COMPILED = tuple((re.compile(p, re.IGNORECASE), why)
                  for p, why in _SLOP_PATTERNS)

#: Dashes a model reaches for as drama. Rewritten, never rejected: the tic is
#: formatting, and dropping a true finding over punctuation loses more than
#: it protects.
_DASHES = re.compile(r"\s*[—–]\s*")


def normalize_prose(text: str) -> str:
    """Rewrite the losslessly fixable tics. Em-dashes become commas.

    A trailing dash aside ("the plan spends it - and you have the FTs")
    becomes a comma clause, which reads the same without the theatre.
    """
    if not text:
        return text
    out = _DASHES.sub(", ", text)
    out = re.sub(r",\s*,", ",", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


def slop_findings(text: str) -> list[str]:
    """Every rhetorical construction in ``text``, named. Empty means clean.

    The caller decides what a finding costs. The briefing validator drops
    the item and counts it; a report surface may prefer to flag it.
    """
    if not text:
        return []
    return [why for rx, why in _COMPILED if rx.search(text)]


def ungrounded(text: str) -> bool:
    """True when a sentence-level claim carries no number at all.

    Rule 2's cheap half: prose with no digit anywhere is an assertion about
    the world with nothing to check it against. Player and source names are
    validated separately, against the payload, by the caller.
    """
    return bool(text) and not re.search(r"\d", text)
