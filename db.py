import aiosqlite
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from config import DB_PATH, TIMEZONE

TZ = ZoneInfo(TIMEZONE)


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        # WAL — лучше переносит конкурентные записи (round-robin кураторов и т.п.).
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                full_name  TEXT,
                source     TEXT,
                entered_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS calls (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                call_dt      TEXT NOT NULL,
                link         TEXT NOT NULL,
                trigger_word TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'scheduled',
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL REFERENCES users(user_id),
                call_id          INTEGER NOT NULL REFERENCES calls(id),
                status           TEXT NOT NULL DEFAULT 'invited',
                invited_at       TEXT NOT NULL,
                rsvp_at          TEXT,
                contact_issued_at TEXT,
                manager_contact  TEXT,
                UNIQUE(user_id, call_id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS blacklist (
                username   TEXT PRIMARY KEY,
                added_at   TEXT NOT NULL
            );
        """)
        # Миграция: добавить manager_contact в attendance если нет
        try:
            await db.execute("ALTER TABLE attendance ADD COLUMN manager_contact TEXT")
            await db.commit()
        except Exception:
            pass  # колонка уже есть
        await db.commit()

        await db.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('max_invites', '3')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES "
            "('vacancy_text', '[ВСТАВЬТЕ ОПИСАНИЕ ВАКАНСИИ]')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('manager_contact', '')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('manager_contacts', '')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('manager_rr_index', '0')"
        )
        # Контакт для варианта «ни одно время не подходит» (редактируется через /admin)
        await db.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('support_contact', '')"
        )
        await db.commit()


def _now_iso() -> str:
    return datetime.now(TZ).isoformat()


# ── Users ──────────────────────────────────────────────────────────────────

async def upsert_user(user_id: int, username: str | None, full_name: str, source: str | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users(user_id, username, full_name, source, entered_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username  = excluded.username,
                full_name = excluded.full_name
            """,
            (user_id, username, full_name, source, _now_iso()),
        )
        await db.commit()



async def get_users_invited_no_rsvp(minutes: int = 60) -> list[dict]:
    """Получили инвайт на созвон, но не нажали ни одну кнопку RSVP."""
    from datetime import timedelta
    cutoff = (datetime.now(TZ) - timedelta(minutes=minutes)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT DISTINCT u.user_id, u.username, u.full_name
            FROM users u
            JOIN attendance a ON a.user_id = u.user_id
            WHERE a.status = 'invited'
              AND a.invited_at <= ?
            """,
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_all_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Settings ───────────────────────────────────────────────────────────────

async def get_setting(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def get_curators() -> list[dict]:
    """Возвращает список кураторов: [{contact, active}, ...]."""
    import json
    raw = await get_setting("manager_contacts") or ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data
        # Старый формат — строка через запятую, конвертируем
        contacts = [c.strip() for c in raw.split(",") if c.strip()]
        return [{"contact": c, "active": True} for c in contacts]
    except Exception:
        contacts = [c.strip() for c in raw.split(",") if c.strip()]
        return [{"contact": c, "active": True} for c in contacts]


async def get_any_contact() -> str:
    """Любой доступный контакт БЕЗ прокрутки round-robin счётчика:
    первый активный куратор → запасной manager_contact. Пусто, если ничего нет."""
    curators = await get_curators()
    for c in curators:
        if c.get("active", True) and c.get("contact"):
            return c["contact"]
    return await get_setting("manager_contact") or ""


async def save_curators(curators: list[dict], reset_index: bool = False) -> None:
    """Сохраняет список кураторов. reset_index=True сбрасывает round-robin
    счётчик — нужно только при смене состава кураторов, но НЕ при toggle
    активности (иначе счётчик обнуляется и первый куратор получает перекос)."""
    import json
    await set_setting("manager_contacts", json.dumps(curators, ensure_ascii=False))
    if reset_index:
        await set_setting("manager_rr_index", "0")


async def get_next_manager_contact() -> str:
    """Возвращает контакт следующего активного куратора по round-robin.
    Если активных нет — фолбэк на manager_contact."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=10000")
        async with db.execute("SELECT value FROM settings WHERE key='manager_contacts'") as cur:
            row = await cur.fetchone()
        raw = (row[0] if row else "") or ""

        curators = []
        if raw:
            import json
            try:
                data = json.loads(raw)
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    curators = data
                else:
                    contacts = [c.strip() for c in raw.split(",") if c.strip()]
                    curators = [{"contact": c, "active": True} for c in contacts]
            except Exception:
                contacts = [c.strip() for c in raw.split(",") if c.strip()]
                curators = [{"contact": c, "active": True} for c in contacts]

        active = [c for c in curators if c.get("active", True)]

        if not active:
            # Фолбэк на manager_contact
            async with db.execute("SELECT value FROM settings WHERE key='manager_contact'") as cur:
                row2 = await cur.fetchone()
            return (row2[0] if row2 else "") or ""

        if len(active) == 1:
            return active[0]["contact"]

        # Атомарный round-robin: один UPDATE ... RETURNING инкрементирует счётчик
        # и возвращает старый индекс за одну statement. SQLite сериализует запись,
        # поэтому параллельные вызовы не получают одного и того же куратора (нет гонки).
        n = len(active)
        async with db.execute(
            "UPDATE settings SET value = CAST(((CAST(value AS INTEGER) + 1) % ?) AS TEXT) "
            "WHERE key='manager_rr_index' RETURNING (CAST(value AS INTEGER) - 1 + ?) % ?",
            (n, n, n),
        ) as cur:
            r = await cur.fetchone()
        await db.commit()
        idx = r[0] if r else 0
    return active[idx]["contact"]


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


# ── Calls ──────────────────────────────────────────────────────────────────

async def create_call(call_dt: datetime, link: str, trigger_word: str) -> int:
    dt_iso = call_dt.astimezone(TZ).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO calls(call_dt, link, trigger_word, status, created_at) VALUES (?, ?, ?, 'scheduled', ?)",
            (dt_iso, link, trigger_word, _now_iso()),
        )
        call_id = cur.lastrowid
        await db.commit()
    return call_id


