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

---

# 2026-09-07 — the shows whose hosts were not on the roster

Second pass, same method, same bar. The corpus tracks ~24 shows whose people
had no row. Every id below was read back from `/api/entry/{id}/` on
2026-09-07 between 07:30 and 08:10 UTC (`User-Agent: fpl-edge/0.1`, ≥1.3 s
between calls); `entry_api_name` is what the API returned, verbatim.

## Result

**38 rows appended: 28 with an id (19 conclusive, 9 high → 28
`entry_verified: true`), 10 deliberately null.** Two of the nulls are brands/
anonymous operators, not failed searches. File totals: 54 rows, 29 conclusive,
14 high, 11 null.

| Show | Person | Entry | Conf. | API name | What it rests on |
|---|---|---:|---|---|---|
| FPL General / The 59th Minute / The Athletic FPL | Mark McGettigan | 176749 | conclusive | Mark McGettigan | admins 38523 "Beat the @FPLGeneral"; team "@FPLGeneral"; Pro Pundits |
| FPL Tom | Tom L | 5406 | conclusive | Tom L | team "FPL TOM"; admins 58929 "FPL Tom Super League"; Pro Pundits |
| FPL Focal | Oscar | 298 | conclusive | Oscar - | admins 3876 "YouTube.com/FPLFocal" + Discord/Members/Experts leagues |
| Gianni Buttice | Gianni Buttice | 2375 | conclusive | Gianni Buttice | admins 42307 "GIANNI BUTTICÈ YOUTUBE"; Mods & Cons; Sky guests |
| Planet FPL | James Linden | 1194 | conclusive | James Linden | admins 122414 "Planet #FPL Podcast" + 122418 Correspondents |
| Planet FPL | Sujan Shah (Suj) | 84298 | high | Sujan Shah | team "Suj - Planet FPL"; in both Planet leagues (admin = James) |
| Who Got The Assist? | Tom C | 454 | conclusive | Tom C (WGTA) | admins 6791 "Who Got The Assist? Minileague", 6829 "WGTA Fight Club"; Hub Contributors |
| Who Got The Assist? | Sam Price | 125 | conclusive | Sam Price | admins 26990 "www.youtube.com/@fplpricey"; in WGTA Minileague |
| Always Cheating | Josh Landon | 88520 | high | Joshua Landon | Hub Contributors; AC Community Team League (admin = show account 5120019) |
| Always Cheating | Brandon Kelley | 279 | high | Brandon Kelley | AC Community Team League + AC Underdogs; admins a league Josh is in |
| FPL Family | Lee Bonfield | 2913 | conclusive | Lee Bonfield | admins 6229 "FPL Family", 6223 "FPL Family Patreon"; Pro Pundits; Sky guests |
| FPL Family / FPL Pod / Scout | Sam Bonfield | 2977 | conclusive | Sam Bonfield | admins 8507 "FPL Extended Family", 8449 "FFScout Mods & Cons"; in 1359 "FPL Pod", 53993 Coreteam |
| FPL BlackBox | Luke Williams | 71 | high | Luke Williams | BlackBox Contributors (6 entries) + Patreon + youtube leagues; feed "Luke (@fpl_dis)" |
| FPL BlackBox / Scout | Andy North | 2325 | high | Andy North | BlackBox Contributors + Patreon; Pro Pundits; feed "Az is joined by Andy North" |
| FPL Mate | Dan | 120 | conclusive | Dan FPL Mate | admins 37471 "youtube.com/FPLMate", 37485, 2139579; team "FPL Mate on YouTube" |
| FPL JUiCE | Nick Turner | 2472 | conclusive | Nick Turner -FPL JUiCE- | admins 544/566/579 JUiCE leagues; name carries the brand |
| FPL JUiCE | Ash | null | — | — | not found; one LOW name-resemblance (24846) rejected |
| Above Average FPL | Adam Currier | 157 | conclusive | Adam Currier | admins 2543 "Above Average FPL", 72643 "AAFPL Invitational" |
| Above Average FPL | Martin Baker | 4470 | high | Martin Baker | only Baker in AAFPL Invitational; 6 further leagues shared with Adam |
| Sky Sports FPL | Mark Briggs | 27539 | conclusive | Mark Briggs | admins 5790/5800/5814 Sky Sports News leagues |
| Sky Sports FPL | James Savundra | 2992401 | high | James Savundra | Sky Sports News Internal + guests (admin = Briggs) |
| Fantasy Football Hub | Rich Clarke | 209763 | conclusive | Richard Clarke | admins 772236 "Hub Ultras"; 4-entry "Green Arrow Hosts"; Hub Team |
| Fantasy Football Hub | Fergi | 2057 | conclusive | Andrew Ferguson | admins 32922 "The Green Arrow FPL Podcast", 32932 "Green Arrow Hosts"; feed email andrew.ferguson@ |
| Fantasy Football Hub | Jack (@Redditor) | 6801 | high | Jack Ukiah | team "Redditor's Predators"; Hub Team + Hub Contributors |
| FPL Fran | Francisco MW | 21 | conclusive | Francisco MW | admins 18475 "YouTube.com/@FPLFran"; team "FPL Fran"; best 437 = Hub's "437 in 2023/24" |
| Fantasy Football Scout | Neale Rigg | 6225 | conclusive | Neale Rigg | admins 1774 "Fantasy Football Scout Members"; Coreteam; Scout Editor |
| Fantasy Football Scout | FPL Chai (stand-in) | 2157 | conclusive | Chai - | admins 150425/203542/672640 "FPL CHAI 26/27"; Pro Pundits |
| Fantasy Football Scout | Joe Lepper | null | — | — | identified, no entry found in any Scout league |
| FML FPL | Alon Shapiro | 28410 | conclusive | Alon Shapiro | admins 5550 "FML FPL Public League", 83218 "FML FPL Prize Mug League", 1917942 |
| FML FPL | Alex Walsh | 220415 | high | Alex Walsh | sole Walsh in Alon's 39-entry league; shares private "AZ vs. LDN" with Alon |
| FPL Pod | Kelly Somers | null | — | — | broadcaster; no id; PL's old show-note ids are strangers |
| FPL Pod | Julien Laurens | null | — | — | PL feed's /entry/2496505/ now Ebbe Gärdelid — rejected |
| All In Football FPL | Barry Stokes | null | — | — | no link anywhere; not in any creator league |
| All In Football FPL | Alex Rex | null | — | — | same |
| AllAboutFPL | Srinivasan S | null | — | — | blog; only an auto-join code (2xywgk) published |
| AllAboutFPL | Surya | null | — | — | first name only |
| FPL Review | (anonymous operator) | null | — | — | brand row; anonymous by design |
| Ignore the Template | (anonymous host) | null | — | — | no name anywhere in feed or page |

