"""Loaders for hand-curated track and car reference Markdown files.

Reference files use a simple Markdown table format so they double as
human-readable documentation. The loaders only need a few well-known
sections (`## Start / Finish`, `## Turns`, `## Sections (named complexes)`)
to extract structured data; everything else is preserved as prose for
downstream Claude coaching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
TRACKS_DIR = REPO_ROOT / "tracks"
CARS_DIR = REPO_ROOT / "cars"


@dataclass
class Turn:
    number: str           # "1", "5a", "17a"
    name: str             # "Turn 1"
    section: str          # "snake", "esses", ...
    latitude: float
    longitude: float

    @property
    def sort_key(self) -> tuple[int, str]:
        m = re.match(r"(\d+)([a-z]?)", self.number.lower())
        return (int(m.group(1)) if m else 999, m.group(2) if m else "")


@dataclass
class Sector:
    """A timed sector defined by a start/end turn-apex passage.

    The start/end anchors are either turn ``number`` strings ("1", "5a", "6b")
    or the special string ``"start_finish"`` indicating the lap's S/F crossing.
    """

    number: int
    name: str
    start_anchor: str
    end_anchor: str


@dataclass
class TrackRef:
    slug: str
    title: str
    start_finish: tuple[float, float] | None
    turns: list[Turn] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)
    sectors: list[Sector] = field(default_factory=list)
    notes_markdown: str = ""
    source_path: Path | None = None


@dataclass
class Gear:
    """A single forward gear, characterised by its measured `mph per 1000 RPM` ratio."""

    number: int                 # 1..6
    mph_per_1000_rpm: float     # speed (mph) the car travels at 1000 RPM in this gear


@dataclass
class CarRef:
    slug: str
    title: str
    year: int | None
    make: str
    model: str
    trim: str
    mods: list[tuple[str, str]] = field(default_factory=list)
    gears: list[Gear] = field(default_factory=list)  # empty if drivetrain section missing
    notes_markdown: str = ""
    source_path: Path | None = None

    def gear_boundaries(self) -> list[float]:
        """Return the boundaries (mph/1000RPM) between adjacent gears, in gear order.

        A sample's ratio < boundaries[i] means it's in gear (i+1); ≥ means (i+2) or higher.
        Uses the geometric mean of adjacent gear ratios as the boundary (correct for
        multiplicative gear steps). Length is ``len(gears) - 1``.
        """
        if len(self.gears) < 2:
            return []
        sorted_g = sorted(self.gears, key=lambda g: g.number)
        return [
            (sorted_g[i].mph_per_1000_rpm * sorted_g[i + 1].mph_per_1000_rpm) ** 0.5
            for i in range(len(sorted_g) - 1)
        ]


_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")


def _parse_table(lines: list[str]) -> list[list[str]]:
    """Parse a contiguous Markdown table into a list of cell rows (header included)."""
    rows: list[list[str]] = []
    for line in lines:
        m = _TABLE_ROW_RE.match(line)
        if not m:
            break
        cells = [c.strip() for c in m.group(1).split("|")]
        # Skip the separator row (|---|---|---|)
        if all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue
        rows.append(cells)
    return rows


def _read_bullet_line(line: str) -> tuple[str, str] | None:
    """Parse ``- **key**: value`` style metadata bullets."""
    m = re.match(r"^\s*-\s*\*\*(.+?)\*\*\s*:\s*`?([^`]*)`?\s*$", line)
    if not m:
        return None
    return m.group(1).strip().lower(), m.group(2).strip()


def _split_sections(md: str) -> dict[str, list[str]]:
    """Split a Markdown doc into a ``{heading: lines}`` map keyed by ``##`` headings."""
    sections: dict[str, list[str]] = {"_preamble": []}
    current = "_preamble"
    for line in md.splitlines():
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h:
            current = h.group(1).strip()
            sections[current] = []
        else:
            sections[current].append(line)
    return sections


