import pytest

from app.services.scoring import calculate_ball

TIME_LIMIT_MS = 15000
GRACE_MS = 3000


def test_wrong_answer_always_scores_zero():
    assert calculate_ball(0, TIME_LIMIT_MS, False) == 0
    assert calculate_ball(TIME_LIMIT_MS, TIME_LIMIT_MS, False) == 0


def test_instant_correct_answer_scores_max_ball():
    assert calculate_ball(0, TIME_LIMIT_MS, True) == 1000


def test_correct_answer_at_time_limit_scores_half_ball():
    assert calculate_ball(TIME_LIMIT_MS, TIME_LIMIT_MS, True) == 500


def test_correct_answer_within_network_grace_still_scores_half_ball():
    assert calculate_ball(TIME_LIMIT_MS + GRACE_MS, TIME_LIMIT_MS, True) == 500
    assert calculate_ball(TIME_LIMIT_MS + GRACE_MS - 1, TIME_LIMIT_MS, True) == 500


def test_correct_answer_past_network_grace_scores_zero():
    assert calculate_ball(TIME_LIMIT_MS + GRACE_MS + 1, TIME_LIMIT_MS, True) == 0


@pytest.mark.parametrize(
    ("faster_elapsed_ms", "slower_elapsed_ms"),
    [
        (0, 1),
        (0, TIME_LIMIT_MS),
        (2000, 7500),
        (7500, 14000),
        (14000, TIME_LIMIT_MS),
        (TIME_LIMIT_MS - 1, TIME_LIMIT_MS + GRACE_MS),
    ],
)
def test_faster_correct_answer_never_scores_less_than_a_slower_one(faster_elapsed_ms, slower_elapsed_ms):
    faster_ball = calculate_ball(faster_elapsed_ms, TIME_LIMIT_MS, True)
    slower_ball = calculate_ball(slower_elapsed_ms, TIME_LIMIT_MS, True)
    assert faster_ball >= slower_ball


def test_ball_stays_within_the_500_to_1000_range_for_any_correct_answer_inside_the_limit():
    for elapsed_ms in range(0, TIME_LIMIT_MS + 1, 500):
        ball = calculate_ball(elapsed_ms, TIME_LIMIT_MS, True)
        assert 500 <= ball <= 1000
