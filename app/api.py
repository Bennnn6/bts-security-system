from datetime import datetime, timedelta
from functools import wraps
import secrets
import time

from flask import (
    Blueprint,
    request,
    jsonify,
    session,
    current_app,
)

from flask_login import current_user

from sqlalchemy import or_

from app import db
from app.models import (
    Technician,
    BtsSite,
    AccessLog,
    ActivityLog,
)

from app.face_recognition_module import (
    extract_face_encoding,
    compare_faces,
)

from app.totp_module import verify_totp

from app.access_control import (
    open_gate,
    close_gate,
    force_open_gate,
    force_close_gate,
    get_gate_status,
    get_gate_health,
    get_gate_opened_at,
    get_gate_closed_at,
    get_gate_last_error,
    get_gate_last_action,
    get_gate_device_id,
    check_gate_health,
)


# =========================================================
# BLUEPRINT
# =========================================================

bp = Blueprint("api", __name__)


# =========================================================
# SECURITY CONFIG
# =========================================================

VALID_ACCESS_TYPES = (
    "MASUK",
    "KELUAR",
)

VALID_INCIDENT_LEVELS = (
    "NORMAL",
    "WARNING",
    "HIGH",
    "CRITICAL",
)

VALID_ACCESS_RESULTS = (
    "GRANTED",
    "DENIED",
    "PENDING",
)

OTP_MAX_FAILURES = 3
OTP_LOCKOUT_MINUTES = 5

GATE_OPEN_SECONDS = 30
GATE_COOLDOWN_SECONDS = 3

MAX_EVIDENCE_LENGTH = 2_000_000
MAX_JSON_LENGTH = 3_000_000

LIVENESS_CHALLENGE_TIMEOUT = 60

# Rate limit sederhana untuk demo/local.
# key = IP + endpoint
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX_REQUESTS = 30

_rate_limit_store = {}

# Challenge liveness
_liveness_sessions = {}


# =========================================================
# BASIC RESPONSE
# =========================================================

def api_error(message, status_code=400, **extra):
    data = {
        "status": "error",
        "message": message,
    }

    data.update(extra)

    return jsonify(data), status_code


def api_success(message=None, **data):
    result = {
        "status": "success",
    }

    if message:
        result["message"] = message

    result.update(data)

    return jsonify(result)


# =========================================================
# DEVICE / IP
# =========================================================

def get_request_device():
    forwarded = (
        request.headers.get("X-Forwarded-For")
        or ""
    ).strip()

    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    else:
        client_ip = (
            request.remote_addr
            or "UNKNOWN"
        )

    user_agent = (
        request.headers.get("User-Agent")
        or "UNKNOWN"
    ).strip()

    # Batasi panjang agar tidak memenuhi database.
    return (
        f"{client_ip} | {user_agent[:450]}"
    )[:500]


def get_client_ip():
    forwarded = (
        request.headers.get("X-Forwarded-For")
        or ""
    ).strip()

    if forwarded:
        return forwarded.split(",")[0].strip()

    return (
        request.remote_addr
        or "UNKNOWN"
    )


# =========================================================
# RATE LIMIT
# =========================================================

def check_rate_limit():
    """
    Rate limiting sederhana berbasis IP + endpoint.
    """

    now = time.time()

    ip = get_client_ip()

    key = (
        f"{ip}:"
        f"{request.endpoint or request.path}"
    )

    history = _rate_limit_store.get(key, [])

    history = [
        timestamp
        for timestamp in history
        if now - timestamp < RATE_LIMIT_WINDOW
    ]

    if len(history) >= RATE_LIMIT_MAX_REQUESTS:

        _rate_limit_store[key] = history

        return False

    history.append(now)

    _rate_limit_store[key] = history

    # Bersihkan store yang sudah lama.
    if len(_rate_limit_store) > 5000:

        stale_keys = []

        for store_key, values in _rate_limit_store.items():

            if not values:
                stale_keys.append(store_key)
                continue

            if now - values[-1] > RATE_LIMIT_WINDOW:

                stale_keys.append(store_key)

        for stale_key in stale_keys[:1000]:
            _rate_limit_store.pop(
                stale_key,
                None,
            )

    return True


@bp.before_request
def api_rate_limit():
    if not check_rate_limit():

        return api_error(
            "Terlalu banyak request. "
            "Silakan coba lagi beberapa saat.",
            429,
        )

    return None


# =========================================================
# JSON LIMIT
# =========================================================

@bp.before_request
def check_request_size():
    """
    Tolak payload yang terlalu besar.
    """

    content_length = request.content_length

    if content_length is None:
        return None

    if content_length > MAX_JSON_LENGTH:

        return api_error(
            "Ukuran request terlalu besar.",
            413,
        )

    return None


# =========================================================
# CSRF
# =========================================================

def get_csrf_token():
    token = session.get("csrf_token")

    if not token:

        token = secrets.token_urlsafe(32)

        session["csrf_token"] = token

        session.permanent = True

    return token


def csrf_protected(function):
    """
    Proteksi CSRF untuk endpoint yang mengubah data.
    """

    @wraps(function)
    def wrapper(*args, **kwargs):

        expected = session.get("csrf_token")

        if not expected:

            expected = get_csrf_token()

        received = (
            request.headers.get("X-CSRFToken")
            or request.headers.get("X-CSRF-Token")
            or ""
        ).strip()

        if not received:

            return api_error(
                "CSRF token wajib dikirim.",
                403,
                reason_code="CSRF_MISSING",
            )

        if not secrets.compare_digest(
            expected,
            received,
        ):

            create_activity_log(
                action="CSRF_BLOCKED",
                description=(
                    "Request API ditolak "
                    "karena CSRF token tidak valid."
                ),
                incident_level="HIGH",
            )

            return api_error(
                "CSRF token tidak valid.",
                403,
                reason_code="CSRF_INVALID",
            )

        return function(*args, **kwargs)

    return wrapper


# =========================================================
# CSRF TOKEN ENDPOINT
# =========================================================

@bp.route(
    "/csrf-token",
    methods=["GET"],
)
def csrf_token():

    token = get_csrf_token()

    return api_success(
        csrf_token=token,
    )


# =========================================================
# SESSION USER
# =========================================================

def get_logged_worker_id():
    return (
        session.get("worker_id")
        or None
    )


def is_authenticated_api_user():
    """
    API dapat digunakan oleh:
    - Admin melalui Flask-Login session
    - Teknisi melalui worker_id session
    """

    try:

        from flask_login import current_user

        if (
            current_user
            and current_user.is_authenticated
        ):
            return True

    except Exception:
        pass

    return bool(
        get_logged_worker_id()
    )


def api_login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):

        if not is_authenticated_api_user():

            return api_error(
                "Authentication required.",
                401,
                reason_code="AUTH_REQUIRED",
            )

        return function(*args, **kwargs)

    return wrapper



