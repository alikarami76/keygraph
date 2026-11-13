# FK recovery: bird mini-dev (97ab6cd)

keygraph 0.3.3, git 97ab6cd, 2026-11-13, 11 databases.
Precision is a lower bound: undeclared but real relations count as extra edges.

## Confidence >= 0.8

| db | declared | hits | inferred | recall | precision (lower bound) |
|---|---|---|---|---|---|
| california_schools | 2 | 0 | 0 | 0.0% | n/a |
| card_games | 4 | 0 | 0 | 0.0% | n/a |
| codebase_community | 13 | 11 | 11 | 84.6% | 100.0% |
| debit_card_specializing | 1 | 1 | 4 | 100.0% | 25.0% |
| european_football_2 | 31 | 3 | 3 | 9.7% | 100.0% |
| financial | 8 | 8 | 8 | 100.0% | 100.0% |
| formula_1 | 19 | 18 | 19 | 94.7% | 94.7% |
| student_club | 8 | 7 | 7 | 87.5% | 100.0% |
| superhero | 11 | 5 | 5 | 45.5% | 100.0% |
| thrombosis_prediction | 2 | 0 | 0 | 0.0% | n/a |
| toxicology | 5 | 4 | 4 | 80.0% | 100.0% |
| **total** | 104 | 57 | 61 | 54.8% | 93.4% |

## Confidence >= 0.6

| db | declared | hits | inferred | recall | precision (lower bound) |
|---|---|---|---|---|---|
| california_schools | 2 | 0 | 0 | 0.0% | n/a |
| card_games | 4 | 0 | 0 | 0.0% | n/a |
| codebase_community | 13 | 13 | 14 | 100.0% | 92.9% |
| debit_card_specializing | 1 | 1 | 4 | 100.0% | 25.0% |
| european_football_2 | 31 | 6 | 6 | 19.4% | 100.0% |
| financial | 8 | 8 | 8 | 100.0% | 100.0% |
| formula_1 | 19 | 19 | 20 | 100.0% | 95.0% |
| student_club | 8 | 7 | 7 | 87.5% | 100.0% |
| superhero | 11 | 8 | 8 | 72.7% | 100.0% |
| thrombosis_prediction | 2 | 1 | 1 | 50.0% | 100.0% |
| toxicology | 5 | 5 | 5 | 100.0% | 100.0% |
| **total** | 104 | 68 | 73 | 65.4% | 93.2% |

## Misses and extra edges at 0.6

### california_schools
- miss: frpm.cdscode <-> schools.cdscode
- miss: satscores.cds <-> schools.cdscode

### card_games
- miss: cards.uuid <-> foreign_data.uuid
- miss: cards.uuid <-> legalities.uuid
- miss: cards.uuid <-> rulings.uuid
- miss: set_translations.setcode <-> sets.code

### codebase_community
- extra: posts.id <-> tags.wikipostid (0.73) role prefix 'wiki' stripped, 'post' matches table name

### debit_card_specializing
- extra: customers.customerid <-> transactions_1k.customerid (0.95) column name 'CustomerID' matches table name
- extra: gasstations.gasstationid <-> transactions_1k.gasstationid (0.93) column name 'GasStationID' equals the primary key name of main.gasstations
- extra: products.productid <-> transactions_1k.productid (0.95) column name 'ProductID' matches table name

### european_football_2
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

### formula_1
- extra: constructorresults.constructorresultsid <-> results.resultid (0.8) role prefix 'constructor' stripped, 'results' matches table name

### student_club
- miss: member.zip <-> zip_code.zip_code

### superhero
- miss: hero_attribute.hero_id <-> superhero.id
- miss: hero_power.hero_id <-> superhero.id
- miss: hero_power.power_id <-> superpower.id

### thrombosis_prediction
- miss: laboratory.id <-> patient.id
