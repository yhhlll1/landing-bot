"""
Интеграционные тесты — end-to-end сценарии с реальным aiosqlite.
"""
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import db

TZ = ZoneInfo("Europe/Moscow")


def future_dt(hours: int = 24) -> datetime:
    return datetime.now(TZ) + timedelta(hours=hours)


def past_dt(hours: int = 1) -> datetime:
    return datetime.now(TZ) - timedelta(hours=hours)


# ── Сценарий 1 ─────────────────────────────────────────────────────────────
# Пользователь заходит → один слот → записывается (invited) → подтверждает → статус confirmed

async def test_scenario_user_joins_and_confirms(db_path):
    # Пользователь регистрируется
    await db.upsert_user(1001, "alice", "Alice", "tg_ads")

    # Создаём один будущий созвон
    call_id = await db.create_call(future_dt(24), "https://zoom.us/1", "СЛОВО1")

    # Пользователь видит слот и записывается
    slots = await db.get_available_slots_for_user(1001)
    assert len(slots) == 1
    assert slots[0]["id"] == call_id

    await db.upsert_attendance(1001, call_id)
    att = await db.get_attendance(1001, call_id)
    assert att["status"] == "invited"

    # После записи слот больше не показывается
    slots_after = await db.get_available_slots_for_user(1001)
    assert slots_after == []

    # Пользователь подтверждает участие
    await db.set_attendance_status(1001, call_id, "confirmed")
    att = await db.get_attendance(1001, call_id)
    assert att["status"] == "confirmed"
    assert att["rsvp_at"] is not None

    # invite_count == 1
    assert await db.invite_count(1001) == 1


# ── Сценарий 2 ─────────────────────────────────────────────────────────────
# Несколько слотов → пользователь выбирает один → другой остаётся для него доступным

async def test_scenario_multiple_slots_partial_registration(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)

    call_id_1 = await db.create_call(future_dt(24), "https://zoom.us/1", "AAA")
    call_id_2 = await db.create_call(future_dt(48), "https://zoom.us/2", "BBB")

    # Оба слота доступны
    slots = await db.get_available_slots_for_user(1001)
    assert len(slots) == 2

    # Записываем на первый
    await db.upsert_attendance(1001, call_id_1)

    # Второй слот по-прежнему доступен
    slots_after = await db.get_available_slots_for_user(1001)
    assert len(slots_after) == 1
    assert slots_after[0]["id"] == call_id_2

    # Invite count == 1 (только на один созвон)
    assert await db.invite_count(1001) == 1


# ── Сценарий 3 ─────────────────────────────────────────────────────────────
# declined пользователь → появляется новый созвон → слот снова доступен

async def test_scenario_declined_user_gets_new_slot(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)

    # Первый созвон — пользователь отказался
    call_id_1 = await db.create_call(past_dt(2), "https://zoom.us/old", "OLD")
    await db.upsert_attendance(1001, call_id_1)
    await db.set_attendance_status(1001, call_id_1, "declined")

    # Нет новых созвонов — слотов нет
    slots = await db.get_available_slots_for_user(1001)
    assert slots == []

    # HR создаёт новый созвон
    call_id_2 = await db.create_call(future_dt(24), "https://zoom.us/new", "NEW")

    # Новый слот появился для declined пользователя
    slots = await db.get_available_slots_for_user(1001)
    assert len(slots) == 1
    assert slots[0]["id"] == call_id_2

    # get_users_to_reinvite тоже возвращает этого пользователя
    max_invites = int(await db.get_setting("max_invites"))
    reinvite = await db.get_users_to_reinvite(call_id_2, max_invites)
    user_ids = {u["user_id"] for u in reinvite}
    assert 1001 in user_ids


# ── Сценарий 4 ─────────────────────────────────────────────────────────────
# Лимит инвайтов: после max_invites=2 инвайтов слоты больше не показываются

async def test_scenario_invite_limit_reached(db_path):
    await db.upsert_user(1001, "alice", "Alice", None)

    # Ставим лимит 2
    await db.set_setting("max_invites", "2")
    max_invites = 2

    # Первый созвон (прошедший)
    call_id_1 = await db.create_call(past_dt(48), "https://zoom.us/1", "A")
    await db.upsert_attendance(1001, call_id_1)

    # Второй созвон (прошедший)
    call_id_2 = await db.create_call(past_dt(24), "https://zoom.us/2", "B")
    await db.upsert_attendance(1001, call_id_2)

    # invite_count == 2 == max_invites
    count = await db.invite_count(1001)
    assert count == 2
    assert count >= max_invites

    # Новый будущий созвон
    call_id_3 = await db.create_call(future_dt(24), "https://zoom.us/3", "C")

    # get_available_slots_for_user не фильтрует по лимиту (это логика бота),
    # но get_users_to_reinvite — да
    reinvite = await db.get_users_to_reinvite(call_id_3, max_invites)
    user_ids = {u["user_id"] for u in reinvite}
    assert 1001 not in user_ids

    # И новых инвайтов тоже нет
    new_users = await db.get_new_users_to_invite(call_id_3, max_invites)
    assert all(u["user_id"] != 1001 for u in new_users)


# ── Сценарий 5 ─────────────────────────────────────────────────────────────
# canceled созвон не попадает в get_all_calls_for_stats

async def test_scenario_canceled_call_excluded_from_stats(db_path):
    id_ok = await db.create_call(future_dt(24), "https://zoom.us/ok", "OK")
    id_done = await db.create_call(past_dt(48), "https://zoom.us/done", "DONE")
    await db.set_call_status(id_done, "done")
    id_canceled = await db.create_call(future_dt(48), "https://zoom.us/canceled", "C")
    await db.set_call_status(id_canceled, "canceled")

    calls = await db.get_all_calls_for_stats()
    ids = {c["id"] for c in calls}

    assert id_ok in ids
    assert id_done in ids
    assert id_canceled not in ids

    # Статусы
    statuses = {c["id"]: c["status"] for c in calls}
    assert statuses[id_ok] == "scheduled"
    assert statuses[id_done] == "done"
