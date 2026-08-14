"""Unit tests for the skmeter pure core. No GPU required."""

import json

import pytest

from skcapstone.fleet.skmeter import (
    EnergyCounter,
    build_energy_response,
    integrate,
    load_checkpoint,
    measure_idle_baseline,
    parse_power_line,
    resolve_boot_idle_baseline,
    save_checkpoint,
    should_rebaseline,
)


class TestParsePowerLine:
    def test_plain_value(self):
        assert parse_power_line("140.77") == pytest.approx(140.77)

    def test_strips_whitespace(self):
        assert parse_power_line("  8.86\n") == pytest.approx(8.86)

    def test_null_bytes_are_stripped(self):
        # Observed in the field 2026-08-14: reading the sampler's output file
        # while nvidia-smi was still writing produced NUL padding.
        assert parse_power_line("\x00\x00\x00\x008.86") == pytest.approx(8.86)

    def test_units_suffix_tolerated(self):
        assert parse_power_line("99.12 W") == pytest.approx(99.12)

    def test_not_supported_returns_none(self):
        assert parse_power_line("[N/A]") is None

    def test_blank_returns_none(self):
        assert parse_power_line("   ") is None

    def test_garbage_returns_none(self):
        assert parse_power_line("nvidia-smi: command not found") is None

    def test_negative_value_returns_none(self):
        # Power draw cannot be negative; a negative reading is corruption.
        assert parse_power_line("-5.0") is None


class TestIntegrate:
    def test_constant_power(self):
        r = integrate([100.0] * 10, dt_s=0.2)
        assert r["total_j"] == pytest.approx(200.0)  # 100 W x 2.0 s
        assert r["window_s"] == pytest.approx(2.0)
        assert r["samples_n"] == 10

    def test_marginal_subtracts_idle(self):
        r = integrate([100.0] * 10, dt_s=0.2, idle_w=10.0)
        assert r["total_j"] == pytest.approx(200.0)
        assert r["marginal_j"] == pytest.approx(180.0)  # 90 W x 2.0 s

    def test_marginal_never_negative(self):
        # Below-idle samples must not create energy credits.
        r = integrate([5.0, 5.0], dt_s=1.0, idle_w=10.0)
        assert r["marginal_j"] == pytest.approx(0.0)

    def test_mean_and_peak(self):
        r = integrate([10.0, 20.0, 60.0], dt_s=1.0)
        assert r["mean_w"] == pytest.approx(30.0)
        assert r["peak_w"] == pytest.approx(60.0)

    def test_empty_is_zero_not_error(self):
        r = integrate([], dt_s=0.2)
        assert r["total_j"] == 0.0
        assert r["marginal_j"] == 0.0
        assert r["samples_n"] == 0

    def test_matches_field_measurement(self):
        # Regression against the real 2026-08-14 run on .100:
        # 95 samples at 0.2 s, mean 99.12 W, idle 8.96 W -> ~1713 J marginal.
        samples = [99.12] * 95
        r = integrate(samples, dt_s=0.2, idle_w=8.96)
        assert r["marginal_j"] == pytest.approx(1713.0, abs=2.0)


