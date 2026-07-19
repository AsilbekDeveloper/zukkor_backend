def calculate_ball(elapsed_ms: float, time_limit_ms: int, is_correct: bool) -> int:
    if not is_correct or elapsed_ms > time_limit_ms:
        return 0
    remaining = max(0, time_limit_ms - elapsed_ms)
    return round(1000 * (0.5 + (remaining / time_limit_ms) * 0.5))
