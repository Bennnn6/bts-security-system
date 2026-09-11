import base64
import html
import logging
import threading
from datetime import datetime
import requests

logger = logging.getLogger(__name__)


def _dispatch_telegram(
    token,
    chat_id,
    title,
    message,
    incident_level="INFO",
    evidence_base64=None,
    site_id=None,
    header_badge=None,
):
    try:
        if header_badge:
            badge = header_badge
        elif incident_level == "CRITICAL":
            badge = "🚨 [BTS CRITICAL ALERT]"
        elif incident_level == "HIGH":
            badge = "⚠️ [BTS SECURITY ALERT]"
        elif incident_level in ("GRANTED", "SUCCESS"):
            badge = "🟢 [BTS GATE ACCESS - DIIJINKAN]"
        elif incident_level in ("DENIED", "FAILED"):
            badge = "🔴 [BTS GATE ACCESS - DITOLAK]"
        elif incident_level == "WORK_ORDER":
            badge = "📋 [BTS WORK ORDER UPDATE]"
        elif incident_level == "EMERGENCY":
            badge = "🚨 [BTS EMERGENCY MODE]"
        else:
            badge = f"📢 [BTS NOTIFIKASI - {incident_level}]"

        now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        site_str = html.escape(str(site_id or "-"))

        caption = (
            f"<b>{badge}</b>\n\n"
            f"<b>{html.escape(str(title))}</b>\n"
            f"{message}\n\n"
            f"📍 <b>Site:</b> <code>{site_str}</code>\n"
            f"⏱️ <b>Waktu:</b> {now_str}\n"
            f"🛡️ <i>Smart BTS Gate Security System</i>"
        )

        sent = False
        if evidence_base64:
            try:
                raw_b64 = str(evidence_base64)
                if "," in raw_b64:
                    raw_b64 = raw_b64.split(",", 1)[1]
                image_bytes = base64.b64decode(raw_b64)

                photo_caption = caption if len(caption) <= 1020 else caption[:1015] + "..."
                files = {"photo": ("evidence.jpg", image_bytes, "image/jpeg")}
                data = {"chat_id": chat_id, "caption": photo_caption, "parse_mode": "HTML"}
                url = f"https://api.telegram.org/bot{token}/sendPhoto"
                resp = requests.post(url, data=data, files=files, timeout=10)
                if resp.status_code == 200:
                    sent = True
                else:
                    logger.warning("Telegram sendPhoto failed: %s", resp.text)
            except Exception as img_err:
                logger.warning("Error processing evidence image for Telegram: %s", img_err)

        if not sent:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            msg_text = caption if len(caption) <= 4096 else caption[:4090] + "..."
            payload = {
                "chat_id": chat_id,
                "text": msg_text,
                "parse_mode": "HTML",
            }
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.warning("Telegram sendMessage failed: %s", resp.text)

    except Exception as exc:
        logger.error("Failed to send Telegram alert: %s", exc)


def send_telegram_alert(
    title,
    message,
    incident_level="HIGH",
    evidence_image_base64=None,
    site_id=None,
    header_badge=None,
):
    """
    Kirim notifikasi Telegram secara asinkron di thread terpisah
    sehingga tidak memperlambat respon aplikasi web.
    """
    try:
        from app import app
        config = app.config

        enabled = config.get("TELEGRAM_NOTIFICATIONS_ENABLED", False)
        token = config.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = config.get("TELEGRAM_CHAT_ID", "").strip()

        if not enabled or not token or not chat_id:
            return

        worker = threading.Thread(
            target=_dispatch_telegram,
            args=(token, chat_id, title, message, incident_level, evidence_image_base64, site_id, header_badge),
            daemon=True
        )
        worker.start()

    except Exception as exc:
        logger.error("Error initiating Telegram alert thread: %s", exc)


