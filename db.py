"""Veritabanı CRUD operasyonları (SQLite)."""
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Optional

from config import DB_PATH

logger = logging.getLogger(__name__)


# ===== Bağlantı yönetimi =====
@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Thread-safe SQLite bağlantısı sağlar."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ===== Şema oluşturma =====
def init_db() -> None:
    """Tabloları oluşturur (yoksa) ve varsayılan verileri ekler."""
    try:
        with get_conn() as conn:
            c = conn.cursor()
            # 1. Kaynaklar tablosu
            c.execute("""
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    url TEXT,
                    x_handle TEXT,
                    source_type TEXT NOT NULL CHECK(source_type IN ('web','rss','x')),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # 2. Haberler tablosu
            c.execute("""
                CREATE TABLE IF NOT EXISTS news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT,
                    url TEXT UNIQUE,
                    published_at TEXT,
                    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    summary_tr TEXT,
                    is_column INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
                )
            """)
            # 3. Gönderilen haberler tablosu
            c.execute("""
                CREATE TABLE IF NOT EXISTS sent_news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    news_id INTEGER NOT NULL,
                    sent_to_chat_id TEXT NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    message_id INTEGER,
                    FOREIGN KEY (news_id) REFERENCES news(id) ON DELETE CASCADE
                )
            """)
            # 4. Kullanıcı durumu tablosu
            c.execute("""
                CREATE TABLE IF NOT EXISTS user_state (
                    user_id INTEGER PRIMARY KEY,
                    last_manual_fetch_at TEXT,
                    last_scheduled_fetch_at TEXT,
                    admin INTEGER NOT NULL DEFAULT 0
                )
            """)
            # 5. Zamanlama saatleri tablosu
            c.execute("""
                CREATE TABLE IF NOT EXISTS schedule_times (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hour INTEGER NOT NULL CHECK(hour BETWEEN 0 AND 23),
                    minute INTEGER NOT NULL CHECK(minute BETWEEN 0 AND 59),
                    active INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(hour, minute)
                )
            """)
            # İndeksler
            c.execute("CREATE INDEX IF NOT EXISTS idx_news_source ON news(source_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_news_fetched ON news(fetched_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_sent_news_news ON sent_news(news_id)")
        # Varsayılan veri ekle
        _seed_defaults()
        logger.info("Veritabanı başarıyla başlatıldı.")
    except Exception as e:
        logger.exception("Veritabanı başlatılamadı: %s", e)
        raise


def _seed_defaults() -> None:
    """Varsayılan kaynakları ve saatleri ekler (yalnızca tablo boşsa)."""
    try:
        # Varsayılan saatler
        default_times = [(9, 0), (12, 0), (18, 0), (23, 0)]
        with get_conn() as conn:
            cur = conn.execute("SELECT COUNT(*) AS c FROM schedule_times")
            if cur.fetchone()["c"] == 0:
                conn.executemany(
                    "INSERT INTO schedule_times(hour, minute, active) VALUES (?, ?, 1)",
                    default_times,
                )
                logger.info("Varsayılan zamanlama saatleri eklendi.")

        # Varsayılan haber siteleri (RSS feed'leri ile)
        default_web_sources = [
            ("Memurlar.net", "https://www.memurlar.net/rss/yeni.xml", "rss"),
            ("Habertürk", "https://www.haberturk.com/rss", "rss"),
            ("Sözcü", "https://www.sozcu.com.tr/feeds-rss-category-sozcu", "rss"),
            ("Cumhuriyet", "https://www.cumhuriyet.com.tr/rss/sondakika.xml", "rss"),
            ("Doğruhaber", "https://www.dogruhaber.com.tr/rss/anasayfa.xml", "rss"),
            ("CNN Türk", "https://www.cnnturk.com/feed/rss/all/news", "rss"),
        ]
        # Varsayılan X hesapları
        default_x_handles = [
            "claudeai", "SanayiSavunmaTR", "turkhafiza1", "MindVortex01",
            "fokusplus", "setavakfi", "sihirlielma", "ClashReport",
            "ClasReportr", "gundem7x24", "dailyislamist", "bosunatiklama",
            "AAGorus", "PerspektifOn", "sardan_tolga", "ConflictTr",
            "SavunmaSanayiST", "yunuspaksoy", "AlMonitorTurkce", "sebestiyetweb",
            "140journos", "RBursa", "nevzatcicek", "fehimtastekin",
            "ahmethc", "siring", "metesohtaoglu", "haaretzcom", "AlArabiya_Eng",
        ]
        with get_conn() as conn:
            cur = conn.execute("SELECT COUNT(*) AS c FROM sources")
            if cur.fetchone()["c"] == 0:
                conn.executemany(
                    "INSERT INTO sources(name, url, source_type, active) VALUES (?, ?, ?, 1)",
                    default_web_sources,
                )
                conn.executemany(
                    "INSERT INTO sources(name, x_handle, source_type, active) VALUES (?, ?, 'x', 1)",
                    [(f"@{h}", h) for h in default_x_handles],
                )
                logger.info("Varsayılan kaynaklar eklendi.")
    except Exception as e:
        logger.exception("Varsayılan veriler eklenemedi: %s", e)


# ===== Sources CRUD =====
def list_sources(only_active: bool = False) -> list[dict[str, Any]]:
    """Tüm kaynakları döndürür."""
    try:
        with get_conn() as conn:
            q = "SELECT * FROM sources"
            if only_active:
                q += " WHERE active = 1"
            q += " ORDER BY source_type, id"
            return [dict(r) for r in conn.execute(q).fetchall()]
    except Exception as e:
        logger.exception("Kaynaklar listelenemedi: %s", e)
        return []


def add_source(name: str, source_type: str, url: Optional[str] = None,
               x_handle: Optional[str] = None) -> Optional[int]:
    """Yeni kaynak ekler. ID döndürür."""
    try:
        if source_type not in ("web", "rss", "x"):
            raise ValueError("source_type web/rss/x olmalı")
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO sources(name, url, x_handle, source_type, active) VALUES (?, ?, ?, ?, 1)",
                (name, url, x_handle, source_type),
            )
            return cur.lastrowid
    except Exception as e:
        logger.exception("Kaynak eklenemedi: %s", e)
        return None


def remove_source(source_id: int) -> bool:
    """Kaynağı tamamen siler."""
    try:
        with get_conn() as conn:
            cur = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            return cur.rowcount > 0
    except Exception as e:
        logger.exception("Kaynak silinemedi: %s", e)
        return False


def set_source_active(source_id: int, active: bool) -> bool:
    """Kaynağı etkinleştirir/kapatır."""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE sources SET active = ? WHERE id = ?",
                (1 if active else 0, source_id),
            )
            return cur.rowcount > 0
    except Exception as e:
        logger.exception("Kaynak durumu güncellenemedi: %s", e)
        return False


# ===== News CRUD =====
def insert_news(source_id: int, title: str, content: str, url: str,
                published_at: Optional[str] = None, is_column: bool = False) -> Optional[int]:
    """Yeni haber ekler (URL benzersiz, varsa atlar)."""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO news
                   (source_id, title, content, url, published_at, is_column)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (source_id, title, content, url, published_at, 1 if is_column else 0),
            )
            return cur.lastrowid if cur.rowcount > 0 else None
    except Exception as e:
        logger.exception("Haber eklenemedi: %s", e)
        return None


def get_unsent_news(chat_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Henüz belirtilen chat'e gönderilmemiş haberleri döndürür."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT n.*, s.name AS source_name
                   FROM news n
                   JOIN sources s ON s.id = n.source_id
                   WHERE NOT EXISTS (
                     SELECT 1 FROM sent_news sn
                     WHERE sn.news_id = n.id AND sn.sent_to_chat_id = ?
                   )
                   ORDER BY n.fetched_at DESC
                   LIMIT ?""",
                (chat_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.exception("Gönderilmemiş haberler alınamadı: %s", e)
        return []


def update_news_summary(news_id: int, summary_tr: str) -> bool:
    """Haberin Türkçe özetini günceller."""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE news SET summary_tr = ? WHERE id = ?",
                (summary_tr, news_id),
            )
            return cur.rowcount > 0
    except Exception as e:
        logger.exception("Özet güncellenemedi: %s", e)
        return False


