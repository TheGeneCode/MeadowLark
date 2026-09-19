"""Unit tests for src.progress_smoothing: time-windowed download progress smoothing."""

from src.progress_smoothing import ProgressSmoother


def _tick(downloaded: int, total: int | None = None, *, filename: str = "a.mp4", **extra) -> dict:
    """Construct a minimal progress dict for testing."""
    d: dict = {"status": "downloading", "downloaded_bytes": downloaded, "filename": filename}
    if total is not None:
        d["total_bytes"] = total
    d.update(extra)
    return d


def test_speed_averages_over_window() -> None:
    """Speed is the average over the window, not the last interval's raw speed."""
    smoother = ProgressSmoother(
        min_window=4.0, max_window=30.0, elapsed_fraction=0.25, min_interval=0.0
    )
    # Alternating spiky: 0 → 10M (10M/s) → 10.1M (100k/s) → 20M (10M/s) → 20.1M (100k/s)
    smoother.update(_tick(0, total=100_000_000), now=0.0)
    smoother.update(_tick(10_000_000, total=100_000_000), now=1.0)
    smoother.update(_tick(10_100_000, total=100_000_000), now=2.0)
    smoother.update(_tick(20_000_000, total=100_000_000), now=3.0)
    result = smoother.update(_tick(20_100_000, total=100_000_000), now=4.0)

    assert result is not None
    # Over 4 seconds, 20.1M bytes = 5.025M B/s average; within 1% of that
    assert result.speed is not None
    assert 4_974_750 <= result.speed <= 5_075_250


def test_window_grows_with_elapsed_time() -> None:
    """Window grows from min_window up to max_window as elapsed time increases."""
    smoother = ProgressSmoother(
        min_window=3.0, max_window=30.0, elapsed_fraction=0.25, min_interval=0.0
    )
    smoother._session_start = 0.0

    # now=0 → elapsed=0 → window = max(3, min(30, 0*0.25)) = 3.0
    assert smoother._window(0.0) == 3.0

    # now=4 → elapsed=4 → window = max(3, min(30, 4*0.25)) = 3.0 (still at floor)
    assert smoother._window(4.0) == 3.0

    # now=40 → elapsed=40 → window = max(3, min(30, 40*0.25)) = 10.0
    assert smoother._window(40.0) == 10.0

    # now=200 → elapsed=200 → window = max(3, min(30, 200*0.25)) = 30.0 (at ceiling)
    assert smoother._window(200.0) == 30.0


def test_window_caps_at_max() -> None:
    """Sample count stays bounded even on very long downloads."""
    smoother = ProgressSmoother(
        min_window=3.0, max_window=30.0, elapsed_fraction=0.25, min_interval=0.0
    )
    # Feed ticks 1 s apart for 300 simulated seconds at constant 1 MB/s
    for i in range(300):
        smoother.update(
            _tick(i * 1_000_000, total=1_000_000_000),
            now=float(i),
        )
    # Window at t=300 is min(30, max(3, 300*0.25)) = 30.0
    # Samples older than 300-30=270 should be pruned
    assert len(smoother._samples) <= 32  # ~31 samples for 30-second window at 1s interval
    # Speed should be approximately 1 MB/s
    assert smoother._samples[-1][1] > 200_000_000  # Cumulative should be high


def test_eta_derived_from_smoothed_speed() -> None:
    """ETA is derived from the smoothed speed, not injected from d['eta']."""
    smoother = ProgressSmoother(min_interval=0.0)
    # Steady 10 MB/s for 5 seconds; at 50M downloaded, 50M remain
    for i in range(5):
        smoother.update(_tick(i * 10_000_000, total=100_000_000), now=float(i))
    result = smoother.update(_tick(50_000_000, total=100_000_000), now=5.0)

    assert result is not None
    assert result.eta is not None
    # (100M - 50M) / 10MB/s = 5.0; within ±0.2 for floating-point wiggle
    assert 4.8 <= result.eta <= 5.2


def test_eta_none_when_speed_below_threshold() -> None:
    """ETA is None when speed < 1 B/s; no huge or infinite ETA, no ZeroDivisionError."""
    smoother = ProgressSmoother(min_interval=0.0)
    # Two ticks 10 s apart with downloaded unchanged at 1000
    smoother.update(_tick(1000, total=100_000), now=0.0)
    result = smoother.update(_tick(1000, total=100_000), now=10.0)

    assert result is not None
    assert result.speed == 0.0  # (1000-1000) / 10 = 0
    assert result.eta is None


