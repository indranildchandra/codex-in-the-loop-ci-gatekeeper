def promised_days(distance_km: int) -> int:
    """Return promised delivery days for the given distance."""
    if distance_km <= 0:
        return 0
    # BUG: this floors partial-day distances; the contract should round up.
    return distance_km // 500
