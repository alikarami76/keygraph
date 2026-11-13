# FK recovery: spider2-lite (97ab6cd)

keygraph 0.3.3, git 97ab6cd, 2026-11-13, 30 databases.
Precision is a lower bound: undeclared but real relations count as extra edges.

## Confidence >= 0.8

| db | declared | hits | inferred | recall | precision (lower bound) |
|---|---|---|---|---|---|
| AdventureWorks | 0 | 0 | 0 | n/a | n/a |
| Airlines | 0 | 0 | 0 | n/a | n/a |
| Baseball | 0 | 0 | 0 | n/a | n/a |
| BowlingLeague | 10 | 6 | 23 | 60.0% | 26.1% |
| Brazilian_E_Commerce | 0 | 0 | 0 | n/a | n/a |
| California_Traffic_Collision | 0 | 0 | 0 | n/a | n/a |
| Db-IMDB | 0 | 0 | 0 | n/a | n/a |
| EU_soccer | 31 | 3 | 3 | 9.7% | 100.0% |
| E_commerce | 0 | 0 | 0 | n/a | n/a |
| EntertainmentAgency | 9 | 9 | 9 | 100.0% | 100.0% |
| IPL | 0 | 0 | 7 | n/a | 0.0% |
| Pagila | 22 | 21 | 25 | 95.5% | 84.0% |
| WWE | 0 | 0 | 5 | n/a | 0.0% |
| bank_sales_trading | 0 | 0 | 0 | n/a | n/a |
| chinook | 11 | 9 | 9 | 81.8% | 100.0% |
| city_legislation | 0 | 0 | 0 | n/a | n/a |
| complex_oracle | 10 | 10 | 12 | 100.0% | 83.3% |
| delivery_center | 0 | 0 | 4 | n/a | 0.0% |
| education_business | 0 | 0 | 4 | n/a | 0.0% |
| electronic_sales | 0 | 0 | 0 | n/a | n/a |
| f1 | 0 | 0 | 45 | n/a | 0.0% |
| imdb_movies | 0 | 0 | 6 | n/a | 0.0% |
| log | 0 | 0 | 0 | n/a | n/a |
| modern_data | 0 | 0 | 0 | n/a | n/a |
| music | 11 | 9 | 9 | 81.8% | 100.0% |
| northwind | 0 | 0 | 0 | n/a | n/a |
| oracle_sql | 35 | 21 | 39 | 60.0% | 53.8% |
| school_scheduling | 17 | 12 | 16 | 70.6% | 75.0% |
| sqlite-sakila | 22 | 21 | 25 | 95.5% | 84.0% |
| stacking | 0 | 0 | 0 | n/a | n/a |
| **total** | 178 | 121 | 241 | 68.0% | 50.2% |

## Confidence >= 0.6

| db | declared | hits | inferred | recall | precision (lower bound) |
|---|---|---|---|---|---|
| AdventureWorks | 0 | 0 | 0 | n/a | n/a |
| Airlines | 0 | 0 | 0 | n/a | n/a |
| Baseball | 0 | 0 | 0 | n/a | n/a |
| BowlingLeague | 10 | 6 | 23 | 60.0% | 26.1% |
| Brazilian_E_Commerce | 0 | 0 | 0 | n/a | n/a |
| California_Traffic_Collision | 0 | 0 | 0 | n/a | n/a |
| Db-IMDB | 0 | 0 | 0 | n/a | n/a |
| EU_soccer | 31 | 6 | 6 | 19.4% | 100.0% |
| E_commerce | 0 | 0 | 0 | n/a | n/a |
| EntertainmentAgency | 9 | 9 | 9 | 100.0% | 100.0% |
| IPL | 0 | 0 | 9 | n/a | 0.0% |
| Pagila | 22 | 22 | 26 | 100.0% | 84.6% |
| WWE | 0 | 0 | 5 | n/a | 0.0% |
| bank_sales_trading | 0 | 0 | 0 | n/a | n/a |
| chinook | 11 | 9 | 9 | 81.8% | 100.0% |
| city_legislation | 0 | 0 | 0 | n/a | n/a |
| complex_oracle | 10 | 10 | 12 | 100.0% | 83.3% |
| delivery_center | 0 | 0 | 6 | n/a | 0.0% |
| education_business | 0 | 0 | 4 | n/a | 0.0% |
| electronic_sales | 0 | 0 | 0 | n/a | n/a |
| f1 | 0 | 0 | 56 | n/a | 0.0% |
| imdb_movies | 0 | 0 | 6 | n/a | 0.0% |
| log | 0 | 0 | 0 | n/a | n/a |
| modern_data | 0 | 0 | 0 | n/a | n/a |
| music | 11 | 9 | 9 | 81.8% | 100.0% |
| northwind | 0 | 0 | 0 | n/a | n/a |
| oracle_sql | 35 | 23 | 41 | 65.7% | 56.1% |
| school_scheduling | 17 | 14 | 18 | 82.3% | 77.8% |
| sqlite-sakila | 22 | 22 | 26 | 100.0% | 84.6% |
| stacking | 0 | 0 | 0 | n/a | n/a |
| **total** | 178 | 130 | 265 | 73.0% | 49.1% |

