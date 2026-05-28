"""Lap detection, sector splitting, and outlier filtering.

RaceChrono provides a ``lap_number`` column already; we slice the dataframe
by it. Lap time is computed as the time between the first sample of lap N
and the first sample of lap N+1 — i.e. start/finish to start/finish. The
last lap in a file has no successor and is reported as ``incomplete``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Lap:
    lap_number: int                  # RaceChrono's lap number (may skip if data was lost)
    index_in_session: int            # 0-based position among completed laps in this session
    start_time_unix: float
    end_time_unix: float
    lap_time_s: float
    distance_m: float
    sample_count: int
    row_start: int                   # inclusive
    row_end: int                     # exclusive
    avg_speed_mps: float
    max_speed_mps: float
    min_speed_mps: float
    has_canbus: bool
    incomplete: bool = False         # last-lap-of-file with no next-crossing
    outlier: bool = False            # off-pace / incident / in-out lap
    outlier_reasons: list[str] = field(default_factory=list)
    segment_id: int = 0              # 0-based segment number; increments after each paddock/pit break
    segment_break_after: bool = False  # True if a long stationary gap occurred during this lap
    segment_break_duration_s: float = 0.0  # estimated stationary time inside this lap (seconds)

    @property
    def lap_time_str(self) -> str:
        return format_lap_time(self.lap_time_s)

    def slice(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.iloc[self.row_start : self.row_end]


def format_lap_time(seconds: float) -> str:
    """Format ``92.345`` as ``'1:32.345'`` for human-readable display."""
    if seconds is None or not np.isfinite(seconds):
        return "—"
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"{minutes}:{rem:06.3f}"


def detect_laps(df: pd.DataFrame) -> list[Lap]:
    """Slice ``df`` by ``lap_number`` and compute per-lap timing and speed summary."""
    if "lap_number" not in df.columns or "timestamp" not in df.columns:
        return []

    lap_series = df["lap_number"]
    valid = lap_series.notna()
    if not valid.any():
        return []

    # Identify lap boundaries by where lap_number changes (ignoring NaN segments).
    laps: list[Lap] = []
    cur_lap: int | None = None
    cur_start: int | None = None
    for i, val in enumerate(lap_series):
        if pd.isna(val):
            # End any in-progress lap whose data stream just ended.
            if cur_lap is not None and cur_start is not None:
                laps.append(_finalize_lap(df, cur_lap, cur_start, i, next_start=None))
                cur_lap = None
                cur_start = None
            continue
        lap_int = int(val)
        if cur_lap is None:
            cur_lap = lap_int
            cur_start = i
            continue
        if lap_int != cur_lap:
            laps.append(_finalize_lap(df, cur_lap, cur_start, i, next_start=i))
            cur_lap = lap_int
            cur_start = i

    # Trailing lap whose end is the end of the file (no next-crossing).
    if cur_lap is not None and cur_start is not None:
        laps.append(_finalize_lap(df, cur_lap, cur_start, len(df), next_start=None))

    # Index the completed (non-incomplete) laps in chronological order.
    completed_idx = 0
    for lap in laps:
        if not lap.incomplete:
            lap.index_in_session = completed_idx
            completed_idx += 1

    return laps


def _finalize_lap(df: pd.DataFrame, lap_number: int, row_start: int, row_end: int, next_start: int | None) -> Lap:
    """Build a :class:`Lap` from a row range. ``next_start`` is None for the file's trailing lap."""
    seg = df.iloc[row_start:row_end]
    ts = seg["timestamp"].dropna()
    start_ts = float(ts.iloc[0]) if not ts.empty else float("nan")

    if next_start is not None:
        # Lap ended at the next start/finish crossing — that sample's timestamp.
        end_ts_candidates = df["timestamp"].iloc[next_start : next_start + 1].dropna()
        end_ts = float(end_ts_candidates.iloc[0]) if not end_ts_candidates.empty else float(ts.iloc[-1])
        incomplete = False
    else:
        end_ts = float(ts.iloc[-1]) if not ts.empty else float("nan")
        incomplete = True

    lap_time_s = end_ts - start_ts if np.isfinite(start_ts) and np.isfinite(end_ts) else float("nan")

    # Distance: use distance_traveled delta if available, else integrate speed.
    dist_m = float("nan")
    if "distance_traveled" in seg.columns:
        d = seg["distance_traveled"].dropna()
        if len(d) >= 2:
            dist_m = float(d.iloc[-1] - d.iloc[0])

    # Speed source preference: GPS > calc (CAN-bus speed is rarely populated reliably here).
    speed_series = None
    for cand in ("gps_speed", "calc_speed"):
        if cand in seg.columns and seg[cand].notna().any():
            speed_series = seg[cand].dropna()
            break
    if speed_series is not None and not speed_series.empty:
        avg_speed = float(speed_series.mean())
        max_speed = float(speed_series.max())
        min_speed = float(speed_series.min())
    else:
        avg_speed = max_speed = min_speed = float("nan")

    has_canbus = any(
        c in seg.columns and seg[c].notna().any()
        for c in ("canbus_accelerator_pos", "canbus_brake_pos", "canbus_rpm", "canbus_steering_angle")
    )

    return Lap(
        lap_number=lap_number,
        index_in_session=-1,            # filled in by caller
        start_time_unix=start_ts,
        end_time_unix=end_ts,
        lap_time_s=lap_time_s,
        distance_m=dist_m,
        sample_count=len(seg),
        row_start=row_start,
        row_end=row_end,
        avg_speed_mps=avg_speed,
        max_speed_mps=max_speed,
        min_speed_mps=min_speed,
        has_canbus=has_canbus,
        incomplete=incomplete,
    )


