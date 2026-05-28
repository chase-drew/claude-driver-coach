"""Markdown report generators.

These reports are designed to be read by Claude (or a human) and turned into
coaching narrative. They lean heavily on structured Markdown tables and
explicit units so the reader never has to guess what a number means.

Three report types:

  * ``write_session_report`` — one session in depth: lap list, best-lap detail,
    per-corner table, lap deltas vs. session best.
  * ``write_weekend_report`` — every session at a track/car in a single weekend,
    annotated with weather, tires, time-of-day, day-over-day evolution.
  * ``write_progression_report`` — same track + car across all weekends:
    best-lap trend, sector trend, technique change pointers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .laps import Lap, classify_outliers, detect_laps, format_lap_time, on_pace, session_best
from .metrics import (
    CornerDelta,
    FixedSectorTime,
    LapMetrics,
    TurnPassage,
    compare_to_reference,
    compute_lap_metrics,
    corner_stats,
    lost_time_attribution,
    sample_trace_around_corner,
    theoretical_best,
)
from .refs import CarRef, TrackRef, Turn, find_car, find_track
from .storage import (
    SessionMeta,
    TireSpec,
    load_session_df,
    load_session_meta,
    report_dir_for,
    sessions_for,
    weekend_ids,
)
from .weather import WeatherSnapshot, conditions_summary


MPS_TO_MPH = 2.2369362921
MPS_TO_KPH = 3.6


def _fmt(v: float | None, spec: str = ".2f", dash: str = "—") -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return dash
    return format(v, spec)


def _fmt_signed(v: float | None, spec: str = "+.3f") -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return format(v, spec)


def _mph(v_mps: float | None) -> str:
    if v_mps is None or not np.isfinite(v_mps):
        return "—"
    return f"{v_mps * MPS_TO_MPH:.1f}"


def _time_of_day(start_iso: str) -> str:
    if not start_iso:
        return "—"
    try:
        dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    except ValueError:
        return start_iso
    return dt.astimezone().strftime("%H:%M %Z").strip()


# ---------------------------------------------------------------------------
# Per-session report
# ---------------------------------------------------------------------------

def write_session_report(session_id: str) -> Path:
    """Generate the per-session Markdown report and return the output path."""
    meta = load_session_meta(session_id)
    df = load_session_df(session_id)
    track = find_track(meta.track_slug)
    car = find_car(meta.car_slug)

    laps = detect_laps(df)
    classify_outliers(laps)
    best = session_best(laps)

    metrics_by_lap: dict[int, LapMetrics] = {}
    lap_by_num: dict[int, Lap] = {}
    for lap in on_pace(laps):
        metrics_by_lap[lap.lap_number] = compute_lap_metrics(lap, df, track)
        lap_by_num[lap.lap_number] = lap
    best_metrics = metrics_by_lap.get(best.lap_number) if best else None

    out_dir = report_dir_for(meta.track_slug, meta.car_slug)
    weekend_dir = out_dir / meta.weekend_id
    weekend_dir.mkdir(parents=True, exist_ok=True)
    out_path = weekend_dir / f"session_{session_id}.md"

    md = _render_session_report(meta, track, car, df, laps, lap_by_num, metrics_by_lap, best, best_metrics)
    out_path.write_text(md, encoding="utf-8")
    return out_path


def _render_session_report(
    meta: SessionMeta,
    track: TrackRef,
    car: CarRef,
    df: pd.DataFrame,
    laps: list[Lap],
    lap_by_num: dict[int, Lap],
    metrics_by_lap: dict[int, LapMetrics],
    best: Lap | None,
    best_metrics: LapMetrics | None,
) -> str:
    weather = WeatherSnapshot(**meta.weather) if meta.weather else None
    weather_str = conditions_summary(weather) if weather else "Weather not collected."

    lines: list[str] = []
    lines.append(f"# Session report — {track.title} — {meta.session_label or meta.session_id}")
    lines.append("")
    lines.append(f"- **Session ID**: `{meta.session_id}`")
    lines.append(f"- **CSV**: `{meta.csv_filename}`")
    lines.append(f"- **Track**: {track.title} (`{track.slug}`)")
    lines.append(f"- **Car**: {car.title} (`{car.slug}`)")
    lines.append(f"- **Weekend**: `{meta.weekend_id}` — {meta.day_of_weekend}")
    lines.append(f"- **Start (UTC)**: {meta.start_time_utc}")
    lines.append(f"- **Start (local)**: {_time_of_day(meta.start_time_utc)}")
    lines.append(f"- **Samples**: {meta.sample_count:,}    **Duration**: {meta.duration_s:.1f}s")
    lines.append("")
    lines.append("## Tires")
    lines.append("")
    lines.append(f"- **Compound**: {meta.tires.compound or '—'}")
    lines.append(f"- **Size**: {meta.tires.size or '—'}")
    lines.append(f"- **Age / heat cycles**: {meta.tires.age_heat_cycles or '—'}")
    lines.append(f"- **Cold pressures**: {meta.tires.pressures_cold_psi or '—'}")
    if meta.tires.note:
        lines.append(f"- **Note**: {meta.tires.note}")
    lines.append("")
    lines.append("## Weather (at session start)")
    lines.append("")
    lines.append(f"- {weather_str}")
    if weather:
        lines.append(f"- Source: `{weather.source}` for hour {weather.timestamp_utc}")
    lines.append("")
    if meta.notes:
        lines.append("## Driver notes")
        lines.append("")
        lines.append(meta.notes)
        lines.append("")

    # ---- Session segments (paddock / pit cool-down breaks) --------------------
    segment_breaks = [l for l in laps if l.segment_break_after]
    n_segments = max((l.segment_id for l in laps), default=0) + 1
    if segment_breaks:
        lines.append("## Session segments")
        lines.append("")
        lines.append(
            f"This session has **{n_segments} segments** separated by {len(segment_breaks)} paddock/pit cool-down break(s). "
            "A new segment means the car was stationary long enough that tires and brake pads cooled — treat the first 2–3 laps of each segment as warm-up, not technique signal. **Gap durations are in seconds.**"
        )
        lines.append("")
        lines.append("| Segment | Laps in segment | Break ends on lap | Stationary gap |")
        lines.append("|---|---|---|---|")
        for seg_idx in range(n_segments):
            seg_laps = [l for l in laps if l.segment_id == seg_idx]
            if not seg_laps:
                continue
            lap_range = f"L{seg_laps[0].lap_number}–L{seg_laps[-1].lap_number}"
            break_lap = next((l for l in seg_laps if l.segment_break_after), None)
            if break_lap is not None:
                gap_s = break_lap.segment_break_duration_s
                gap_str = f"{gap_s:.0f} s (~{gap_s / 60:.1f} min) after L{break_lap.lap_number}"
            else:
                gap_str = "—"
            lines.append(f"| S{seg_idx} | {len(seg_laps)} ({lap_range}) | L{break_lap.lap_number if break_lap else '—'} | {gap_str} |")
        lines.append("")

    # ---- Lap summary table ----------------------------------------------------
    lines.append("## Lap summary")
    lines.append("")
    lines.append("On-pace = lap kept for analysis (not an in/out lap, not a structural outlier, not a paddock/pit break, not >5% off median pace). Long laps that are paddock breaks show the stationary gap in seconds; the lap-time column would mislead.")
    lines.append("")
    lines.append("| Lap # | Seg | Lap time | Δ vs best | Distance (m) | Avg mph | Max mph | Status |")
    lines.append("|---|---|---|---|---|---|---|---|")
    best_s = best.lap_time_s if best else float("nan")
    for lap in laps:
        delta = lap.lap_time_s - best_s if best and np.isfinite(lap.lap_time_s) and np.isfinite(best_s) else float("nan")
        status = "on-pace" if not lap.outlier and not lap.incomplete else ", ".join(lap.outlier_reasons) or "—"
        if best and lap.lap_number == best.lap_number:
            status = "**best**"
        # For segment-break laps, the M:SS.mmm lap time is misleading — show the gap in seconds instead.
        if lap.segment_break_after:
            lap_time_display = f"_break: {lap.segment_break_duration_s:.0f} s_"
            delta_display = "—"
        else:
            lap_time_display = lap.lap_time_str
            delta_display = _fmt_signed(delta)
        lines.append(
            f"| {lap.lap_number} | S{lap.segment_id} | {lap_time_display} | {delta_display} | "
            f"{_fmt(lap.distance_m, '.0f')} | {_mph(lap.avg_speed_mps)} | {_mph(lap.max_speed_mps)} | {status} |"
        )
    lines.append("")

    on_pace_laps = on_pace(laps)
    if on_pace_laps:
        times = np.array([l.lap_time_s for l in on_pace_laps])
        lines.append(f"**On-pace count**: {len(on_pace_laps)}    "
                     f"**Median**: {format_lap_time(float(np.median(times)))}    "
                     f"**Std dev**: {np.std(times):.3f}s    "
                     f"**Spread (max−min)**: {(times.max() - times.min()):.3f}s")
        lines.append("")

    if best is None or best_metrics is None:
        lines.append("_No on-pace laps available; skipping per-corner analysis._")
        return "\n".join(lines) + "\n"

    # ---- Lap-by-lap headline metrics ------------------------------------------
    lines.append("## Lap-by-lap headline metrics")
    lines.append("")
    lines.append("Whole-lap aggregates for every on-pace lap, side by side. Useful for spotting whether a faster lap "
                 "came from higher commitment (more throttle %, higher peak Gs) or smoother inputs (lower steering RMS).")
    lines.append("")
    lines.append("| Lap | Time | Δ vs best | Peak latG | Peak decelG | Peak accelG | Peak combG | Max mph | Min mph | %thr | %brk | %coast | %overlap | Steer RMS |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for lap_num in sorted(metrics_by_lap.keys(), key=lambda n: lap_by_num[n].lap_time_s):
        lm = metrics_by_lap[lap_num]
        lap_obj = lap_by_num[lap_num]
        delta = lap_obj.lap_time_s - best.lap_time_s
        peak_comb = max((p.peak_combined_g for p in lm.passages if np.isfinite(p.peak_combined_g)), default=float("nan"))
        marker = " **best**" if lap_num == best.lap_number else ""
        lines.append(
            f"| {lap_num}{marker} | {lap_obj.lap_time_str} | {_fmt_signed(delta)} | "
            f"{_fmt(lm.peak_lateral_g, '.2f')} | {_fmt(lm.peak_longitudinal_decel_g, '.2f')} | "
            f"{_fmt(lm.peak_longitudinal_accel_g, '.2f')} | {_fmt(peak_comb, '.2f')} | "
            f"{_mph(lm.max_speed_mps)} | {_mph(lm.min_speed_mps)} | "
            f"{_fmt(lm.pct_lap_on_throttle, '.1f')} | {_fmt(lm.pct_lap_on_brake, '.1f')} | "
            f"{_fmt(lm.pct_lap_coasting, '.1f')} | {_fmt(lm.pct_lap_overlap, '.1f')} | "
            f"{_fmt(lm.steering_smoothness, '.0f')} |"
        )
    lines.append("")

    # ---- Theoretical best -----------------------------------------------------
    tb = theoretical_best(metrics_by_lap, best.lap_time_s)
    if tb.segments:
        lines.append("## Theoretical best lap")
        lines.append("")
        lines.append(f"Picking the fastest corner-to-corner segment from any on-pace lap and summing them:")
        lines.append("")
        lines.append(f"- **Theoretical best**: {format_lap_time(tb.total_s)} (sum of {len(tb.segments)} best segments)")
        lines.append(f"- **Actual best**: {format_lap_time(tb.actual_best_s)} (lap {best.lap_number})")
        lines.append(f"- **Gap (room to find)**: {tb.delta_s * 1000:+.0f} ms")
        lines.append("")
        lines.append("| Segment | From lap | Time (s) |")
        lines.append("|---|---|---|")
        for label, lap_num, t in tb.segments:
            mark = " **(best lap)**" if lap_num == best.lap_number else ""
            lines.append(f"| {label} | lap {lap_num}{mark} | {t:.3f} |")
        lines.append("")
        lines.append("_A wide spread of source laps (many different lap numbers in the table above) means time is "
                     "spread across the session — no single lap was clean throughout. A narrow spread (most from one lap) "
                     "means that lap was very close to optimal._")
        lines.append("")

    # ---- Lost-time attribution -------------------------------------------------
    other_laps = sorted(
        [l for l in on_pace_laps if l.lap_number != best.lap_number],
        key=lambda l: l.lap_time_s,
    )
    if other_laps:
        lines.append("## Lost-time attribution (vs best lap)")
        lines.append("")
        lines.append("For each non-best lap, where the deficit accumulated. Segments are ranked by time lost (positive = lost time).")
        lines.append("")
        for other in other_laps:
            other_m = metrics_by_lap.get(other.lap_number)
            if other_m is None:
                continue
            losses = lost_time_attribution(other_m, best_metrics)
            total_def_ms = (other.lap_time_s - best.lap_time_s) * 1000.0
            lines.append(f"### Lap {other.lap_number} — {other.lap_time_str} (deficit {total_def_ms:+.0f} ms)")
            lines.append("")
            ranked = sorted(losses, key=lambda r: r.ms_lost, reverse=True)
            lines.append("| Segment | Section | Δ ms | % of total deficit |")
            lines.append("|---|---|---|---|")
            for r in ranked:
                lines.append(f"| {r.segment_label} | {r.section} | {r.ms_lost:+.0f} | {r.pct_of_total_deficit:+.1f}% |")
            lines.append("")
            # Quick "top contributors" callout.
            top = [r for r in ranked if r.ms_lost > 0][:3]
            gains = [r for r in sorted(losses, key=lambda r: r.ms_lost)[:3] if r.ms_lost < 0]
            if top:
                top_str = ", ".join(f"{r.segment_label} ({r.ms_lost:+.0f}ms)" for r in top)
                lines.append(f"**Top 3 losses**: {top_str}")
            if gains:
                gains_str = ", ".join(f"{r.segment_label} ({r.ms_lost:+.0f}ms)" for r in gains)
                lines.append(f"**Top 3 gains over best**: {gains_str}")
            lines.append("")

    # ---- Named-section times across all on-pace laps --------------------------
    section_names = [s for s in best_metrics.sector_times_s if "→" not in s]
    if section_names and len(metrics_by_lap) > 1:
        lines.append("## Named-section times across on-pace laps")
        lines.append("")
        header_laps = sorted(metrics_by_lap.keys(), key=lambda n: lap_by_num[n].lap_time_s)
        header = "| Section | " + " | ".join(f"L{n}{'★' if n == best.lap_number else ''}" for n in header_laps) + " | Min | Max | Median | Std |"
        sep = "|---|" + "|".join(["---"] * len(header_laps)) + "|---|---|---|---|"
        lines.append(header)
        lines.append(sep)
        for section in section_names:
            row: list[str] = [section]
            vals: list[float] = []
            for n in header_laps:
                t = metrics_by_lap[n].sector_times_s.get(section)
                if t is None or not np.isfinite(t):
                    row.append("—")
                else:
                    row.append(f"{t:.3f}")
                    vals.append(t)
            if vals:
                row += [f"{min(vals):.3f}", f"{max(vals):.3f}", f"{float(np.median(vals)):.3f}", f"{float(np.std(vals)):.3f}"]
            else:
                row += ["—", "—", "—", "—"]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # ---- Best-lap headline -----------------------------------------------------
    lines.append(f"## Best lap whole-lap detail (lap {best.lap_number} — {best.lap_time_str})")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Peak lateral G | {_fmt(best_metrics.peak_lateral_g, '.2f')} g |")
    lines.append(f"| Peak braking G | {_fmt(best_metrics.peak_longitudinal_decel_g, '.2f')} g |")
    lines.append(f"| Peak accel G | {_fmt(best_metrics.peak_longitudinal_accel_g, '.2f')} g |")
    lines.append(f"| Max speed | {_mph(best_metrics.max_speed_mps)} mph |")
    lines.append(f"| Min speed | {_mph(best_metrics.min_speed_mps)} mph |")
    lines.append(f"| % lap on throttle (>5%) | {_fmt(best_metrics.pct_lap_on_throttle, '.1f')}% |")
    lines.append(f"| % lap on brake (>5%) | {_fmt(best_metrics.pct_lap_on_brake, '.1f')}% |")
    lines.append(f"| % lap coasting | {_fmt(best_metrics.pct_lap_coasting, '.1f')}% |")
    lines.append(f"| % lap brake+throttle overlap | {_fmt(best_metrics.pct_lap_overlap, '.1f')}% |")
    lines.append(f"| Steering smoothness (RMS rate, deg/s — lower=smoother) | {_fmt(best_metrics.steering_smoothness, '.1f')} |")
    lines.append("")

    # ---- Per-corner deep dives -------------------------------------------------
    lines.append("## Per-corner deep dive")
    lines.append("")
    lines.append("For every corner: a side-by-side table of every on-pace lap, an apex-style / trail-brake summary, "
                 "a per-field consistency block, and a distance-from-apex time-series trace (sampled every 10 m from "
                 "−160 m before apex to +100 m after) for speed, brake %, throttle %, steering angle, lateral G, and "
                 "longitudinal G on every on-pace lap.")
    lines.append("")
    turn_by_num = {t.number: t for t in track.turns}
    ordered_turn_numbers = [p.turn_number for p in best_metrics.passages]
    sorted_laps = sorted(metrics_by_lap.keys(), key=lambda n: lap_by_num[n].lap_time_s)

    for turn_number in ordered_turn_numbers:
        turn_ref = turn_by_num.get(turn_number)
        passages_for_turn = _collect_passages_for_turn(metrics_by_lap, turn_number)
        if not passages_for_turn:
            continue
        lines.extend(_render_corner_section(
            turn_number=turn_number,
            turn_ref=turn_ref,
            passages_for_turn=passages_for_turn,
            metrics_by_lap=metrics_by_lap,
            lap_by_num=lap_by_num,
            best_lap_number=best.lap_number,
            sorted_laps=sorted_laps,
            df=df,
        ))
        lines.append("")

    # ---- Lap-over-lap per-corner deltas vs best (kept) ------------------------
    if other_laps:
        lines.append("## Lap-over-lap per-corner deltas vs best")
        lines.append("")
        lines.append("Per-corner deltas show how much time was won or lost into each corner relative to the best lap, "
                     "plus the apex speed / brake onset / throttle pickup differences that explain it.")
        lines.append("")
        for other in other_laps:
            other_m = metrics_by_lap.get(other.lap_number)
            if other_m is None:
                continue
            lap_delta = other.lap_time_s - best.lap_time_s
            lines.append(f"### Lap {other.lap_number} — {other.lap_time_str} (Δ {_fmt_signed(lap_delta)}s)")
            lines.append("")
            deltas = compare_to_reference(other_m, best_metrics)
            lines.append("| T | Section | Δ into-corner (s) | Δ apex mph | Δ min mph | Δ brake onset (m) | Δ throttle pickup (m) | Δ peak latG |")
            lines.append("|---|---|---|---|---|---|---|---|")
            for d in deltas:
                lines.append(
                    f"| {d.turn_number} | {d.section} | "
                    f"{_fmt_signed(d.sector_into_corner_delta_s)} | "
                    f"{_fmt_signed(d.speed_delta_mps * MPS_TO_MPH, '+.1f')} | "
                    f"{_fmt_signed(d.min_speed_delta_mps * MPS_TO_MPH, '+.1f')} | "
                    f"{_fmt_signed(d.brake_onset_delta_m, '+.1f')} | "
                    f"{_fmt_signed(d.throttle_onset_delta_m, '+.1f')} | "
                    f"{_fmt_signed(d.peak_lat_g_delta, '+.2f')} |"
                )
            lines.append("")

    # ---- Sign conventions reference -------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append("## Sign conventions and reading guide")
    lines.append("")
    lines.append("### Time / position")
    lines.append("- **Apex t (s)**: elapsed seconds from lap start (start/finish crossing) to the sample where the car was closest to that turn's GPS coordinate.")
    lines.append("- **Δ into-corner**: time from prior turn to this turn, target lap minus best. Positive = lost time arriving.")
    lines.append("- **Lost-time attribution**: positive ms = time lost in that segment vs best; sum across all segments equals the lap's total deficit (within rounding).")
    lines.append("")
    lines.append("### Speed")
    lines.append("- **Apex mph**: speed at the moment of closest approach to the turn's GPS coord.")
    lines.append("- **Min mph (window)**: lowest speed inside the Voronoi window for this turn (often a few meters before or after the GPS apex).")
    lines.append("- **Δ apex/min mph**: positive = faster than best lap at that point.")
    lines.append("")
    lines.append("### Apex style")
    lines.append("- Derived from where (signed meters from the apex coord) the minimum speed occurred inside the window:")
    lines.append("  - `early` — min speed ≥ 8 m *before* the apex coord. Usually means an early-apex line; speed comes back up before the actual apex but the exit phase is longer / slower.")
    lines.append("  - `geometric` — within ±8 m of the apex coord. Driver was slowest right at the geometric apex.")
    lines.append("  - `late` — min speed ≥ 8 m *after* the apex coord. Late-apex line; rotating the car past the apex and getting on power earlier.")
    lines.append("")
    lines.append("### Brake / throttle / coast / overlap")
    lines.append("- **Brake onset (m)**: meters *before* apex where brake first crossed 5%. Larger = braked earlier (further from apex).")
    lines.append("- **Brake release (m)**: positive = released brake before apex (clean turn-in); negative = trail-braked past apex.")
    lines.append("- **Brake duration (m)**: total meters within the window with brake > 5%.")
    lines.append("- **Trail-brake past apex (m)**: portion of the brake duration that occurred after the apex coord.")
    lines.append("- **Throttle pickup (m)**: meters *after* apex where throttle first crossed 5%. Closer to 0 = back to power earlier.")
    lines.append("- **Throttle at apex (%)**: throttle position at the apex sample.")
    lines.append("- **Coast distance (m)**: meters with neither brake nor throttle above 5% — pure rolling.")
    lines.append("- **Overlap distance (m)**: meters with *both* brake AND throttle above 5% — left-foot braking or stab-and-go.")
    lines.append("- **Δ brake onset (m)**: positive = braked *later* (closer to apex) than best lap.")
    lines.append("- **Δ throttle pickup (m)**: negative = got back to throttle *earlier* (closer to apex) than best lap.")
    lines.append("")
    lines.append("### Steering")
    lines.append("- **Steer peak (°)**: maximum absolute steering angle within the corner window.")
    lines.append("- **Steer at apex (°)**: steering angle at the apex sample.")
    lines.append("- **Steer RMS rate (deg/s)**: root-mean-square of dθ/dt across the window. Lower = smoother hands.")
    lines.append("- **Steer reversals**: sign changes in steering rate within the window. High counts = busy / corrective hands.")
    lines.append("")
    lines.append("### G-forces")
    lines.append("- **Peak latG**: maximum |lateral G| in the window.")
    lines.append("- **Peak decelG / accelG**: maximum braking-direction and accel-direction longitudinal G.")
    lines.append("- **Peak combined G**: max √(latG² + longG²); a proxy for traction-circle usage at any one instant.")
    lines.append("")
    lines.append("### Time-series traces (per corner)")
    lines.append("- Rows are signed distance from the apex (m): negative = approach, 0 = apex, positive = exit.")
    lines.append("- Values are linearly interpolated between actual samples — NaN means no data at that offset (window edge).")
    lines.append("- Compare across laps: if speed is higher 60 m before apex on the best lap but the same at apex, the difference is in the braking zone, not corner speed.")
    lines.append("")
    lines.append("### Per-corner windows")
    lines.append("- Each sample in the lap is assigned to the *nearest* turn coordinate (Voronoi cell). The contiguous run of samples assigned to a turn that contains its apex sample is that corner's window.")
    lines.append("- This means brake/throttle events in long approaches get attributed to the corner being prepared for, even if they happen far from the apex coord.")
    return "\n".join(lines) + "\n"


def _collect_passages_for_turn(metrics_by_lap: dict[int, LapMetrics], turn_number: str) -> dict[int, TurnPassage]:
    """Return {lap_number: passage} for every on-pace lap that has this turn."""
    out: dict[int, TurnPassage] = {}
    for lap_num, lm in metrics_by_lap.items():
        for p in lm.passages:
            if p.turn_number == turn_number:
                out[lap_num] = p
                break
    return out


def _render_corner_section(
    *,
    turn_number: str,
    turn_ref: Turn | None,
    passages_for_turn: dict[int, TurnPassage],
    metrics_by_lap: dict[int, LapMetrics],
    lap_by_num: dict[int, Lap],
    best_lap_number: int,
    sorted_laps: list[int],
    df: pd.DataFrame,
) -> list[str]:
    """Render the full per-corner block: header, side-by-side, consistency, traces."""
    lines: list[str] = []
    section = passages_for_turn[sorted_laps[0]].section if sorted_laps else ""
    title = turn_ref.name if turn_ref else f"Turn {turn_number}"
    lines.append(f"### Turn {turn_number} — {title} ({section})")
    if turn_ref:
        lines.append("")
        lines.append(f"GPS: `{turn_ref.latitude}, {turn_ref.longitude}`")
    lines.append("")

    # ---- Side-by-side table for this turn -----------------------------------
    lines.append("**Per-lap summary (sorted fastest → slowest lap time)**")
    lines.append("")
    lines.append(
        "| Lap | Lap time | Apex t (s) | Apex mph | Min mph | Min-speed m from apex | Apex style | "
        "Peak latG | Peak combG | Brake max | Brake onset (m) | Brake release (m) | Brake dur (m) | Trail past apex (m) | "
        "Throttle min | Throttle@apex | Throttle pickup (m) | Coast (m) | Overlap (m) | Steer peak | Steer@apex | Steer RMS | Reversals | RPM@apex |"
    )
    lines.append("|" + "---|" * 24)
    for lap_num in sorted_laps:
        p = passages_for_turn.get(lap_num)
        if p is None:
            continue
        lap_obj = lap_by_num[lap_num]
        marker = "★" if lap_num == best_lap_number else ""
        lines.append(
            f"| L{lap_num}{marker} | {lap_obj.lap_time_str} | {_fmt(p.elapsed_in_lap_s, '.2f')} | "
            f"{_mph(p.speed_at_apex_mps)} | {_mph(p.min_speed_in_window_mps)} | "
            f"{_fmt(p.min_speed_m_from_apex, '+.1f')} | {p.apex_style} | "
            f"{_fmt(p.peak_lateral_g, '.2f')} | {_fmt(p.peak_combined_g, '.2f')} | "
            f"{_fmt(p.brake_max_pct, '.0f')}% | {_fmt(p.brake_onset_m_before_apex, '.1f')} | "
            f"{_fmt(p.brake_release_m_before_apex, '+.1f')} | {_fmt(p.brake_duration_m, '.1f')} | "
            f"{_fmt(p.trail_brake_past_apex_m, '.1f')} | "
            f"{_fmt(p.throttle_min_pct, '.0f')}% | {_fmt(p.throttle_at_apex_pct, '.0f')}% | "
            f"{_fmt(p.throttle_onset_m_after_apex, '.1f')} | {_fmt(p.coast_distance_m, '.1f')} | "
            f"{_fmt(p.overlap_distance_m, '.1f')} | {_fmt(p.steering_peak_deg, '.0f')}° | "
            f"{_fmt(p.steering_at_apex_deg, '+.0f')}° | {_fmt(p.steering_smoothness, '.0f')} | "
            f"{p.steering_reversals} | {_fmt(p.rpm_at_apex, '.0f')} |"
        )
    lines.append("")

    # ---- Consistency block --------------------------------------------------
    stats = corner_stats(metrics_by_lap, best_lap_number, turn_number)
    if stats:
        lines.append("**Consistency across on-pace laps** (best-lap value + where it ranks low→high among the samples)")
        lines.append("")
        lines.append("| Field | Min | Max | Median | Std | Best-lap value | Best-lap rank |")
        lines.append("|---|---|---|---|---|---|---|")
        # Order the most-coachable fields first.
        field_order = [
            "elapsed_in_lap_s",
            "speed_at_apex_mps",
            "min_speed_in_window_mps",
            "peak_lateral_g",
            "peak_longitudinal_decel_g",
            "brake_max_pct",
            "brake_onset_m_before_apex",
            "brake_release_m_before_apex",
            "trail_brake_past_apex_m",
            "throttle_min_pct",
            "throttle_onset_m_after_apex",
            "steering_peak_deg",
            "steering_smoothness",
            "steering_reversals",
        ]
        for field in field_order:
            s = stats.get(field)
            if s is None:
                continue
            n = max(len(passages_for_turn), 1)
            rank_str = f"{s.best_lap_rank_low_to_high}/{n}" if s.best_lap_rank_low_to_high else "—"
            spec = ".0f" if field in ("steering_reversals", "brake_max_pct") else ".2f"
            lines.append(
                f"| {field} | {_fmt(s.min_value, spec)} | {_fmt(s.max_value, spec)} | "
                f"{_fmt(s.median_value, spec)} | {_fmt(s.std_value, spec)} | "
                f"{_fmt(s.best_lap_value, spec)} | {rank_str} |"
            )
        lines.append("")

    # ---- Time-series traces -------------------------------------------------
    lines.append("**Time-series traces around apex** (signed meters from apex; one sub-table per signal; one column per lap, fastest left)")
    lines.append("")
    trace_per_lap: dict[int, pd.DataFrame] = {}
    for lap_num in sorted_laps:
        p = passages_for_turn.get(lap_num)
        if p is None:
            continue
        lap_obj = lap_by_num[lap_num]
        seg = lap_obj.slice(df).reset_index(drop=True)
        # apex_row_in_lap is already in seg-relative (post-reset) coords.
        trace = sample_trace_around_corner(seg, p.apex_row_in_lap, dist_min_m=-160, dist_max_m=100, step_m=10)
        if not trace.empty:
            trace_per_lap[lap_num] = trace
    if trace_per_lap:
        # Distance axis taken from the first trace (all use the same offsets).
        first = next(iter(trace_per_lap.values()))
        distances = first["dist_m"].tolist()
        signal_specs = [
            ("speed_mps", "Speed (mph)", lambda v: f"{v * MPS_TO_MPH:.1f}"),
            ("brake_pct", "Brake (%)", lambda v: f"{v:.0f}"),
            ("throttle_pct", "Throttle (%)", lambda v: f"{v:.0f}"),
            ("steering_deg", "Steering (°)", lambda v: f"{v:+.0f}"),
            ("lateral_g", "Lateral G", lambda v: f"{v:+.2f}"),
            ("longitudinal_g", "Longitudinal G", lambda v: f"{v:+.2f}"),
        ]
        for key, label, formatter in signal_specs:
            lines.append(f"_{label}_")
            lines.append("")
            laps_present = [n for n in sorted_laps if n in trace_per_lap]
            header = "| dist (m) | " + " | ".join(f"L{n}{'★' if n == best_lap_number else ''}" for n in laps_present) + " |"
            sep = "|---|" + "|".join(["---"] * len(laps_present)) + "|"
            lines.append(header)
            lines.append(sep)
            for i, d in enumerate(distances):
                row = [f"{int(d):+d}"]
                for n in laps_present:
                    v = float(trace_per_lap[n][key].iloc[i])
                    row.append(formatter(v) if np.isfinite(v) else "—")
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Weekend report
# ---------------------------------------------------------------------------

@dataclass
class _SessionDigest:
    meta: SessionMeta
    laps: list[Lap]
    best: Lap | None
    best_metrics: LapMetrics | None
    on_pace_count: int
    on_pace_median_s: float
    on_pace_std_s: float


def _digest(session_id: str, track: TrackRef) -> _SessionDigest:
    meta = load_session_meta(session_id)
    df = load_session_df(session_id)
    laps = detect_laps(df)
    classify_outliers(laps)
    best = session_best(laps)
    bm = compute_lap_metrics(best, df, track) if best else None
    pool = on_pace(laps)
    times = np.array([l.lap_time_s for l in pool]) if pool else np.array([])
    return _SessionDigest(
        meta=meta,
        laps=laps,
        best=best,
        best_metrics=bm,
        on_pace_count=len(pool),
        on_pace_median_s=float(np.median(times)) if times.size else float("nan"),
        on_pace_std_s=float(np.std(times)) if times.size else float("nan"),
    )


def write_weekend_report(track_slug: str, car_slug: str, weekend_id: str) -> Path:
    """Generate the weekend-rollup Markdown report."""
    track = find_track(track_slug)
    car = find_car(car_slug)
    entries = sessions_for(track_slug=track_slug, car_slug=car_slug, weekend_id=weekend_id)
    if not entries:
        raise ValueError(f"No sessions match track={track_slug} car={car_slug} weekend={weekend_id}")
    entries.sort(key=lambda e: e.get("start_time_utc") or "")

    digests = [_digest(e["session_id"], track) for e in entries]
    out_dir = report_dir_for(track_slug, car_slug)
    out_path = out_dir / f"weekend_{weekend_id}.md"
    md = _render_weekend_report(track, car, weekend_id, digests)
    out_path.write_text(md, encoding="utf-8")
    return out_path


def _render_weekend_report(track: TrackRef, car: CarRef, weekend_id: str, digests: list[_SessionDigest]) -> str:
    lines: list[str] = []
    lines.append(f"# Weekend report — {track.title} — {car.title} — `{weekend_id}`")
    lines.append("")
    lines.append(f"Sessions in this rollup: **{len(digests)}**")
    lines.append("")

    # ---- Session-by-session row -----------------------------------------------
    lines.append("## Sessions (chronological)")
    lines.append("")
    lines.append("| Session | Day | Start (local) | Tires | Weather | On-pace laps | Best lap | Median | Std dev |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for d in digests:
        weather = WeatherSnapshot(**d.meta.weather) if d.meta.weather else None
        weather_str = conditions_summary(weather) if weather else "—"
        tire_str = d.meta.tires.compound if isinstance(d.meta.tires, type(d.meta.tires)) and d.meta.tires.compound else (
            (d.meta.tires.compound if d.meta.tires else "—") or "—"
        )
        lines.append(
            f"| {d.meta.session_label or d.meta.session_id} | {d.meta.day_of_weekend} | "
            f"{_time_of_day(d.meta.start_time_utc)} | {tire_str} | {weather_str} | "
            f"{d.on_pace_count} | {d.best.lap_time_str if d.best else '—'} | "
            f"{format_lap_time(d.on_pace_median_s) if np.isfinite(d.on_pace_median_s) else '—'} | "
            f"{_fmt(d.on_pace_std_s, '.3f')}s |"
        )
    lines.append("")

    # ---- Inter-session gaps (paddock time between CSVs) ----------------------
    sorted_digests = sorted(digests, key=lambda d: d.meta.start_time_utc or "")
    inter_gaps: list[tuple[_SessionDigest, _SessionDigest, float]] = []
    for prev, curr in zip(sorted_digests, sorted_digests[1:], strict=False):
        try:
            prev_end = datetime.fromisoformat((prev.meta.start_time_utc or "").replace("Z", "+00:00")) + \
                       timedelta(seconds=prev.meta.duration_s)
            curr_start = datetime.fromisoformat((curr.meta.start_time_utc or "").replace("Z", "+00:00"))
            gap_s = (curr_start - prev_end).total_seconds()
            if np.isfinite(gap_s) and gap_s > 60.0:
                inter_gaps.append((prev, curr, gap_s))
        except (ValueError, AttributeError):
            continue
    if inter_gaps:
        lines.append("## Between-session gaps (paddock / overnight time)")
        lines.append("")
        lines.append("Time the car sat between successive imported CSVs. Each gap is a full cold-start for the next session. **Durations are in seconds.**")
        lines.append("")
        lines.append("| From | To | Gap |")
        lines.append("|---|---|---|")
        for prev, curr, gap_s in inter_gaps:
            if gap_s >= 3600:
                human = f"~{gap_s / 3600:.1f} h"
            elif gap_s >= 60:
                human = f"~{gap_s / 60:.1f} min"
            else:
                human = f"~{gap_s:.0f} s"
            lines.append(
                f"| {prev.meta.session_label or prev.meta.session_id} ({prev.meta.day_of_weekend}) | "
                f"{curr.meta.session_label or curr.meta.session_id} ({curr.meta.day_of_weekend}) | "
                f"**{gap_s:.0f} s** ({human}) |"
            )
        lines.append("")

    # ---- Weekend best summary -------------------------------------------------
    bests = [d for d in digests if d.best is not None]
    if bests:
        overall = min(bests, key=lambda d: d.best.lap_time_s)
        lines.append("## Weekend best")
        lines.append("")
        lines.append(f"- **Best lap**: {overall.best.lap_time_str} — session `{overall.meta.session_label or overall.meta.session_id}` "
                     f"({overall.meta.day_of_weekend}, lap {overall.best.lap_number})")
        lines.append("")

    # ---- Day-over-day evolution -----------------------------------------------
    by_day: dict[str, list[_SessionDigest]] = {}
    for d in digests:
        by_day.setdefault(d.meta.day_of_weekend or "—", []).append(d)
    if len(by_day) > 1:
        lines.append("## Day-over-day evolution")
        lines.append("")
        lines.append("| Day | Sessions | Best of day | Median of day | Conditions trend |")
        lines.append("|---|---|---|---|---|")
        for day, day_digests in sorted(by_day.items()):
            day_best = min((d.best.lap_time_s for d in day_digests if d.best), default=float("nan"))
            day_median = float(np.median([d.on_pace_median_s for d in day_digests if np.isfinite(d.on_pace_median_s)])) if day_digests else float("nan")
            conds = []
            for d in day_digests:
                if d.meta.weather:
                    w = WeatherSnapshot(**d.meta.weather)
                    if w.temperature_c is not None:
                        conds.append(f"{w.temperature_c:.0f}°C")
            cond_str = " → ".join(conds) if conds else "—"
            lines.append(
                f"| {day} | {len(day_digests)} | {format_lap_time(day_best) if np.isfinite(day_best) else '—'} | "
                f"{format_lap_time(day_median) if np.isfinite(day_median) else '—'} | {cond_str} |"
            )
        lines.append("")

    # ---- Per-corner spread across the weekend ---------------------------------
    apex_speeds: dict[str, list[tuple[str, float]]] = {}
    for d in digests:
        if d.best_metrics is None:
            continue
        for p in d.best_metrics.passages:
            apex_speeds.setdefault(p.turn_number, []).append((d.meta.session_label or d.meta.session_id, p.speed_at_apex_mps))
    if apex_speeds:
        lines.append("## Apex speed spread (best lap of each session)")
        lines.append("")
        lines.append("Per-turn min/max apex mph across all session bests this weekend. Wide spreads point at corners where the day or conditions mattered.")
        lines.append("")
        lines.append("| T | Min apex mph (session) | Max apex mph (session) | Spread mph |")
        lines.append("|---|---|---|---|")
        # Preserve track turn order.
        turn_order = [t.number for t in track.turns]
        for tn in turn_order:
            samples = apex_speeds.get(tn, [])
            valid = [(label, v) for label, v in samples if np.isfinite(v)]
            if not valid:
                continue
            valid.sort(key=lambda x: x[1])
            lo_label, lo = valid[0]
            hi_label, hi = valid[-1]
            lines.append(f"| {tn} | {lo * MPS_TO_MPH:.1f} (`{lo_label}`) | {hi * MPS_TO_MPH:.1f} (`{hi_label}`) | {(hi - lo) * MPS_TO_MPH:.1f} |")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("### Reading guide")
    lines.append("")
    lines.append("- Use session order plus weather/tires to reason about whether faster laps came from track evolution, tires, conditions, or technique.")
    lines.append("- A widening spread of apex speeds across sessions usually means a corner is being learned or tire grip is changing.")
    lines.append("- Std dev across on-pace laps is the cleanest consistency signal; compare it between morning and afternoon sessions.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Cross-weekend progression report
# ---------------------------------------------------------------------------

def write_progression_report(track_slug: str, car_slug: str) -> Path:
    """Generate the cross-weekend progression report."""
    track = find_track(track_slug)
    car = find_car(car_slug)
    weekends = weekend_ids(track_slug=track_slug, car_slug=car_slug)
    if not weekends:
        raise ValueError(f"No sessions match track={track_slug} car={car_slug}")

    per_weekend: list[tuple[str, list[_SessionDigest]]] = []
    for wid in weekends:
        entries = sessions_for(track_slug=track_slug, car_slug=car_slug, weekend_id=wid)
        entries.sort(key=lambda e: e.get("start_time_utc") or "")
        per_weekend.append((wid, [_digest(e["session_id"], track) for e in entries]))

    out_dir = report_dir_for(track_slug, car_slug)
    out_path = out_dir / "progression.md"
    md = _render_progression_report(track, car, per_weekend)
    out_path.write_text(md, encoding="utf-8")
    return out_path


def _render_progression_report(track: TrackRef, car: CarRef, per_weekend: list[tuple[str, list[_SessionDigest]]]) -> str:
    lines: list[str] = []
    lines.append(f"# Progression report — {track.title} — {car.title}")
    lines.append("")
    lines.append(f"Weekends tracked: **{len(per_weekend)}**")
    lines.append("")

    # ---- Headline trend table -------------------------------------------------
    lines.append("## Best lap by weekend")
    lines.append("")
    lines.append("| Weekend | Sessions | Best lap | Median (across sessions) | Tires (best session) | Weather (best session) |")
    lines.append("|---|---|---|---|---|---|")
    weekend_bests: list[tuple[str, _SessionDigest]] = []
    for wid, digests in per_weekend:
        bests = [d for d in digests if d.best is not None]
        if not bests:
            lines.append(f"| {wid} | {len(digests)} | — | — | — | — |")
            continue
        overall = min(bests, key=lambda d: d.best.lap_time_s)
        medians = [d.on_pace_median_s for d in digests if np.isfinite(d.on_pace_median_s)]
        median_str = format_lap_time(float(np.median(medians))) if medians else "—"
        weather = WeatherSnapshot(**overall.meta.weather) if overall.meta.weather else None
        weather_str = conditions_summary(weather) if weather else "—"
        tire_str = overall.meta.tires.compound or "—" if overall.meta.tires else "—"
        lines.append(
            f"| {wid} | {len(digests)} | {overall.best.lap_time_str} | {median_str} | {tire_str} | {weather_str} |"
        )
        weekend_bests.append((wid, overall))
    lines.append("")

    # ---- Per-turn apex-speed evolution ----------------------------------------
    if weekend_bests:
        lines.append("## Apex speed per turn — best lap of each weekend")
        lines.append("")
        header_weekends = [wid for wid, _ in weekend_bests]
        header = "| T | Section | " + " | ".join(header_weekends) + " | First→Last Δ mph |"
        sep = "|---|---|" + "|".join(["---"] * len(header_weekends)) + "|---|"
        lines.append(header)
        lines.append(sep)
        turn_order = [(t.number, t.section) for t in track.turns]
        per_turn_per_weekend: dict[str, dict[str, float]] = {tn: {} for tn, _ in turn_order}
        for wid, digest in weekend_bests:
            if digest.best_metrics is None:
                continue
            for p in digest.best_metrics.passages:
                per_turn_per_weekend.setdefault(p.turn_number, {})[wid] = p.speed_at_apex_mps
        for tn, section in turn_order:
            row = [tn, section]
            speeds: list[float] = []
            for wid in header_weekends:
                v = per_turn_per_weekend.get(tn, {}).get(wid)
                if v is None or not np.isfinite(v):
                    row.append("—")
                else:
                    row.append(f"{v * MPS_TO_MPH:.1f}")
                    speeds.append(v)
            if len(speeds) >= 2 and np.isfinite(speeds[0]) and np.isfinite(speeds[-1]):
                delta = (speeds[-1] - speeds[0]) * MPS_TO_MPH
                row.append(f"{delta:+.1f}")
            else:
                row.append("—")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # ---- Sector time evolution ------------------------------------------------
    sections_set: set[str] = set()
    for wid, digest in weekend_bests:
        if digest.best_metrics is None:
            continue
        for k in digest.best_metrics.sector_times_s:
            if "→" not in k:
                sections_set.add(k)
    if sections_set:
        lines.append("## Named-section times — best lap of each weekend")
        lines.append("")
        header = "| Section | " + " | ".join(wid for wid, _ in weekend_bests) + " | First→Last Δ s |"
        sep = "|---|" + "|".join(["---"] * len(weekend_bests)) + "|---|"
        lines.append(header)
        lines.append(sep)
        for section in sorted(sections_set):
            row: list[str] = [section]
            vals: list[float] = []
            for wid, digest in weekend_bests:
                t = (digest.best_metrics.sector_times_s if digest.best_metrics else {}).get(section)
                if t is None or not np.isfinite(t):
                    row.append("—")
                else:
                    row.append(f"{t:.3f}")
                    vals.append(t)
            if len(vals) >= 2:
                row.append(f"{vals[-1] - vals[0]:+.3f}")
            else:
                row.append("—")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # ---- Technique markers across weekends -------------------------------------
    lines.append("## Technique markers — best lap of each weekend")
    lines.append("")
    lines.append("Whole-lap inputs and behavior; trends here suggest changes in style or commitment.")
    lines.append("")
    lines.append("| Weekend | Best | %throttle | %brake | %coasting | %overlap | Peak latG | Peak decelG | Steer smoothness |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for wid, digest in weekend_bests:
        bm = digest.best_metrics
        if bm is None:
            lines.append(f"| {wid} | — | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {wid} | {digest.best.lap_time_str} | "
            f"{_fmt(bm.pct_lap_on_throttle, '.1f')}% | "
            f"{_fmt(bm.pct_lap_on_brake, '.1f')}% | "
            f"{_fmt(bm.pct_lap_coasting, '.1f')}% | "
            f"{_fmt(bm.pct_lap_overlap, '.1f')}% | "
            f"{_fmt(bm.peak_lateral_g, '.2f')} | "
            f"{_fmt(bm.peak_longitudinal_decel_g, '.2f')} | "
            f"{_fmt(bm.steering_smoothness, '.1f')} |"
        )
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("### Reading guide")
    lines.append("")
    lines.append("- A negative First→Last Δ mph at a corner with **also** negative section Δ s = real improvement.")
    lines.append("- A positive First→Last Δ mph with worse section Δ s usually means later braking but worse exit — net loss.")
    lines.append("- Rising %throttle and falling %coasting across weekends = more confidence; pair with consistent section times to confirm it isn't just risk.")
    lines.append("- Steering smoothness trending down (smaller number) is usually a sign of cleaner inputs once corner familiarity grows.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Cross-weekend comparison report
# ---------------------------------------------------------------------------

@dataclass
class _WeekendDigest:
    """Aggregate of one weekend's sessions: best lap, optimal, sector bests, tires/weather summary."""

    weekend_id: str
    sessions: list[_SessionDigest]
    on_pace_lap_count: int
    total_lap_count: int
    best_lap_s: float
    best_lap_str: str
    best_lap_session_label: str
    best_lap_day: str
    best_lap_metrics: LapMetrics | None
    optimal_lap_s: float                              # sum of fastest fixed-sector times across all on-pace laps
    sector_best_s: dict[int, float]                   # sector_number → fastest time (seconds)
    sector_best_source: dict[int, str]                # sector_number → "L<lap>@<session>" label
    sector_names: dict[int, str]                      # sector_number → name
    tire_compounds: list[str]                         # distinct compounds used this weekend
    days: list[str]                                   # distinct day labels
    weather_summary: str
    temp_range_c: tuple[float, float] | None
    earliest_start_utc: str
    can_dropout_flagged_laps: int