## Misses and extra edges at 0.6

### BowlingLeague
- miss: bowler_scores.gamenumber <-> match_games.gamenumber
- miss: bowler_scores.matchid <-> match_games.matchid
- miss: bowler_scores_archive.gamenumber <-> match_games_archive.gamenumber
- miss: bowler_scores_archive.matchid <-> match_games_archive.matchid
- extra: bowler_scores.matchid <-> tourney_matches.matchid (0.9) column name 'MatchID' equals the primary key name of main.Tourney_Matches
- extra: bowler_scores.matchid <-> tourney_matches_archive.matchid (0.9) column name 'MatchID' equals the primary key name of main.Tourney_Matches_Archive
- extra: bowler_scores_archive.bowlerid <-> bowlers.bowlerid (0.95) column name 'BowlerID' matches table name
- extra: bowler_scores_archive.matchid <-> tourney_matches.matchid (0.9) column name 'MatchID' equals the primary key name of main.Tourney_Matches
- extra: bowler_scores_archive.matchid <-> tourney_matches_archive.matchid (0.9) column name 'MatchID' equals the primary key name of main.Tourney_Matches_Archive
- extra: match_games.matchid <-> tourney_matches.matchid (0.9) column name 'MatchID' equals the primary key name of main.Tourney_Matches
- extra: match_games.matchid <-> tourney_matches_archive.matchid (0.9) column name 'MatchID' equals the primary key name of main.Tourney_Matches_Archive
- extra: match_games.winningteamid <-> teams.teamid (0.8) role prefix 'winning' stripped, 'team' matches table name
- extra: match_games_archive.matchid <-> tourney_matches.matchid (0.9) column name 'MatchID' equals the primary key name of main.Tourney_Matches
- extra: match_games_archive.matchid <-> tourney_matches_archive.matchid (0.9) column name 'MatchID' equals the primary key name of main.Tourney_Matches_Archive
- extra: match_games_archive.winningteamid <-> teams.teamid (0.8) role prefix 'winning' stripped, 'team' matches table name
- extra: teams.teamid <-> tourney_matches_archive.evenlaneteamid (0.8) role prefix 'even_lane' stripped, 'team' matches table name
- extra: teams.teamid <-> tourney_matches_archive.oddlaneteamid (0.8) role prefix 'odd_lane' stripped, 'team' matches table name
- extra: tournaments.tourneyid <-> tournaments_archive.tourneyid (0.95) column name 'TourneyID' equals the primary key name of main.Tournaments_Archive
- extra: tournaments.tourneyid <-> tourney_matches_archive.tourneyid (0.9) column name 'TourneyID' equals the primary key name of main.Tournaments
- extra: tournaments_archive.tourneyid <-> tourney_matches.tourneyid (0.9) column name 'TourneyID' equals the primary key name of main.Tournaments_Archive
- extra: tourney_matches.matchid <-> tourney_matches_archive.matchid (0.95) column name 'MatchID' equals the primary key name of main.Tourney_Matches_Archive