def send_gate_access_notification(log):
    """
    Kirim notifikasi Telegram secara real-time untuk setiap peristiwa akses gerbang
    (baik GRANTED maupun DENIED / INCIDENT).
    """
    if not log or str(getattr(log, 'access_result', '')).upper() == 'PENDING':
        return

    try:
        from app import app
        config = app.config

        if not config.get("TELEGRAM_NOTIFICATIONS_ENABLED", False):
            return

        all_access_enabled = config.get("TELEGRAM_NOTIFY_ALL_ACCESS", True)
        is_incident = log.incident_level in ("HIGH", "CRITICAL")

        if not all_access_enabled and not is_incident:
            return

        tech_name = html.escape(str(log.technician_name or "Unknown"))
        tech_id = html.escape(str(log.technician_id or "-"))
        site_name = log.site_name or log.site_id or "-"
        access_type = html.escape(str(log.access_type or "MASUK"))
        reason = html.escape(str(log.reason_code or "-"))
        description = html.escape(str(log.description or "-"))
        evidence = getattr(log, "evidence_image", None)

        if str(log.access_result).upper() == "GRANTED":
            badge = "🟢 [BTS GATE ACCESS - DIIJINKAN]"
            title = f"Akses Masuk/Keluar: {tech_name}"
            open_sec = config.get("GATE_OPEN_SECONDS", 10)

            face_detail = f"{html.escape(str(log.face_result or '-'))}"
            if log.face_distance is not None:
                face_detail += f" (dist: {log.face_distance:.3f})"

            otp_detail = html.escape(str(log.otp_result or "-"))

            message = (
                f"👤 <b>Teknisi:</b> {tech_name} (<code>{tech_id}</code>)\n"
                f"🚪 <b>Akses:</b> {access_type}\n"
                f"🔓 <b>Status Gerbang:</b> DIBUKA SELAMA {open_sec} DETIK\n"
                f"📷 <b>Face Recognition:</b> {face_detail}\n"
                f"🔢 <b>OTP Verifikasi:</b> {otp_detail}\n"
                f"📝 <b>Catatan:</b> {description}"
            )
            incident_level = "GRANTED"
        else:
            if is_incident:
                badge = f"🚨 [BTS SECURITY INCIDENT - {log.incident_level}]"
                title = f"Insiden Akses: {reason}"
            else:
                badge = "🔴 [BTS GATE ACCESS - DITOLAK]"
                title = f"Akses Ditolak: {reason}"

            message = (
                f"👤 <b>Teknisi:</b> {tech_name} (<code>{tech_id}</code>)\n"
                f"🚪 <b>Akses:</b> {access_type}\n"
                f"⛔ <b>Hasil:</b> DITOLAK\n"
                f"⚠️ <b>Alasan:</b> <code>{reason}</code>\n"
                f"📊 <b>Level:</b> {html.escape(str(log.incident_level or 'NORMAL'))}\n"
                f"📝 <b>Keterangan:</b> {description}"
            )
            incident_level = log.incident_level or "DENIED"

        send_telegram_alert(
            title=title,
            message=message,
            incident_level=incident_level,
            evidence_image_base64=evidence,
            site_id=site_name,
            header_badge=badge,
        )

        if is_incident:
            send_whatsapp_alert(
                title=title,
                message=message,
                incident_level=log.incident_level,
                site_id=site_name,
            )

    except Exception as exc:
        logger.error("Error in send_gate_access_notification: %s", exc)