async def get_next_call() -> dict | None:
    now = datetime.now(TZ).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM calls WHERE status='scheduled' AND call_dt > ? ORDER BY call_dt ASC LIMIT 1",
            (now,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_call(call_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM calls WHERE id=?", (call_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_all_scheduled_calls() -> list[dict]:
    now = datetime.now(TZ).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM calls WHERE status='scheduled' AND call_dt > ? ORDER BY call_dt ASC",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_all_calls_for_stats() -> list[dict]:
    """Все не отменённые созвоны для статистики (и прошлые и будущие)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM calls WHERE status != 'canceled' ORDER BY call_dt DESC",
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_available_slots_for_user(user_id: int) -> list[dict]:
    """Будущие scheduled созвоны, доступные для выбора пользователем.

    Исключаем только те слоты, где пользователь уже confirmed (нет смысла выбирать снова).
    Слоты со статусом invited или declined показываем — пользователь может подтвердить или
    записаться повторно.
    """
    now = datetime.now(TZ).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT c.* FROM calls c
            WHERE c.status = 'scheduled'
              AND c.call_dt > ?
              AND NOT EXISTS (
                  SELECT 1 FROM attendance a
                  WHERE a.user_id=? AND a.call_id=c.id
                    AND a.status = 'confirmed'
              )
            ORDER BY c.call_dt ASC
            """,
            (now, user_id),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_last_started_call() -> dict | None:
    """Последний созвон, который уже начался (call_dt <= now, status scheduled или done)."""
    now = datetime.now(TZ).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM calls WHERE status IN ('scheduled','done') AND call_dt <= ? ORDER BY call_dt DESC LIMIT 1",
            (now,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_next_call_after(call_id: int) -> dict | None:
    """Следующий созвон после данного (для окна активности триггера)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT call_dt FROM calls WHERE id=?", (call_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        async with db.execute(
            "SELECT * FROM calls WHERE call_dt > ? ORDER BY call_dt ASC LIMIT 1",
            (row[0],),
        ) as cur:
            nxt = await cur.fetchone()
    return dict(nxt) if nxt else None


async def set_call_status(call_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE calls SET status=? WHERE id=?", (status, call_id))
        await db.commit()


async def update_call(call_id: int, call_dt: datetime | None = None, link: str | None = None) -> None:
    """Обновляет дату/время и/или ссылку созвона."""
    async with aiosqlite.connect(DB_PATH) as db:
        if call_dt and link:
            await db.execute(
                "UPDATE calls SET call_dt=?, link=? WHERE id=?",
                (call_dt.astimezone(TZ).isoformat(), link, call_id),
            )
        elif call_dt:
            await db.execute(
                "UPDATE calls SET call_dt=? WHERE id=?",
                (call_dt.astimezone(TZ).isoformat(), call_id),
            )
        elif link:
            await db.execute("UPDATE calls SET link=? WHERE id=?", (link, call_id))
        await db.commit()


# ── Attendance ─────────────────────────────────────────────────────────────

async def set_attendance_manager(user_id: int, call_id: int, manager_contact: str) -> None:
    """Сохраняет назначенного куратора в attendance (только если ещё не назначен)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE attendance SET manager_contact=? WHERE user_id=? AND call_id=? AND manager_contact IS NULL",
            (manager_contact, user_id, call_id),
        )
        await db.commit()


async def get_attendance_manager(user_id: int, call_id: int) -> str | None:
    """Возвращает ранее назначенного куратора для кандидата по конкретному созвону."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT manager_contact FROM attendance WHERE user_id=? AND call_id=?",
            (user_id, call_id),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def get_user_curator(user_id: int) -> str | None:
    """Возвращает куратора назначенного кандидату ранее (из любого attendance).
    Приоритет — записи со статусом contact_issued, потом любые с manager_contact."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Сначала ищем куратора из записей где уже выдавался контакт
        async with db.execute(
            """
            SELECT manager_contact FROM attendance
            WHERE user_id=? AND manager_contact IS NOT NULL AND manager_contact != ''
            ORDER BY CASE WHEN status='contact_issued' THEN 0 ELSE 1 END, invited_at DESC
            LIMIT 1
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def upsert_attendance(user_id: int, call_id: int, status: str = "invited") -> None:
    now = _now_iso()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO attendance(user_id, call_id, status, invited_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, call_id) DO NOTHING
            """,
            (user_id, call_id, status, now),
        )
        await db.commit()


async def get_attendance(user_id: int, call_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM attendance WHERE user_id=? AND call_id=?", (user_id, call_id)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def set_attendance_status(user_id: int, call_id: int, status: str) -> None:
    now = _now_iso()
    rsvp_statuses = {"confirmed", "declined"}
    async with aiosqlite.connect(DB_PATH) as db:
        if status in rsvp_statuses:
            await db.execute(
                "UPDATE attendance SET status=?, rsvp_at=? WHERE user_id=? AND call_id=?",
                (status, now, user_id, call_id),
            )
        elif status == "contact_issued":
            await db.execute(
                "UPDATE attendance SET status=?, contact_issued_at=? WHERE user_id=? AND call_id=?",
                (status, now, user_id, call_id),
            )
        else:
            await db.execute(
                "UPDATE attendance SET status=? WHERE user_id=? AND call_id=?",
                (status, user_id, call_id),
            )
        await db.commit()


async def invite_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM attendance WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def get_attendees_for_call(call_id: int) -> list[dict]:
    """Все участники созвона с username и статусом."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT u.user_id, u.username, u.full_name, a.status
            FROM attendance a
            JOIN users u ON u.user_id = a.user_id
            WHERE a.call_id=?
            ORDER BY a.status, u.username
            """,
            (call_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_confirmed_for_call(call_id: int) -> list[dict]:
    """Пользователи со статусом confirmed для данного созвона."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT u.user_id, u.username, u.full_name, a.manager_contact "
            "FROM attendance a "
            "JOIN users u ON u.user_id = a.user_id "
            "WHERE a.call_id=? AND a.status='confirmed'",
            (call_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_users_to_reinvite(call_id: int, max_invites: int) -> list[dict]:
    """
    Пользователи, которых нужно позвать на новый созвон:
    - ещё не приглашены на этот созвон
    - суммарное кол-во инвайтов < max_invites
    - статус в предыдущих созвонах: declined или invited (молчуны)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT u.user_id, u.username, u.full_name
            FROM users u
            WHERE
                -- ещё не позваны на этот созвон
                NOT EXISTS (
                    SELECT 1 FROM attendance a WHERE a.user_id=u.user_id AND a.call_id=?
                )
                -- не исчерпан лимит
                AND (SELECT COUNT(*) FROM attendance a2 WHERE a2.user_id=u.user_id) < ?
                -- есть хотя бы один инвайт (т.е. это не новый кандидат без истории)
                AND EXISTS (
                    SELECT 1 FROM attendance a3 WHERE a3.user_id=u.user_id
                    AND a3.status IN ('invited', 'declined')
                )
            """,
            (call_id, max_invites),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_new_users_to_invite(call_id: int, max_invites: int) -> list[dict]:
    """
    Новые пользователи (без истории инвайтов), которых нужно позвать.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT u.user_id, u.username, u.full_name
            FROM users u
            WHERE
                NOT EXISTS (SELECT 1 FROM attendance a WHERE a.user_id=u.user_id AND a.call_id=?)
                AND (SELECT COUNT(*) FROM attendance a2 WHERE a2.user_id=u.user_id) < ?
                AND NOT EXISTS (SELECT 1 FROM attendance a3 WHERE a3.user_id=u.user_id)
            """,
            (call_id, max_invites),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Статистика ─────────────────────────────────────────────────────────────

async def get_funnel_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            entered = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM attendance") as cur:
            invited = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM attendance WHERE status IN ('confirmed','contact_issued')") as cur:
            confirmed = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM attendance WHERE status='declined'") as cur:
            declined = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM attendance WHERE status='contact_issued'") as cur:
            contact_issued = (await cur.fetchone())[0]
    return {
        "entered": entered,
        "invited": invited,
        "confirmed": confirmed,
        "declined": declined,
        "contact_issued": contact_issued,
    }


async def get_funnel_by_call(call_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT status, COUNT(*) FROM attendance WHERE call_id=? GROUP BY status", (call_id,)
        ) as cur:
            rows = await cur.fetchall()
    return {r[0]: r[1] for r in rows}


async def get_funnel_by_source() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
                u.source,
                COUNT(DISTINCT u.user_id) AS entered,
                COUNT(DISTINCT CASE WHEN a.status IN ('invited','confirmed','declined','contact_issued') THEN u.user_id END) AS invited,
                COUNT(DISTINCT CASE WHEN a.status IN ('confirmed','contact_issued') THEN u.user_id END) AS confirmed,
                COUNT(DISTINCT CASE WHEN a.status='contact_issued' THEN u.user_id END) AS contact_issued
            FROM users u
            LEFT JOIN attendance a ON a.user_id = u.user_id
            GROUP BY u.source
            ORDER BY entered DESC
            """
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Blacklist ──────────────────────────────────────────────────────────────

async def add_to_blacklist(usernames: list[str]) -> int:
    """Добавляет usernames (без @) в ЧС. Возвращает кол-во новых записей."""
    now = _now_iso()
    added = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for uname in usernames:
            uname = uname.lstrip("@").strip().lower()
            if not uname:
                continue
            cur = await db.execute(
                "INSERT OR IGNORE INTO blacklist(username, added_at) VALUES (?, ?)",
                (uname, now),
            )
            added += cur.rowcount
        await db.commit()
    return added


async def is_blacklisted(username: str | None) -> bool:
    if not username:
        return False
    uname = username.lstrip("@").strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM blacklist WHERE username=?", (uname,)
        ) as cur:
            return await cur.fetchone() is not None


async def get_blacklist() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT username FROM blacklist ORDER BY added_at DESC") as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_users_by_call_and_status(call_id: int, status: str) -> list[dict]:
    """Пользователи конкретного созвона с заданным статусом (или все если status='all')."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status == "all":
            async with db.execute(
                "SELECT DISTINCT u.user_id, u.username, u.full_name "
                "FROM attendance a JOIN users u ON u.user_id=a.user_id "
                "WHERE a.call_id=?",
                (call_id,),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT DISTINCT u.user_id, u.username, u.full_name "
                "FROM attendance a JOIN users u ON u.user_id=a.user_id "
                "WHERE a.call_id=? AND a.status=?",
                (call_id, status),
            ) as cur:
                rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_users_by_attendance_status(status: str) -> list[dict]:
    """
    Все пользователи, у которых есть хотя бы одна запись attendance с данным статусом.
    status='all' — все пользователи.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status == "all":
            async with db.execute(
                "SELECT user_id, username, full_name FROM users"
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                """
                SELECT DISTINCT u.user_id, u.username, u.full_name
                FROM users u
                JOIN attendance a ON a.user_id = u.user_id
                WHERE a.status = ?
                """,
                (status,),
            ) as cur:
                rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_all_attendance_for_export() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
                u.user_id,
                u.username,
                u.full_name,
                u.source,
                u.entered_at,
                a.call_id,
                c.call_dt,
                a.status        AS attendance_status,
                a.invited_at,
                a.rsvp_at,
                a.contact_issued_at
            FROM users u
            LEFT JOIN attendance a ON a.id = (
                SELECT id FROM attendance
                WHERE user_id = u.user_id
                ORDER BY invited_at DESC
                LIMIT 1
            )
            LEFT JOIN calls c ON c.id = a.call_id
            ORDER BY u.entered_at DESC
            """
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