## Two more sources that resolved to strangers

Add these to the Fantasy Football Pundit table in the "do not reuse" list.

**Old show-notes entry links, even from the game operator.** The Premier
League's own FPL Pod RSS (`audioboom.com/channels/5001585.rss`) carries
episode notes reading "Sam Bonfield takes the top spot … Her team:
`fantasy.premierleague.com/entry/32044/history`" and "Julien Laurens extends
his lead … `entry/2496505`". Read back today: **32044 = Tomas Naulickas,
"BeoPower", Lithuania; 2496505 = Ebbe Gärdelid, "Change Name", Sweden.**
Likewise the Fantasy Football Scout feed's "Follow Gianni here:
`entry/24862/history`" is now **George Mayhew, "Jacquet Potato"**. These were
presumably guest teams created for a past season's segment and since
re-registered, or simply wrong when written — it does not matter which. Sam
Bonfield's real account (2977) administers four Scout/Family leagues and was
found through the league graph, not through the notes.

**League ids recycle.** The FPL Pod's "Official FPL Podcast (Guests)" league
730132 is today a 4-entry "League of Ordinary Gentlemen"; FML FPL's
`fmlfpl.com/ourteams` links league 146684, today "Balon D Best 3" (31 Nigerian
entries). A league id from any page older than this season is not evidence.

Neither of these is new information about *entry* stability — 53517 still
carries 19 seasons — but they are the first concrete cases in this repo of a
publisher's own link pointing at a stranger, and they justify the rule that
nothing goes in the file without a live read-back.

## Per person — the ones worth reading

