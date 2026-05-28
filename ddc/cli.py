"""``ddc`` CLI — import RaceChrono CSVs and generate Markdown reports.

Commands:
  ddc tracks                       List known track references.
  ddc cars                         List known car references.
  ddc import <csv> [...]           Tag, parse, weather-stamp, save, and write a session report.
  ddc list                         List imported sessions.
  ddc report <session-id>          (Re)generate the per-session report.
  ddc weekend <track> <car> <wid>  Generate the weekend rollup report.
  ddc progress <track> <car>       Generate the cross-weekend progression report.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .laps import classify_outliers, detect_laps
from .parser import parse_csv, session_start_latlon, session_start_utc
from .refs import list_cars, list_tracks
from .report import (
    write_progression_report,
    write_session_report,
    write_weekend_comparison_report,
    write_weekend_report,
)
from .storage import (
    SessionMeta,
    TireSpec,
    WEATHER_CACHE_PATH,
    csv_already_imported,
    load_manifest,
    make_session_id,
    save_session,
    sessions_for,
)
from .weather import WeatherCache, fetch_weather


console = Console()


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "--version", "-V")
def cli() -> None:
    """Track-day telemetry → Markdown for AI-assisted driver coaching."""


# ---------------------------------------------------------------------------
# tracks / cars / list
# ---------------------------------------------------------------------------

@cli.command("tracks")
def cmd_tracks() -> None:
    """List known track references (one Markdown file per track in ``tracks/``)."""
    tracks = list_tracks()
    if not tracks:
        console.print("[yellow]No tracks defined yet. Add one to tracks/<slug>.md.[/yellow]")
        return
    tbl = Table(title="Tracks", show_lines=False)
    tbl.add_column("slug")
    tbl.add_column("title")
    tbl.add_column("turns", justify="right")
    tbl.add_column("sections")
    for t in tracks:
        tbl.add_row(t.slug, t.title, str(len(t.turns)), ", ".join(t.sections.keys()) or "—")
    console.print(tbl)


@cli.command("cars")
def cmd_cars() -> None:
    """List known car references (one Markdown file per car in ``cars/``)."""
    cars = list_cars()
    if not cars:
        console.print("[yellow]No cars defined yet. Add one to cars/<slug>.md.[/yellow]")
        return
    tbl = Table(title="Cars", show_lines=False)
    tbl.add_column("slug")
    tbl.add_column("title")
    tbl.add_column("mods", justify="right")
    for c in cars:
        tbl.add_row(c.slug, c.title, str(len(c.mods)))
    console.print(tbl)


@cli.command("list")
@click.option("--track", "track_slug", help="Filter by track slug.")
@click.option("--car", "car_slug", help="Filter by car slug.")
@click.option("--weekend", "weekend_id", help="Filter by weekend id.")
def cmd_list(track_slug: str | None, car_slug: str | None, weekend_id: str | None) -> None:
    """List imported sessions, optionally filtered."""
    rows = sessions_for(track_slug=track_slug, car_slug=car_slug, weekend_id=weekend_id)
    if not rows:
        console.print("[yellow]No sessions imported yet.[/yellow]")
        return
    tbl = Table(title=f"Sessions ({len(rows)})", show_lines=False)
    tbl.add_column("session_id", overflow="fold")
    tbl.add_column("track")
    tbl.add_column("car")
    tbl.add_column("weekend")
    tbl.add_column("day")
    tbl.add_column("label")
    tbl.add_column("on-pace", justify="right")
    tbl.add_column("best", justify="right")
    for r in rows:
        best = r.get("best_lap_s")
        from .laps import format_lap_time
        best_str = format_lap_time(best) if isinstance(best, (int, float)) else "—"
        tbl.add_row(
            r.get("session_id", ""),
            r.get("track_slug", ""),
            r.get("car_slug", ""),
            r.get("weekend_id", ""),
            r.get("day_of_weekend", ""),
            r.get("session_label", ""),
            str(r.get("lap_count_on_pace", "")),
            best_str,
        )
    console.print(tbl)


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

@cli.command("import")
@click.argument("csv", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--track", "track_slug", help="Track slug (matches tracks/<slug>.md). Prompts if omitted.")
@click.option("--car", "car_slug", help="Car slug (matches cars/<slug>.md). Prompts if omitted.")
@click.option("--weekend", "weekend_id", help="Weekend identifier (e.g. 2026-05-03_vir).")
@click.option("--day", "day_of_weekend", help="Day of weekend (e.g. day_1, friday, 2026-05-03).")
@click.option("--label", "session_label", help="Session label (e.g. session_1_morning, qualifying).")
@click.option("--tire-compound", help="Tire compound (e.g. Bridgestone Potenza RE-71RS).")
@click.option("--tire-size", help="Tire size (e.g. 255/40R17).")
@click.option("--tire-age", help="Tire age / heat-cycle count (e.g. 'new' or '~12 cycles').")
@click.option("--tire-pressures", help="Cold pressures (e.g. 'FL 28 / FR 28 / RL 26 / RR 26').")
@click.option("--tire-note", help="Free-form tire note.")
@click.option("--notes", help="Driver notes for this session.")
@click.option("--no-weather", is_flag=True, help="Skip Open-Meteo weather lookup.")
@click.option("--force", is_flag=True, help="Re-import even if this CSV's bytes match an existing session.")
def cmd_import(
    csv: Path,
    track_slug: str | None,
    car_slug: str | None,
    weekend_id: str | None,
    day_of_weekend: str | None,
    session_label: str | None,
    tire_compound: str | None,
    tire_size: str | None,
    tire_age: str | None,
    tire_pressures: str | None,
    tire_note: str | None,
    notes: str | None,
    no_weather: bool,
    force: bool,
) -> None:
    """Import a RaceChrono CSV and generate its session report.

    Prompts interactively for any required tag not provided on the command line.
    """
    _import_one(
        csv=csv,
        track_slug=track_slug,
        car_slug=car_slug,
        weekend_id=weekend_id,
        day_of_weekend=day_of_weekend,
        session_label=session_label,
        tire_compound=tire_compound,
        tire_size=tire_size,
        tire_age=tire_age,
        tire_pressures=tire_pressures,
        tire_note=tire_note,
        notes=notes,
        no_weather=no_weather,
        force=force,
        previous_tires=None,
    )


def _import_one(
    *,
    csv: Path,
    track_slug: str | None,
    car_slug: str | None,
    weekend_id: str | None,
    day_of_weekend: str | None,
    session_label: str | None,
    tire_compound: str | None,
    tire_size: str | None,
    tire_age: str | None,
    tire_pressures: str | None,
    tire_note: str | None,
    notes: str | None,
    no_weather: bool,
    force: bool,
    previous_tires: TireSpec | None,
) -> tuple[str, str, str, str, TireSpec] | None:
    """Single-CSV import. Returns ``(session_id, track_slug, car_slug, weekend_id, tires)`` so the
    weekend orchestrator can re-use the resolved tags as defaults for the next CSV. Returns ``None``
    if the CSV was already imported.
    """
    csv = csv.resolve()
    console.print(f"[bold]Reading[/bold] {csv}")

    existing = csv_already_imported(csv) if not force else None
    if existing:
        console.print(f"[yellow]This CSV's bytes already match session [bold]{existing}[/bold]. Use --force to re-import.[/yellow]")
        return None

    df, rc_meta = parse_csv(csv)
    start_utc = session_start_utc(df)
    if start_utc is None:
        raise click.ClickException("CSV has no parseable timestamps; cannot determine session start.")
    start_ll = session_start_latlon(df)

    # ---- Resolve track/car interactively if not provided ----------------------
    tracks = {t.slug: t for t in list_tracks()}
    cars = {c.slug: c for c in list_cars()}
    if not tracks:
        raise click.ClickException("No tracks defined. Add a tracks/<slug>.md first.")
    if not cars:
        raise click.ClickException("No cars defined. Add a cars/<slug>.md first.")

    if not track_slug:
        suggestion = _suggest_track_slug(rc_meta.track_name, list(tracks.keys()))
        track_slug = _prompt_choice("Track slug", list(tracks.keys()), default=suggestion)
    if track_slug not in tracks:
        raise click.ClickException(f"Unknown track slug: {track_slug}. Known: {', '.join(tracks)}")

    if not car_slug:
        car_slug = _prompt_choice("Car slug", list(cars.keys()), default=next(iter(cars)))
    if car_slug not in cars:
        raise click.ClickException(f"Unknown car slug: {car_slug}. Known: {', '.join(cars)}")

    if not weekend_id:
        default_wid = f"{start_utc.date().isoformat()}_{track_slug}"
        weekend_id = click.prompt("Weekend ID", default=default_wid)
    if not day_of_weekend:
        day_of_weekend = click.prompt("Day of weekend (e.g. day_1, friday, 2026-05-03)", default=start_utc.date().isoformat())
    if not session_label:
        default_label = f"session_{start_utc.strftime('%H%M')}"
        session_label = click.prompt("Session label", default=default_label)

    # ---- Tires --------------------------------------------------------------
    # In weekend mode, default to the previous session's tires when the user didn't pass flags.
    pt = previous_tires
    console.print("[bold]Tires for this session:[/bold]")
    if pt is not None:
        console.print(f"  [dim](previous: {pt.compound or '—'} / {pt.size or '—'} / {pt.age_heat_cycles or '—'} / {pt.pressures_cold_psi or '—'})[/dim]")
    if tire_compound is None:
        tire_compound = click.prompt("  Compound", default=(pt.compound if pt else ""))
    if tire_size is None:
        tire_size = click.prompt("  Size", default=(pt.size if pt else ""))
    if tire_age is None:
        tire_age = click.prompt("  Age / heat cycles", default=(pt.age_heat_cycles if pt else ""))
    if tire_pressures is None:
        tire_pressures = click.prompt("  Cold pressures", default=(pt.pressures_cold_psi if pt else ""))
    if tire_note is None:
        tire_note = click.prompt("  Note", default=(pt.note if pt else ""))
    if notes is None:
        notes = click.prompt("Driver notes for this session", default="")

    tires = TireSpec(
        compound=tire_compound,
        size=tire_size,
        age_heat_cycles=tire_age,
        pressures_cold_psi=tire_pressures,
        note=tire_note,
    )

    # ---- Weather --------------------------------------------------------------
    weather_dict = None
    if not no_weather and start_ll is not None:
        console.print("Fetching weather from Open-Meteo…")
        cache = WeatherCache(WEATHER_CACHE_PATH)
        snap = fetch_weather(start_ll[0], start_ll[1], start_utc, cache=cache)
        weather_dict = snap.to_dict()
        console.print(f"[green]Weather:[/green] {snap.source} — temp {snap.temperature_c}°C, wind {snap.wind_speed_kmh} km/h, "
                      f"hour {snap.timestamp_utc}")

    # ---- Lap pre-pass to populate the manifest summary ------------------------
    laps = detect_laps(df)
    classify_outliers(laps)
    lap_summaries = [
        {
            "lap_number": l.lap_number,
            "index_in_session": l.index_in_session,
            "lap_time_s": l.lap_time_s,
            "lap_time_str": l.lap_time_str,
            "distance_m": l.distance_m,
            "avg_speed_mps": l.avg_speed_mps,
            "max_speed_mps": l.max_speed_mps,
            "incomplete": l.incomplete,
            "outlier": l.outlier,
            "outlier_reasons": list(l.outlier_reasons),
        }
        for l in laps
    ]
    duration_s = float(df["elapsed_time"].max()) if "elapsed_time" in df.columns and df["elapsed_time"].notna().any() else 0.0

    session_id = make_session_id(start_utc, track_slug, csv)
    meta = SessionMeta(
        session_id=session_id,
        csv_filename=csv.name,
        csv_sha256="",
        track_slug=track_slug,
        car_slug=car_slug,
        weekend_id=weekend_id,
        day_of_weekend=day_of_weekend,
        session_label=session_label,
        tires=tires,
        notes=notes,
        start_time_utc=start_utc.isoformat(),
        start_lat=start_ll[0] if start_ll else None,
        start_lon=start_ll[1] if start_ll else None,
        racechrono_session_title=rc_meta.session_title,
        racechrono_created=rc_meta.created_raw,
        weather=weather_dict,
        laps=lap_summaries,
        sample_count=len(df),
        duration_s=duration_s,
        imported_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    parquet_path, meta_path = save_session(df, meta)
    console.print(f"[green]Saved[/green] {parquet_path.name} + {meta_path.name}")

    report_path = write_session_report(session_id)
    console.print(f"[green]Report:[/green] {report_path}")

    return session_id, track_slug, car_slug, weekend_id, tires


def _suggest_track_slug(racechrono_track_name: str, candidates: list[str]) -> str | None:
    """Map RaceChrono's free-form Track name ('VIR Full') to a known slug ('vir_full')."""
    if not racechrono_track_name:
        return candidates[0] if candidates else None
    norm = racechrono_track_name.lower().replace(" ", "_").replace("-", "_")
    if norm in candidates:
        return norm
    # Loose contains-match either direction.
    for c in candidates:
        if c in norm or norm in c:
            return c
    return candidates[0] if candidates else None


