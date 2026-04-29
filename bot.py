"""Telegram bot ana dosyası — komutlar + APScheduler ile zamanlı haber akışı."""
import asyncio
import logging
from datetime import datetime
from typing import Any

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from config import (
    ADMIN_USER_IDS,
    CHAT_ID,
    TELEGRAM_TOKEN,
    TIMEZONE,
)
import db
from filter import deduplicate
from scraper import fetch_all_active
from summarizer import summarize_all

logger = logging.getLogger(__name__)


# ===== Yardımcılar =====
def _is_private(update: Update) -> bool:
    """Sohbet özel mi (DM)?"""
    return bool(update.effective_chat and update.effective_chat.type == ChatType.PRIVATE)


def _admin_only(func):
    """Sadece adminlerin çalıştırabildiği, gruplarda hiç cevap vermeyen komutlar için."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Gruplarda komut işleme yok
        if not _is_private(update):
            return
        user = update.effective_user
        if not user:
            return
        if not (db.is_admin(user.id) or user.id in ADMIN_USER_IDS):
            await update.message.reply_text("⛔ Bu komut yalnızca yöneticiler içindir.")
            return
        return await func(update, context)
    return wrapper


def _format_news_message(news: dict[str, Any]) -> str:
    """Tek bir haberi Telegram mesajı olarak formatlar (HTML)."""
    title = (news.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
    summary = (news.get("summary_tr") or news.get("content") or "").replace("<", "&lt;").replace(">", "&gt;")
    source = (news.get("source_name") or "").replace("<", "&lt;").replace(">", "&gt;")
    url = news.get("url") or ""
    column_tag = " 📝<i>köşe yazısı</i>" if news.get("is_column") else ""
    return (
        f"📰 <b>{title}</b>{column_tag}\n\n"
        f"{summary}\n\n"
        f"📌 <i>{source}</i>\n"
        f"🔗 {url}"
    )


# ===== Çekirdek: tek tur haber çekme + özetleme + gönderme =====
async def run_news_cycle(app: Application, chat_id: str, manual: bool = False) -> int:
    """Tüm aktif kaynakları çeker, özetler, hedef chat'e gönderir. Gönderilen sayı döner."""
    try:
        sources = db.list_sources(only_active=True)
        if not sources:
            logger.warning("Aktif kaynak yok.")
            return 0

        # 1) Çek
        raw = fetch_all_active(sources)
        if not raw:
            logger.info("Hiç haber çekilemedi.")
            return 0

        # 2) DB'ye kaydet (yeni olanları)
        new_news_ids: list[int] = []
        for item in raw:
            nid = db.insert_news(
                source_id=item["source_id"],
                title=item["title"],
                content=item["content"],
                url=item["url"],
                published_at=item.get("published_at"),
                is_column=item.get("is_column", False),
            )
            if nid:
                new_news_ids.append(nid)

        logger.info("DB'ye %d yeni haber eklendi.", len(new_news_ids))

        # 3) Henüz bu chat'e gönderilmemiş haberleri al
        pending = db.get_unsent_news(chat_id, limit=50)
        if not pending:
            logger.info("Gönderilecek yeni haber yok.")
            return 0

        # 4) Tekrar filtresi
        pending = deduplicate(pending)

        # 5) Özetleme (parti parti)
        to_summarize = [p for p in pending if not p.get("summary_tr")]
        if to_summarize:
            summaries = summarize_all(to_summarize)
            for nid, summary in summaries.items():
                db.update_news_summary(nid, summary)
            # Pending listesindeki güncellenmiş özetleri yansıt
            for p in pending:
                if p["id"] in summaries:
                    p["summary_tr"] = summaries[p["id"]]

        # 6) Gönder
        sent_count = 0
        for news in pending:
            try:
                msg = await app.bot.send_message(
                    chat_id=chat_id,
                    text=_format_news_message(news),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                )
                db.mark_news_sent(news["id"], chat_id, message_id=msg.message_id)
                sent_count += 1
                # Telegram rate limit için kısa bekleme
                await asyncio.sleep(1.2)
            except Exception as e:
                logger.exception("Mesaj gönderilemedi: %s", e)

        logger.info("Toplam %d haber gönderildi.", sent_count)
        return sent_count
    except Exception as e:
        logger.exception("Haber döngüsü hatası: %s", e)
        return 0