class TestEnergyCounter:
    def test_starts_at_zero(self):
        c = EnergyCounter(idle_w=8.96)
        assert c.total_j == 0.0
        assert c.marginal_j == 0.0
        assert c.samples_n == 0

    def test_accumulates_monotonically(self):
        c = EnergyCounter(idle_w=0.0)
        c.observe(100.0, 0.2)
        first = c.total_j
        c.observe(100.0, 0.2)
        assert c.total_j > first
        assert c.total_j == pytest.approx(40.0)

    def test_never_decreases_even_below_idle(self):
        c = EnergyCounter(idle_w=50.0)
        c.observe(10.0, 1.0)
        assert c.marginal_j == 0.0
        c.observe(150.0, 1.0)
        assert c.marginal_j == pytest.approx(100.0)

    def test_snapshot_shape(self):
        c = EnergyCounter(idle_w=8.96)
        c.observe(100.0, 0.2)
        s = c.snapshot()
        assert set(s) >= {"total_j", "marginal_j", "idle_baseline_w", "samples_n"}
        assert s["idle_baseline_w"] == pytest.approx(8.96)

    def test_delta_between_two_reads_is_the_energy_of_that_window(self):
        # This is exactly how the gateway will use it.
        c = EnergyCounter(idle_w=10.0)
        c.observe(10.0, 1.0)  # idle before the request
        before = c.marginal_j
        c.observe(110.0, 2.0)  # the request itself: 100 W x 2 s
        after = c.marginal_j
        assert after - before == pytest.approx(200.0)

    def test_negative_watts_leaves_counters_unchanged(self):
        # Negative watts (corrupt sample) must not decrease either counter.
        c = EnergyCounter(idle_w=10.0)
        c.observe(100.0, 1.0)
        total_before = c.total_j
        marginal_before = c.marginal_j
        c.observe(-50.0, 1.0)
        assert c.total_j == total_before
        assert c.marginal_j == marginal_before

    def test_idle_baseline_w_reflects_constructor(self):
        # idle_baseline_w property reflects the constructor argument.
        c = EnergyCounter(idle_w=42.5)
        assert c.idle_baseline_w == pytest.approx(42.5)

    def test_set_idle_baseline_changes_future_observations(self):
        # set_idle_baseline() changes what subsequent observe() calls treat as idle.
        c = EnergyCounter(idle_w=10.0)
        c.observe(100.0, 1.0)
        marginal_first = c.marginal_j
        c.set_idle_baseline(50.0)
        c.observe(100.0, 1.0)
        marginal_second = c.marginal_j
        # First: (100-10)*1=90, Second: (100-50)*1=50
        assert marginal_first == pytest.approx(90.0)
        assert marginal_second == pytest.approx(140.0)

    def test_set_idle_baseline_does_not_retroactively_alter(self):
        # set_idle_baseline() does not retroactively alter accumulated marginal_j.
        c = EnergyCounter(idle_w=10.0)
        c.observe(100.0, 1.0)
        accumulated = c.marginal_j
        c.set_idle_baseline(50.0)
        # marginal_j should not change retroactively
        assert c.marginal_j == accumulated


class TestIdleBaseline:
    def test_averages_the_samples(self):
        vals = iter([8.9, 9.0, 8.8, 9.1])
        assert measure_idle_baseline(lambda: next(vals), n=4) == pytest.approx(8.95)

    def test_ignores_unparseable_samples(self):
        vals = iter([8.9, None, 9.1, None])
        assert measure_idle_baseline(lambda: next(vals), n=4) == pytest.approx(9.0)

    def test_all_bad_samples_returns_zero_not_error(self):
        # A zero baseline means we charge absolute energy, which is wrong but
        # safe. Crashing the meter would be worse.
        assert measure_idle_baseline(lambda: None, n=3) == 0.0


class TestEnergyResponse:
    def test_counter_j_is_the_marginal_counter(self):
        c = EnergyCounter(idle_w=10.0)
        c.observe(110.0, 1.0)  # 100 J marginal, 110 J total
        r = build_energy_response(
            c, watts_now=110.0, device="gpu0", node="dot100", now_ms=1_700_000_000_000
        )
        assert r["counter_j"] == pytest.approx(100.0)
        assert r["total_j"] == pytest.approx(110.0)

    def test_carries_identity_and_timestamp(self):
        c = EnergyCounter(idle_w=8.96)
        r = build_energy_response(
            c, watts_now=9.0, device="gpu0", node="dot100", now_ms=1_700_000_000_000
        )
        assert r["device"] == "gpu0"
        assert r["node"] == "dot100"
        assert r["ts"] == 1_700_000_000_000
        assert r["idle_baseline_w"] == pytest.approx(8.96)


