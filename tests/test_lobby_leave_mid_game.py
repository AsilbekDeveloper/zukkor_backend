import pytest

from app.core.security import hash_password
from app.models.user import User
from app.services import lobby_manager


class _FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


def _user(user_id: str) -> User:
    return User(id=user_id, email=f"{user_id}@example.com", hashed_password=hash_password("Parol1234"))


def _make_room_with_game(participant_ids: list[str]) -> lobby_manager._Room:
    """Builds a room + in-progress game state directly, bypassing
    start_game's DB-touching setup (start_game/_finish_game use the
    module-level AsyncSessionLocal, which isn't wired to the test DB) -
    this test only exercises the leave/removal bookkeeping itself.
    """
    room_id = "room-1"
    room = lobby_manager._Room(room_id, "123456", host_participant_id=participant_ids[0])
    for i, pid in enumerate(participant_ids):
        room.participants[pid] = lobby_manager._Participant(
            pid, _user(f"user-{pid}"), _FakeWebSocket(), is_host=(i == 0)
        )

    participant_user_ids = {pid: f"user-{pid}" for pid in participant_ids}
    game = lobby_manager._GameState(room_id, category_id=1, total_questions=5, participant_user_ids=participant_user_ids)
    room.game = game
    lobby_manager._rooms[room_id] = room
    lobby_manager._room_code_index[room.room_code] = room_id
    return room


@pytest.fixture(autouse=True)
def _cleanup_rooms():
    yield
    lobby_manager._rooms.clear()
    lobby_manager._room_code_index.clear()


@pytest.fixture
def _no_op_finish_game(monkeypatch):
    """_finish_game itself touches the DB via AsyncSessionLocal (not the
    test fixture's engine) - what this test file cares about is WHETHER
    and WHEN leave_room decides to call it, not its own DB-writing
    internals (which are covered by the existing quiz/duel-style manual
    testing this module has always relied on)."""
    calls: list[lobby_manager._Room] = []

    async def _fake_finish_game(room):
        calls.append(room)
        room.game.finished = True

    monkeypatch.setattr(lobby_manager, "_finish_game", _fake_finish_game)
    return calls


@pytest.mark.anyio
async def test_non_host_leaving_mid_game_does_not_end_it_for_others(_no_op_finish_game):
    room = _make_room_with_game(["a", "b", "c"])

    await lobby_manager.leave_room(room.room_id, "b")

    assert "b" not in room.participants
    assert "b" not in room.game.participant_user_ids
    assert not room.game.finished
    assert _no_op_finish_game == []


@pytest.mark.anyio
async def test_host_leaving_mid_game_does_not_close_the_room(_no_op_finish_game):
    room = _make_room_with_game(["a", "b", "c"])

    await lobby_manager.leave_room(room.room_id, "a")  # "a" is the host

    # Unlike the pre-game (waiting room) case, the room must survive and
    # the game must keep running for the remaining players.
    assert lobby_manager._rooms.get(room.room_id) is room
    assert "a" not in room.participants
    assert not room.game.finished


@pytest.mark.anyio
async def test_leaving_cancels_the_departed_participants_pending_timeout_task(_no_op_finish_game):
    room = _make_room_with_game(["a", "b"])

    async def _never_ends():
        await lobby_manager.asyncio.sleep(999)

    pending_task = lobby_manager.asyncio.create_task(_never_ends())
    room.game.participant_timeout_task["b"] = pending_task

    await lobby_manager.leave_room(room.room_id, "b")
    # Give the event loop a beat to actually process the cancellation.
    await lobby_manager.asyncio.sleep(0)

    with pytest.raises(lobby_manager.asyncio.CancelledError):
        await pending_task


@pytest.mark.anyio
async def test_game_ends_immediately_once_only_one_participant_remains(_no_op_finish_game):
    room = _make_room_with_game(["a", "b"])

    await lobby_manager.leave_room(room.room_id, "b")

    assert _no_op_finish_game == [room]
    assert room.game.finished is True


@pytest.mark.anyio
async def test_everyone_leaving_mid_game_cleans_up_without_crashing(_no_op_finish_game):
    room = _make_room_with_game(["a", "b"])

    await lobby_manager.leave_room(room.room_id, "a")
    await lobby_manager.leave_room(room.room_id, "b")

    assert lobby_manager._rooms.get(room.room_id) is None


@pytest.mark.anyio
async def test_leaving_when_all_remaining_players_already_finished_ends_the_game(_no_op_finish_game):
    # 'a' and 'b' have already answered every question and are sitting in
    # "waiting for others" - 'c' is still mid-game, then leaves. Since the
    # two who remain are both already finished, that departure should be
    # exactly what tips the game over into ending, without either of them
    # needing to do anything further.
    room = _make_room_with_game(["a", "b", "c"])
    room.game.participant_finished["a"] = True
    room.game.participant_finished["b"] = True

    await lobby_manager.leave_room(room.room_id, "c")

    assert _no_op_finish_game == [room]
    assert room.game.finished is True


@pytest.mark.anyio
async def test_leaving_with_two_still_playing_participants_does_not_end_the_game(_no_op_finish_game):
    room = _make_room_with_game(["a", "b", "c", "d"])

    await lobby_manager.leave_room(room.room_id, "d")

    assert len(room.game.participant_user_ids) == 3
    assert _no_op_finish_game == []
    assert not room.game.finished