### EU_soccer
- miss: match.away_player_1 <-> player.player_api_id
- miss: match.away_player_10 <-> player.player_api_id
- miss: match.away_player_11 <-> player.player_api_id
- miss: match.away_player_2 <-> player.player_api_id
- miss: match.away_player_3 <-> player.player_api_id
- miss: match.away_player_4 <-> player.player_api_id
- miss: match.away_player_5 <-> player.player_api_id
- miss: match.away_player_6 <-> player.player_api_id
- miss: match.away_player_7 <-> player.player_api_id
- miss: match.away_player_8 <-> player.player_api_id
- miss: match.away_player_9 <-> player.player_api_id
- miss: match.away_team_api_id <-> team.team_api_id
- miss: match.home_player_1 <-> player.player_api_id
- miss: match.home_player_10 <-> player.player_api_id
- miss: match.home_player_11 <-> player.player_api_id
- miss: match.home_player_2 <-> player.player_api_id
- miss: match.home_player_3 <-> player.player_api_id
- miss: match.home_player_4 <-> player.player_api_id
- miss: match.home_player_5 <-> player.player_api_id
- miss: match.home_player_6 <-> player.player_api_id
- miss: match.home_player_7 <-> player.player_api_id
- miss: match.home_player_8 <-> player.player_api_id
- miss: match.home_player_9 <-> player.player_api_id
- miss: match.home_team_api_id <-> team.team_api_id
- miss: team.team_fifa_api_id <-> team_attributes.team_fifa_api_id

### IPL
- extra: ball_by_ball.match_id <-> match.match_id (0.95) column name 'match_id' matches table name
- extra: batsman_scored.match_id <-> match.match_id (0.95) column name 'match_id' matches table name
- extra: extra_runs.match_id <-> match.match_id (0.95) column name 'match_id' matches table name
- extra: match.match_id <-> player_match.match_id (0.95) column name 'match_id' matches table name
- extra: match.match_id <-> wicket_taken.match_id (0.95) column name 'match_id' matches table name
- extra: match.team_1 <-> team.team_id (0.75) numbered role column 'team_1' reads as a reference to 'team'
- extra: match.team_2 <-> team.team_id (0.75) numbered role column 'team_2' reads as a reference to 'team'
- extra: player.player_id <-> player_match.player_id (0.95) column name 'player_id' matches table name
- extra: player_match.team_id <-> team.team_id (0.95) column name 'team_id' matches table name

### Pagila
- extra: film.film_id <-> film_text.film_id (0.95) column name 'film_id' equals the primary key name of main.film_text
- extra: film_actor.film_id <-> film_text.film_id (0.9) column name 'film_id' equals the primary key name of main.film_text
- extra: film_category.film_id <-> film_text.film_id (0.9) column name 'film_id' equals the primary key name of main.film_text
- extra: film_text.film_id <-> inventory.film_id (0.9) column name 'film_id' equals the primary key name of main.film_text

### WWE
- extra: cards.event_id <-> events.id (0.95) column name 'event_id' matches table name
- extra: cards.id <-> matches.card_id (0.95) column name 'card_id' matches table name
- extra: cards.location_id <-> locations.id (0.95) column name 'location_id' matches table name
- extra: cards.promotion_id <-> promotions.id (0.95) column name 'promotion_id' matches table name
- extra: cards.table_id <-> tables.id (0.95) column name 'table_id' matches table name

### chinook
- miss: customers.supportrepid <-> employees.employeeid
- miss: employees.employeeid <-> employees.reportsto

### complex_oracle
- extra: customers.cust_id <-> supplementary_demographics.cust_id (0.95) column name 'cust_id' equals the primary key name of main.supplementary_demographics
- extra: sales.cust_id <-> supplementary_demographics.cust_id (0.83) column name 'cust_id' equals the primary key name of main.supplementary_demographics

### delivery_center
- extra: channels.channel_id <-> orders.channel_id (0.95) column name 'channel_id' matches table name
- extra: deliveries.delivery_order_id <-> orders.order_id (0.73) role prefix 'delivery' stripped, 'order' matches table name
- extra: deliveries.driver_id <-> drivers.driver_id (0.95) column name 'driver_id' matches table name
- extra: hubs.hub_id <-> stores.hub_id (0.95) column name 'hub_id' matches table name
- extra: orders.order_id <-> payments.payment_order_id (0.73) role prefix 'payment' stripped, 'order' matches table name
- extra: orders.store_id <-> stores.store_id (0.95) column name 'store_id' matches table name

