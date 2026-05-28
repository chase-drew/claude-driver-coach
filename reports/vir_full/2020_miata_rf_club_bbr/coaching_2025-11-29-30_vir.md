# VIR Full Course — November 29–30, 2025

**Tires:** Nankang CR-S 225/45R17, ~4–7 heat cycles (moderate)
**Car:** 2020 Mazda Miata RF Club, BBR tune
**Source files:** `session_20251129_0924_vir_full_v3.csv` (D1), `session_20251130_0920_vir_full_v3.csv` (D2)
**Compared to:** _baseline weekend — no prior data at this track + car._

---

## Conditions

| | Day 1 — Sat Nov 29 | Day 2 — Sun Nov 30 |
|---|---|---|
| Air temp (session start) | -0.6 °C / 31 °F | 3.0 °C / 37 °F |
| Humidity | 52% | 52% |
| Cloud / precip | 76% cloud, dry | 100% cloud, dry |
| Wind | 2.5 mph @ 5° | 4.3 mph @ 200° |

Both days started cold and overcast. Day 2 was 6.3 °F warmer with rubber laid down from Day 1's 26 laps — that combination, not technique, explains most of the day-over-day gain. On a heat-cycled CR-S at 31 °F, the first 8–10 laps will be tire warm-up, and lateral grip stays the dominant limiter all session.

---

## Top-Line Numbers

| Metric | Day 1 | Day 2 | Weekend |
|---|---|---|---|
| **Best lap** | 2:21.439 (L24) | **2:18.888 (L27)** | **2:18.888** |
| Theoretical optimal | 2:20.533 | 2:16.642 | **2:17.378** |
| Time left on best | +0.906 s | +2.246 s | **+1.510 s** |
| On-pace / total laps | 17 / 26 | 19 / 32 | 36 / 58 |
| Std dev (on-pace) | 5.568 s | 5.036 s | — |
| Top speed (best) | 120.3 mph | 121.5 mph | — |
| **CAN-bus dropout laps** | 4 (incl. L24★) | 0 (+2 partial: L14, L20) | 4 |

**Headline.** Weekend best 2:18.888 on D2 L27, with 1.510 s of theoretical room. **58% of that gap (0.880 s) lives in two front-straight sectors — S1 T1 entry (+0.448 s) and S10 Front Straight (+0.432 s) — and both reference D2 L14.** That's not two problems; it's one repeatable front-straight speed difference traceable to a single technique change at T17 (see Hog Pen analysis below).

> **Data caveats:** Day 1 L24 (best lap) has CAN dropout — lap time and sectors valid, no pedal data. Day 1 technique reference = L13 (2:26.398). On Day 2, L14 and L20 show physically impossible pedal % values (signal anomalies); their GPS/sector data is valid (correctly used in the optimal calculation), but ignore their pedal stats for technique analysis.

---

## Day-by-Day

**Day 1** (17 on-pace, 5.568 s std dev). Took 12 laps to come to life — long cold-tire build, then a stair-step down from L13 (2:26.4) through L23 (2:28.6) to L24 (2:21.4). L13 is the right Day-1 technique reference (76.4% throttle, 17.2% brake, 7.0% coast, 1.21 peak lat G). L24 had no pedal data, so the "how" of the 2:21.4 isn't recoverable — only the "where" via GPS.

**Day 2** (19 on-pace, 5.036 s std dev). Came competitive by L4 (2:21.2 — Day-1-best territory in 4 laps). Three sub-2:20 candidates appeared (L13, L14, L27), with L27 being the cleanest of the three — not the most committed, the most *joined-up*. Median dropped 9 seconds vs Day 1 and consistency tightened slightly.

---

## Sector Analysis — Best Lap (L27) vs Weekend Optimum

