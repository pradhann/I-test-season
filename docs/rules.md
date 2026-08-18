# Verified FPL rules

**Generated file — do not edit by hand.**
Source of truth is `fpl_edge/rules/registry.yaml`;
regenerate with `make rules-doc`.

Season: **2026-27**

## Sources

| Key | URL | Fetched (UTC) |
| --- | --- | --- |
| `API` | https://fantasy.premierleague.com/api/bootstrap-static/ | 2026-08-18T22:42:47Z |
| `RULES` | https://fantasy.premierleague.com/help/rules | 2026-08-18T22:43:00Z |

**API note.** Machine-readable game_config.scoring / game_config.rules / chips / element_types.

**RULES note.** Rendered via headless browser; the page is a JS SPA and returns an empty shell to plain HTTP fetches. IMPORTANT: the deadline table on this page is rendered in BROWSER-LOCAL time, not UK time. Observed "GW1 Fri 21 Aug 10:30" against the API's 2026-08-21T17:30:00Z (US/Pacific offset). Never parse deadlines from this page -- use the API's UTC values.

## Verification status

64 of 65 rules verified against an authoritative source.

The following rules are **UNVERIFIED**. Code reading them raises
`UnverifiedRuleError` rather than guessing.

| Rule | Why it matters |
| --- | --- |
| `prices.in_season_change_time_utc` | UNVERIFIED: commonly ~01:30 UTC daily but not stated by any authoritative source we read. Price model must not assume this. |

## Rules