### education_business
- extra: web_accounts.id <-> web_events.account_id (0.81) column name 'account_id' matches table name (prefix 'web_' stripped)
- extra: web_accounts.id <-> web_orders.account_id (0.81) column name 'account_id' matches table name (prefix 'web_' stripped)
- extra: web_accounts.sales_rep_id <-> web_sales_reps.id (0.81) column name 'sales_rep_id' matches table name (prefix 'web_' stripped)
- extra: web_region.id <-> web_sales_reps.region_id (0.81) column name 'region_id' matches table name (prefix 'web_' stripped)

### f1
- extra: circuits.circuit_id <-> circuits_ext.circuit_id (0.95) column name 'circuit_id' matches table name
- extra: circuits.circuit_id <-> races.circuit_id (0.95) column name 'circuit_id' matches table name
- extra: circuits.circuit_id <-> races_ext.circuit_id (0.95) column name 'circuit_id' matches table name
- extra: constructor_results.constructor_id <-> constructors.constructor_id (0.95) column name 'constructor_id' matches table name
- extra: constructor_results.constructor_results_id <-> results.result_id (0.8) role prefix 'constructor' stripped, 'results' matches table name
- extra: constructor_results.race_id <-> races.race_id (0.95) column name 'race_id' matches table name
- extra: constructor_standings.constructor_id <-> constructors.constructor_id (0.95) column name 'constructor_id' matches table name
- extra: constructor_standings.race_id <-> races.race_id (0.95) column name 'race_id' matches table name
- extra: constructors.constructor_id <-> constructors_ext.constructor_id (0.95) column name 'constructor_id' matches table name
- extra: constructors.constructor_id <-> drives.constructor_id (0.95) column name 'constructor_id' matches table name
- extra: constructors.constructor_id <-> qualifying.constructor_id (0.95) column name 'constructor_id' matches table name
- extra: constructors.constructor_id <-> results.constructor_id (0.95) column name 'constructor_id' matches table name
- extra: constructors.constructor_id <-> sprint_results.constructor_id (0.95) column name 'constructor_id' matches table name
- extra: constructors.constructor_id <-> team_driver_ranks.constructor_id (0.95) column name 'constructor_id' matches table name
- extra: constructors.constructor_ref <-> short_constructor_names.constructor_ref (0.75) column name 'constructor_ref' equals the key column of main.short_constructor_names (rule: shared key name)
- extra: constructors_ext.constructor_ref <-> short_constructor_names.constructor_ref (0.75) column name 'constructor_ref' equals the key column of main.short_constructor_names (rule: shared key name)
- extra: driver_standings.driver_id <-> drivers.driver_id (0.95) column name 'driver_id' matches table name
- extra: driver_standings.driver_standings_id <-> driver_standings_ext.driver_standings_id (0.95) column name 'driver_standings_id' matches table name
- extra: driver_standings.race_id <-> races.race_id (0.95) column name 'race_id' matches table name
- extra: driver_standings_ext.driver_id <-> drivers.driver_id (0.95) column name 'driver_id' matches table name
- extra: driver_standings_ext.race_id <-> races.race_id (0.95) column name 'race_id' matches table name
- extra: drivers.driver_id <-> drivers_ext.driver_id (0.95) column name 'driver_id' matches table name
- extra: drivers.driver_id <-> drives.driver_id (0.95) column name 'driver_id' matches table name
- extra: drivers.driver_id <-> lap_positions.driver_id (0.95) column name 'driver_id' matches table name
- extra: drivers.driver_id <-> lap_time_stats.driver_id (0.95) column name 'driver_id' matches table name
- extra: drivers.driver_id <-> lap_times.driver_id (0.95) column name 'driver_id' matches table name
- extra: drivers.driver_id <-> lap_times_ext.driver_id (0.95) column name 'driver_id' matches table name
- extra: drivers.driver_id <-> pit_stops.driver_id (0.95) column name 'driver_id' matches table name
- extra: drivers.driver_id <-> qualifying.driver_id (0.95) column name 'driver_id' matches table name
- extra: drivers.driver_id <-> results.driver_id (0.95) column name 'driver_id' matches table name
- extra: drivers.driver_id <-> retirements.driver_id (0.95) column name 'driver_id' matches table name
- extra: drivers.driver_id <-> sprint_results.driver_id (0.95) column name 'driver_id' matches table name
- extra: drivers.driver_id <-> team_driver_ranks.driver_id (0.95) column name 'driver_id' matches table name
- extra: drivers_ext.full_name <-> short_grand_prix_names.full_name (0.75) column name 'full_name' equals the key column of main.short_grand_prix_names (rule: shared key name)
- extra: drives.year <-> seasons.year (0.75) column name 'year' equals the key column of main.seasons (rule: shared key name)
- extra: lap_positions.race_id <-> races.race_id (0.95) column name 'race_id' matches table name
- extra: lap_time_stats.race_id <-> races.race_id (0.95) column name 'race_id' matches table name
- extra: lap_times.race_id <-> races.race_id (0.95) column name 'race_id' matches table name
- extra: lap_times_ext.race_id <-> races.race_id (0.95) column name 'race_id' matches table name
- extra: liveries.constructor_ref <-> short_constructor_names.constructor_ref (0.75) column name 'constructor_ref' equals the key column of main.short_constructor_names (rule: shared key name)
- extra: pit_stops.race_id <-> races.race_id (0.95) column name 'race_id' matches table name
- extra: qualifying.race_id <-> races.race_id (0.95) column name 'race_id' matches table name
- extra: races.race_id <-> races_ext.race_id (0.95) column name 'race_id' matches table name
- extra: races.race_id <-> results.race_id (0.95) column name 'race_id' matches table name
- extra: races.race_id <-> retirements.race_id (0.95) column name 'race_id' matches table name
- extra: races.race_id <-> sprint_results.race_id (0.95) column name 'race_id' matches table name
- extra: races.year <-> seasons.year (0.75) column name 'year' equals the key column of main.seasons (rule: shared key name)
- extra: races_ext.year <-> seasons.year (0.75) column name 'year' equals the key column of main.seasons (rule: shared key name)
- extra: results.result_id <-> sprint_results.result_id (0.95) column name 'result_id' equals the primary key name of main.sprint_results
- extra: results.status_id <-> status.status_id (0.95) column name 'status_id' matches table name
- extra: retirements.status_id <-> status.status_id (0.95) column name 'status_id' matches table name
- extra: seasons.year <-> tdr_overrides.year (0.75) column name 'year' equals the key column of main.seasons (rule: shared key name)
- extra: seasons.year <-> team_driver_ranks.year (0.75) column name 'year' equals the key column of main.seasons (rule: shared key name)
- extra: short_constructor_names.constructor_ref <-> tdr_overrides.constructor_ref (0.75) column name 'constructor_ref' equals the key column of main.short_constructor_names (rule: shared key name)
- extra: short_constructor_names.constructor_ref <-> team_driver_ranks.constructor_ref (0.75) column name 'constructor_ref' equals the key column of main.short_constructor_names (rule: shared key name)
- extra: sprint_results.status_id <-> status.status_id (0.95) column name 'status_id' matches table name

