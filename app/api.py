from datetime import datetime, timedelta
from functools import wraps
import secrets
import time
import os
import uuid
import base64
import math

from flask import (
    Blueprint,
    request,
    jsonify,
    session,
    current_app,
)

from flask_login import current_user

from app import db
from app.models import (
    Admin,
    Technician,
    BtsSite,
    AccessLog,
    ActivityLog,
)

from app.face_recognition_module import (
    extract_face_encoding,
    compare_faces,
    analyze_liveness,
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
    record_kiosk_heartbeat,
    get_kiosk_status,
    update_door_sensor,
    get_door_sensor_status,
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
RATE_LIMIT_MAX_REQUESTS = 120
SENSITIVE_RATE_LIMIT_MAX = 20

SENSITIVE_ENDPOINTS = (
    "/verify-face",
    "/verify-otp",
    "/verify-totp",
    "/trigger-duress",
    "/verify-token",
    "/gate-force-open",
    "/kiosk/verify-pin",
)

_rate_limit_store = {}

# Challenge liveness
_liveness_sessions = {}


# =========================================================
# GPS GEOFENCING HELPER
# =========================================================

def calculate_haversine_distance(lat1, lon1, lat2, lon2):
    """
    Menghitung jarak dalam satuan meter antara dua titik koordinat
    (latitude, longitude) menggunakan rumus Haversine.
    """
    try:
        lat1 = float(str(lat1).strip())
        lon1 = float(str(lon1).strip())
        lat2 = float(str(lat2).strip())
        lon2 = float(str(lon2).strip())
    except (TypeError, ValueError):
        return None

    # Radius bumi dalam meter
    R = 6371000.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2)
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))

    return R * c


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

    return jsonify(result), 200


# =========================================================
# CLIENT INFO
# =========================================================

def get_request_device():
    client_ip = get_client_ip()

    user_agent = (
        request.headers.get("User-Agent")
        or "UNKNOWN"
    )

    return (
        f"{client_ip} | {user_agent[:450]}"
    )[:500]


def get_client_ip():
    trust_proxy = current_app.config.get("TRUST_PROXY", False)
    if trust_proxy:
        forwarded = (
            request.headers.get("X-Forwarded-For")
            or ""
        ).strip()

        if forwarded:
            ip = forwarded.split(",")[0].strip()
            if all(c in "0123456789abcdefABCDEF:." for c in ip) and len(ip) <= 45:
                return ip

    return (
        request.remote_addr
        or "127.0.0.1"
    )


# =========================================================
# RATE LIMIT
# =========================================================

def check_rate_limit():
    """
    Rate limiting sederhana berbasis IP + endpoint.
    Polling status gerbang dan status keamanan dikecualikan agar UI tidak terkunci.
    """
    path = request.path or ""
    if path.endswith("/gate-status") or path.endswith("/security-status") or path.endswith("/stream-status"):
        return True

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

    max_requests = (
        SENSITIVE_RATE_LIMIT_MAX
        if any(path.endswith(ep) for ep in SENSITIVE_ENDPOINTS)
        else RATE_LIMIT_MAX_REQUESTS
    )

    if len(history) >= max_requests:

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
    - Kiosk Terminal fisik gerbang
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

    if session.get("is_kiosk") or session.get("kiosk_site_id"):
        return True

    if session.get("auth_role") in ("ADMIN", "TECHNICIAN"):
        return True

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

    if image_data.startswith("/static/"):
        return True, None

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


