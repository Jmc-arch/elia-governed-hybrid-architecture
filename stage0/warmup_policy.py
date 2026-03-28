# warmup_policy.py — ELIA Stage 0
# Centralized warm-up exit criteria.
#
# ARCHITECTURAL DECISION — Single Source of Truth
# All warm-up thresholds are defined HERE and only here.
# No other module or document should duplicate these values.
#
# Background: EL-ARCH.md previously defined warm-up criteria in two
# separate sections (Phase 0 and SM_SYN) with conflicting values.
# This file resolves that inconsistency definitively.
# See: GitHub Issue #XX — Warm-up criteria consolidation
#
# Arbitration rationale: when two values conflicted, the more
# conservative (stricter) value was retained.


class WarmupPolicy:
    """
    Single source of truth for all warm-up exit criteria in ELIA.

    All modules that need to evaluate warm-up completion must
    import and reference these constants — never hardcode values.

    Usage:
        from stage0.warmup_policy import WarmupPolicy

        if cycle_count >= WarmupPolicy.DENSE_CYCLES_REQUIRED:
            ...
    """

    # ----------------------------------------------------------------
    # Standard exit condition (normal traffic)
    # ----------------------------------------------------------------

    # Number of dense cycles required to exit warm-up
    # A dense cycle = at least MIN_REQUESTS_PER_CYCLE requests processed
    DENSE_CYCLES_REQUIRED: int = 8

    # Minimum number of requests per cycle to qualify as "dense"
    MIN_REQUESTS_PER_CYCLE: int = 5

    # ----------------------------------------------------------------
    # Low-traffic exit condition
    # Used when traffic is below LOW_TRAFFIC_THRESHOLD_PER_SLOT
    # ----------------------------------------------------------------

    # Cumulative requests required to exit warm-up in low-traffic mode
    # Arbitrated: SM_SYN said 60, Phase 0 said 120 → retained 60
    # Rationale: 120 forces ~400 min warm-up at 3 req/10min — absurd
    LOW_TRAFFIC_CUMULATIVE_REQ: int = 60

    # Minimum number of validated cycles in low-traffic mode
    LOW_TRAFFIC_MIN_CYCLES: int = 5

    # Minimum wall-clock duration before low-traffic exit is allowed
    # Arbitrated: SM_SYN said 15 min, Phase 0 was silent → retained 10 min
    # Rationale: prevents artificial request bursts from triggering
    # premature warm-up exit; 10 min is a pragmatic compromise
    LOW_TRAFFIC_MIN_DURATION_S: int = 600  # 10 minutes

    # Traffic threshold below which low-traffic rules apply
    # (requests per 10-minute slot)
    LOW_TRAFFIC_THRESHOLD_PER_SLOT: int = 3

    # In low-traffic environments, a cycle is valid from this many requests
    LOW_TRAFFIC_MIN_REQ_PER_CYCLE: int = 3

    # ----------------------------------------------------------------
    # Short-circuit condition
    # Allows bypassing warm-up if a recent stable state is available
    # ----------------------------------------------------------------

    # Maximum age of saved state to allow short-circuit (seconds)
    # Arbitrated: Phase 0 said 24h, SM_SYN said 12h → retained 12h
    # Rationale: a 20h-old state may reflect very different system load
    SHORTCIRCUIT_MAX_AGE_S: int = 43200  # 12 hours

    # Minimum Stability_Index required to allow short-circuit
    SHORTCIRCUIT_MIN_STABILITY: int = 75

    # ----------------------------------------------------------------
    # Absolute timeout (safety net)
    # Forces warm-up exit regardless of other conditions
    # ----------------------------------------------------------------

    # Consensus between Phase 0 and SM_SYN — no conflict
    ABSOLUTE_TIMEOUT_S: int = 1200  # 20 minutes

    # ----------------------------------------------------------------
    # Forcing mechanism
    # Guarantees a minimum evaluation rhythm in low-traffic situations
    # ----------------------------------------------------------------

    # Minimum cycles per hour guaranteed by the forcing mechanism
    MIN_CYCLES_PER_HOUR: int = 30

    # Forcing interval if no cycle has been finalized (seconds)
    FORCING_INTERVAL_S: int = 120  # 2 minutes