### imdb_movies
- extra: director_mapping.movie_id <-> movies.id (0.85) column name 'movie_id' matches table name
- extra: director_mapping.name_id <-> names.id (0.85) column name 'name_id' matches table name
- extra: genre.movie_id <-> movies.id (0.85) column name 'movie_id' matches table name
- extra: movies.id <-> ratings.movie_id (0.85) column name 'movie_id' matches table name
- extra: movies.id <-> role_mapping.movie_id (0.85) column name 'movie_id' matches table name
- extra: names.id <-> role_mapping.name_id (0.85) column name 'name_id' matches table name

### music
- miss: customer.supportrepid <-> employee.employeeid
- miss: employee.employeeid <-> employee.reportsto

### oracle_sql
- miss: emp_hire_periods.emp_id <-> employees.id
- miss: employees.id <-> employees.supervisor_id
- miss: employees.id <-> picking_list.picker_emp_id
- miss: packaging.id <-> packaging_relations.contains_id
- miss: picking_line.line_no <-> picking_log.pickline_no
- miss: picking_line.picklist_id <-> picking_list.id
- miss: picking_line.picklist_id <-> picking_log.picklist_id
- miss: picking_list.id <-> picking_log.picklist_id
- miss: web_counter_hist.app_id <-> web_pages.app_id
- miss: web_counter_hist.page_no <-> web_pages.page_no
- miss: web_page_visits.app_id <-> web_pages.app_id
- miss: web_page_visits.page_no <-> web_pages.page_no
- extra: customer_favorites.customer_id <-> customer_reviews.customer_id (0.9) column name 'customer_id' equals the primary key name of main.customer_reviews
- extra: customer_favorites.customer_id <-> orders.customer_id (0.9) column name 'customer_id' equals the primary key name of main.customer_favorites
- extra: customer_reviews.customer_id <-> orders.customer_id (0.9) column name 'customer_id' equals the primary key name of main.customer_reviews
- extra: inventory.product_id <-> product_alcohol.product_id (0.9) column name 'product_id' equals the primary key name of main.product_alcohol
- extra: inventory.product_id <-> product_minimums.product_id (0.9) column name 'product_id' equals the primary key name of main.product_minimums
- extra: monthly_budget.product_id <-> product_alcohol.product_id (0.9) column name 'product_id' equals the primary key name of main.product_alcohol
- extra: monthly_budget.product_id <-> product_minimums.product_id (0.9) column name 'product_id' equals the primary key name of main.product_minimums
- extra: monthly_sales.product_id <-> product_alcohol.product_id (0.9) column name 'product_id' equals the primary key name of main.product_alcohol
- extra: monthly_sales.product_id <-> product_minimums.product_id (0.9) column name 'product_id' equals the primary key name of main.product_minimums
- extra: orderlines.product_id <-> product_alcohol.product_id (0.83) column name 'product_id' equals the primary key name of main.product_alcohol
- extra: orderlines.product_id <-> product_minimums.product_id (0.83) column name 'product_id' equals the primary key name of main.product_minimums
- extra: picking_line.product_id <-> product_alcohol.product_id (0.9) column name 'product_id' equals the primary key name of main.product_alcohol
- extra: picking_line.product_id <-> product_minimums.product_id (0.9) column name 'product_id' equals the primary key name of main.product_minimums
- extra: product_alcohol.product_id <-> product_minimums.product_id (0.9) column name 'product_id' equals the primary key name of main.product_minimums
- extra: product_alcohol.product_id <-> purchases.product_id (0.9) column name 'product_id' equals the primary key name of main.product_alcohol
- extra: product_minimums.product_id <-> purchases.product_id (0.9) column name 'product_id' equals the primary key name of main.product_minimums
- extra: web_apps.id <-> web_counter_hist.app_id (0.95) column name 'app_id' matches table name (prefix 'web_' stripped)
- extra: web_apps.id <-> web_page_visits.app_id (0.95) column name 'app_id' matches table name (prefix 'web_' stripped)

### school_scheduling
- miss: departments.deptchair <-> staff.staffid
- miss: majors.majorid <-> students.studmajor
- miss: subjects.subjectcode <-> subjects.subjectprereq
- extra: categories.departmentid <-> departments.departmentid (0.95) column name 'DepartmentID' matches table name
- extra: faculty.staffid <-> faculty_classes.staffid (0.9) column name 'StaffID' equals the primary key name of main.Faculty
- extra: faculty_categories.staffid <-> staff.staffid (0.92) column name 'StaffID' matches table name
- extra: faculty_subjects.staffid <-> staff.staffid (0.92) column name 'StaffID' matches table name

### sqlite-sakila
- extra: film.film_id <-> film_text.film_id (0.95) column name 'film_id' equals the primary key name of main.film_text
- extra: film_actor.film_id <-> film_text.film_id (0.9) column name 'film_id' equals the primary key name of main.film_text
- extra: film_category.film_id <-> film_text.film_id (0.9) column name 'film_id' equals the primary key name of main.film_text
- extra: film_text.film_id <-> inventory.film_id (0.9) column name 'film_id' equals the primary key name of main.film_text