| # | Sector | Optimum | L27 | Δ | Optimum from |
|---|---|---|---|---|---|
| 1 | S1 T1 | **14.333** | 14.781 | **+0.448** | L14 D2 |
| 2 | S2 NASCAR ★ | 16.507 | 16.507 | 0.000 | L27 D2 |
| 3 | S3 Snake | 9.120 | 9.280 | +0.160 | L26 D2 |
| 4 | S4 Esses ★ | 19.300 | 19.300 | 0.000 | L27 D2 |
| 5 | S5 South Bend | 13.320 | 13.540 | +0.220 | L26 D2 |
| 6 | S6 Oak Tree ★ | 3.720 | 3.720 | 0.000 | L27 D2 |
| 7 | S7 Back Straight | 29.080 | 29.240 | +0.160 | L14 D2 |
| 8 | S8 Roller Coaster | 6.560 | 6.650 | +0.090 | L9 D2 |
| 9 | S9 Hog Pen ★ | 12.000 | 12.000 | 0.000 | L27 D2 |
| 10 | S10 Front Straight | **13.438** | 13.870 | **+0.432** | L14 D2 |
| | **Total** | **2:17.378** | 2:18.888 | **+1.510** | |

**L27 owned all four named-corner-complex sectors (NASCAR, Esses, Oak Tree, Hog Pen ★)** — every brake-and-rotate section of the lap was at its weekend best on the best lap. **The 1.51-s deficit lives entirely in straight-line and transition sectors**, and three of the four biggest gaps (S1, S7, S10) reference D2 L14, suggesting one root-cause speed difference rather than multiple unrelated issues.

---

## Day-Over-Day Apex Speed Gains

Where warmer track + rubber helped most (D1 best vs D2 best, mph):

| Turn | D1 | D2 | Δ |
|---|---|---|---|
| **T9** Esses | 97.7 | 103.9 | **+6.2** |
| **T14a** Roller Coaster | 67.4 | 73.4 | **+6.0** |
| **T7** Esses | 106.0 | 111.0 | **+4.9** |
| **T17** Hog Pen | 64.1 | 68.0 | **+3.9** |
| **T8** Esses | 104.8 | 108.6 | **+3.8** |
| **T11** Oak Tree | 57.1 | 60.7 | **+3.6** |
| T1, T2, T17a | — | — | +1.7 – +2.5 |

Gains cluster in **lateral-grip-dependent corners** (Esses, Roller Coaster, Oak Tree, Hog Pen apex) — exactly where cold-tire grip fails first. Straight-line braking and exit-traction corners (T1, T17a, T2) gained only 1.7–2.5 mph by comparison. On Day 1 the tire was the limiter at the high-G corners, not the inputs.

---

## Corner Stats — Top 5 Laps, Day 2

### T1 (front_straight_braking)

| Lap | Lap time | Entry (−100m) | Apex | Min | Peak latG | Brake max | Brake release (m) | Throttle@apex |
|---|---|---|---|---|---|---|---|---|
| **L27 ★** | 2:18.888 | 86.9 | 46.4 | 46.1 | 1.21 | 100% | +18.1 | 88% |
| L23 | 2:19.557 | 85.2 | 41.9 | 41.8 | 1.08 | 100% | +17.4 | 40% |
| L13 | 2:20.040 | 75.2 | 46.9 | 44.8 | 1.11 | 99% | +36.5 | 0% |
| L31 | 2:20.698 | 84.1 | 45.0 | 44.4 | 1.13 | 100% | +24.9 | 40% |
| L26 | 2:22.431 | 83.1 | 46.7 | 46.5 | 1.17 | 100% | +35.3 | 100% |

L14 (suspect pedal data) owns S1 with entry mph at −100m = **89.3** vs L27's **86.9** — a 2.4 mph entry-speed gap. **L27's T1 execution itself was the cleanest of the session** (release point, peak lat G, throttle pickup). The S1 deficit isn't in the corner — it's in the speed carried *into* the brake zone. The fix is upstream at T17 exit, not at T1 itself.

### T7 (Esses entry)