### FPL General → 176749 (three shows)
Search identified FPL General as **Mark McGettigan** (Scout "Meet the Manager
#10", Hub "Veterans Series"). Found as `176749 Mark McGettigan, team
"@FPLGeneral"` in the invite-only **FFScout Pro Pundits** league 1426424
(admin 16 = Scout's Tom Johnson). Admins 38523 "Beat the @FPLGeneral", 38555
"#Elite64 Qualifier", 38563 "The McGettigans", 1200595 "Discorders". Region
Scotland; Finn Harps league membership fits the Donegal connection. History:
18 seasons, 3 top-10k, best 102 — his "3 × top 500" billing.
He is the **solo host of The 59th Minute** (feed title "hosted by Mark
McGettigan, known as FPL General") and of **The Athletic FPL Podcast** (acast
description: "The Athletic's Fantasy Premier League expert Mark McGettigan —
aka FPL General"). Three registered shows collapse onto one person, which is
exactly the situation `panel_person_show` exists for. He is also a Scoutcast
regular; that show link was left off the row because Scout's roster is many
people and the item-level attribution will need titles anyway.

### FFScout Pro Pundits 1426424 — the richest discovery league
24 entries, admin 16 ("Tom Johnson", Scout's deputy GM, who also admins 53993
"FFScout Coreteam" and 53998 "Fantasy Football Scout"). Members include Harry
Daniels (3054), Az Phillips (246), Mark McGettigan, Lee and Sam Bonfield,
"Tom L / FPL TOM" (5406), "Oscar - / Focal Point" (298), Andy North (2325),
"Chai -" (2157), Tom Freeman (22728), Neale Rigg via Coreteam. Six of this
pass's rows came from this one standings call. Note the league also contains
people with *no* show in the registry (Tom Hadley, Callum Dummelow, Josh
Cuthbert, Grey Head, Simon March, Obay Eid, Mohamed "FPL Mo", Yelena
Cekerevac, Joshua Scott, Ben Brown, Stephen Gallagher, Sean Jackson, Charlie
Noakes) — a ready-made shortlist if the registry grows.

### FPL Focal → 298, FPL Tom → 5406, FPL Mate → 120
All three self-identify through league names: 298 admins **"YouTube.com/
FPLFocal"** plus Discord/Members/Experts leagues (Holly Shand, Tom Johnson,
Josh Scott and Francisco MW sit in "FPL Focal Experts"); 5406 admins **"FPL
Tom Super League"** with team **"FPL TOM"**; 120 admins **"youtube.com/FPLMate"**
with account name **"Dan FPL Mate"** and team **"FPL Mate on YouTube"**. FPL
Mate was found as the *admin* of a league in FPL Tom's list — the graph walk
again. None of the three publishes a surname; the rows carry the API names
as returned ("Oscar -", "Tom L", "Dan FPL Mate"). Entry 298 has a rank-0
season (2022/23) in `past[]`; stats exclude it.

### Planet FPL — James Linden 1194 (conclusive), Sujan Shah 84298 (high)
Mark Sutherns' list contained 122418 "Planet #FPL Correspondents" (admin
1194). 1194 = **James Linden**, admin of 122414 "Planet #FPL Podcast" and the
Correspondents league; team "Ledley's Kings" (Spurs, as billed). The
Correspondents standings list **84298 "Sujan Shah", team "Suj - Planet FPL"**;
he is in both show leagues and in FPL Harry's league, but admins only "Shah
Boyz", so high. The Correspondents league (40 entries) is Planet FPL's
Patreon-tier roster, not a hosts list — two joke accounts "Suj and James" /
"James And Suj" (4103882, 4104550) sit in it and must not be mistaken for the
hosts.

### Who Got The Assist? — Tom 454 (conclusive), Sam Price 125 (conclusive)
Hub Contributors carried **454 "Tom C (WGTA)"**, who admins 6791 "Who Got The
Assist? Minileague" and 6829 "WGTA Fight Club". Sam was found by scanning
6791's standings page by page for the handle @FPLPricey's surname: page 3,
**125 "Sam Price"**, who admins 26990 **"www.youtube.com/@fplpricey"**. Both
self-identifying. Neither Tom's surname nor an @FPLPricey ↔ "Price" record
beyond his own league name exists publicly; the league names are the
evidence.

### FPL Family → Lee 2913 and Sam 2977 (both conclusive)
Both in Pro Pundits and in Sky's 7-entry "Sky Sports News guests". Lee admins
**"FPL Family"** 6229 and **"FPL Family Patreon"** 6223. Sam admins **"FPL
Extended Family"** 8507 and — because she is Scout's General Manager —
**"FFScout Mods & Cons"** 8449, "FFScout Family" and "Inner Circle"; she is in
the Premier League's "FPL Pod" league 1359 (admin 6212 = "Christian
Harrall-Baker", who admins "@OfficialFPL on X", "@OfficialFPL on Facebook" and
"FPL on WhatsApp" — the PL's social account, not a host). Sam's row therefore
links three shows: FPL Family, FPL Pod, Fantasy Football Scout.

### FPL BlackBox — Luke Williams 71 and Andy North 2325 (both high)
The feed's `itunes:author` is **"Az, Andy, and Luke"**; description: "Az
(@fplblackbox_az), Andy (@FPLMode) and Luke (@fpl_dis)". Episode notes say
"Az is joined by Andy North"; a BlackBox video is titled "Meet Luke" and
search returns "Luke Williams, also known as d1sable" as a BlackBox
recruit. Both are in the 6-entry **BlackBox Contributors** and the Patreon
league. Neither admins a show-named league (Luke: "CID"; Andy: six family/
work leagues), so high. Luke's record is real — 3 top-10k, best 580. Andy has
none in 20 seasons. The remaining Contributors (Zoe Clarke 7909621, Natalie
Chowdhury 4643619) are contributors, not hosts, and got no rows.

### Always Cheating — Josh Landon 88520, Brandon Kelley 279 (both high)
Hub Contributors carried **88520 "Joshua Landon"** (USA). His list included
1099942 "AC Community Team League", whose admin **5120019 is "Always Cheating
Community", team "AlwaysCommunity"** — the show's own account (a brand
account, one league, no row). Its standings include **279 "Brandon Kelley",
"Inter Butternuts"**, Canada, who also admins 679217 "Elks Lodge Classic" with
Josh in it. Exact name matches to the two billed hosts plus membership of the
show's own league; no show-named admin league on either side, so high.

### Above Average FPL — Adam Currier 157 (conclusive), Martin Baker 4470 (high)
Luke Williams' list had 2543 "Above Average FPL" (admin 157). **157 = Adam
Currier**, admin of 2543 and 72643 "AAFPL Invitational"; the co-host is
billed only as "Baker" (@BakerFPL343). Page 2 of the Invitational has **4470
"Martin Baker"**, the only Baker across six pages; he shares six more leagues
with Adam (7421, 58598, 109318, 110140, 311542 "Thought Dump", 1032855) and
sits in both FML FPL leagues. That is a strong co-membership pattern, but
"Baker" could in principle be a superfan's surname; high, with that caveat
written in the row.

### Sky Sports FPL — Mark Briggs 27539 (conclusive), James Savundra 2992401 (high)
The captivate feed bills every episode "James Savundra and Mark Briggs". Lee
Bonfield's list had 5814 "Sky Sports News guests" (admin 27539). **27539 =
Mark Briggs, "Ballabriggs"**, admin of 5790 "Sky Sports News Official FPL",
5800 "Sky Sports News Internal" and 5814. The guests league (7 entries: the
Bonfields, Gianni, Josh Cuthbert, Holly Shand, Briggs) also holds **2992401
"James Savundra", "BazBall 2.0"**, who is in the staff league 5800 too. High.

### Fantasy Football Hub — Rich Clarke 209763, Fergi 2057 (conclusive), Jack Ukiah 6801 (high)
The Hub feed: "The Green Arrow FPL Podcast with Rich Clarke and Fergi … audio
versions of our YouTube content hosted by Jack (@Redditor)". Hub Contributors
carried **2057 "Andrew Ferguson", "Fergi Time"**, admin of 32922 **"The Green
Arrow FPL Podcast"** and the 4-entry 32932 **"Green Arrow Hosts"**; the feed's
`itunes:email` is andrew.ferguson@fantasyfootballhub.co.uk. Green Arrow Hosts
= Ferguson, **209763 "Richard Clarke"** (admin of 772236 "Hub Ultras", 9
top-10k finishes, best 263), Adam Hopcroft 9804 and Scott Harris 3966 (not
billed anywhere; no rows). **6801 "Jack Ukiah", "Redditor's Predators"**,
Mauritius, is in Hub Team and Hub Contributors. Hub Team (37 entries, admin
6774 "Will Thomas", who admins every Hub league) also holds two brand
accounts "Fantasy Football Hub / AI Team" (2245143, 2246265) — not people.

### FPL Fran → 21 (conclusive)
The Hub team-reveal page is paywalled and gives nothing. Found on page 1 of
the AAFPL Invitational: **21 "Francisco MW", team "FPL Fran"**, Spain. Admins
18475 **"YouTube.com/@FPLFran"** and 18497 "FPL Fran Members' League"; member
of 32123 "Youtube.com/@Fplscript" (his FPL Script podcast with JD), Bakar's
"Kings League" and Sertalp's "Analytics League". **Record match: Hub bills
"rank 437 in 2023/24"; `history` best_ever_rank = 437.**

### Fantasy Football Scout — a roster, not a host
Scout's podcast is presented by a rotating cast: the feed names "FPL Harry,
Joe, Neale, Sam, FPL General", Andy North (22 mentions), "FPL Chai standing
in", and a "Ryan (@Ryan_ms28)" segment. Rows written: **Neale Rigg 6225**
(Editor; admins 1774 "Fantasy Football Scout Members"; Coreteam), **FPL Chai
2157** (admins three "FPL CHAI 26/27" leagues; Pro Pundits — billed "former
world no. 2", which is an in-season peak, since his best *final* rank is
1839), Sam Bonfield and Andy North via their rows. **Joe = Joe Lepper**
(meet-the-team: contributor, 6-8 top-10k) — not found in Coreteam, Mods &
Cons, Pro Pundits, Hub Contributors, or three pages of the public Scout
league; null row. Two Joes to NOT confuse him with: 283492 "Joe Collett"
(Mods & Cons) and 1735 "Joshua Scott" (marketing, FPL Graduates). Ryan got no
row: a segment host with only a handle. Harry and General already have rows;
their `shows` lists were left as written.

### FML FPL — Alon Shapiro 28410 (conclusive), Alex Walsh 220415 (high)
Billed only as "Alon and Walsh". Martin Baker's list had 5550 "FML FPL Public
League" and 83218 "FML FPL Prize Mug League" (admin 28410). **28410 = Alon
Shapiro, team "alon"**, USA, admin of both plus 1917942 "livestream merchants
fml fpl" (39 entries). That league's sole Walsh is **220415 "Alex Walsh"**,
USA, who is in all three FML leagues, shares the private 1679400 "AZ vs. LDN"
with Alon, and sits in the AAFPL Invitational. High. (100018 "Guest Jason",
region "Western Sahara", who admins two leagues Alon is in, is a joke account
and was not taken as Walsh.)

### Nulls, and why
- **Ash (FPL JUiCE)**: only "Ash"/"Ashley" in the feed; not in Nick's 10-entry
  Social Club; nothing JUiCE-branded administered by any Ash in four pages of
  the two public leagues.
- **Kelly Somers, Julien Laurens (FPL Pod)**: broadcasters; the PL's own
  show-note ids resolve to strangers (above); not on page 1 of the 1359 "FPL
  Pod" league.
- **Barry Stokes, Alex Rex (All In Football)**: acast feed has 166 / 76 name
  mentions and no links at all; not in any league walked; web search returns
  an NFL player.
- **AllAboutFPL (Srinivasan S, Surya)**: a bylined blog; only an auto-join code
  (2xywgk), which does not resolve without a session.
- **FPL Review**: "developed and run independently by myself"; handle
  formdork@; anonymous by design. Brand row like Solio.
- **Ignore the Template**: feed and Buzzsprout page carry no name; "Link to
  the mini league: Link" with the href stripped.

## Dead ends this pass (so nobody re-walks them)
- `fantasyfootballhub.co.uk/fpl-fran-team-reveal`: paywalled, header only.
- `fmlfpl.com/pod`, `/ourteams`: nav only, plus the recycled league 146684.
- `ignorethetemplate.buzzsprout.com`: no name.
- Show-note `entry/` links in the FPL Pod and Scout feeds (above).
- `leagues-classic/{id}/standings/` for 1359 "FPL Pod" (page 1 of 50+), 544
  "FPL JUiCE League" p1-4, 579 p1-4, 2543 p1-6, 72643 p1-6, 6791 p1-3, 53998
  p1-3: the only hits were the ones written.
- macOS ships bash 3: `declare -A` does not exist, and zsh does not
  word-split `$f` in `for … set -- $f`. Use Python for feed fetching.

## Re-verification addendum
The checklist above still holds. Two additions: (5) treat any `entry/` or
`leagues/` link in a podcast feed as a *candidate*, never as evidence — three
of three sampled this pass were strangers; (6) the cheapest re-derivation for
this pass's rows is the FFScout Pro Pundits league 1426424 plus the admin_entry
of each show-named league listed in the rows.