def send_work_order_notification(action, work_order, extra_note=None):
    """
    Kirim notifikasi Telegram untuk siklus hidup Work Order / SPK:
    CREATED, STARTED, COMPLETED, VERIFIED, CANCELLED.
    """
    if not work_order:
        return

    try:
        from app import app
        config = app.config

        if not config.get("TELEGRAM_NOTIFICATIONS_ENABLED", False):
            return
        if not config.get("TELEGRAM_NOTIFY_WORK_ORDERS", True):
            return

        action_upper = str(action or "").upper()
        tech_name = html.escape(str(work_order.technician_name or work_order.technician_id or "Teknisi"))
        tech_id = html.escape(str(work_order.technician_id or "-"))
        site_name = work_order.site_name or work_order.site_id or "-"
        ticket = html.escape(str(work_order.ticket_number or "-"))
        purpose = html.escape(str(work_order.purpose or "-"))
        evidence = None

        if action_upper in ("CREATED", "NEW", "APPROVED"):
            badge = "📋 [BTS WORK ORDER - DITERBITKAN]"
            title = f"Work Order Baru: {ticket}"
            sched = work_order.scheduled_date.strftime("%d/%m/%Y") if work_order.scheduled_date else "-"
            notes = html.escape(str(work_order.notes or "-"))
            message = (
                f"🎫 <b>No. Tiket:</b> <code>{ticket}</code>\n"
                f"👤 <b>Teknisi Bertugas:</b> {tech_name} (<code>{tech_id}</code>)\n"
                f"📅 <b>Jadwal:</b> {sched}\n"
                f"🎯 <b>Tujuan Pekerjaan:</b> {purpose}\n"
                f"📝 <b>Instruksi:</b> {notes}"
            )
        elif action_upper in ("STARTED", "IN_PROGRESS"):
            badge = "🚀 [BTS WORK ORDER - DIMULAI]"
            title = f"Pekerjaan Dimulai: {ticket}"
            gps = (
                f"{work_order.start_latitude}, {work_order.start_longitude}"
                if (work_order.start_latitude and work_order.start_longitude)
                else "Tidak terdeteksi"
            )
            time_str = (
                work_order.started_at.strftime("%d/%m/%Y %H:%M:%S")
                if work_order.started_at
                else datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            )
            message = (
                f"🎫 <b>No. Tiket:</b> <code>{ticket}</code>\n"
                f"👤 <b>Teknisi:</b> {tech_name} (<code>{tech_id}</code>)\n"
                f"⏱️ <b>Waktu Mulai:</b> {time_str}\n"
                f"📍 <b>GPS Teknisi:</b> <code>{html.escape(gps)}</code>\n"
                f"🎯 <b>Tujuan:</b> {purpose}"
            )
        elif action_upper == "COMPLETED":
            badge = "✅ [BTS WORK ORDER - SELESAI]"
            title = f"Pekerjaan Selesai: {ticket}"
            gps = (
                f"{work_order.completion_latitude}, {work_order.completion_longitude}"
                if (work_order.completion_latitude and work_order.completion_longitude)
                else "Tidak terdeteksi"
            )
            time_str = (
                work_order.completed_at.strftime("%d/%m/%Y %H:%M:%S")
                if work_order.completed_at
                else datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            )
            note = html.escape(str(work_order.completion_note or "-"))
            message = (
                f"🎫 <b>No. Tiket:</b> <code>{ticket}</code>\n"
                f"👤 <b>Teknisi:</b> {tech_name} (<code>{tech_id}</code>)\n"
                f"⏱️ <b>Waktu Selesai:</b> {time_str}\n"
                f"📍 <b>GPS Lokasi:</b> <code>{html.escape(gps)}</code>\n"
                f"📝 <b>Catatan Hasil:</b> {note}\n"
                f"⏳ <b>Status:</b> Menunggu Verifikasi Admin"
            )
            evidence = getattr(work_order, "evidence_image", None)
        elif action_upper == "VERIFIED":
            badge = "🌟 [BTS WORK ORDER - DIVERIFIKASI]"
            title = f"Work Order Diverifikasi: {ticket}"
            verifier = html.escape(str(work_order.verified_by or "Admin"))
            time_str = (
                work_order.verified_at.strftime("%d/%m/%Y %H:%M:%S")
                if work_order.verified_at
                else datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            )
            message = (
                f"🎫 <b>No. Tiket:</b> <code>{ticket}</code>\n"
                f"👤 <b>Teknisi:</b> {tech_name} (<code>{tech_id}</code>)\n"
                f"👮 <b>Diverifikasi Oleh:</b> {verifier}\n"
                f"⏱️ <b>Waktu Verifikasi:</b> {time_str}\n"
                f"🎉 <b>Status Akhir:</b> Pekerjaan resmi disetujui & ditutup"
            )
        elif action_upper == "CANCELLED":
            badge = "🚫 [BTS WORK ORDER - DIBATALKAN]"
            title = f"Work Order Dibatalkan: {ticket}"
            reason = html.escape(str(extra_note or work_order.notes or "-"))
            message = (
                f"🎫 <b>No. Tiket:</b> <code>{ticket}</code>\n"
                f"👤 <b>Teknisi:</b> {tech_name} (<code>{tech_id}</code>)\n"
                f"⚠️ <b>Status:</b> DIBATALKAN\n"
                f"📝 <b>Alasan:</b> {reason}"
            )
        else:
            badge = f"📋 [BTS WORK ORDER - {action_upper}]"
            title = f"Update Work Order: {ticket}"
            message = (
                f"🎫 <b>No. Tiket:</b> <code>{ticket}</code>\n"
                f"👤 <b>Teknisi:</b> {tech_name} (<code>{tech_id}</code>)\n"
                f"📊 <b>Status Baru:</b> {action_upper}\n"
                f"📝 <b>Catatan:</b> {html.escape(str(extra_note or '-'))}"
            )

        send_telegram_alert(
            title=title,
            message=message,
            incident_level="WORK_ORDER",
            evidence_image_base64=evidence,
            site_id=site_name,
            header_badge=badge,
        )

    except Exception as exc:
        logger.error("Error in send_work_order_notification: %s", exc)


