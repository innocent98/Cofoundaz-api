RUNWAY_HORIZON_MONTHS = 12

# (growth_delta_pp, cost_multiplier) per scenario, applied to the stored assumptions.
SCENARIOS: dict[str, tuple[int, float]] = {
    "base": (0, 1.00),
    "best": (10, 0.90),
    "worst": (-10, 1.15),
}
