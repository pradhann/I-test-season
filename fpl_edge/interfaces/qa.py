"""The question router: conversational access to everything the engine knows.

Every incoming Telegram message is offered to this router first. A message that
matches a question intent gets an :class:`Answer` — text plus zero or more PNG
images — and never reaches the idea inbox; everything else falls through and is
treated as an idea, exactly as before. The split matters: "which defenders have
the highest xPoints" is a query, "I like Rashford" is a belief to be tracked,
and conflating them either pollutes the idea registry with questions or answers
beliefs nobody asked about.

Routing is deterministic pattern matching, not a language model. The question
types are enumerable, the parameters (position, metric, horizon, source name)
are extractable, misroutes are debuggable from a table of patterns, and the
whole thing runs offline in tests. A message the router does not understand is
not guessed at.

Handlers read through :class:`fpl_edge.store.LeasedWarehouse` handed in by the
bot, so the single-writer lock stays free between questions.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Callable

from fpl_edge.config import USER

SEASON_DEFAULT = "2026-27"

_POS_WORDS = {
    "goalkeeper": 1, "goalkeepers": 1, "keeper": 1, "keepers": 1, "gk": 1, "gkp": 1,
    "defender": 2, "defenders": 2, "def": 2, "defs": 2,
    "midfielder": 3, "midfielders": 3, "mid": 3, "mids": 3,
    "forward": 4, "forwards": 4, "striker": 4, "strikers": 4, "fwd": 4, "fwds": 4,
}
_POS_NAME = {1: "goalkeepers", 2: "defenders", 3: "midfielders", 4: "forwards"}


@dataclass
class Answer:
    text: str
    images: list[tuple[str, bytes]] = field(default_factory=list)  # (filename, png)


@dataclass(frozen=True)
class Intent:
    name: str
    pattern: re.Pattern
    handler: Callable


class QuestionRouter:
    """Ordered intent table. First match wins; no match returns None."""

    def __init__(self, wh, *, season: str = SEASON_DEFAULT,
                 entry_id: int = USER.entry_id) -> None:
        self.wh = wh
        self.season = season
        self.entry_id = entry_id
        self.intents: list[Intent] = [
            Intent("review_team",
                   re.compile(r"\b(review|show|rate|check)\b.{0,20}\b(my\s+)?(team|squad)\b|"
                              r"\bmy\s+(team|squad)\b.{0,12}\b(review|look|doing)\b", re.I),
                   self.review_team),
            Intent("suggest_transfers",
                   re.compile(r"\b(suggest|recommend|make|any|what|which)\b.{0,24}\btransfers?\b|"
                              r"\btransfers?\b.{0,16}\b(should|make|suggest)\b", re.I),
                   self.suggest_transfers),
            Intent("ideas_this_gw",
                   re.compile(r"\b(my\s+)?ideas?\b.{0,30}\b(gw|gameweek|week|said|this)\b|"
                              r"\bwhat\s+have\s+i\s+said\b", re.I),
                   self.ideas_this_gw),
            Intent("vs_elite",
                   re.compile(r"\b(differ|different|compare|vs|versus)\b.{0,40}"
                              r"\b(top|elite|best)\b.{0,20}\bmanagers?\b|"
                              r"\btop\s+managers?\b.{0,24}\b(own|different|compare)\b", re.I),
                   self.vs_elite),
            Intent("fixtures_target",
                   re.compile(r"\bfixtures?\b.{0,30}\b(target|good|easy|next)\b|"
                              r"\b(target|which)\b.{0,12}\bfixtures?\b|"
                              r"\bessential\b.{0,24}\b(players?|picks?)\b|"
                              r"\b(players?|picks?)\b.{0,24}\bessential\b", re.I),
                   self.fixtures_target),
            # Links first: a shared URL is always a transcription request,
            # whatever words surround it.
            Intent("link_ingest",
                   re.compile(r"https?://\S+", re.I),
                   self.link_ingest),
            # Chip talk across creators ("who is playing bench boost?").
            Intent("creator_chip_scan",
                   re.compile(r"\b(bench\s*boost|triple\s*captain|wild\s*card|wildcard|"
                              r"free\s*hit)\b.{0,40}\b(creators?|content|anyone|who)\b|"
                              r"\b(who|which|any)\b.{0,30}\b(playing|using|on)\b.{0,16}"
                              r"\b(bench\s*boost|triple\s*captain|wildcard|free\s*hit)\b",
                              re.I),
                   self.creator_chip_scan),
            # Any message that names a tracked creator routes here -- matched
            # against the roster with typo-tolerant aliases, not a phrase
            # template. "Summarize FPLRaptor", "key ideas from FPl Harry and
            # FPLWire", "what is Let's Talk FPL saying?" all land.
            Intent("creator_summary",
                   re.compile(r".", re.S),  # gate is the roster match below
                   self.creator_summary),
            Intent("top_by_position",
                   re.compile(r"\b(which|what|top|best|highest)\b.{0,30}"
                              r"\b(goalkeepers?|keepers?|defenders?|midfielders?|forwards?|"
                              r"strikers?|players?)\b.{0,40}"
                              r"\b(xpoints?|xpts|expected|points|haul|value|ceiling)\b|"
                              r"\b(xpoints?|xpts|expected points)\b.{0,30}"
                              r"\b(goalkeepers?|keepers?|defenders?|midfielders?|forwards?)\b",
                              re.I),
                   self.top_by_position),
        ]

    def route(self, text: str) -> Answer | None:
        stripped = text.strip()
        for intent in self.intents:
            m = intent.pattern.search(stripped)
            if intent.name == "creator_summary":
                # The pattern is a catch-all; the real gate is naming a tracked
                # creator. Without a creator named, fall through to other
                # intents and ultimately the idea inbox.
                from fpl_edge.interfaces.creators import match_creators

                if not match_creators(stripped):
                    continue
            if m:
                try:
                    return intent.handler(stripped, m)
                except Exception as exc:  # noqa: BLE001 - a failed answer must say so
                    return Answer(
                        f"I understood that as '{intent.name}' but the answer "
                        f"failed: {type(exc).__name__}: {exc}"
                    )
        return None

    # -- handlers --------------------------------------------------------------

    def _now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)

    def _snapshot(self):
        return self.wh.snapshot_at(self._now())

    def _projection(self):
        """The cached per-player simulation artefact, or None with a reason."""
        import pandas as pd
        from pathlib import Path

        p = Path("data/warehouse/gw1_projection.parquet")
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        # The solve script writes the frame with `code` as the index; every
        # handler here wants it as a column. Normalising once at the loader is
        # what keeps that decision from crashing each handler separately.
        if "code" not in df.columns:
            df = df.reset_index()
        return df

    def _team_state(self):
        from fpl_edge.myteam.sources import PublicEntryClient
        from fpl_edge.myteam.state import reconstruct
        from fpl_edge.myteam.store import MyTeamStore

        private = None
        try:
            from fpl_edge.myteam.private import PrivateTeamClient

            pc = PrivateTeamClient()
            if not PrivateTeamClient.disabled_by_env():
                private = pc.fetch(self.entry_id)
        except Exception:  # noqa: BLE001 - fall back to public/manual
            private = None
        client = PublicEntryClient()
        try:
            store = MyTeamStore(self.entry_id)
            return reconstruct(
                self._snapshot(), entry_id=self.entry_id, season=self.season,
                client=client, manual=store.confirmed(season=self.season),
                private=private, validate=False,
            )
        finally:
            client.close()

    def review_team(self, text: str, m) -> Answer:
        from fpl_edge.interfaces.render import PitchPlayer, squad_pitch_png
        from fpl_edge.myteam.state import Provenance

        state = self._team_state()
        if state.picks is None:
            return Answer(
                "I can't see your squad yet. Before GW1 kicks off FPL publishes "
                "nothing publicly — either run `fpl myteam auth` once (reads it "
                "live) or text /setsquad with your 15."
            )
        players = self._snapshot().players(self.season)
        name = dict(zip(players["code"], players["web_name"]))
        price = dict(zip(players["code"], players["price_tenths"]))
        pos = dict(zip(players["code"], players["position"]))
        status = dict(zip(players["code"], players["status"]))

        proj = self._projection()
        xpts = {}
        if proj is not None:
            xpts = dict(zip(proj["code"].astype(int), proj["xpts"]))

        def card(p) -> PitchPlayer:
            note = ""
            if status.get(p.code) in ("i", "s", "u"):
                note = {"i": "inj", "s": "susp", "u": "gone"}[status[p.code]]
            elif p.code in xpts:
                note = f"{xpts[p.code]:.1f} xPts"
            return PitchPlayer(
                name=str(name.get(p.code, p.code)), position=int(pos.get(p.code, 3)),
                price_tenths=int(price.get(p.code, 0)),
                is_captain=p.is_captain, is_vice=p.is_vice, note=note,
            )

        starters = [card(p) for p in state.picks if p.is_starter]
        bench = [card(p) for p in state.picks if not p.is_starter]
        total_x = sum(xpts.get(p.code, 0.0) for p in state.picks if p.is_starter)
        flags = [f"{name.get(p.code)}: {status.get(p.code)}" for p in state.picks
                 if status.get(p.code) in ("i", "s", "d", "u")]

        src = {Provenance.PRIVATE_API: "live from your account",
               Provenance.PUBLIC_PICKS: "public picks",
               Provenance.MANUAL: "as you entered it"}.get(state.provenance, "?")
        bank = f"£{state.bank.tenths / 10:.1f}m" if state.bank else "?"
        lines = [
            f"Your team ({src}). Bank {bank}.",
            f"Projected XI total this GW: {total_x:.1f} pts." if xpts else
            "No projection artefact cached — run `make solve` for xPts.",
        ]
        if flags:
            lines.append("Flags: " + "; ".join(flags))
        cap = next((p for p in state.picks if p.is_captain), None)
        if cap is not None and xpts:
            best = max((p for p in state.picks if p.is_starter),
                       key=lambda p: xpts.get(p.code, 0))
            if best.code != cap.code and xpts.get(best.code, 0) > xpts.get(cap.code, 0) + 0.5:
                lines.append(
                    f"Captaincy check: {name.get(best.code)} projects "
                    f"{xpts[best.code]:.1f} vs your captain "
                    f"{name.get(cap.code)} {xpts.get(cap.code, 0):.1f}."
                )
        png = squad_pitch_png(
            starters, bench,
            title=f"{USER.team_name} — {self.season} GW{state.gw}",
            subtitle=f"bank {bank} · source: {src}",
        )
        return Answer("\n".join(lines), images=[("team.png", png)])

    def suggest_transfers(self, text: str, m) -> Answer:
        state = self._team_state()
        if state.picks is None:
            return Answer(
                "No squad to transfer from yet — GW1 hasn't started and no "
                "squad is set. Run `fpl myteam auth` once or /setsquad."
            )
        # Full solve is minutes of compute; the bot answers from the persisted
        # plan and says how stale it is, which is what a phone answer can honour.
        import json
        from pathlib import Path

        plan_p = Path("data/warehouse/gw1_plan.json")
        if not plan_p.exists():
            return Answer("No solved plan cached. Run `make solve` and ask again.")
        plan = json.loads(plan_p.read_text())
        players = self._snapshot().players(self.season)
        name = dict(zip(players["code"], players["web_name"]))
        mine = {p.code for p in state.picks}
        theirs = set(plan["gw1"]["squad"])
        buy = [name.get(c, c) for c in sorted(theirs - mine)]
        sell = [name.get(c, c) for c in sorted(mine - theirs)]
        generated = plan.get("generated_at", "?")[:16].replace("T", " ")
        lines = [f"Against the solved plan ({generated}Z, "
                 f"objective mode {plan.get('objective_mode')}):"]
        if not buy:
            lines.append("Your squad already matches the plan. Hold.")
        else:
            lines.append("The plan holds instead of you: " + ", ".join(sell[:6]))
            lines.append("And owns instead: " + ", ".join(buy[:6]))
            lines.append(
                f"That is {len(buy)} difference(s). Before GW1 transfers are "
                f"free and unlimited; after, weigh each at -4."
            )
        for note in plan.get("notes", []):
            lines.append(f"Note: {note}")
        return Answer("\n".join(lines))

    def top_by_position(self, text: str, m) -> Answer:
        from fpl_edge.interfaces.render import hbar_png

        proj = self._projection()
        if proj is None:
            return Answer("No projection artefact cached — run `make solve` first.")
        lowered = text.lower()
        pos = next((v for w, v in _POS_WORDS.items()
                    if re.search(rf"\b{w}\b", lowered)), None)
        metric, label = "xpts", "expected points"
        if "haul" in lowered or "ceiling" in lowered:
            metric, label = "p_haul", "P(10+ point haul)"
        elif "value" in lowered:
            metric, label = "value", "points per £m"

        df = proj
        if pos is not None:
            df = df[df["position"] == pos]
        df = df.nlargest(10, metric)
        rows = [(str(r.web_name), float(getattr(r, metric))) for r in df.itertuples()]
        title = f"Top {_POS_NAME.get(pos, 'players')} by {label} — GW1"
        png = hbar_png(rows, title=title, unit=label)
        top = df.iloc[0]
        txt = (f"{title}:\n" + "\n".join(
            f"{i}. {r.web_name} £{r.price_tenths / 10:.1f} — "
            f"{getattr(r, metric):.2f} ({r.selected_by_pct:.0f}% owned)"
            for i, r in enumerate(df.head(5).itertuples(), 1))
            + f"\n\nSpread on {top.web_name}: p10 {top.p10:.0f}, p90 {top.p90:.0f}.")
        return Answer(txt, images=[(f"top_{metric}.png", png)])

    def fixtures_target(self, text: str, m) -> Answer:
        from fpl_edge.interfaces.render import fixture_grid_png
        from fpl_edge.models.team_goals import DixonColesModel

        snap = self._snapshot()
        model = DixonColesModel()
        model.fit(snap, self.season)
        gws = self._next_gws(snap, 5)
        pred = model.predict(snap, self.season, gws)
        teams = snap.table("dim_team", where="season = ?", params=[self.season])
        short = dict(zip(teams["team_code"], teams["short_name"]))

        # Rank clubs by expected goal difference over the run.
        agg = pred.groupby("team_code").agg(
            xgf=("exp_goals_for", "sum"), xga=("exp_goals_against", "sum")).reset_index()
        agg["edge"] = agg["xgf"] - agg["xga"]
        best = agg.nlargest(8, "edge")

        rows, opps, labels = [], [], []
        lo, hi = pred["exp_goals_against"].min(), pred["exp_goals_against"].max()
        for r in best.itertuples():
            tp = pred[pred["team_code"] == r.team_code].sort_values("gw")
            diff, opp = [], []
            for g in gws:
                row = tp[tp["gw"] == g]
                if row.empty:
                    diff.append(0.5); opp.append("—")
                else:
                    x = row.iloc[0]
                    diff.append(float((x["exp_goals_against"] - lo) / max(hi - lo, 1e-9)))
                    o = short.get(x["opponent_code"], "?")
                    opp.append(o.upper() if x["is_home"] else o.lower())
            rows.append(diff); opps.append(opp)
            labels.append(short.get(r.team_code, str(r.team_code)))
        png = fixture_grid_png(labels, [int(g) for g in gws], rows, opps,
                               title="Best fixture runs, our fitted ratings "
                                     "(UPPER = home, green = easy)")

        proj = self._projection()
        essential = ""
        if proj is not None:
            best_codes = set(best["team_code"])
            players = snap.players(self.season)
            club = dict(zip(players["code"], players["team_code"]))
            pool = proj[proj["code"].map(club).isin(best_codes)].nlargest(6, "xpts")
            essential = "\nEssential from those runs (GW1 xPts): " + ", ".join(
                f"{r.web_name} {r.xpts:.1f}" for r in pool.itertuples())
        txt = ("Fixture targets over the next 5 GWs, ranked by our expected "
               "goal difference:\n" + "\n".join(
                   f"{i}. {short.get(r.team_code)} — xGF {r.xgf:.1f}, xGA {r.xga:.1f}"
                   for i, r in enumerate(best.head(5).itertuples(), 1)) + essential)
        return Answer(txt, images=[("fixtures.png", png)])

    def _next_gws(self, snap, n: int) -> list[int]:
        nxt = snap.next_gw(self.season)
        return list(range(int(nxt), int(nxt) + n))

    def creator_summary(self, text: str, m) -> Answer:
        from fpl_edge.interfaces.creators import match_creators, summarize_creator

        creators = match_creators(text)
        blocks = []
        for creator in creators[:3]:
            summary = summarize_creator(self.wh, creator)
            blocks.append(summary.render())
        note = ("\n\nClaims are extracted and player-resolved from stored "
                "transcripts/descriptions; share a specific video link and I "
                "will transcribe that one directly.")
        return Answer("\n\n".join(blocks) + note)

    def creator_chip_scan(self, text: str, m) -> Answer:
        from fpl_edge.interfaces.creators import chip_scan

        rows = chip_scan(self.wh)
        if not rows:
            return Answer(
                "No chip talk found in the last 10 days of stored creator "
                "content. Most YouTube items are description-only until "
                "transcripts accumulate, so absence here is weak evidence — "
                "share a specific video link and I'll transcribe it."
            )
        wanted = None
        low = text.lower()
        for phrase, chip in {"bench boost": "bboost", "triple": "3xc",
                             "wildcard": "wildcard", "free hit": "freehit"}.items():
            if phrase in low:
                wanted = chip
                break
        picked = [r for r in rows if wanted is None or r[1] == wanted] or rows
        lines = ["Chip talk across tracked creators (last 10 days):"]
        seen = set()
        for creator, chip, title, snippet in picked[:8]:
            key = (creator, chip)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"• {creator} — {chip}: \"{snippet[:90]}\"")
        lines.append("\nThese are keyword hits with context, not resolved "
                     "claims; chip advice enters the model only through the "
                     "scored-claim pipeline.")
        return Answer("\n".join(lines))

    def link_ingest(self, text: str, m) -> Answer:
        from fpl_edge.interfaces.creators import find_url, ingest_link

        url = find_url(text)
        if url is None:
            return Answer("I saw something like a link but could not parse it.")
        findings = ingest_link(self.wh, url)
        return Answer(findings.render())

    def vs_elite(self, text: str, m) -> Answer:
        state = self._team_state()
        picks = self.wh.sql(
            """
            SELECT p.code, count(DISTINCT p.entry_id) AS holders
            FROM fact_manager_pick p
            WHERE p.season = ? AND p.gw = (
                SELECT max(gw) FROM fact_manager_pick WHERE season = ?)
            GROUP BY p.code ORDER BY holders DESC
            """,
            [self.season, self.season],
        )
        if picks.empty:
            return Answer(
                "Elite squads for this season aren't public yet — FPL reveals "
                "picks only after each gameweek's deadline passes. The moment "
                "GW1 locks (Fri 17:30 UTC) the nightly crawl ingests the "
                "shortlist's squads and this comparison lights up. Until then: "
                "`fpl elite` shows WHO the skill-scored managers are."
            )
        players = self._snapshot().players(self.season)
        name = dict(zip(players["code"], players["web_name"]))
        total = self.wh.sql(
            "SELECT count(DISTINCT entry_id) AS n FROM fact_manager_pick "
            "WHERE season = ?", [self.season]).iloc[0]["n"]
        mine = {p.code for p in state.picks} if state.picks else set()
        top = picks.head(15)
        lines = [f"Elite ownership (of {int(total)} tracked managers), "
                 f"✓ = you own him:"]
        for r in top.itertuples():
            mark = " ✓" if int(r.code) in mine else ""
            lines.append(f"• {name.get(int(r.code), r.code)} — "
                         f"{int(r.holders)}/{int(total)}{mark}")
        if mine:
            elite_set = set(picks.head(30)["code"].astype(int))
            only_mine = [name.get(c, c) for c in mine - elite_set]
            if only_mine:
                lines.append("\nYou hold, they don't: " + ", ".join(map(str, only_mine[:8])))
        return Answer("\n".join(lines))

    def ideas_this_gw(self, text: str, m) -> Answer:
        snap = self._snapshot()
        try:
            gw = snap.next_gw(self.season)
        except KeyError:
            gw = 1
        rows = self.wh.sql(
            """
            SELECT created_utc, raw_text, thesis, status, acted, outcome
            FROM idea WHERE season = ? AND gw <= ? AND gw + horizon_gws > ?
            ORDER BY created_utc DESC LIMIT 15
            """,
            [self.season, int(gw), int(gw)],
        )
        if rows.empty:
            return Answer(f"No ideas covering GW{gw} yet. Text me one — "
                          f"'I like X', 'X captain?', 'fade Y'.")
        lines = [f"Your ideas in play for GW{gw}:"]
        for r in rows.itertuples():
            when = str(r.created_utc)[:16]
            acted = "acted" if r.acted else "not acted"
            out = r.outcome or r.status
            lines.append(f"• {when} — \"{r.raw_text[:60]}\" → {r.thesis[:80]} "
                         f"[{acted}, {out}]")
        return Answer("\n".join(lines))
