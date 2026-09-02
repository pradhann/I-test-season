"""The house prose rule and its enforcement (fpl_edge/platform/prose_style).

The owner's standard, in his words: "NO AI SLOP". These tests pin the exact
answer that provoked it, so the constructions in it can never ship again.
"""

from __future__ import annotations

from fpl_edge.platform import briefing_intel as bi
from fpl_edge.platform.prose_style import (
    STYLE_RULES,
    normalize_prose,
    slop_findings,
    ungrounded,
)

#: The chat answer the owner rejected, verbatim (em-dashes included).
SLOP = (
    "Your team is not bad. Your process is idle. You have made zero "
    "transfers in two gameweeks, and in that time your squad has drifted "
    "about 22 expected points behind what the same money could buy. "
    "Nothing here asks you to gamble, wildcard, or chase. Two of the fixes "
    "cost nothing — not a hit, not a penny — and you already have the "
    "transfers to make them."
)

GOOD = ("Haaland 70.1% owned (n=309 elite), you do not hold him. "
        "Consensus GW3 xPts 7.2 vs your best forward 4.6.")


def test_the_rejected_answer_is_caught_by_more_than_one_rule():
    found = slop_findings(SLOP)
    assert "negation-for-emphasis opener" in found
    assert "repeated-phrase aside" in found
    assert "judgment about the manager rather than the squad" in found


def test_grounded_prose_passes_clean():
    assert slop_findings(GOOD) == []
    assert not ungrounded(GOOD)


def test_prose_with_no_number_anywhere_is_ungrounded():
    assert ungrounded("Your defence looks vulnerable this week.")
    assert not ungrounded("Your defence conceded 4.1 xG over GW1-2.")


def test_em_dashes_are_rewritten_not_rejected():
    """A formatting tic must not cost a true finding: the dash is rewritten
    losslessly, and what remains is clean."""
    out = normalize_prose("Isak 6.7 — consensus says 3.5")
    assert "—" not in out and "–" not in out
    assert out == "Isak 6.7, consensus says 3.5"
    assert slop_findings(out) == []


def test_rhetorical_questions_and_throat_clearing_are_slop():
    assert slop_findings("Worth noting that Haaland is 70.1% owned.")
    assert slop_findings("So what does 70.1% ownership actually mean?")
    assert slop_findings("Frankly, the 70.1% number says enough.")


def test_the_style_rules_ship_in_both_prompts():
    """One block, two surfaces: an edit to the rule moves the chat analyst
    and the briefing together, which is the point of the shared module."""
    from fpl_edge.platform.chat_agent import CHARTER

    assert STYLE_RULES in CHARTER
    prompt = bi.build_prompt("META", {"panel": {}}, {"panel": None})
    assert STYLE_RULES in prompt


# ------------------------------------------------- enforcement in the briefing


def _item(**over):
    item = {
        "headline": "Haaland 70.1% owned, you do not hold him",
        "why": "Consensus GW3 xPts 7.2 against your best forward at 4.6.",
        "severity": 1,
        "numbers": [{"value": 70.1, "unit": "%",
                     "source_panel": "ownership_eo", "as_of": None}],
        "codes": [1],
        "drill": {"drawer": 1},
        "source_panels": ["ownership_eo"],
    }
    item.update(over)
    return item


def _validate(items):
    return bi.validate_items(items, panels={"ownership_eo"}, codes={1})


def test_a_slop_item_is_dropped_and_counted():
    kept, rejected = _validate([_item(why=SLOP)])
    assert kept == [] and rejected == 1


def test_an_em_dash_item_survives_with_the_dash_rewritten():
    kept, rejected = _validate(
        [_item(headline="Isak 6.7 — consensus says 3.5")])
    assert rejected == 0 and len(kept) == 1
    assert "—" not in kept[0]["headline"]
    assert kept[0]["headline"] == "Isak 6.7, consensus says 3.5"


def test_a_clean_item_is_untouched():
    kept, rejected = _validate([_item()])
    assert rejected == 0
    assert kept[0]["headline"] == _item()["headline"]
    assert kept[0]["why"] == _item()["why"]