def test_eta_none_when_total_unknown() -> None:
    """ETA is None when total is unknown; speed is still computed."""
    smoother = ProgressSmoother(min_interval=0.0)
    # Ticks with downloaded_bytes only, no total
    smoother.update(_tick(5_000_000, total=None), now=0.0)
    result = smoother.update(_tick(10_000_000, total=None), now=1.0)

    assert result is not None
    assert result.total is None
    assert result.eta is None
    # Speed should still be computable
    assert result.speed is not None


def test_downloaded_never_decreases() -> None:
    """Clamping ensures downloaded never decreases for the same file."""
    smoother = ProgressSmoother(min_interval=0.0)
    smoother.update(_tick(5_000_000, total=100_000_000), now=0.0)
    result = smoother.update(_tick(3_000_000, total=100_000_000), now=1.0)

    assert result is not None
    assert result.downloaded == 5_000_000


def test_total_estimate_is_ema_smoothed() -> None:
    """total_bytes_estimate is EMA-smoothed, not taken as-is."""
    smoother = ProgressSmoother(
        total_alpha=0.2,
        min_interval=0.0,  # 20% weight on new estimate
    )
    smoother.update(_tick(10_000_000, total=None, total_bytes_estimate=100_000_000), now=0.0)
    result = smoother.update(
        _tick(20_000_000, total=None, total_bytes_estimate=200_000_000), now=1.0
    )

    assert result is not None
    assert result.total is not None
    # 0.2 * 200M + 0.8 * 100M = 40M + 80M = 120M
    expected = 120_000_000
    assert result.total == expected


def test_exact_total_wins_and_sticks() -> None:
    """Once exact total_bytes is seen, never fall back to estimate."""
    smoother = ProgressSmoother(min_interval=0.0)
    # Tick 1: estimate only
    smoother.update(_tick(10_000_000, total=None, total_bytes_estimate=100_000_000), now=0.0)
    # Tick 2: exact total (95M, not 100M)
    smoother.update(_tick(20_000_000, total=95_000_000), now=1.0)
    # Tick 3: a new estimate arrives (300M), but exact already seen
    result = smoother.update(
        _tick(30_000_000, total=None, total_bytes_estimate=300_000_000), now=2.0
    )

    assert result is not None
    # Should stick at 95M, not regress to 300M or any blend
    assert result.total == 95_000_000


def test_total_never_below_downloaded() -> None:
    """Bar cannot exceed 100 % — total is clamped >= downloaded."""
    smoother = ProgressSmoother(min_interval=0.0)
    # Estimate is way too low; downloaded exceeds it
    result = smoother.update(_tick(50_000, total=None, total_bytes_estimate=1_000), now=0.0)

    assert result is not None
    # total = max(round(1000), 50000) = 50000
    assert result.total == 50_000


def test_speed_survives_file_boundary() -> None:
    """Speed estimate carries across video-stream → audio-stream boundary."""
    smoother = ProgressSmoother(
        min_window=5.0, max_window=30.0, elapsed_fraction=0.25, min_interval=0.0
    )
    # 5 ticks on video.mp4 at 10 MB/s
    for i in range(5):
        smoother.update(_tick(i * 10_000_000, total=50_000_000, filename="video.mp4"), now=float(i))
    # Switch to audio.m4a with 1M at now=5 (it's a fresh file, so per-file is 1M not cumulative)
    result = smoother.update(_tick(1_000_000, total=10_000_000, filename="audio.m4a"), now=5.0)

    assert result is not None
    # Speed should still be ~10 MB/s (cumulative went from 50M to 51M over ~5 seconds)
    # Allowing ±20% = 8-12 MB/s
    assert result.speed is not None
    assert 8_000_000 <= result.speed <= 12_000_000
    # Returned downloaded is per-file (audio), not cumulative
    assert result.downloaded == 1_000_000