def send_emergency_notification(enabled, reason=None, actor="ADMIN"):
    """
    Kirim notifikasi Telegram saat Emergency Mode diaktifkan atau dinonaktifkan.
    """
    try:
        from app import app
        config = app.config

        if not config.get("TELEGRAM_NOTIFICATIONS_ENABLED", False):
            return
        if not config.get("TELEGRAM_NOTIFY_EMERGENCY", True):
            return

        actor_str = html.escape(str(actor or "SYSTEM"))
        reason_str = html.escape(str(reason or "Tidak ada keterangan"))

        if enabled:
            badge = "🚨 [BTS EMERGENCY LOCKDOWN - AKTIF]"
            title = "DARURAT: Mode Darurat Gerbang Diaktifkan!"
            message = (
                f"⚠️ <b>PERINGATAN KEAMANAN MAKSIMUM!</b>\n"
                f"Sistem keamanan BTS telah dialihkan ke mode <b>EMERGENCY LOCKDOWN</b>.\n"
                f"Semua gerbang otomatis dipaksa TUTUP dan akses teknisi dibekukan.\n\n"
                f"👮 <b>Operator:</b> {actor_str}\n"
                f"📋 <b>Alasan:</b> {reason_str}"
            )
            incident_level = "CRITICAL"
        else:
            badge = "🛡️ [BTS EMERGENCY MODE - DINONAKTIFKAN]"
            title = "Status Normal: Mode Darurat Dicabut"
            message = (
                f"✅ <b>KONDISI KEMBALI AMAN</b>\n"
                f"Mode darurat telah dinonaktifkan. Sistem gerbang BTS kembali beroperasi normal.\n\n"
                f"👮 <b>Operator:</b> {actor_str}"
            )
            incident_level = "NORMAL"

        send_telegram_alert(
            title=title,
            message=message,
            incident_level=incident_level,
            site_id="SEMUA SITE BTS",
            header_badge=badge,
        )

        if enabled:
            send_whatsapp_alert(
                title=title,
                message=message,
                incident_level="CRITICAL",
                site_id="SEMUA SITE BTS",
            )

    except Exception as exc:
        logger.error("Error in send_emergency_notification: %s", exc)


def _dispatch_whatsapp(sid, token, from_number, to_number, title, message, incident_level, site_id=None):
    try:
        from twilio.rest import Client
        client = Client(sid, token)
        badge = "🚨" if incident_level == "CRITICAL" else "⚠️"
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        body = (
            f"{badge} [BTS SECURITY ALERT - {incident_level}]\n\n"
            f"{title}\n{message}\n\n"
            f"📍 Site: {site_id or '-'}\n"
            f"⏱️ Waktu: {now_str}\n"
            f"🛡️ Smart BTS Gate Security System"
        )
        client.messages.create(body=body, from_=from_number, to=f'whatsapp:{to_number}')
    except Exception as exc:
        logger.error("Failed to send WhatsApp alert: %s", exc)


def send_whatsapp_alert(title, message, incident_level="HIGH", site_id=None):
    try:
        from app import app
        config = app.config
        enabled = config.get("WHATSAPP_NOTIFICATIONS_ENABLED", False)
        sid = config.get("WHATSAPP_ACCOUNT_SID", "").strip()
        token = config.get("WHATSAPP_AUTH_TOKEN", "").strip()
        from_num = config.get("WHATSAPP_FROM", "").strip()
        to_num = config.get("WHATSAPP_TO", "").strip()
        if not enabled or not sid or not token or not to_num:
            return
        worker = threading.Thread(
            target=_dispatch_whatsapp,
            args=(sid, token, from_num, to_num, title, message, incident_level, site_id),
            daemon=True
        )
        worker.start()
    except Exception as exc:
        logger.error("Error initiating WhatsApp alert thread: %s", exc)


def send_notification(title, message, incident_level="HIGH", evidence_image_base64=None, site_id=None):
    """Kirim notifikasi ke SEMUA channel yang aktif (Telegram + WhatsApp)."""
    send_telegram_alert(title, message, incident_level, evidence_image_base64, site_id)
    send_whatsapp_alert(title, message, incident_level, site_id)

