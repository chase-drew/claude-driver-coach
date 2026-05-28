"""OS-agnostic terminal progress bars for the long-running pipeline steps.

Uses `rich.progress`, which handles Windows terminals, macOS Terminal/iTerm, and
typical Linux terminals identically — including correct width handling, ANSI
color fallback, and TTY detection. When output is redirected to a file or
`--no-progress` is set, the progress UI is suppressed and replaced with a
single-line status print so logs stay clean.

Usage:

    from .progress import progress_section

    with progress_section("Building coaching inputs", total=len(weekends)) as p:
        for w in weekends:
            p.update_description(f"Processing {w.id}")
            do_work(w)
            p.advance()

The yielded handle is thread-safe — pass `p.advance` as an `on_complete`
callback to `_parallel_map` to track parallel work.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Iterator

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)


# Modern Windows Terminal / iTerm / GNOME Terminal speak UTF-8 fine, but older
# Windows consoles default to cp1252, which crashes when rich emits its default
# braille-character spinner or box-drawing bars. Reconfiguring stdout to UTF-8
# with replacement errors is safe everywhere: macOS and Linux already use UTF-8,
# and on Windows this is the same opt-in PowerShell 7 / Windows Terminal use.
def _reconfigure_stdout_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass

_reconfigure_stdout_utf8()


# Module-level state. `disable_progress()` is called by the CLI when `--no-progress`
# is passed or when stdout isn't a terminal.
_console = Console()
_disabled = False
_lock = Lock()


def disable_progress() -> None:
    """Suppress progress bars globally. Status lines still print."""
    global _disabled
    with _lock:
        _disabled = True


def enable_progress() -> None:
    global _disabled
    with _lock:
        _disabled = False


def _progress_visible() -> bool:
    """True when we should draw an interactive progress bar.

    Suppressed when:
      * the user passed --no-progress
      * stdout isn't a TTY (redirected to a file / piped through another command)
      * stdout is the special non-interactive console (e.g. CI runners)
    """
    if _disabled:
        return False
    if not sys.stdout.isatty():
        return False
    return _console.is_terminal


@dataclass
class _ProgressHandle:
    """Returned by `progress_section`; thread-safe advance + description update."""

    _advance: Callable[[int], None]
    _set_desc: Callable[[str], None]

    def advance(self, n: int = 1) -> None:
        self._advance(n)

    def update_description(self, description: str) -> None:
        self._set_desc(description)

    # alias so this can be passed straight as a callback (matches `_parallel_map`'s
    # `on_complete: Callable[[], None]` signature).
    def __call__(self) -> None:
        self._advance(1)


@contextmanager
def progress_section(description: str, total: int | None) -> Iterator[_ProgressHandle]:
    """Context manager that draws a progress bar with elapsed time and ETA.

    When ``total`` is unknown (None or 0), shows a spinner with elapsed time
    only — no bar, no ETA, since rich can't extrapolate. Falls back to a
    one-line status print when the terminal isn't interactive.
    """
    visible = _progress_visible() and bool(total)
    if not visible:
        _console.print(f"[dim]> {description}...[/dim]")
        # No-op handle so callers don't need to branch on visibility.
        def _noop_adv(n: int = 1) -> None:
            return
        def _noop_desc(d: str) -> None:
            return
        yield _ProgressHandle(_noop_adv, _noop_desc)
        return

    # Use ASCII-safe spinner ("line" = `-`, `\`, `|`, `/`) so we never crash
    # on a legacy cp1252 Windows console even if the UTF-8 reconfigure didn't
    # take effect. The bar itself only uses block + dash characters, which
    # rich gracefully degrades to ASCII when `safe_box=True` is on the console.
    columns = [
        SpinnerColumn(spinner_name="line"),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TextColumn("|"),
        TimeElapsedColumn(),
        TextColumn("| ETA"),
        TimeRemainingColumn(),
    ]
    with Progress(*columns, console=_console, transient=False, refresh_per_second=8) as bar:
        task_id = bar.add_task(description, total=total)

        def _advance(n: int = 1) -> None:
            bar.advance(task_id, n)

        def _set_desc(d: str) -> None:
            bar.update(task_id, description=d)

        yield _ProgressHandle(_advance, _set_desc)


@contextmanager
def status_line(description: str) -> Iterator[None]:
    """Show a transient spinner with a single description while the block runs.

    Use this for indeterminate-duration steps (e.g. weather fetch, parquet load)
    that aren't worth a full progress bar but still benefit from "yes, the
    machine is doing something" feedback. Becomes a plain status print when
    progress is disabled or output is redirected.
    """
    if not _progress_visible():
        _console.print(f"[dim]> {description}...[/dim]")
        yield
        return
    with _console.status(f"[bold cyan]{description}...", spinner="line"):
        yield