def api_admin_required(function):
    """Endpoint API khusus Admin."""
    @wraps(function)
    def wrapper(*args, **kwargs):

        if (
            not current_user
            or not current_user.is_authenticated
        ):
            return api_error(
                "Admin authentication required.",
                403,
                reason_code="ADMIN_REQUIRED",
            )

        return function(*args, **kwargs)

    return wrapper


# =========================================================
# ACTIVITY LOG
# =========================================================

def create_activity_log(
    action,
    description,
    incident_level="NORMAL",
    device_id=None,
):
    """
    Audit log yang aman dari error.
    """

    try:

        username = "SYSTEM"

        worker_id = session.get(
            "worker_id"
        )

        if worker_id:

            technician = (
                Technician.query
                .filter_by(
                    technician_id=worker_id
                )
                .first()
            )

            if technician:
                username = technician.name
            else:
                username = worker_id

        else:

            try:

                from flask_login import current_user

                if current_user.is_authenticated:
                    username = (
                        current_user.username
                    )

            except Exception:
                pass

        log = ActivityLog(
            timestamp=datetime.utcnow(),
            username=username,
            action=action,
            description=description[:500]
            if description
            else "",
            incident_level=(
                incident_level
                if incident_level
                in VALID_INCIDENT_LEVELS
                else "NORMAL"
            ),
            device_id=(
                device_id
                or get_request_device()
            ),
        )

        db.session.add(log)
        db.session.commit()

        if log.incident_level in ("HIGH", "CRITICAL"):
            try:
                from app.notifications import send_telegram_alert
                send_telegram_alert(
                    title=f"Aktivitas Keamanan: {action}",
                    message=f"User: {username}\nDeskripsi: {description}",
                    incident_level=log.incident_level,
                    site_id=None
                )
            except Exception:
                pass

    except Exception as exc:

        db.session.rollback()

        print(
            "[ACTIVITY LOG ERROR]",
            exc,
        )


# =========================================================
# SITE VALIDATION
# =========================================================

def validate_site_and_access(
    site_id,
    access_type,
):
    site_id = (
        str(site_id or "")
        .strip()
    )

    access_type = (
        str(access_type or "")
        .strip()
        .upper()
    )

    if not site_id:

        return None, (
            "Site wajib dipilih.",
            "SITE_REQUIRED",
        )

    if len(site_id) > 50:

        return None, (
            "Site ID terlalu panjang.",
            "SITE_INVALID",
        )

    if access_type not in VALID_ACCESS_TYPES:

        return None, (
            "Jenis akses tidak valid.",
            "ACCESS_TYPE_INVALID",
        )

    site = (
        BtsSite.query
        .filter_by(site_id=site_id)
        .first()
    )

    if not site:

        return None, (
            "BTS Site tidak ditemukan.",
            "SITE_NOT_FOUND",
        )

    if site.status != "Normal (Up)":

        return None, (
            "BTS Site sedang tidak aktif.",
            "SITE_NOT_AVAILABLE",
        )

    return site, None


# =========================================================
# EVIDENCE VALIDATION
# =========================================================

def validate_evidence(image_data):
    """
    Validasi evidence image/base64.
    """

    if not image_data:

        return True, None

    if not isinstance(
        image_data,
        str,
    ):

        return False, (
            "Evidence tidak valid."
        )

    if len(image_data) > MAX_EVIDENCE_LENGTH:

        return False, (
            "Ukuran evidence terlalu besar."
        )

    # Hanya izinkan Data URI image.
    if not image_data.startswith(
        "data:image/"
    ):

        return False, (
            "Evidence harus berupa image Data URI."
        )

    if ";base64," not in image_data:

        return False, (
            "Format evidence tidak valid."
        )

    return True, None


# =========================================================
# LIVENESS
# =========================================================

LIVENESS_ACTIONS = (
    "BLINK",
    "TURN_LEFT",
    "TURN_RIGHT",
)


def cleanup_liveness():
    now = time.time()

    expired = []

    for key, value in _liveness_sessions.items():

        created = value.get(
            "created_at",
            now,
        )

        if (
            now - created
            > LIVENESS_CHALLENGE_TIMEOUT
        ):

            expired.append(key)

    for key in expired:
        _liveness_sessions.pop(
            key,
            None,
        )


@bp.route(
    "/liveness-challenge",
    methods=["GET"],
)
@api_login_required
def liveness_challenge():

    cleanup_liveness()

    challenge = secrets.choice(
        LIVENESS_ACTIONS
    )

    challenge_id = (
        secrets.token_urlsafe(24)
    )

    _liveness_sessions[
        challenge_id
    ] = {
        "challenge": challenge,
        "created_at": time.time(),
        "completed": False,
    }

    return api_success(
        challenge_id=challenge_id,
        challenge=challenge,
        expires_in=LIVENESS_CHALLENGE_TIMEOUT,
    )


def validate_liveness(
    challenge_id,
    challenge_result,
):
    """
    Verifikasi challenge sederhana.

    Catatan:
    Ini masih challenge-state validation,
    bukan computer vision liveness detection.
    """

    if not challenge_id:

        return False, (
            "Liveness challenge belum dibuat.",
            "LIVENESS_REQUIRED",
        )

    state = _liveness_sessions.get(
        challenge_id
    )

    if not state:

        return False, (
            "Liveness challenge tidak ditemukan "
            "atau sudah kedaluwarsa.",
            "LIVENESS_EXPIRED",
        )

    if (
        time.time()
        - state["created_at"]
        > LIVENESS_CHALLENGE_TIMEOUT
    ):

        _liveness_sessions.pop(
            challenge_id,
            None,
        )

        return False, (
            "Liveness challenge telah kedaluwarsa.",
            "LIVENESS_EXPIRED",
        )

    expected = state["challenge"]

    received = (
        str(challenge_result or "")
        .strip()
        .upper()
    )

    if received != expected:

        return False, (
            "Liveness challenge tidak sesuai.",
            "LIVENESS_FAILED",
        )

    state["completed"] = True

    return True, None


# =========================================================
# ACCESS STATE
# =========================================================

def get_latest_access_state(
    technician_id,
):
    latest = (
        AccessLog.query
        .filter(
            AccessLog.technician_id
            == technician_id,

            AccessLog.access_result
            == "GRANTED",

            AccessLog.access_type.in_(
                [
                    "MASUK",
                    "KELUAR",
                ]
            ),
        )
        .order_by(
            AccessLog.timestamp.desc()
        )
        .first()
    )

    if not latest:

        return {
            "inside": False,
            "site_id": None,
            "site_name": None,
            "access_type": None,
        }

    return {
        "inside": (
            latest.access_type
            == "MASUK"
        ),
        "site_id": latest.site_id,
        "site_name": (
            latest.site_name
            or (
                latest.site.site_name
                if latest.site
                else None
            )
        ),
        "access_type": (
            latest.access_type
        ),
    }


