"""
Юнит-тесты функций db.py.
Используют реальный aiosqlite с tmp-файлом (без моков БД).
"""
import aiosqlite
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import db

TZ = ZoneInfo("Europe/Moscow")


# ── helpers ────────────────────────────────────────────────────────────────

def future_dt(hours: int = 24) -> datetime:
    return datetime.now(TZ) + timedelta(hours=hours)


def past_dt(hours: int = 1) -> datetime:
    return datetime.now(TZ) - timedelta(hours=hours)


# ── init_db ────────────────────────────────────────────────────────────────

async def test_init_db_creates_tables(db_path):
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}
    assert {"users", "calls", "attendance", "settings", "blacklist"} <= tables


async def test_init_db_creates_default_settings(db_path):
    val = await db.get_setting("max_invites")
    assert val == "3"
    val2 = await db.get_setting("vacancy_text")
    assert val2 is not None and len(val2) > 0


# ── upsert_user ────────────────────────────────────────────────────────────

async def test_upsert_user_new(db_path):
    await db.upsert_user(1001, "alice", "Alice A", "tg_ads")
    users = await db.get_all_users()
    assert len(users) == 1
    assert users[0]["user_id"] == 1001
    assert users[0]["source"] == "tg_ads"


async def test_upsert_user_repeated_does_not_overwrite_source(db_path):
    await db.upsert_user(1001, "alice", "Alice A", "tg_ads")
    # повторный вход — source НЕ должен перетираться
    await db.upsert_user(1001, "alice_new", "Alice B", "other_source")
    users = await db.get_all_users()
    assert len(users) == 1
    u = users[0]
    # username и full_name обновляются
    assert u["username"] == "alice_new"
    assert u["full_name"] == "Alice B"
    # source остаётся первым (ON CONFLICT не трогает source)
    assert u["source"] == "tg_ads"


async def test_upsert_user_no_source(db_path):
    await db.upsert_user(1002, None, "Bob B", None)
    users = await db.get_all_users()
    assert users[0]["source"] is None


# ── get_setting / set_setting ──────────────────────────────────────────────

async def test_get_set_setting(db_path):
    await db.set_setting("vacancy_text", "Вакансия тест")
    val = await db.get_setting("vacancy_text")
    assert val == "Вакансия тест"


async def test_set_setting_upsert(db_path):
    await db.set_setting("custom_key", "v1")
    await db.set_setting("custom_key", "v2")
    assert await db.get_setting("custom_key") == "v2"


async def test_get_setting_missing(db_path):
    val = await db.get_setting("nonexistent_key")
    assert val is None


# ── create_call / get_call / get_next_call / get_all_scheduled_calls ────────

async def test_create_call_returns_id(db_path):
    call_id = await db.create_call(future_dt(24), "https://zoom.us/1", "ТЕСТ1")
    assert isinstance(call_id, int) and call_id > 0


async def test_get_call(db_path):
    call_id = await db.create_call(future_dt(24), "https://zoom.us/1", "ТЕСТ1")
    call = await db.get_call(call_id)
    assert call is not None
    assert call["id"] == call_id
    assert call["link"] == "https://zoom.us/1"
    assert call["trigger_word"] == "ТЕСТ1"
    assert call["status"] == "scheduled"


async def test_get_call_missing(db_path):
    call = await db.get_call(9999)
    assert call is None


async def test_get_next_call_returns_nearest(db_path):
    id1 = await db.create_call(future_dt(48), "https://zoom.us/1", "A")
    id2 = await db.create_call(future_dt(24), "https://zoom.us/2", "B")
    nxt = await db.get_next_call()
    assert nxt is not None
    assert nxt["id"] == id2  # ближайший — через 24ч


async def test_get_next_call_ignores_past(db_path):
    await db.create_call(past_dt(2), "https://zoom.us/old", "OLD")
    nxt = await db.get_next_call()
    assert nxt is None


async def test_get_next_call_ignores_canceled(db_path):
    call_id = await db.create_call(future_dt(24), "https://zoom.us/1", "A")
    await db.set_call_status(call_id, "canceled")
    nxt = await db.get_next_call()
    assert nxt is None