| Rule | Value | Sources | Note |
| --- | --- | --- | --- |
| `autosubs.gk_rule` | `GK only replaced by the bench GK` | RULES |  |
| `autosubs.outfield_rule` | `highest-priority bench outfielder who played and does not break formation rules` | RULES |  |
| `autosubs.played_definition` | `appearance on pitch OR receiving a yellow/red card` | RULES |  |
| `autosubs.processed` | `end of gameweek` | RULES |  |
| `bps.table` | `{"play_1_to_60": 3, "play_over_60": 6, "goal_from_penalty": 12, "goal_gkp_def_nonpen": 12, "goal_mid_nonpen": 18, "goal_fwd_nonpen": 24, "assist": 9, "clean_sheet_gkp_def": 12, "save": 2, "save_from_shot_inside_box": 1, "save_from_big_chance": 1, "penalty_save": 7, "per_3_clearances_blocks_interceptions": 1, "per_3_recoveries": 1, "chance_created": 1, "big_chance_created": 3, "successful_open_play_cross": 1, "successful_tackle": 2, "successful_dribble": 1, "match_winning_goal": 3, "goalline_clearance": 9, "foul_won": 1, "shot_on_target": 2, "pass_completion_70_79_min30": 2, "pass_completion_80_89_min30": 4, "pass_completion_90_plus_min30": 6, "goal_conceded_gkp_def": -4, "penalty_conceded": -3, "penalty_missed": -6, "yellow_card": -3, "red_card": -9, "own_goal": -6, "big_chance_missed": -3, "error_leading_to_goal": -3, "error_leading_to_attempt": -1, "foul_conceded": -1, "caught_offside": -1, "shot_off_target": -1}` | RULES |  |
| `bps.tie_rules` | `tie for 1st -> both get 3, third gets 1. tie for 2nd -> 3,2,2. tie for 3rd -> 3,2,1,1.` | RULES |  |
| `captaincy.captain_multiplier` | `2` | RULES |  |
| `captaincy.triple_captain_multiplier` | `3` | RULES |  |
| `captaincy.vice_enabled` | `True` | API, RULES | sys_vice_captain_enabled |
| `captaincy.vice_takes_over_rule` | `captain plays 0 minutes -> vice captaincy applies; if both 0 min, no player doubled` | RULES |  |
| `chips.available` | `["wildcard", "freehit", "bboost", "3xc"]` | API, RULES |  |
| `chips.bboost_3xc_cancellable` | `True` | RULES | cancellable before deadline |
| `chips.count_each` | `2` | API, RULES | one per half of season |
| `chips.freehit_not_consecutive` | `True` | RULES | if FH played GW19, second FH not active until GW21 |
| `chips.one_per_gw` | `True` | RULES |  |
| `chips.wildcard_freehit_cancellable` | `False` | RULES | cannot be cancelled once confirmed |
| `chips.windows` | `{"wildcard": [[2, 19], [20, 38]], "freehit": [[2, 19], [20, 38]], "bboost": [[1, 19], [20, 38]], "3xc": [[1, 19], [20, 38]]}` | API, RULES | Inclusive [start_event, stop_event]. CRITICAL: Wildcard and Free Hit are NOT available in GW1 ("available after the first Gameweek of your season"), but Bench Boost and Triple Captain ARE. Second half unlocks after the GW19 deadline. |
| `deadlines.authoritative_source` | `API events[].deadline_time (UTC)` | API | NEVER parse the rules page deadline table -- it renders in browser-local time |
| `deadlines.frozen_within_hours` | `24` | RULES | a deadline will not change within 24h of scheduled time |
| `deadlines.offset_before_first_kickoff_minutes` | `90` | RULES |  |
| `deadlines.points_final_at` | `09:00 UK the day after the final match of the Gameweek` | RULES |  |
| `defensive_contribution.def_actions` | `["clearances", "blocked_shots", "interceptions", "tackles"]` | RULES |  |
| `defensive_contribution.def_threshold` | `10` | RULES | DEF: clearances + blocks + interceptions + tackles (CBIT). Recoveries NOT counted. |
| `defensive_contribution.mid_fwd_actions` | `["clearances", "blocked_shots", "interceptions", "tackles", "recoveries"]` | RULES |  |
| `defensive_contribution.mid_fwd_threshold` | `12` | RULES | MID/FWD: CBIT + recoveries. |
| `defensive_contribution.points` | `{"GKP": 0, "DEF": 2, "MID": 2, "FWD": 2}` | API, RULES | Forwards are eligible and were already eligible in 2025-26 -- replaying the scoring map over 113,260 archived stat lines reproduces total_points with zero mismatches, including the 9 forward rows that cleared the threshold. Forward DC is nonetheless negligible in practice: 9 qualifying rows in 3,278 forward appearances (0.27%) in 2025-26, versus 429/9,733 for defenders (4.4%) and 587/13,309 for midfielders (4.4%). |
| `defensive_contribution.stacks` | `False` | RULES | does NOT stack; 20 CBI still scores 2 not 4 |
| `league_phases` | `{"Overall": [1, 38], "August": [1, 2], "September": [3, 5], "October": [6, 9], "November": [10, 12], "December": [13, 18], "January": [19, 23], "February": [24, 27], "March": [28, 30], "April": [31, 33], "May": [34, 38]}` | RULES |  |
| `misc.classic_tiebreak` | `fewest transfers made (WC/FH transfers excluded)` | RULES |  |
| `misc.manager_scoring_removed` | `True` | API, RULES | All mng_* scoring weights are 0 in game_config.scoring and element_types contains only GKP/DEF/MID/FWD. The "Manager" element type (element_type 5) existed ONLY in 2024-25 -- verified by counting raw vaastav players_raw.csv: 2022-23=0, 2023-24=0, 2024-25=20, 2025-26=0. It does not exist in 2026-27. Any historical backtest over 2024-25 MUST strip manager elements or it will score points that cannot be earned this season. |
| `misc.total_players_at_fetch` | `5896644` | API | field is still growing pre-GW1; do not treat as final field size |
| `prices.change_deadlines_preseason` | `["2026-08-18T23:00:00Z", "2026-08-19T23:00:00Z", "2026-08-20T23:00:00Z"]` | API | game_config.settings.price_change_deadlines |
| `prices.in_season_change_time_utc` ⚠️ UNVERIFIED | `None` | — | UNVERIFIED: commonly ~01:30 UTC daily but not stated by any authoritative source we read. Price model must not assume this. |
| `prices.no_change_before_season` | `True` | RULES | prices do not change until the season starts |
| `prices.sell_at_purchase_price` | `False` | API | element_sell_at_purchase_price=false |
| `prices.sell_on_fee_fraction` | `0.5` | API, RULES | transfers_sell_on_fee=0.5; keep half the RISE |
| `prices.sell_rounding` | `floor_to_0.1m` | RULES | 7.5m bought, 7.8m now -> sells at 7.6m |
| `scoring.assist` | `3` | API, RULES |  |
| `scoring.bonus` | `[3, 2, 1]` | API, RULES | top-3 BPS in match |
| `scoring.clean_sheet` | `{"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}` | API, RULES |  |
| `scoring.goal` | `{"GKP": 10, "DEF": 6, "MID": 5, "FWD": 4}` | API, RULES |  |
| `scoring.goals_conceded` | `{"GKP": -1, "DEF": -1, "MID": 0, "FWD": 0}` | API | per 2 conceded |
| `scoring.goals_conceded_per_penalty` | `2` | RULES | -1 per 2 conceded, GKP/DEF only |
| `scoring.minutes_long` | `2` | API, RULES | 60+ min EXCLUDING stoppage time |
| `scoring.minutes_short` | `1` | API, RULES | playing 1-59 min ('up to 60') |
| `scoring.own_goal` | `-2` | API, RULES |  |
| `scoring.penalty_miss` | `-2` | API, RULES |  |
| `scoring.penalty_save` | `5` | API, RULES |  |
| `scoring.red_card` | `-3` | API, RULES | includes any yellow deduction; keeps conceding penalty after red |
| `scoring.saves_per_point` | `3` | RULES | 1 pt per 3 saves; API exposes saves:1 as per-unit weight |
| `scoring.yellow_card` | `-1` | API, RULES |  |
| `squad.budget_tenths` | `1000` | API, RULES | squad_total_spend, i.e. GBP 100.0m in 0.1m units |
| `squad.formation_rule` | `1 GKP, >=3 DEF, >=1 FWD at all times` | RULES |  |
| `squad.max_per_club` | `3` | API, RULES | squad_team_limit |
| `squad.max_play_by_position` | `{"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}` | API |  |
| `squad.min_play_by_position` | `{"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}` | API |  |
| `squad.select_by_position` | `{"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}` | API, RULES |  |
| `squad.size` | `15` | API, RULES | game_config.rules.squad_squadsize |
| `squad.starting_xi` | `11` | API, RULES | squad_squadplay |
| `transfers.cap_per_gw` | `20` | API, RULES | transfers_cap; does not apply under Wildcard/Free Hit |
| `transfers.chips_retain_banked_ft` | `True` | RULES | WC/FH: saved FTs are retained for the following GW |
| `transfers.free_per_gw` | `1` | RULES |  |
| `transfers.hit_cost` | `-4` | RULES | per extra transfer, deducted at start of next GW |
| `transfers.max_banked` | `5` | API, RULES | API max_extra_free_transfers=4, i.e. 1+4=5 |
| `transfers.unlimited_before_first_deadline` | `True` | RULES |  |