def test_mark_file_finished_rolls_bytes_forward() -> None:
    """mark_file_finished adds file bytes to _completed_bytes; cumulative stays monotone."""
    smoother = ProgressSmoother(min_interval=0.0)
    # 3 ticks on video.mp4
    for i in range(3):
        smoother.update(_tick(i * 10_000_000, total=30_000_000, filename="video.mp4"), now=float(i))
    # Mark finished
    smoother.mark_file_finished(
        {"status": "finished", "total_bytes": 30_000_000, "filename": "video.mp4"}, now=3.0
    )
    # Tick on audio.m4a
    result = smoother.update(_tick(1_000_000, total=10_000_000, filename="audio.m4a"), now=4.0)

    assert smoother._completed_bytes == 30_000_000
    assert result is not None
    # Cumulative series should be non-decreasing: samples should go 0, 10M, 20M, 30M, 31M
    for i in range(len(smoother._samples) - 1):
        assert smoother._samples[i][1] <= smoother._samples[i + 1][1]


def test_mark_file_finished_is_idempotent() -> None:
    """Calling mark_file_finished twice is safe; _completed_bytes doesn't double-count."""
    smoother = ProgressSmoother(min_interval=0.0)
    smoother.update(_tick(25_000_000, total=30_000_000), now=0.0)
    d = {"status": "finished", "total_bytes": 30_000_000, "filename": "a.mp4"}
    smoother.mark_file_finished(d, now=1.0)
    completed_after_first = smoother._completed_bytes
    smoother.mark_file_finished(d, now=1.0)
    completed_after_second = smoother._completed_bytes

    assert completed_after_first == completed_after_second == 30_000_000


def test_mark_file_finished_without_prior_update() -> None:
    """mark_file_finished is safe on a fresh smoother (tiny file that finishes in one event)."""
    smoother = ProgressSmoother()
    d = {"status": "finished", "total_bytes": 5_000, "filename": "x.m4a"}
    smoother.mark_file_finished(d, now=1.0)

    assert smoother._completed_bytes == 5_000
    assert len(smoother._samples) == 1
    assert smoother._session_start == 1.0


def test_throttle_suppresses_rapid_updates() -> None:
    """Repaint throttle suppresses results within min_interval; ingestion still happens."""
    smoother = ProgressSmoother(min_interval=0.2)
    # Ticks at 0.0, 0.05, 0.1, 0.3 seconds
    result_0_0 = smoother.update(_tick(0), now=0.0)
    result_0_05 = smoother.update(_tick(1_000_000), now=0.05)
    result_0_1 = smoother.update(_tick(2_000_000), now=0.1)
    result_0_3 = smoother.update(_tick(3_000_000), now=0.3)

    assert result_0_0 is not None  # First emit
    assert result_0_05 is None  # Throttled (0.05 < 0.0 + 0.2)
    assert result_0_1 is None  # Throttled (0.1 < 0.0 + 0.2)
    assert result_0_3 is not None  # Emitted (0.3 >= 0.0 + 0.2)


def test_throttled_calls_still_ingest_samples() -> None:
    """Suppressed repaints still record samples; the window sees the full data."""
    smoother = ProgressSmoother(
        min_window=11.0, max_window=30.0, elapsed_fraction=0.25, min_interval=10.0
    )
    result_0 = smoother.update(_tick(0), now=0.0)
    result_1 = smoother.update(_tick(10_000_000), now=1.0)  # Throttled, returns None
    result_11 = smoother.update(_tick(110_000_000), now=11.0)  # Emitted

    assert result_0 is not None
    assert result_1 is None  # Throttled
    assert result_11 is not None
    # Speed at now=11 should use all three samples, proving the suppressed one was ingested
    # Cumulative: 0, 10M, 110M over 11 seconds = ~10M B/s
    assert result_11.speed is not None
    assert 9_000_000 <= result_11.speed <= 11_000_000
    assert len(smoother._samples) == 3


def test_identical_timestamps_do_not_divide_by_zero() -> None:
    """Dt <= 1e-6 triggers fallback; no ZeroDivisionError or inf/nan."""
    smoother = ProgressSmoother(min_interval=0.0)
    # Three ticks all at now=1.0
    smoother.update(_tick(1_000_000), now=1.0)
    smoother.update(_tick(5_000_000), now=1.0)
    result = smoother.update(_tick(9_000_000), now=1.0)

    assert result is not None
    # dt=0, so fallback to d["speed"], which is None → speed is None
    assert result.speed is None or isinstance(result.speed, float)
    # Never inf or nan
    if result.speed is not None:
        assert result.speed != float("inf")
        assert result.speed == result.speed  # NaN check


