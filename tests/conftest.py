import os
import sys

# Подставляем фиктивные env-переменные до импорта config/db,
# чтобы не требовался реальный .env при запуске тестов.
os.environ.setdefault("BOT_TOKEN", "0:test_token")
os.environ.setdefault("ADMIN_IDS", "123456")

import pytest
import db


@pytest.fixture()
async def db_path(tmp_path, monkeypatch):
    """Патчит db.DB_PATH на временный файл и инициализирует схему."""
    test_db = str(tmp_path / "test.db")
    monkeypatch.setattr("db.DB_PATH", test_db)
    await db.init_db()
    return test_db
