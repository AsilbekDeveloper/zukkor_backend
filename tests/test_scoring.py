import pytest

from app.services.scoring import calculate_ball, compute_time_limit_ms

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


# --- compute_time_limit_ms (2026-09-06: qattiq 15s konstanta o'rniga
# savol/variant uzunligiga qarab moslashadigan vaqt) ---


def test_very_short_question_still_gets_at_least_the_old_15s_baseline():
    ball = compute_time_limit_ms("2x2?", ["3", "4", "5", "6"])
    assert ball >= 15_000


def test_longer_question_gets_more_time_than_a_shorter_one():
    short = compute_time_limit_ms("Bu qaysi yil?", ["2020", "2021", "2022", "2023"])
    long = compute_time_limit_ms(
        "Quyidagi voqealardan qaysi biri 1991-yilda O'zbekiston mustaqillikka "
        "erishishidan oldin sodir bo'lgan va uning tarixiy ahamiyatini tushuntiring?",
        [
            "Ikkinchi jahon urushi tugashi",
            "Sovet Ittifoqining tarqalishi jarayoni boshlanishi",
            "Amir Temur davlatining barpo etilishi",
            "Buyuk ipak yo'lining ochilishi",
        ],
    )
    assert long > short


def test_time_limit_is_capped_at_30_seconds_for_extremely_long_questions():
    huge_text = "a" * 5000
    assert compute_time_limit_ms(huge_text, ["b" * 1000] * 4) == 30_000


def test_time_limit_never_goes_below_15_seconds():
    assert compute_time_limit_ms("", []) == 15_000
