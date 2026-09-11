import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    # =====================================================
    # SECRET KEY
    # =====================================================

    SECRET_KEY = os.environ.get(
        'SECRET_KEY',
        'bts-security-system-secret-key-change-this'
    )

    # =====================================================
    # DATABASE
    # =====================================================

    SQLALCHEMY_DATABASE_URI = (
        os.environ.get('DATABASE_URL')
        or os.environ.get('SQLALCHEMY_DATABASE_URI')
        or 'sqlite:///app.db'
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # =====================================================
    # FACE RECOGNITION
    # =====================================================
    #
    # Semakin kecil nilai threshold,
    # semakin ketat pencocokan wajah.
    #
    # Contoh:
    # 0.40 = ketat
    # 0.45 = standar/ketat
    # 0.50 = lebih longgar
    #
    # =====================================================

    FACE_MATCH_THRESHOLD = float(
        os.environ.get(
            'FACE_MATCH_THRESHOLD',
            '0.45'
        )
    )

    # =====================================================
    # MAX IMAGE SIZE
    # =====================================================

    MAX_CONTENT_LENGTH = 5 * 1024 * 1024

    # =====================================================
    # GATE
    # =====================================================

    GATE_OPEN_SECONDS = int(
        os.environ.get(
            'GATE_OPEN_SECONDS',
            '10'
        )
    )

    # =====================================================
    # OTP SECURITY
    # =====================================================

    OTP_MAX_FAILURES = int(
        os.environ.get(
            'OTP_MAX_FAILURES',
            '3'
        )
    )

    OTP_LOCKOUT_MINUTES = int(
        os.environ.get(
            'OTP_LOCKOUT_MINUTES',
            '5'
        )
    )

    # =====================================================
    # WORK ORDER / TIKET KERJA
    # =====================================================

    ENABLE_WORK_ORDER_CHECK = (
        os.environ.get('ENABLE_WORK_ORDER_CHECK', 'False').strip().lower()
        in ('true', '1', 'yes')
    )

    # =====================================================
    # TELEGRAM NOTIFICATIONS
    # =====================================================

    TELEGRAM_NOTIFICATIONS_ENABLED = (
        os.environ.get('TELEGRAM_NOTIFICATIONS_ENABLED', 'False').strip().lower()
        in ('true', '1', 'yes')
    )

    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

    # Real-time event notifications (Selalu Update)
    TELEGRAM_NOTIFY_ALL_ACCESS = (
        os.environ.get('TELEGRAM_NOTIFY_ALL_ACCESS', 'True').strip().lower()
        in ('true', '1', 'yes')
    )
    TELEGRAM_NOTIFY_WORK_ORDERS = (
        os.environ.get('TELEGRAM_NOTIFY_WORK_ORDERS', 'True').strip().lower()
        in ('true', '1', 'yes')
    )
    TELEGRAM_NOTIFY_EMERGENCY = (
        os.environ.get('TELEGRAM_NOTIFY_EMERGENCY', 'True').strip().lower()
        in ('true', '1', 'yes')
    )

    # =====================================================
    # WHATSAPP NOTIFICATIONS (Twilio)
    # =====================================================

    WHATSAPP_NOTIFICATIONS_ENABLED = (
        os.environ.get('WHATSAPP_NOTIFICATIONS_ENABLED', 'False').strip().lower()
        in ('true', '1', 'yes')
    )

    WHATSAPP_ACCOUNT_SID = os.environ.get('WHATSAPP_ACCOUNT_SID', '').strip()
    WHATSAPP_AUTH_TOKEN = os.environ.get('WHATSAPP_AUTH_TOKEN', '').strip()
    WHATSAPP_FROM = os.environ.get('WHATSAPP_FROM', 'whatsapp:+14155238886').strip()
    WHATSAPP_TO = os.environ.get('WHATSAPP_TO', '').strip()

    # =====================================================
    # SESSION / COOKIE SECURITY
    # =====================================================

    SESSION_TIMEOUT_MINUTES = int(
        os.environ.get(
            'SESSION_TIMEOUT_MINUTES',
            '30'
        )
    )

    SESSION_COOKIE_SECURE = (
        os.environ.get('SESSION_COOKIE_SECURE', 'False').strip().lower()
        in ('true', '1', 'yes')
    )

    SESSION_COOKIE_HTTPONLY = True

    SESSION_COOKIE_SAMESITE = (
        os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax').strip()
        or 'Lax'
    )

    PERMANENT_SESSION_LIFETIME = (
        SESSION_TIMEOUT_MINUTES * 60
    )

    # =====================================================
    # HARDWARE / IOT RELAY WEBHOOK
    # =====================================================

    GATE_WEBHOOK_URL = os.environ.get('GATE_WEBHOOK_URL', '').strip()