class TestCheckpoint:
    def test_snapshot_restore_roundtrip(self):
        c = EnergyCounter(idle_w=8.96)
        c.observe(110.0, 2.0)
        state = c.snapshot()

        restored = EnergyCounter()
        restored.restore(state)
        assert restored.marginal_j == pytest.approx(c.marginal_j)
        assert restored.total_j == pytest.approx(c.total_j)
        assert restored.idle_baseline_w == pytest.approx(8.96)

    def test_counter_survives_a_restart(self, tmp_path):
        # The whole point: a restart must not rewind the counter, or every
        # in-flight request straddling it loses its measurement.
        path = tmp_path / "skmeter-state.json"
        c = EnergyCounter(idle_w=10.0)
        c.observe(110.0, 5.0)  # 500 J marginal
        save_checkpoint(c, path)

        revived = EnergyCounter()
        revived.restore(load_checkpoint(path))
        assert revived.marginal_j == pytest.approx(500.0)

        revived.observe(110.0, 1.0)
        assert revived.marginal_j == pytest.approx(600.0)

    def test_load_checkpoint_missing_file_returns_none(self, tmp_path):
        assert load_checkpoint(tmp_path / "nope.json") is None

    def test_load_checkpoint_corrupt_file_returns_none(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        assert load_checkpoint(path) is None

    def test_save_is_atomic(self, tmp_path):
        # A crash mid-write must not leave a truncated file that reads as a
        # zero balance on the next boot.
        path = tmp_path / "state.json"
        c = EnergyCounter(idle_w=1.0)
        c.observe(101.0, 1.0)
        save_checkpoint(c, path)
        assert json.loads(path.read_text())["marginal_j"] == pytest.approx(100.0)
        assert not list(tmp_path.glob("*.tmp")), "temp file should be gone"

    def test_restore_ignores_garbage_keys(self):
        c = EnergyCounter()
        c.restore({"marginal_j": 5.0, "nonsense": "x"})
        assert c.marginal_j == pytest.approx(5.0)

    def test_restore_never_installs_negative_values(self):
        # A hand-edited or partially-written checkpoint must not hand the
        # gateway a negative counter after restart.
        c = EnergyCounter(idle_w=10.0)
        c.restore(
            {
                "total_j": -5.0,
                "marginal_j": -999.0,
                "samples_n": -3,
                "idle_baseline_w": -1.0,
            }
        )
        assert c.total_j == 0.0
        assert c.marginal_j == 0.0
        assert c.samples_n == 0
        assert c.idle_baseline_w == 0.0

    def test_restore_ignores_wrong_typed_values_without_raising(self):
        # A syntactically valid file with the wrong types must not crash the
        # daemon on boot; it degrades to "start from what we have".
        c = EnergyCounter(idle_w=10.0)
        c.observe(110.0, 5.0)
        total_before = c.total_j
        marginal_before = c.marginal_j
        c.restore({"total_j": "abc", "marginal_j": [], "samples_n": {}})
        assert c.total_j == pytest.approx(total_before)
        assert c.marginal_j == pytest.approx(marginal_before)

    def test_restore_survives_a_checkpoint_round_trip_of_garbage(self, tmp_path):
        # Same path serve() takes: load_checkpoint then restore. Must not
        # raise even when the file is syntactically valid but semantically
        # garbage.
        path = tmp_path / "garbage.json"
        path.write_text(json.dumps({"total_j": "not a number", "marginal_j": -50.0}))
        c = EnergyCounter(idle_w=5.0)
        c.restore(load_checkpoint(path))
        assert c.total_j == 0.0
        assert c.marginal_j == 0.0

    def test_restore_missing_key_leaves_existing_value_untouched(self):
        c = EnergyCounter(idle_w=10.0)
        c.observe(110.0, 5.0)  # accumulates real total_j and marginal_j
        total_before = c.total_j
        c.restore({"marginal_j": 999.0})  # total_j key absent
        assert c.total_j == pytest.approx(total_before)
        assert c.marginal_j == pytest.approx(999.0)


class TestRebaseline:
    def test_due_after_the_interval(self):
        day_ms = 24 * 3600 * 1000
        assert should_rebaseline(0, day_ms + 1) is True

    def test_not_due_before_the_interval(self):
        assert should_rebaseline(0, 3600 * 1000) is False

    def test_never_baselined_is_due(self):
        assert should_rebaseline(0, 0) is False
        assert should_rebaseline(None, 12345) is True


class TestBootIdleBaseline:
    def test_fresh_measurement_wins_over_checkpoint(self):
        assert resolve_boot_idle_baseline(9.5, {"idle_baseline_w": 3.0}) == pytest.approx(9.5)

    def test_checkpoint_is_fallback_when_fresh_measurement_failed(self):
        assert resolve_boot_idle_baseline(0.0, {"idle_baseline_w": 3.0}) == pytest.approx(3.0)

    def test_zero_when_both_fresh_and_checkpoint_are_unusable(self):
        assert resolve_boot_idle_baseline(0.0, None) == 0.0
        assert resolve_boot_idle_baseline(0.0, {}) == 0.0

    def test_negative_checkpoint_baseline_is_floored(self):
        assert resolve_boot_idle_baseline(0.0, {"idle_baseline_w": -4.0}) == 0.0
