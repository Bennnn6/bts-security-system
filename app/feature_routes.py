from datetime import datetime, timedelta
from io import BytesIO

from flask import (
    Blueprint,
    jsonify,
    request,
    render_template,
    send_file,
)

from flask_login import current_user
from sqlalchemy import or_

from app import app, db

from app.models import (
    Technician,
    BtsSite,
    AccessLog,
    ActivityLog,
)

from app.auth_helpers import admin_required

from app.access_control import (
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

features_bp = Blueprint(
    "security_features_bp",
    __name__,
)


# =========================================================
# CONFIGURATION
# =========================================================

GATE_OPEN_SECONDS = 30


# =========================================================
# EMERGENCY MODE
# =========================================================

_emergency = {
    "enabled": False,
    "reason": None,
    "changed_at": None,
}


# =========================================================
# DATETIME HELPER
# =========================================================

def dt(value):

    if value is None:
        return None

    try:
        return value.isoformat()
    except Exception:
        return str(value)


# =========================================================
# CURRENT ACTOR
# =========================================================

def actor():

    if (
        current_user
        and current_user.is_authenticated
    ):

        return (
            getattr(
                current_user,
                "username",
                None,
            )
            or "ADMIN"
        )

    return "SYSTEM"


# =========================================================
# AUDIT LOG
# =========================================================

def audit(
    action,
    description,
    level="NORMAL",
):

    try:

        log = ActivityLog(
            username=actor(),
            action=str(action)[:100],
            description=str(description)[:500],
            incident_level=str(level)[:20],
        )

        db.session.add(log)
        db.session.commit()

    except Exception:

        db.session.rollback()


# =========================================================
# RISK SCORE
# =========================================================

def calculate_site_risk(site_id):

    start = (
        datetime.utcnow()
        - timedelta(hours=24)
    )

    logs = AccessLog.query.filter(
        AccessLog.site_id == site_id,
        AccessLog.timestamp >= start,
    ).all()

    denied = sum(
        1
        for x in logs
        if x.access_result == "DENIED"
    )

    critical = sum(
        1
        for x in logs
        if x.incident_level == "CRITICAL"
    )

    high = sum(
        1
        for x in logs
        if x.incident_level == "HIGH"
    )

    warning = sum(
        1
        for x in logs
        if x.incident_level == "WARNING"
    )

    face_failed = sum(
        1
        for x in logs
        if x.face_result == "FAILED"
    )

    otp_failed = sum(
        1
        for x in logs
        if x.otp_result == "FAILED"
    )

    score = min(
        100,
        (
            denied * 8
            + critical * 25
            + high * 15
            + warning * 5
            + face_failed * 5
            + otp_failed * 5
        ),
    )

    if score >= 70:

        level = "CRITICAL"

    elif score >= 45:

        level = "HIGH"

    elif score >= 20:

        level = "WARNING"

    else:

        level = "NORMAL"

    return {
        "score": score,
        "level": level,
        "events_24h": len(logs),
        "denied_24h": denied,
        "critical_24h": critical,
        "high_24h": high,
        "warning_24h": warning,
        "face_failed_24h": face_failed,
        "otp_failed_24h": otp_failed,
    }


# =========================================================
# SUMMARY
# =========================================================

@features_bp.get("/summary")
@admin_required
def summary():

    now = datetime.utcnow()

    start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    technicians = (
        Technician.query
        .order_by(
            Technician.name.asc()
        )
        .all()
    )

    sites = (
        BtsSite.query
        .order_by(
            BtsSite.site_name.asc()
        )
        .all()
    )

    logs = (
        AccessLog.query
        .filter(
            AccessLog.timestamp >= start
        )
        .all()
    )

    inside = 0

    for technician in technicians:

        last = (
            AccessLog.query
            .filter(
                AccessLog.technician_id
                == technician.technician_id,

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

        if (
            last
            and last.access_type == "MASUK"
        ):

            inside += 1

    return jsonify(
        {
            "status": "success",

            "generated_at": dt(now),

            "emergency": {
                "enabled":
                    _emergency["enabled"],

                "reason":
                    _emergency["reason"],

                "changed_at":
                    dt(
                        _emergency[
                            "changed_at"
                        ]
                    ),
            },

            "gate": {
                "status":
                    get_gate_status(),

                "health":
                    get_gate_health(),

                "opened_at":
                    dt(
                        get_gate_opened_at()
                    ),

                "closed_at":
                    dt(
                        get_gate_closed_at()
                    ),

                "last_error":
                    get_gate_last_error(),

                "last_action":
                    get_gate_last_action(),

                "device_id":
                    get_gate_device_id(),
            },

            "counts": {
                "technicians":
                    len(technicians),

                "active_technicians":
                    sum(
                        1
                        for x in technicians
                        if x.status == "Active"
                    ),

                "sites":
                    len(sites),

                "access_today":
                    len(logs),

                "granted_today":
                    sum(
                        1
                        for x in logs
                        if x.access_result
                        == "GRANTED"
                    ),

                "denied_today":
                    sum(
                        1
                        for x in logs
                        if x.access_result
                        == "DENIED"
                    ),

                "pending_today":
                    sum(
                        1
                        for x in logs
                        if x.access_result
                        == "PENDING"
                    ),

                "inside":
                    inside,

                "incidents_today":
                    sum(
                        1
                        for x in logs
                        if x.incident_level
                        in [
                            "WARNING",
                            "HIGH",
                            "CRITICAL",
                        ]
                    ),

                "critical_today":
                    sum(
                        1
                        for x in logs
                        if x.incident_level
                        == "CRITICAL"
                    ),
            },

            "sites": [
                {
                    "site_id":
                        site.site_id,

                    "site_name":
                        site.site_name,

                    "location":
                        site.location,

                    "latitude":
                        site.latitude,

                    "longitude":
                        site.longitude,

                    "status":
                        site.status,

                    "temperature":
                        site.temperature,

                    "power_output":
                        site.power_output,

                    "risk":
                        calculate_site_risk(
                            site.site_id
                        ),
                }

                for site in sites
            ],
        }
    )


# =========================================================
# SYSTEM HEALTH
# =========================================================

@features_bp.get("/health")
@admin_required
def health():

    database_ok = True
    database_error = None

    try:

        db.session.execute(
            db.text("SELECT 1")
        )

    except Exception as exc:

        database_ok = False
        database_error = str(exc)

    gate = check_gate_health()

    return jsonify(
        {
            "status": "success",

            "services": {

                "database": {
                    "status":
                        "NORMAL"
                        if database_ok
                        else "ERROR",

                    "healthy":
                        database_ok,

                    "message":
                        "Database OK"
                        if database_ok
                        else database_error,
                },

                "gate": {
                    "status":
                        gate["health"],

                    "healthy":
                        gate["healthy"],

                    "message":
                        gate["message"],
                },

                "face_recognition": {
                    "status":
                        "NORMAL",

                    "healthy":
                        True,

                    "message":
                        "Modul Face Recognition tersedia.",
                },

                "otp": {
                    "status":
                        "NORMAL",

                    "healthy":
                        True,

                    "message":
                        "Modul OTP tersedia.",
                },

                "api": {
                    "status":
                        "NORMAL",

                    "healthy":
                        True,

                    "message":
                        "API Security Center aktif.",
                },
            },
        }
    )


# =========================================================
# TECHNICIANS
# =========================================================

@features_bp.get("/technicians")
@admin_required
def technicians():

    result = []

    rows = (
        Technician.query
        .order_by(
            Technician.name.asc()
        )
        .all()
    )

    for technician in rows:

        last = (
            AccessLog.query
            .filter(
                AccessLog.technician_id
                == technician.technician_id,

                AccessLog.access_result
                == "GRANTED",
            )
            .order_by(
                AccessLog.timestamp.desc()
            )
            .first()
        )

        state = "DI LUAR"

        if (
            last
            and last.access_type
            == "MASUK"
        ):

            state = "DI DALAM"

        result.append(
            {
                "technician_id":
                    technician.technician_id,

                "name":
                    technician.name,

                "username":
                    technician.username,

                "position":
                    technician.position,

                "phone":
                    technician.phone,

                "status":
                    technician.status,

                "face_registered":
                    bool(
                        technician.face_encoding
                    ),

                "totp_configured":
                    bool(
                        technician.totp_secret
                    ),

                "state":
                    state,

                "last_access":
                    dt(
                        last.timestamp
                    )
                    if last
                    else None,
            }
        )

    return jsonify(
        {
            "status": "success",
            "technicians": result,
        }
    )


# =========================================================
# BTS SITES
# =========================================================

@features_bp.get("/sites")
@admin_required
def sites():

    rows = (
        BtsSite.query
        .order_by(
            BtsSite.site_name.asc()
        )
        .all()
    )

    result = []

    for site in rows:

        result.append(
            {
                "site_id":
                    site.site_id,

                "site_name":
                    site.site_name,

                "location":
                    site.location,

                "latitude":
                    site.latitude,

                "longitude":
                    site.longitude,

                "status":
                    site.status,

                "temperature":
                    site.temperature,

                "power_output":
                    site.power_output,

                "risk":
                    calculate_site_risk(
                        site.site_id
                    ),
            }
        )

    return jsonify(
        {
            "status": "success",
            "sites": result,
        }
    )


# =========================================================
# DEVICES
# =========================================================

@features_bp.get("/devices")
@admin_required
def devices():

    rows = (
        db.session.query(
            AccessLog.device_id,
            db.func.max(
                AccessLog.timestamp
            ),
            db.func.count(
                AccessLog.id
            ),
        )
        .group_by(
            AccessLog.device_id
        )
        .all()
    )

    result = []

    for device_id, last_seen, count in rows:

        online = False

        if last_seen:

            online = (
                datetime.utcnow()
                - last_seen
            ).total_seconds() <= 300

        result.append(
            {
                "device_id":
                    device_id
                    or "UNKNOWN",

                "last_seen":
                    dt(last_seen),

                "access_count":
                    count,

                "status":
                    "ONLINE"
                    if online
                    else "OFFLINE",
            }
        )

    return jsonify(
        {
            "status": "success",
            "devices": result,
        }
    )


# =========================================================
# SECURITY INCIDENTS
# =========================================================

@features_bp.get("/incidents")
@admin_required
def incidents():

    query = (
        AccessLog.query
        .filter(
            AccessLog.incident_level.in_(
                [
                    "WARNING",
                    "HIGH",
                    "CRITICAL",
                ]
            )
        )
    )

    level = (
        request.args
        .get(
            "level",
            ""
        )
        .strip()
        .upper()
    )

    site_id = (
        request.args
        .get(
            "site_id",
            ""
        )
        .strip()
    )

    if level in [
        "WARNING",
        "HIGH",
        "CRITICAL",
    ]:

        query = query.filter(
            AccessLog.incident_level
            == level
        )

    if site_id:

        query = query.filter(
            AccessLog.site_id
            == site_id
        )

    logs = (
        query
        .order_by(
            AccessLog.timestamp.desc()
        )
        .limit(500)
        .all()
    )

    result = []

    for log in logs:

        result.append(
            {
                "id":
                    log.id,

                "timestamp":
                    dt(log.timestamp),

                "site_id":
                    log.site_id,

                "technician_id":
                    log.technician_id,

                "access_type":
                    log.access_type,

                "access_result":
                    log.access_result,

                "face_result":
                    log.face_result,

                "otp_result":
                    log.otp_result,

                "reason_code":
                    log.reason_code,

                "incident_level":
                    log.incident_level,

                "gate_status":
                    log.gate_status,

                "device_id":
                    log.device_id,

                "description":
                    log.description,
            }
        )

    return jsonify(
        {
            "status": "success",
            "incidents": result,
        }
    )


# =========================================================
# AUDIT TRAIL
# =========================================================

@features_bp.get("/audit")
@admin_required
def audit_logs():

    logs = (
        ActivityLog.query
        .order_by(
            ActivityLog.timestamp.desc()
        )
        .limit(500)
        .all()
    )

    result = []

    for log in logs:

        result.append(
            {
                "id":
                    log.id,

                "timestamp":
                    dt(log.timestamp),

                "username":
                    log.username,

                "action":
                    log.action,

                "description":
                    log.description,

                "incident_level":
                    log.incident_level,

                "device_id":
                    getattr(
                        log,
                        "device_id",
                        None
                    ),
            }
        )

    return jsonify(
        {
            "status": "success",
            "audit": result,
        }
    )


# =========================================================
# GATE HISTORY
# =========================================================

@features_bp.get("/gate-history")
@admin_required
def gate_history():

    logs = (
        AccessLog.query
        .filter(
            AccessLog.gate_status.in_(
                [
                    "OPEN",
                    "CLOSED",
                ]
            )
        )
        .order_by(
            AccessLog.timestamp.desc()
        )
        .limit(500)
        .all()
    )

    result = []

    for log in logs:

        result.append(
            {
                "id":
                    log.id,

                "timestamp":
                    dt(log.timestamp),

                "site_id":
                    log.site_id,

                "technician_id":
                    log.technician_id,

                "access_type":
                    log.access_type,

                "gate_status":
                    log.gate_status,

                "gate_opened_at":
                    dt(
                        log.gate_opened_at
                    ),

                "gate_closed_at":
                    dt(
                        log.gate_closed_at
                    ),

                "device_id":
                    log.device_id,
            }
        )

    return jsonify(
        {
            "status": "success",
            "history": result,
        }
    )


# =========================================================
# RISK PER SITE
# =========================================================

@features_bp.get(
    "/risk/<string:site_id>"
)
@admin_required
def risk_api(site_id):

    site = (
        BtsSite.query
        .filter_by(
            site_id=site_id
        )
        .first()
    )

    if not site:

        return jsonify(
            {
                "status": "error",
                "message":
                    "Site tidak ditemukan.",
            }
        ), 404

    return jsonify(
        {
            "status": "success",
            "risk":
                calculate_site_risk(
                    site_id
                ),
        }
    )


# =========================================================
# OPEN GATE
# =========================================================

@features_bp.post(
    "/gate/open"
)
@admin_required
def gate_open():

    if _emergency["enabled"]:

        return jsonify(
            {
                "status": "error",
                "message":
                    "Emergency Mode aktif. "
                    "Gate tidak dapat dibuka.",
            }
        ), 423

    success = force_open_gate(
        "ADMIN_FEATURE_PANEL"
    )

    if not success:

        audit(
            "GATE_OPEN_FAILED",
            "Gate gagal dibuka.",
            "CRITICAL",
        )

        return jsonify(
            {
                "status": "error",
                "message":
                    "Gate gagal dibuka.",
            }
        ), 503

    audit(
        "GATE_MANUAL_OPEN",
        "Admin membuka gate dari Security Center.",
        "WARNING",
    )

    return jsonify(
        {
            "status": "success",
            "message":
                "Gate berhasil dibuka.",

            "gate_status":
                get_gate_status(),

            "auto_close_seconds":
                GATE_OPEN_SECONDS,

            "opened_at":
                dt(
                    get_gate_opened_at()
                ),
        }
    )


# =========================================================
# CLOSE GATE
# =========================================================

@features_bp.post(
    "/gate/close"
)
@admin_required
def gate_close():

    success = force_close_gate(
        "ADMIN_FEATURE_PANEL"
    )

    if not success:

        audit(
            "GATE_CLOSE_FAILED",
            "Gate gagal ditutup.",
            "CRITICAL",
        )

        return jsonify(
            {
                "status": "error",
                "message":
                    "Gate gagal ditutup.",
            }
        ), 503

    audit(
        "GATE_MANUAL_CLOSE",
        "Admin menutup gate dari Security Center.",
        "WARNING",
    )

    return jsonify(
        {
            "status": "success",
            "message":
                "Gate berhasil ditutup.",

            "gate_status":
                get_gate_status(),

            "closed_at":
                dt(
                    get_gate_closed_at()
                ),
        }
    )


# =========================================================
# EMERGENCY MODE
# =========================================================

@features_bp.route(
    "/emergency",
    methods=["GET", "POST"],
)
@admin_required
def emergency():

    if request.method == "POST":

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        enabled = bool(
            data.get(
                "enabled"
            )
        )

        reason = str(
            data.get(
                "reason"
            )
            or ""
        ).strip()[:200]

        _emergency[
            "enabled"
        ] = enabled

        _emergency[
            "reason"
        ] = (
            reason
            if enabled
            else None
        )

        _emergency[
            "changed_at"
        ] = datetime.utcnow()

        if enabled:

            force_close_gate(
                "EMERGENCY_MODE"
            )

            audit(
                "EMERGENCY_MODE_ON",
                (
                    "Emergency Mode aktif: "
                    + (
                        reason
                        or "tanpa alasan"
                    )
                ),
                "CRITICAL",
            )

            try:
                from app.notifications import send_emergency_notification
                send_emergency_notification(True, reason=reason, actor=actor())
            except Exception:
                pass

        else:

            audit(
                "EMERGENCY_MODE_OFF",
                "Emergency Mode dinonaktifkan.",
                "WARNING",
            )

            try:
                from app.notifications import send_emergency_notification
                send_emergency_notification(False, reason=None, actor=actor())
            except Exception:
                pass

    return jsonify(
        {
            "status":
                "success",

            "enabled":
                _emergency[
                    "enabled"
                ],

            "reason":
                _emergency[
                    "reason"
                ],

            "changed_at":
                dt(
                    _emergency[
                        "changed_at"
                    ]
                ),
        }
    )


# =========================================================
# FILTER ACCESS LOG
# =========================================================

def filtered_logs():

    query = (
        AccessLog.query
        .order_by(
            AccessLog.timestamp.desc(),
            AccessLog.id.desc(),
        )
    )

    site_id = (
        request.args
        .get(
            "site_id",
            ""
        )
        .strip()
    )

    technician_id = (
        request.args
        .get(
            "technician_id",
            ""
        )
        .strip()
    )

    result = (
        request.args
        .get(
            "result",
            ""
        )
        .strip()
        .upper()
    )

    incident_level = (
        request.args
        .get(
            "incident_level",
            ""
        )
        .strip()
        .upper()
    )

    if site_id:

        query = query.filter(
            AccessLog.site_id
            == site_id
        )

    if technician_id:

        query = query.filter(
            AccessLog.technician_id
            == technician_id
        )

    if result in [
        "GRANTED",
        "DENIED",
        "PENDING",
    ]:

        query = query.filter(
            AccessLog.access_result
            == result
        )

    if incident_level in [
        "NORMAL",
        "WARNING",
        "HIGH",
        "CRITICAL",
    ]:

        query = query.filter(
            AccessLog.incident_level
            == incident_level
        )

    return query



# =========================================================
# ACCESS LOGS API
# Dipakai oleh halaman /access-logs
# =========================================================

@features_bp.get("/access-logs")
@admin_required
def access_logs_api():
    """
    API khusus untuk halaman Access Logs.

    Mengembalikan JSON agar fetch() pada access_logs.html
    tidak lagi menerima halaman HTML 404.
    """

    try:
        # -----------------------------
        # Parameter filter
        # -----------------------------
        site_id = request.args.get(
            "site_id",
            ""
        ).strip()

        technician_id = (
            request.args.get("technician_id", "")
            or request.args.get("tech_id", "")
        ).strip()

        access_type = request.args.get(
            "access_type",
            ""
        ).strip().upper()

        result_filter = request.args.get(
            "result",
            ""
        ).strip().upper()

        incident_level = request.args.get(
            "incident_level",
            ""
        ).strip().upper()

        reason_code = request.args.get(
            "reason_code",
            ""
        ).strip()

        gate_status = request.args.get(
            "gate_status",
            ""
        ).strip().upper()

        search = (
            request.args.get("q", "")
            or request.args.get("search", "")
        ).strip()

        date_from = request.args.get(
            "date_from",
            ""
        ).strip()

        date_to = request.args.get(
            "date_to",
            ""
        ).strip()

        # -----------------------------
        # Jumlah data
        # -----------------------------
        try:
            limit = int(
                request.args.get("limit", 50)
            )
        except (TypeError, ValueError):
            limit = 50

        limit = max(1, min(limit, 500))

        try:
            page = int(
                request.args.get("page", 1)
            )
        except (TypeError, ValueError):
            page = 1

        page = max(page, 1)

        # -----------------------------
        # Query
        # -----------------------------
        query = AccessLog.query

        if site_id:
            query = query.filter(
                AccessLog.site_id == site_id
            )

        if technician_id:
            query = query.filter(
                AccessLog.technician_id == technician_id
            )

        if access_type in (
            "MASUK",
            "KELUAR",
        ):
            query = query.filter(
                AccessLog.access_type == access_type
            )

        if result_filter in (
            "GRANTED",
            "DENIED",
            "PENDING",
        ):
            query = query.filter(
                AccessLog.access_result
                == result_filter
            )

        if incident_level in (
            "NORMAL",
            "WARNING",
            "HIGH",
            "CRITICAL",
        ):
            query = query.filter(
                AccessLog.incident_level
                == incident_level
            )

        if reason_code:
            query = query.filter(
                AccessLog.reason_code.ilike(
                    f"%{reason_code}%"
                )
            )

        if gate_status in (
            "OPEN",
            "CLOSED",
        ):
            query = query.filter(
                AccessLog.gate_status
                == gate_status
            )

        if date_from:
            try:
                start_date = datetime.strptime(
                    date_from,
                    "%Y-%m-%d"
                )
                query = query.filter(
                    AccessLog.timestamp
                    >= start_date
                )
            except ValueError:
                pass

        if date_to:
            try:
                end_date = (
                    datetime.strptime(
                        date_to,
                        "%Y-%m-%d"
                    )
                    + timedelta(days=1)
                )
                query = query.filter(
                    AccessLog.timestamp
                    < end_date
                )
            except ValueError:
                pass

        # -----------------------------
        # Search bebas
        # -----------------------------
        if search:
            like = f"%{search}%"

            query = query.outerjoin(
                Technician,
                AccessLog.technician_id
                == Technician.technician_id
            ).outerjoin(
                BtsSite,
                AccessLog.site_id
                == BtsSite.site_id
            ).filter(
                or_(
                    AccessLog.site_id.ilike(like),
                    AccessLog.technician_id.ilike(like),
                    AccessLog.access_type.ilike(like),
                    AccessLog.face_result.ilike(like),
                    AccessLog.otp_result.ilike(like),
                    AccessLog.access_result.ilike(like),
                    AccessLog.reason_code.ilike(like),
                    AccessLog.incident_level.ilike(like),
                    AccessLog.gate_status.ilike(like),
                    AccessLog.device_id.ilike(like),
                    AccessLog.description.ilike(like),
                    Technician.name.ilike(like),
                    BtsSite.site_name.ilike(like),
                )
            )

        # -----------------------------
        # Total + pagination
        # -----------------------------
        total = query.count()
        total_pages = (
            max(
                (total + limit - 1) // limit,
                1
            )
        )

        if page > total_pages:
            page = total_pages

        logs = (
            query
            .order_by(
                AccessLog.timestamp.desc(),
                AccessLog.id.desc(),
            )
            .offset(
                (page - 1) * limit
            )
            .limit(limit)
            .all()
        )

        # -----------------------------
        # Bentuk JSON
        # -----------------------------
        data = []

        for index, log in enumerate(
            logs,
            ((page - 1) * limit) + 1
        ):
            technician = getattr(
                log,
                "technician",
                None
            )

            site = getattr(
                log,
                "site",
                None
            )

            data.append(
                {
                    "id": log.id,
                    "no": index,
                    "timestamp": dt(
                        log.timestamp
                    ),
                    "waktu": dt(
                        log.timestamp
                    ),
                    "site_id": log.site_id,
                    "site_name": (
                        site.site_name
                        if site
                        else log.site_id
                    ),
                    "technician_id":
                        log.technician_id,
                    "technician_name": (
                        technician.name
                        if technician
                        else log.technician_id
                    ),
                    "access_type":
                        log.access_type,
                    "face_result":
                        log.face_result,
                    "face_distance":
                        log.face_distance,
                    "otp_result":
                        log.otp_result,
                    "access_result":
                        log.access_result,
                    "result":
                        log.access_result,
                    "reason_code":
                        log.reason_code,
                    "incident_level":
                        log.incident_level,
                    "gate_status":
                        log.gate_status,
                    "device_id":
                        log.device_id,
                    "description":
                        log.description,
                }
            )

        granted = sum(
            1
            for log in logs
            if str(
                log.access_result or ""
            ).upper() == "GRANTED"
        )

        denied = sum(
            1
            for log in logs
            if str(
                log.access_result or ""
            ).upper() == "DENIED"
        )

        pending = sum(
            1
            for log in logs
            if str(
                log.access_result or ""
            ).upper() == "PENDING"
        )

        return jsonify(
            {
                "status": "success",
                "success": True,
                "data": data,
                "logs": data,
                "total": total,
                "count": len(data),
                "granted": granted,
                "denied": denied,
                "pending": pending,
                "page": page,
                "limit": limit,
                "per_page": limit,
                "total_pages": total_pages,
            }
        )

    except Exception as exc:
        db.session.rollback()

        print(
            "ACCESS LOG API ERROR:",
            exc
        )

        return jsonify(
            {
                "status": "error",
                "success": False,
                "message": (
                    "Gagal memuat access log: "
                    f"{exc}"
                ),
                "data": [],
                "logs": [],
                "total": 0,
                "count": 0,
                "granted": 0,
                "denied": 0,
                "pending": 0,
            }
        ), 500


# =========================================================
# EXPORT EXCEL
# =========================================================

@features_bp.get(
    "/export/access-logs.xlsx"
)
@admin_required
def export_xlsx():

    try:

        import openpyxl

        from openpyxl.styles import (
            Font
        )

    except ImportError:

        return jsonify(
            {
                "status": "error",
                "message":
                    "Jalankan: pip install openpyxl",
            }
        ), 500

    logs = (
        filtered_logs()
        .limit(5000)
        .all()
    )

    workbook = (
        openpyxl.Workbook()
    )

    worksheet = (
        workbook.active
    )

    worksheet.title = (
        "Access Log"
    )

    headers = [
        "No",
        "Waktu",
        "Site",
        "Teknisi",
        "Akses",
        "Face",
        "Distance",
        "OTP",
        "Hasil",
        "Reason",
        "Level",
        "Gate",
        "Device",
        "Keterangan",
    ]

    worksheet.append(
        headers
    )

    for cell in worksheet[1]:

        cell.font = Font(
            bold=True
        )

    for index, log in enumerate(
        logs,
        1,
    ):

        worksheet.append(
            [
                index,
                dt(
                    log.timestamp
                ),

                log.site_id,

                log.technician_id,

                log.access_type,

                log.face_result,

                log.face_distance,

                log.otp_result,

                log.access_result,

                log.reason_code,

                log.incident_level,

                log.gate_status,

                log.device_id,

                log.description,
            ]
        )

    output = BytesIO()

    workbook.save(
        output
    )

    output.seek(0)

    audit(
        "EXPORT_ACCESS_LOG_XLSX",
        "Admin mengekspor Access Log ke Excel.",
    )

    return send_file(
        output,

        as_attachment=True,

        download_name=
            "access_logs.xlsx",

        mimetype=
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# =========================================================
# EXPORT PDF
# =========================================================

@features_bp.get(
    "/export/access-logs.pdf"
)
@admin_required
def export_pdf():

    try:

        from reportlab.lib.pagesizes import (
            A4,
            landscape,
        )

        from reportlab.lib import (
            colors
        )

        from reportlab.platypus import (
            SimpleDocTemplate,
            Table,
            TableStyle,
            Paragraph,
        )

        from reportlab.lib.styles import (
            getSampleStyleSheet
        )

    except ImportError:

        return jsonify(
            {
                "status": "error",
                "message":
                    "Jalankan: pip install reportlab",
            }
        ), 500

    logs = (
        filtered_logs()
        .limit(5000)
        .all()
    )

    output = BytesIO()

    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
    )

    styles = (
        getSampleStyleSheet()
    )

    data = [
        [
            "No",
            "Waktu",
            "Site",
            "Teknisi",
            "Akses",
            "Face",
            "OTP",
            "Hasil",
            "Level",
            "Gate",
            "Device",
        ]
    ]

    for index, log in enumerate(
        logs,
        1,
    ):

        data.append(
            [
                index,

                dt(
                    log.timestamp
                )
                or "-",

                log.site_id
                or "-",

                log.technician_id
                or "-",

                log.access_type
                or "-",

                log.face_result
                or "-",

                log.otp_result
                or "-",

                log.access_result
                or "-",

                log.incident_level
                or "-",

                log.gate_status
                or "-",

                (
                    log.device_id
                    or "-"
                )[:35],
            ]
        )

    table = Table(
        data,
        repeatRows=1,
    )

    table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor(
                        "#212529"
                    ),
                ),

                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.white,
                ),

                (
                    "FONTNAME",
                    (0, 0),
                    (-1, 0),
                    "Helvetica-Bold",
                ),

                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.3,
                    colors.grey,
                ),

                (
                    "FONTSIZE",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
            ]
        )
    )

    document.build(
        [
            Paragraph(
                "Laporan Access Log - BTS Security System",
                styles["Title"],
            ),
            table,
        ]
    )

    output.seek(0)

    audit(
        "EXPORT_ACCESS_LOG_PDF",
        "Admin mengekspor Access Log ke PDF.",
    )

    return send_file(
        output,

        as_attachment=True,

        download_name=
            "access_logs.pdf",

        mimetype=
            "application/pdf",
    )
# =========================================================
# REGISTER BLUEPRINT
# =========================================================

app.register_blueprint(
    features_bp,
    url_prefix="/api/features",
    name="security_features_api",
)