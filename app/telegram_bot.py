import html
import logging
import threading
import time
from datetime import datetime
import requests

logger = logging.getLogger(__name__)

_bot_thread = None
_stop_event = threading.Event()


def _send_telegram_reply(token, chat_id, text, parse_mode="HTML"):
    """Kirim balasan ke chat Telegram."""
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as exc:
        logger.error("Failed to send Telegram reply: %s", exc)
        return False


def handle_telegram_command(app, token, message):
    """Memproses pesan perintah masuk dari Telegram."""
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    from_user = message.get("from", {})
    user_name = from_user.get("first_name") or "Operator"

    if not chat_id or not text:
        return

    config_chat_id = str(app.config.get("TELEGRAM_CHAT_ID", "")).strip()
    if config_chat_id and str(chat_id) != config_chat_id:
        deny_msg = (
            f"⛔ <b>Akses Ditolak</b>\n\n"
            f"Akun Telegram Anda (ID: <code>{chat_id}</code>) tidak terdaftar "
            f"sebagai operator resmi Smart BTS Security System.\n\n"
            f"Hubungi Administrator NOC untuk pendaftaran otorisasi."
        )
        _send_telegram_reply(token, chat_id, deny_msg)
        return

    cmd = text.split()[0].lower() if text else ""
    # Normalize commands with bot username e.g. /status@BtsSecuritySistem_Bot
    if "@" in cmd:
        cmd = cmd.split("@")[0]

    with app.app_context():
        try:
            if cmd in ("/start", "/help", "/menu"):
                help_text = (
                    f"🤖 <b>SMART BTS SECURITY BOT - NOC COMMAND CENTER</b>\n\n"
                    f"Halo, <b>{html.escape(user_name)}</b>! Bot asisten keamanan BTS "
                    f"aktif dan siap menerima instruksi operasi jarak jauh.\n\n"
                    f"📋 <b>Daftar Perintah (Commands):</b>\n"
                    f"▫️ /status — Ringkasan kondisi BTS, gerbang & suhu\n"
                    f"▫️ /wo — Daftar Work Order / Surat Perintah Kerja\n"
                    f"▫️ /logs — 5 aktivitas akses gerbang terakhir\n"
                    f"▫️ /sites — Daftar site tower BTS terdaftar\n"
                    f"▫️ /tech — Daftar teknisi aktif lapangan\n"
                    f"▫️ /lockdown <i>[alasan]</i> — Kunci darurat semua gerbang BTS\n"
                    f"▫️ /unlock — Buka kuncian darurat (Normal)\n"
                    f"▫️ /open &lt;site_id&gt; — Buka gerbang jarak jauh (10 dtk)\n"
                    f"▫️ /ping — Cek koneksi & latensi server\n\n"
                    f"🛡️ <i>Smart BTS Gate Security System • Enterprise Edition</i>"
                )
                _send_telegram_reply(token, chat_id, help_text)

            elif cmd in ("/status", "/info"):
                from app.models import BtsSite, AccessLog, WorkOrder
                from app.feature_routes import _emergency
                from app.access_control import get_gate_status

                sites = BtsSite.query.all()
                total_sites = len(sites)
                active_sites = sum(1 for s in sites if s.status == "Active")

                is_emergency = _emergency.get("enabled", False)
                em_badge = "🚨 <b>AKTIF (LOCKDOWN)</b>" if is_emergency else "🟢 <b>NONAKTIF (NORMAL)</b>"
                em_reason = f" ({html.escape(_emergency.get('reason') or '-')})" if is_emergency else ""

                today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                granted_today = AccessLog.query.filter(
                    AccessLog.timestamp >= today_start,
                    AccessLog.access_result == "GRANTED"
                ).count()
                denied_today = AccessLog.query.filter(
                    AccessLog.timestamp >= today_start,
                    AccessLog.access_result == "DENIED"
                ).count()

                active_wo = WorkOrder.query.filter(
                    WorkOrder.status.in_(["APPROVED", "PENDING", "IN_PROGRESS"])
                ).count()

                lines = [
                    "📊 <b>STATUS SISTEM BTS TELECOM NOC</b>",
                    f"⏱️ <i>{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</i>\n",
                    f"🚨 <b>Emergency Mode:</b> {em_badge}{em_reason}",
                    f"📡 <b>Total Site:</b> {active_sites}/{total_sites} Aktif",
                    f"🎫 <b>Work Order Aktif:</b> {active_wo} tiket",
                    f"🚪 <b>Akses Hari Ini:</b> 🟢 {granted_today} Diterima | 🔴 {denied_today} Ditolak\n",
                    "🏢 <b>Status Site Lapangan:</b>"
                ]

                for s in sites:
                    g_status = get_gate_status(s.site_id)
                    g_badge = "🔓 Buka" if g_status == "OPEN" else "🔒 Tutup"
                    temp = f"{s.temperature}°C" if s.temperature is not None else "-"
                    pwr = f"{s.power_output} kW" if s.power_output is not None else "-"
                    lines.append(
                        f"• <b>{html.escape(s.site_name)}</b> (<code>{s.site_id}</code>)\n"
                        f"  Gerbang: {g_badge} | Suhu: {temp} | Daya: {pwr}"
                    )

                lines.append("\n🛡️ <i>Gunakan /open &lt;site_id&gt; untuk remote control.</i>")
                _send_telegram_reply(token, chat_id, "\n".join(lines))

            elif cmd in ("/wo", "/workorders", "/tiket"):
                from app.models import WorkOrder

                orders = (
                    WorkOrder.query
                    .order_by(WorkOrder.scheduled_date.desc(), WorkOrder.id.desc())
                    .limit(6)
                    .all()
                )

                if not orders:
                    _send_telegram_reply(token, chat_id, "ℹ️ Belum ada Surat Perintah Kerja (Work Order) terdaftar.")
                    return

                lines = [
                    "📋 <b>SURAT PERINTAH KERJA (WORK ORDERS) TERKINI</b>\n"
                ]

                status_emojis = {
                    "APPROVED": "🟡",
                    "PENDING": "🟠",
                    "IN_PROGRESS": "🚀",
                    "COMPLETED": "✅",
                    "VERIFIED": "🌟",
                    "CANCELLED": "🚫",
                }

                for wo in orders:
                    emoji = status_emojis.get(wo.status, "▫️")
                    sched = wo.scheduled_date.strftime("%d/%m/%Y") if wo.scheduled_date else "-"
                    lines.append(
                        f"{emoji} <code>{wo.ticket_number}</code> [<b>{wo.status}</b>]\n"
                        f"📍 <b>Site:</b> {html.escape(wo.site_name)}\n"
                        f"👤 <b>Teknisi:</b> {html.escape(wo.technician_name)}\n"
                        f"📅 <b>Jadwal:</b> {sched}\n"
                        f"🎯 <b>Tugas:</b> {html.escape(wo.purpose)}\n"
                    )

                _send_telegram_reply(token, chat_id, "\n".join(lines))

            elif cmd in ("/logs", "/log", "/riwayat"):
                from app.models import AccessLog

                logs = (
                    AccessLog.query
                    .order_by(AccessLog.timestamp.desc())
                    .limit(6)
                    .all()
                )

                if not logs:
                    _send_telegram_reply(token, chat_id, "ℹ️ Belum ada riwayat akses log.")
                    return

                lines = [
                    "🚪 <b>5 RIWAYAT AKSES GERBANG TERAKHIR</b>\n"
                ]

                for log_item in logs:
                    is_granted = str(log_item.access_result).upper() == "GRANTED"
                    badge = "🟢" if is_granted else "🔴"
                    time_str = log_item.timestamp.strftime("%d/%m %H:%M") if log_item.timestamp else "-"
                    tech_name = html.escape(str(log_item.technician_name or log_item.technician_id or "Unknown"))
                    site_str = html.escape(str(log_item.site_name or log_item.site_id or "-"))
                    res_str = "DIIJINKAN" if is_granted else f"DITOLAK ({html.escape(str(log_item.reason_code or '-'))})"

                    lines.append(
                        f"{badge} <b>{time_str}</b> — <b>{tech_name}</b>\n"
                        f"📍 {site_str} | {res_str}\n"
                    )

                _send_telegram_reply(token, chat_id, "\n".join(lines))

            elif cmd in ("/sites", "/towers"):
                from app.models import BtsSite

                sites = BtsSite.query.all()
                if not sites:
                    _send_telegram_reply(token, chat_id, "ℹ️ Belum ada BTS Site terdaftar.")
                    return

                lines = ["📡 <b>DAFTAR SITE TOWER BTS TELEKOMUNIKASI</b>\n"]
                for s in sites:
                    lines.append(
                        f"🏢 <b>{html.escape(s.site_name)}</b>\n"
                        f"▫️ ID: <code>{s.site_id}</code>\n"
                        f"▫️ Lokasi: {html.escape(s.location or '-')}\n"
                        f"▫️ Status: <b>{s.status}</b>\n"
                    )
                _send_telegram_reply(token, chat_id, "\n".join(lines))

            elif cmd in ("/tech", "/teknisi"):
                from app.models import Technician

                techs = Technician.query.filter_by(status="Active").all()
                if not techs:
                    _send_telegram_reply(token, chat_id, "ℹ️ Belum ada teknisi aktif terdaftar.")
                    return

                lines = ["👷 <b>DAFTAR TEKNISI RESMI AKTIF</b>\n"]
                for t in techs:
                    lines.append(
                        f"👤 <b>{html.escape(t.name)}</b> (<code>{t.technician_id}</code>)\n"
                        f"▫️ Posisi: {html.escape(t.position or '-')}\n"
                        f"▫️ Kontak: {html.escape(t.phone or '-')}\n"
                    )
                _send_telegram_reply(token, chat_id, "\n".join(lines))

            elif cmd in ("/lockdown", "/lock"):
                from app.feature_routes import _emergency, force_close_gate, audit

                args = text.split(maxsplit=1)
                reason = args[1].strip() if len(args) > 1 else f"Diaktifkan via Telegram oleh {user_name}"

                _emergency["enabled"] = True
                _emergency["reason"] = reason
                _emergency["changed_at"] = datetime.utcnow()

                force_close_gate("TELEGRAM_LOCKDOWN")
                audit("EMERGENCY_MODE_ON", f"Telegram Lockdown: {reason}", "CRITICAL")

                reply = (
                    f"🚨 <b>EMERGENCY LOCKDOWN AKTIF!</b>\n\n"
                    f"Seluruh gerbang otomatis site BTS telah <b>DIPAKSA TUTUP</b> dan akses teknisi dibekukan.\n\n"
                    f"📋 <b>Alasan:</b> {html.escape(reason)}\n"
                    f"👮 <b>Otorisasi:</b> {html.escape(user_name)} (Telegram)\n\n"
                    f"<i>Ketik /unlock untuk membuka kuncian darurat.</i>"
                )
                _send_telegram_reply(token, chat_id, reply)

            elif cmd in ("/unlock", "/normal"):
                from app.feature_routes import _emergency, audit

                _emergency["enabled"] = False
                _emergency["reason"] = None
                _emergency["changed_at"] = datetime.utcnow()

                audit("EMERGENCY_MODE_OFF", f"Telegram Lockdown dicabut oleh {user_name}", "WARNING")

                reply = (
                    f"🛡️ <b>EMERGENCY LOCKDOWN DINONAKTIFKAN</b>\n\n"
                    f"Sistem keamanan BTS telah dinormalisasi. Akses teknisi dan pengoperasian gerbang otomatis kembali normal.\n\n"
                    f"👮 <b>Otorisasi:</b> {html.escape(user_name)} (Telegram)"
                )
                _send_telegram_reply(token, chat_id, reply)

            elif cmd == "/open":
                from app.models import BtsSite
                from app.feature_routes import _emergency, audit
                from app.access_control import force_open_gate

                args = text.split(maxsplit=1)
                if len(args) < 2:
                    _send_telegram_reply(
                        token,
                        chat_id,
                        "⚠️ <b>Format Perintah:</b> <code>/open &lt;site_id&gt;</code>\n"
                        "Contoh: <code>/open BTS-BOJONG</code>\n"
                        "<i>Ketik /sites untuk melihat daftar ID site.</i>"
                    )
                    return

                target_site = args[1].strip()
                site = BtsSite.query.filter(
                    (BtsSite.site_id.ilike(f"%{target_site}%")) |
                    (BtsSite.site_name.ilike(f"%{target_site}%"))
                ).first()

                if not site:
                    _send_telegram_reply(
                        token,
                        chat_id,
                        f"❌ Site '<b>{html.escape(target_site)}</b>' tidak ditemukan.\n"
                        f"Ketik /sites untuk melihat daftar site yang valid."
                    )
                    return

                if _emergency.get("enabled", False):
                    _send_telegram_reply(
                        token,
                        chat_id,
                        "⛔ <b>Gagal Membuka Gerbang</b>\n"
                        "Mode Darurat (Emergency Lockdown) sedang aktif!\n"
                        "Harap nonaktifkan mode darurat terlebih dahulu dengan <code>/unlock</code>."
                    )
                    return

                opened = force_open_gate(device_id="TELEGRAM_REMOTE", site_id=site.site_id)
                if not opened:
                    _send_telegram_reply(
                        token,
                        chat_id,
                        f"❌ <b>Gagal Membuka Gerbang</b>: Kendala teknis pada site {html.escape(site.site_name)}."
                    )
                    return

                open_seconds = app.config.get("GATE_OPEN_SECONDS", 10)

                audit(
                    "REMOTE_GATE_OPEN",
                    f"Admin Telegram {user_name} membuka gerbang site {site.site_name}",
                    "WARNING"
                )

                reply = (
                    f"🟢 <b>REMOTE GATE OPEN — BERHASIL</b>\n\n"
                    f"📍 <b>Site:</b> {html.escape(site.site_name)} (<code>{site.site_id}</code>)\n"
                    f"🔓 <b>Status Gerbang:</b> DIBUKA SELAMA {open_seconds} DETIK\n"
                    f"👮 <b>Otorisasi:</b> {html.escape(user_name)} (Telegram NOC)\n\n"
                    f"Gerbang akan tertutup kembali secara otomatis."
                )
                _send_telegram_reply(token, chat_id, reply)

            elif cmd in ("/ping", "/test"):
                _send_telegram_reply(
                    token,
                    chat_id,
                    f"🏓 <b>PONG!</b> Server Smart BTS Security System beroperasi normal.\n"
                    f"⏱️ Waktu Server: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
                )

            else:
                _send_telegram_reply(
                    token,
                    chat_id,
                    f"❓ Perintah tidak dikenali: <code>{html.escape(text)}</code>\n\n"
                    f"Ketik <b>/help</b> atau <b>/start</b> untuk melihat daftar perintah yang tersedia."
                )

        except Exception as handler_err:
            logger.error("Error handling Telegram command '%s': %s", text, handler_err)
            _send_telegram_reply(
                token,
                chat_id,
                f"⚠️ Terjadi kesalahan saat memproses perintah: <code>{html.escape(str(handler_err))}</code>"
            )


