import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
    MONGO_URI: str = os.getenv("MONGO_URI", "").strip()
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "biolink_guardian_bot").strip()

    try:
        OWNER_ID: int = int(os.getenv("OWNER_ID", "0").strip())
    except ValueError:
        OWNER_ID = 0

    try:
        LOG_GROUP_ID: int = int(os.getenv("LOG_GROUP_ID", "0").strip())
    except ValueError:
        LOG_GROUP_ID = 0

    SUPPORT_CHANNEL_URL: str = (
        os.getenv("SUPPORT_CHANNEL_URL", "https://t.me/Telegram").strip()
        or "https://t.me/Telegram"
    )
    BANNER_URL: str = os.getenv("BANNER_URL", "").strip()
    BANNER_PATH: str = os.getenv("BANNER_PATH", "banner.jpg").strip()

    DEFAULT_ACTION: str = os.getenv("DEFAULT_ACTION", "delete").strip().lower()
    if DEFAULT_ACTION not in {"delete", "warn", "mute", "ban"}:
        DEFAULT_ACTION = "delete"

    MAX_WARNINGS: int = int(os.getenv("MAX_WARNINGS", "3"))
    MUTE_DURATION_MINUTES: int = int(os.getenv("MUTE_DURATION_MINUTES", "60"))
    WARN_DELETE_SECONDS: int = int(os.getenv("WARN_DELETE_SECONDS", "15"))
    BIO_CACHE_TTL_SECONDS: int = int(os.getenv("BIO_CACHE_TTL_SECONDS", "120"))
    PORT: int = int(os.getenv("PORT", "8080"))