def validate_access_state(
    technician_id,
    site_id,
    access_type,
):
    """
    Memastikan urutan MASUK/KELUAR masuk akal.
    """

    state = get_latest_access_state(
        technician_id
    )

    if access_type == "MASUK":

        if state["inside"]:

            return False, (
                "Teknisi masih tercatat "
                "berada di dalam site.",
                "ALREADY_INSIDE",
            )

    elif access_type == "KELUAR":

        if not state["inside"]:

            return False, (
                "Teknisi belum tercatat masuk.",
                "NOT_INSIDE",
            )

        if (
            state["site_id"]
            and state["site_id"] != site_id
        ):

            return False, (
                "Teknisi tercatat berada "
                "di site lain.",
                "WRONG_SITE",
            )

    return True, None


# =========================================================
# OTP SECURITY
# =========================================================

def get_recent_otp_failures(
    technician_id,
):
    cutoff = (
        datetime.utcnow()
        - timedelta(
            minutes=OTP_LOCKOUT_MINUTES
        )
    )

    return (
        AccessLog.query
        .filter(
            AccessLog.technician_id
            == technician_id,

            AccessLog.timestamp
            >= cutoff,

            AccessLog.otp_result
            == "FAILED",
        )
        .count()
    )


def otp_locked(
    technician_id,
):
    failures = get_recent_otp_failures(
        technician_id
    )

    return failures >= OTP_MAX_FAILURES


# =========================================================
# GATE COOLDOWN
# =========================================================

def gate_is_in_cooldown(site_id=None):
    opened_at = (
        get_gate_opened_at(site_id)
    )

    if not opened_at:
        return False

    elapsed = (
        datetime.utcnow()
        - opened_at
    ).total_seconds()

    return elapsed < GATE_COOLDOWN_SECONDS


# =========================================================
# SAVE ACCESS LOG
# =========================================================

def save_access_log(
    technician_id=None,
    site_id=None,
    access_type=None,
    face_result=None,
    face_distance=None,
    otp_result=None,
    access_result=None,
    reason_code=None,
    incident_level="NORMAL",
    description=None,
    evidence_image=None,
    gate_status="CLOSED",
    gate_opened_at=None,
    gate_closed_at=None,
):
    log = AccessLog(
        technician_id=technician_id,
        site_id=site_id,
        access_type=access_type,
        timestamp=datetime.utcnow(),

        face_result=face_result,
        face_distance=face_distance,

        otp_result=otp_result,
        access_result=access_result,

        reason_code=reason_code,

        incident_level=(
            incident_level
            if incident_level
            in VALID_INCIDENT_LEVELS
            else "NORMAL"
        ),

        device_id=get_request_device(),

        description=(
            description[:500]
            if description
            else None
        ),

        evidence_image=evidence_image,

        gate_status=gate_status,

        gate_opened_at=gate_opened_at,
        gate_closed_at=gate_closed_at,
    )

    db.session.add(log)
    db.session.commit()

    if str(log.access_result or '').upper() == 'DENIED':
        from app.sse_manager import broadcast
        sse_data = {
            'type': 'access_denied',
            'site_id': log.site_id,
            'site_name': log.site_name or 'Unknown',
            'technician_id': log.technician_id,
            'technician_name': log.technician_name or 'Unknown',
            'access_type': log.access_type,
            'reason_code': log.reason_code or '-',
            'incident_level': log.incident_level,
            'timestamp': log.timestamp.isoformat(),
            'message': f'Akses ditolak: {log.reason_code or "Unknown"}'
        }
        try:
            broadcast(sse_data)
        except Exception:
            pass

    try:
        from app.notifications import send_gate_access_notification
        send_gate_access_notification(log)
    except Exception:
        pass

    return log


# =========================================================
# SIMULATE DENIED ACCESS (TEST NOTIFICATIONS)
# =========================================================

@bp.route(
    '/simulate/denied',
    methods=['POST'],
)
@api_login_required
def simulate_denied():
    from app.models import BtsSite, Technician

    site = BtsSite.query.first()
    tech = Technician.query.first()

    log = save_access_log(
        technician_id=getattr(tech, 'technician_id', None) if tech else None,
        site_id=site.site_id if site else 'BTS-BOJONG',
        access_type='MASUK',
        face_result='FAILED',
        face_distance=0.95,
        access_result='DENIED',
        reason_code='FACE_MISMATCH',
        incident_level='HIGH',
        description='[TEST] Simulasi akses ditolak untuk verifikasi notifikasi.'
    )

    return jsonify({
        'status': 'denied',
        'log_id': log.id,
        'reason_code': log.reason_code,
        'incident_level': log.incident_level,
    })


# =========================================================
# TECHNICIAN SYNC
# =========================================================

@bp.route(
    "/technicians/sync",
    methods=["GET"],
)
@api_login_required
def technicians_sync():

    technicians = (
        Technician.query
        .filter_by(status="Active")
        .order_by(
            Technician.name.asc()
        )
        .all()
    )

    data = []

    for technician in technicians:

        data.append({
            "technician_id": (
                technician.technician_id
            ),
            "name": technician.name,
            "position": technician.position,
            "phone": technician.phone,
            "totp_configured": bool(
                technician.totp_secret
            ),
        })

    return api_success(
        technicians=data,
        data=data,
    )


# =========================================================
# VERIFY FACE
# =========================================================

