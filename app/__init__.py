
import secrets

from flask import Flask, jsonify, render_template, request, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from config import Config


# =========================================================
# INISIALISASI FLASK
# =========================================================

app = Flask(__name__)
app.config.from_object(Config)

if app.config.get("APP_ENV") == "production":
    secret_key = str(app.config.get("SECRET_KEY", ""))
    placeholder_key = (
        "bts-security-system-secret-key-change-this",
        "your_secret_key_here",
        "replace_with_",
    )
    if len(secret_key) < 32 or any(marker in secret_key.lower() for marker in placeholder_key):
        raise RuntimeError("Production requires a unique SECRET_KEY with at least 32 characters.")
    if not app.config.get("SESSION_COOKIE_SECURE"):
        raise RuntimeError("SESSION_COOKIE_SECURE must be enabled in production.")


# =========================================================
# DATABASE
# =========================================================

db = SQLAlchemy(app)
migrate = Migrate(app, db)


# =========================================================
# LOGIN MANAGER
# =========================================================

login_manager = LoginManager(app)

login_manager.login_view = "login"
login_manager.login_message = "Silakan login terlebih dahulu."
login_manager.login_message_category = "info"


# =========================================================
# IMPORT MODEL
# =========================================================

from app import models  # noqa: F401, E402


# =========================================================
# IMPORT API UTAMA
# =========================================================

from app import api  # noqa: E402

if api.bp.name not in app.blueprints:
    app.register_blueprint(
        api.bp,
        url_prefix="/api"
    )


# =========================================================
# IMPORT ROUTES UTAMA
# =========================================================

from app import routes  # noqa: F401, E402


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": api.get_csrf_token()}


@app.before_request
def protect_html_mutations_and_private_uploads():
    path = (request.path or "").lower()
    if path.startswith(("/static/uploads/evidence/", "/static/uploads/work_orders/")):
        return "Not Found", 404

    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if path.startswith("/api/"):
        return None

    expected = session.get("csrf_token")
    received = (
        request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or ""
    ).strip()
    if not expected or not received or not secrets.compare_digest(expected, received):
        return jsonify({"status": "error", "message": "CSRF token tidak valid."}), 403
    return None


# =========================================================
# SECURITY HEADERS
# =========================================================

@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(self), microphone=(), camera=(self)'
    return response


# =========================================================
# IMPORT SECURITY FEATURES
# =========================================================

from app import feature_routes  # noqa: F401, E402


# =========================================================
# FALLBACK SECURITY FEATURES PAGE
#
# Kalau endpoint security_features_page sudah dibuat oleh
# feature_routes/routes.py, jangan dibuat ulang.
# Kalau belum ada, kita buat otomatis di sini.
# =========================================================

if "security_features_page" not in app.view_functions:

    def security_features_page():
        return render_template("security_features.html")

    app.add_url_rule(
        "/security/features",
        endpoint="security_features_page",
        view_func=security_features_page,
        methods=["GET"]
    )


# =========================================================
# TELEGRAM BOT (BACKGROUND POLLING)
# =========================================================

from app.telegram_bot import start_telegram_bot  # noqa: E402

start_telegram_bot(app)


# =========================================================
# DATABASE
# =========================================================

with app.app_context():
    db.create_all()