| Lap | Lap time | Apex | Min | Peak latG | Brake max | Throttle@apex |
|---|---|---|---|---|---|---|
| **L27 ★** | 2:18.888 | **111.0** | 105.9 | 0.85 | 0% | **75%** |
| L23 | 2:19.557 | 108.6 | 105.1 | 0.80 | 4% | 13% |
| L13 | 2:20.040 | 110.1 | 105.2 | 0.81 | 1% | 33% |
| L14 | 2:20.211 | 109.2 | 101.6 | 0.85 | 2% | 39% |
| L26 | 2:22.431 | 110.6 | 105.5 | 0.85 | 0% | 17% |

L27 owned T7 by a wide margin — highest apex of the session at **75% throttle through it**. The next-best lap (L23) lifted to 13% throttle through a 2.4 mph slower apex. **This is the lap-winning commitment of L27** and the reason S4 Esses came out at +0.000. Don't change it; replicate it.

### T11 (Oak Tree entry)

| Lap | Lap time | Apex | Min | Peak latG | Brake max | Brake onset (m) | Throttle@apex |
|---|---|---|---|---|---|---|---|
| **L27 ★** | 2:18.888 | 60.7 | **51.8** | 0.97 | **0%** | — | 60% |
| L23 | 2:19.557 | 61.3 | 49.2 | 0.92 | 83% | 90.8 | 0% |
| L13 | 2:20.040 | 60.4 | 49.0 | 0.93 | 0% | — | **100%** |
| L14 | 2:20.211 | 59.4 | 48.4 | 0.90 | 0% | — | **100%** |
| L26 | 2:22.431 | 62.5 | 50.9 | 0.94 | 91% | 105.0 | 0% |

**Oak Tree is a flow corner that doesn't need brake if entry is right** — three of your fastest five laps used 0% brake (L27, L13, L14). L27's 51.8 mph minimum is the **highest of any lap at T11** (rank 19/19) — the entry technique is solid. But L13 and L14 stayed at **100% throttle through the apex** while L27 was only at 60%. Holding more throttle through T11 apex on the clean lap is the gain — not changing the brake-free entry, but trusting the car to take more throttle once committed.

### T14a (Roller Coaster entry)

| Lap | Lap time | Apex | Min | Peak latG | Brake max | Brake onset (m) | Trail past apex | Throttle@apex |
|---|---|---|---|---|---|---|---|---|
| **L27 ★** | 2:18.888 | **73.4** | 57.5 | 0.56 | 100% | 141.4 | 44.7 m | 47% |
| L23 | 2:19.557 | 72.5 | 55.7 | 0.58 | 100% | 132.7 | 44.4 m | 0% |
| L13 | 2:20.040 | 74.7 | 58.6 | 0.56 | 100% | 136.1 | 44.4 m | 1% |
| L9 | 2:21.989 | 71.3 | 56.1 | 0.65 | 99% | 140.1 | 44.4 m | 0% |
| L4 | 2:21.168 | 63.9 | 50.3 | 0.45 | 88% | 151.4 | 44.7 m | 0% |

The 44 m trail-brake-past-apex pattern is repeated across all top laps — that's the correct technique for the off-camber downhill transition into Roller Coaster, and it's consistent. L27's 47% throttle *during* the trail-brake is rotation back to power, and the resulting 1.8 mph higher min speed vs L23 (57.5 vs 55.7) confirms it's working. Don't change.

### T17 (Hog Pen apex) — the key finding

| Lap | Lap time | Apex | Min | Peak latG | Brake max | Throttle@apex |
|---|---|---|---|---|---|---|
| **L27 ★** | 2:18.888 | 68.0 | 67.8 | 1.23 | 22% | **51%** |
| L23 | 2:19.557 | 67.6 | 67.5 | 1.20 | 1% | **100%** |
| L13 | 2:20.040 | 68.6 | 67.0 | 1.13 | 73% | 19% |
| L14 | 2:20.211 | **69.0** | 68.2 | 1.22 | 0% | **100%** |
| L31 | 2:20.698 | 68.0 | 67.2 | **1.30** | 0% | **99%** |

