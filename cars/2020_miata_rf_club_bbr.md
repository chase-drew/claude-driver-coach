# 2020 Mazda Miata RF — Club, BBR

- **slug**: `2020_miata_rf_club_bbr`
- **year**: 2020
- **make**: Mazda
- **model**: MX-5 Miata RF
- **trim**: Club
- **tune**: BBR

## Modifications

| Category | Item |
|---|---|
| Suspension | aFe 32mm front sway bar |
| Suspension | No rear sway bar |
| Suspension | Progress lowering springs |
| Brakes | Carbotech XP12 front brake pads |
| Brakes | Carbotech XP10 rear brake pads |
| Wheels | 17x9 |

## Tires

Tires change frequently and are tracked **per session**, not on the car. The import flow prompts for the current tire compound, size, and approximate age/heat-cycle count each time data is imported.

## Drivetrain — gear ratios

The car has no CAN gear-position sensor, so we infer gear from `mph_per_1000_rpm = (mph / RPM) × 1000`. Each gear sits in a narrow band around its characteristic ratio; samples in transition (mid-shift, clutch in, coasting) fall between bands. The values below are **nominal** — measured against the published Mazda 6MT spec on this car's stock-diameter wheel/tire — and used as a starting point for per-session calibration (see note below).

| Gear | mph_per_1000_rpm |
|---|---|
| 1 | 4.95 |
| 2 | 8.45 |
| 3 | 12.15 |
| 4 | 15.65 |
| 5 | 19.55 |
| 6 | 25.18 |

Boundaries between gears are the geometric mean of adjacent ratios. These nominal boundaries: 1↔2: 6.47   2↔3: 10.13   3↔4: 13.79   4↔5: 17.49   5↔6: 22.18.

**Per-session calibration.** Effective rolling diameter varies with tire compound, tire pressure (cold vs hot), wear, and even ambient temperature, so the same gear can read ±3–5% different mph_per_1000_rpm across sessions. The pipeline auto-calibrates each session: for every nominal gear it finds samples within ±15% of that ratio in the actual session data and takes the median as the refined value. Gears with too few samples (e.g. 1st/2nd on a flying-lap-only session) fall back to nominal. The refined ratios are used for gear inference and for the heel-toe gear-drop confirmation; the nominal values above remain the documented reference.

## Notes for analysis

- No rear sway bar + stiff front bar biases the car toward understeer-on-throttle / rotation-on-trail-brake. Look for trail-brake usage on corner entry as a technique marker.
- XP12 front / XP10 rear pad split favors strong initial bite up front; expect aggressive forward weight transfer on threshold braking.
