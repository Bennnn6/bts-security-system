from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from config import Config


# =========================================================
# INISIALISASI FLASK
# =========================================================

app = Flask(__name__)
app.config.from_object(Config)


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


# =========================================================
# SECURITY HEADERS
# =========================================================

@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=(self)'
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