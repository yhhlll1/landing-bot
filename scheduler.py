import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import TIMEZONE, CALL_SERVICE_NAME
from db import (
    get_all_scheduled_calls,
    get_confirmed_for_call,
    get_call,
    get_next_manager_contact,
    get_setting,
    set_attendance_status,
)

logger = logging.getLogger(__name__)
TZ = ZoneInfo(TIMEZONE)

BATCH_SIZE = 25
BATCH_SLEEP = 1.0  # секунд между батчами


# ── Троттлинг-рассылка ─────────────────────────────────────────────────────

async def _broadcast(bot: Bot, user_ids: list[int], text: str) -> tuple[int, int]:
    sent = 0
    failed = 0
    for i in range(0, len(user_ids), BATCH_SIZE):
        batch = user_ids[i : i + BATCH_SIZE]
        for uid in batch:
            try:
                await bot.send_message(uid, text, parse_mode="HTML")
                sent += 1
            except TelegramRetryAfter as e:
                logger.warning("RetryAfter %s s, waiting...", e.retry_after)
                await asyncio.sleep(e.retry_after)
                try:
                    await bot.send_message(uid, text, parse_mode="HTML")
                    sent += 1
                except Exception:
                    failed += 1
            except TelegramForbiddenError:
                logger.info("User %s blocked the bot, skipping.", uid)
                failed += 1
            except Exception as e:
                logger.error("Failed to send to %s: %s", uid, e)
                failed += 1
        if i + BATCH_SIZE < len(user_ids):
            await asyncio.sleep(BATCH_SLEEP)
    return sent, failed


# ── Пуш-джобы ─────────────────────────────────────────────────────────────

async def _send_ping(bot: Bot, call_id: int, kind: str) -> None:
    call = await get_call(call_id)
    if not call or call["status"] == "canceled":
        return

    users = await get_confirmed_for_call(call_id)
    if not users:
        return

    call_dt = datetime.fromisoformat(call["call_dt"])
    dt_str = call_dt.strftime("%d.%m.%Y %H:%M МСК")

    if kind == "1h":
        from texts import PING_1H
        text = PING_1H.format(service=CALL_SERVICE_NAME)
    elif kind == "15m":
        from texts import PING_15M
        link = call.get("link", "")
        text = PING_15M.format(link=link)
    elif kind == "start":
        from texts import PING_START
        link = call.get("link", "")
        text = PING_START.format(link=link, service=CALL_SERVICE_NAME)
    else:
        return

    user_ids = [u["user_id"] for u in users]
    logger.info("Ping %s for call %s → %d users", kind, call_id, len(user_ids))
    await _broadcast(bot, user_ids, text)


def schedule_call_pings(scheduler: AsyncIOScheduler, bot: Bot, call: dict) -> None:
    """Планирует три пуша для созвона. Уже прошедшие отсечки пропускает."""
    call_id = call["id"]
    call_dt = datetime.fromisoformat(call["call_dt"])
    now = datetime.now(TZ)

    pings = [
        ("1h",      call_dt - timedelta(hours=1)),
        ("15m",     call_dt - timedelta(minutes=15)),
        ("start",   call_dt),
        ("contact", call_dt + timedelta(minutes=6)),
    ]

    for kind, fire_at in pings:
        if fire_at <= now:
            logger.debug("Ping %s for call %s is in the past, skipping.", kind, call_id)
            continue
        job_id = f"ping_{kind}_{call_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        if kind == "contact":
            scheduler.add_job(
                _send_auto_contact,
                trigger="date",
                run_date=fire_at,
                id=job_id,
                args=[bot, call_id],
                replace_existing=True,
            )
        else:
            scheduler.add_job(
                _send_ping,
                trigger="date",
                run_date=fire_at,
                id=job_id,
                args=[bot, call_id, kind],
                replace_existing=True,
            )
        logger.info("Scheduled ping %s for call %s at %s", kind, call_id, fire_at)


async def _send_auto_contact(bot: Bot, call_id: int) -> None:
    """Авто-рассылка контакта менеджера через ~6 минут после начала созвона."""
    call = await get_call(call_id)
    if not call or call["status"] == "canceled":
        return
    users = await get_confirmed_for_call(call_id)
    if not users:
        return
    from texts import AUTO_CONTACT
    from db import get_next_manager_contact as _get_next
    logger.info("Auto-contact for call %s → %d users", call_id, len(users))
    for u in users:
        # Каждый получает своего назначенного куратора
        manager_contact = u.get("manager_contact") or await _get_next() or ""
        if not manager_contact:
            continue
        try:
            await set_attendance_status(u["user_id"], call_id, "contact_issued")
        except Exception:
            pass
        try:
            text = AUTO_CONTACT.format(manager_contact=manager_contact)
            await bot.send_message(u["user_id"], text, parse_mode="HTML")
        except Exception as e:
            logger.error("Auto-contact failed for %s: %s", u["user_id"], e)


def remove_call_pings(scheduler: AsyncIOScheduler, call_id: int) -> None:
    for kind in ("1h", "15m", "start", "contact"):
        job_id = f"ping_{kind}_{call_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            logger.info("Removed ping %s for call %s", kind, call_id)



async def restore_on_startup(scheduler: AsyncIOScheduler, bot: Bot) -> None:
    """Перепланировать пуши по всем будущим scheduled-созвонам при старте."""
    calls = await get_all_scheduled_calls()
    for call in calls:
        schedule_call_pings(scheduler, bot, call)
    logger.info("restore_on_startup: scheduled pings for %d calls", len(calls))