def save_evidence_image_file(image_data, prefix="evidence"):
    """
    Menyimpan snapshot gambar Base64 ke disk (app/static/uploads/evidence/)
    dan mengembalikan path URL statis agar database tidak mengalami bloat.
    """
    if not image_data or not isinstance(image_data, str):
        return None

    if image_data.startswith("/static/") or image_data.startswith("http"):
        return image_data

    # Ekstrak string Base64 murni
    if ";base64," in image_data:
        _, encoded = image_data.split(";base64,", 1)
    elif image_data.startswith("data:image"):
        _, encoded = image_data.split(",", 1)
    else:
        encoded = image_data

    encoded = encoded.strip()
    if not encoded:
        return None

    try:
        raw_bytes = base64.b64decode(encoded)
        upload_dir = os.path.join(current_app.root_path, "static", "uploads", "evidence")
        os.makedirs(upload_dir, exist_ok=True)
        filename = f"{prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.jpg"
        filepath = os.path.join(upload_dir, filename)
        with open(filepath, "wb") as f:
            f.write(raw_bytes)
        return f"/static/uploads/evidence/{filename}"
    except Exception as exc:
        print("[EVIDENCE SAVE ERROR]", exc)
        return image_data


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
    device_id=None,
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

        device_id=device_id or get_request_device(),

        description=(
            description[:500]
            if description
            else None
        ),

        evidence_image=save_evidence_image_file(
            evidence_image,
            prefix="access",
        ) if evidence_image and not str(evidence_image).startswith("/static/") else evidence_image,

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
    # GPS GEOFENCING VALIDATION
    # =====================================================

    geofence_enabled = current_app.config.get("GEOFENCE_ENABLED", True)
    geofence_radius = current_app.config.get("GEOFENCE_RADIUS_METERS", 150)
    user_lat = data.get("latitude")
    user_lon = data.get("longitude")
    geofence_distance = None

    if geofence_enabled and user_lat is not None and user_lon is not None and str(user_lat).strip() != "" and str(user_lon).strip() != "":
        geofence_distance = calculate_haversine_distance(user_lat, user_lon, site.latitude, site.longitude)
        if geofence_distance is not None and geofence_distance > geofence_radius:
            dist_str = f"{geofence_distance:.0f} meter" if geofence_distance < 1000 else f"{(geofence_distance / 1000.0):.2f} km"
            reason_code = "OUT_OF_GEOFENCE"
            desc_text = (
                f"Akses ditolak di luar radius BTS: {dist_str} dari tower "
                f"(izin radius: {geofence_radius}m). Koordinat: {user_lat}, {user_lon}"
            )
            save_access_log(
                site_id=site.site_id,
                access_type=access_type,
                face_result="FAILED",
                access_result="DENIED",
                reason_code=reason_code,
                incident_level="HIGH",
                description=desc_text,
                evidence_image=image if image else None,
            )
            create_activity_log(
                action="GEOFENCE_VIOLATION",
                description=f"Pelanggaran Geofence pada site {site.site_id}: {desc_text}",
                incident_level="HIGH",
            )
            try:
                from app.notifications import send_telegram_alert
                send_telegram_alert(
                    title="📍 PELANGGARAN GEOFENCE BTS!",
                    message=(
                        f"Permintaan akses ditolak karena teknisi di luar area BTS!\n"
                        f"Site: {site.site_name} ({site.site_id})\n"
                        f"Jarak: {dist_str} (Maks {geofence_radius}m)\n"
                        f"Koordinat: {user_lat}, {user_lon}"
                    ),
                    incident_level="HIGH",
                    evidence_base64=image,
                    site_id=site.site_id,
                )
            except Exception:
                pass

            return api_error(
                f"Akses ditolak: Anda terdeteksi berada di luar area BTS ({dist_str} dari tower, batas izin {geofence_radius}m).",
                403,
                reason_code=reason_code,
                distance_meters=round(geofence_distance, 1),
                max_radius=geofence_radius,
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
    # ANTI-SPOOFING & LIVENESS DETECTION
    # =====================================================

    liveness_enabled = current_app.config.get(
        "LIVENESS_DETECTION_ENABLED",
        True,
    )

    liveness_score = 1.0
    liveness_details = {}

    if liveness_enabled:
        is_live, liveness_score, liveness_details, liveness_msg = analyze_liveness(image)

        if not is_live:
            reason_code = "SPOOF_ATTACK_DETECTED"
            save_access_log(
                site_id=site.site_id,
                access_type=access_type,
                face_result="FAILED",
                access_result="DENIED",
                reason_code=reason_code,
                incident_level="HIGH",
                description=f"Anti-Spoofing Alert: {liveness_msg} (Skor: {liveness_score})",
                evidence_image=image,
            )

            create_activity_log(
                action="SPOOF_ATTACK_DETECTED",
                description=f"Percobaan pemalsuan foto/layar terdeteksi pada site {site.site_id}: {liveness_msg}",
                incident_level="HIGH",
            )

            try:
                from app.notifications import send_telegram_alert
                send_telegram_alert(
                    title="🚨 PERINGATAN ANTI-SPOOFING!",
                    message=f"Percobaan buka gerbang menggunakan foto/layar HP tidak sah terdeteksi!\n"
                            f"Detail: {liveness_msg}\nSkor Liveness: {liveness_score}",
                    incident_level="HIGH",
                    evidence_base64=image,
                    site_id=site.site_id,
                )
            except Exception:
                pass

            return api_error(
                f"Percobaan pemalsuan foto terdeteksi (Anti-Spoofing Alert): {liveness_msg}",
                403,
                reason_code=reason_code,
                liveness_score=liveness_score,
                liveness_details=liveness_details,
            )

    # Validasi challenge interaktif jika ada
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
    # GATE COOLDOWN
    # =====================================================

    if gate_is_in_cooldown(site.site_id):

        save_access_log(
            technician_id=best_technician.technician_id,
            site_id=site.site_id,
            access_type=access_type,
            face_result="MATCHED",
            face_distance=best_distance,
            otp_result="SKIPPED",
            access_result="DENIED",
            reason_code="GATE_COOLDOWN",
            incident_level="WARNING",
            description="Gate masih dalam cooldown.",
            evidence_image=image,
        )

        return api_error(
            "Gate masih dalam cooldown.",
            429,
            reason_code="GATE_COOLDOWN",
        )

    # =====================================================
    # WORK ORDER CHECK (TELCO GRADE)
    # =====================================================

    try:
        if current_app.config.get("ENABLE_WORK_ORDER_CHECK", False) and access_type != "KELUAR":
            from app.models import WorkOrder
            from sqlalchemy import or_
            today = datetime.utcnow().date()
            active_wo = WorkOrder.query.filter(
                WorkOrder.technician_id == best_technician.technician_id,
                WorkOrder.site_id == site.site_id,
                WorkOrder.status.in_(["APPROVED", "IN_PROGRESS"])
            ).filter(
                or_(
                    WorkOrder.scheduled_date == today,
                    WorkOrder.status == "IN_PROGRESS"
                )
            ).first()

            if not active_wo:
                save_access_log(
                    technician_id=best_technician.technician_id,
                    site_id=site.site_id,
                    access_type=access_type,
                    face_result="MATCHED",
                    face_distance=best_distance,
                    otp_result="SKIPPED",
                    access_result="DENIED",
                    reason_code="NO_ACTIVE_WORK_ORDER",
                    incident_level="WARNING",
                    description=(
                        f"Teknisi {best_technician.name} tidak memiliki Surat Perintah Kerja "
                        f"(Work Order) aktif/disetujui untuk site {site.site_id} hari ini."
                    ),
                    evidence_image=image,
                )
                return api_error(
                    "Akses Masuk Ditolak: Anda tidak memiliki Surat Perintah Kerja (Work Order) "
                    "aktif berstatus APPROVED / IN_PROGRESS untuk site ini hari ini.",
                    403,
                    reason_code="NO_ACTIVE_WORK_ORDER",
                )
            elif active_wo.status == "APPROVED":
                active_wo.status = "IN_PROGRESS"
                active_wo.updated_at = datetime.utcnow()
                db.session.commit()
    except Exception as wo_err:
        print("[WORK ORDER CHECK ERROR]", wo_err)

    # =====================================================
    # OPEN GATE VIA FACE RECOGNITION (PRIMARY METHOD)
    # =====================================================

    device_id = get_request_device()

    try:
        gate_opened = open_gate(
            device_id=device_id,
            site_id=site.site_id
        )
    except Exception as exc:
        print("[GATE OPEN ERROR]", exc)
        gate_opened = False

    if not gate_opened:
        save_access_log(
            technician_id=best_technician.technician_id,
            site_id=site.site_id,
            access_type=access_type,
            face_result="MATCHED",
            face_distance=best_distance,
            otp_result="SKIPPED",
            access_result="DENIED",
            reason_code="GATE_OPEN_ERROR",
            incident_level="CRITICAL",
            description="Verifikasi wajah cocok tetapi gate gagal dibuka.",
            evidence_image=image,
        )
        return api_error(
            "Gate gagal dibuka.",
            503,
            reason_code="GATE_OPEN_ERROR",
        )

    opened_at = get_gate_opened_at(site.site_id) or datetime.utcnow()

    access_log = save_access_log(
        technician_id=best_technician.technician_id,
        site_id=site.site_id,
        access_type=access_type,
        face_result="MATCHED",
        face_distance=best_distance,
        otp_result="SKIPPED",
        access_result="GRANTED",
        reason_code="ACCESS_GRANTED",
        incident_level="NORMAL",
        description="Verifikasi wajah berhasil. Gate dibuka langsung.",
        evidence_image=image,
    )
    access_log.gate_status = "OPEN"
    access_log.gate_opened_at = opened_at
    access_log.gate_closed_at = None

    db.session.commit()

    try:
        from app.notifications import send_gate_access_notification
        send_gate_access_notification(access_log)
    except Exception:
        pass

    create_activity_log(
        action="ACCESS_GRANTED",
        description=(
            f"Akses diberikan via verifikasi wajah ke site {site.site_id} "
            f"untuk {best_technician.name} ({access_type})."
        ),
        incident_level="NORMAL",
    )

    return api_success(
        message="Verifikasi wajah berhasil. Gerbang dibuka!",
        access_granted=True,
        gate_opened=True,
        tech_id=best_technician.technician_id,
        technician_name=best_technician.name,
        face_result="MATCHED",
        face_distance=best_distance,
        access_log_id=access_log.id,
        site_id=site.site_id,
        access_type=access_type,
        gate_open_duration=GATE_OPEN_SECONDS,
        gate_open_seconds=GATE_OPEN_SECONDS,
        is_live=True,
        liveness_score=liveness_score,
        liveness_details=liveness_details,
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

    # =====================================================
    # GPS GEOFENCING VALIDATION (OTP)
    # =====================================================

    geofence_enabled = current_app.config.get("GEOFENCE_ENABLED", True)
    geofence_radius = current_app.config.get("GEOFENCE_RADIUS_METERS", 150)
    user_lat = data.get("latitude")
    user_lon = data.get("longitude")

    if geofence_enabled and user_lat is not None and user_lon is not None and str(user_lat).strip() != "" and str(user_lon).strip() != "":
        geofence_distance = calculate_haversine_distance(user_lat, user_lon, site.latitude, site.longitude)
        if geofence_distance is not None and geofence_distance > geofence_radius:
            dist_str = f"{geofence_distance:.0f} meter" if geofence_distance < 1000 else f"{(geofence_distance / 1000.0):.2f} km"
            reason_code = "OUT_OF_GEOFENCE"
            desc_text = (
                f"Akses OTP ditolak di luar radius BTS: {dist_str} dari tower "
                f"(izin radius: {geofence_radius}m). Koordinat: {user_lat}, {user_lon}"
            )
            save_access_log(
                technician_id=tech_id,
                site_id=site.site_id,
                access_type=access_type,
                otp_result="FAILED",
                access_result="DENIED",
                reason_code=reason_code,
                incident_level="HIGH",
                description=desc_text,
            )
            create_activity_log(
                action="GEOFENCE_VIOLATION_OTP",
                description=f"Pelanggaran Geofence OTP pada site {site.site_id}: {desc_text}",
                incident_level="HIGH",
            )
            return api_error(
                f"Akses ditolak: Anda terdeteksi berada di luar area BTS ({dist_str} dari tower, batas izin {geofence_radius}m).",
                403,
                reason_code=reason_code,
                distance_meters=round(geofence_distance, 1),
                max_radius=geofence_radius,
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

    # =====================================================
    # SILENT DURESS PIN (EMERGENCY HOSTAGE / COERCION ALARM)
    # =====================================================
    duress_pin = str(current_app.config.get("DURESS_PIN", "999999")).strip()
    evidence_image = data.get("image") or None

    if duress_pin and otp == duress_pin:
        device_id = get_request_device()
        try:
            gate_opened = open_gate(device_id=device_id, site_id=site.site_id)
        except Exception as exc:
            print("[DURESS GATE OPEN ERROR]", exc)
            gate_opened = True

        opened_at = get_gate_opened_at(site.site_id) or datetime.utcnow()

        access_log = save_access_log(
            technician_id=technician.technician_id,
            site_id=site.site_id,
            access_type=access_type,
            face_result="SKIPPED",
            otp_result="DURESS_TRIGGERED",
            access_result="GRANTED",
            reason_code="SILENT_DURESS_ALARM",
            incident_level="CRITICAL",
            description=(
                f"🚨 PERINGATAN DARURAT: Silent Duress PIN digunakan pada Keypad Kiosk "
                f"oleh teknisi {technician.name} ({technician.technician_id}) di site {site.site_id}! "
                f"Teknisi kemungkinan berada di bawah ancaman kekerasan atau disandera."
            ),
            evidence_image=evidence_image,
            device_id=device_id,
        )
        if gate_opened and access_log:
            access_log.gate_status = "OPEN"
            access_log.gate_opened_at = opened_at
            access_log.gate_closed_at = None
        db.session.commit()

        create_activity_log(
            action="SILENT_DURESS_ALARM",
            description=(
                f"🚨 SILENT DURESS ALARM: Kode darurat digunakan pada site {site.site_id} "
                f"oleh {technician.name} ({technician.technician_id})."
            ),
            incident_level="CRITICAL",
            device_id=device_id,
        )

        try:
            from app.notifications import send_telegram_alert
            send_telegram_alert(
                title="🚨 SILENT DURESS ALARM (DARURAT)!",
                message=(
                    f"⚠️ <b>KODE PIN DURESS DIGUNAKAN DI SITE BTS!</b>\n\n"
                    f"<b>Site:</b> {site.site_name} (<code>{site.site_id}</code>)\n"
                    f"<b>Teknisi:</b> {technician.name} (<code>{technician.technician_id}</code>)\n"
                    f"<b>Tipe Akses:</b> {access_type}\n"
                    f"<b>Status Gerbang:</b> Terbuka (Tampilan normal pada kiosk)\n\n"
                    f"🚨 <b>INDIKASI BAHAYA:</b> Teknisi diduga disandera atau dipaksa membuka pintu oleh pihak tidak berwenang!\n"
                    f"Segera hubungi tim patroli keamanan atau pihak berwajib ke lokasi!"
                ),
                incident_level="CRITICAL",
                evidence_base64=evidence_image,
                site_id=site.site_id,
                header_badge="🚨 [SILENT DURESS ALARM]",
            )
        except Exception as alert_err:
            print("[DURESS TELEGRAM ALERT ERROR]", alert_err)

        return api_success(
            message="Kode OTP terverifikasi. Gerbang dibuka!",
            access_granted=True,
            gate_opened=True,
            tech_id=technician.technician_id,
            technician_name=technician.name,
            site_id=site.site_id,
            access_type=access_type,
            gate_open_duration=GATE_OPEN_SECONDS,
            gate_open_seconds=GATE_OPEN_SECONDS,
            is_duress=True,
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

    if not pending_log or (
        datetime.utcnow() - pending_log.timestamp > timedelta(minutes=5)
    ):
        # Standalone / Fallback OTP Verification (teknisi memilih akses via OTP tanpa / setelah gagal scan wajah)
        pending_log = save_access_log(
            technician_id=technician.technician_id,
            site_id=site.site_id,
            access_type=access_type,
            face_result="SKIPPED",
            otp_result="PENDING",
            access_result="PENDING",
            reason_code="OTP_FALLBACK_STARTED",
            incident_level="NORMAL",
            description="Verifikasi akses via kode OTP alternatif/cadangan dimulai.",
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
        if current_app.config.get("ENABLE_WORK_ORDER_CHECK", False) and access_type != "KELUAR":
            from app.models import WorkOrder
            from sqlalchemy import or_
            today = datetime.utcnow().date()
            active_wo = WorkOrder.query.filter(
                WorkOrder.technician_id == technician.technician_id,
                WorkOrder.site_id == site.site_id,
                WorkOrder.status.in_(["APPROVED", "IN_PROGRESS"])
            ).filter(
                or_(
                    WorkOrder.scheduled_date == today,
                    WorkOrder.status == "IN_PROGRESS"
                )
            ).first()

            if not active_wo:
                pending_log.otp_result = "VERIFIED"
                pending_log.access_result = "DENIED"
                pending_log.reason_code = "NO_ACTIVE_WORK_ORDER"
                pending_log.incident_level = "WARNING"
                pending_log.description = (
                    f"Teknisi {technician.name} tidak memiliki Surat Perintah Kerja "
                    f"(Work Order) aktif/disetujui untuk site {site.site_id} hari ini."
                )
                db.session.commit()

                try:
                    from app.notifications import send_gate_access_notification
                    send_gate_access_notification(pending_log)
                except Exception:
                    pass

                return api_error(
                    "Akses Masuk Ditolak: Anda tidak memiliki Surat Perintah Kerja (Work Order) "
                    "aktif berstatus APPROVED / IN_PROGRESS untuk site ini hari ini.",
                    403,
                    reason_code="NO_ACTIVE_WORK_ORDER",
                )
            elif active_wo.status == "APPROVED":
                # Otomatis update status WO ke IN_PROGRESS saat teknisi berhasil masuk site
                active_wo.status = "IN_PROGRESS"
                active_wo.updated_at = datetime.utcnow()
                db.session.commit()
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
    pending_log.reason_code = "ACCESS_GRANTED_OTP"
    pending_log.incident_level = "NORMAL"
    pending_log.description = (
        "Kode OTP valid (cadangan). Gate dibuka."
        if pending_log.face_result in ("SKIPPED", None, "FAILED")
        else "Face recognition dan OTP valid. Gate dibuka."
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
            f"Akses {access_type} diberikan via OTP "
            f"untuk teknisi {technician.name} "
            f"di site {site.site_id}."
        ),
        incident_level="NORMAL",
    )

    return api_success(
        message="Kode OTP valid. Gate dibuka!",
        access_granted=True,
        gate_opened=True,
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
def features_sites():
    from app.models import BtsSite
    try:
        from app.feature_routes import calculate_site_risk
    except Exception:
        calculate_site_risk = None

    sites = BtsSite.query.order_by(BtsSite.site_name.asc()).all()
    result = []
    for site in sites:
        risk = calculate_site_risk(site.site_id) if calculate_site_risk else {"level": "NORMAL", "score": 0}
        result.append({
            'site_id': site.site_id,
            'site_name': site.site_name,
            'location': site.location,
            'latitude': site.latitude,
            'longitude': site.longitude,
            'status': site.status or 'Normal (Up)',
            'temperature': getattr(site, 'temperature', None) or '-',
            'power_output': getattr(site, 'power_output', None) or '-',
            'risk': risk,
        })
    return api_success(sites=result)


# =========================================================
# KIOSK HEARTBEAT & STATUS
# =========================================================

@bp.route(
    "/kiosk/heartbeat",
    methods=["POST"],
)
def kiosk_heartbeat():
    """
    Heartbeat ping dari terminal fisik gerbang BTS.
    Dapat dipanggil oleh terminal perangkat tanpa login admin.
    """
    data = request.get_json(silent=True) or {}
    site_id = str(data.get("site_id") or "").strip()
    device_id = str(data.get("device_id") or "KIOSK_TERMINAL").strip()

    if not site_id:
        return api_error("Site ID wajib dikirim.", 400, reason_code="SITE_REQUIRED")

    record_kiosk_heartbeat(site_id=site_id, device_id=device_id)
    kiosk_info = get_kiosk_status(site_id=site_id)

    return api_success(
        message="Heartbeat diterima.",
        kiosk=kiosk_info,
        gate_status=get_gate_status(site_id=site_id),
    )


@bp.route(
    "/kiosk/status",
    methods=["GET"],
)
def kiosk_status_endpoint():
    """
    Mengambil status liveness terminal kiosk untuk Site BTS tertentu.
    """
    site_id = request.args.get("site_id", "").strip()
    if not site_id:
        return api_error("Parameter site_id wajib diisi.", 400, reason_code="SITE_REQUIRED")

    kiosk_info = get_kiosk_status(site_id=site_id)
    return api_success(kiosk=kiosk_info)


@bp.route(
    "/kiosk/verify-pin",
    methods=["POST"],
)
def verify_kiosk_admin_pin():
    """
    Verifikasi otorisasi konfigurasi perangkat Kiosk di sisi server.
    Menerima PIN atau password Admin.
    """
    data = request.get_json(silent=True) or {}
    pin = str(data.get("pin") or "").strip()

    if not pin:
        return api_error("PIN / Password Admin wajib diisi.", 400, reason_code="PIN_REQUIRED")

    # 1. Cek terhadap konfigurasi KIOSK_ADMIN_PIN jika diset
    configured_pin = current_app.config.get("KIOSK_ADMIN_PIN", "").strip()
    if configured_pin and pin == configured_pin:
        return api_success(message="Otorisasi PIN Kiosk berhasil.")

    # 2. Cek terhadap password Admin di database
    admin = Admin.query.first()
    if admin and admin.check_password(pin):
        return api_success(message="Otorisasi Admin berhasil.")

    # 3. Fallback jika belum diubah dan PIN khusus belum dikonfigurasi
    if not configured_pin and pin in ("admin", "123456"):
        # Hanya izinkan jika password admin default belum diganti atau tidak ada admin
        if admin is None or admin.check_password("admin123") or admin.check_password("admin"):
            return api_success(message="Otorisasi default berhasil.")

    return api_error("PIN atau Password Admin tidak valid.", 401, reason_code="INVALID_PIN")


# =========================================================
# DOOR SENSOR & FORCED ENTRY ALARM
# =========================================================

@bp.route(
    "/gate/sensor-event",
    methods=["POST"],
)
def gate_sensor_event():
    """
    Menerima event dari sensor magnetik pintu gerbang BTS
    (misal: DOOR_CLOSED, DOOR_OPENED, DOOR_FORCED, DOOR_AJAR).
    """
    data = request.get_json(silent=True) or {}
    site_id = str(data.get("site_id") or "").strip()
    event = str(data.get("event") or "").strip().upper()
    device_id = str(data.get("device_id") or "DOOR_SENSOR").strip()
    image = data.get("image") or None

    if not site_id:
        return api_error("Site ID wajib dikirim.", 400, reason_code="SITE_REQUIRED")

    valid_events = ("DOOR_CLOSED", "DOOR_OPENED", "DOOR_FORCED", "DOOR_AJAR")
    if event not in valid_events:
        return api_error(
            f"Event sensor tidak valid. Pilihan: {', '.join(valid_events)}",
            400,
            reason_code="INVALID_SENSOR_EVENT",
        )

    update_door_sensor(site_id=site_id, event=event, device_id=device_id)

    # Tangani insiden pembobolan gerbang
    if event == "DOOR_FORCED":
        try:
            from app.models import AccessLog
            access_log = save_access_log(
                technician_id=None,
                site_id=site_id,
                access_type="MASUK",
                face_result="NOT_CHECKED",
                otp_result="NOT_CHECKED",
                access_result="DENIED",
                reason_code="DOOR_FORCED_BREACH",
                incident_level="CRITICAL",
                description="ALARM! Sensor magnet mendeteksi gerbang dibuka paksa (pembobolan)!",
                device_id=device_id,
                evidence_image=image,
            )
            create_activity_log(
                action="SECURITY_ALARM",
                description=f"ALARM! Sensor mendeteksi gerbang pada site {site_id} dibuka paksa!",
                incident_level="CRITICAL",
                device_id=device_id,
            )
        except Exception as exc:
            print("[SENSOR FORCED LOG ERROR]", exc)

        try:
            from app.notifications import send_telegram_alert
            send_telegram_alert(
                title="🚨 ALARM PEMBOBOLAN GERBANG BTS!",
                message=(
                    f"⚠️ <b>PERINGATAN KEAMANAN TINGGI</b>\n\n"
                    f"Sensor magnetik mendeteksi gerbang pada site <code>{site_id}</code> "
                    f"<b>DIBUKA PAKSA (FORCED BREACH)</b> tanpa otentikasi resmi!\n\n"
                    f"Periksa lokasi atau kamera CCTV sekarang juga!"
                ),
                incident_level="CRITICAL",
                evidence_base64=image,
                site_id=site_id,
                header_badge="🚨 [ALARM GERBANG DIBOBOL]",
            )
        except Exception as exc:
            print("[TELEGRAM SENSOR ALARM ERROR]", exc)

        return api_success(
            alarm=True,
            event=event,
            site_id=site_id,
            sensor=get_door_sensor_status(site_id),
            message="ALARM DIAKTIFKAN: Pembobolan pintu terdeteksi!",
        )

    elif event == "DOOR_AJAR":
        create_activity_log(
            action="DOOR_AJAR_WARNING",
            description=f"Peringatan: Gerbang pada site {site_id} dibiarkan terbuka terlalu lama.",
            incident_level="WARNING",
            device_id=device_id,
        )

    return api_success(
        alarm=False,
        event=event,
        site_id=site_id,
        sensor=get_door_sensor_status(site_id),
        message=f"Event sensor {event} berhasil diproses.",
    )


@bp.route(
    "/gate/sensor-status",
    methods=["GET"],
)
def gate_sensor_status():
    """
    Mengambil status sensor kontak pintu saat ini.
    """
    site_id = request.args.get("site_id", "").strip()
    if not site_id:
        return api_error("Parameter site_id wajib diisi.", 400, reason_code="SITE_REQUIRED")

    return api_success(
        sensor=get_door_sensor_status(site_id),
        kiosk=get_kiosk_status(site_id),
    )


# =========================================================
# VERIFY QR CODE ACCESS (DIGITAL PASS / WORK ORDER TICKET)
# =========================================================

@bp.route(
    "/verify-qr",
    methods=["POST"],
)
@api_login_required
@csrf_protected
def verify_qr():
    """
    Verifikasi akses gerbang menggunakan QR Code tiket kerja / digital pass.
    """
    if not request.is_json:
        return api_error("Request harus menggunakan JSON.", 415, reason_code="CONTENT_TYPE_INVALID")

    data = request.get_json(silent=True) or {}
    qr_data = str(data.get("qr_data") or data.get("qr") or "").strip()
    site_id = str(data.get("site_id") or "").strip()
    access_type = str(data.get("access_type") or "MASUK").strip().upper()
    image = data.get("image") or None
    device_id = get_request_device()

    if not qr_data and image:
        try:
            import cv2
            import numpy as np
            import base64
            img_b64 = str(image)
            if "," in img_b64:
                img_b64 = img_b64.split(",", 1)[1]
            raw_bytes = base64.b64decode(img_b64)
            nparr = np.frombuffer(raw_bytes, np.uint8)
            cv_frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if cv_frame is not None:
                detector = cv2.QRCodeDetector()
                val, _, _ = detector.detectAndDecode(cv_frame)
                if val:
                    qr_data = str(val).strip()
        except Exception as exc:
            print("[OPENCV QR DECODE ERROR]", exc)

    if not qr_data:
        return api_error("QR Code tidak terdeteksi pada kamera atau data QR kosong.", 400, reason_code="QR_NOT_DETECTED")

    site, error = validate_site_and_access(site_id, access_type)
    if error:
        message, reason_code = error
        return api_error(message, 400, reason_code=reason_code)

    parsed_tech_id = None
    parsed_ticket = None

    import json
    if qr_data.startswith("{") and qr_data.endswith("}"):
        try:
            parsed_json = json.loads(qr_data)
            parsed_tech_id = parsed_json.get("tech_id") or parsed_json.get("technician_id")
            parsed_ticket = parsed_json.get("ticket_number") or parsed_json.get("wo_number") or parsed_json.get("ticket")
        except Exception:
            pass

    if not parsed_ticket and not parsed_tech_id:
        if ":" in qr_data:
            parts = qr_data.split(":", 1)
            parsed_tech_id, parsed_ticket = parts[0].strip(), parts[1].strip()
        elif qr_data.startswith("WO-"):
            parsed_ticket = qr_data
        elif qr_data.startswith("TECH-"):
            parsed_tech_id = qr_data

    technician = None
    from app.models import WorkOrder

    if parsed_ticket:
        wo = WorkOrder.query.filter_by(ticket_number=parsed_ticket).first()
        if wo:
            technician = Technician.query.filter_by(technician_id=wo.technician_id, status="Active").first()
            if wo.site_id != site.site_id:
                return api_error(
                    f"Tiket Kerja {parsed_ticket} terdaftar untuk site {wo.site_id}, bukan site ini ({site.site_id}).",
                    403,
                    reason_code="WO_SITE_MISMATCH",
                )
            if str(wo.status or "").strip().upper() not in ("APPROVED", "IN_PROGRESS", "ASSIGNED"):
                return api_error(
                    f"Tiket Kerja {parsed_ticket} berstatus {wo.status} (bukan APPROVED / IN_PROGRESS / ASSIGNED).",
                    403,
                    reason_code="WO_NOT_ACTIVE",
                )

    if not technician and parsed_tech_id:
        technician = Technician.query.filter_by(technician_id=parsed_tech_id, status="Active").first()

    if not technician:
        save_access_log(
            site_id=site.site_id,
            access_type=access_type,
            face_result="SKIPPED",
            otp_result="QR_INVALID",
            access_result="DENIED",
            reason_code="QR_INVALID",
            incident_level="WARNING",
            description=f"QR Code tidak dikenali atau tidak memiliki tiket aktif: {qr_data[:50]}",
            evidence_image=image,
            device_id=device_id,
        )
        return api_error("QR Code tidak valid atau teknisi tidak ditemukan.", 403, reason_code="QR_INVALID")

    if gate_is_in_cooldown(site.site_id):
        return api_error("Gate masih dalam cooldown.", 429, reason_code="GATE_COOLDOWN")

    gate_opened = open_gate(device_id=device_id, site_id=site.site_id)
    opened_at = get_gate_opened_at(site.site_id) or datetime.utcnow()

    access_log = save_access_log(
        technician_id=technician.technician_id,
        site_id=site.site_id,
        access_type=access_type,
        face_result="SKIPPED",
        otp_result="QR_VERIFIED",
        access_result="GRANTED",
        reason_code="QR_ACCESS_GRANTED",
        incident_level="NORMAL",
        description=f"Akses dibuka via pemindaian QR Code tiket kerja {parsed_ticket or ''} oleh {technician.name}.",
        evidence_image=image,
        device_id=device_id,
    )
    if gate_opened and access_log:
        access_log.gate_status = "OPEN"
        access_log.gate_opened_at = opened_at
        access_log.gate_closed_at = None
    db.session.commit()

    try:
        from app.notifications import send_gate_access_notification
        send_gate_access_notification(access_log)
    except Exception:
        pass

    create_activity_log(
        action="QR_ACCESS_GRANTED",
        description=f"Akses QR Code berhasil untuk {technician.name} pada site {site.site_id}.",
        incident_level="NORMAL",
        device_id=device_id,
    )

    return api_success(
        message="QR Code valid. Gerbang dibuka!",
        access_granted=True,
        gate_opened=True,
        tech_id=technician.technician_id,
        technician_name=technician.name,
        site_id=site.site_id,
        access_type=access_type,
        gate_open_duration=GATE_OPEN_SECONDS,
        gate_open_seconds=GATE_OPEN_SECONDS,
    )


# =========================================================
# NOC & MONITORING DASHBOARD API ENDPOINTS
# =========================================================

@bp.route("/noc/active-personnel", methods=["GET"])
@api_login_required
def noc_active_personnel():
    """
    Mengambil daftar personel/teknisi yang saat ini berada di dalam site BTS (On-Site).
    Mendeteksi potensi overstay jika melebihi batas waktu 3 jam.
    """
    from app.models import AccessLog, Technician, BtsSite
    from sqlalchemy import desc

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    logs = (
        AccessLog.query
        .filter(AccessLog.access_result == "GRANTED")
        .filter(AccessLog.timestamp >= today_start)
        .order_by(desc(AccessLog.timestamp))
        .all()
    )

    seen = set()
    active_list = []
    now = datetime.utcnow()

    for log in logs:
        if not log.technician_id:
            continue
        key = (log.technician_id, log.site_id)
        if key in seen:
            continue
        seen.add(key)

        if log.access_type == "MASUK":
            tech = Technician.query.filter_by(technician_id=log.technician_id).first()
            site = BtsSite.query.filter_by(site_id=log.site_id).first()
            
            diff_seconds = int((now - log.timestamp).total_seconds())
            hours = diff_seconds // 3600
            mins = (diff_seconds % 3600) // 60
            
            # Overstay threshold: 3 hours (180 mins)
            is_overstay = diff_seconds > (3 * 3600)

            active_list.append({
                "technician_id": log.technician_id,
                "technician_name": tech.name if tech else log.technician_id,
                "phone": tech.phone if tech else "-",
                "site_id": log.site_id,
                "site_name": site.site_name if site else log.site_id,
                "location": site.location if site else "-",
                "check_in_time": log.timestamp.strftime("%H:%M:%S"),
                "duration_str": f"{hours}j {mins}m",
                "duration_minutes": diff_seconds // 60,
                "is_overstay": is_overstay,
                "status": "OVERSTAY" if is_overstay else "NORMAL",
            })

    return api_success(
        active_count=len(active_list),
        overstay_count=sum(1 for p in active_list if p["is_overstay"]),
        personnel=active_list,
    )


@bp.route("/noc/site-map-summary", methods=["GET"])
def noc_site_map_summary():
    """
    Ringkasan sebaran menara BTS, status operasional, sensor pintu, dan personel aktif untuk Live NOC Map.
    """
    from app.models import BtsSite, AccessLog
    from sqlalchemy import desc

    sites = BtsSite.query.order_by(BtsSite.site_name.asc()).all()
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    summary = []
    for s in sites:
        gate_st = get_gate_status(s.site_id)
        sensor_st = get_door_sensor_status(s.site_id)
        
        two_hours_ago = datetime.utcnow() - timedelta(hours=2)
        critical_alert = (
            AccessLog.query
            .filter(AccessLog.site_id == s.site_id)
            .filter(AccessLog.incident_level.in_(["HIGH", "CRITICAL"]))
            .filter(AccessLog.timestamp >= two_hours_ago)
            .first()
        )

        latest_logs = (
            AccessLog.query
            .filter(AccessLog.site_id == s.site_id)
            .filter(AccessLog.access_result == "GRANTED")
            .filter(AccessLog.timestamp >= today_start)
            .order_by(desc(AccessLog.timestamp))
            .all()
        )
        tech_inside = set()
        for l in latest_logs:
            if l.technician_id and l.technician_id not in tech_inside:
                if l.access_type == "MASUK":
                    tech_inside.add(l.technician_id)

        has_breach = (sensor_st.get("state") == "DOOR_FORCED") or bool(
            critical_alert and critical_alert.reason_code in ("DOOR_FORCED_BREACH", "SILENT_DURESS_ALARM")
        )
        has_tech = len(tech_inside) > 0

        if has_breach:
            pin_status = "CRITICAL"
        elif has_tech:
            pin_status = "OCCUPIED"
        else:
            pin_status = "NORMAL"

        summary.append({
            "id": s.id,
            "site_id": s.site_id,
            "site_name": s.site_name,
            "location": s.location,
            "latitude": s.latitude,
            "longitude": s.longitude,
            "temperature": s.temperature,
            "power_output": s.power_output,
            "gate_status": str(gate_st or "CLOSED"),
            "door_sensor": sensor_st.get("state", "DOOR_CLOSED") if isinstance(sensor_st, dict) else str(sensor_st or "DOOR_CLOSED"),
            "active_tech_count": len(tech_inside),
            "pin_status": pin_status,
            "has_alarm": has_breach,
            "alarm_message": critical_alert.description if (critical_alert and has_breach) else None,
        })

    return api_success(sites=summary)