@bp.route(
    "/verify-face",
    methods=["POST"],
)
@api_login_required
@csrf_protected
def verify_face():

    if not request.is_json:

        return api_error(
            "Request harus menggunakan JSON.",
            415,
            reason_code="CONTENT_TYPE_INVALID",
        )

    data = request.get_json(
        silent=True
    )

    if not isinstance(
        data,
        dict,
    ):

        return api_error(
            "Payload JSON tidak valid.",
            400,
            reason_code="JSON_INVALID",
        )

    image = (
        data.get("image")
        or ""
    )

    site_id = (
        data.get("site_id")
        or ""
    )

    access_type = (
        data.get("access_type")
        or ""
    )

    challenge_id = (
        data.get("challenge_id")
        or ""
    )

    challenge_result = (
        data.get("challenge_result")
        or ""
    )

    # =====================================================
    # VALIDASI SITE
    # =====================================================

    site, error = (
        validate_site_and_access(
            site_id,
            access_type,
        )
    )

    if error:

        message, reason_code = error

        create_activity_log(
            action="FACE_VERIFY_BLOCKED",
            description=message,
            incident_level="WARNING",
        )

        return api_error(
            message,
            400,
            reason_code=reason_code,
        )

    # =====================================================
    # VALIDASI EVIDENCE
    # =====================================================

    evidence_ok, evidence_error = (
        validate_evidence(image)
    )

    if not evidence_ok:

        return api_error(
            evidence_error,
            400,
            reason_code="EVIDENCE_INVALID",
        )

    if not image:

        save_access_log(
            site_id=site.site_id,
            access_type=access_type,
            face_result="FAILED",
            access_result="DENIED",
            reason_code="FACE_IMAGE_MISSING",
            incident_level="WARNING",
            description=(
                "Foto wajah tidak dikirim."
            ),
        )

        return api_error(
            "Foto wajah wajib dikirim.",
            400,
            reason_code="FACE_IMAGE_MISSING",
        )

    # =====================================================
    # LIVENESS
    # =====================================================

    if challenge_id or challenge_result:

        valid, liveness_error = (
            validate_liveness(
                challenge_id,
                challenge_result,
            )
        )

        if not valid:

            message, reason_code = (
                liveness_error
            )

            save_access_log(
                site_id=site.site_id,
                access_type=access_type,
                face_result="FAILED",
                access_result="DENIED",
                reason_code=reason_code,
                incident_level="HIGH",
                description=message,
                evidence_image=image,
            )

            return api_error(
                message,
                403,
                reason_code=reason_code,
            )

    # =====================================================
    # EXTRACT FACE
    # =====================================================

    try:

        encoding_result = (
            extract_face_encoding(
                image
            )
        )

    except Exception as exc:

        save_access_log(
            site_id=site.site_id,
            access_type=access_type,
            face_result="ERROR",
            access_result="DENIED",
            reason_code="FACE_PROCESSING_ERROR",
            incident_level="HIGH",
            description=(
                f"Face processing error: {exc}"
            ),
            evidence_image=image,
        )

        return api_error(
            "Gagal memproses wajah.",
            500,
            reason_code="FACE_PROCESSING_ERROR",
        )

    # Modul face recognition Anda dapat
    # mengembalikan encoding langsung.
    if (
        isinstance(
            encoding_result,
            tuple,
        )
        and len(encoding_result) == 2
    ):

        face_encoding, face_message = (
            encoding_result
        )

    else:

        face_encoding = encoding_result
        face_message = ""

    if face_encoding is None:

        save_access_log(
            site_id=site.site_id,
            access_type=access_type,
            face_result="FAILED",
            access_result="DENIED",
            reason_code="FACE_NOT_FOUND",
            incident_level="WARNING",
            description=(
                face_message
                or "Wajah tidak ditemukan."
            ),
            evidence_image=image,
        )

        return api_error(
            face_message
            or "Wajah tidak ditemukan.",
            400,
            reason_code="FACE_NOT_FOUND",
        )

    # =====================================================
    # ACTIVE TECHNICIANS
    # =====================================================

    technicians = (
        Technician.query
        .filter_by(status="Active")
        .all()
    )

    if not technicians:

        return api_error(
            "Belum ada teknisi aktif.",
            503,
            reason_code="NO_ACTIVE_TECHNICIAN",
        )

    # =====================================================
    # FACE MATCH
    # =====================================================

    best_technician = None
    best_distance = None
    best_matched = False

    for technician in technicians:

        if not technician.face_encoding:
            continue

        try:

            # compare_faces() pada face_recognition_module.py
            # mengembalikan: (matched, distance)
            matched, distance = compare_faces(
                technician.face_encoding,
                face_encoding,
            )

            if distance is None:
                continue

            distance = float(distance)

            if (
                best_distance is None
                or distance < best_distance
            ):

                best_distance = distance
                best_technician = technician
                best_matched = bool(matched)

        except Exception as exc:

            print(
                "[FACE COMPARE SKIP]",
                technician.technician_id,
                exc,
            )

            continue

    # =====================================================
    # NO MATCH
    # =====================================================

    if not best_technician or not best_matched:

        save_access_log(
            site_id=site.site_id,
            access_type=access_type,
            face_result="FAILED",
            face_distance=best_distance,
            access_result="DENIED",
            reason_code="FACE_UNKNOWN",
            incident_level="HIGH",
            description=(
                "Wajah tidak cocok dengan "
                "teknisi aktif."
            ),
            evidence_image=image,
        )

        create_activity_log(
            action="FACE_UNKNOWN",
            description=(
                f"Wajah tidak dikenali pada site "
                f"{site.site_id}."
            ),
            incident_level="HIGH",
        )

        return api_error(
            "Wajah tidak dikenali.",
            403,
            reason_code="FACE_UNKNOWN",
            face_distance=best_distance,
        )

    # =====================================================
    # ACCESS STATE
    # =====================================================

    valid_state, state_error = (
        validate_access_state(
            best_technician.technician_id,
            site.site_id,
            access_type,
        )
    )

    if not valid_state:

        message, reason_code = state_error

        save_access_log(
            technician_id=(
                best_technician.technician_id
            ),
            site_id=site.site_id,
            access_type=access_type,
            face_result="MATCHED",
            face_distance=best_distance,
            access_result="DENIED",
            reason_code=reason_code,
            incident_level="WARNING",
            description=message,
            evidence_image=image,
        )

        return api_error(
            message,
            409,
            reason_code=reason_code,
            technician={
                "technician_id": (
                    best_technician.technician_id
                ),
                "name": best_technician.name,
            },
        )

    # =====================================================
    # FACE SUCCESS -> PENDING OTP
    # =====================================================

    pending_log = save_access_log(
        technician_id=(
            best_technician.technician_id
        ),
        site_id=site.site_id,
        access_type=access_type,
        face_result="MATCHED",
        face_distance=best_distance,
        otp_result="PENDING",
        access_result="PENDING",
        reason_code="FACE_MATCHED",
        incident_level="NORMAL",
        description=(
            "Face recognition berhasil. "
            "Menunggu OTP."
        ),
        evidence_image=image,
    )

    return api_success(
        message=(
            "Wajah berhasil dikenali. "
            "Silakan masukkan OTP."
        ),
        tech_id=(
            best_technician.technician_id
        ),
        technician_name=(
            best_technician.name
        ),
        face_result="MATCHED",
        face_distance=best_distance,
        pending_log_id=pending_log.id,
        site_id=site.site_id,
        access_type=access_type,
        otp_required=True,
    )


# =========================================================
# VERIFY OTP
# =========================================================

