LEVEL_XP_STEP = 250

LEVEL_TITLES = [
    (1, "Novice"),
    (3, "Apprentice"),
    (5, "Scholar"),
    (8, "Expert"),
    (11, "Master"),
    (15, "Grandmaster"),
    (20, "Sage"),
    (30, "Legend"),
]


def compute_level(total_xp: int) -> tuple[int, str, int]:
    """total_xp'dan (level, level_title, next_level_xp) qaytaradi."""
    level = total_xp // LEVEL_XP_STEP + 1
    next_level_xp = level * LEVEL_XP_STEP

    title = LEVEL_TITLES[0][1]
    for min_level, name in LEVEL_TITLES:
        if level >= min_level:
            title = name

    return level, title, next_level_xp
