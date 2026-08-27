# Creator identity research — finding real FPL entry ids for the tracked panel

Research date: **2026-08-27**. Season **2026/27**.
Output: [`data/panels/creator_panel_2026_27.yaml`](../../data/panels/creator_panel_2026_27.yaml).

This file is the audit trail. It exists so that in six months nobody has to
take the ids on trust, and so that next August's re-verification starts from
what was actually checked rather than from a number in a YAML file.

All API calls used `User-Agent: fpl-edge/0.1 (personal research; contact via
repo owner)` with ≥1.2 s between requests.

---

## Result

**15 of 16 rows resolved.** The one null is deliberate and is not a failure —
see [Solio](#2-solio--a-brand-not-a-person).

| Person | Show(s) | Entry | Verified | Confidence | What the id rests on |
|---|---|---:|:---:|---|---|
| Pras | The FPL Wire | 3315 | yes | conclusive | admins league 725 "The FPL Wire Discord" |
| Zophar (Utkarsh Dalmia) | The FPL Wire | 2177 | yes | high | in both Wire leagues; best-ever rank **17** matches his bio |
| Lateriser (Pranil Sheth) | The FPL Wire | 6816 | yes | conclusive | admins league 311543 "The FPL Wire" |
| BigMan Bakar | The FPL Wire | 5133 | yes | high | ELITE_NAMED, re-verified; best rank **4** matches "#4 in 2014/15" |
| FPL Harry (Harry Daniels) | FPL Harry | 3054 | yes | conclusive | admins league 7735 "www.youtube.com/@FPLHarry" |
| FPL Raptor (Ross Dowsett) | FPL Raptor | 199 | yes | conclusive | admins league 40378 "youtube.com/FPLRaptor" |
| Mark Sutherns | FPL BlackBox | 252 | yes | conclusive | in "FPL BlackBox Contributors"; **10** top-10k matches brief exactly |
| Az Phillips | FPL BlackBox | 246 | yes | conclusive | admins "youtube.com/FPLBlackBox" + Contributors + Patreon leagues |
| Ben Crellin | FFHub | 53517 | yes | high | ELITE_NAMED, re-verified; Hub Contributors; "Planning Makes Perfect" |
| FPL Salah (Abdul Rehman) | FFHub | 70 | yes | conclusive | admins league 1032385 "FPL Salah's League" |
| Andy (Let's Talk FPL) | Let's Talk FPL | 41 | yes | conclusive | admins league 28071 "youtube.com/letstalkfpl 📽️" |
| **Solio Analytics (brand)** | Solio Analytics | **null** | — | — | **a company, not a manager — no house team exists** |
| Sertalp Bilal Cay | Solio Analytics | 3333334 | yes | high | in 8-entry "Solio Analytics" league; team "Solio Optimized FC" |
| Jonny Currie | Solio Analytics | 124 | yes | conclusive | admins league 1524 "fpl.solioanalytics.com" |
| Jørgen Gjærum | Solio Analytics | 2843 | yes | conclusive | admins league 1757 "Solio Analytics" |
| James Palmer | Solio Analytics | 1000001 | yes | high | member of the Solio Analytics league |

---

## The method that worked, and why the obvious ones didn't

### Dead ends, in the order they were tried

**Web search for "<creator> team ID".** Nothing. Every query returned
generic "how to find your own FPL team ID" SEO pages. Creators do not, as a
rule, publish their entry id — the brief's assumption that "most FPL creators
publish their team ID" did not hold for this panel.

**Podcast RSS descriptions.** Fetched and grepped the full feeds for
The FPL Wire (`BLU9598812574`, 5.7 MB), FPL Harry (`BLU5639728837`, 3.6 MB),
FPL BlackBox (`BLU4752868283`) and FPL Raptor (`COMG8319298159`), searching for
`team id`, `entry/<digits>`, and `id: <digits>`. **Zero entry ids.** What the
feeds *do* carry is mini-league **auto-join codes** (`v8tx2p`, `vjng38` for
BlackBox; `5s23tm` for Harry). Those codes are not league ids and do not
resolve to one without an authenticated session — fetching
`/leagues/auto-join/<code>` returns the SPA shell (HTTP 200, identical
`etag` for every code).

**YouTube channel `/videos` pages** for @FPLHarry, @FPLRaptor, @FPLBlackBox —
no entry ids in the served HTML.

**linktr.ee/FPL_Harry** — nine links, all social/membership. A league auto-join
code, no entry id.

### ⚠ A source that actively fabricated data — do not reuse

`https://www.fantasyfootballpundit.com/fpl-content-creators-league-table/`

Search surfaced this as an "FPL Content Creators League Table", which is
precisely what this task needs. Fetching-and-summarising it returned a
tidy 20-row table of creator → team ID:

> Andy Martin 20360 · Big Man Bakar 271 · Ben Crellin 2869 · Fabio Borges 2869 ·
> FPL Harry 3544 · FPL Raptor 746 · FPL Salah 156 · Lateriser 24194 ·
> Let's Talk FPL 24 · Pras 4805 · Zophar 9505 …

**It is entirely false.** Two tells were visible before any API call: Ben
Crellin and Fabio Borges were given the *same* id (2869), and three of the
values contradicted ids this repo has already verified (Crellin 53517, Bakar
5133, LTFPL 41). Fetching the page with curl confirmed the table is JS-loaded
and **the raw HTML contains none of those numbers** — they were invented by the
summarising step.

Verifying all ten against `/api/entry/{id}/` settled it — every one is an
unrelated stranger:

| Claimed | Actually |
|---:|---|
| 4805 "Pras" | Aleksander Kaczerowski, "GetTheShiieet!!", Poland |
| 9505 "Zophar" | Max Roberts, "Rattle FC", England |
| 3544 "FPL Harry" | Niklavs Grava, "Wieffer Vendetta", England |
| 746 "FPL Raptor" | A Almarzooqi, "Arsenal", UAE |
| 156 "FPL Salah" | Mohammed Naif, "Headache", Saudi Arabia |
| 24194 "Lateriser" | Musab AL, "Musab", Kuwait |
| 2869 "Ben Crellin" | Jakub Makiewicz, "Grażynki", Poland |
| 271 "Big Man Bakar" | Dave Erskine, "Joga Bonito FC", England |
| 24 "Let's Talk FPL" | Omar Snær Omarsson, "Lacroissant", Norway |
| 20360 "Andy Martin" | Morgan Boyd, "Rosario Jrs", Northern Ireland |

This is exactly the defect the panel contract is written to prevent: a
plausible table, rendered under real people's names, all wrong. **Had these
been written to the seed file unverified, the repo would have shipped the
20-stale-ids bug a second time.**

### The method that did work: the public `leagues` block

`GET /api/entry/{id}/` returns, for **any** entry and with no authentication,
a `leagues.classic[]` array in which every invitational league (`league_type:
"x"`) carries its `id`, its user-set `name`, and — critically — its
**`admin_entry`**.

That gives a self-identification primitive. Only the account holder can create
and administer a league. So an entry that is `admin_entry` of a league named
after a creator's own channel is asserting, through the FPL API itself, that it
belongs to that creator. Entry 3054 administers a league called
`www.youtube.com/@FPLHarry`; entry 199 administers `youtube.com/FPLRaptor`;
entry 70 administers `FPL Salah's League`.

It also gives a **discovery** primitive in the other direction: invite-only
creator leagues (`Hub Contributors`, `FPL BlackBox Contributors`,
`The FPL Wire Discord`, `Solio Analytics`) can be read via
`/api/leagues-classic/{id}/standings/`, and their members *are* the creators.
FPL Raptor and Az Phillips were both found this way, not by search.

**Honest limitation.** League names are user-set free text. Nothing stops an
impostor naming a league after a channel. The reason this is nonetheless
strong is that the admin evidence never stands alone — each confirmed account
is also a member of at least one *invite-only* league curated by a third party
(Fantasy Football Hub, Fantasy Football Scout, the show itself), which an
impostor cannot join, and in several cases the account's *measured* season
history matches a published record claim to the exact number.

---

## Per person

### 1. The FPL Wire — four hosts, four separate people

The show is a panel, not a solo channel. Current lineup, confirmed from the
show's listings and from the Fantasy Football Hub team-reveal articles
("FPL Wire co-host Zophar's…", "FPL Wire co-host Pras's…"): **Lateriser12
(Pranil Sheth)**, **Zophar666 (Utkarsh Dalmia)**, **Pras**, and
**BigManBakar (AbuBakar Siddiq)**. Billed records: Lateriser top-200 ×3,
Zophar top-10k ×7, Pras top-10k ×4, Bakar #4 in 2014/15.

Neither host has a personal YouTube channel distinct from the show; the show's
feed is `https://feeds.megaphone.fm/BLU9598812574` (already registered as
`pod_fplwire`). Note the show's own Hub-contributor listing is under
"FPL Wire", so Hub articles are show-level, not person-level.

**Pras → 3315.** Candidate found in this repo's pinned LiveFPL all-time list
(`elite_list.py:180`, `(3315, 'Pras United')`).
API: `Pras United`, team `Pras's Team`, England.
`leagues.classic` shows **`admin_entry: 3315` for league 725, "The FPL Wire
Discord"** — he created the show's Discord league. Also admins 733 "North West
London Derby" (consistent with a London base), and is a member of 311543 "The
FPL Wire", 357990-adjacent elite leagues (#Elite64, Cønts Alumni, Hall of Fame
🔝1k) and 1757 "Solio Analytics". History: 16 seasons, 5 top-10k, best 4184 —
consistent with the "top 10k ×4" billing (the count has since ticked up).
**Verdict: conclusive.**

**Zophar → 2177.** The trickiest of the confirmed set, and the only one worth
reading the reasoning for.
A web search identified Zophar as **Utkarsh Dalmia**. `elite_list.py:590` has
`(2177, 'Utkarsh D')`. API: `Utkarsh D`, team **`Z`**, India.
That alone is suggestive but not sufficient — "Utkarsh D" is not rare.
Four things converge:
1. He is a member of **both** invite-only Wire leagues — 725 "The FPL Wire
   Discord" (admin = Pras) and 311543 "The FPL Wire" (admin = Lateriser).
   Membership of the co-hosts' private leagues is not something an outsider
   arranges.
2. Team name is literally `Z`.
3. He is on the LiveFPL all-time top-1000, matching a genuinely elite record.
4. **The decisive one.** His published bio describes seven top-10k finishes
   *"including a 17th place finish"*. `/api/entry/2177/history/` returns 17
   seasons, 8 top-10k finishes, and **best-ever rank = 17**. A published
   all-time best of 17th matching the API's best of 17 is not coincidence.

**Verdict: high** (not conclusive only because he admins no
Zophar-branded league — his one admin league is "Shire folk").

**Lateriser → 6816.** Found by resolving the `admin_entry` of league 311543
"The FPL Wire", which appeared in Pras's and Zophar's league lists.
API: `Pranil Sheth`, team `Pranil's Team`, India — and Pranil Sheth is the
name published for Lateriser12. He also admins 311542 "Thought Dump".
History: best-ever rank **30**, 6 top-10k over 17 seasons, consistent with the
"Top 200 ×3" billing. **Verdict: conclusive.**
*(He was not on the owner's original list; included because the brief asked for
every Wire host as a person. Drop at the UI layer if unwanted — the id is sound.)*

**BigMan Bakar → 5133.** Already in `ELITE_NAMED`. **Re-verified live rather
than trusted**: still `BigMan Bakar`, team `The Malouda Triangle`, Pakistan.
Admins 614192 "Kings League" and 975480 "BigMan's Battleground"; member of
invite-only 357990 "Hub Contributors" and 1491 "Multiple Top 10k Finishes".
History: **best-ever rank 4**, matching the "#4 in 2014/15" billing exactly.
**Verdict: high** (account display name is the handle, so no legal-name
cross-check is possible, but the record match is decisive).

### 2. Solio — a brand, not a person

The owner referred to "solioanalytics". **Solio Analytics is a company**, not a
creator with a team: an independent FPL analytics outfit shipping projections,
a planner and an optimiser at `fpl.solioanalytics.com`, plus a podcast. Its
own X account describes **@sertalpbilal as "co-founder of Solio Analytics"**,
and the founding group is Sertalp, James, Jonny and Jørgen.

There is therefore **no single Solio team to link**, and the brand row is
deliberately `entry_id: null`. This is the correct answer, not a gap.

The four co-founders were then resolved as people, via the two Solio-branded
leagues that appeared in Pras's league list:

- **Jonny Currie → 124** — `admin_entry` of league **1524
  "fpl.solioanalytics.com"**. Team "JC Milan", Scotland. *Conclusive.*
- **Jørgen Gjærum → 2843** — `admin_entry` of league **1757 "Solio
  Analytics"**. Team "Guinness FC", Norway. *Conclusive.*
- **Sertalp Bilal Cay → 3333334** — member of league 1757, team **"Solio
  Optimized FC"**, region USA (consistent with the known FPL-optimisation
  author). Also admins 696621 "Analytics League". *High.*