def _prompt_choice(label: str, choices: list[str], default: str | None = None) -> str:
    if len(choices) == 1:
        return choices[0]
    console.print(f"  Choices: {', '.join(choices)}")
    return click.prompt(label, default=default or choices[0])


# ---------------------------------------------------------------------------
# report commands
# ---------------------------------------------------------------------------

@cli.command("report")
@click.argument("session_id")
def cmd_report(session_id: str) -> None:
    """Regenerate the per-session report for ``session_id``."""
    path = write_session_report(session_id)
    console.print(f"[green]Wrote[/green] {path}")


@cli.command("weekend")
@click.argument("track_slug")
@click.argument("car_slug")
@click.argument("weekend_id")
def cmd_weekend(track_slug: str, car_slug: str, weekend_id: str) -> None:
    """Generate the weekend-rollup report for ``track car weekend``."""
    path = write_weekend_report(track_slug, car_slug, weekend_id)
    console.print(f"[green]Wrote[/green] {path}")


@cli.command("progress")
@click.argument("track_slug")
@click.argument("car_slug")
def cmd_progress(track_slug: str, car_slug: str) -> None:
    """Generate the cross-weekend progression report for ``track car``."""
    path = write_progression_report(track_slug, car_slug)
    console.print(f"[green]Wrote[/green] {path}")