@bp.route(
    "/verify-otp",
    methods=["POST"],
)
@api_login_required
@csrf_protected
def verify_otp():

    if not request.is_json:

        return api_error(
            "Request harus menggunakan JSON.",
            415,
        )

    data = request.get_json(
        silent=True
    )

    if not isinstance(
        data,
        dict,
    ):

        return api_error(
            "Payload JSON tidak valid.",
            400,
        )

    tech_id = (
        data.get("tech_id")
        or ""
    ).strip()

    otp = (
        str(
            data.get("otp")
            or ""
        )
        .strip()
    )

    site_id = (
        data.get("site_id")
        or ""
    ).strip()

    access_type = (
        data.get("access_type")
        or ""
    ).strip().upper()

    if not tech_id:

        return api_error(
            "Technician ID wajib dikirim.",
            400,
            reason_code="TECHNICIAN_REQUIRED",
        )

    if len(tech_id) > 20:

        return api_error(
            "Technician ID tidak valid.",
            400,
            reason_code="TECHNICIAN_INVALID",
        )

    if not (
        otp.isdigit()
        and len(otp) == 6
    ):

        return api_error(
            "OTP harus terdiri dari 6 digit.",
            400,
            reason_code="OTP_FORMAT_INVALID",
        )

    site, error = (
        validate_site_and_access(
            site_id,
            access_type,
        )
    )

    if error:

        message, reason_code = error

        return api_error(
            message,
            400,
            reason_code=reason_code,
        )

    technician = (
        Technician.query
        .filter_by(
            technician_id=tech_id,
            status="Active",
        )
        .first()
    )

    if not technician:

        return api_error(
            "Teknisi tidak ditemukan.",
            404,
            reason_code="TECHNICIAN_NOT_FOUND",
        )

    if not technician.totp_secret:

        return api_error(
            "TOTP teknisi belum dikonfigurasi.",
            400,
            reason_code="TOTP_NOT_CONFIGURED",
        )

    # =====================================================
    # OTP LOCKOUT
    # =====================================================

    if otp_locked(
        technician.technician_id
    ):

        create_activity_log(
            action="OTP_LOCKED",
            description=(
                f"OTP teknisi "
                f"{technician.technician_id} "
                "terkunci karena terlalu "
                "banyak percobaan gagal."
            ),
            incident_level="HIGH",
        )

        return api_error(
            "OTP terkunci. Silakan coba lagi "
            f"setelah {OTP_LOCKOUT_MINUTES} menit.",
            429,
            reason_code="OTP_LOCKED",
        )

    # =====================================================
    # FIND PENDING LOG
    # =====================================================

    pending_log = (
        AccessLog.query
        .filter(
            AccessLog.id
            == (
                data.get("pending_log_id")
                if data.get("pending_log_id")
                else -1
            ),

            AccessLog.technician_id
            == technician.technician_id,

            AccessLog.site_id
            == site.site_id,

            AccessLog.access_type
            == access_type,

            AccessLog.access_result
            == "PENDING",
        )
        .order_by(
            AccessLog.timestamp.desc()
        )
        .first()
    )

    # Fallback: cari pending terbaru
    if not pending_log:

        pending_log = (
            AccessLog.query
            .filter(
                AccessLog.technician_id
                == technician.technician_id,

                AccessLog.site_id
                == site.site_id,

                AccessLog.access_type
                == access_type,

                AccessLog.access_result
                == "PENDING",
            )
            .order_by(
                AccessLog.timestamp.desc()
            )
            .first()
        )

    if not pending_log:

        return api_error(
            "Tidak ditemukan proses face verification "
            "yang menunggu OTP.",
            409,
            reason_code="PENDING_ACCESS_NOT_FOUND",
        )

    # Jangan gunakan pending log yang terlalu lama.
    if (
        datetime.utcnow()
        - pending_log.timestamp
        > timedelta(minutes=2)
    ):

        pending_log.access_result = "DENIED"
        pending_log.otp_result = "EXPIRED"
        pending_log.reason_code = (
            "OTP_SESSION_EXPIRED"
        )
        pending_log.incident_level = "WARNING"

        db.session.commit()

        return api_error(
            "Sesi OTP telah kedaluwarsa. "
            "Silakan ulangi face verification.",
            410,
            reason_code="OTP_SESSION_EXPIRED",
        )

    # =====================================================
    # VERIFY OTP
    # =====================================================

    try:

        valid_otp = verify_totp(
            technician.totp_secret,
            otp,
        )

    except Exception as exc:

        print(
            "[OTP ERROR]",
            exc,
        )

        valid_otp = False

    if not valid_otp:

        pending_log.otp_result = "FAILED"
        pending_log.access_result = "DENIED"
        pending_log.reason_code = "OTP_INVALID"
        pending_log.incident_level = "WARNING"
        pending_log.description = (
            "OTP tidak valid."
        )

        db.session.commit()

        try:
            from app.notifications import send_gate_access_notification
            send_gate_access_notification(pending_log)
        except Exception:
            pass

        failures = get_recent_otp_failures(
            technician.technician_id
        )

        create_activity_log(
            action="OTP_FAILED",
            description=(
                f"OTP gagal untuk teknisi "
                f"{technician.name}. "
                f"Percobaan gagal: {failures}"
            ),
            incident_level=(
                "HIGH"
                if failures >= OTP_MAX_FAILURES
                else "WARNING"
            ),
        )

        if failures >= OTP_MAX_FAILURES:

            return api_error(
                "OTP salah 3 kali. "
                "Akses dikunci sementara.",
                429,
                reason_code="OTP_LOCKED",
                failures=failures,
            )

        return api_error(
            "OTP salah.",
            403,
            reason_code="OTP_INVALID",
            failures=failures,
            remaining_attempts=max(
                0,
                OTP_MAX_FAILURES - failures,
            ),
        )

    # =====================================================
    # ACCESS STATE RE-CHECK
    # =====================================================

    valid_state, state_error = (
        validate_access_state(
            technician.technician_id,
            site.site_id,
            access_type,
        )
    )

    if not valid_state:

        message, reason_code = state_error

        pending_log.otp_result = "VERIFIED"
        pending_log.access_result = "DENIED"
        pending_log.reason_code = reason_code
        pending_log.incident_level = "WARNING"
        pending_log.description = message

        db.session.commit()

        try:
            from app.notifications import send_gate_access_notification
            send_gate_access_notification(pending_log)
        except Exception:
            pass

        return api_error(
            message,
            409,
            reason_code=reason_code,
        )

    # =====================================================
    # GATE COOLDOWN
    # =====================================================

    if gate_is_in_cooldown(site.site_id):

        pending_log.otp_result = "VERIFIED"
        pending_log.access_result = "DENIED"
        pending_log.reason_code = (
            "GATE_COOLDOWN"
        )
        pending_log.incident_level = "WARNING"
        pending_log.description = (
            "Gate masih dalam cooldown."
        )

        db.session.commit()

        try:
            from app.notifications import send_gate_access_notification
            send_gate_access_notification(pending_log)
        except Exception:
            pass

        return api_error(
            "Gate masih dalam cooldown.",
            429,
            reason_code="GATE_COOLDOWN",
        )

    # =====================================================
    # WORK ORDER CHECK (TELCO GRADE)
    # =====================================================

    try:
        from flask import current_app
        if current_app.config.get("ENABLE_WORK_ORDER_CHECK", False):
            from app.models import WorkOrder
            today = datetime.utcnow().date()
            active_wo = WorkOrder.query.filter(
                WorkOrder.technician_id == technician.technician_id,
                WorkOrder.site_id == site.site_id,
                WorkOrder.scheduled_date == today,
                WorkOrder.status.in_(
                    ["APPROVED", "IN_PROGRESS", "COMPLETED", "VERIFIED"]
                )
            ).first()

            if not active_wo:
                pending_log.otp_result = "VERIFIED"
                pending_log.access_result = "DENIED"
                pending_log.reason_code = "NO_ACTIVE_WORK_ORDER"
                pending_log.incident_level = "WARNING"
                pending_log.description = (
                    f"Teknisi {technician.name} tidak memiliki Surat Perintah Kerja "
                    f"(Work Order) aktif untuk site {site.site_id} hari ini."
                )
                db.session.commit()

                try:
                    from app.notifications import send_gate_access_notification
                    send_gate_access_notification(pending_log)
                except Exception:
                    pass

                return api_error(
                    "Akses ditolak: Tidak ada Surat Perintah Kerja (Work Order) "
                    "aktif untuk Anda di site ini hari ini.",
                    403,
                    reason_code="NO_ACTIVE_WORK_ORDER",
                )
    except Exception as wo_err:
        print("[WORK ORDER CHECK ERROR]", wo_err)

    # =====================================================
    # OPEN GATE
    # =====================================================

    device_id = get_request_device()

    try:

        gate_opened = open_gate(
            device_id=device_id,
            site_id=site.site_id
        )

    except Exception as exc:

        print(
            "[GATE OPEN ERROR]",
            exc,
        )

        gate_opened = False

    if not gate_opened:

        pending_log.otp_result = "VERIFIED"
        pending_log.access_result = "DENIED"
        pending_log.reason_code = (
            "GATE_OPEN_ERROR"
        )
        pending_log.incident_level = "CRITICAL"
        pending_log.description = (
            "OTP valid tetapi gate gagal dibuka."
        )

        db.session.commit()

        try:
            from app.notifications import send_gate_access_notification
            send_gate_access_notification(pending_log)
        except Exception:
            pass

        create_activity_log(
            action="GATE_OPEN_FAILED",
            description=(
                f"Gate gagal dibuka untuk "
                f"teknisi {technician.name}."
            ),
            incident_level="CRITICAL",
        )

        return api_error(
            "Gate gagal dibuka.",
            503,
            reason_code="GATE_OPEN_ERROR",
        )

    # =====================================================
    # GRANTED
    # =====================================================

    opened_at = (
        get_gate_opened_at(site.site_id)
        or datetime.utcnow()
    )

    pending_log.otp_result = "VERIFIED"
    pending_log.access_result = "GRANTED"
    pending_log.reason_code = "ACCESS_GRANTED"
    pending_log.incident_level = "NORMAL"
    pending_log.description = (
        "Face recognition dan OTP valid. "
        "Gate dibuka."
    )
    pending_log.gate_status = "OPEN"
    pending_log.gate_opened_at = opened_at
    pending_log.gate_closed_at = None

    db.session.commit()

    try:
        from app.notifications import send_gate_access_notification
        send_gate_access_notification(pending_log)
    except Exception:
        pass

    create_activity_log(
        action="ACCESS_GRANTED",
        description=(
            f"Akses {access_type} diberikan "
            f"untuk teknisi {technician.name} "
            f"di site {site.site_id}."
        ),
        incident_level="NORMAL",
    )

    return api_success(
        message="Akses diterima. Gate dibuka.",
        tech_id=technician.technician_id,
        technician_name=technician.name,
        site_id=site.site_id,
        access_type=access_type,
        access_result="GRANTED",
        gate_status="OPEN",
        gate_open_seconds=GATE_OPEN_SECONDS,
        gate_auto_close_seconds=GATE_OPEN_SECONDS,
        remaining_seconds=GATE_OPEN_SECONDS,
        log_id=pending_log.id,
    )


