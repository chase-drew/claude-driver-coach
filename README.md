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
4. **Writes Markdown reports** at four levels:
   - per-session (lap list, best-lap detail, per-corner table with gear-at-apex, lap-over-lap deltas)
   - per-weekend rollup (all sessions in a weekend, day-over-day, weather, tires)
   - per-(track, car) progression (best lap by weekend, apex-speed and section-time evolution)
   - **coaching inputs** (`coaching_inputs_<weekend>.md`) — a single dense, source-cited file with weekend facts, ranked per-corner improvement gaps, and per-corner technique signature comparisons vs the all-time best lap. Designed as the *only* file the coaching writer reads.

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

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv). Runs on Windows, macOS, and Linux.

```bash
uv sync
```

## Usage

The CLI examples below use POSIX-style quoting (`bash`, `zsh`). On Windows PowerShell, swap single quotes for double quotes and use backtick line continuations instead of backslashes.

```bash
# See what's registered
uv run ddc tracks
uv run ddc cars

# Import a single session — prompts for any tag you don't pass as a flag
uv run ddc import "path/to/session.csv"

# Or fully scripted
uv run ddc import "path/to/session.csv" \
  --track vir_full \
  --car 2020_miata_rf_club_bbr \
  --weekend 2026-05-03_vir \
  --day day_1 \
  --label session_1_morning \
  --tire-compound "Bridgestone Potenza RE-71RS" \
  --tire-size "255/40R17" \
  --tire-age "8 heat cycles" \
  --tire-pressures "FL 28 / FR 28 / RL 26 / RR 26" \
  --notes "Cool morning, sticky track"

# Import a whole weekend (multiple CSVs in one go) and auto-regenerate every
# downstream report (rollup, vs-prior comparison, progression, coaching inputs).
uv run ddc import-weekend "path/to/csv/folder" --track vir_full --car 2020_miata_rf_club_bbr --weekend 2026-05-03_vir

# Browse what you've imported
uv run ddc list
uv run ddc list --track vir_full --car 2020_miata_rf_club_bbr

# (Re)generate reports individually
uv run ddc report <session-id>
uv run ddc weekend vir_full 2020_miata_rf_club_bbr 2026-05-03_vir
uv run ddc compare vir_full 2020_miata_rf_club_bbr 2026-05-03_vir
uv run ddc coaching-inputs vir_full 2020_miata_rf_club_bbr 2026-05-03_vir
uv run ddc progress vir_full 2020_miata_rf_club_bbr
```

### Progress bars and CPU cores

Every long-running command (anything that loads more than one parquet — `compare`, `coaching-inputs`, `progress`, `weekend`, `report`) shows a live progress bar with elapsed time and ETA. The bar uses ASCII-safe characters so it works in modern Windows Terminal, macOS Terminal/iTerm, and any standard Linux terminal. When stdout is redirected to a file or you pass `--no-progress`, the bar is suppressed and a single status line is printed instead so logs stay clean.

```bash
# Disable bars (useful for CI or when piping to a file)
uv run ddc --no-progress coaching-inputs vir_full 2020_miata_rf_club_bbr 2026-05-03_vir
```

The pipeline parallelizes per-lap and per-weekend work using a thread pool. By default it uses up to 8 worker threads (capped to the CPU count). Override with the `DDC_MAX_WORKERS` environment variable when you want more or fewer:

```bash
# macOS / Linux
DDC_MAX_WORKERS=12 uv run ddc compare vir_full 2020_miata_rf_club_bbr 2026-05-03_vir

# Windows PowerShell
$env:DDC_MAX_WORKERS=12; uv run ddc compare vir_full 2020_miata_rf_club_bbr 2026-05-03_vir
```

### A note on PowerShell quoting

PowerShell expands a leading `~` to your home directory. If you want a tilde
in a tire-age string (`~8 heat cycles`), wrap it like this:
`--tire-age '~8 heat cycles'` (single quotes), or just write
`--tire-age "8 heat cycles"`.

### Cross-platform notes

- All paths in CLI args use forward slashes on macOS/Linux and backslashes on Windows — Python's pathlib accepts either everywhere, so quoting matters more than slash direction.
- Progress bars use ASCII-safe characters; they render correctly in Windows Terminal (recommended), macOS Terminal.app, iTerm2, and any common Linux terminal. Older legacy `cmd.exe` is also supported (stdout is auto-reconfigured to UTF-8 at startup).
- `uv` works identically on all three OSes. If you don't have it, install via the script at https://docs.astral.sh/uv/ — it's a single-binary download with no dependencies.

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
- Optional `## Drivetrain — gear ratios` table with columns `Gear`, `mph_per_1000_rpm` — used to infer gear at every sample (no CAN gear sensor needed). Values are nominal; the pipeline auto-calibrates them per session to handle tire compound / pressure / wear / temperature drift, so you don't need to re-edit this when you change tires.
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
- **Gear inference** comes from speed × RPM (no CAN gear sensor needed). The car
  reference's nominal `mph_per_1000_rpm` ratios are auto-calibrated per session
  using the actual data, so they stay accurate across tire compound, tire
  pressure, wear, and temperature changes.
- **Heel-toe rev-match detection** identifies the brief throttle pulses during
  heavy braking that are downshifts, not technique errors. These samples are
  excluded from the `% overlap` metric so prior coaching never flags correct
  downshift technique as a two-pedal fault. Detection uses *either* a confirmed
  gear-drop across the overlap window or a RPM-rise while the car decelerates.

The full sign-convention reading guide is appended to every per-session
report so the meaning of every column is always one scroll away.

## Coaching workflow

After importing a weekend, the pipeline auto-generates `reports/<track>/<car>/coaching_inputs_<weekend>.md`. **This is the file to open in a Claude chat** when you want a coaching writeup — it contains:

- Weekend best / optimum / gap (with source lap citations)
- Per-session weather and conditions
- Sector bests with source-lap labels
- Cross-weekend sector best matrix with all-time owner
- Vs prior weekend sector delta
- Per-corner apex-speed gap ranking (sorted by deficit, with all-time source weekend/lap)
- Per-corner technique signature comparison vs the all-time best lap (apex mph, style, brake onset, brake max, trail past apex, throttle@apex, throttle pickup, latG, gear entry→apex)

Every value cites its source lap, so the coaching writer cannot invent benchmark numbers. For deeper drill-down into a specific session, the per-session report (`reports/<track>/<car>/<weekend>/session_*.md`) still has the full per-lap tables. For longer-arc questions ("am I getting better at the Esses across the year?") use `progression.md`.

Typical Claude prompts:

- "Based on coaching_inputs, what are the top three things to work on next weekend?"
- "Compare my T9 technique this weekend to the all-time best lap — what specifically changed?"
- "Did last weekend's improvement targets land? Use the prior coaching file."
- "Pull up the per-session report for session_0915 and look at the Esses lap-by-lap."
