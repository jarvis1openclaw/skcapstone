"""Unit tests for the skmeter pure core. No GPU required."""

import pytest

from skcapstone.fleet.skmeter import (
    EnergyCounter,
    build_energy_response,
    integrate,
    measure_idle_baseline,
    parse_power_line,
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
