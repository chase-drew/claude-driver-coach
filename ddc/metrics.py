"""Per-lap metrics, corner segmentation, and lap-over-lap deltas.

The approach:

1.  For each turn coordinate, find the sample in the lap where the car was
    closest to that point ("turn passage"). The cumulative passage times
    define sector splits.
2.  A "corner window" around a turn is the contiguous span of samples whose
    nearest turn is that one — i.e. Voronoi cells along the lap path.
3.  Metrics are computed both at the whole-lap level and per corner.

All distances are in meters; G in g (9.81 m/s²); speed in m/s (converted to
mph/kph at report time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt

import numpy as np
import pandas as pd

from .laps import Lap
from .refs import Sector, TrackRef, Turn


EARTH_RADIUS_M = 6_371_000.0


@dataclass
class TurnPassage:
    """A single corner's passage during one lap."""

    turn_number: str
    turn_name: str
    section: str
    passage_time_unix: float        # time of closest approach to the turn coord
    elapsed_in_lap_s: float         # passage_time_unix − lap.start_time_unix
    min_distance_to_apex_m: float
    speed_at_apex_mps: float
    min_speed_in_window_mps: float
    min_speed_m_from_apex: float    # signed distance from apex where min-speed occurred (− = before, + = after)
    apex_style: str                 # "early" | "geometric" | "late" | "unknown" — derived from min_speed_m_from_apex
    max_speed_in_window_mps: float
    peak_lateral_g: float
    peak_longitudinal_decel_g: float    # positive = braking
    peak_longitudinal_accel_g: float    # positive = throttle-driven accel
    peak_combined_g: float              # sqrt(lat² + long²) — traction-circle usage
    brake_max_pct: float
    brake_onset_m_before_apex: float    # distance from apex when brake first crossed threshold; NaN if never
    brake_release_m_before_apex: float  # distance from apex when brake last released; negative = released after apex
    brake_duration_m: float             # total meters with brake > threshold within the window
    trail_brake_past_apex_m: float      # meters of braking AFTER apex; 0 if released before apex
    throttle_min_pct: float
    throttle_onset_m_after_apex: float  # distance after apex where throttle crossed threshold; NaN if never
    throttle_100_onset_m_after_apex: float  # distance after apex where throttle reached 100% (or close); NaN if never
    throttle_at_apex_pct: float
    coast_distance_m: float             # meters with neither brake nor throttle within the window
    overlap_distance_m: float           # meters with both brake AND throttle (trail-braking with throttle / left-foot)
    steering_peak_deg: float
    steering_at_apex_deg: float
    steering_smoothness: float          # RMS of steering rate (deg/s); lower = smoother
    steering_reversals: int             # sign changes in steering rate within the window
    rpm_at_apex: float
    apex_row_in_lap: int                # row index of the apex sample within the lap segment (lap.slice(df).reset_index)
    window_row_start: int
    window_row_end: int
    window_distance_m: float            # total meters covered within the window


@dataclass
class FixedSectorTime:
    """One fixed-distance sector timed for a single lap."""

    number: int
    name: str
    start_anchor: str
    end_anchor: str
    time_s: float
    distance_m: float