def load_track(path: str | Path) -> TrackRef:
    """Load a ``tracks/<slug>.md`` reference into a :class:`TrackRef`."""
    path = Path(path)
    md = path.read_text(encoding="utf-8")
    sections = _split_sections(md)

    title_match = re.search(r"^#\s+(.+?)\s*$", md, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem

    slug = path.stem
    for line in sections.get("_preamble", []):
        kv = _read_bullet_line(line)
        if kv and kv[0] == "slug":
            slug = kv[1] or slug

    start_finish: tuple[float, float] | None = None
    for heading, body in sections.items():
        if heading.lower().startswith("start"):
            rows = _parse_table(body[_first_table_index(body):])
            if len(rows) >= 2:
                # header row + at least one data row
                header = [c.lower() for c in rows[0]]
                data = rows[1]
                rec = dict(zip(header, data, strict=False))
                try:
                    start_finish = (float(rec["latitude"]), float(rec["longitude"]))
                except (KeyError, ValueError):
                    pass
            break

    turns: list[Turn] = []
    for heading, body in sections.items():
        if heading.lower() == "turns":
            rows = _parse_table(body[_first_table_index(body):])
            if not rows:
                break
            header = [c.lower() for c in rows[0]]
            for row in rows[1:]:
                rec = dict(zip(header, row, strict=False))
                try:
                    turns.append(
                        Turn(
                            number=rec.get("#", "").strip(),
                            name=rec.get("name", "").strip(),
                            section=rec.get("section", "").strip(),
                            latitude=float(rec["latitude"]),
                            longitude=float(rec["longitude"]),
                        )
                    )
                except (KeyError, ValueError):
                    continue
            break

    sections_map: dict[str, list[str]] = {}
    for heading, body in sections.items():
        if heading.lower().startswith("sections"):
            for line in body:
                m = re.match(r"^\s*-\s*\*\*(.+?)\*\*\s*:\s*(.+)$", line)
                if m:
                    section_name = m.group(1).strip()
                    turn_list = [t.strip() for t in re.split(r"[→,]", m.group(2)) if t.strip()]
                    sections_map[section_name] = turn_list
            break

    sector_list: list[Sector] = []
    for heading, body in sections.items():
        if heading.lower().startswith("sectors"):
            rows = _parse_table(body[_first_table_index(body):])
            if not rows:
                break
            header = [c.lower() for c in rows[0]]
            for row in rows[1:]:
                rec = dict(zip(header, row, strict=False))
                try:
                    num = int(rec.get("#", "").strip())
                except (ValueError, TypeError):
                    continue
                start_anchor = _normalize_anchor(rec.get("start anchor", "").strip())
                end_anchor = _normalize_anchor(rec.get("end anchor", "").strip())
                name = rec.get("name", "").strip()
                if name and start_anchor and end_anchor:
                    sector_list.append(Sector(number=num, name=name, start_anchor=start_anchor, end_anchor=end_anchor))
            break

    notes_md = "\n".join(sections.get("Notes for analysis", [])).strip()

    return TrackRef(
        slug=slug,
        title=title,
        start_finish=start_finish,
        turns=sorted(turns, key=lambda t: t.sort_key),
        sections=sections_map,
        sectors=sorted(sector_list, key=lambda s: s.number),
        notes_markdown=notes_md,
        source_path=path,
    )


def _normalize_anchor(raw: str) -> str:
    """Normalize a sector boundary anchor: ``'start/finish'`` → ``'start_finish'``, ``'T1'`` → ``'1'``."""
    s = raw.lower().strip()
    if s in ("start/finish", "start_finish", "s/f", "sf"):
        return "start_finish"
    if s.startswith("t") and len(s) > 1:
        s = s[1:]
    return s.strip()


def load_car(path: str | Path) -> CarRef:
    """Load a ``cars/<slug>.md`` reference into a :class:`CarRef`."""
    path = Path(path)
    md = path.read_text(encoding="utf-8")
    sections = _split_sections(md)

    title_match = re.search(r"^#\s+(.+?)\s*$", md, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem

    slug = path.stem
    year: int | None = None
    make = model = trim = ""
    for line in sections.get("_preamble", []):
        kv = _read_bullet_line(line)
        if not kv:
            continue
        k, v = kv
        if k == "slug":
            slug = v or slug
        elif k == "year":
            try:
                year = int(v)
            except ValueError:
                pass
        elif k == "make":
            make = v
        elif k == "model":
            model = v
        elif k == "trim":
            trim = v

    mods: list[tuple[str, str]] = []
    for heading, body in sections.items():
        if heading.lower() == "modifications":
            rows = _parse_table(body[_first_table_index(body):])
            if not rows:
                break
            header = [c.lower() for c in rows[0]]
            for row in rows[1:]:
                rec = dict(zip(header, row, strict=False))
                cat = rec.get("category", "").strip()
                item = rec.get("item", "").strip()
                if item:
                    mods.append((cat, item))
            break

    gears: list[Gear] = []
    for heading, body in sections.items():
        if heading.lower().startswith("drivetrain"):
            rows = _parse_table(body[_first_table_index(body):])
            if not rows:
                break
            header = [c.lower() for c in rows[0]]
            for row in rows[1:]:
                rec = dict(zip(header, row, strict=False))
                gear_str = rec.get("gear", "").strip()
                ratio_str = rec.get("mph_per_1000_rpm", "").strip()
                try:
                    gnum = int(gear_str)
                    # ratio cell may include a parenthetical comment like "25.18 (extrapolated)"
                    ratio_clean = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)", ratio_str)
                    if ratio_clean:
                        gears.append(Gear(number=gnum, mph_per_1000_rpm=float(ratio_clean.group(1))))
                except (ValueError, TypeError):
                    continue
            break

    notes_md = "\n".join(sections.get("Notes for analysis", [])).strip()

    return CarRef(
        slug=slug,
        title=title,
        year=year,
        make=make,
        model=model,
        trim=trim,
        mods=mods,
        gears=sorted(gears, key=lambda g: g.number),
        notes_markdown=notes_md,
        source_path=path,
    )


def _first_table_index(lines: list[str]) -> int:
    """Find the line index where the first Markdown table starts in ``lines``."""
    for i, line in enumerate(lines):
        if _TABLE_ROW_RE.match(line):
            return i
    return len(lines)


def list_tracks() -> list[TrackRef]:
    if not TRACKS_DIR.exists():
        return []
    return [load_track(p) for p in sorted(TRACKS_DIR.glob("*.md"))]


def list_cars() -> list[CarRef]:
    if not CARS_DIR.exists():
        return []
    return [load_car(p) for p in sorted(CARS_DIR.glob("*.md"))]


def find_track(slug: str) -> TrackRef:
    path = TRACKS_DIR / f"{slug}.md"
    if not path.exists():
        raise FileNotFoundError(f"No track reference at {path}")
    return load_track(path)


def find_car(slug: str) -> CarRef:
    path = CARS_DIR / f"{slug}.md"
    if not path.exists():
        raise FileNotFoundError(f"No car reference at {path}")
    return load_car(path)