# =========================================================
# GATE CLOSE
# =========================================================

@bp.route(
    "/gate-close",
    methods=["POST"],
)
@api_login_required
@csrf_protected
def gate_close():

    data = request.get_json(silent=True) if request.is_json else {}
    site_id = str((data or {}).get("site_id") or request.args.get("site_id") or "").strip()

    if not site_id:
        return api_error(
            "Site BTS wajib dipilih.",
            400,
            reason_code="SITE_REQUIRED",
        )

    site = BtsSite.query.filter_by(site_id=site_id).first()

    if not site:
        return api_error(
            "BTS Site tidak ditemukan.",
            404,
            reason_code="SITE_NOT_FOUND",
        )

    device_id = get_request_device()

    try:

        closed = close_gate(
            device_id=device_id,
            site_id=site.site_id
        )

    except Exception as exc:

        print(
            "[GATE CLOSE ERROR]",
            exc,
        )

        closed = False

    if not closed:

        create_activity_log(
            action="GATE_CLOSE_FAILED",
            description=(
                "Gate gagal ditutup."
            ),
            incident_level="CRITICAL",
        )

        return api_error(
            "Gate gagal ditutup.",
            503,
            reason_code="GATE_CLOSE_ERROR",
        )

    latest_open_log = (
        AccessLog.query
        .filter(
            AccessLog.gate_status
            == "OPEN",
            AccessLog.site_id == site.site_id,
        )
        .order_by(
            AccessLog.gate_opened_at.desc()
        )
        .first()
    )

    closed_at = datetime.utcnow()

    if latest_open_log:

        latest_open_log.gate_status = (
            "CLOSED"
        )

        latest_open_log.gate_closed_at = (
            closed_at
        )

        db.session.commit()

    create_activity_log(
        action="GATE_CLOSED",
        description=(
            f"Gate site {site.site_id} berhasil ditutup."
        ),
        incident_level="NORMAL",
    )

    return api_success(
        message=(
            f"Gate site {site.site_id} berhasil ditutup."
        ),
        site_id=site.site_id,
        gate_status="CLOSED",
        gate_closed_at=(
            closed_at.isoformat()
        ),
    )


# =========================================================
# GATE STATUS
# =========================================================