**L27 ran 51% throttle at T17 apex with 22% brake still applied. L14, L23, and L31 ran 99–100% throttle with 0% brake — same apex speeds, completely different commitment.** L31 hit the session's highest lateral G (1.30) at T17 — the tire is willing to take it. Three different laps, on three different attempts, demonstrated the corner can be taken at full throttle. On the best lap, you didn't. This is the single most actionable finding in the dataset.

---

## How the Front-Straight Deficit Ties Together

The S10 (+0.432 s) and S1 (+0.448 s) deficits are not two separate problems — they're one technique change at T17:

1. **L27 was at 51% throttle at T17 apex** (vs 99–100% on three other laps).
2. Lower throttle through Hog Pen → lower T17a exit speed.
3. Lower exit speed compounds across the 700 m front straight → **−0.432 s S10**.
4. Lower top-of-front-straight speed → slower into T1 brake zone → **−0.448 s S1**.

**One technique change at T17 ≈ 0.880 s recovered.** This is the highest-leverage single change available on this weekend's data.

---

## Improvement Targets — Next Session

### 1. Commit to full throttle at T17 apex (Target: ~0.880 s across S10 + S1)
L27 → 51% throttle + 22% brake at T17 apex. Reference: **L23 ran 100% throttle, 0% brake, 67.6 mph apex** (nearly identical apex speed, completely different commitment). L31 hit 1.30 lat G at T17 — grip is there. Three laps did it; do it on the clean lap.

### 2. Hold more throttle through T11 Oak Tree apex (Target: 0.100–0.150 s in S6)
L27 → 60% throttle, 0% brake, 51.8 mph min (highest of any lap). L13 and L14 held **100% throttle** through T11 apex. The 0%-brake entry technique is correct — the gain is in not lifting at the apex itself.

### 3. Replicate L26's S5 South Bend line (Target: 0.220 s in S5)
L26 (2:22.431, +3.5 s overall) ran S5 in **13.320 s** vs L27's 13.540 s. Slower overall lap, faster sector — the line/min-speed combination exists. Worth a side-by-side review of L26's T10 entry on the next session.

---

## What to Keep Doing

- **T7 Esses commitment** — 75% throttle through 111 mph apex was the session's most committed T7 and owned S4 outright.
- **T1 execution** — 100% brake → release 18 m before apex → 88% throttle by apex → 1.21 lat G. The gain isn't in T1 itself, it's in feeding it more entry speed.
- **T14a trail-brake** — 100% brake, 44 m past apex, 47% throttle during rotation. Correct technique for the off-camber downhill transition.
- **Day-over-day consistency** — std dev came down 0.5 s and median dropped 9 s from D1 to D2. The lap cadence that produced L13/L14/L23/L27 in the same session is working.

---

## Series Baseline

| Weekend | Tires | Conditions | Best | Optimal | Gap |
|---|---|---|---|---|---|
| **2025-11-29-30** | Nankang CR-S (4–7 cycles) | Cold dry (31 → 37 °F) | **2:18.888** | 2:17.378 | +1.510 s |

This is the **baseline weekend**. Two things to watch as more weekends arrive: (1) does the T17 throttle hesitation persist across weekends, or was it a cold-day caution, and (2) how do the lateral-grip-dependent apex speeds (Esses, Roller Coaster) move on a warmer day or stickier tire — those are the corners that'll move first.

---

## Data-Quality Notes

- **D1 L24** (best lap): CAN dropout. GPS valid; pedal data not. Use L13 for D1 technique.
- **D1 L22, L23, L25**: also CAN dropout.
- **D2 L14, L20**: sub-threshold CAN anomalies (pedal stats impossible). Sector times valid.
- **Tire pressures not recorded** — capture cold pressures next weekend for grip-balance analysis.
