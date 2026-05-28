# Virginia International Raceway — Full Course

- **slug**: `vir_full`
- **layout**: Full Course (~3.27 mi / 17 turns)
- **location**: Alton, VA

## Start / Finish

| Name | Latitude | Longitude |
|---|---|---|
| start_finish | 36.56882411463739 | -79.20668876777894 |

## Turns

Turns are listed in driving order. `section` groups turns that are commonly discussed together (named complexes get their own section).

| # | Name | Section | Latitude | Longitude |
|---|---|---|---|---|
| 1 | Turn 1 | front_straight_braking | 36.568487392639994 | -79.20226991566314 |
| 2 | Turn 2 | t1_t2_complex | 36.56790084490779 | -79.20297556637101 |
| 3 | Turn 3 | nascar_bend | 36.56771936019917 | -79.20636531790542 |
| 4 | Turn 4 | t4_t5a | 36.56611576334765 | -79.20576281080075 |
| 5a | Turn 5a | t4_t5a | 36.566176082873 | -79.20506543651899 |
| 5b | Turn 5b | snake | 36.5652583592868 | -79.20423395179846 |
| 6a | Turn 6a | snake | 36.56468100642488 | -79.20444316401122 |
| 6b | Turn 6b | snake | 36.563655548240774 | -79.20465237629575 |
| 7 | Turn 7 | esses | 36.558579760551176 | -79.20469529161036 |
| 8 | Turn 8 | esses | 36.55789032395295 | -79.20474357136834 |
| 9 | Turn 9 | esses | 36.55683892126846 | -79.20469529162375 |
| 10 | Turn 10 | south_bend | 36.55498600468375 | -79.20534438612985 |
| 11 | Turn 11 | oak_tree | 36.552367810667725 | -79.20441694244697 |
| 12 | Turn 12 | oak_tree | 36.55195411619226 | -79.20507140138831 |
| 13 | Turn 13 (marked, not a turn) | back_straight | 36.55934507898279 | -79.21003616472757 |
| 14a | Turn 14a | roller_coaster | 36.56180974016135 | -79.21141482014309 |
| 14b | Turn 14b | roller_coaster | 36.56245605436446 | -79.21197808398603 |
| 15 | Turn 15 | roller_coaster | 36.563072202203685 | -79.21153283732922 |
| 16 | Turn 16 | hog_pen | 36.56483875272622 | -79.21213365215574 |
| 17 | Turn 17 | hog_pen | 36.565291155497235 | -79.2127076448338 |
| 17a | Turn 17a | hog_pen | 36.56634675165921 | -79.21261644973542 |

## Sections (named complexes)

- **snake**: 5b → 6a → 6b
- **esses**: 7 → 8 → 9
- **oak_tree**: 11 → 12
- **roller_coaster**: 14a → 14b → 15
- **hog_pen**: 16 → 17 → 17a

## Sectors (timing splits)

Sectors are timed at turn-apex passages: a sector starts at one anchor's apex passage and ends at the next. The first sector starts at the start/finish line crossing and the last sector ends at the next start/finish crossing — every meter of the lap is accounted for. Distances are computed per lap at analysis time (RaceChrono's `distance_traveled` is normalized by subtracting the lap-start distance).

| # | Name | Start anchor | End anchor |
|---|---|---|---|
| 1 | T1 | start/finish | 2 |
| 2 | NASCAR | 2 | 4 |
| 3 | Snake | 4 | 6a |
| 4 | Esses | 6a | 9 |
| 5 | South Bend | 9 | 11 |
| 6 | Oak Tree | 11 | 12 |
| 7 | Back Straight | 12 | 14a |
| 8 | Roller Coaster | 14a | 15 |
| 9 | Hog Pen | 15 | 17a |
| 10 | Front Straight | 17a | start/finish |

## Notes for analysis

- Turn 13 is a reference point on the back straight, not a true corner; use it as a sector split only.
- Snake and Esses are commitment sections — analyze smoothness of inputs and minimum speed through each rather than discrete brake/throttle events per turn.
- Hog Pen (16-17-17a) leads onto the front straight; exit speed there sets up lap time more than any other corner.
