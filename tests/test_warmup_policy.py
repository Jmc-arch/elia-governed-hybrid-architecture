# test_warmup_policy.py — ELIA Stage 0
# Regression tests for WarmupPolicy constants.
#
# Purpose: guard against accidental modification of arbitrated values.
# These tests act as a contract — if a value changes, the test fails
# and forces an explicit architectural decision before merging.
#
# See: GitHub Issue — warm-up exit criteria consolidation

import unittest

from stage0.warmup_policy import WarmupPolicy


class TestWarmupPolicyValues(unittest.TestCase):
    """
    Validates that all WarmupPolicy constants match the arbitrated values.
    Any change to these values requires a conscious architectural decision.
    """

    # ----------------------------------------------------------------
    # Standard exit condition
    # ----------------------------------------------------------------

    def test_dense_cycles_required(self):
        """8 dense cycles required — consensus between Phase 0 and SM_SYN."""
        self.assertEqual(WarmupPolicy.DENSE_CYCLES_REQUIRED, 8)

    def test_min_requests_per_cycle(self):
        """Each dense cycle requires at least 5 requests."""
        self.assertEqual(WarmupPolicy.MIN_REQUESTS_PER_CYCLE, 5)

    # ----------------------------------------------------------------
    # Low-traffic exit condition
    # ----------------------------------------------------------------

    def test_low_traffic_cumulative_requests(self):
        """
        Arbitrated value: 60 requests (not 120).
        Phase 0 said 120 — rejected as absurd at 3 req/10min.
        SM_SYN said 60 — retained as authoritative.
        """
        self.assertEqual(WarmupPolicy.LOW_TRAFFIC_CUMULATIVE_REQ, 60)

    def test_low_traffic_min_cycles(self):
        """Minimum 5 validated cycles in low-traffic mode."""
        self.assertEqual(WarmupPolicy.LOW_TRAFFIC_MIN_CYCLES, 5)

    def test_low_traffic_min_duration(self):
        """
        Arbitrated value: 600 seconds (10 minutes).
        Phase 0 was silent — SM_SYN said 15 min.
        Retained 10 min as pragmatic compromise.
        Prevents artificial bursts from triggering premature exit.
        """
        self.assertEqual(WarmupPolicy.LOW_TRAFFIC_MIN_DURATION_S, 600)

    def test_low_traffic_threshold_per_slot(self):
        """Low-traffic mode activates below 3 requests per 10-minute slot."""
        self.assertEqual(WarmupPolicy.LOW_TRAFFIC_THRESHOLD_PER_SLOT, 3)

    def test_low_traffic_min_req_per_cycle(self):
        """In low-traffic mode, a cycle is valid from 3 requests (not 5)."""
        self.assertEqual(WarmupPolicy.LOW_TRAFFIC_MIN_REQ_PER_CYCLE, 3)

    # ----------------------------------------------------------------
    # Short-circuit condition
    # ----------------------------------------------------------------

    def test_shortcircuit_max_age(self):
        """
        Arbitrated value: 43200 seconds (12 hours).
        Phase 0 said 24h — rejected (20h-old state may be stale).
        SM_SYN said 12h — retained as more conservative.
        """
        self.assertEqual(WarmupPolicy.SHORTCIRCUIT_MAX_AGE_S, 43200)

    def test_shortcircuit_max_age_is_12_hours(self):
        """Human-readable check: 43200 seconds = exactly 12 hours."""
        self.assertEqual(WarmupPolicy.SHORTCIRCUIT_MAX_AGE_S, 12 * 3600)

    def test_shortcircuit_min_stability(self):
        """Stability_Index must be >= 75 to allow short-circuit."""
        self.assertEqual(WarmupPolicy.SHORTCIRCUIT_MIN_STABILITY, 75)

    # ----------------------------------------------------------------
    # Absolute timeout
    # ----------------------------------------------------------------

    def test_absolute_timeout(self):
        """
        Arbitrated value: 1200 seconds (20 minutes).
        Consensus between Phase 0 and SM_SYN — no conflict.
        """
        self.assertEqual(WarmupPolicy.ABSOLUTE_TIMEOUT_S, 1200)

    def test_absolute_timeout_is_20_minutes(self):
        """Human-readable check: 1200 seconds = exactly 20 minutes."""
        self.assertEqual(WarmupPolicy.ABSOLUTE_TIMEOUT_S, 20 * 60)

    # ----------------------------------------------------------------
    # Forcing mechanism
    # ----------------------------------------------------------------

    def test_min_cycles_per_hour(self):
        """Forcing mechanism guarantees minimum 30 cycles per hour."""
        self.assertEqual(WarmupPolicy.MIN_CYCLES_PER_HOUR, 30)

    def test_forcing_interval(self):
        """Forcing triggers every 120 seconds if no cycle finalized."""
        self.assertEqual(WarmupPolicy.FORCING_INTERVAL_S, 120)

    # ----------------------------------------------------------------
    # Internal coherence checks
    # ----------------------------------------------------------------

    def test_low_traffic_threshold_below_standard(self):
        """
        Low-traffic min requests per cycle must be less than standard.
        If low-traffic >= standard, the low-traffic condition is useless.
        """
        self.assertLess(
            WarmupPolicy.LOW_TRAFFIC_MIN_REQ_PER_CYCLE,
            WarmupPolicy.MIN_REQUESTS_PER_CYCLE
        )

    def test_shortcircuit_age_less_than_absolute_timeout(self):
        """Short-circuit max age must be greater than absolute timeout."""
        self.assertGreater(
            WarmupPolicy.SHORTCIRCUIT_MAX_AGE_S,
            WarmupPolicy.ABSOLUTE_TIMEOUT_S
        )

    def test_forcing_interval_less_than_absolute_timeout(self):
        """Forcing interval must be less than absolute timeout."""
        self.assertLess(
            WarmupPolicy.FORCING_INTERVAL_S,
            WarmupPolicy.ABSOLUTE_TIMEOUT_S
        )

    def test_no_value_is_zero_or_negative(self):
        """All policy values must be strictly positive."""
        all_values = [
            WarmupPolicy.DENSE_CYCLES_REQUIRED,
            WarmupPolicy.MIN_REQUESTS_PER_CYCLE,
            WarmupPolicy.LOW_TRAFFIC_CUMULATIVE_REQ,
            WarmupPolicy.LOW_TRAFFIC_MIN_CYCLES,
            WarmupPolicy.LOW_TRAFFIC_MIN_DURATION_S,
            WarmupPolicy.LOW_TRAFFIC_THRESHOLD_PER_SLOT,
            WarmupPolicy.LOW_TRAFFIC_MIN_REQ_PER_CYCLE,
            WarmupPolicy.SHORTCIRCUIT_MAX_AGE_S,
            WarmupPolicy.SHORTCIRCUIT_MIN_STABILITY,
            WarmupPolicy.ABSOLUTE_TIMEOUT_S,
            WarmupPolicy.MIN_CYCLES_PER_HOUR,
            WarmupPolicy.FORCING_INTERVAL_S,
        ]
        for value in all_values:
            self.assertGreater(value, 0)


if __name__ == "__main__":
    unittest.main()