- **James Palmer → 1000001** — member of league 1757, team "Virgil van Pike",
  England; matches co-founder "James". Membership only, no admin evidence, so
  *high*, not conclusive.

League 1757 has only 8 entries, which is why membership carries weight here.

### 3. FPL Harry → 3054

Real name **Harry Daniels** (from search; then confirmed by the account
itself). `elite_list.py:68` carries `(3054, 'Harry Daniels')`.
API: `Harry Daniels`, team `DANIELS XI`, England.
**`admin_entry: 3054` for league 7735, named literally
`www.youtube.com/@FPLHarry`.** He also admins 89929 "H's MVPs 2026/27", 39586
"Watchlist FFScout League!", 39161 "Pros vs Pretenders" and five more, and is a
member of invite-only 1426424 "FFScout Pro Pundits".
History: 10 seasons, 5 top-10k, best 510.
Channel: `https://www.youtube.com/@FPLHarry` (registry key `yt_fplharry`),
podcast `BLU5639728837` (`pod_fplharry`). Single-host show.
**Verdict: conclusive.**

### 4. FPL Raptor → 199

Real name **Ross Dowsett** (Instagram `@fpl__raptor` displays "Ross Dowsett";
X `@FPL__Raptor` displays "FPL Raptor (Ross)"; a Fantasy Football Fix
introduction blog is written in his own voice as "FPL Raptor, or Ross").