def mark_news_sent(news_id: int, chat_id: str, message_id: Optional[int] = None) -> None:
    """Bir haberi 'gönderildi' olarak işaretler."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO sent_news(news_id, sent_to_chat_id, message_id) VALUES (?, ?, ?)",
                (news_id, chat_id, message_id),
            )
    except Exception as e:
        logger.exception("Haber gönderildi olarak işaretlenemedi: %s", e)


def count_recent_news(hours: int = 24) -> int:
    """Son N saatte eklenen haber sayısı."""
    try:
        with get_conn() as conn:
            r = conn.execute(
                f"SELECT COUNT(*) AS c FROM news WHERE fetched_at >= datetime('now', '-{hours} hours')"
            ).fetchone()
            return int(r["c"])
    except Exception as e:
        logger.exception("Haber sayımı alınamadı: %s", e)
        return 0


# ===== User state =====
def is_admin(user_id: int) -> bool:
    """Kullanıcı admin mi?"""
    from config import ADMIN_USER_IDS
    if user_id in ADMIN_USER_IDS:
        return True
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT admin FROM user_state WHERE user_id = ?", (user_id,)
            ).fetchone()
            return bool(r and r["admin"])
    except Exception as e:
        logger.exception("Admin durumu kontrol edilemedi: %s", e)
        return False


def set_admin(user_id: int, admin: bool = True) -> None:
    """Kullanıcıyı admin yapar/kaldırır."""
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO user_state(user_id, admin) VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET admin = excluded.admin""",
                (user_id, 1 if admin else 0),
            )
    except Exception as e:
        logger.exception("Admin atanamadı: %s", e)


