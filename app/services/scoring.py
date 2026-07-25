# elapsed_ms is measured server-side from broadcast_at (question sent) to
# the answer request arriving back, so it always includes two network legs
# the client's own on-screen countdown never sees. Without this grace period,
# a correct answer submitted instantly by the player can still score 0 purely
# because of network/cold-start latency, with no error shown to explain it.
_NETWORK_GRACE_MS = 3000


def calculate_ball(elapsed_ms: float, time_limit_ms: int, is_correct: bool) -> int:
    if not is_correct or elapsed_ms > time_limit_ms + _NETWORK_GRACE_MS:
        return 0
    remaining = max(0, time_limit_ms - elapsed_ms)
    return round(1000 * (0.5 + (remaining / time_limit_ms) * 0.5))