He is **not** in the LiveFPL all-time list — his record is not elite — so the
elite-list route failed. Found instead by reading the standings of invite-only
league **357990 "Hub Contributors"**, where the entry `199` appears with the
display name **`FPL Raptor`** and team "Eggspected Goals".

Confirmed from his own league estate: **`admin_entry: 199` for league 40378,
`youtube.com/FPLRaptor`**, plus 370267 "Raptor Members League" and 40409
"Raptor x Beta Squad ft Stormzy" (matching a real Raptor collaboration).
History: 6 seasons, 0 top-10k, best 10198 — worth noting for weighting: a very
large audience, no elite finishing record. **Verdict: conclusive.**

### 5. Mark Sutherns → 252

Already in `ELITE_NAMED`. **Re-verified live**: `Mark Sutherns`, team
`Sutherns Comfort`, England, 20 seasons.
New corroboration gathered here: member of invite-only **14891 "FPL BlackBox
Contributors"** and 14884 "youtube.com/FPLBlackBox", plus 8449 "FFScout Mods &
Cons" and 122418 "Planet #FPL Correspondents".
**Record match: the brief states 10 verified top-10k finishes; counting
`past[]` with `rank <= 10000` gives exactly 10.** Best-ever 42.
**Verdict: conclusive.**

### 6. Ben Crellin → 53517

