# Repository Guidelines

## Project Structure & Module Organization
This is a Flask application for BTS site access security. `run.py` starts the server, while `config.py` loads environment-backed settings for the database, face matching, OTP, gate timing, work orders, notifications, and relay integration. The `app/` package is initialized eagerly in `app/__init__.py`: SQLAlchemy, Flask-Migrate, Flask-Login, models, the `/api` blueprint, page routes, and security feature routes are registered there. Domain behavior is split across `access_control.py`, `face_recognition_module.py`, `totp_module.py`, and `notifications.py`; persistence models live in `models.py`. HTML templates and browser/PWA assets are under `app/templates/` and `app/static/`. Schema revisions are stored in `migrations/`, and the default SQLite database is under `instance/`.

## Build, Test, and Development Commands
Use Python 3.10 or 3.11 in a virtual environment on Windows:

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

Start the local Flask server with `python run.py`, then open `http://localhost:5000`. Run the repository’s automated verification script with `python test_system.py`. There is no package script or test-runner configuration in the repository, so individual test selection is not available; the script checks imports, database connectivity and schema, API endpoints, page routes, and security features as one run.

## Coding Style & Naming Conventions
The Python code uses four-space indentation, `snake_case` functions and variables, `PascalCase` model classes, and single-quoted strings in most application modules. Keep route handlers and security logic in their existing modules, and use the established Flask/SQLAlchemy integrations rather than introducing a separate application entry point. Runtime configuration belongs in environment variables loaded by `config.py`; do not put local secrets in source files.

## Testing Guidelines
`test_system.py` is a standalone diagnostic script, not a pytest suite. It imports `app` and `db`, runs inside an application context, and prints `[ PASS ]` or `[ FAIL ]` checks. It points `DATABASE_URL` at a temporary SQLite file before importing the app (override with `TEST_DATABASE_URL`), so the test fixtures it seeds — including `admin/admin123` — never reach the real database. Run it after dependency installation and database changes.

## Database Changes
Track schema changes as revisions in `migrations/versions/`. The application currently calls `db.create_all()` during initialization, while Flask-Migrate is also configured; keep model changes and migration revisions consistent.