def _digest_weekend(track: TrackRef, weekend_id: str, entries: list[dict]) -> _WeekendDigest:
    """Build a :class:`_WeekendDigest` from all sessions in one weekend."""
    session_digests = [_digest(e["session_id"], track) for e in entries]

    # Per-lap metrics across the whole weekend so we can compute weekend-level optima.
    all_lap_records: list[tuple[_SessionDigest, Lap, LapMetrics]] = []
    can_dropout_flagged = 0
    for sd in session_digests:
        df = load_session_df(sd.meta.session_id)
        laps_full = detect_laps(df)
        classify_outliers(laps_full)
        for lap in on_pace(laps_full):
            lm = compute_lap_metrics(lap, df, track)
            if lm.can_dropout_flag:
                can_dropout_flagged += 1
            all_lap_records.append((sd, lap, lm))

    # Weekend best lap.
    if all_lap_records:
        sd_best, lap_best, lm_best = min(all_lap_records, key=lambda r: r[1].lap_time_s)
        best_lap_s = float(lap_best.lap_time_s)
        best_lap_str = lap_best.lap_time_str
        best_label = sd_best.meta.session_label or sd_best.meta.session_id
        best_day = sd_best.meta.day_of_weekend
        best_lap_metrics = lm_best
    else:
        best_lap_s = float("nan")
        best_lap_str = "—"
        best_label = "—"
        best_day = "—"
        best_lap_metrics = None

    # Fixed-sector bests across all on-pace laps in the weekend.
    sector_best_s: dict[int, float] = {}
    sector_best_source: dict[int, str] = {}
    sector_names: dict[int, str] = {}
    for sd, lap, lm in all_lap_records:
        for fs in lm.fixed_sectors:
            sector_names[fs.number] = fs.name
            if not np.isfinite(fs.time_s):
                continue
            prev = sector_best_s.get(fs.number)
            if prev is None or fs.time_s < prev:
                sector_best_s[fs.number] = float(fs.time_s)
                label = sd.meta.session_label or sd.meta.session_id
                sector_best_source[fs.number] = f"L{lap.lap_number}@{label}"
    optimal_lap_s = float(sum(sector_best_s.values())) if sector_best_s else float("nan")

    on_pace_count = sum(d.on_pace_count for d in session_digests)
    total_lap_count = sum(len(d.laps) for d in session_digests)

    # Tire / day / weather summary.
    compounds = []
    seen_compounds: set[str] = set()
    for d in session_digests:
        c = d.meta.tires.compound if isinstance(d.meta.tires, TireSpec) else (d.meta.tires.get("compound") if d.meta.tires else "")  # type: ignore[union-attr]
        if c and c not in seen_compounds:
            compounds.append(c)
            seen_compounds.add(c)
    days = sorted({d.meta.day_of_weekend for d in session_digests if d.meta.day_of_weekend})

    temps_c: list[float] = []
    weather_bits: list[str] = []
    for d in session_digests:
        if not d.meta.weather:
            continue
        w = WeatherSnapshot(**d.meta.weather)
        if w.temperature_c is not None:
            temps_c.append(w.temperature_c)
        weather_bits.append(conditions_summary(w))
    temp_range = (min(temps_c), max(temps_c)) if temps_c else None
    if temp_range:
        weather_summary = f"{temp_range[0]:.1f}–{temp_range[1]:.1f}°C across sessions"
    else:
        weather_summary = "—"

    earliest = min((d.meta.start_time_utc for d in session_digests if d.meta.start_time_utc), default="")

    return _WeekendDigest(
        weekend_id=weekend_id,
        sessions=session_digests,
        on_pace_lap_count=on_pace_count,
        total_lap_count=total_lap_count,
        best_lap_s=best_lap_s,
        best_lap_str=best_lap_str,
        best_lap_session_label=best_label,
        best_lap_day=best_day,
        best_lap_metrics=best_lap_metrics,
        optimal_lap_s=optimal_lap_s,
        sector_best_s=sector_best_s,
        sector_best_source=sector_best_source,
        sector_names=sector_names,
        tire_compounds=compounds,
        days=days,
        weather_summary=weather_summary,
        temp_range_c=temp_range,
        earliest_start_utc=earliest,
        can_dropout_flagged_laps=can_dropout_flagged,
    )