Already in `ELITE_NAMED`; the brief said reuse, and it was **re-verified rather
than trusted** — still `Ben Crellin`, team `ƃuᴉʞuᴉɥʇuʍopǝpᴉsdn` (an inverted
"upsidedownthinking"), England.
Extra corroboration: member of 357990 "Hub Contributors" and 1291919
"Analytics Elite 64"; admins 648598 **"Planning Makes Perfect"**, which fits
the fixture-planning spreadsheets he is known for.
History: 19 seasons, 8 top-10k, best 550. (The brief said 7 top-10k; the API
now counts 8 — a season has been added since that figure was written.)
**Verdict: high.**

This also remains the cautionary example already recorded in `elite.py`: the
prior season's seed had Ben Crellin at **6586**, which now belongs to Levi
Longworth.

### 7. Az Phillips → 246

No entry in the LiveFPL all-time list, and search yielded only handles
(`@fplblackbox_az`, `@az_fpl`, Brighton-based).

Found by walking the graph: Mark Sutherns' league list contained 14884
`youtube.com/FPLBlackBox` and 14891 `FPL BlackBox Contributors`, both with
`admin_entry: 246`. Resolving 246 gives **`Az Phillips`**, team
**"Blackbox Redemption"**, England, 19 seasons.

He administers the show's entire league estate — 14884 (`youtube.com/FPLBlackBox`),
14891 (Contributors), 15389 (`FPL BlackBox Patreon 2627`) — plus several
personal leagues. History: 4 top-10k, best 817.
**Verdict: conclusive.**