def test_zero_downloaded_first_tick() -> None:
    """Starting at 0 downloaded bytes is safe; no exceptions."""
    smoother = ProgressSmoother(min_interval=0.0)
    result = smoother.update(_tick(0, total=1_000_000), now=0.0)

    assert result is not None
    assert result.downloaded == 0
    assert result.total == 1_000_000
    assert result.speed is None or isinstance(result.speed, float)


def test_missing_keys_do_not_raise() -> None:
    """Missing keys (no downloaded_bytes, total, filename) are handled gracefully."""
    smoother = ProgressSmoother(min_interval=0.0)
    result = smoother.update({"status": "downloading"}, now=0.0)

    assert result is not None
    assert result.downloaded == 0
    assert result.total is None
    assert result.eta is None


def test_none_valued_keys_do_not_raise() -> None:
    """None values (downloaded_bytes: None, total_bytes: None, etc.) are handled gracefully."""
    smoother = ProgressSmoother(min_interval=0.0)
    result = smoother.update(
        {
            "status": "downloading",
            "downloaded_bytes": None,
            "total_bytes": None,
            "total_bytes_estimate": None,
            "speed": None,
            "filename": None,
        },
        now=0.0,
    )

    assert result is not None
    assert result.downloaded == 0
    assert result.total is None


def test_mark_file_finished_idempotent_when_no_filename_present() -> None:
    """
    Regression: a keyless "finished" dict must still be de-duplicated on repeat.

    The key falls back to "" when both filename and tmpfilename are absent; the
    idempotency guard must compare against the None sentinel (no prior finish
    recorded), not truthiness of key, or every keyless repeat bypasses the guard
    and re-adds the same bytes.
    """
    smoother = ProgressSmoother(min_interval=0.0)
    d = {"status": "finished", "total_bytes": 30_000_000}  # no filename/tmpfilename
    smoother.mark_file_finished(d, now=1.0)
    completed_after_first = smoother._completed_bytes
    smoother.mark_file_finished(d, now=1.0)
    completed_after_second = smoother._completed_bytes

    assert completed_after_first == 30_000_000
    assert completed_after_second == 30_000_000  # must NOT double to 60M


def test_mark_file_finished_two_distinct_files_back_to_back_no_updates() -> None:
    """Two different tiny files finishing with zero intermediate update() calls both count."""
    smoother = ProgressSmoother(min_interval=0.0)
    smoother.mark_file_finished(
        {"status": "finished", "total_bytes": 1_000, "filename": "a.mp4"}, now=0.0
    )
    smoother.mark_file_finished(
        {"status": "finished", "total_bytes": 500, "filename": "b.m4a"}, now=0.1
    )

    assert smoother._completed_bytes == 1_500


def test_finish_update_finish_three_stage_lifecycle_no_double_count() -> None:
    """A file finishes (no updates), a new file starts via update(), then it also finishes."""
    smoother = ProgressSmoother(min_interval=0.0)
    smoother.mark_file_finished(
        {"status": "finished", "total_bytes": 1_000, "filename": "a.mp4"}, now=0.0
    )
    smoother.update(_tick(400, total=1_000, filename="b.m4a"), now=0.1)
    smoother.mark_file_finished(
        {"status": "finished", "total_bytes": 1_000, "filename": "b.m4a"}, now=0.2
    )

    assert smoother._completed_bytes == 2_000


def test_out_of_order_timestamps_do_not_crash_or_go_negative() -> None:
    """
    A clock that appears to move backward between calls must not raise or corrupt state.

    now(5.0) - last_emit(10.0) = -5.0, which is < min_interval(0.0), so the throttle
    correctly (if conservatively) suppresses the repaint rather than crashing -- the
    important thing is no exception and no corrupted internal state, not that a result
    is necessarily returned.
    """
    smoother = ProgressSmoother(min_interval=0.0)
    smoother.update(_tick(5_000_000), now=10.0)
    result = smoother.update(_tick(6_000_000), now=5.0)  # now < previous now

    assert result is None or isinstance(result.speed, (float, type(None)))
    # A third, forward-moving tick must still work normally afterward.
    follow_up = smoother.update(_tick(7_000_000), now=11.0)
    assert follow_up is not None
    if follow_up.speed is not None:
        assert follow_up.speed >= 0.0
        assert follow_up.speed != float("inf")
        assert follow_up.speed == follow_up.speed  # not NaN