@bp.route(
    "/gate-status",
    methods=["GET"],
)
@api_login_required
def gate_status():

    site_id = (
        request.args.get("site_id")
        or ""
    ).strip()

    if not site_id:
        return api_error(
            "Site BTS wajib dipilih.",
            400,
            reason_code="SITE_REQUIRED",
        )

    site = BtsSite.query.filter_by(site_id=site_id).first()

    if not site:
        return api_error(
            "BTS Site tidak ditemukan.",
            404,
            reason_code="SITE_NOT_FOUND",
        )

    opened_at = get_gate_opened_at(site.site_id)

    elapsed = None
    remaining = None

    if opened_at:
        elapsed = (
            datetime.utcnow() - opened_at
        ).total_seconds()

        remaining = max(
            0,
            GATE_OPEN_SECONDS - elapsed,
        )

    health = check_gate_health(site.site_id)

    return api_success(
        site_id=site.site_id,
        site_name=site.site_name,
        gate_status=get_gate_status(site.site_id),
        gate_health=get_gate_health(site.site_id),
        healthy=health["healthy"],
        opened_at=(
            opened_at.isoformat()
            if opened_at
            else None
        ),
        closed_at=(
            get_gate_closed_at(site.site_id).isoformat()
            if get_gate_closed_at(site.site_id)
            else None
        ),
        remaining_seconds=round(
            remaining,
            1,
        )
        if remaining is not None
        else 0,
        last_error=get_gate_last_error(site.site_id),
        last_action=get_gate_last_action(site.site_id),
        device_id=get_gate_device_id(site.site_id),
    )

# =========================================================
# GATE HEALTH
# =========================================================

@bp.route(
    "/gate-health",
    methods=["GET"],
)
@api_login_required
def gate_health():

    site_id = (
        request.args.get("site_id")
        or ""
    ).strip()

    if not site_id:
        return api_error(
            "Site BTS wajib dipilih.",
            400,
            reason_code="SITE_REQUIRED",
        )

    site = BtsSite.query.filter_by(site_id=site_id).first()

    if not site:
        return api_error(
            "BTS Site tidak ditemukan.",
            404,
            reason_code="SITE_NOT_FOUND",
        )

    health = check_gate_health(site.site_id)

    return api_success(
        site_id=site.site_id,
        site_name=site.site_name,
        gate_status=get_gate_status(site.site_id),
        gate_health=get_gate_health(site.site_id),
        healthy=health["healthy"],
        message=health["message"],
        last_error=get_gate_last_error(site.site_id),
        last_action=get_gate_last_action(site.site_id),
        device_id=get_gate_device_id(site.site_id),
        gate_opened_at=(
            get_gate_opened_at(site.site_id).isoformat()
            if get_gate_opened_at(site.site_id)
            else None
        ),
        gate_closed_at=(
            get_gate_closed_at(site.site_id).isoformat()
            if get_gate_closed_at(site.site_id)
            else None
        ),
    )

# =========================================================
# MANUAL GATE OPEN - ADMIN ONLY
# =========================================================

@bp.route(
    "/gate/manual-open",
    methods=["POST"],
)
@api_admin_required
@csrf_protected
def manual_gate_open():

    data = request.get_json(silent=True) if request.is_json else {}
    site_id = str((data or {}).get("site_id") or request.args.get("site_id") or "").strip()

    if not site_id:
        return api_error("Site BTS wajib dipilih.", 400, reason_code="SITE_REQUIRED")

    site = BtsSite.query.filter_by(site_id=site_id).first()
    if not site:
        return api_error("BTS Site tidak ditemukan.", 404, reason_code="SITE_NOT_FOUND")

    device_id = get_request_device()

    try:
        opened = force_open_gate(
            device_id=device_id,
            site_id=site.site_id
        )
    except Exception as exc:
        opened = False

        print(
            "[MANUAL OPEN ERROR]",
            exc
        )

    if not opened:

        create_activity_log(
            action="MANUAL_GATE_OPEN_FAILED",
            description=(
                "Admin gagal membuka gate secara manual."
            ),
            incident_level="CRITICAL",
            device_id=device_id,
        )

        return api_error(
            "Gate gagal dibuka secara manual.",
            503,
            reason_code="MANUAL_GATE_OPEN_FAILED",
        )

    create_activity_log(
        action="MANUAL_GATE_OPEN",
        description=(
            "Admin membuka gate secara manual."
        ),
        incident_level="WARNING",
        device_id=device_id,
    )

    return api_success(
        message="Gate berhasil dibuka secara manual.",
        site_id=site.site_id,
        site_name=site.site_name,
        gate_status=get_gate_status(site.site_id),
        gate_health=get_gate_health(site.site_id),
        manual_override=True,
        device_id=device_id,
    )


# =========================================================
# MANUAL GATE CLOSE - ADMIN ONLY
# =========================================================

@bp.route(
    "/gate/manual-close",
    methods=["POST"],
)
@api_admin_required
@csrf_protected
def manual_gate_close():

    data = request.get_json(silent=True) if request.is_json else {}
    site_id = str((data or {}).get("site_id") or request.args.get("site_id") or "").strip()

    if not site_id:
        return api_error("Site BTS wajib dipilih.", 400, reason_code="SITE_REQUIRED")

    site = BtsSite.query.filter_by(site_id=site_id).first()
    if not site:
        return api_error("BTS Site tidak ditemukan.", 404, reason_code="SITE_NOT_FOUND")

    device_id = get_request_device()

    try:
        closed = force_close_gate(
            device_id=device_id,
            site_id=site.site_id
        )
    except Exception as exc:
        closed = False

        print(
            "[MANUAL CLOSE ERROR]",
            exc
        )

    if not closed:

        create_activity_log(
            action="MANUAL_GATE_CLOSE_FAILED",
            description=(
                "Admin gagal menutup gate secara manual."
            ),
            incident_level="CRITICAL",
            device_id=device_id,
        )

        return api_error(
            "Gate gagal ditutup secara manual.",
            503,
            reason_code="MANUAL_GATE_CLOSE_FAILED",
        )

    create_activity_log(
        action="MANUAL_GATE_CLOSE",
        description=(
            "Admin menutup gate secara manual."
        ),
        incident_level="WARNING",
        device_id=device_id,
    )

    return api_success(
        message="Gate berhasil ditutup secara manual.",
        site_id=site.site_id,
        site_name=site.site_name,
        gate_status=get_gate_status(site.site_id),
        gate_health=get_gate_health(site.site_id),
        manual_override=True,
        device_id=device_id,
    )


# =========================================================
# TECHNICIAN STATE
# =========================================================

@bp.route(
    "/technician-state/<string:tech_id>",
    methods=["GET"],
)
@api_login_required
def technician_state(tech_id):

    technician = (
        Technician.query
        .filter_by(
            technician_id=tech_id,
        )
        .first()
    )

    if not technician:

        return api_error(
            "Teknisi tidak ditemukan.",
            404,
            reason_code="TECHNICIAN_NOT_FOUND",
        )

    state = get_latest_access_state(
        technician.technician_id
    )

    return api_success(
        technician_id=(
            technician.technician_id
        ),
        technician_name=(
            technician.name
        ),
        state=(
            "DI DALAM"
            if state["inside"]
            else "DI LUAR"
        ),
        inside=state["inside"],
        site_id=state["site_id"],
        site_name=state["site_name"],
        access_type=state["access_type"],
    )