@dataclass
class LapMetrics:
    lap: Lap
    passages: list[TurnPassage] = field(default_factory=list)
    sector_times_s: dict[str, float] = field(default_factory=dict)   # section_name → time (corner-based)
    fixed_sectors: list[FixedSectorTime] = field(default_factory=list)  # 9-sector distance-based timings
    peak_lateral_g: float = float("nan")
    peak_longitudinal_decel_g: float = float("nan")
    peak_longitudinal_accel_g: float = float("nan")
    avg_throttle_pct: float = float("nan")
    avg_brake_pct: float = float("nan")
    pct_lap_on_throttle: float = float("nan")        # % samples with throttle > 5%
    pct_lap_on_brake: float = float("nan")           # % samples with brake > 5%
    pct_lap_coasting: float = float("nan")           # neither brake nor throttle
    pct_lap_overlap: float = float("nan")            # both brake and throttle (trail-brake / left-foot)
    steering_smoothness: float = float("nan")        # RMS steering rate, whole lap
    max_speed_mps: float = float("nan")
    min_speed_mps: float = float("nan")
    can_dropout_pct: float = float("nan")            # % of lap samples where all CAN signals are missing
    can_dropout_flag: bool = False                   # True if can_dropout_pct exceeds threshold (default 25%)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two lat/lon points."""
    rlat1, rlat2 = radians(lat1), radians(lat2)
    dlat = rlat2 - rlat1
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def haversine_array(lat: np.ndarray, lon: np.ndarray, lat0: float, lon0: float) -> np.ndarray:
    """Vectorized Haversine — distance from each (lat, lon) sample to a fixed point."""
    rlat = np.radians(lat)
    rlat0 = radians(lat0)
    dlat = rlat - rlat0
    dlon = np.radians(lon - lon0)
    a = np.sin(dlat / 2.0) ** 2 + np.cos(rlat0) * np.cos(rlat) * np.sin(dlon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


# ---------------------------------------------------------------------------
# Per-lap metrics
# ---------------------------------------------------------------------------

BRAKE_THRESH_PCT = 5.0     # treat brake_pos > 5% as "on brake"
THROTTLE_THRESH_PCT = 5.0  # treat accelerator_pos > 5% as "on throttle"


def compute_lap_metrics(lap: Lap, df: pd.DataFrame, track: TrackRef) -> LapMetrics:
    """Compute whole-lap and per-corner metrics for ``lap`` against ``track``."""
    seg = lap.slice(df).reset_index(drop=True)
    lm = LapMetrics(lap=lap)

    if seg.empty:
        return lm

    # ---- whole-lap aggregates ----------------------------------------------------
    lat_g = seg.get("calc_lateral_acc")
    lon_g = seg.get("calc_longitudinal_acc")
    if lat_g is not None and lat_g.notna().any():
        lm.peak_lateral_g = float(lat_g.abs().max())
    if lon_g is not None and lon_g.notna().any():
        lm.peak_longitudinal_decel_g = float(lon_g.min() * -1.0)   # decel is negative longitudinal G
        lm.peak_longitudinal_accel_g = float(lon_g.max())

    brake = seg.get("canbus_brake_pos")
    throttle = seg.get("canbus_accelerator_pos")
    if brake is not None and brake.notna().any():
        b = brake.fillna(0.0)
        lm.avg_brake_pct = float(b.mean())
        on_brake = b > BRAKE_THRESH_PCT
        lm.pct_lap_on_brake = float(on_brake.mean() * 100.0)
    else:
        on_brake = pd.Series([False] * len(seg))
    if throttle is not None and throttle.notna().any():
        t = throttle.fillna(0.0)
        lm.avg_throttle_pct = float(t.mean())
        on_throttle = t > THROTTLE_THRESH_PCT
        lm.pct_lap_on_throttle = float(on_throttle.mean() * 100.0)
    else:
        on_throttle = pd.Series([False] * len(seg))

    if len(on_brake) == len(on_throttle) and len(on_brake) > 0:
        coasting = (~on_brake) & (~on_throttle)
        overlap = on_brake & on_throttle
        lm.pct_lap_coasting = float(coasting.mean() * 100.0)
        lm.pct_lap_overlap = float(overlap.mean() * 100.0)

    steer = seg.get("canbus_steering_angle")
    if steer is not None and steer.notna().any() and "elapsed_time" in seg.columns:
        lm.steering_smoothness = _steering_smoothness(seg["elapsed_time"], steer)

    speed = _best_speed_series(seg)
    if speed is not None and not speed.empty:
        lm.max_speed_mps = float(speed.max())
        lm.min_speed_mps = float(speed.min())

    # CAN-bus dropout detection: samples where every CAN signal is missing or stuck at zero.
    can_cols = [c for c in ("canbus_brake_pos", "canbus_accelerator_pos", "canbus_rpm") if c in seg.columns]
    if can_cols:
        # "Missing" = NaN OR RPM literally zero (engine-off) — but only flag if all three are dead.
        missing_mask = pd.Series(True, index=seg.index)
        for col in can_cols:
            col_missing = seg[col].isna()
            if col == "canbus_rpm":
                col_missing = col_missing | (seg[col] == 0)
            missing_mask = missing_mask & col_missing
        lm.can_dropout_pct = float(missing_mask.mean() * 100.0)
        lm.can_dropout_flag = lm.can_dropout_pct >= 25.0

    # ---- per-turn passages ------------------------------------------------------
    if "gps_latitude" in seg.columns and "gps_longitude" in seg.columns and track.turns:
        lats = seg["gps_latitude"].to_numpy(dtype=float)
        lons = seg["gps_longitude"].to_numpy(dtype=float)
        valid_mask = np.isfinite(lats) & np.isfinite(lons)
        if valid_mask.any():
            # For each turn, find the row of minimum Haversine distance (apex passage).
            apex_indices: list[tuple[Turn, int, float]] = []
            for turn in track.turns:
                d = haversine_array(lats, lons, turn.latitude, turn.longitude)
                d_masked = np.where(valid_mask, d, np.inf)
                idx = int(np.argmin(d_masked))
                apex_indices.append((turn, idx, float(d_masked[idx])))

            # Build Voronoi-style windows: for each sample, which turn is closest?
            # Then a turn's window = contiguous run of samples assigned to it that contains its apex.
            turn_lat = np.array([t.latitude for t in track.turns], dtype=float)
            turn_lon = np.array([t.longitude for t in track.turns], dtype=float)
            # n_samples × n_turns distance matrix would be huge for long laps; compute per-sample argmin streamingly.
            nearest_turn = _nearest_turn_indices(lats, lons, turn_lat, turn_lon, valid_mask)

            for (turn, apex_idx, min_dist) in apex_indices:
                turn_pos = track.turns.index(turn)
                window_start, window_end = _expand_window(nearest_turn, apex_idx, turn_pos)
                passage = _build_passage(turn, seg, apex_idx, window_start, window_end, lap.start_time_unix)
                if passage is not None:
                    passage.min_distance_to_apex_m = min_dist
                    lm.passages.append(passage)

            # Sort passages by elapsed time so sectors come out in driving order.
            lm.passages.sort(key=lambda p: p.elapsed_in_lap_s if np.isfinite(p.elapsed_in_lap_s) else float("inf"))
            lm.sector_times_s = _sector_times(lm.passages, track)

    # Fixed (distance-based) sector times — boundary-anchored at turn-apex passages or S/F.
    if track.sectors:
        lm.fixed_sectors = _compute_fixed_sectors(lap, seg, lm.passages, track.sectors)

    return lm


def _compute_fixed_sectors(
    lap: Lap,
    seg: pd.DataFrame,
    passages: list[TurnPassage],
    sectors: list[Sector],
) -> list[FixedSectorTime]:
    """Slice the lap into named sectors using turn-apex passages and S/F crossings as boundaries.

    Each sector's start anchor is either ``"start_finish"`` (lap start time, distance 0)
    or a turn number whose ``TurnPassage`` is in ``passages``. End anchors work the same
    way; ``"start_finish"`` at the end means lap end.
    """
    passage_by_turn = {p.turn_number: p for p in passages}
    out: list[FixedSectorTime] = []

    # Pre-compute per-lap distance offsets so distance_traveled starts at 0 for this lap.
    has_distance = "distance_traveled" in seg.columns
    lap_start_d = float(seg["distance_traveled"].iloc[0]) if has_distance and seg["distance_traveled"].notna().any() else float("nan")

    for sec in sectors:
        t_start = _anchor_time(sec.start_anchor, lap, passage_by_turn, position="start")
        t_end = _anchor_time(sec.end_anchor, lap, passage_by_turn, position="end")
        if not (np.isfinite(t_start) and np.isfinite(t_end)):
            out.append(FixedSectorTime(sec.number, sec.name, sec.start_anchor, sec.end_anchor, float("nan"), float("nan")))
            continue
        time_s = t_end - t_start

        # Distance covered in the sector (lap-relative meters).
        distance_m = float("nan")
        if has_distance:
            d_start = _anchor_distance(sec.start_anchor, lap, passage_by_turn, seg, lap_start_d, position="start")
            d_end = _anchor_distance(sec.end_anchor, lap, passage_by_turn, seg, lap_start_d, position="end")
            if np.isfinite(d_start) and np.isfinite(d_end):
                distance_m = d_end - d_start

        out.append(FixedSectorTime(sec.number, sec.name, sec.start_anchor, sec.end_anchor, float(time_s), distance_m))
    return out


def _anchor_time(anchor: str, lap: Lap, passage_by_turn: dict[str, TurnPassage], *, position: str) -> float:
    """Resolve a sector anchor (turn number or 'start_finish') to a Unix timestamp."""
    if anchor == "start_finish":
        return lap.start_time_unix if position == "start" else lap.end_time_unix
    p = passage_by_turn.get(anchor)
    if p is None or not np.isfinite(p.passage_time_unix):
        return float("nan")
    return p.passage_time_unix


def _anchor_distance(
    anchor: str,
    lap: Lap,
    passage_by_turn: dict[str, TurnPassage],
    seg: pd.DataFrame,
    lap_start_d: float,
    *,
    position: str,
) -> float:
    """Lap-relative distance (m) at this anchor; 0 at lap start, lap distance at lap end."""
    if anchor == "start_finish":
        if position == "start":
            return 0.0
        d = seg["distance_traveled"].dropna()
        if d.empty or not np.isfinite(lap_start_d):
            return float("nan")
        return float(d.iloc[-1] - lap_start_d)
    p = passage_by_turn.get(anchor)
    if p is None:
        return float("nan")
    apex_row = p.apex_row_in_lap
    if apex_row < 0 or apex_row >= len(seg):
        return float("nan")
    d_at_apex = seg["distance_traveled"].iloc[apex_row]
    if not np.isfinite(d_at_apex) or not np.isfinite(lap_start_d):
        return float("nan")
    return float(d_at_apex - lap_start_d)


def _best_speed_series(seg: pd.DataFrame) -> pd.Series | None:
    for cand in ("gps_speed", "calc_speed"):
        if cand in seg.columns and seg[cand].notna().any():
            return seg[cand].dropna()
    return None


def _steering_smoothness(elapsed: pd.Series, steer: pd.Series) -> float:
    """RMS of dθ/dt across the lap. Lower = smoother hands."""
    df = pd.DataFrame({"t": elapsed, "s": steer}).dropna()
    if len(df) < 3:
        return float("nan")
    dt = df["t"].diff()
    ds = df["s"].diff()
    rate = ds / dt.replace(0, np.nan)
    rate = rate.replace([np.inf, -np.inf], np.nan).dropna()
    if rate.empty:
        return float("nan")
    return float(np.sqrt(np.mean(rate**2)))


def _nearest_turn_indices(
    lats: np.ndarray,
    lons: np.ndarray,
    turn_lats: np.ndarray,
    turn_lons: np.ndarray,
    valid_mask: np.ndarray,
) -> np.ndarray:
    """For each sample, which turn (by index) is closest? Invalid samples → -1."""
    n_samples = lats.shape[0]
    n_turns = turn_lats.shape[0]
    out = np.full(n_samples, -1, dtype=int)

    # Loop per turn (small N, ~20) and update a running min — cheaper memory-wise
    # than a full n_samples × n_turns matrix.
    best = np.full(n_samples, np.inf)
    for ti in range(n_turns):
        d = haversine_array(lats, lons, float(turn_lats[ti]), float(turn_lons[ti]))
        d = np.where(valid_mask, d, np.inf)
        update = d < best
        best[update] = d[update]
        out[update] = ti
    return out


def _expand_window(nearest_turn: np.ndarray, apex_idx: int, turn_pos: int) -> tuple[int, int]:
    """Walk left/right from the apex while samples stay assigned to this turn."""
    start = apex_idx
    while start > 0 and nearest_turn[start - 1] == turn_pos:
        start -= 1
    end = apex_idx + 1
    n = nearest_turn.shape[0]
    while end < n and nearest_turn[end] == turn_pos:
        end += 1
    return start, end


def _build_passage(
    turn: Turn,
    seg: pd.DataFrame,
    apex_idx: int,
    win_start: int,
    win_end: int,
    lap_start_unix: float,
) -> TurnPassage | None:
    """Compute corner metrics within ``[win_start, win_end)`` of the lap segment."""
    win = seg.iloc[win_start:win_end]
    if win.empty:
        return None
    ts_series = win["timestamp"].dropna() if "timestamp" in win.columns else pd.Series(dtype=float)
    if ts_series.empty:
        return None

    apex_ts = float(seg["timestamp"].iloc[apex_idx]) if "timestamp" in seg.columns else float("nan")
    elapsed_in_lap = apex_ts - lap_start_unix if np.isfinite(apex_ts) and np.isfinite(lap_start_unix) else float("nan")

    speed = _best_speed_series(win)
    speed_at_apex = float("nan")
    speed_series_full = None
    for cand in ("gps_speed", "calc_speed"):
        if cand in seg.columns:
            speed_series_full = seg[cand]
            break
    if speed_series_full is not None and np.isfinite(speed_series_full.iloc[apex_idx]):
        speed_at_apex = float(speed_series_full.iloc[apex_idx])

    min_speed = float(speed.min()) if speed is not None and not speed.empty else float("nan")
    max_speed = float(speed.max()) if speed is not None and not speed.empty else float("nan")

    # Distance-from-apex axis (signed: negative before apex, positive after).
    dist_signed = _signed_distance_to_apex(seg, apex_idx, win_start, win_end)

    # Where in the window did min speed occur (relative to apex)?
    if speed_series_full is not None:
        win_speed = speed_series_full.iloc[win_start:win_end].reset_index(drop=True)
        win_dist = dist_signed.reset_index(drop=True)
        if win_speed.notna().any():
            local_min_idx = int(win_speed.idxmin())
            min_speed_m_from_apex = float(win_dist.iloc[local_min_idx]) if np.isfinite(win_dist.iloc[local_min_idx]) else float("nan")
        else:
            min_speed_m_from_apex = float("nan")
    else:
        min_speed_m_from_apex = float("nan")

    apex_style = _classify_apex(min_speed_m_from_apex)

    lat_g = win.get("calc_lateral_acc")
    lon_g = win.get("calc_longitudinal_acc")
    peak_lat = float(lat_g.abs().max()) if lat_g is not None and lat_g.notna().any() else float("nan")
    peak_decel = float(lon_g.min() * -1.0) if lon_g is not None and lon_g.notna().any() else float("nan")
    peak_accel = float(lon_g.max()) if lon_g is not None and lon_g.notna().any() else float("nan")
    if lat_g is not None and lon_g is not None and lat_g.notna().any() and lon_g.notna().any():
        combined = np.sqrt(lat_g.fillna(0).to_numpy() ** 2 + lon_g.fillna(0).to_numpy() ** 2)
        peak_combined = float(np.nanmax(combined)) if combined.size else float("nan")
    else:
        peak_combined = float("nan")

    brake = win.get("canbus_brake_pos")
    brake_max = float(brake.max()) if brake is not None and brake.notna().any() else float("nan")
    brake_onset_m = _onset_distance(dist_signed, brake, BRAKE_THRESH_PCT, before_apex=True)
    brake_release_m = _release_distance(dist_signed, brake, BRAKE_THRESH_PCT)
    brake_duration_m = _signal_above_distance(dist_signed, brake, BRAKE_THRESH_PCT)
    # Trail-brake past apex: portion of brake_duration that's after apex.
    trail_brake_past_apex = _signal_above_distance(dist_signed, brake, BRAKE_THRESH_PCT, only_after_apex=True)

    throttle = win.get("canbus_accelerator_pos")
    throttle_min = float(throttle.min()) if throttle is not None and throttle.notna().any() else float("nan")
    throttle_onset_m = _onset_distance(dist_signed, throttle, THROTTLE_THRESH_PCT, before_apex=False)
    # Treat ≥ 98% as "100%" — the pedal sensor rarely reads exactly 100 even at full throttle.
    throttle_100_onset_m = _onset_distance(dist_signed, throttle, 98.0, before_apex=False)
    throttle_at_apex = float(throttle.iloc[apex_idx - win_start]) if (throttle is not None and (apex_idx - win_start) < len(throttle) and np.isfinite(throttle.iloc[apex_idx - win_start])) else float("nan")

    # Coast and overlap distances within the window.
    coast_distance_m = _coast_overlap_distance(
        dist_signed, brake, throttle, BRAKE_THRESH_PCT, THROTTLE_THRESH_PCT, mode="coast"
    )
    overlap_distance_m = _coast_overlap_distance(
        dist_signed, brake, throttle, BRAKE_THRESH_PCT, THROTTLE_THRESH_PCT, mode="overlap"
    )

    steer = win.get("canbus_steering_angle")
    if steer is not None and steer.notna().any():
        steering_peak = float(steer.abs().max())
        steering_smooth = _steering_smoothness(win["elapsed_time"], steer) if "elapsed_time" in win.columns else float("nan")
        steering_reversals = _count_reversals(win["elapsed_time"], steer) if "elapsed_time" in win.columns else 0
        try:
            steering_at_apex = float(steer.iloc[apex_idx - win_start])
        except (IndexError, ValueError):
            steering_at_apex = float("nan")
    else:
        steering_peak = float("nan")
        steering_smooth = float("nan")
        steering_reversals = 0
        steering_at_apex = float("nan")

    rpm_at_apex = float("nan")
    if "canbus_rpm" in seg.columns and np.isfinite(seg["canbus_rpm"].iloc[apex_idx]):
        rpm_at_apex = float(seg["canbus_rpm"].iloc[apex_idx])

    # Window distance (meters covered between window start and end).
    window_distance = float("nan")
    if "distance_traveled" in seg.columns:
        d = seg["distance_traveled"].iloc[win_start:win_end].dropna()
        if len(d) >= 2:
            window_distance = float(d.iloc[-1] - d.iloc[0])

    return TurnPassage(
        turn_number=turn.number,
        turn_name=turn.name,
        section=turn.section,
        passage_time_unix=apex_ts,
        elapsed_in_lap_s=elapsed_in_lap,
        min_distance_to_apex_m=float("nan"),    # filled in by caller
        speed_at_apex_mps=speed_at_apex,
        min_speed_in_window_mps=min_speed,
        min_speed_m_from_apex=min_speed_m_from_apex,
        apex_style=apex_style,
        max_speed_in_window_mps=max_speed,
        peak_lateral_g=peak_lat,
        peak_longitudinal_decel_g=peak_decel,
        peak_longitudinal_accel_g=peak_accel,
        peak_combined_g=peak_combined,
        brake_max_pct=brake_max,
        brake_onset_m_before_apex=brake_onset_m,
        brake_release_m_before_apex=brake_release_m,
        brake_duration_m=brake_duration_m,
        trail_brake_past_apex_m=trail_brake_past_apex,
        throttle_min_pct=throttle_min,
        throttle_onset_m_after_apex=throttle_onset_m,
        throttle_100_onset_m_after_apex=throttle_100_onset_m,
        throttle_at_apex_pct=throttle_at_apex,
        coast_distance_m=coast_distance_m,
        overlap_distance_m=overlap_distance_m,
        steering_peak_deg=steering_peak,
        steering_at_apex_deg=steering_at_apex,
        steering_smoothness=steering_smooth,
        steering_reversals=steering_reversals,
        rpm_at_apex=rpm_at_apex,
        apex_row_in_lap=int(apex_idx),
        window_row_start=int(win_start),
        window_row_end=int(win_end),
        window_distance_m=window_distance,
    )


def _signed_distance_to_apex(seg: pd.DataFrame, apex_idx: int, win_start: int, win_end: int) -> pd.Series:
    """Cumulative track distance from each sample in the window to the apex (negative = before apex)."""
    if "distance_traveled" not in seg.columns:
        return pd.Series([np.nan] * (win_end - win_start), index=seg.index[win_start:win_end])
    full_d = seg["distance_traveled"].astype(float)
    apex_d = float(full_d.iloc[apex_idx])
    return full_d.iloc[win_start:win_end] - apex_d


def _onset_distance(dist_signed: pd.Series, signal: pd.Series | None, thresh: float, *, before_apex: bool) -> float:
    """Distance from apex where ``signal`` first crosses ``thresh``.

    With ``before_apex=True``, looks at samples with negative distance and returns the
    distance (positive meters) where the signal first rose above threshold (i.e. brake
    onset = how far before apex you started braking). With ``before_apex=False``,
    looks at positive-distance samples and returns the distance after apex where the
    signal first crossed threshold (throttle pickup point).
    """
    if signal is None or signal.dropna().empty or dist_signed.dropna().empty:
        return float("nan")
    df = pd.DataFrame({"d": dist_signed.values, "s": signal.values})
    df = df.dropna()
    if before_apex:
        df = df[df["d"] <= 0].sort_values("d")    # walk apex → start (least negative to most negative)
        above = df[df["s"] > thresh]
        if above.empty:
            return float("nan")
        # The "onset" is the most-negative distance where we were still above threshold.
        return float(-above["d"].min())
    else:
        df = df[df["d"] >= 0].sort_values("d")
        above = df[df["s"] > thresh]
        if above.empty:
            return float("nan")
        return float(above["d"].iloc[0])


def _signal_above_distance(
    dist_signed: pd.Series,
    signal: pd.Series | None,
    thresh: float,
    *,
    only_after_apex: bool = False,
) -> float:
    """Total meters traveled within the window where ``signal`` is above ``thresh``.

    Uses trapezoidal accumulation of the distance axis between consecutive samples
    where the signal exceeds the threshold. With ``only_after_apex=True``, only
    counts distance with ``dist_signed > 0`` (samples after the apex).
    """
    if signal is None or signal.dropna().empty or dist_signed.dropna().empty:
        return float("nan")
    df = pd.DataFrame({"d": dist_signed.values, "s": signal.values}).dropna().sort_values("d").reset_index(drop=True)
    if df.empty:
        return float("nan")
    if only_after_apex:
        df = df[df["d"] >= 0].reset_index(drop=True)
        if df.empty:
            return 0.0
    above = df["s"] > thresh
    total = 0.0
    for i in range(1, len(df)):
        seg_len = df["d"].iloc[i] - df["d"].iloc[i - 1]
        if seg_len <= 0:
            continue
        # If both endpoints are above threshold count full segment;
        # if only one, count half (rough approximation of the crossover).
        if above.iloc[i] and above.iloc[i - 1]:
            total += seg_len
        elif above.iloc[i] or above.iloc[i - 1]:
            total += seg_len * 0.5
    return float(total)


def _coast_overlap_distance(
    dist_signed: pd.Series,
    brake: pd.Series | None,
    throttle: pd.Series | None,
    brake_thresh: float,
    throttle_thresh: float,
    *,
    mode: str,
) -> float:
    """Meters traveled within the window where the driver is coasting or overlapping inputs.

    ``mode="coast"``: brake ≤ threshold AND throttle ≤ threshold.
    ``mode="overlap"``: brake > threshold AND throttle > threshold.
    """
    if brake is None or throttle is None:
        return float("nan")
    df = pd.DataFrame({"d": dist_signed.values, "b": brake.values, "t": throttle.values}).dropna().sort_values("d").reset_index(drop=True)
    if df.empty:
        return float("nan")
    if mode == "coast":
        flag = (df["b"] <= brake_thresh) & (df["t"] <= throttle_thresh)
    elif mode == "overlap":
        flag = (df["b"] > brake_thresh) & (df["t"] > throttle_thresh)
    else:
        raise ValueError(f"unknown mode {mode}")
    total = 0.0
    for i in range(1, len(df)):
        seg_len = df["d"].iloc[i] - df["d"].iloc[i - 1]
        if seg_len <= 0:
            continue
        if flag.iloc[i] and flag.iloc[i - 1]:
            total += seg_len
        elif flag.iloc[i] or flag.iloc[i - 1]:
            total += seg_len * 0.5
    return float(total)


def _classify_apex(min_speed_m_from_apex: float, tolerance_m: float = 8.0) -> str:
    """Classify apex style by where in the corner the min-speed point occurred.

    - Min speed well *before* the geometric apex (>= ``tolerance_m`` meters before)
      → ``"early"`` (early-apex line, slower exit phase).
    - Min speed within ``±tolerance_m`` of the apex coord → ``"geometric"``.
    - Min speed well *after* the apex → ``"late"`` (late-apex line, on power earlier).
    """
    if not np.isfinite(min_speed_m_from_apex):
        return "unknown"
    if min_speed_m_from_apex < -tolerance_m:
        return "early"
    if min_speed_m_from_apex > tolerance_m:
        return "late"
    return "geometric"


def _release_distance(dist_signed: pd.Series, signal: pd.Series | None, thresh: float) -> float:
    """Distance before apex where the signal last dropped below threshold.

    Positive = released before apex (e.g. quick brake release for a flowing corner).
    Negative = released after apex (trail-braking into rotation).
    """
    if signal is None or signal.dropna().empty or dist_signed.dropna().empty:
        return float("nan")
    df = pd.DataFrame({"d": dist_signed.values, "s": signal.values}).dropna().sort_values("d")
    above = df[df["s"] > thresh]
    if above.empty:
        return float("nan")
    last_above_d = float(above["d"].iloc[-1])
    return -last_above_d


def _count_reversals(elapsed: pd.Series, steer: pd.Series) -> int:
    """Count sign changes in the steering rate; a "busy hands" indicator."""
    df = pd.DataFrame({"t": elapsed, "s": steer}).dropna()
    if len(df) < 3:
        return 0
    rate = df["s"].diff() / df["t"].diff().replace(0, np.nan)
    rate = rate.dropna()
    if rate.empty:
        return 0
    signs = np.sign(rate.values)
    # Treat zero as no change; count transitions between +1 and −1.
    nonzero = signs[signs != 0]
    if nonzero.size < 2:
        return 0
    return int(np.sum(nonzero[1:] != nonzero[:-1]))


def _sector_times(passages: list[TurnPassage], track: TrackRef) -> dict[str, float]:
    """Time spent within each named section (snake, esses, oak_tree, etc.)."""
    if not passages:
        return {}
    # Group passages by section in driving order.
    by_section: dict[str, list[TurnPassage]] = {}
    for p in passages:
        by_section.setdefault(p.section, []).append(p)

    out: dict[str, float] = {}
    # For named multi-turn sections, "time in section" = last apex elapsed − first apex elapsed.
    # For single-turn sections, we can't get a span this way; skip.
    for section, ps in by_section.items():
        if not section or len(ps) < 2:
            continue
        ps_sorted = sorted(ps, key=lambda p: p.elapsed_in_lap_s)
        span = ps_sorted[-1].elapsed_in_lap_s - ps_sorted[0].elapsed_in_lap_s
        if np.isfinite(span):
            out[section] = float(span)

    # Also compute corner-to-corner deltas using the full turn ordering as informal "sectors".
    ordered = sorted(passages, key=lambda p: p.elapsed_in_lap_s)
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        delta = b.elapsed_in_lap_s - a.elapsed_in_lap_s
        if np.isfinite(delta):
            out[f"T{a.turn_number}→T{b.turn_number}"] = float(delta)
    return out


# ---------------------------------------------------------------------------
# Lap-over-lap comparison
# ---------------------------------------------------------------------------

@dataclass
class CornerDelta:
    turn_number: str
    turn_name: str
    section: str
    speed_delta_mps: float          # this lap apex speed − reference apex speed
    min_speed_delta_mps: float
    sector_into_corner_delta_s: float   # time from prior turn to this turn, vs reference
    brake_onset_delta_m: float          # + = braking later than reference (good if not overshooting)
    throttle_onset_delta_m: float       # − = back to power earlier than reference (good)
    peak_lat_g_delta: float


def compare_to_reference(target: LapMetrics, reference: LapMetrics) -> list[CornerDelta]:
    """Per-corner deltas of ``target`` vs ``reference`` (typically session best)."""
    if not target.passages or not reference.passages:
        return []
    ref_by_num = {p.turn_number: p for p in reference.passages}
    out: list[CornerDelta] = []
    # Build a quick map: turn_number → elapsed_in_lap_s for "into-corner" delta lookup.
    target_elapsed = {p.turn_number: p.elapsed_in_lap_s for p in target.passages}
    ref_elapsed = {p.turn_number: p.elapsed_in_lap_s for p in reference.passages}
    target_ordered = sorted(target.passages, key=lambda p: p.elapsed_in_lap_s)
    prev_target: dict[str, str | None] = {}
    prev_num: str | None = None
    for p in target_ordered:
        prev_target[p.turn_number] = prev_num
        prev_num = p.turn_number

    for tp in target.passages:
        rp = ref_by_num.get(tp.turn_number)
        if rp is None:
            continue
        # "Sector into corner" = time from previous corner passage to this one.
        prev_num = prev_target.get(tp.turn_number)
        if prev_num and prev_num in ref_elapsed and prev_num in target_elapsed:
            target_into = tp.elapsed_in_lap_s - target_elapsed[prev_num]
            ref_into = rp.elapsed_in_lap_s - ref_elapsed[prev_num]
            sector_delta = target_into - ref_into
        else:
            sector_delta = float("nan")
        out.append(
            CornerDelta(
                turn_number=tp.turn_number,
                turn_name=tp.turn_name,
                section=tp.section,
                speed_delta_mps=_delta(tp.speed_at_apex_mps, rp.speed_at_apex_mps),
                min_speed_delta_mps=_delta(tp.min_speed_in_window_mps, rp.min_speed_in_window_mps),
                sector_into_corner_delta_s=sector_delta,
                brake_onset_delta_m=_delta(tp.brake_onset_m_before_apex, rp.brake_onset_m_before_apex),
                throttle_onset_delta_m=_delta(tp.throttle_onset_m_after_apex, rp.throttle_onset_m_after_apex),
                peak_lat_g_delta=_delta(tp.peak_lateral_g, rp.peak_lateral_g),
            )
        )
    return out


def _delta(a: float, b: float) -> float:
    if not (np.isfinite(a) and np.isfinite(b)):
        return float("nan")
    return float(a - b)


# ---------------------------------------------------------------------------
# Time-series sampling around a corner (distance-from-apex aligned)
# ---------------------------------------------------------------------------

TRACE_SIGNALS = {
    "speed_mps": ("gps_speed", "calc_speed"),
    "brake_pct": ("canbus_brake_pos",),
    "throttle_pct": ("canbus_accelerator_pos",),
    "steering_deg": ("canbus_steering_angle",),
    "lateral_g": ("calc_lateral_acc",),
    "longitudinal_g": ("calc_longitudinal_acc",),
    "rpm": ("canbus_rpm",),
}


def sample_trace_around_corner(
    lap_seg: pd.DataFrame,
    apex_row_in_lap: int,
    *,
    dist_min_m: float = -160.0,
    dist_max_m: float = 100.0,
    step_m: float = 10.0,
) -> pd.DataFrame:
    """Resample telemetry around a corner apex at fixed track-distance offsets.

    Returns a DataFrame with one row per distance offset (e.g. -160, -150, …, +100)
    and columns for each :data:`TRACE_SIGNALS` key. Each value is interpolated
    linearly from the underlying samples in the lap segment.

    The lap segment is ``lap.slice(df)`` (already 0-indexed). ``apex_row_in_lap``
    is the row index *within that segment* — i.e. ``apex_idx - lap.row_start``
    from the metrics computation.
    """
    if lap_seg.empty or "distance_traveled" not in lap_seg.columns:
        return pd.DataFrame()
    full_d = lap_seg["distance_traveled"].astype(float).reset_index(drop=True)
    if not np.isfinite(full_d.iloc[apex_row_in_lap]):
        return pd.DataFrame()
    apex_d = float(full_d.iloc[apex_row_in_lap])
    signed_d = full_d - apex_d

    offsets = np.arange(dist_min_m, dist_max_m + step_m / 2.0, step_m)
    out = {"dist_m": offsets}
    for key, candidates in TRACE_SIGNALS.items():
        series = None
        for col in candidates:
            if col in lap_seg.columns and lap_seg[col].notna().any():
                series = lap_seg[col].astype(float).reset_index(drop=True)
                break
        if series is None:
            out[key] = np.full(offsets.shape, np.nan)
            continue
        # Drop NaN-aligned samples for clean monotonic interpolation along signed_d.
        valid = signed_d.notna() & series.notna()
        if valid.sum() < 2:
            out[key] = np.full(offsets.shape, np.nan)
            continue
        xs = signed_d[valid].to_numpy()
        ys = series[valid].to_numpy()
        # Sort by xs (distance may not be monotonic across very long stops, but lap segments are).
        order = np.argsort(xs)
        xs, ys = xs[order], ys[order]
        # np.interp clips at endpoints — return NaN outside actual sample range so it's obvious where data is missing.
        out_vals = np.interp(offsets, xs, ys, left=np.nan, right=np.nan)
        out[key] = out_vals
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# Cross-lap statistics
# ---------------------------------------------------------------------------

@dataclass
class CornerStat:
    """min/max/median/std of a single field across all on-pace passages of one turn."""

    turn_number: str
    field: str
    min_value: float
    max_value: float
    median_value: float
    std_value: float
    best_lap_value: float          # value on the session best lap (for "is best the outlier?")
    best_lap_rank_low_to_high: int # 1 = best lap had the lowest value; len(laps) = had the highest


def corner_stats(
    metrics_by_lap: dict[int, "LapMetrics"],
    best_lap_number: int | None,
    turn_number: str,
    fields: list[str] | None = None,
) -> dict[str, CornerStat]:
    """For each requested field on a single turn, compute distribution across on-pace laps."""
    if fields is None:
        fields = [
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
            "elapsed_in_lap_s",
        ]
    out: dict[str, CornerStat] = {}
    rows: list[tuple[int, "TurnPassage"]] = []
    for lap_num, lm in metrics_by_lap.items():
        for p in lm.passages:
            if p.turn_number == turn_number:
                rows.append((lap_num, p))
                break
    if not rows:
        return out
    for field in fields:
        values = [(lap_num, getattr(p, field)) for lap_num, p in rows if np.isfinite(getattr(p, field))]
        if not values:
            continue
        vals = np.array([v for _, v in values])
        sorted_lap_to_val = sorted(values, key=lambda x: x[1])
        best_val = float("nan")
        rank = 0
        if best_lap_number is not None:
            for i, (ln, v) in enumerate(sorted_lap_to_val, start=1):
                if ln == best_lap_number:
                    best_val = v
                    rank = i
                    break
        out[field] = CornerStat(
            turn_number=turn_number,
            field=field,
            min_value=float(vals.min()),
            max_value=float(vals.max()),
            median_value=float(np.median(vals)),
            std_value=float(np.std(vals)),
            best_lap_value=best_val,
            best_lap_rank_low_to_high=rank,
        )
    return out


# ---------------------------------------------------------------------------
# Lost-time attribution
# ---------------------------------------------------------------------------

@dataclass
class LostTimeRow:
    turn_number: str
    section: str
    segment_label: str          # e.g. "T2→T3" or "T1" for the first into-corner sector
    ms_lost: float              # negative = gained time vs reference
    pct_of_total_deficit: float # ms_lost / total_deficit; meaningful only when total > 0


def lost_time_attribution(target: "LapMetrics", reference: "LapMetrics") -> list[LostTimeRow]:
    """Break a lap's deficit-to-best down into per-segment time deltas.

    "Segments" are corner-to-corner spans: the time from the apex passage of
    turn N to the apex passage of turn N+1. The first segment is from lap
    start to the first turn's apex. Each segment is compared between
    ``target`` and ``reference`` (both must include the same turns in the
    same order).
    """
    if not target.passages or not reference.passages:
        return []
    ref_by_num = {p.turn_number: p for p in reference.passages}
    target_ordered = sorted(target.passages, key=lambda p: p.elapsed_in_lap_s)
    total_deficit = target.lap.lap_time_s - reference.lap.lap_time_s

    rows: list[LostTimeRow] = []
    prev_target_elapsed = 0.0
    prev_ref_elapsed = 0.0
    prev_num: str | None = None
    for tp in target_ordered:
        rp = ref_by_num.get(tp.turn_number)
        if rp is None or not np.isfinite(tp.elapsed_in_lap_s) or not np.isfinite(rp.elapsed_in_lap_s):
            prev_target_elapsed = tp.elapsed_in_lap_s
            prev_ref_elapsed = rp.elapsed_in_lap_s if rp else prev_ref_elapsed
            prev_num = tp.turn_number
            continue
        seg_target = tp.elapsed_in_lap_s - prev_target_elapsed
        seg_ref = rp.elapsed_in_lap_s - prev_ref_elapsed
        ms = (seg_target - seg_ref) * 1000.0
        label = f"start→T{tp.turn_number}" if prev_num is None else f"T{prev_num}→T{tp.turn_number}"
        pct = (ms / 1000.0) / total_deficit * 100.0 if abs(total_deficit) > 1e-6 else 0.0
        rows.append(
            LostTimeRow(
                turn_number=tp.turn_number,
                section=tp.section,
                segment_label=label,
                ms_lost=ms,
                pct_of_total_deficit=pct,
            )
        )
        prev_target_elapsed = tp.elapsed_in_lap_s
        prev_ref_elapsed = rp.elapsed_in_lap_s
        prev_num = tp.turn_number
    return rows


# ---------------------------------------------------------------------------
# Theoretical best
# ---------------------------------------------------------------------------

@dataclass
class TheoreticalBest:
    segments: list[tuple[str, int, float]]  # (segment_label, source_lap_number, segment_seconds)
    total_s: float
    actual_best_s: float
    delta_s: float                          # actual_best − theoretical (positive = room to find)


def theoretical_best(metrics_by_lap: dict[int, "LapMetrics"], actual_best_s: float) -> TheoreticalBest:
    """Construct the ideal lap by picking the fastest corner-to-corner segment from any lap.

    For each segment ``prev_turn → next_turn`` that appears in the lap, find
    the lap whose segment time is lowest, and sum those minima.
    """
    # Build segment timings per lap: dict[lap_num] → list of (label, seconds).
    per_lap_segments: dict[int, list[tuple[str, float]]] = {}
    for lap_num, lm in metrics_by_lap.items():
        ordered = sorted(lm.passages, key=lambda p: p.elapsed_in_lap_s)
        prev_elapsed = 0.0
        prev_num: str | None = None
        segs: list[tuple[str, float]] = []
        for p in ordered:
            if not np.isfinite(p.elapsed_in_lap_s):
                continue
            seg_t = p.elapsed_in_lap_s - prev_elapsed
            label = f"start→T{p.turn_number}" if prev_num is None else f"T{prev_num}→T{p.turn_number}"
            segs.append((label, seg_t))
            prev_elapsed = p.elapsed_in_lap_s
            prev_num = p.turn_number
        # Trailing segment from last apex to lap end.
        if prev_num is not None and np.isfinite(lm.lap.lap_time_s):
            segs.append((f"T{prev_num}→finish", lm.lap.lap_time_s - prev_elapsed))
        per_lap_segments[lap_num] = segs

    # Collect segment labels in canonical order (the order of the first lap with that label).
    label_order: list[str] = []
    seen: set[str] = set()
    for lap_num, segs in per_lap_segments.items():
        for label, _ in segs:
            if label not in seen:
                seen.add(label)
                label_order.append(label)

    # For each segment label, pick the lap with the minimum time.
    best_segments: list[tuple[str, int, float]] = []
    for label in label_order:
        cands: list[tuple[int, float]] = []
        for lap_num, segs in per_lap_segments.items():
            for lbl, t in segs:
                if lbl == label and np.isfinite(t):
                    cands.append((lap_num, t))
                    break
        if not cands:
            continue
        best_lap, best_t = min(cands, key=lambda x: x[1])
        best_segments.append((label, best_lap, best_t))

    total = float(sum(t for _, _, t in best_segments))
    return TheoreticalBest(
        segments=best_segments,
        total_s=total,
        actual_best_s=actual_best_s,
        delta_s=actual_best_s - total,
    )
