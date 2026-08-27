"""A name in a structured field is not prose, and must not be scanned like it.

`TranscriptAnalysis.PlayerCall.player` is a NAME the model returned. Resolving
it with the prose scanner produced a real misattribution on live data:
find_mentions tokenises on [a-z0-9]+, so "Martin Ødegaard" loses the stroke
letter, fails on "degaard", falls back to the bare token "martin", and lands on
David Raya Martín. A creator's call about one player would be stored as a claim
about another, under that creator's name.

Strict lookup alone over-corrects -- it refuses "Ezri Konsa" for the registered
"Ezri Konsa Ngoyo". So the rule is: exact alias first, then containment or a
given-name prefix, and never an edit distance. These cases are the live ones
that drove it.
"""

from __future__ import annotations

import pytest

from fpl_edge.ingest.content.analyze import _resolve_call_name


class _Mention:
    def __init__(self, code, matched_name):
        self.code = code
        self.matched_name = matched_name


class _Resolver:
    """Stands in for PlayerResolver with the live warehouse's real answers."""

    def __init__(self, exact, scanned):
        self._exact, self._scanned = exact, scanned

    def lookup(self, name):
        return self._exact.get(name, (None, "unknown"))

    def find_mentions(self, name, _):
        got = self._scanned.get(name)
        return [_Mention(*got)] if got else []


RESOLVER = _Resolver(
    exact={"Martin Ødegaard": (184029, "ok"), "Antonín Kinský": (485055, "ok")},
    scanned={
        # What the prose scanner actually returns on the live warehouse.
        "Martin Ødegaard": (154561, "david raya martin"),
        "Ezri Konsa": (199798, "ezri konsa ngoyo"),
        "Will Osula": (538207, "william osula"),
        "Dan Ballard": (223827, "daniel ballard"),
        "Erik ten Hag squad player Milos Kerkez": (544877, "milos kerkez"),
        "Louie Barry": (999001, "thierno barry"),
        "Mohammed Vuskovic": (999002, "luka vuskovic"),
        "Trent Hume": (999003, "trai hume"),
        "Cristian Mosquera": (999004, "cristhian mosquera"),
    },
)


def test_a_stroke_letter_name_is_not_resolved_to_someone_elses_first_name():
    # The bug in one line: this must be Ødegaard, never Raya.
    assert _resolve_call_name(RESOLVER, "Martin Ødegaard") == 184029


@pytest.mark.parametrize("spoken,code", [
    ("Ezri Konsa", 199798),        # registered as "ezri konsa ngoyo"
    ("Will Osula", 538207),        # given name is a prefix: will/william
    ("Dan Ballard", 223827),       # dan/daniel
    ("Erik ten Hag squad player Milos Kerkez", 544877),  # name inside a phrase
])
def test_a_name_written_longer_or_shorter_still_resolves(spoken, code):
    assert _resolve_call_name(RESOLVER, spoken) == code


@pytest.mark.parametrize("spoken", ["Louie Barry", "Mohammed Vuskovic", "Trent Hume"])
def test_a_shared_surname_with_a_different_person_is_refused(spoken):
    # Each of these is a DIFFERENT footballer who happens to share a surname.
    # Guessing here writes a fabricated claim against a named creator.
    assert _resolve_call_name(RESOLVER, spoken) is None


def test_a_name_needing_a_typo_forgiven_is_dropped_rather_than_guessed():
    # "Cristian" vs registered "Cristhian" differs by an inserted letter. A
    # dropped claim is missing; a wrong one is a fabrication. Drop it.
    assert _resolve_call_name(RESOLVER, "Cristian Mosquera") is None


def test_an_unresolvable_name_is_none_not_an_exception():
    assert _resolve_call_name(RESOLVER, "Somebody Nobody") is None
