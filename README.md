# claude-driver-coach

Track-day telemetry analysis from RaceChrono CSV exports. Produces structured
Markdown reports designed to be read by Claude (or you) and turned into
coaching feedback.

The Python tool is purely deterministic — it does the data crunching and
writes rich Markdown. The narrative coaching happens when you hand the
generated `reports/` files to a Claude session.

## What it does

1. **Imports** a RaceChrono Pro CSV. You tag it with track, car, weekend,
   day, session label, and the tires that were on the car for that session.
2. **Auto-fetches weather** from Open-Meteo for the session's start
   timestamp + GPS location, and caches the result on disk.
3. **Detects laps**, filters outliers (in/out laps, incidents, anything more
   than ~5% off the session's median), and computes per-corner metrics
   using your track's hand-curated turn coordinates.
4. **Writes Markdown reports** at three levels:
   - per-session (lap list, best-lap detail, per-corner table, lap-over-lap deltas)
   - per-weekend rollup (all sessions in a weekend, day-over-day, weather, tires)
   - per-(track, car) progression (best lap by weekend, apex-speed and section-time evolution)

## Layout

```
ddc/                              # python package
tracks/<slug>.md                  # hand-curated track reference (turn coords + named sections)
cars/<slug>.md                    # hand-curated car reference
sessions/                         # parquet + .meta.json per import, plus manifest.json and weather_cache.json
reports/<track>/<car>/            # generated .md (per-session under weekend subfolders; weekend + progression at top)
```

`sessions/` is gitignored; `reports/` is tracked so you can diff coaching
material over time.

## Setup

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```powershell
uv sync
```

## Usage

```powershell
# See what's registered
uv run ddc tracks
uv run ddc cars

# Import a session — prompts for any tag you don't pass as a flag
uv run ddc import "path\to\session.csv"

# Or fully scripted
uv run ddc import "path\to\session.csv" `
  --track vir_full `
  --car 2020_miata_rf_club_bbr `
  --weekend 2026-05-03_vir `
  --day day_1 `
  --label session_1_morning `
  --tire-compound "Bridgestone Potenza RE-71RS" `
  --tire-size "255/40R17" `
  --tire-age "8 heat cycles" `
  --tire-pressures "FL 28 / FR 28 / RL 26 / RR 26" `
  --notes "Cool morning, sticky track"

# Browse what you've imported
uv run ddc list
uv run ddc list --track vir_full --car 2020_miata_rf_club_bbr

# (Re)generate reports
uv run ddc report <session-id>
uv run ddc weekend vir_full 2020_miata_rf_club_bbr 2026-05-03_vir
uv run ddc progress vir_full 2020_miata_rf_club_bbr
```

### A note on PowerShell quoting

PowerShell expands a leading `~` to your home directory. If you want a tilde
in a tire-age string (`~8 heat cycles`), wrap it like this:
`--tire-age '~8 heat cycles'` (single quotes), or just write
`--tire-age "8 heat cycles"`.

## Adding a track or car

Tracks and cars are plain Markdown files with a simple convention so they
double as human-readable documentation. The loaders look for:

**Track** (`tracks/<slug>.md`):

- A preamble bullet `- **slug**: <slug>` (defaults to filename if omitted)
- `## Start / Finish` with a table containing `Latitude` and `Longitude` columns
- `## Turns` with a table containing `#`, `Name`, `Section`, `Latitude`, `Longitude`
- Optional `## Sections (named complexes)` with bullets like `- **snake**: 5b → 6a → 6b`
- Optional `## Notes for analysis` — preserved verbatim for Claude to read

**Car** (`cars/<slug>.md`):

- Preamble bullets: `slug`, `year`, `make`, `model`, `trim`
- `## Modifications` table with `Category`, `Item`
- Optional `## Notes for analysis`

Tires are deliberately **not** on the car — they're prompted for at each
import, since they change session-to-session.

## How metrics are computed

- **Lap times** = time between consecutive start/finish crossings, taken from
  RaceChrono's own `lap_number` column. The final lap in a file is marked
  `incomplete` since it has no closing crossing.
- **Outliers** = incomplete laps, laps with abnormal distance (±15% vs.
  the session median), laps with no time, and laps more than 5% slower than
  the median of structurally-clean laps. Faster-than-median laps are kept.
- **Corner windows** = Voronoi assignment of each telemetry sample to its
  nearest turn coordinate. The "apex passage" is the sample where the car
  was closest to the turn coordinate.
- **Brake / throttle onset and release** are measured in meters before or
  after the apex, signed so positive always means "closer to / past apex."
- **Steering smoothness** is the RMS of `dθ/dt` across the window. Lower is
  smoother. "Steering reversals" counts sign changes in the rate within
  the window — a busy-hands indicator.
- **Sector times** include both named-section spans (snake, esses, ...) and
  every corner-to-corner pair.

The full sign-convention reading guide is appended to every per-session
report so the meaning of every column is always one scroll away.

## Coaching workflow

After importing, open the generated `reports/<track>/<car>/<weekend>/session_*.md`
file in a Claude chat and ask things like:

- "What's costing me the most time vs. my best lap?"
- "Compare my technique in the Snake to my best lap — am I scrubbing speed?"
- "I trail-braked T11 on the best lap; how did that compare to my other laps?"
- "Looking at the weekend rollup, did the afternoon heat hurt me, or did the tires drop off?"

For longer-arc questions ("am I getting better at the Esses?") use the
progression report.