The BlackBox Contributors league (6 entries) also gives the show's roster:
Az Phillips (246), Mark Sutherns (252), Luke Williams (71), Andy North (2325),
Zoe Clarke (7909621), Natalie Chowdhury (4643619) — useful if per-host
attribution is later wanted for BlackBox as it now is for the Wire.

### 8. FPL Salah → 70

The brief gives his real name as **Abdul Rehman**. `elite_list.py:526` carries
`(70, 'Abdul Rehman')`. That name is common enough that the list entry alone
proves nothing.

API: `Abdul Rehman`, team `Attock Athletic`, Scotland, 19 seasons.
**`admin_entry: 70` for league 1032385, named `FPL Salah's League`.** He also
admins 501693 "Running it back!", and is a member of invite-only 357990 "Hub
Contributors" (he is listed as an FFHub contributor under the name "FPL Salah")
and 1338122 "Analytics Elite Qualifier".
History: 6 top-10k, best 604. FFHub bills him as "4 top 5k"; his sub-5000
finishes are consistent with that.
**Verdict: conclusive.**

### 9. Andy, Let's Talk FPL → 41

Already in `ELITE_NAMED`; **re-verified**: `Andy LTFPL`, team
`Let's Talk FPL`, Ireland, 16 seasons, 5 top-10k, best 588.
Additionally: **`admin_entry: 41` for league 28071,
`youtube.com/letstalkfpl 📽️`**. Self-identifying.
**Verdict: conclusive.**

---

## Which shows have multiple hosts

The brief asked for this explicitly, because a pick should tie to a person's
team, not a show's.

| Show (`content_source.creator`) | Hosts | Per-person ids? |
|---|---|---|
| **The FPL Wire** | Lateriser (6816), Zophar (2177), Pras (3315), BigMan Bakar (5133) | **all four resolved** |
| **FPL BlackBox** | Az Phillips (246), Mark Sutherns (252), + Luke Williams (71), Andy North (2325), Zoe Clarke, Natalie Chowdhury | 2 panel members resolved; roster known |
| **Solio Analytics** | Sertalp Cay, Jonny Currie, Jørgen Gjærum, James Palmer | all four resolved |
| FPL Harry | Harry Daniels only | n/a |
| FPL Raptor | Ross Dowsett only | n/a |
| Let's Talk FPL | Andy only | n/a |
| Fantasy Football Hub | many contributors, article-level bylines | Crellin + Salah resolved |

Consequence for the pipeline: an item from `pod_fplwire` **cannot** be
attributed to one team without reading the episode title. The Wire's titles are
person-stamped ("Zophar Gameweek 2 Team", "Pras's Gameweek 1 team reveal"),
which is a usable attribution key. FPL BlackBox episodes are not reliably
person-stamped.

---

## Re-verification checklist for August 2027

1. Re-run every id in the seed file through `/api/entry/{id}/` and diff
   `player_first_name`/`player_last_name` against `entry_api_name`.
   `fpl_edge.ingest.rivals.elite.verify()` and `names.name_matches` already do
   this — reuse them; do not write a second matcher.
2. Where a name no longer matches, **blank the id**, do not guess a new one.
3. To re-derive: the `leagues.classic[].admin_entry` route in this document
   works from a single known-good creator entry and re-finds most of the panel
   in a handful of requests. Start from any confirmed host and walk their
   invitational leagues.
4. Never reuse the Fantasy Football Pundit creators table.