# =========================================================
# SECURITY STATUS
# =========================================================

@bp.route(
    "/security-status",
    methods=["GET"],
)
@api_login_required
def security_status():

    site_id = (request.args.get("site_id") or "").strip()

    today = datetime.utcnow().replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    logs = (
        AccessLog.query
        .filter(
            AccessLog.timestamp >= today,
            *( [AccessLog.site_id == site_id] if site_id else [] )
        )
        .all()
    )

    total = len(logs)

    granted = sum(
        1
        for log in logs
        if log.access_result == "GRANTED"
    )

    denied = sum(
        1
        for log in logs
        if log.access_result == "DENIED"
    )

    warning = sum(
        1
        for log in logs
        if log.incident_level
        == "WARNING"
    )

    high = sum(
        1
        for log in logs
        if log.incident_level
        == "HIGH"
    )

    critical = sum(
        1
        for log in logs
        if log.incident_level
        == "CRITICAL"
    )

    if critical > 0:
        level = "CRITICAL"

    elif high > 0:
        level = "HIGH"

    elif warning > 0:
        level = "WARNING"

    else:
        level = "NORMAL"

    payload = {
        "security_level": level,
        "total_access": total,
        "granted": granted,
        "denied": denied,
        "warning": warning,
        "high": high,
        "critical": critical,
        "gate_status": get_gate_status(site_id or None),
    }

    return api_success(
        data=payload,
        security_level=level,
        total_access=total,
        granted=granted,
        denied=denied,
        warning=warning,
        high=high,
        critical=critical,
        gate_status=get_gate_status(site_id or None),
        csrf_token=get_csrf_token(),
    )


# =========================================================
# AI ANALYTICS: FACE ACCURACY DISTRIBUTION
# =========================================================

@bp.route(
    "/ai/face-accuracy",
    methods=["GET"],
)
@api_login_required
def ai_face_accuracy():
    from app.ai_analytics import get_face_accuracy_distribution
    data = get_face_accuracy_distribution()
    return api_success(data=data)


# =========================================================
# AI ANALYTICS: ANOMALY DETECTION
# =========================================================

@bp.route(
    "/ai/anomalies",
    methods=["GET"],
)
@api_login_required
def ai_anomalies():
    from app.ai_analytics import detect_access_anomalies
    data = detect_access_anomalies()
    return api_success(data=data)


# =========================================================
# AI ANALYTICS: THREAT SCORES
# =========================================================

@bp.route(
    "/ai/threat-scores",
    methods=["GET"],
)
@api_login_required
def ai_threat_scores():
    from app.ai_analytics import predict_threat_scores
    sites = BtsSite.query.all()
    data = predict_threat_scores(sites)
    return api_success(scores=data)


# =========================================================
# AI ANALYTICS: HOURLY ACCESS PATTERN
# =========================================================

@bp.route(
    "/ai/hourly-pattern",
    methods=["GET"],
)
@api_login_required
def ai_hourly_pattern():
    from app.ai_analytics import get_hourly_access_pattern
    data = get_hourly_access_pattern()
    return api_success(pattern=data)


# =========================================================
# AI ANALYTICS: DENIAL REASONS
# =========================================================

@bp.route(
    "/ai/denial-reasons",
    methods=["GET"],
)
@api_login_required
def ai_denial_reasons():
    from app.ai_analytics import get_denial_reasons
    data = get_denial_reasons()
    return api_success(reasons=data)


# =========================================================
# AI ANALYTICS: TECHNICIAN RISK PROFILE
# =========================================================

@bp.route(
    "/ai/tech-risk",
    methods=["GET"],
)
@api_login_required
def ai_technician_risk():
    from app.ai_analytics import get_technician_risk_profile
    data = get_technician_risk_profile()
    return api_success(profiles=data)


# =========================================================
# AI ANALYTICS: INSIGHTS SUMMARY
# =========================================================

@bp.route(
    "/ai/insights",
    methods=["GET"],
)
@api_login_required
def ai_insights():
    from app.ai_analytics import generate_ai_insights
    data = generate_ai_insights()
    return api_success(insights=data)


# =========================================================
# AI ANALYTICS: HEATMAP DATA
# =========================================================

@bp.route(
    "/ai/heatmap",
    methods=["GET"],
)
@api_login_required
def ai_heatmap():
    from app.ai_analytics import get_site_heatmap_data
    data = get_site_heatmap_data()
    return api_success(sites=data)


# =========================================================
# FEATURES SUMMARY
# =========================================================

@bp.route(
    "/features/summary",
    methods=["GET"],
)
@api_login_required
def features_summary():
    from app.models import BtsSite, Technician, AccessLog, WorkOrder
    site_count = BtsSite.query.count()
    tech_count = Technician.query.count()
    access_count = AccessLog.query.count()
    work_order_count = WorkOrder.query.count()
    return api_success(
        sites=site_count,
        technicians=tech_count,
        access_logs=access_count,
        work_orders=work_order_count,
    )


# =========================================================
# FEATURES SITES
# =========================================================

@bp.route(
    "/features/sites",
    methods=["GET"],
)
@api_login_required
def features_sites():
    from app.models import BtsSite
    sites = BtsSite.query.all()
    result = []
    for site in sites:
        result.append({
            'site_id': site.site_id,
            'site_name': site.site_name,
            'location': site.location,
            'status': site.status or 'ACTIVE',
        })
    return api_success(sites=result)


# =========================================================
# GATE MANUAL CONTROL
# =========================================================

@bp.route(
    "/gate/manual-open",
    methods=["POST"],
)
@api_login_required
@api_admin_required
def gate_manual_open():
    from app.access_control import force_open_gate
    site_id = request.json.get('site_id') if request.json else None
    device_id = (request.json.get('device_id') if request.json else None) or get_request_device()
    description = (request.json.get('description') if request.json else None) or 'Manual gate open from dashboard'
    result = force_open_gate(site_id=site_id, device_id=device_id, description=description, username=current_user.username if current_user.is_authenticated else 'admin')
    if not result:
        return api_error('Gagal membuka gerbang.', 500)
    return api_success(message='Gerbang berhasil dibuka.', gate_status=result)


@bp.route(
    "/gate/manual-close",
    methods=["POST"],
)
@api_login_required
@api_admin_required
def gate_manual_close():
    from app.access_control import force_close_gate
    site_id = request.json.get('site_id') if request.json else None
    device_id = (request.json.get('device_id') if request.json else None) or get_request_device()
    description = (request.json.get('description') if request.json else None) or 'Manual gate close from dashboard'
    result = force_close_gate(site_id=site_id, device_id=device_id, description=description, username=current_user.username if current_user.is_authenticated else 'admin')
    if not result:
        return api_error('Gagal menutup gerbang.', 500)
    return api_success(message='Gerbang berhasil ditutup.', gate_status=result)
