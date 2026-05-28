"""Open-Meteo historical weather client with on-disk caching.

We use Open-Meteo's free `archive-api` endpoint, which serves ERA5 reanalysis
for past dates and ICON/GFS for the recent past. It accepts a lat/lon and a
date range and returns hourly data. We round the requested timestamp to the
nearest hour and cache the response keyed by (lat, lon, hour) so repeated
imports never re-fetch.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"  # for very recent / future-dated test data
HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation",
    "rain",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "surface_pressure",
]


@dataclass
class WeatherSnapshot:
    timestamp_utc: str          # ISO 8601 of the rounded hour actually queried
    latitude: float
    longitude: float
    temperature_c: float | None
    relative_humidity_pct: float | None
    dew_point_c: float | None
    precipitation_mm: float | None
    rain_mm: float | None
    cloud_cover_pct: float | None
    wind_speed_kmh: float | None
    wind_direction_deg: float | None
    wind_gusts_kmh: float | None
    surface_pressure_hpa: float | None
    source: str                 # "open-meteo-archive" | "open-meteo-forecast" | "unavailable"
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class WeatherCache:
    """Tiny JSON-on-disk cache; one file holds every (lat, lon, hour) key we've seen."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {}

    @staticmethod
    def _key(lat: float, lon: float, hour_iso: str) -> str:
        # Round coords to ~110m precision; weather grids are much coarser anyway.
        return f"{round(lat, 3)}|{round(lon, 3)}|{hour_iso}"

    def get(self, lat: float, lon: float, hour_iso: str) -> WeatherSnapshot | None:
        raw = self._data.get(self._key(lat, lon, hour_iso))
        if raw is None:
            return None
        return WeatherSnapshot(**raw)

    def put(self, snap: WeatherSnapshot) -> None:
        self._data[self._key(snap.latitude, snap.longitude, snap.timestamp_utc)] = snap.to_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")


def _round_to_hour(dt: datetime) -> datetime:
    dt = dt.astimezone(timezone.utc)
    return dt.replace(minute=0, second=0, microsecond=0)


def _pick_hour(payload: dict, target_iso: str) -> dict[str, float | None]:
    """Pull the row matching ``target_iso`` out of Open-Meteo's parallel-list hourly response."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return {}
    if target_iso in times:
        idx = times.index(target_iso)
    else:
        # Find the closest hour we have.
        target_dt = datetime.fromisoformat(target_iso)
        diffs = [
            abs((datetime.fromisoformat(t) - target_dt).total_seconds())
            for t in times
        ]
        idx = diffs.index(min(diffs))
    out: dict[str, float | None] = {}
    for var in HOURLY_VARS:
        series = hourly.get(var)
        if series and idx < len(series):
            out[var] = series[idx]
        else:
            out[var] = None
    return out


def _fetch(url: str, lat: float, lon: float, hour: datetime, timeout: float) -> dict:
    date_str = hour.date().isoformat()
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": date_str,
        "end_date": date_str,
        "hourly": ",".join(HOURLY_VARS),
        "timezone": "UTC",
        "wind_speed_unit": "kmh",
    }
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_weather(
    lat: float,
    lon: float,
    when: datetime,
    cache: WeatherCache | None = None,
    timeout: float = 15.0,
) -> WeatherSnapshot:
    """Return a :class:`WeatherSnapshot` for the hour containing ``when`` at the given coords.

    If the archive endpoint has no data yet (very recent timestamps), falls back
    to the forecast endpoint. Network failures return a snapshot with
    ``source="unavailable"`` and a populated ``note`` rather than raising.
    """
    hour = _round_to_hour(when)
    hour_iso = hour.strftime("%Y-%m-%dT%H:%M")

    if cache is not None:
        cached = cache.get(lat, lon, hour_iso)
        if cached is not None:
            return cached

    snap = WeatherSnapshot(
        timestamp_utc=hour_iso,
        latitude=lat,
        longitude=lon,
        temperature_c=None,
        relative_humidity_pct=None,
        dew_point_c=None,
        precipitation_mm=None,
        rain_mm=None,
        cloud_cover_pct=None,
        wind_speed_kmh=None,
        wind_direction_deg=None,
        wind_gusts_kmh=None,
        surface_pressure_hpa=None,
        source="unavailable",
    )

    # Archive endpoint covers most past dates; recent or future ones need forecast.
    age_days = (datetime.now(timezone.utc) - hour).days
    endpoints = [(ARCHIVE_URL, "open-meteo-archive")] if age_days > 5 else [
        (FORECAST_URL, "open-meteo-forecast"),
        (ARCHIVE_URL, "open-meteo-archive"),
    ]

    last_err: str = ""
    for url, source in endpoints:
        try:
            payload = _fetch(url, lat, lon, hour, timeout)
            picked = _pick_hour(payload, hour_iso)
            if not picked or all(v is None for v in picked.values()):
                last_err = f"empty payload from {source}"
                continue
            snap = WeatherSnapshot(
                timestamp_utc=hour_iso,
                latitude=lat,
                longitude=lon,
                temperature_c=picked.get("temperature_2m"),
                relative_humidity_pct=picked.get("relative_humidity_2m"),
                dew_point_c=picked.get("dew_point_2m"),
                precipitation_mm=picked.get("precipitation"),
                rain_mm=picked.get("rain"),
                cloud_cover_pct=picked.get("cloud_cover"),
                wind_speed_kmh=picked.get("wind_speed_10m"),
                wind_direction_deg=picked.get("wind_direction_10m"),
                wind_gusts_kmh=picked.get("wind_gusts_10m"),
                surface_pressure_hpa=picked.get("surface_pressure"),
                source=source,
            )
            break
        except (requests.RequestException, ValueError) as e:
            last_err = f"{source}: {e}"
            continue
    else:
        snap.note = last_err or "no data"

    if cache is not None and snap.source != "unavailable":
        cache.put(snap)
    elif cache is not None:
        # Cache the unavailable result too so we don't hammer the API for known-missing data.
        snap.note = last_err
        cache.put(snap)

    return snap


def conditions_summary(snap: WeatherSnapshot) -> str:
    """Compose a short prose summary for inclusion in Markdown reports."""
    if snap.source == "unavailable":
        return f"Weather unavailable ({snap.note or 'no data'})."
    parts = []
    if snap.temperature_c is not None:
        f = snap.temperature_c * 9 / 5 + 32
        parts.append(f"{snap.temperature_c:.1f}°C ({f:.0f}°F)")
    if snap.relative_humidity_pct is not None:
        parts.append(f"{snap.relative_humidity_pct:.0f}% RH")
    if snap.cloud_cover_pct is not None:
        parts.append(f"{snap.cloud_cover_pct:.0f}% cloud")
    if snap.wind_speed_kmh is not None and snap.wind_direction_deg is not None:
        parts.append(f"wind {snap.wind_speed_kmh:.0f} km/h @ {snap.wind_direction_deg:.0f}°")
    if snap.precipitation_mm is not None and snap.precipitation_mm > 0:
        parts.append(f"precip {snap.precipitation_mm:.1f} mm")
    return ", ".join(parts) if parts else "no data"
