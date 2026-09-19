import logging
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv нужен только для локального запуска
    pass

log = logging.getLogger(__name__)


def _int_list(raw: str) -> list[int]:
    result = []
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            result.append(int(part))
    return result


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        # Railway отдаёт postgresql://... — переводим на асинхронный драйвер
        if url.startswith("postgres://"):
            url = "postgresql+asyncpg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        # asyncpg не понимает sslmode=..., у него это параметр ssl=...
        return url.replace("sslmode=", "ssl=")

    # Без Postgres — SQLite. На Railway при подключённом Volume
    # переменная RAILWAY_VOLUME_MOUNT_PATH выставляется автоматически.
    data_dir = os.getenv("DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "data"
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{Path(data_dir) / 'bot.db'}"


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: tuple[int, ...]
    admin_chat_id: int | None
    database_url: str

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN (переменная окружения)")

    admin_ids = tuple(_int_list(os.getenv("ADMIN_IDS", "")))
    if not admin_ids:
        log.warning("ADMIN_IDS пуст — админ-панель никому не доступна")

    chat = os.getenv("ADMIN_CHAT_ID", "").strip()
    admin_chat_id = int(chat) if chat.lstrip("-").isdigit() else None

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        admin_chat_id=admin_chat_id,
        database_url=_database_url(),
    )


config: Config | None = None


def get_config() -> Config:
    global config
    if config is None:
        config = load_config()
    return config