# ===== Komutlar =====
@_admin_only
async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/start"""
    await update.message.reply_text(
        "👋 Haber akışı botuna hoş geldiniz!\n\n"
        "Tüm komutları görmek için /yardim yazın."
    )


@_admin_only
async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/yardim"""
    text = (
        "<b>📋 Komutlar</b>\n\n"
        "/kaynaklar — Tüm kaynakları listele\n"
        "/kaynak_ekle &lt;tip&gt; &lt;ad&gt; &lt;url|@handle&gt; — Yeni kaynak ekle\n"
        "    tip: rss | web | x\n"
        "    örnek: /kaynak_ekle rss BBCTürkçe https://www.bbc.com/turkce/index.xml\n"
        "    örnek: /kaynak_ekle x AAGorus AAGorus\n"
        "/kaynak_sil &lt;id&gt; — Kaynak sil\n"
        "/kaynak_etkinlestir &lt;id&gt; — Kaynağı etkinleştir\n"
        "/kaynak_kapat &lt;id&gt; — Kaynağı pasifleştir\n"
        "/saatler — Zamanlama saatlerini listele\n"
        "/saat_ekle &lt;HH:MM&gt; — Yeni gönderim saati\n"
        "/saat_sil &lt;id&gt; — Saat sil\n"
        "/manuel_ozet — Şimdi haberleri çek ve özetle gönder\n"
        "/durum — Bot ve veritabanı durumu"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@_admin_only
async def cmd_sources(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/kaynaklar"""
    sources = db.list_sources()
    if not sources:
        await update.message.reply_text("Kaynak yok.")
        return
    lines = ["<b>📚 Kaynaklar</b>"]
    for s in sources:
        status = "✅" if s["active"] else "❌"
        target = s.get("url") or f"@{s.get('x_handle', '')}"
        lines.append(f"{status} <code>{s['id']}</code> [{s['source_type']}] <b>{s['name']}</b> — {target}")
    # Telegram mesaj sınırı (4096) için parçala
    text = "\n".join(lines)
    for chunk in [text[i:i+3800] for i in range(0, len(text), 3800)]:
        await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)


@_admin_only
async def cmd_add_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kaynak_ekle <tip> <ad> <url_veya_handle>"""
    args = context.args or []
    if len(args) < 3:
        await update.message.reply_text(
            "Kullanım: /kaynak_ekle <rss|web|x> <ad> <url|@handle>"
        )
        return
    stype = args[0].lower()
    name = args[1]
    target = " ".join(args[2:])
    if stype not in ("rss", "web", "x"):
        await update.message.reply_text("Tip rss, web veya x olmalı.")
        return
    if stype == "x":
        new_id = db.add_source(name=name, source_type="x", x_handle=target.lstrip("@"))
    else:
        new_id = db.add_source(name=name, source_type=stype, url=target)
    if new_id:
        await update.message.reply_text(f"✅ Kaynak eklendi (id={new_id}).")
    else:
        await update.message.reply_text("❌ Kaynak eklenemedi.")


@_admin_only
async def cmd_remove_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kaynak_sil <id>"""
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Kullanım: /kaynak_sil <id>")
        return
    ok = db.remove_source(int(args[0]))
    await update.message.reply_text("🗑 Silindi." if ok else "❌ Bulunamadı.")


@_admin_only
async def cmd_enable_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kaynak_etkinlestir <id>"""
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Kullanım: /kaynak_etkinlestir <id>")
        return
    ok = db.set_source_active(int(args[0]), True)
    await update.message.reply_text("✅ Etkinleştirildi." if ok else "❌ Bulunamadı.")


@_admin_only
async def cmd_disable_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kaynak_kapat <id>"""
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Kullanım: /kaynak_kapat <id>")
        return
    ok = db.set_source_active(int(args[0]), False)
    await update.message.reply_text("⏸ Pasifleştirildi." if ok else "❌ Bulunamadı.")


@_admin_only
async def cmd_times(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/saatler"""
    times = db.list_schedule_times()
    if not times:
        await update.message.reply_text("Zamanlama saati yok.")
        return
    lines = ["<b>🕒 Zamanlama Saatleri</b>"]
    for t in times:
        status = "✅" if t["active"] else "❌"
        lines.append(f"{status} <code>{t['id']}</code> {t['hour']:02d}:{t['minute']:02d}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@_admin_only
async def cmd_add_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/saat_ekle HH:MM"""
    args = context.args or []
    if not args or ":" not in args[0]:
        await update.message.reply_text("Kullanım: /saat_ekle HH:MM")
        return
    try:
        h, m = args[0].split(":")
        hour, minute = int(h), int(m)
    except ValueError:
        await update.message.reply_text("Saat formatı hatalı (HH:MM).")
        return
    new_id = db.add_schedule_time(hour, minute)
    if new_id is None:
        await update.message.reply_text("❌ Saat eklenemedi (zaten var olabilir).")
        return
    # Scheduler'ı güncelle
    app = context.application
    _reload_scheduler(app)
    await update.message.reply_text(f"✅ Saat eklendi: {hour:02d}:{minute:02d} (id={new_id})")


@_admin_only
async def cmd_remove_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/saat_sil <id>"""
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Kullanım: /saat_sil <id>")
        return
    ok = db.remove_schedule_time(int(args[0]))
    if ok:
        _reload_scheduler(context.application)
        await update.message.reply_text("🗑 Saat silindi.")
    else:
        await update.message.reply_text("❌ Bulunamadı.")


@_admin_only
async def cmd_manual_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/manuel_ozet — şimdi çek + gönder."""
    user = update.effective_user
    await update.message.reply_text("⏳ Haberler çekiliyor, bu birkaç dakika sürebilir…")
    sent = await run_news_cycle(context.application, CHAT_ID, manual=True)
    if user:
        db.update_last_fetch(user.id, manual=True)
    await update.message.reply_text(f"✅ Tamamlandı. {sent} haber gönderildi.")


@_admin_only
async def cmd_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """/durum"""
    sources = db.list_sources()
    active_sources = [s for s in sources if s["active"]]
    times = db.list_schedule_times(only_active=True)
    recent = db.count_recent_news(24)
    text = (
        "<b>🤖 Bot Durumu</b>\n\n"
        f"• Toplam kaynak: <b>{len(sources)}</b> (aktif: {len(active_sources)})\n"
        f"• Aktif zamanlama: <b>{len(times)}</b>\n"
        f"• Son 24 saatte çekilen haber: <b>{recent}</b>\n"
        f"• Hedef chat: <code>{CHAT_ID}</code>\n"
        f"• Saat dilimi: {TIMEZONE}\n"
        f"• Şimdi: {datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ===== Scheduler =====
_scheduler: AsyncIOScheduler | None = None


def _reload_scheduler(app: Application) -> None:
    """DB'deki saatlere göre scheduler'ı yeniden kurar."""
    global _scheduler
    if _scheduler is None:
        return
    # Eski tüm 'news_cycle' işlerini sil
    for job in _scheduler.get_jobs():
        if job.id.startswith("news_cycle_"):
            _scheduler.remove_job(job.id)
    # Yeni saatleri ekle
    tz = pytz.timezone(TIMEZONE)
    for t in db.list_schedule_times(only_active=True):
        job_id = f"news_cycle_{t['id']}"
        _scheduler.add_job(
            _scheduled_run,
            CronTrigger(hour=t["hour"], minute=t["minute"], timezone=tz),
            id=job_id,
            args=[app],
            replace_existing=True,
        )
        logger.info("Zamanlama eklendi: %02d:%02d (job=%s)", t["hour"], t["minute"], job_id)


async def _scheduled_run(app: Application) -> None:
    """Zamanlanmış haber çekme görevi."""
    logger.info("⏰ Zamanlanmış haber döngüsü başladı.")
    try:
        await run_news_cycle(app, CHAT_ID, manual=False)
    except Exception as e:
        logger.exception("Zamanlanmış görev hatası: %s", e)


# ===== Başlangıç =====
async def _post_init(app: Application) -> None:
    """Bot başlatıldıktan sonra scheduler'ı kur."""
    global _scheduler
    db.init_db()
    # ADMIN_USER_IDS'i DB'ye admin olarak işle
    for uid in ADMIN_USER_IDS:
        db.set_admin(uid, True)
    # CHAT_ID özel sohbet (pozitif tam sayı) ise otomatik admin yap
    try:
        cid = int(CHAT_ID)
        if cid > 0:
            db.set_admin(cid, True)
            logger.info("CHAT_ID kullanıcısı (%d) otomatik admin yapıldı.", cid)
    except (TypeError, ValueError):
        pass
    tz = pytz.timezone(TIMEZONE)
    _scheduler = AsyncIOScheduler(timezone=tz)
    _scheduler.start()
    _reload_scheduler(app)
    logger.info("Bot hazır. Hedef chat: %s", CHAT_ID)


def main() -> None:
    """Bot giriş noktası."""
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(_post_init)
        .build()
    )

    # Komutlar
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("yardim", cmd_help))
    app.add_handler(CommandHandler("kaynaklar", cmd_sources))
    app.add_handler(CommandHandler("kaynak_ekle", cmd_add_source))
    app.add_handler(CommandHandler("kaynak_sil", cmd_remove_source))
    app.add_handler(CommandHandler("kaynak_etkinlestir", cmd_enable_source))
    app.add_handler(CommandHandler("kaynak_kapat", cmd_disable_source))
    app.add_handler(CommandHandler("saatler", cmd_times))
    app.add_handler(CommandHandler("saat_ekle", cmd_add_time))
    app.add_handler(CommandHandler("saat_sil", cmd_remove_time))
    app.add_handler(CommandHandler("manuel_ozet", cmd_manual_summary))
    app.add_handler(CommandHandler("durum", cmd_status))

    logger.info("Telegram bot başlatılıyor…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