def _poll_telegram_updates(app, token):
    """Loop polling getUpdates Telegram di background thread."""
    logger.info("Starting Telegram Bot long-polling worker...")
    last_update_id = 0

    while not _stop_event.is_set():
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {
                "timeout": 20,
                "allowed_updates": ["message"],
            }
            if last_update_id > 0:
                params["offset"] = last_update_id + 1

            resp = requests.get(url, params=params, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    updates = data.get("result", [])
                    for update in updates:
                        up_id = update.get("update_id", 0)
                        if up_id > last_update_id:
                            last_update_id = up_id

                        msg = update.get("message")
                        if msg:
                            handle_telegram_command(app, token, msg)
            elif resp.status_code == 409:
                logger.warning("Telegram polling conflict (409). Sleeping 10s...")
                time.sleep(10)
            else:
                time.sleep(3)

        except requests.exceptions.Timeout:
            continue
        except requests.exceptions.RequestException as net_err:
            logger.warning("Telegram network issue: %s. Retrying in 5s...", net_err)
            time.sleep(5)
        except Exception as exc:
            logger.error("Unexpected error in Telegram polling loop: %s", exc)
            time.sleep(3)


def start_telegram_bot(app):
    """Mulai background worker listener bot Telegram."""
    global _bot_thread

    if _bot_thread and _bot_thread.is_alive():
        return

    enabled = app.config.get("TELEGRAM_NOTIFICATIONS_ENABLED", False)
    token = app.config.get("TELEGRAM_BOT_TOKEN", "").strip()

    if not enabled or not token:
        logger.info("Telegram Bot listener disabled (missing token or disabled in config).")
        return

    _stop_event.clear()
    _bot_thread = threading.Thread(
        target=_poll_telegram_updates,
        args=(app, token),
        name="TelegramBotWorker",
        daemon=True,
    )
    _bot_thread.start()
    logger.info("Telegram Bot Command Center started successfully.")


def stop_telegram_bot():
    """Hentikan background worker listener bot Telegram."""
    _stop_event.set()
