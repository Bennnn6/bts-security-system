from functools import wraps

from flask import flash, redirect, session, url_for
from flask_login import current_user


def is_admin():
    try:
        return bool(
            current_user
            and current_user.is_authenticated
        )
    except Exception:
        return session.get("auth_role") == "ADMIN"


def is_technician():
    return bool(
        session.get("worker_id")
        or session.get("auth_role") == "TECHNICIAN"
    )


def is_authenticated_user():
    return is_admin() or is_technician()


def get_current_role():
    if is_admin():
        return "ADMIN"
    if is_technician():
        return "TECHNICIAN"
    return "GUEST"


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if is_admin():
            return function(*args, **kwargs)

        if is_technician():
            flash(
                "Akses ditolak. Halaman ini hanya untuk Admin.",
                "danger",
            )
            return redirect(
                url_for("worker_dashboard")
            )

        flash(
            "Silakan login sebagai Admin terlebih dahulu.",
            "warning",
        )
        return redirect(
            url_for("login")
        )

    return wrapper


def technician_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if is_technician():
            return function(*args, **kwargs)

        if is_admin():
            flash(
                "Halaman ini khusus untuk Teknisi.",
                "warning",
            )
            return redirect(
                url_for("dashboard")
            )

        flash(
            "Silakan login terlebih dahulu.",
            "info",
        )
        return redirect(
            url_for("login")
        )

    return wrapper


def login_required_any(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if is_authenticated_user():
            return function(*args, **kwargs)

        flash(
            "Silakan login terlebih dahulu.",
            "info",
        )
        return redirect(
            url_for("login")
        )

    return wrapper


def require_role(role):
    role = str(role).strip().upper()

    def decorator(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            current_role = get_current_role()

            if current_role == role:
                return function(*args, **kwargs)

            if current_role == "GUEST":
                flash(
                    "Silakan login terlebih dahulu.",
                    "info",
                )
                return redirect(
                    url_for("login")
                )

            flash(
                "Anda tidak memiliki hak akses ke halaman ini.",
                "danger",
            )

            if current_role == "ADMIN":
                return redirect(
                    url_for("dashboard")
                )

            if current_role == "TECHNICIAN":
                return redirect(
                    url_for("worker_dashboard")
                )

            return redirect(
                url_for("login")
            )

        return wrapper

    return decorator


def get_current_user_id():
    if is_admin():
        try:
            if current_user.is_authenticated:
                return str(current_user.id)
        except Exception:
            pass

        value = session.get("auth_user_id")
        return str(value) if value is not None else None

    if is_technician():
        value = (
            session.get("worker_id")
            or session.get("auth_user_id")
        )
        return str(value) if value is not None else None

    return None


def get_current_username():
    if is_admin():
        try:
            username = getattr(
                current_user,
                "username",
                None,
            )
            if username:
                return username
        except Exception:
            pass

        return session.get(
            "auth_username",
            "ADMIN",
        )

    if is_technician():
        return (
            session.get("worker_name")
            or session.get("auth_username")
            or session.get("worker_id")
            or "TECHNICIAN"
        )

    return "GUEST"


def get_auth_context():
    return {
        "authenticated": is_authenticated_user(),
        "role": get_current_role(),
        "user_id": get_current_user_id(),
        "username": get_current_username(),
        "is_admin": is_admin(),
        "is_technician": is_technician(),
    }