SEGMENT_BREAK_THRESHOLD_S = 240.0  # lap time > this and roughly normal distance → paddock/pit cool-down break


def classify_outliers(
    laps: list[Lap],
    *,
    expected_distance_m: float | None = None,
    distance_tolerance: float = 0.15,
    pace_pct_off_median: float = 0.05,
    min_pace_sample: int = 4,
    segment_break_threshold_s: float = SEGMENT_BREAK_THRESHOLD_S,
) -> list[Lap]:
    """Tag laps as outliers using distance, completeness, pace, and stationary-gap heuristics.

    Rules:
      - Incomplete laps (trailing lap with no end crossing) → outlier ``incomplete``.
      - Distance < ``(1 - distance_tolerance) * expected_distance_m`` → outlier
        ``short_distance`` (cut track, missing data, in-lap).
      - Distance > ``(1 + distance_tolerance) * expected_distance_m`` → outlier
        ``long_distance`` (extra loop, GPS glitch).
      - Lap time NaN or non-positive → outlier ``no_time``.
      - **Lap time > ``segment_break_threshold_s`` AND distance within ±tolerance →
        outlier ``segment_break (Xs)``** where X is the estimated stationary time
        in **seconds** (the lap time minus the median on-pace lap time). This is a
        paddock or pit cool-down break; the next lap is treated as the start of a
        new segment. The lap that *contains* the break has ``segment_break_after =
        True``, and all subsequent laps have ``segment_id`` incremented by 1.
      - With ≥ ``min_pace_sample`` other "candidate-clean" laps in the same
        session: lap time > median * (1 + ``pace_pct_off_median``) → outlier
        ``off_pace_slow`` (incident, traffic). Faster-than-median laps are
        *not* flagged — they're the gold we're trying to keep.
    """
    if not laps:
        return laps

    if expected_distance_m is None:
        # Best guess: median distance of completed laps.
        comp = [l.distance_m for l in laps if not l.incomplete and np.isfinite(l.distance_m)]
        if comp:
            expected_distance_m = float(np.median(comp))

    # First pass: structural outliers.
    for lap in laps:
        reasons: list[str] = []
        if lap.incomplete:
            reasons.append("incomplete")
        if not np.isfinite(lap.lap_time_s) or lap.lap_time_s <= 0:
            reasons.append("no_time")
        if expected_distance_m and np.isfinite(lap.distance_m):
            if lap.distance_m < expected_distance_m * (1 - distance_tolerance):
                reasons.append("short_distance")
            elif lap.distance_m > expected_distance_m * (1 + distance_tolerance):
                reasons.append("long_distance")
        lap.outlier_reasons = reasons
        lap.outlier = bool(reasons)

    # Second pass: segment-break detection. A lap with a very long time and roughly
    # normal distance means the car was stationary for most of it (paddock / pit break).
    # We use the median on-pace lap time as the "expected" lap duration and call the
    # excess time the stationary gap.
    clean_times_for_median = [
        l.lap_time_s for l in laps
        if not l.outlier and not l.incomplete and np.isfinite(l.lap_time_s)
    ]
    median_lap_time = float(np.median(clean_times_for_median)) if clean_times_for_median else float("nan")
    for lap in laps:
        if not np.isfinite(lap.lap_time_s) or lap.lap_time_s <= segment_break_threshold_s:
            continue
        # Require distance to be within normal range — a long lap with abnormal distance is just an off-track.
        if expected_distance_m and np.isfinite(lap.distance_m):
            if not (expected_distance_m * (1 - distance_tolerance) <= lap.distance_m <= expected_distance_m * (1 + distance_tolerance)):
                continue
        # Estimate the stationary gap: lap_time minus a "normal lap" duration.
        if np.isfinite(median_lap_time):
            gap_s = max(0.0, lap.lap_time_s - median_lap_time)
        else:
            gap_s = lap.lap_time_s
        lap.segment_break_after = True
        lap.segment_break_duration_s = float(gap_s)
        reason = f"segment_break ({gap_s:.0f}s)"
        if reason not in lap.outlier_reasons:
            lap.outlier_reasons.append(reason)
        lap.outlier = True

    # Third pass: pace outliers, using only structurally-clean laps as the baseline.
    clean_times = [l.lap_time_s for l in laps if not l.outlier and np.isfinite(l.lap_time_s)]
    if len(clean_times) >= min_pace_sample:
        median_time = float(np.median(clean_times))
        threshold = median_time * (1 + pace_pct_off_median)
        for lap in laps:
            if lap.outlier or not np.isfinite(lap.lap_time_s):
                continue
            if lap.lap_time_s > threshold:
                lap.outlier_reasons.append("off_pace_slow")
                lap.outlier = True

    # Fourth pass: assign segment IDs. Segment 0 covers everything from the start of the
    # data through the first segment break (inclusive). After a segment break, the segment
    # ID increments for the next lap onward.
    current_segment = 0
    for lap in laps:
        lap.segment_id = current_segment
        if lap.segment_break_after:
            current_segment += 1

    return laps


def on_pace(laps: list[Lap]) -> list[Lap]:
    """Return only non-outlier, non-incomplete laps."""
    return [l for l in laps if not l.outlier and not l.incomplete]


def session_best(laps: list[Lap]) -> Lap | None:
    pool = on_pace(laps)
    if not pool:
        return None
    return min(pool, key=lambda l: l.lap_time_s)
