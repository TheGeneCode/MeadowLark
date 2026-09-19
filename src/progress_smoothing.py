"""Time-windowed smoothing of yt-dlp progress events for a steady UI readout."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Final

from src.config import (
    PROGRESS_MAX_SAMPLES,
    PROGRESS_MAX_WINDOW_SECONDS,
    PROGRESS_MIN_WINDOW_SECONDS,
    PROGRESS_TOTAL_EMA_ALPHA,
    PROGRESS_UI_MIN_INTERVAL_SECONDS,
    PROGRESS_WINDOW_ELAPSED_FRACTION,
)

_MIN_SPEED_FOR_ETA: Final[float] = 1.0
"""Below one byte/second the ETA is meaningless; report None instead of a huge number."""


@dataclass(frozen=True)
class SmoothedProgress:
    """Render-ready progress values for the current file."""

    downloaded: int
    """Exact bytes on disk for the current file, clamped non-decreasing."""

    total: int | None
    """Sticky exact total, or EMA-smoothed estimate; None if unknown."""

    speed: float | None
    """Bytes/second over the rolling window; None until measurable."""

    eta: float | None
    """Seconds remaining, derived from ``speed``; None if not derivable."""


class ProgressSmoother:
    """
    Rolling-window smoother over yt-dlp ``"downloading"`` progress dicts.

    Keeps a deque of ``(timestamp, session cumulative bytes)`` samples pruned to a window
    that grows with elapsed session time, so speed and ETA settle instead of flickering.
    The cumulative series deliberately spans file boundaries -- yt-dlp switches from the
    video stream to the audio stream mid-download, and the rate must survive that.
    """

    def __init__(
        self,
        *,
        min_window: float = PROGRESS_MIN_WINDOW_SECONDS,
        max_window: float = PROGRESS_MAX_WINDOW_SECONDS,
        elapsed_fraction: float = PROGRESS_WINDOW_ELAPSED_FRACTION,
        total_alpha: float = PROGRESS_TOTAL_EMA_ALPHA,
        min_interval: float = PROGRESS_UI_MIN_INTERVAL_SECONDS,
    ) -> None:
        self._min_window = min_window
        self._max_window = max_window
        self._elapsed_fraction = elapsed_fraction
        self._total_alpha = total_alpha
        self._min_interval = min_interval
        self.reset()

    def update(self, d: dict, now: float | None = None) -> SmoothedProgress | None:
        """
        Ingest one progress dict; return render-ready values, or None to skip the repaint.

        ``now`` defaults to ``time.monotonic()`` and exists so tests can inject a clock.
        The throttle check happens *after* all state mutation: a throttled call still
        ingests its sample, because dropping samples would corrupt the rolling average.
        """
        now = time.monotonic() if now is None else now

        key = d.get("filename") or d.get("tmpfilename") or ""
        if self._file_key is not None and key != self._file_key:
            self._roll_file()
        self._file_key = key

        raw_downloaded = int(d.get("downloaded_bytes") or 0)
        self._file_downloaded = max(self._file_downloaded, raw_downloaded)
        self._update_total(d)

        cumulative = max(self._last_cumulative, self._completed_bytes + self._file_downloaded)
        self._last_cumulative = cumulative
        self._samples.append((now, cumulative))
        if self._session_start is None:
            self._session_start = now
        self._prune(now)

        speed = self._speed(d)
        eta = self._eta(speed)

        if self._last_emit is not None and now - self._last_emit < self._min_interval:
            return None
        self._last_emit = now
        return SmoothedProgress(
            downloaded=self._file_downloaded,
            total=self._total(),
            speed=speed,
            eta=eta,
        )

    def mark_file_finished(self, d: dict, now: float | None = None) -> None:
        """
        Fold a completed file into the session totals from its ``"finished"`` dict.

        Safe to call twice for the same file, and safe when no ``update()`` ever ran --
        a tiny file can finish in a single event.
        """
        now = time.monotonic() if now is None else now

        key = d.get("filename") or d.get("tmpfilename") or ""
        if self._last_finished_key is not None and key == self._last_finished_key:
            return

        final = int(d.get("total_bytes") or d.get("downloaded_bytes") or 0)
        self._file_downloaded = max(self._file_downloaded, final)

        cumulative = max(self._last_cumulative, self._completed_bytes + self._file_downloaded)
        self._last_cumulative = cumulative
        self._samples.append((now, cumulative))
        if self._session_start is None:
            self._session_start = now

        self._last_finished_key = key
        self._roll_file()

    def reset(self) -> None:
        """Return every field to its initial value; called at batch end."""
        self._samples: deque[tuple[float, int]] = deque(maxlen=PROGRESS_MAX_SAMPLES)
        self._session_start: float | None = None
        self._completed_bytes: int = 0
        self._file_key: str | None = None
        self._file_downloaded: int = 0
        self._file_total: float | None = None
        self._file_total_exact: bool = False
        self._last_cumulative: int = 0
        self._last_emit: float | None = None
        self._last_finished_key: str | None = None

    def _roll_file(self) -> None:
        """Retire the current file, keeping the sample window and cumulative series intact."""
        self._completed_bytes += self._file_downloaded
        self._file_downloaded = 0
        self._file_total = None
        self._file_total_exact = False
        self._file_key = None

    def _update_total(self, d: dict) -> None:
        """Track the current file's size: sticky when exact, EMA-smoothed when estimated."""
        exact = d.get("total_bytes")
        if exact:
            self._file_total = float(exact)
            self._file_total_exact = True
            return
        if self._file_total_exact:
            return
        est = d.get("total_bytes_estimate")
        if not est:
            return
        if self._file_total is None:
            self._file_total = float(est)
        else:
            self._file_total = (
                self._total_alpha * float(est) + (1.0 - self._total_alpha) * self._file_total
            )

    def _total(self) -> int | None:
        """Render-ready total, never below the byte count so the bar cannot exceed 100 %."""
        if self._file_total is None:
            return None
        return max(round(self._file_total), self._file_downloaded)

    def _window(self, now: float) -> float:
        """Averaging window in seconds, growing with elapsed time inside its bounds."""
        elapsed = 0.0 if self._session_start is None else now - self._session_start
        return min(self._max_window, max(self._min_window, elapsed * self._elapsed_fraction))

    def _prune(self, now: float) -> None:
        """Drop samples older than the window, never below two -- speed needs a baseline."""
        cutoff = now - self._window(now)
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def _speed(self, d: dict) -> float | None:
        """Bytes/second across the retained window, falling back to yt-dlp's own figure."""
        fallback = d.get("speed")
        fallback_speed = float(fallback) if fallback else None
        if len(self._samples) < 2:
            return fallback_speed
        t0, b0 = self._samples[0]
        t1, b1 = self._samples[-1]
        dt = t1 - t0
        if dt <= 1e-6:
            return fallback_speed
        return max(0.0, (b1 - b0) / dt)

    def _eta(self, speed: float | None) -> float | None:
        """Seconds remaining for the current file, or None when it is not derivable."""
        total = self._total()
        if total is None or speed is None or speed < _MIN_SPEED_FOR_ETA:
            return None
        if total <= self._file_downloaded:
            return None
        return (total - self._file_downloaded) / speed