def test_burst_of_identical_timestamps_after_large_gap() -> None:
    """A same-timestamp burst arriving long after session start must stay finite and bounded."""
    smoother = ProgressSmoother(
        min_window=3.0, max_window=30.0, elapsed_fraction=0.25, min_interval=0.0
    )
    smoother.update(_tick(0), now=0.0)
    for i in range(5):
        result = smoother.update(_tick(1_000_000 + i), now=100.0)

    assert result is not None
    assert len(smoother._samples) > 2  # floor never violated
    if result.speed is not None:
        assert result.speed != float("inf")
        assert result.speed == result.speed  # not NaN


def test_prune_keeps_sample_exactly_at_cutoff() -> None:
    """_prune uses strict '<' against cutoff, so a sample exactly at the boundary survives."""
    smoother = ProgressSmoother(
        min_window=5.0, max_window=5.0, elapsed_fraction=1.0, min_interval=0.0
    )
    smoother._session_start = 0.0
    smoother._samples.append((5.0, 1_000))  # now=10 → window=5 → cutoff=5.0 exactly
    smoother._samples.append((7.0, 2_000))
    smoother._samples.append((10.0, 3_000))

    smoother._prune(10.0)

    assert smoother._samples[0][0] == 5.0  # not evicted: 5.0 < 5.0 is False


def test_total_clamp_holds_while_estimate_whipsaws_below_and_above_downloaded() -> None:
    """_total() never regresses below the running downloaded count even as the EMA whipsaws."""
    smoother = ProgressSmoother(total_alpha=0.5, min_interval=0.0)
    # Estimate starts far below what will be downloaded, then swings high, then low again.
    ticks = [
        (1_000, 500),  # est below downloaded
        (5_000, 50_000),  # est jumps high
        (5_500, 100),  # est collapses low again
        (6_000, 1_000_000),  # est jumps high again
    ]
    result = None
    for i, (downloaded, est) in enumerate(ticks):
        result = smoother.update(
            _tick(downloaded, total=None, total_bytes_estimate=est), now=float(i)
        )
        assert result is not None
        assert result.total is not None
        assert result.total >= result.downloaded  # bar can never exceed 100%


def test_repeated_updates_with_no_filename_or_tmpfilename_stay_one_file() -> None:
    """Several update() calls carrying no filename/tmpfilename in a row are the same file."""
    smoother = ProgressSmoother(min_interval=0.0)
    smoother.update({"status": "downloading", "downloaded_bytes": 100}, now=0.0)
    smoother.update({"status": "downloading", "downloaded_bytes": 200}, now=1.0)
    result = smoother.update({"status": "downloading", "downloaded_bytes": 300}, now=2.0)

    assert result is not None
    # If a spurious roll had fired, downloaded would have reset and _completed_bytes
    # would have absorbed the earlier bytes instead of the clamp tracking them directly.
    assert result.downloaded == 300
    assert smoother._completed_bytes == 0


def test_negative_exact_total_bytes_never_yields_negative_rendered_total() -> None:
    """A malformed negative total_bytes must not leak a negative total past the clamp."""
    smoother = ProgressSmoother(min_interval=0.0)
    result = smoother.update(_tick(1_000, total=-500), now=0.0)

    assert result is not None
    assert result.total is not None
    assert result.total >= 0


def test_reset_clears_all_state() -> None:
    """reset() clears all state; next tick after reset starts fresh."""
    smoother = ProgressSmoother(min_interval=0.0)
    # Full session: 5 ticks + mark_file_finished
    for i in range(5):
        smoother.update(_tick(i * 10_000_000, total=50_000_000), now=float(i))
    smoother.mark_file_finished({"status": "finished", "total_bytes": 50_000_000}, now=5.0)

    smoother.reset()

    # Feed one tick at a much later time
    result = smoother.update(_tick(1_000_000), now=100.0)

    assert smoother._completed_bytes == 0
    assert len(smoother._samples) == 1
    assert smoother._session_start == 100.0
    assert smoother._file_total_exact is False
    assert result is not None  # Throttle clock was cleared, so this emits