@cli.command("compare")
@click.argument("track_slug")
@click.argument("car_slug")
@click.argument("weekend_id")
def cmd_compare(track_slug: str, car_slug: str, weekend_id: str) -> None:
    """Generate the ``this weekend vs every prior weekend`` comparison report.

    The report explicitly flags tire and weather differences so attribution of
    improvements/regressions is honest.
    """
    path = write_weekend_comparison_report(track_slug, car_slug, weekend_id)
    console.print(f"[green]Wrote[/green] {path}")


# ---------------------------------------------------------------------------
# import-weekend (batch import + automatic comparison)
# ---------------------------------------------------------------------------

@cli.command("import-weekend")
@click.argument("paths", nargs=-1, type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--track", "track_slug", help="Track slug. Asked once and applied to all CSVs.")
@click.option("--car", "car_slug", help="Car slug. Asked once and applied to all CSVs.")
@click.option("--weekend", "weekend_id", help="Weekend identifier. Asked once and applied to all CSVs.")
@click.option("--no-weather", is_flag=True, help="Skip Open-Meteo weather lookup for every CSV.")
@click.option("--force", is_flag=True, help="Re-import even CSVs whose bytes match an existing session.")
def cmd_import_weekend(
    paths: tuple[Path, ...],
    track_slug: str | None,
    car_slug: str | None,
    weekend_id: str | None,
    no_weather: bool,
    force: bool,
) -> None:
    """Import all CSVs for one weekend at once.

    PATHS can be one or more files, or a single directory (all ``*.csv`` inside it
    will be imported, sorted by filename). Track / car / weekend are prompted once;
    day, label, tires, and notes are prompted per CSV, with tires defaulting to the
    previous CSV's tires for convenience when they didn't change between sessions.

    After all CSVs are imported, the weekend rollup, cross-weekend comparison, and
    full progression reports are regenerated automatically.
    """
    # Expand directories.
    csvs: list[Path] = []
    for p in paths:
        p = p.resolve()
        if p.is_dir():
            csvs.extend(sorted(p.glob("*.csv")))
        else:
            csvs.append(p)
    csvs = [c for c in csvs if c.suffix.lower() == ".csv"]
    if not csvs:
        raise click.ClickException("No .csv files found in the given paths.")

    # Sort by parsed start timestamp so importing in chronological order is the default.
    timed: list[tuple[datetime, Path]] = []
    for c in csvs:
        try:
            df, _ = parse_csv(c)
            ts = session_start_utc(df)
        except Exception as e:  # noqa: BLE001 — surface parse errors per-file rather than abort the batch.
            console.print(f"[yellow]Skipping {c.name}: parse failed ({e})[/yellow]")
            continue
        if ts is None:
            console.print(f"[yellow]Skipping {c.name}: no parseable timestamps[/yellow]")
            continue
        timed.append((ts, c))
    if not timed:
        raise click.ClickException("No importable CSVs after parse pre-check.")
    timed.sort(key=lambda x: x[0])

    console.print(f"[bold]Found {len(timed)} CSV(s) to import:[/bold]")
    for ts, c in timed:
        console.print(f"  {ts.isoformat()}  {c.name}")
    console.print("")

    # One-time prompts for track / car / weekend.
    tracks = {t.slug: t for t in list_tracks()}
    cars = {c.slug: c for c in list_cars()}
    if not tracks or not cars:
        raise click.ClickException("Define a track and a car first (tracks/<slug>.md, cars/<slug>.md).")
    if not track_slug:
        track_slug = _prompt_choice("Track slug (applies to all)", list(tracks.keys()))
    if track_slug not in tracks:
        raise click.ClickException(f"Unknown track slug: {track_slug}.")
    if not car_slug:
        car_slug = _prompt_choice("Car slug (applies to all)", list(cars.keys()))
    if car_slug not in cars:
        raise click.ClickException(f"Unknown car slug: {car_slug}.")
    if not weekend_id:
        default_wid = f"{timed[0][0].date().isoformat()}_{track_slug}"
        weekend_id = click.prompt("Weekend ID (applies to all)", default=default_wid)

    imported_session_ids: list[str] = []
    previous_tires: TireSpec | None = None
    for i, (ts, c) in enumerate(timed, start=1):
        console.print("")
        console.print(f"[bold cyan]── CSV {i}/{len(timed)}: {c.name} ──[/bold cyan]")
        result = _import_one(
            csv=c,
            track_slug=track_slug,
            car_slug=car_slug,
            weekend_id=weekend_id,
            day_of_weekend=None,
            session_label=None,
            tire_compound=None,
            tire_size=None,
            tire_age=None,
            tire_pressures=None,
            tire_note=None,
            notes=None,
            no_weather=no_weather,
            force=force,
            previous_tires=previous_tires,
        )
        if result is None:
            continue
        sid, _, _, _, tires = result
        imported_session_ids.append(sid)
        previous_tires = tires

    # Regenerate rollup + comparison + progression reports.
    console.print("")
    console.print("[bold]Regenerating weekend / comparison / progression reports…[/bold]")
    try:
        wp = write_weekend_report(track_slug, car_slug, weekend_id)
        console.print(f"  weekend rollup → {wp}")
    except Exception as e:  # noqa: BLE001
        console.print(f"  [yellow]weekend rollup skipped: {e}[/yellow]")
    try:
        cp = write_weekend_comparison_report(track_slug, car_slug, weekend_id)
        console.print(f"  comparison vs prior → {cp}")
    except Exception as e:  # noqa: BLE001
        console.print(f"  [yellow]comparison report skipped: {e}[/yellow]")
    try:
        pp = write_progression_report(track_slug, car_slug)
        console.print(f"  progression → {pp}")
    except Exception as e:  # noqa: BLE001
        console.print(f"  [yellow]progression skipped: {e}[/yellow]")

    console.print("")
    console.print(f"[bold green]Done.[/bold green] Imported {len(imported_session_ids)} session(s) for weekend `{weekend_id}`.")


if __name__ == "__main__":
    cli()