def write_weekend_comparison_report(track_slug: str, car_slug: str, weekend_id: str) -> Path:
    """Generate the ``this weekend vs every prior weekend`` Markdown report."""
    track = find_track(track_slug)
    car = find_car(car_slug)

    # All weekends, chronological by earliest session start.
    all_wids = weekend_ids(track_slug=track_slug, car_slug=car_slug)
    if weekend_id not in all_wids:
        raise ValueError(f"Weekend {weekend_id} has no sessions for {track_slug}/{car_slug}")
    if not all_wids:
        raise ValueError(f"No weekends found for {track_slug}/{car_slug}")

    digests: dict[str, _WeekendDigest] = {}
    for wid in all_wids:
        entries = sessions_for(track_slug=track_slug, car_slug=car_slug, weekend_id=wid)
        digests[wid] = _digest_weekend(track, wid, entries)

    # Order: chronological by earliest_start_utc, then ensure this weekend is last for narrative purposes.
    ordered = sorted(digests.values(), key=lambda d: d.earliest_start_utc or "")
    this_idx = next((i for i, d in enumerate(ordered) if d.weekend_id == weekend_id), -1)
    if this_idx < 0:
        raise ValueError(f"Weekend {weekend_id} missing from ordered digests")
    this_w = ordered[this_idx]
    priors = [d for d in ordered if d.weekend_id != weekend_id]

    out_dir = report_dir_for(track_slug, car_slug)
    out_path = out_dir / f"weekend_vs_prior_{weekend_id}.md"
    md = _render_comparison_report(track, car, this_w, priors, all_ordered=ordered)
    out_path.write_text(md, encoding="utf-8")
    return out_path


