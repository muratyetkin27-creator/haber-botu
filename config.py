"""Bot konfigürasyon dosyası - environment variable'ları okur."""
import os
import logging
from typing import Optional

# Loglama ayarları
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _get_env(key: str, default: Optional[str] = None, required: bool = True) -> str:
    """Environment variable okur, yoksa hata fırlatır."""
    val = os.environ.get(key, default)
    if required and not val:
        raise RuntimeError(f"Eksik environment variable: {key}")
    return val or ""


# Telegram bot token (BotFather'dan)
TELEGRAM_TOKEN: str = _get_env("TELEGRAM_TOKEN")

# Anthropic Claude API key
CLAUDE_API_KEY: str = _get_env("CLAUDE_API_KEY")

# Botun haber göndereceği chat ID (kanal/grup/kullanıcı)
CHAT_ID: str = _get_env("CHAT_ID")

# Veritabanı dosya yolu
DB_PATH: str = os.environ.get("DB_PATH", "bottur.db")

# Claude model adı (özetleme + çeviri için)
CLAUDE_MODEL: str = os.environ.get("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")

# HTTP istek timeout süresi (saniye)
HTTP_TIMEOUT: int = int(os.environ.get("HTTP_TIMEOUT", "10"))

# Cosine similarity eşik değeri (bu değerin üstündeki haberler tekrar sayılır)
SIMILARITY_THRESHOLD: float = float(os.environ.get("SIMILARITY_THRESHOLD", "0.75"))

# Bir özet partisinde kaç haber olacak
BATCH_SIZE: int = int(os.environ.get("BATCH_SIZE", "6"))

# Saat dilimi
TIMEZONE: str = os.environ.get("TIMEZONE", "Europe/Istanbul")

# Yönetici Telegram user ID listesi (virgülle ayrılmış). Boşsa CHAT_ID admin sayılır.
_admin_raw = os.environ.get("ADMIN_USER_IDS", "")
ADMIN_USER_IDS: list[int] = (
    [int(x.strip()) for x in _admin_raw.split(",") if x.strip().lstrip("-").isdigit()]
    if _admin_raw
    else []
)

# User-Agent (web scraping için)
USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