async def test_get_all_scheduled_calls(db_path):
    await db.create_call(future_dt(24), "https://zoom.us/1", "A")
    await db.create_call(future_dt(48), "https://zoom.us/2", "B")
    canceled_id = await db.create_call(future_dt(72), "https://zoom.us/3", "C")
    await db.set_call_status(canceled_id, "canceled")

    calls = await db.get_all_scheduled_calls()
    assert len(calls) == 2
    assert all(c["status"] == "scheduled" for c in calls)


# ── upsert_attendance / get_attendance / set_attendance_status ─────────────

async def test_upsert_attendance_creates(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    call_id = await db.create_call(future_dt(24), "https://z.us/1", "T")
    await db.upsert_attendance(1001, call_id)
    att = await db.get_attendance(1001, call_id)
    assert att is not None
    assert att["status"] == "invited"


async def test_upsert_attendance_idempotent(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    call_id = await db.create_call(future_dt(24), "https://z.us/1", "T")
    await db.upsert_attendance(1001, call_id)
    # изменим статус вручную
    await db.set_attendance_status(1001, call_id, "confirmed")
    # повторный upsert не должен сбросить статус
    await db.upsert_attendance(1001, call_id)
    att = await db.get_attendance(1001, call_id)
    assert att["status"] == "confirmed"


async def test_get_attendance_missing(db_path):
    att = await db.get_attendance(9999, 9999)
    assert att is None


async def test_set_attendance_status_confirmed(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    call_id = await db.create_call(future_dt(24), "https://z.us/1", "T")
    await db.upsert_attendance(1001, call_id)
    await db.set_attendance_status(1001, call_id, "confirmed")
    att = await db.get_attendance(1001, call_id)
    assert att["status"] == "confirmed"
    assert att["rsvp_at"] is not None


async def test_set_attendance_status_declined(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    call_id = await db.create_call(future_dt(24), "https://z.us/1", "T")
    await db.upsert_attendance(1001, call_id)
    await db.set_attendance_status(1001, call_id, "declined")
    att = await db.get_attendance(1001, call_id)
    assert att["status"] == "declined"
    assert att["rsvp_at"] is not None


async def test_set_attendance_status_contact_issued(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    call_id = await db.create_call(future_dt(24), "https://z.us/1", "T")
    await db.upsert_attendance(1001, call_id)
    await db.set_attendance_status(1001, call_id, "contact_issued")
    att = await db.get_attendance(1001, call_id)
    assert att["status"] == "contact_issued"
    assert att["contact_issued_at"] is not None


# ── invite_count ───────────────────────────────────────────────────────────

async def test_invite_count_zero(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    assert await db.invite_count(1001) == 0


async def test_invite_count_multiple(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    id1 = await db.create_call(future_dt(24), "https://z.us/1", "A")
    id2 = await db.create_call(future_dt(48), "https://z.us/2", "B")
    await db.upsert_attendance(1001, id1)
    await db.upsert_attendance(1001, id2)
    assert await db.invite_count(1001) == 2


# ── get_available_slots_for_user ───────────────────────────────────────────

async def test_available_slots_empty_when_no_calls(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    slots = await db.get_available_slots_for_user(1001)
    assert slots == []


async def test_available_slots_not_shown_if_invited(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    call_id = await db.create_call(future_dt(24), "https://z.us/1", "T")
    await db.upsert_attendance(1001, call_id, "invited")
    slots = await db.get_available_slots_for_user(1001)
    assert slots == []


async def test_available_slots_not_shown_if_confirmed(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    call_id = await db.create_call(future_dt(24), "https://z.us/1", "T")
    await db.upsert_attendance(1001, call_id, "invited")
    await db.set_attendance_status(1001, call_id, "confirmed")
    slots = await db.get_available_slots_for_user(1001)
    assert slots == []


async def test_available_slots_shown_if_declined(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    # прошедший созвон — declined (в attendance)
    old_call_id = await db.create_call(past_dt(2), "https://z.us/old", "OLD")
    await db.upsert_attendance(1001, old_call_id, "invited")
    await db.set_attendance_status(1001, old_call_id, "declined")

    # новый будущий созвон — пользователь ещё не зарегистрирован
    new_call_id = await db.create_call(future_dt(24), "https://z.us/new", "NEW")

    slots = await db.get_available_slots_for_user(1001)
    ids = [s["id"] for s in slots]
    assert new_call_id in ids


async def test_available_slots_ignores_past_calls(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    await db.create_call(past_dt(2), "https://z.us/old", "OLD")
    slots = await db.get_available_slots_for_user(1001)
    assert slots == []


# ── get_funnel_stats ───────────────────────────────────────────────────────

async def test_get_funnel_stats_empty(db_path):
    stats = await db.get_funnel_stats()
    assert stats["entered"] == 0
    assert stats["invited"] == 0
    assert stats["confirmed"] == 0
    assert stats["declined"] == 0
    assert stats["contact_issued"] == 0


async def test_get_funnel_stats_counts(db_path):
    # 3 пользователя
    await db.upsert_user(1001, "a", "Alice", None)
    await db.upsert_user(1002, "b", "Bob", None)
    await db.upsert_user(1003, "c", "Carol", None)

    call_id = await db.create_call(future_dt(24), "https://z.us/1", "T")

    await db.upsert_attendance(1001, call_id)
    await db.set_attendance_status(1001, call_id, "confirmed")

    await db.upsert_attendance(1002, call_id)
    await db.set_attendance_status(1002, call_id, "declined")

    await db.upsert_attendance(1003, call_id)
    await db.set_attendance_status(1003, call_id, "contact_issued")

    stats = await db.get_funnel_stats()
    assert stats["entered"] == 3
    assert stats["invited"] == 3
    # confirmed считает и confirmed, и contact_issued
    assert stats["confirmed"] == 2
    assert stats["declined"] == 1
    assert stats["contact_issued"] == 1


# ── get_attendees_for_call ─────────────────────────────────────────────────

async def test_get_attendees_for_call_returns_users(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)
    await db.upsert_user(1002, "bob", "Bob", None)
    call_id = await db.create_call(future_dt(24), "https://z.us/1", "T")
    await db.upsert_attendance(1001, call_id)
    await db.upsert_attendance(1002, call_id)
    await db.set_attendance_status(1002, call_id, "confirmed")

    attendees = await db.get_attendees_for_call(call_id)
    user_ids = {a["user_id"] for a in attendees}
    assert user_ids == {1001, 1002}


async def test_get_attendees_for_call_empty(db_path):
    call_id = await db.create_call(future_dt(24), "https://z.us/1", "T")
    attendees = await db.get_attendees_for_call(call_id)
    assert attendees == []


# ── blacklist ──────────────────────────────────────────────────────────────

async def test_is_blacklisted_false_by_default(db_path):
    assert await db.is_blacklisted("alice") is False


async def test_add_and_check_blacklist(db_path):
    await db.add_to_blacklist(["alice", "@Bob"])
    assert await db.is_blacklisted("alice") is True
    assert await db.is_blacklisted("Bob") is True  # @ stripped, case-insensitive
    assert await db.is_blacklisted("carol") is False


async def test_is_blacklisted_none_username(db_path):
    assert await db.is_blacklisted(None) is False


async def test_add_to_blacklist_deduplication(db_path):
    n1 = await db.add_to_blacklist(["alice"])
    n2 = await db.add_to_blacklist(["alice"])
    assert n1 == 1
    assert n2 == 0  # уже есть, INSERT OR IGNORE


# ── get_all_calls_for_stats ────────────────────────────────────────────────

async def test_get_all_calls_for_stats_excludes_canceled(db_path):
    id1 = await db.create_call(future_dt(24), "https://z.us/1", "A")
    id2 = await db.create_call(future_dt(48), "https://z.us/2", "B")
    canceled_id = await db.create_call(future_dt(72), "https://z.us/3", "C")
    await db.set_call_status(canceled_id, "canceled")

    calls = await db.get_all_calls_for_stats()
    ids = {c["id"] for c in calls}
    assert id1 in ids
    assert id2 in ids
    assert canceled_id not in ids


async def test_get_all_calls_for_stats_includes_past(db_path):
    past_id = await db.create_call(past_dt(48), "https://z.us/old", "OLD")
    future_id = await db.create_call(future_dt(24), "https://z.us/new", "NEW")

    calls = await db.get_all_calls_for_stats()
    ids = {c["id"] for c in calls}
    assert past_id in ids
    assert future_id in ids
