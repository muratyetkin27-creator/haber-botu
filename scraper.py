"""Web scraping ve RSS okuyucu (BeautifulSoup + feedparser)."""
import logging
import re
from datetime import datetime
from typing import Any, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from config import HTTP_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)


# Köşe yazısı tespiti için anahtar kelimeler
COLUMN_KEYWORDS = [
    "köşe", "yazar", "yazısı", "yorum", "kose", "yazi",
    "/yazarlar/", "/kose/", "/yorum/", "columnist",
]

# X (Twitter) çekme için nitter mirror'ları (failover)
NITTER_MIRRORS = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.cz",
]


def _http_get(url: str, timeout: int = HTTP_TIMEOUT) -> Optional[requests.Response]:
    """Güvenli HTTP GET isteği (User-Agent ile)."""
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "tr,en;q=0.8"},
            allow_redirects=True,
        )
        if resp.status_code == 200:
            return resp
        logger.warning("HTTP %s döndü: %s", resp.status_code, url)
        return None
    except requests.RequestException as e:
        logger.warning("HTTP isteği başarısız (%s): %s", url, e)
        return None


def _is_column(url: str, title: str = "") -> bool:
    """URL ya da başlığa göre köşe yazısı mı tespit eder."""
    text = f"{url} {title}".lower()
    return any(kw in text for kw in COLUMN_KEYWORDS)


def _clean_text(text: str, max_chars: int = 2000) -> str:
    """HTML kalıntılarını ve fazla boşlukları temizler, kısaltır."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


# ===== RSS Scraping =====
def fetch_rss(source: dict[str, Any], limit: int = 15) -> list[dict[str, Any]]:
    """RSS feed'inden haberleri çeker."""
    url = source.get("url")
    if not url:
        return []
    try:
        # feedparser timeout'u doğrudan desteklemediği için requests ile çekiyoruz
        resp = _http_get(url, timeout=HTTP_TIMEOUT)
        if not resp:
            return []
        feed = feedparser.parse(resp.content)
        items: list[dict[str, Any]] = []
        for entry in feed.entries[:limit]:
            link = entry.get("link", "")
            title = (entry.get("title") or "").strip()
            if not link or not title:
                continue
            # İçerik: summary > description > content
            content = ""
            if entry.get("summary"):
                content = BeautifulSoup(entry.summary, "lxml").get_text(" ")
            elif entry.get("description"):
                content = BeautifulSoup(entry.description, "lxml").get_text(" ")
            content = _clean_text(content)
            published = None
            if entry.get("published"):
                published = entry.published
            elif entry.get("updated"):
                published = entry.updated
            items.append({
                "source_id": source["id"],
                "title": title,
                "content": content,
                "url": link,
                "published_at": published,
                "is_column": _is_column(link, title),
            })
        logger.info("RSS '%s': %d haber çekildi.", source.get("name"), len(items))
        return items
    except Exception as e:
        logger.exception("RSS çekme hatası (%s): %s", source.get("name"), e)
        return []


# ===== HTML Web Scraping (genel amaçlı) =====
def fetch_web(source: dict[str, Any], limit: int = 15) -> list[dict[str, Any]]:
    """Genel HTML sayfasından makale linklerini çeker."""
    url = source.get("url")
    if not url:
        return []
    try:
        resp = _http_get(url)
        if not resp:
            return []
        soup = BeautifulSoup(resp.content, "lxml")
        items: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        # Manşet/başlık linklerini topla
        for a in soup.find_all("a", href=True):
            if len(items) >= limit:
                break
            href: str = a["href"]
            title = a.get_text(strip=True)
            if not title or len(title) < 15:
                continue
            # Bağıl link → mutlak
            if href.startswith("/"):
                base = re.match(r"^(https?://[^/]+)", url)
                if base:
                    href = base.group(1) + href
            if not href.startswith("http"):
                continue
            if href in seen_urls:
                continue
            seen_urls.add(href)
            items.append({
                "source_id": source["id"],
                "title": title,
                "content": "",
                "url": href,
                "published_at": None,
                "is_column": _is_column(href, title),
            })
        logger.info("WEB '%s': %d link çekildi.", source.get("name"), len(items))
        return items
    except Exception as e:
        logger.exception("Web çekme hatası (%s): %s", source.get("name"), e)
        return []


# ===== X (Twitter) Scraping (Nitter üzerinden) =====
def fetch_x(source: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    """X (Twitter) hesabından son gönderileri Nitter üzerinden çeker."""
    handle = source.get("x_handle", "").lstrip("@")
    if not handle:
        return []
    last_err: Optional[str] = None
    for mirror in NITTER_MIRRORS:
        nitter_url = f"{mirror}/{handle}"
        try:
            resp = _http_get(nitter_url, timeout=HTTP_TIMEOUT)
            if not resp:
                continue
            soup = BeautifulSoup(resp.content, "lxml")
            tweets = soup.select(".timeline-item")
            if not tweets:
                continue
            items: list[dict[str, Any]] = []
            for t in tweets[:limit]:
                content_el = t.select_one(".tweet-content")
                link_el = t.select_one("a.tweet-link")
                if not content_el or not link_el:
                    continue
                content = _clean_text(content_el.get_text(" "))
                if not content:
                    continue
                tweet_path = link_el.get("href", "")
                # Twitter.com URL'sine çevir
                tweet_url = f"https://twitter.com{tweet_path}" if tweet_path.startswith("/") else tweet_path
                # Başlık: ilk 100 karakter
                title = content[:100] + ("…" if len(content) > 100 else "")
                items.append({
                    "source_id": source["id"],
                    "title": title,
                    "content": content,
                    "url": tweet_url,
                    "published_at": None,
                    "is_column": False,
                })
            if items:
                logger.info("X '@%s' (%s): %d gönderi çekildi.", handle, mirror, len(items))
                return items
        except Exception as e:
            last_err = str(e)
            logger.debug("Nitter mirror %s başarısız: %s", mirror, e)
            continue
    logger.warning("X '@%s' tüm aynalardan çekilemedi (son hata: %s)", handle, last_err)
    return []


# ===== Birleşik dispatcher =====
def fetch_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Kaynak tipine göre uygun scraper'ı çağırır."""
    try:
        stype = source.get("source_type")
        if stype == "rss":
            return fetch_rss(source)
        if stype == "web":
            return fetch_web(source)
        if stype == "x":
            return fetch_x(source)
        logger.warning("Bilinmeyen source_type: %s", stype)
        return []
    except Exception as e:
        logger.exception("Kaynak çekme hatası (%s): %s", source.get("name"), e)
        return []


def fetch_all_active(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tüm aktif kaynakları sırayla çeker ve birleştirir."""
    all_items: list[dict[str, Any]] = []
    for src in sources:
        if not src.get("active"):
            continue
        items = fetch_source(src)
        all_items.extend(items)
    logger.info("Toplam %d kaynaktan %d ham haber çekildi.", len(sources), len(all_items))
    return all_items
