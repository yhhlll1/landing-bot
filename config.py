import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
ADMIN_IDS: list[int] = [int(x) for x in os.environ["ADMIN_IDS"].split(",")]
TIMEZONE: str = os.environ.get("TIMEZONE", "Europe/Moscow")
DB_PATH: str = os.environ.get("DB_PATH", "landingbot.db")

# Название сервиса видеосозвонов, которое видят кандидаты в текстах.
CALL_SERVICE_NAME: str = os.environ.get("CALL_SERVICE_NAME", "видеосервис")
