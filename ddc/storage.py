"""Session library on disk.

Every imported CSV becomes a ``Session`` with three artifacts in ``sessions/``:

  * ``<session-id>.parquet`` — the parsed telemetry, including units/sources in metadata
  * ``<session-id>.meta.json`` — user-provided tags, weather snapshot, lap summary
  * an entry in ``sessions/manifest.json`` — the index used for listing/grouping/comparison

Reports are written to ``reports/`` and grouped by track / car for easy diffing.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .weather import WeatherSnapshot

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = REPO_ROOT / "sessions"
REPORTS_DIR = REPO_ROOT / "reports"
MANIFEST_PATH = SESSIONS_DIR / "manifest.json"
WEATHER_CACHE_PATH = SESSIONS_DIR / "weather_cache.json"


@dataclass
class TireSpec:
    compound: str = ""             # e.g. "Bridgestone Potenza RE-71RS"
    size: str = ""                 # e.g. "255/40R17"
    age_heat_cycles: str = ""      # free-form, e.g. "~12 heat cycles" or "new"
    pressures_cold_psi: str = ""   # free-form, e.g. "FL 28 / FR 28 / RL 26 / RR 26"
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LapSummary:
    """Lightweight per-lap summary stored in the .meta.json (full data lives in parquet)."""

    lap_number: int
    index_in_session: int
    lap_time_s: float
    lap_time_str: str
    distance_m: float
    avg_speed_mps: float
    max_speed_mps: float
    incomplete: bool
    outlier: bool
    outlier_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SessionMeta:
    session_id: str
    csv_filename: str
    csv_sha256: str
    track_slug: str
    car_slug: str
    weekend_id: str               # e.g. "2026-05-03_vir" — used to group multi-day events
    day_of_weekend: str           # "day_1", "day_2", or a date string the user picked
    session_label: str            # e.g. "session_1_morning", "afternoon_qualifying", free-form
    tires: TireSpec = field(default_factory=TireSpec)
    notes: str = ""
    start_time_utc: str = ""      # ISO 8601
    start_lat: float | None = None
    start_lon: float | None = None
    racechrono_session_title: str = ""
    racechrono_created: str = ""
    weather: dict | None = None
    laps: list[dict] = field(default_factory=list)
    sample_count: int = 0
    duration_s: float = 0.0
    imported_at_utc: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# IDs and paths
# ---------------------------------------------------------------------------

def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "untitled"


def make_session_id(start_utc: datetime, track_slug: str, csv_path: Path) -> str:
    """Stable ID derived from the session start, track, and a short content hash.

    Format: ``YYYYMMDD_HHMMSS_<track>_<hash8>``. The hash ensures two CSVs imported
    with the same start timestamp don't collide.
    """
    stamp = start_utc.astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short = _sha256_of(csv_path)[:8]
    return f"{stamp}_{track_slug}_{short}"


def session_paths(session_id: str) -> tuple[Path, Path]:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR / f"{session_id}.parquet", SESSIONS_DIR / f"{session_id}.meta.json"


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------

def save_session(df: pd.DataFrame, meta: SessionMeta) -> tuple[Path, Path]:
    """Persist the parsed dataframe (parquet) + metadata (JSON), then update the manifest."""
    parquet_path, meta_path = session_paths(meta.session_id)
    # Parquet writer JSON-serializes df.attrs; drop the parser's RaceChronoMeta which isn't.
    safe_df = df.copy(deep=False)
    safe_attrs: dict = {}
    for k, v in df.attrs.items():
        try:
            json.dumps(v)
            safe_attrs[k] = v
        except TypeError:
            continue
    safe_df.attrs = safe_attrs
    safe_df.to_parquet(parquet_path, index=False)
    meta_path.write_text(json.dumps(meta.to_dict(), indent=2, default=str), encoding="utf-8")
    _update_manifest(meta)
    return parquet_path, meta_path


def load_session_meta(session_id: str) -> SessionMeta:
    _, meta_path = session_paths(session_id)
    if not meta_path.exists():
        raise FileNotFoundError(f"No session metadata at {meta_path}")
    raw = json.loads(meta_path.read_text(encoding="utf-8"))
    tires = TireSpec(**raw.pop("tires", {}) or {})
    return SessionMeta(tires=tires, **raw)


def load_session_df(session_id: str) -> pd.DataFrame:
    parquet_path, _ = session_paths(session_id)
    if not parquet_path.exists():
        raise FileNotFoundError(f"No session parquet at {parquet_path}")
    return pd.read_parquet(parquet_path)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"sessions": []}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _update_manifest(meta: SessionMeta) -> None:
    manifest = load_manifest()
    sessions = [s for s in manifest.get("sessions", []) if s.get("session_id") != meta.session_id]
    sessions.append(
        {
            "session_id": meta.session_id,
            "track_slug": meta.track_slug,
            "car_slug": meta.car_slug,
            "weekend_id": meta.weekend_id,
            "day_of_weekend": meta.day_of_weekend,
            "session_label": meta.session_label,
            "start_time_utc": meta.start_time_utc,
            "csv_filename": meta.csv_filename,
            "tires": meta.tires.to_dict() if isinstance(meta.tires, TireSpec) else (meta.tires or {}),
            "lap_count_on_pace": sum(1 for l in meta.laps if not (l.get("outlier") or l.get("incomplete"))),
            "lap_count_total": len(meta.laps),
            "best_lap_s": _best_lap_s(meta.laps),
        }
    )
    sessions.sort(key=lambda s: s.get("start_time_utc") or "")
    manifest["sessions"] = sessions
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")


def _best_lap_s(laps: list[dict]) -> float | None:
    pool = [
        l.get("lap_time_s")
        for l in laps
        if not (l.get("outlier") or l.get("incomplete")) and isinstance(l.get("lap_time_s"), (int, float))
    ]
    return min(pool) if pool else None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def sessions_for(track_slug: str | None = None, car_slug: str | None = None, weekend_id: str | None = None) -> list[dict]:
    """Filter the manifest by any combination of track / car / weekend."""
    out = []
    for s in load_manifest().get("sessions", []):
        if track_slug and s.get("track_slug") != track_slug:
            continue
        if car_slug and s.get("car_slug") != car_slug:
            continue
        if weekend_id and s.get("weekend_id") != weekend_id:
            continue
        out.append(s)
    return out


def weekend_ids(track_slug: str | None = None, car_slug: str | None = None) -> list[str]:
    """Distinct weekend IDs, optionally filtered by track/car, sorted chronologically (alpha works for ISO dates)."""
    seen: dict[str, str] = {}
    for s in sessions_for(track_slug=track_slug, car_slug=car_slug):
        wid = s.get("weekend_id") or ""
        if wid and wid not in seen:
            seen[wid] = s.get("start_time_utc") or ""
    return sorted(seen.keys(), key=lambda k: seen[k])


def csv_already_imported(csv_path: Path) -> str | None:
    """Return the existing ``session_id`` if this exact CSV bytes have been imported, else ``None``."""
    sha = _sha256_of(csv_path)
    for s in load_manifest().get("sessions", []):
        sid = s.get("session_id", "")
        if sid.endswith(f"_{sha[:8]}"):
            return sid
    return None


def report_dir_for(track_slug: str, car_slug: str) -> Path:
    """Reports are nested by track then car: ``reports/<track>/<car>/``."""
    p = REPORTS_DIR / track_slug / car_slug
    p.mkdir(parents=True, exist_ok=True)
    return p