def _render_comparison_report(
    track: TrackRef,
    car: CarRef,
    this_w: _WeekendDigest,
    priors: list[_WeekendDigest],
    all_ordered: list[_WeekendDigest],
) -> str:
    lines: list[str] = []
    lines.append(f"# Weekend comparison report — {track.title} — {car.title} — `{this_w.weekend_id}`")
    lines.append("")
    if priors:
        prior_str = ", ".join(f"`{d.weekend_id}`" for d in priors)
        lines.append(f"This weekend vs {len(priors)} prior weekend(s): {prior_str}.")
    else:
        lines.append("_No prior weekends to compare against — this is the first weekend at this track + car combination._")
    lines.append("")

    # ---- This weekend headline ------------------------------------------------
    lines.append("## This weekend at a glance")
    lines.append("")
    lines.append(f"- **Weekend ID**: `{this_w.weekend_id}`")
    lines.append(f"- **Days**: {', '.join(this_w.days) if this_w.days else '—'}")
    lines.append(f"- **Sessions**: {len(this_w.sessions)}  |  **Total laps**: {this_w.total_lap_count}  |  **On-pace laps**: {this_w.on_pace_lap_count}")
    lines.append(f"- **Tires (distinct compounds this weekend)**: {', '.join(this_w.tire_compounds) if this_w.tire_compounds else '—'}")
    lines.append(f"- **Conditions**: {this_w.weather_summary}")
    if this_w.can_dropout_flagged_laps:
        lines.append(f"- **Data-quality flag**: {this_w.can_dropout_flagged_laps} lap(s) had ≥25% CAN-bus dropout; brake/throttle/RPM analysis on those laps is unreliable.")
    lines.append(f"- **Weekend best lap**: **{this_w.best_lap_str}** — `{this_w.best_lap_session_label}` ({this_w.best_lap_day})")
    if np.isfinite(this_w.optimal_lap_s):
        lines.append(f"- **Weekend theoretical optimum**: {format_lap_time(this_w.optimal_lap_s)} (sum of fastest fixed-sector times across all on-pace laps)")
        gap = this_w.best_lap_s - this_w.optimal_lap_s
        lines.append(f"- **Time left on the best lap**: {gap * 1000:+.0f} ms")
    lines.append("")

    # ---- Session list -----------------------------------------------------------
    lines.append("## Sessions this weekend (chronological)")
    lines.append("")
    lines.append("| Session | Day | Start (local) | Tires | Compound age | Weather | On-pace laps | Best lap | Median |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for d in sorted(this_w.sessions, key=lambda x: x.meta.start_time_utc or ""):
        tires = d.meta.tires if isinstance(d.meta.tires, TireSpec) else TireSpec(**(d.meta.tires or {}))
        weather_str = conditions_summary(WeatherSnapshot(**d.meta.weather)) if d.meta.weather else "—"
        lines.append(
            f"| {d.meta.session_label or d.meta.session_id} | {d.meta.day_of_weekend} | "
            f"{_time_of_day(d.meta.start_time_utc)} | {tires.compound or '—'} | {tires.age_heat_cycles or '—'} | "
            f"{weather_str} | {d.on_pace_count} | {d.best.lap_time_str if d.best else '—'} | "
            f"{format_lap_time(d.on_pace_median_s) if np.isfinite(d.on_pace_median_s) else '—'} |"
        )
    lines.append("")

    # ---- Weekend best lap — sector breakdown vs weekend optimum ---------------
    if this_w.best_lap_metrics and this_w.best_lap_metrics.fixed_sectors:
        lines.append("## Weekend best lap — sector breakdown vs weekend optimum")
        lines.append("")
        lines.append("How the best lap stacks against the fastest sectors found anywhere in the weekend. A `★` marks sectors where the best lap *was* the fastest.")
        lines.append("")
        lines.append("| # | Sector | Best-lap time (s) | Optimal time (s) | Δ s | Optimal from |")
        lines.append("|---|---|---|---|---|---|")
        for fs in this_w.best_lap_metrics.fixed_sectors:
            opt_s = this_w.sector_best_s.get(fs.number, float("nan"))
            opt_src = this_w.sector_best_source.get(fs.number, "—")
            delta = fs.time_s - opt_s if np.isfinite(fs.time_s) and np.isfinite(opt_s) else float("nan")
            star = " ★" if np.isfinite(delta) and abs(delta) < 1e-6 else ""
            lines.append(
                f"| {fs.number} | {fs.name}{star} | {_fmt(fs.time_s, '.3f')} | {_fmt(opt_s, '.3f')} | "
                f"{_fmt_signed(delta)} | {opt_src} |"
            )
        # Totals row.
        opt_total = this_w.optimal_lap_s
        best_total = this_w.best_lap_s
        delta_total = best_total - opt_total if np.isfinite(best_total) and np.isfinite(opt_total) else float("nan")
        lines.append(f"| | **Total** | {_fmt(best_total, '.3f')} | {_fmt(opt_total, '.3f')} | {_fmt_signed(delta_total)} | |")
        lines.append("")

    if not priors:
        lines.append("---")
        lines.append("")
        lines.append("_No prior weekends yet. Once you import a second weekend at this track + car, this report will grow a cross-weekend section._")
        return "\n".join(lines) + "\n"

    # ---- 5-weekend-style timeline -------------------------------------------
    lines.append("## All weekends at this track + car (chronological)")
    lines.append("")
    lines.append("| Weekend | Days | Tires | Conditions | Sessions | On-pace laps | Best lap | Optimal | Gap |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for d in all_ordered:
        marker = " **(this weekend)**" if d.weekend_id == this_w.weekend_id else ""
        gap_str = _fmt_signed(d.best_lap_s - d.optimal_lap_s) if np.isfinite(d.best_lap_s) and np.isfinite(d.optimal_lap_s) else "—"
        lines.append(
            f"| `{d.weekend_id}`{marker} | {', '.join(d.days) or '—'} | "
            f"{', '.join(d.tire_compounds) or '—'} | {d.weather_summary} | "
            f"{len(d.sessions)} | {d.on_pace_lap_count} | {d.best_lap_str} | "
            f"{format_lap_time(d.optimal_lap_s) if np.isfinite(d.optimal_lap_s) else '—'} | {gap_str} |"
        )
    lines.append("")

    # ---- Cross-weekend sector comparison ------------------------------------
    sector_numbers = sorted(this_w.sector_best_s.keys())
    if sector_numbers:
        lines.append("## Cross-weekend sector comparison (each weekend's optimal sector time)")
        lines.append("")
        lines.append("Each cell shows the fastest time recorded in that weekend for that sector (not necessarily on the best lap). "
                     "The series-best row marks the all-time fastest by **bold** and notes which weekend holds it.")
        lines.append("")
        header = "| # | Sector | " + " | ".join(_short_wid(d.weekend_id) for d in all_ordered) + " | Series best | Owner |"
        sep = "|---|---|" + "|".join(["---"] * len(all_ordered)) + "|---|---|"
        lines.append(header)
        lines.append(sep)
        for snum in sector_numbers:
            sname = this_w.sector_names.get(snum, "?")
            row: list[str] = [str(snum), sname]
            best_overall = float("inf")
            owner = "—"
            for d in all_ordered:
                v = d.sector_best_s.get(snum, float("nan"))
                if np.isfinite(v) and v < best_overall:
                    best_overall = v
                    owner = _short_wid(d.weekend_id)
            for d in all_ordered:
                v = d.sector_best_s.get(snum, float("nan"))
                if not np.isfinite(v):
                    row.append("—")
                elif np.isfinite(best_overall) and abs(v - best_overall) < 1e-6:
                    row.append(f"**{v:.3f}**")
                else:
                    row.append(f"{v:.3f}")
            row.append(f"**{best_overall:.3f}**" if np.isfinite(best_overall) else "—")
            row.append(owner)
            lines.append("| " + " | ".join(row) + " |")
        # Totals row: best lap and optimal across all weekends.
        opt_row = ["—", "Optimal total"]
        best_opt = float("inf")
        opt_owner = "—"
        for d in all_ordered:
            v = d.optimal_lap_s
            if np.isfinite(v) and v < best_opt:
                best_opt = v
                opt_owner = _short_wid(d.weekend_id)
        for d in all_ordered:
            v = d.optimal_lap_s
            if not np.isfinite(v):
                opt_row.append("—")
            elif abs(v - best_opt) < 1e-6:
                opt_row.append(f"**{format_lap_time(v)}**")
            else:
                opt_row.append(format_lap_time(v))
        opt_row.append(f"**{format_lap_time(best_opt)}**" if np.isfinite(best_opt) else "—")
        opt_row.append(opt_owner)
        lines.append("| " + " | ".join(opt_row) + " |")
        best_row = ["—", "Best lap"]
        best_lap = float("inf")
        bl_owner = "—"
        for d in all_ordered:
            v = d.best_lap_s
            if np.isfinite(v) and v < best_lap:
                best_lap = v
                bl_owner = _short_wid(d.weekend_id)
        for d in all_ordered:
            v = d.best_lap_s
            if not np.isfinite(v):
                best_row.append("—")
            elif abs(v - best_lap) < 1e-6:
                best_row.append(f"**{format_lap_time(v)}**")
            else:
                best_row.append(format_lap_time(v))
        best_row.append(f"**{format_lap_time(best_lap)}**" if np.isfinite(best_lap) else "—")
        best_row.append(bl_owner)
        lines.append("| " + " | ".join(best_row) + " |")
        lines.append("")

    # ---- Headline deltas vs each prior weekend ------------------------------
    lines.append("## Headline deltas vs each prior weekend")
    lines.append("")
    for prior in reversed(priors):  # most recent prior first
        lines.append(f"### vs `{prior.weekend_id}`")
        lines.append("")
        lines.append("| Metric | This weekend | Prior weekend | Δ |")
        lines.append("|---|---|---|---|")
        delta_best = this_w.best_lap_s - prior.best_lap_s if np.isfinite(this_w.best_lap_s) and np.isfinite(prior.best_lap_s) else float("nan")
        delta_opt = this_w.optimal_lap_s - prior.optimal_lap_s if np.isfinite(this_w.optimal_lap_s) and np.isfinite(prior.optimal_lap_s) else float("nan")
        lines.append(f"| Best lap | {this_w.best_lap_str} | {prior.best_lap_str} | {_fmt_signed(delta_best)} s |")
        lines.append(f"| Optimal | {format_lap_time(this_w.optimal_lap_s) if np.isfinite(this_w.optimal_lap_s) else '—'} | "
                     f"{format_lap_time(prior.optimal_lap_s) if np.isfinite(prior.optimal_lap_s) else '—'} | {_fmt_signed(delta_opt)} s |")
        lines.append(f"| Conditions | {this_w.weather_summary} | {prior.weather_summary} | — |")
        lines.append(f"| Tires | {', '.join(this_w.tire_compounds) or '—'} | {', '.join(prior.tire_compounds) or '—'} | "
                     f"{'**changed**' if set(this_w.tire_compounds) != set(prior.tire_compounds) else 'same compound'} |")
        lines.append(f"| Sessions / on-pace laps | {len(this_w.sessions)} / {this_w.on_pace_lap_count} | "
                     f"{len(prior.sessions)} / {prior.on_pace_lap_count} | — |")
        lines.append("")

        # Sector deltas (fixed sectors, optimal per weekend).
        if sector_numbers:
            lines.append("**Sector-by-sector delta (optimal time per weekend):**")
            lines.append("")
            lines.append("| # | Sector | This weekend (s) | Prior (s) | Δ s | Δ % |")
            lines.append("|---|---|---|---|---|---|")
            sector_deltas: list[tuple[int, str, float]] = []
            for snum in sector_numbers:
                tv = this_w.sector_best_s.get(snum, float("nan"))
                pv = prior.sector_best_s.get(snum, float("nan"))
                if not (np.isfinite(tv) and np.isfinite(pv)):
                    lines.append(f"| {snum} | {this_w.sector_names.get(snum, '?')} | {_fmt(tv, '.3f')} | {_fmt(pv, '.3f')} | — | — |")
                    continue
                delta = tv - pv
                pct = (delta / pv * 100.0) if pv > 0 else float("nan")
                lines.append(f"| {snum} | {this_w.sector_names.get(snum, '?')} | {tv:.3f} | {pv:.3f} | {_fmt_signed(delta)} | {_fmt_signed(pct, '+.2f')}% |")
                sector_deltas.append((snum, this_w.sector_names.get(snum, "?"), delta))
            lines.append("")
            if sector_deltas:
                improved = sorted([s for s in sector_deltas if s[2] < 0], key=lambda x: x[2])[:3]
                regressed = sorted([s for s in sector_deltas if s[2] > 0], key=lambda x: -x[2])[:3]
                if improved:
                    lines.append("**Top sector improvements:** " + ", ".join(f"S{n} {nm} ({d * 1000:+.0f}ms)" for n, nm, d in improved))
                if regressed:
                    lines.append("**Top sector regressions:** " + ", ".join(f"S{n} {nm} ({d * 1000:+.0f}ms)" for n, nm, d in regressed))
                lines.append("")

        # Per-corner apex-speed deltas on the best lap of each weekend.
        if this_w.best_lap_metrics and prior.best_lap_metrics:
            lines.append("**Per-corner apex-speed delta (best lap of each weekend):**")
            lines.append("")
            lines.append("| T | Section | This (mph) | Prior (mph) | Δ mph |")
            lines.append("|---|---|---|---|---|")
            prior_by_num = {p.turn_number: p for p in prior.best_lap_metrics.passages}
            apex_deltas: list[tuple[str, str, float]] = []
            for p in this_w.best_lap_metrics.passages:
                pp = prior_by_num.get(p.turn_number)
                if pp is None or not (np.isfinite(p.speed_at_apex_mps) and np.isfinite(pp.speed_at_apex_mps)):
                    lines.append(f"| {p.turn_number} | {p.section} | {_mph(p.speed_at_apex_mps)} | "
                                 f"{_mph(pp.speed_at_apex_mps) if pp else '—'} | — |")
                    continue
                d_mph = (p.speed_at_apex_mps - pp.speed_at_apex_mps) * MPS_TO_MPH
                lines.append(f"| {p.turn_number} | {p.section} | {_mph(p.speed_at_apex_mps)} | "
                             f"{_mph(pp.speed_at_apex_mps)} | {d_mph:+.1f} |")
                apex_deltas.append((p.turn_number, p.section, d_mph))
            lines.append("")
            if apex_deltas:
                top_imp = sorted([d for d in apex_deltas if d[2] > 0], key=lambda x: -x[2])[:5]
                top_reg = sorted([d for d in apex_deltas if d[2] < 0], key=lambda x: x[2])[:5]
                if top_imp:
                    lines.append("**Apex-speed improvements (top 5):** " + ", ".join(f"T{n} {sec} ({d:+.1f} mph)" for n, sec, d in top_imp))
                if top_reg:
                    lines.append("**Apex-speed regressions (top 5):** " + ", ".join(f"T{n} {sec} ({d:+.1f} mph)" for n, sec, d in top_reg))
                lines.append("")

        # Technique markers delta on best lap.
        if this_w.best_lap_metrics and prior.best_lap_metrics:
            lines.append("**Technique markers (best lap each):**")
            lines.append("")
            lines.append("| Metric | This | Prior | Δ |")
            lines.append("|---|---|---|---|")
            t_m = this_w.best_lap_metrics
            p_m = prior.best_lap_metrics
            for label, t_v, p_v, fmt in [
                ("Peak latG", t_m.peak_lateral_g, p_m.peak_lateral_g, ".2f"),
                ("Peak decelG", t_m.peak_longitudinal_decel_g, p_m.peak_longitudinal_decel_g, ".2f"),
                ("Peak accelG", t_m.peak_longitudinal_accel_g, p_m.peak_longitudinal_accel_g, ".2f"),
                ("Max mph", t_m.max_speed_mps * MPS_TO_MPH if np.isfinite(t_m.max_speed_mps) else float("nan"),
                 p_m.max_speed_mps * MPS_TO_MPH if np.isfinite(p_m.max_speed_mps) else float("nan"), ".1f"),
                ("Min mph", t_m.min_speed_mps * MPS_TO_MPH if np.isfinite(t_m.min_speed_mps) else float("nan"),
                 p_m.min_speed_mps * MPS_TO_MPH if np.isfinite(p_m.min_speed_mps) else float("nan"), ".1f"),
                ("% throttle", t_m.pct_lap_on_throttle, p_m.pct_lap_on_throttle, ".1f"),
                ("% brake", t_m.pct_lap_on_brake, p_m.pct_lap_on_brake, ".1f"),
                ("% coast", t_m.pct_lap_coasting, p_m.pct_lap_coasting, ".1f"),
                ("% overlap", t_m.pct_lap_overlap, p_m.pct_lap_overlap, ".1f"),
                ("Steer RMS", t_m.steering_smoothness, p_m.steering_smoothness, ".1f"),
            ]:
                delta = (t_v - p_v) if (np.isfinite(t_v) and np.isfinite(p_v)) else float("nan")
                lines.append(f"| {label} | {_fmt(t_v, fmt)} | {_fmt(p_v, fmt)} | {_fmt_signed(delta, '+' + fmt)} |")
            lines.append("")

        # Tire side-by-side.
        lines.append("**Tire context (most-used per weekend):**")
        lines.append("")
        lines.append("| | This weekend | Prior weekend |")
        lines.append("|---|---|---|")
        this_tire = _representative_tires(this_w)
        prior_tire = _representative_tires(prior)
        lines.append(f"| Compound | {this_tire.compound or '—'} | {prior_tire.compound or '—'} |")
        lines.append(f"| Size | {this_tire.size or '—'} | {prior_tire.size or '—'} |")
        lines.append(f"| Age / heat cycles | {this_tire.age_heat_cycles or '—'} | {prior_tire.age_heat_cycles or '—'} |")
        lines.append(f"| Cold pressures | {this_tire.pressures_cold_psi or '—'} | {prior_tire.pressures_cold_psi or '—'} |")
        lines.append(f"| Note | {this_tire.note or '—'} | {prior_tire.note or '—'} |")
        lines.append("")
        # Plain-fact change tags — Claude does causation, this just states observation.
        change_tags: list[str] = []
        if this_tire.compound and prior_tire.compound and this_tire.compound != prior_tire.compound:
            change_tags.append(f"**Compound changed**: `{prior_tire.compound}` → `{this_tire.compound}`")
        if this_tire.size and prior_tire.size and this_tire.size != prior_tire.size:
            change_tags.append(f"**Size changed**: `{prior_tire.size}` → `{this_tire.size}`")
        if this_tire.age_heat_cycles and prior_tire.age_heat_cycles and this_tire.age_heat_cycles != prior_tire.age_heat_cycles:
            change_tags.append(f"**Age changed**: `{prior_tire.age_heat_cycles}` → `{this_tire.age_heat_cycles}`")
        if change_tags:
            for t in change_tags:
                lines.append(f"- {t}")
        else:
            lines.append("- _No tire spec changes recorded between these weekends._")
        lines.append("")

    # ---- Reading guide --------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append("## Reading guide")
    lines.append("")
    lines.append("- All sector times use the **9 fixed sectors** defined in the track reference (turn-apex boundaries, S/F endpoints). Each weekend's sector best is the fastest time recorded for that sector across all on-pace laps of all sessions in that weekend — not necessarily on the best lap.")
    lines.append("- **Optimal lap** = sum of the 9 fastest sectors *within that weekend*. The gap from best lap to optimal is the time still on the table when individual sectors were fast but not all on one lap.")
    lines.append("- **Series best** in the cross-weekend table is the all-time fastest sector across every recorded weekend (bold + owner column).")
    lines.append("- **Tire change** and **conditions** rows are plain facts. Attribution of improvements/regressions to tires vs. conditions vs. technique is a judgement call — use the per-corner apex deltas, sector deltas, and technique markers together rather than any single number.")
    lines.append("- **CAN dropout flag** marks laps where ≥25% of samples had no brake/throttle/RPM data; those laps still have valid lap times and GPS but pedal-derived metrics on them are unreliable.")
    lines.append("- A **regression on the same tire compound** between consecutive weekends is more interesting (likely technique or conditions) than a regression *after a tire change* (likely the tire).")
    lines.append("- For an arc across more than 2 weekends, the [progression report](progression.md) is the broader view.")
    return "\n".join(lines) + "\n"


def _representative_tires(w: _WeekendDigest) -> TireSpec:
    """Pick a representative TireSpec for the weekend — the tires used in the best-lap session."""
    for d in w.sessions:
        if w.best_lap_metrics is not None and (d.meta.session_label or d.meta.session_id) == w.best_lap_session_label:
            return d.meta.tires if isinstance(d.meta.tires, TireSpec) else TireSpec(**(d.meta.tires or {}))
    # Fallback to the first session's tires.
    if w.sessions:
        d = w.sessions[0]
        return d.meta.tires if isinstance(d.meta.tires, TireSpec) else TireSpec(**(d.meta.tires or {}))
    return TireSpec()


def _short_wid(wid: str) -> str:
    """Compact weekend ID for narrow table headers — keep the leading date if present."""
    parts = wid.split("_", 1)
    return parts[0] if parts and len(parts[0]) >= 8 else wid
