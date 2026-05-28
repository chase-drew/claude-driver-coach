"""RaceChrono Pro CSV parser.

RaceChrono exports a 9-line metadata header followed by 3 header rows
(column name / unit / sensor source) and then time-series data. Many column
names repeat across sensor groups (e.g. `speed` appears under GPS, `calc`, and
CAN bus); we disambiguate by prefixing the source group, leaving the
sensor-agnostic indexing columns bare.

Resulting dataframe columns:
    timestamp, fragment_id, lap_number, elapsed_time, distance_traveled,
    gps_latitude, gps_longitude, gps_altitude, gps_speed, gps_accuracy,
    gps_bearing, gps_satellites, gps_fix_type, gps_device_battery_level,
    gps_device_update_rate,
    calc_combined_acc, calc_lateral_acc, calc_longitudinal_acc,
    calc_lean_angle, calc_speed, calc_device_update_rate,
    canbus_accelerator_pos, canbus_brake_pos, canbus_rpm,
    canbus_steering_angle,
    acc_x_acc, acc_y_acc, acc_z_acc, acc_device_update_rate,
    gyro_x_rate_of_rotation, gyro_y_rate_of_rotation, gyro_z_rate_of_rotation,
    gyro_device_update_rate
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Columns that have no sensor-source label and stay bare.
BARE_COLUMNS = {
    "timestamp",
    "fragment_id",
    "lap_number",
    "elapsed_time",
    "distance_traveled",
}


@dataclass
class RaceChronoMeta:
    """Header metadata from a RaceChrono CSV (lines 1-8)."""

    racechrono_version: str = ""
    format_version: str = ""
    session_title: str = ""
    session_type: str = ""
    track_name: str = ""
    driver_name: str = ""
    created_raw: str = ""
    created_utc: datetime | None = None
    note: str = ""
    source_path: str = ""
    raw: dict[str, str] = field(default_factory=dict)


def _parse_source_label(raw: str) -> str:
    """Convert a source-row cell like ``'100: gps'`` to ``'gps'``; ``'calc'`` stays ``'calc'``; empty stays empty."""
    raw = raw.strip()
    if not raw:
        return ""
    if ":" in raw:
        return raw.split(":", 1)[1].strip()
    return raw


def _disambiguate_columns(names: list[str], sources: list[str]) -> list[str]:
    """Prefix column names with their sensor source, leaving BARE_COLUMNS unprefixed."""
    out: list[str] = []
    for name, source in zip(names, sources, strict=True):
        name = name.strip()
        source = _parse_source_label(source)
        if not name:
            out.append("")
            continue
        if name in BARE_COLUMNS or not source:
            out.append(name)
        else:
            out.append(f"{source}_{name}")
    return out


def _parse_created(value: str) -> datetime | None:
    """Parse a RaceChrono ``Created`` field like ``'03/05/2026,14:24'``.

    RaceChrono writes the date in the device's locale; this parser assumes
    ``MM/DD/YYYY`` (US format) which matches the sample exports. The time is
    stored without timezone info — we treat it as naive local time and
    return a tz-aware UTC datetime by deferring to the row-level Unix
    timestamps for actual session timing. The Created field is informational.
    """
    value = value.strip().strip('"')
    if not value:
        return None
    # The CSV writes "Created,03/05/2026,14:24" so we get two comma-split parts.
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})\s*,\s*(\d{1,2}):(\d{2})", value)
    if not m:
        return None
    month, day, year, hour, minute = (int(g) for g in m.groups())
    try:
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_metadata(lines: list[str]) -> RaceChronoMeta:
    """Parse the first 9 header lines (metadata block) of a RaceChrono CSV."""
    meta = RaceChronoMeta()
    raw: dict[str, str] = {}
    for line in lines:
        line = line.rstrip("\r\n")
        if not line.strip():
            continue
        # Created spans two comma fields ("Created,03/05/2026,14:24"); keep everything after the first comma.
        parts = line.split(",", 1)
        if len(parts) != 2:
            raw[parts[0].strip()] = ""
            continue
        key, value = parts[0].strip(), parts[1].strip().strip('"')
        raw[key] = value

    meta.raw = raw
    # First line is a free-form banner (e.g. "This file is created using RaceChrono Pro v10.1.3 (...)")
    banner = next(iter(raw)) if raw else ""
    meta.racechrono_version = banner
    meta.format_version = raw.get("Format", "")
    meta.session_title = raw.get("Session title", "")
    meta.session_type = raw.get("Session type", "")
    meta.track_name = raw.get("Track name", "")
    meta.driver_name = raw.get("Driver name", "")
    meta.created_raw = raw.get("Created", "")
    meta.created_utc = _parse_created(meta.created_raw)
    meta.note = raw.get("Note", "")
    return meta


def parse_csv(path: str | Path) -> tuple[pd.DataFrame, RaceChronoMeta]:
    """Parse a RaceChrono Pro CSV into a (DataFrame, metadata) tuple."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig", errors="replace")

    # Walk the file once to split: 9-line metadata header, then 3 header rows, then data.
    reader = csv.reader(io.StringIO(text))
    rows: list[list[str]] = list(reader)

    # Find the column-name row: first non-empty row after a blank, starting with "timestamp".
    header_idx = None
    for i, row in enumerate(rows):
        if row and row[0].strip() == "timestamp":
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"Could not locate column header row in {path}")

    meta_lines = ["".join(c if i == 0 else "," + c for i, c in enumerate(r)) for r in rows[:header_idx]]
    meta = parse_metadata(meta_lines)
    meta.source_path = str(path)

    names_row = rows[header_idx]
    units_row = rows[header_idx + 1] if header_idx + 1 < len(rows) else []
    source_row = rows[header_idx + 2] if header_idx + 2 < len(rows) else []
    data_start = header_idx + 3

    # Pad / trim to matching length.
    width = len(names_row)
    units_row = (units_row + [""] * width)[:width]
    source_row = (source_row + [""] * width)[:width]

    columns = _disambiguate_columns(names_row, source_row)

    # Build the data section as CSV text so pandas can stream-parse it.
    data_buf = io.StringIO()
    data_writer = csv.writer(data_buf, lineterminator="\n")
    data_writer.writerow(columns)
    for r in rows[data_start:]:
        if not r or not any(cell.strip() for cell in r):
            continue
        # Trim/pad each data row to the column width to keep pandas happy.
        r = (r + [""] * width)[:width]
        data_writer.writerow(r)
    data_buf.seek(0)

    df = pd.read_csv(data_buf, low_memory=False)

    # Coerce numeric columns; the indexing columns and most sensors should be float/int.
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Convenience: store units + source map alongside the dataframe via attrs.
    df.attrs["units"] = dict(zip(columns, units_row, strict=True))
    df.attrs["sources"] = {c: _parse_source_label(s) for c, s in zip(columns, source_row, strict=True)}
    df.attrs["meta"] = meta

    return df, meta


def session_start_utc(df: pd.DataFrame) -> datetime | None:
    """Best-guess UTC datetime for the first sample in the file."""
    if "timestamp" not in df.columns or df["timestamp"].empty:
        return None
    ts = df["timestamp"].dropna()
    if ts.empty:
        return None
    return datetime.fromtimestamp(float(ts.iloc[0]), tz=timezone.utc)


def session_start_latlon(df: pd.DataFrame) -> tuple[float, float] | None:
    """First valid GPS fix in the file, used for weather lookup."""
    if "gps_latitude" not in df.columns or "gps_longitude" not in df.columns:
        return None
    sub = df[["gps_latitude", "gps_longitude"]].dropna()
    if sub.empty:
        return None
    row = sub.iloc[0]
    return float(row["gps_latitude"]), float(row["gps_longitude"])