def update_last_fetch(user_id: int, manual: bool = True) -> None:
    """Son haber çekme zamanını günceller."""
    try:
        col = "last_manual_fetch_at" if manual else "last_scheduled_fetch_at"
        now = datetime.utcnow().isoformat()
        with get_conn() as conn:
            conn.execute(
                f"""INSERT INTO user_state(user_id, {col}) VALUES (?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET {col} = excluded.{col}""",
                (user_id, now),
            )
    except Exception as e:
        logger.exception("Son çekme zamanı güncellenemedi: %s", e)


# ===== Schedule times =====
def list_schedule_times(only_active: bool = False) -> list[dict[str, Any]]:
    """Tüm zamanlama saatlerini döndürür."""
    try:
        with get_conn() as conn:
            q = "SELECT * FROM schedule_times"
            if only_active:
                q += " WHERE active = 1"
            q += " ORDER BY hour, minute"
            return [dict(r) for r in conn.execute(q).fetchall()]
    except Exception as e:
        logger.exception("Saatler listelenemedi: %s", e)
        return []


def add_schedule_time(hour: int, minute: int) -> Optional[int]:
    """Yeni zamanlama saati ekler."""
    try:
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Geçersiz saat/dakika")
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO schedule_times(hour, minute, active) VALUES (?, ?, 1)",
                (hour, minute),
            )
            return cur.lastrowid if cur.rowcount > 0 else None
    except Exception as e:
        logger.exception("Saat eklenemedi: %s", e)
        return None


def remove_schedule_time(time_id: int) -> bool:
    """Zamanlama saatini siler."""
    try:
        with get_conn() as conn:
            cur = conn.execute("DELETE FROM schedule_times WHERE id = ?", (time_id,))
            return cur.rowcount > 0
    except Exception as e:
        logger.exception("Saat silinemedi: %s", e)
        return False
