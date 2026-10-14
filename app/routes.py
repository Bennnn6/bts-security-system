from urllib.parse import quote
from sqlalchemy import or_
from datetime import datetime, timedelta
from io import BytesIO, StringIO
import base64
import csv
import queue
import os
import uuid
import secrets

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

from flask import (
    render_template,
    redirect,
    url_for,
    flash,
    request,
    jsonify,
    session,
    send_file,
    make_response,
    Response
)

from flask_login import (
    login_user,
    logout_user,
    current_user
)

from app.auth_helpers import (
    login_required_any,
    admin_required,
    technician_required,
)

from app import app, db

from app.models import (
    Admin,
    Technician,
    BtsSite,
    AccessLog,
    ActivityLog,
    WorkOrder
)

from app.face_recognition_module import (
    extract_face_encoding
)

from app.totp_module import (
    generate_secret,
    get_qr_code_base64
)

from app.access_control import (
    get_gate_status,
    get_gate_health,
    get_gate_last_error,
    get_gate_last_action,
    get_gate_device_id,
)


# =========================================================
# CONFIG
# =========================================================

SESSION_TIMEOUT_MINUTES = int(
    app.config.get(
        'SESSION_TIMEOUT_MINUTES',
        30
    )
)


def _can_access_technician_record(technician_id):
    if current_user.is_authenticated:
        return True
    worker_id = session.get('worker_id')
    return bool(worker_id and technician_id and str(worker_id) == str(technician_id))


def _resolve_private_media(value, category):
    if not value or category not in {'evidence', 'work_orders'}:
        return None

    value = str(value)
    private_prefix = f'private:{category}:'
    legacy_prefix = f'/static/uploads/{category}/'
    if value.startswith(private_prefix):
        filename = value[len(private_prefix):]
        directory = os.path.join(app.instance_path, 'uploads', category)
    elif value.startswith(legacy_prefix):
        filename = value[len(legacy_prefix):]
        directory = os.path.join(app.root_path, 'static', 'uploads', category)
    else:
        return None

    if (
        not filename
        or filename in {'.', '..'}
        or os.path.basename(filename) != filename
        or any(character not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for character in filename)
    ):
        return None

    path = os.path.join(directory, filename)
    return path if os.path.isfile(path) else None


# =========================================================
# SESSION SECURITY
# =========================================================

@app.before_request
def check_session_timeout():

    endpoint = request.endpoint

    if endpoint in (
        'login',
        'static'
    ):
        return

    # API punya mekanisme sendiri
    if request.path.startswith('/api/'):
        return

    now = datetime.utcnow()

    # -----------------------------------------------------
    # ADMIN
    # -----------------------------------------------------

    if current_user.is_authenticated:

        last_activity = session.get(
            'admin_last_activity'
        )

        if last_activity:

            try:

                last_dt = datetime.fromisoformat(
                    last_activity
                )

                elapsed = (
                    now - last_dt
                ).total_seconds()

                if elapsed > (
                    SESSION_TIMEOUT_MINUTES * 60
                ):

                    username = (
                        current_user.username
                    )

                    logout_user()

                    session.clear()

                    db.session.add(
                        ActivityLog(
                            username=username,
                            action='SESSION_TIMEOUT',
                            description=(
                                'Session admin berakhir '
                                'karena tidak ada aktivitas.'
                            ),
                            incident_level='NORMAL'
                        )
                    )

                    db.session.commit()

                    flash(
                        'Session berakhir karena tidak ada aktivitas.',
                        'warning'
                    )

                    return redirect(
                        url_for('login')
                    )

            except Exception:
                pass

        session[
            'admin_last_activity'
        ] = now.isoformat()

    # -----------------------------------------------------
    # TEKNISI
    # -----------------------------------------------------

    elif 'worker_id' in session:

        last_activity = session.get(
            'worker_last_activity'
        )

        if last_activity:

            try:

                last_dt = datetime.fromisoformat(
                    last_activity
                )

                elapsed = (
                    now - last_dt
                ).total_seconds()

                if elapsed > (
                    SESSION_TIMEOUT_MINUTES * 60
                ):

                    worker_id = session.get(
                        'worker_id',
                        'Unknown'
                    )

                    session.clear()

                    db.session.add(
                        ActivityLog(
                            username=worker_id,
                            action='SESSION_TIMEOUT',
                            description=(
                                'Session teknisi berakhir '
                                'karena tidak ada aktivitas.'
                            ),
                            incident_level='NORMAL'
                        )
                    )

                    db.session.commit()

                    flash(
                        'Session berakhir karena tidak ada aktivitas.',
                        'warning'
                    )

                    return redirect(
                        url_for('login')
                    )

            except Exception:
                pass

        session[
            'worker_last_activity'
        ] = now.isoformat()


# =========================================================
# HELPER
# =========================================================

def get_actor_name():

    if current_user.is_authenticated:
        return current_user.username

    return session.get(
        'worker_name',
        session.get(
            'worker_id',
            'SYSTEM'
        )
    )


def parse_date(
    value
):
    if not value:
        return None

    try:
        return datetime.strptime(
            value,
            '%Y-%m-%d'
        )

    except ValueError:
        return None


def get_technician_state(
    technician_id,
    site_id=None
):
    """
    Mengambil state (di dalam / di luar) seorang teknisi.
    Dipanggil per-teknisi, gunakan get_bulk_technician_states()
    bila perlu memproses banyak teknisi sekaligus.
    """
    try:
        query = (
            AccessLog.query
            .filter(
                AccessLog.technician_id == technician_id,
                AccessLog.access_result == 'GRANTED',
                AccessLog.access_type.in_(('MASUK', 'KELUAR'))
            )
        )

        if site_id:
            query = query.filter(
                AccessLog.site_id == site_id
            )

        latest = (
            query
            .order_by(AccessLog.timestamp.desc())
            .first()
        )

        if not latest:
            return {
                'inside': False,
                'state': 'DI LUAR',
                'site_id': None,
                'site_name': None,
                'last_access': None,
                'last_access_type': None
            }

        inside = (latest.access_type == 'MASUK')

        return {
            'inside': inside,
            'state': 'DI DALAM' if inside else 'DI LUAR',
            'site_id': latest.site_id,
            'site_name': (
                latest.site.site_name
                if latest.site
                else None
            ),
            'last_access': (
                latest.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                if latest.timestamp
                else None
            ),
            'last_access_type': latest.access_type
        }

    except Exception:
        return {
            'inside': False,
            'state': 'DI LUAR',
            'site_id': None,
            'site_name': None,
            'last_access': None,
            'last_access_type': None
        }


def get_bulk_technician_states(technician_ids):
    """
    B3/B7 FIX: Optimasi bulk query untuk mengambil state semua teknisi
    dalam SATU query, menghindari N+1 query problem.

    Mengembalikan dict: {technician_id: state_dict}
    """
    if not technician_ids:
        return {}

    try:
        from sqlalchemy import func

        # Subquery: ambil ID (primary key) dari AccessLog terbaru
        # per teknisi yang memiliki result GRANTED
        subq = (
            db.session.query(
                func.max(AccessLog.id).label('max_id')
            )
            .filter(
                AccessLog.technician_id.in_(technician_ids),
                AccessLog.access_result == 'GRANTED',
                AccessLog.access_type.in_(('MASUK', 'KELUAR'))
            )
            .group_by(AccessLog.technician_id)
            .subquery()
        )

        # Ambil semua AccessLog yang ID-nya ada di subquery
        latest_logs = (
            AccessLog.query
            .filter(AccessLog.id.in_(subq))
            .all()
        )

        # Buat lookup dict
        result = {}
        for log in latest_logs:
            inside = (log.access_type == 'MASUK')
            result[log.technician_id] = {
                'inside': inside,
                'state': 'DI DALAM' if inside else 'DI LUAR',
                'site_id': log.site_id,
                'site_name': (
                    log.site.site_name if log.site else None
                ),
                'last_access': (
                    log.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                    if log.timestamp
                    else None
                ),
                'last_access_type': log.access_type
            }

        # Teknisi yang tidak ada di log = di luar
        for tech_id in technician_ids:
            if tech_id not in result:
                result[tech_id] = {
                    'inside': False,
                    'state': 'DI LUAR',
                    'site_id': None,
                    'site_name': None,
                    'last_access': None,
                    'last_access_type': None
                }

        return result

    except Exception:
        # Fallback jika subquery gagal
        return {
            tech_id: {
                'inside': False,
                'state': 'DI LUAR',
                'site_id': None,
                'site_name': None,
                'last_access': None,
                'last_access_type': None
            }
            for tech_id in technician_ids
        }


def build_site_metrics(sites):
    """
    B3 FIX: Optimasi dengan bulk query.
    Sebelumnya: N queries per site + M queries per teknisi = N*M total.
    Sekarang  : 3 queries total untuk semua sites.
    """
    if not sites:
        return {}

    site_ids = [site.site_id for site in sites]

    today_start = datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # ---------------------------------------------------------
    # BULK QUERY 1: Semua AccessLog hari ini untuk semua sites
    # ---------------------------------------------------------
    try:
        all_today_logs = (
            AccessLog.query
            .filter(
                AccessLog.site_id.in_(site_ids),
                AccessLog.timestamp >= today_start
            )
            .all()
        )
    except Exception:
        all_today_logs = []

    # ---------------------------------------------------------
    # BULK QUERY 2: AccessLog terbaru per site (untuk last_access)
    # ---------------------------------------------------------
    try:
        from sqlalchemy import func
        subq_last = (
            db.session.query(
                func.max(AccessLog.id).label('max_id')
            )
            .filter(AccessLog.site_id.in_(site_ids))
            .group_by(AccessLog.site_id)
            .subquery()
        )
        last_logs_list = (
            AccessLog.query
            .filter(AccessLog.id.in_(subq_last))
            .all()
        )
        last_access_map = {
            log.site_id: log for log in last_logs_list
        }
    except Exception:
        last_access_map = {}

    # ---------------------------------------------------------
    # BULK QUERY 3: State teknisi aktif (satu query)
    # ---------------------------------------------------------
    try:
        technicians = (
            Technician.query
            .filter_by(status='Active')
            .all()
        )
        tech_ids = [t.technician_id for t in technicians]
        bulk_states = get_bulk_technician_states(tech_ids)
    except Exception:
        technicians = []
        bulk_states = {}

    # ---------------------------------------------------------
    # Grouping: log per site
    # ---------------------------------------------------------
    today_logs_by_site = {}
    for log in all_today_logs:
        today_logs_by_site.setdefault(log.site_id, []).append(log)

    # ---------------------------------------------------------
    # Hitung inside_count per site dari bulk_states
    # ---------------------------------------------------------
    inside_by_site = {}
    for state in bulk_states.values():
        if state['inside'] and state['site_id']:
            inside_by_site[state['site_id']] = (
                inside_by_site.get(state['site_id'], 0) + 1
            )

    # ---------------------------------------------------------
    # Build metrics dict
    # ---------------------------------------------------------
    metrics = {}

    for site in sites:
        today_logs = today_logs_by_site.get(site.site_id, [])

        access_today = len(today_logs)

        granted_today = sum(
            1 for log in today_logs
            if log.access_result == 'GRANTED'
        )

        denied_today = sum(
            1 for log in today_logs
            if log.access_result == 'DENIED'
        )

        incidents_today = sum(
            1 for log in today_logs
            if log.incident_level in ('WARNING', 'HIGH', 'CRITICAL')
        )

        critical_today = sum(
            1 for log in today_logs
            if log.incident_level == 'CRITICAL'
        )

        # Security state
        if critical_today >= 1 or denied_today >= 5 or incidents_today >= 5:
            security_status = 'CRITICAL'
        elif denied_today >= 3 or incidents_today >= 3:
            security_status = 'HIGH'
        elif denied_today >= 1 or incidents_today >= 1:
            security_status = 'WARNING'
        else:
            security_status = 'NORMAL'

        last_access = last_access_map.get(site.site_id)
        inside_count = inside_by_site.get(site.site_id, 0)

        metrics[site.site_id] = {
            'site_id': site.site_id,
            'site_name': site.site_name,
            'access_today': access_today,
            'granted_today': granted_today,
            'denied_today': denied_today,
            'failed_today': denied_today,
            'incident_count': incidents_today,
            'critical_today': critical_today,
            'inside_count': inside_count,
            'security_status': security_status,
            'last_access': (
                last_access.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                if last_access and last_access.timestamp
                else None
            ),
            'last_access_type': (
                last_access.access_type if last_access else None
            )
        }

    return metrics


# =========================================================
# LOGIN BRUTE FORCE PROTECTION
# =========================================================
#
# key = IP address, value = {'count': int, 'first_fail': datetime, 'locked_until': datetime}
#

_login_fail_store = {}
_login_fail_lock = __import__('threading').Lock()  # B11 FIX: Thread-safe access
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_MINUTES = 5
LOGIN_WINDOW_MINUTES = 5


def _check_login_brute_force(ip):
    """
    Cek apakah IP ini sedang dalam status lockout.
    Return: (is_locked: bool, seconds_remaining: int)
    """
    now = datetime.utcnow()
    with _login_fail_lock:  # B11 FIX: Thread-safe read
        record = _login_fail_store.get(ip)

    if not record:
        return False, 0

    locked_until = record.get('locked_until')
    if locked_until and now < locked_until:
        remaining = int((locked_until - now).total_seconds())
        return True, remaining

    # Reset jika window sudah lewat
    first_fail = record.get('first_fail', now)
    if (now - first_fail).total_seconds() > LOGIN_WINDOW_MINUTES * 60:
        with _login_fail_lock:
            _login_fail_store.pop(ip, None)

    return False, 0


def _record_login_failure(ip):
    """Catat kegagalan login dari IP ini (thread-safe)."""
    now = datetime.utcnow()
    with _login_fail_lock:  # B11 FIX: Thread-safe write
        record = _login_fail_store.get(ip, {
            'count': 0,
            'first_fail': now,
            'locked_until': None,
        })

        # Reset counter jika window sudah lewat
        if (now - record['first_fail']).total_seconds() > LOGIN_WINDOW_MINUTES * 60:
            record = {'count': 0, 'first_fail': now, 'locked_until': None}

        record['count'] += 1

        if record['count'] >= LOGIN_MAX_FAILURES:
            record['locked_until'] = now + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)

        _login_fail_store[ip] = record

        # Bersihkan entri lama agar tidak bocor memori
        if len(_login_fail_store) > 2000:
            stale = [
                k for k, v in _login_fail_store.items()
                if (now - v.get('first_fail', now)).total_seconds() > LOGIN_WINDOW_MINUTES * 60
                and not v.get('locked_until')
            ]
            for k in stale[:500]:
                _login_fail_store.pop(k, None)


def _reset_login_failures(ip):
    """Hapus catatan kegagalan login setelah login berhasil (thread-safe)."""
    with _login_fail_lock:  # B11 FIX
        _login_fail_store.pop(ip, None)


# =========================================================
# LOGIN
# =========================================================

@app.route(
    '/',
    methods=['GET', 'POST']
)
def login():

    # Jika masih memiliki session Admin yang valid,
    # langsung ke Dashboard Admin.
    if current_user.is_authenticated:

        session[
            'auth_role'
        ] = 'ADMIN'

        session[
            'auth_user_id'
        ] = str(
            current_user.id
        )

        session[
            'auth_username'
        ] = (
            getattr(
                current_user,
                'username',
                None
            )
            or 'ADMIN'
        )

        return redirect(
            url_for('dashboard')
        )

    # Jika session Teknisi masih valid,
    # langsung ke Dashboard Teknisi.
    if session.get('worker_id'):

        session[
            'auth_role'
        ] = 'TECHNICIAN'

        session[
            'auth_user_id'
        ] = str(
            session.get('worker_id')
        )

        session[
            'auth_username'
        ] = (
            session.get('worker_name')
            or session.get('worker_id')
        )

        return redirect(
            url_for('worker_dashboard')
        )

    if request.method == 'POST':

        # -------------------------------------------------
        # BRUTE FORCE CHECK
        # -------------------------------------------------

        client_ip = (
            request.remote_addr or '127.0.0.1'
        )

        is_locked, seconds_left = _check_login_brute_force(client_ip)

        if is_locked:
            minutes_left = max(1, seconds_left // 60)
            flash(
                f'Terlalu banyak percobaan login gagal. '
                f'Silakan coba lagi dalam {minutes_left} menit.',
                'danger'
            )
            return render_template('login.html')

        username = request.form.get(
            'username',
            ''
        ).strip()

        password = request.form.get(
            'password',
            ''
        )

        # -------------------------------------------------
        # ADMIN
        # -------------------------------------------------

        admin = (
            Admin.query
            .filter_by(
                username=username
            )
            .first()
        )

        if (
            admin
            and admin.check_password(
                password
            )
        ):

            # Login berhasil - hapus catatan kegagalan
            _reset_login_failures(client_ip)

            # Bersihkan session role lama agar tidak terjadi
            # konflik antara session Admin dan Teknisi.
            session.pop('worker_id', None)
            session.pop('worker_name', None)
            session.pop('worker_last_activity', None)

            login_user(
                admin
            )

            session[
                'auth_role'
            ] = 'ADMIN'

            session[
                'auth_user_id'
            ] = str(
                admin.id
            )

            session[
                'auth_username'
            ] = admin.username

            session[
                'admin_last_activity'
            ] = datetime.utcnow().isoformat()

            db.session.add(
                ActivityLog(
                    username=username,
                    action='LOGIN_SUCCESS',
                    description='Admin berhasil login.',
                    incident_level='NORMAL'
                )
            )

            try:  # B9 FIX: Jangan crash jika ActivityLog gagal commit
                db.session.commit()
            except Exception:
                db.session.rollback()

            return redirect(
                url_for('dashboard')
            )

        # -------------------------------------------------
        # TEKNISI
        # -------------------------------------------------

        technician = (
            Technician.query
            .filter_by(
                username=username
            )
            .first()
        )

        if (
            technician
            and technician.status == 'Active'
            and technician.check_password(
                password
            )
        ):

            # Login berhasil - hapus catatan kegagalan
            _reset_login_failures(client_ip)

            # Pastikan session Admin lama tidak bercampur
            # dengan session Teknisi.
            if current_user.is_authenticated:
                logout_user()

            session.pop('admin_last_activity', None)

            session[
                'worker_id'
            ] = technician.technician_id

            session[
                'worker_name'
            ] = technician.name

            session[
                'auth_role'
            ] = 'TECHNICIAN'

            session[
                'auth_user_id'
            ] = technician.technician_id

            session[
                'auth_username'
            ] = technician.username or technician.technician_id

            session[
                'worker_last_activity'
            ] = datetime.utcnow().isoformat()

            db.session.add(
                ActivityLog(
                    username=technician.name,
                    action='WORKER_LOGIN',
                    description=(
                        f'Teknisi {technician.name} '
                        f'({technician.technician_id}) '
                        'berhasil login.'
                    ),
                    incident_level='NORMAL'
                )
            )

            try:  # B9 FIX: Jangan crash jika ActivityLog gagal commit
                db.session.commit()
            except Exception:
                db.session.rollback()

            return redirect(
                url_for('worker_dashboard')
            )

        # -------------------------------------------------
        # LOGIN FAILED
        # -------------------------------------------------

        # Catat kegagalan untuk brute force protection
        _record_login_failure(client_ip)

        db.session.add(
            ActivityLog(
                username=(username or 'Unknown'),
                action='LOGIN_FAILED',
                description=(
                    f'Percobaan login gagal dari IP {client_ip}.'
                ),
                incident_level='WARNING'
            )
        )

        try:  # B9 FIX: Jangan crash jika ActivityLog gagal commit
            db.session.commit()
        except Exception:
            db.session.rollback()

        flash(
            'Username atau password salah.',
            'danger'
        )

    return render_template(
        'login.html'
    )


# =========================================================
# SETUP ADMIN
# =========================================================

@app.route('/setup_admin', methods=['GET', 'POST'])
def setup_admin():
    """
    Bootstrap akun administrator hanya dari mesin lokal dan dengan
    password bootstrap yang disetel melalui environment.
    """
    client_ip = request.remote_addr or '127.0.0.1'
    allowed_ips = ('127.0.0.1', '::1', 'localhost')

    if client_ip not in allowed_ips:
        return 'Akses ditolak.', 403

    if Admin.query.first():
        return 'Admin sudah ada. Setup tidak diperlukan.', 200

    expected_password = str(app.config.get('INITIAL_ADMIN_PASSWORD', '')).strip()
    if not expected_password:
        return 'Atur INITIAL_ADMIN_PASSWORD sebelum membuat akun admin.', 503
    if len(expected_password) < 12:
        return 'INITIAL_ADMIN_PASSWORD harus memiliki minimal 12 karakter.', 503

    if request.method == 'GET':
        return render_template('setup_admin.html')

    password = request.form.get('password', '')
    username = request.form.get('username', 'admin').strip() or 'admin'
    if not secrets.compare_digest(password, expected_password):
        return 'Password bootstrap tidak valid.', 403

    try:
        admin = Admin(username=username)
        admin.set_password(password)
        db.session.add(admin)
        db.session.add(
            ActivityLog(
                username='SYSTEM',
                action='ADMIN_CREATED',
                description='Akun administrator dibuat melalui bootstrap lokal.',
                incident_level='NORMAL'
            )
        )
        db.session.commit()
        return 'Akun administrator berhasil dibuat. Hapus INITIAL_ADMIN_PASSWORD dari environment setelah setup.', 201

    except Exception as exc:
        db.session.rollback()
        return f'Gagal membuat admin: {exc}', 500


# =========================================================
# DASHBOARD
# =========================================================

@app.route('/dashboard')
@admin_required
def dashboard():

    now = datetime.utcnow()

    today_start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    techs = (
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

    activities = (
        ActivityLog.query
        .order_by(
            ActivityLog.timestamp.desc()
        )
        .limit(10)
        .all()
    )

    logs_today = (
        AccessLog.query
        .filter(
            AccessLog.timestamp
            >= today_start
        )
        .all()
    )

    total_techs = len(
        techs
    )

    active_techs = sum(
        1
        for tech in techs
        if tech.status == 'Active'
    )

    access_today = len(
        logs_today
    )

    granted_today = sum(
        1
        for log in logs_today
        if log.access_result
        == 'GRANTED'
    )

    denied_today = sum(
        1
        for log in logs_today
        if log.access_result
        == 'DENIED'
    )

    # -----------------------------------------------------
    # TECH STATES
    # -----------------------------------------------------

    # B7 FIX: Gunakan bulk query untuk semua teknisi sekaligus
    tech_ids = [t.technician_id for t in techs]
    bulk_states = get_bulk_technician_states(tech_ids)

    technician_states = bulk_states

    inside_count = sum(
        1 for state in bulk_states.values()
        if state['inside']
    )

    # -----------------------------------------------------
    # SITE METRICS
    # -----------------------------------------------------

    site_metrics = (
        build_site_metrics(
            sites
        )
    )

    suspicious_sites = sum(
        1
        for metrics in site_metrics.values()
        if metrics['security_status']
        in (
            'HIGH',
            'CRITICAL'
        )
    )

    # -----------------------------------------------------
    # CHART 7 DAYS
    # -----------------------------------------------------

    chart_labels = []

    chart_granted = []

    chart_denied = []

    for days_ago in range(
        6,
        -1,
        -1
    ):

        day_start = (
            today_start
            - timedelta(
                days=days_ago
            )
        )

        day_end = (
            day_start
            + timedelta(
                days=1
            )
        )

        day_logs = (
            AccessLog.query
            .filter(
                AccessLog.timestamp
                >= day_start,

                AccessLog.timestamp
                < day_end
            )
            .all()
        )

        chart_labels.append(
            day_start.strftime(
                '%d/%m'
            )
        )

        chart_granted.append(
            sum(
                1
                for log in day_logs
                if log.access_result
                == 'GRANTED'
            )
        )

        chart_denied.append(
            sum(
                1
                for log in day_logs
                if log.access_result
                == 'DENIED'
            )
        )

    return render_template(
        'dashboard.html',

        total_techs=total_techs,

        active_techs=active_techs,

        total_sites=len(sites),

        access_today=access_today,

        granted_today=granted_today,

        denied_today=denied_today,

        inside_count=inside_count,

        suspicious_sites=suspicious_sites,

        techs=techs,

        sites=sites,

        activities=activities,

        technician_states=technician_states,

        site_metrics=site_metrics,

        chart_labels=chart_labels,

        chart_granted=chart_granted,

        chart_denied=chart_denied,

        gate_status=get_gate_status(),
        gate_health=get_gate_health(),
        gate_last_error=get_gate_last_error(),
        gate_last_action=get_gate_last_action(),
        gate_device_id=get_gate_device_id(),
        pending_work_order_verifications=WorkOrder.query.filter_by(
            status='COMPLETED'
        ).count()
    )


# =========================================================
# WORKER DASHBOARD
# =========================================================

@app.route(
    '/worker/dashboard',
    methods=['GET']
)
@technician_required
def worker_dashboard():

    if 'worker_id' not in session:

        flash(
            'Silakan login terlebih dahulu.',
            'warning'
        )

        return redirect(
            url_for('login')
        )

    search_query = request.args.get(
        'q',
        ''
    ).strip()

    if search_query:

        sites = (
            BtsSite.query
            .filter(
                (
                    BtsSite.site_id.ilike(
                        f'%{search_query}%'
                    )
                )
                |
                (
                    BtsSite.site_name.ilike(
                        f'%{search_query}%'
                    )
                )
                |
                (
                    BtsSite.location.ilike(
                        f'%{search_query}%'
                    )
                )
            )
            .order_by(
                BtsSite.site_name.asc()
            )
            .all()
        )

    else:

        sites = (
            BtsSite.query
            .order_by(
                BtsSite.site_name.asc()
            )
            .all()
        )

    worker_id = session.get('worker_id')
    technician = Technician.query.filter_by(technician_id=worker_id).first() if worker_id else None

    # Work orders ditugaskan ke teknisi ini
    my_work_orders = []
    if worker_id:
        my_work_orders = (
            WorkOrder.query.filter_by(technician_id=worker_id)
            .order_by(WorkOrder.scheduled_date.desc(), WorkOrder.id.desc())
            .all()
        )

    active_work_orders = [
        work_order
        for work_order in my_work_orders
        if work_order.status in ('APPROVED', 'PENDING', 'IN_PROGRESS')
    ]

    # Riwayat akses terakhir oleh teknisi ini
    my_access_logs = []
    if worker_id:
        my_access_logs = (
            AccessLog.query.filter_by(technician_id=worker_id)
            .order_by(AccessLog.timestamp.desc())
            .limit(10)
            .all()
        )

    # Status keberadaan (di dalam / di luar shelter)
    presence = None
    if worker_id:
        try:
            from app.api import get_latest_access_state
            presence = get_latest_access_state(worker_id)
        except Exception:
            pass

    return render_template(
        'worker_dashboard.html',
        sites=sites,
        search_query=search_query,
        worker_name=session.get('worker_name'),
        technician=technician,
        my_work_orders=my_work_orders,
        active_work_orders=active_work_orders,
        my_access_logs=my_access_logs,
        presence=presence,
        total_sites=BtsSite.query.count()
    )


@app.route('/api/worker/work-orders/summary', methods=['GET'])
@technician_required
def worker_work_order_summary():
    worker_id = session.get('worker_id')
    work_orders = (
        WorkOrder.query
        .filter_by(technician_id=worker_id)
        .order_by(WorkOrder.updated_at.desc(), WorkOrder.id.desc())
        .all()
    )
    active_orders = [
        work_order
        for work_order in work_orders
        if work_order.status in ('APPROVED', 'PENDING', 'IN_PROGRESS')
    ]
    latest = active_orders[0] if active_orders else None

    return jsonify({
        'status': 'success',
        'active_count': len(active_orders),
        'latest': {
            'ticket_number': latest.ticket_number,
            'purpose': latest.purpose,
            'site_name': latest.site_name,
            'status': latest.status,
        } if latest else None,
    })


# =========================================================
# REGISTER TECHNICIAN
# =========================================================

@app.route(
    '/technicians/register',
    methods=['GET', 'POST']
)
@admin_required
def register_technician():

    if request.method == 'GET':

        return render_template(
            'register_technician.html'
        )

    data = request.get_json(
        silent=True
    )

    if data is None:

        data = {
            'name': request.form.get(
                'name',
                ''
            ),

            'username': request.form.get(
                'username',
                ''
            ),

            'password': request.form.get(
                'password',
                ''
            ),

            'position': request.form.get(
                'position',
                'Teknisi Lapangan'
            ),

            'phone': request.form.get(
                'phone',
                ''
            ),

            'image': request.form.get(
                'image',
                ''
            )
        }

    name = str(
        data.get(
            'name',
            ''
        )
    ).strip()

    username = str(
        data.get(
            'username',
            ''
        )
    ).strip()

    password = str(
        data.get(
            'password',
            ''
        )
    )

    position = str(
        data.get(
            'position',
            'Teknisi Lapangan'
        )
    ).strip()

    phone = str(
        data.get(
            'phone',
            ''
        )
    ).strip()

    image_data = str(
        data.get(
            'image',
            ''
        )
        or ''
    ).strip()

    # -----------------------------------------------------
    # BASIC VALIDATION
    # -----------------------------------------------------

    if not name:

        if request.is_json:

            return jsonify({
                'status': 'error',
                'message': (
                    'Nama teknisi wajib diisi.'
                )
            }), 400

        flash(
            'Nama teknisi wajib diisi.',
            'danger'
        )

        return redirect(
            url_for('register_technician')
        )

    if not username:

        if request.is_json:

            return jsonify({
                'status': 'error',
                'message': (
                    'Username wajib diisi.'
                )
            }), 400

        flash(
            'Username wajib diisi.',
            'danger'
        )

        return redirect(
            url_for('register_technician')
        )

    if not password:

        if request.is_json:

            return jsonify({
                'status': 'error',
                'message': (
                    'Password wajib diisi.'
                )
            }), 400

        flash(
            'Password wajib diisi.',
            'danger'
        )

        return redirect(
            url_for('register_technician')
        )

    existing = (
        Technician.query
        .filter_by(
            username=username
        )
        .first()
    )

    if existing:

        if request.is_json:

            return jsonify({
                'status': 'error',
                'message': (
                    'Username sudah digunakan.'
                )
            }), 409

        flash(
            'Username sudah digunakan.',
            'danger'
        )

        return redirect(
            url_for('register_technician')
        )

    # -----------------------------------------------------
    # AUTO TECH ID
    # -----------------------------------------------------

    all_technicians = (
        Technician.query
        .all()
    )

    highest_number = 0

    for technician in all_technicians:

        tech_id = (
            technician.technician_id
            or ''
        ).upper()

        if tech_id.startswith(
            'TECH-'
        ):

            suffix = tech_id[5:]

            if suffix.isdigit():

                highest_number = max(
                    highest_number,
                    int(suffix)
                )

    technician_id = (
        f'TECH-{highest_number + 1:03d}'
    )

    # -----------------------------------------------------
    # FACE
    # -----------------------------------------------------

    face_encoding = None

    if image_data:

        try:

            face_encoding, message = (
                extract_face_encoding(
                    image_data
                )
            )

        except Exception as exc:

            print(
                'REGISTER FACE ERROR:',
                exc
            )

            face_encoding = None

            message = (
                'Gagal memproses wajah.'
            )

        if face_encoding is None:

            if request.is_json:

                return jsonify({
                    'status': 'error',
                    'message': message
                }), 400

            flash(
                message,
                'danger'
            )

            return redirect(
                url_for(
                    'register_technician'
                )
            )

    # -----------------------------------------------------
    # SAVE
    # -----------------------------------------------------

    try:

        technician = Technician(
            technician_id=technician_id,

            name=name,

            position=(
                position
                or 'Teknisi Lapangan'
            ),

            phone=phone,

            status='Active',

            face_encoding=face_encoding,

            username=username
        )

        technician.set_password(
            password
        )

        db.session.add(
            technician
        )

        db.session.add(
            ActivityLog(
                username=current_user.username,
                action='REGISTER_TECHNICIAN',
                description=(
                    f'Mendaftarkan teknisi '
                    f'{name} ({technician_id}).'
                ),
                incident_level='NORMAL'
            )
        )

        db.session.commit()

    except Exception as exc:

        db.session.rollback()

        print(
            'REGISTER ERROR:',
            exc
        )

        if request.is_json:

            return jsonify({
                'status': 'error',
                'message': (
                    'Gagal menyimpan teknisi.'
                )
            }), 500

        flash(
            'Gagal menyimpan teknisi.',
            'danger'
        )

        return redirect(
            url_for(
                'register_technician'
            )
        )

    if request.is_json:

        return jsonify({
            'status': 'success',
            'message': (
                'Teknisi berhasil didaftarkan.'
            ),
            'tech_id': technician_id
        })

    flash(
        (
            f'Teknisi {name} berhasil '
            f'didaftarkan sebagai {technician_id}.'
        ),
        'success'
    )

    return redirect(
        url_for('dashboard')
    )


# =========================================================
# EDIT TECHNICIAN
# =========================================================

@app.route(
    '/technicians/edit/<string:tech_id>',
    methods=['GET', 'POST']
)
@admin_required
def edit_technician(
    tech_id
):

    technician = (
        Technician.query
        .filter_by(
            technician_id=tech_id
        )
        .first_or_404()
    )

    if request.method == 'GET':

        return render_template(
            'edit_technician.html',
            technician=technician
        )

    technician.name = request.form.get(
        'name',
        technician.name
    ).strip()

    new_username = request.form.get(
        'username',
        technician.username
        or ''
    ).strip()

    if new_username and new_username != technician.username:
        duplicate = (
            Technician.query
            .filter(
                Technician.username == new_username,
                Technician.technician_id != technician.technician_id
            )
            .first()
        )
        if duplicate:
            flash(
                'Username sudah digunakan oleh teknisi lain.',
                'danger'
            )
            return redirect(
                url_for(
                    'edit_technician',
                    tech_id=tech_id
                )
            )

    technician.username = new_username

    technician.position = request.form.get(
        'position',
        technician.position
    ).strip()

    technician.phone = request.form.get(
        'phone',
        technician.phone
    ).strip()

    technician.status = request.form.get(
        'status',
        technician.status
    ).strip()

    password = request.form.get(
        'password',
        ''
    )

    if password:
        technician.set_password(
            password
        )

    image_data = request.form.get(
        'image',
        ''
    ).strip()

    if image_data:

        try:

            face_encoding, message = (
                extract_face_encoding(
                    image_data
                )
            )

            if face_encoding is None:

                flash(
                    message,
                    'danger'
                )

                return redirect(
                    url_for(
                        'edit_technician',
                        tech_id=tech_id
                    )
                )

            technician.face_encoding = (
                face_encoding
            )

        except Exception as exc:

            print(
                'EDIT FACE ERROR:',
                exc
            )

            flash(
                'Gagal memproses wajah.',
                'danger'
            )

            return redirect(
                url_for(
                    'edit_technician',
                    tech_id=tech_id
                )
            )

    try:

        db.session.add(
            ActivityLog(
                username=current_user.username,
                action='EDIT_TECHNICIAN',
                description=(
                    f'Memperbarui teknisi '
                    f'{technician.name} '
                    f'({technician.technician_id}).'
                ),
                incident_level='NORMAL'
            )
        )

        db.session.commit()

        flash(
            'Data teknisi berhasil diperbarui.',
            'success'
        )

    except Exception as exc:

        db.session.rollback()

        print(
            'EDIT TECH ERROR:',
            exc
        )

        flash(
            'Gagal memperbarui teknisi.',
            'danger'
        )

    return redirect(
        url_for('dashboard')
    )


# =========================================================
# FACE ENROLLMENT
# =========================================================

@app.route(
    '/technicians/enroll/<string:tech_id>',
    methods=['GET', 'POST']
)
@admin_required
def face_enrollment(
    tech_id
):

    technician = (
        Technician.query
        .filter_by(
            technician_id=tech_id
        )
        .first_or_404()
    )

    if request.method == 'GET':

        return render_template(
            'face_enrollment.html',
            technician=technician
        )

    data = request.get_json(
        silent=True
    )

    if data:

        image_data = str(
            data.get(
                'image',
                ''
            )
            or ''
        ).strip()

    else:

        image_data = request.form.get(
            'image',
            ''
        ).strip()

    if not image_data:

        message = (
            'Foto wajah wajib dikirim.'
        )

        if request.is_json:

            return jsonify({
                'status': 'error',
                'message': message
            }), 400

        flash(
            message,
            'danger'
        )

        return redirect(
            url_for(
                'face_enrollment',
                tech_id=tech_id
            )
        )

    try:

        face_encoding, message = (
            extract_face_encoding(
                image_data
            )
        )

        if face_encoding is None:

            if request.is_json:

                return jsonify({
                    'status': 'error',
                    'message': message
                }), 400

            flash(
                message,
                'danger'
            )

            return redirect(
                url_for(
                    'face_enrollment',
                    tech_id=tech_id
                )
            )

        technician.face_encoding = (
            face_encoding
        )

        db.session.add(
            ActivityLog(
                username=current_user.username,
                action='FACE_ENROLLMENT',
                description=(
                    f'Enrollment wajah '
                    f'teknisi {technician.name} '
                    f'({technician.technician_id}).'
                ),
                incident_level='NORMAL'
            )
        )

        db.session.commit()

    except Exception as exc:

        db.session.rollback()

        print(
            'ENROLLMENT ERROR:',
            exc
        )

        if request.is_json:

            return jsonify({
                'status': 'error',
                'message': (
                    'Gagal menyimpan encoding wajah.'
                )
            }), 500

        flash(
            'Gagal menyimpan encoding wajah.',
            'danger'
        )

        return redirect(
            url_for(
                'face_enrollment',
                tech_id=tech_id
            )
        )

    if request.is_json:

        return jsonify({
            'status': 'success',
            'message': (
                'Wajah berhasil didaftarkan.'
            ),
            'tech_id': technician.technician_id
        })

    flash(
        'Wajah berhasil didaftarkan.',
        'success'
    )

    return redirect(
        url_for('dashboard')
    )


# =========================================================
# TOTP SETUP
# =========================================================

@app.route(
    '/technicians/totp/<string:tech_id>',
    methods=['GET', 'POST']
)
@admin_required
def totp_setup(
    tech_id
):

    technician = (
        Technician.query
        .filter(
            (Technician.technician_id == tech_id) | (Technician.id == tech_id)
        )
        .first_or_404()
    )

    regenerate = request.args.get('regenerate') in ('1', 'true', 'True') or request.args.get('reset') in ('1', 'true', 'True')

    if request.method == 'POST':

        data = request.get_json(
            silent=True
        )

        if data:

            secret = str(
                data.get(
                    'totp_secret',
                    ''
                )
                or ''
            ).strip()

        else:

            secret = request.form.get(
                'totp_secret',
                ''
            ).strip()

        if not secret:

            flash(
                'Secret TOTP tidak boleh kosong.',
                'danger'
            )

            return redirect(
                url_for(
                    'totp_setup',
                    tech_id=technician.technician_id
                )
            )

        try:

            technician.totp_secret = (
                secret
            )

            db.session.add(
                ActivityLog(
                    username=current_user.username,
                    action='TOTP_SETUP',
                    description=(
                        f'Setup TOTP untuk '
                        f'teknisi {technician.name}.'
                    ),
                    incident_level='NORMAL'
                )
            )

            db.session.commit()

            flash(
                'TOTP berhasil disimpan.',
                'success'
            )

        except Exception as exc:

            db.session.rollback()

            print(
                'TOTP SAVE ERROR:',
                exc
            )

            flash(
                'Gagal menyimpan TOTP.',
                'danger'
            )

        return redirect(
            url_for(
                'totp_setup',
                tech_id=technician.technician_id
            )
        )

    # -----------------------------------------------------
    # CREATE / REGENERATE SECRET
    # -----------------------------------------------------

    if regenerate or not technician.totp_secret:

        secret = generate_secret()

        technician.totp_secret = (
            secret
        )

        db.session.add(
            ActivityLog(
                username=current_user.username,
                action='TOTP_REGENERATE' if regenerate else 'TOTP_SETUP',
                description=(
                    f'Regenerate TOTP secret untuk teknisi {technician.name}.'
                    if regenerate else
                    f'Inisialisasi TOTP secret untuk teknisi {technician.name}.'
                ),
                incident_level='NORMAL'
            )
        )

        db.session.commit()

        if regenerate:
            flash(
                'Secret Key baru berhasil di-generate. Silakan scan ulang QR Code.',
                'success'
            )
            return redirect(
                url_for(
                    'totp_setup',
                    tech_id=technician.technician_id
                )
            )

    else:

        secret = technician.totp_secret

    # -----------------------------------------------------
    # QR
    # -----------------------------------------------------

    qr_code = None

    try:

        qr_code = get_qr_code_base64(
            technician.name,
            secret
        )

    except TypeError:

        try:

            qr_code = get_qr_code_base64(
                secret
            )

        except Exception as exc:

            print(
                'QR ERROR:',
                exc
            )

    except Exception as exc:

        print(
            'QR ERROR:',
            exc
        )

    return render_template(
        'totp_setup.html',
        technician=technician,
        secret=secret,
        qr_code=qr_code
    )


@app.route(
    '/technicians/totp/<string:tech_id>/verify',
    methods=['POST']
)
@admin_required
def totp_verify_test(tech_id):
    technician = (
        Technician.query
        .filter(
            (Technician.technician_id == tech_id) | (Technician.id == tech_id)
        )
        .first_or_404()
    )

    data = request.get_json(silent=True)
    if not data:
        data = request.form

    otp_code = str(data.get('otp_code', '') or '').strip()
    if not otp_code:
        return jsonify({
            'status': 'error',
            'valid': False,
            'message': 'Masukkan 6 digit kode OTP.'
        }), 400

    from app.totp_module import verify_totp
    is_valid = verify_totp(technician.totp_secret, otp_code)

    if is_valid:
        return jsonify({
            'status': 'success',
            'valid': True,
            'message': 'Kode OTP Valid! Aplikasi Authenticator tersinkronisasi dengan sukses.'
        })
    else:
        return jsonify({
            'status': 'fail',
            'valid': False,
            'message': 'Kode OTP Salah atau Kadaluarsa! Periksa jam pada HP teknisi atau coba kode berikutnya.'
        }), 400


# =========================================================
# BTS SITES
# =========================================================

@app.route(
    '/sites',
    methods=['GET']
)
@admin_required
def bts_sites():

    search_query = request.args.get(
        'q',
        ''
    ).strip()

    if search_query:

        sites = (
            BtsSite.query
            .filter(
                (
                    BtsSite.site_id.ilike(
                        f'%{search_query}%'
                    )
                )
                |
                (
                    BtsSite.site_name.ilike(
                        f'%{search_query}%'
                    )
                )
                |
                (
                    BtsSite.location.ilike(
                        f'%{search_query}%'
                    )
                )
            )
            .order_by(
                BtsSite.site_name.asc()
            )
            .all()
        )

    else:

        sites = (
            BtsSite.query
            .order_by(
                BtsSite.site_name.asc()
            )
            .all()
        )

    site_metrics = build_site_metrics(
        sites
    )

    return render_template(
        'bts_sites.html',
        sites=sites,
        search_query=search_query,
        site_metrics=site_metrics,
        external_base_url=_get_external_access_base_url()
    )


# =========================================================
# ADD BTS SITE
# =========================================================

@app.route(
    '/sites/add',
    methods=['POST']
)
@admin_required
def add_bts_site():

    site_id = request.form.get(
        'site_id',
        ''
    ).strip()

    site_name = request.form.get(
        'site_name',
        ''
    ).strip()

    location = request.form.get(
        'location',
        ''
    ).strip()

    latitude = request.form.get(
        'latitude',
        ''
    ).strip()

    longitude = request.form.get(
        'longitude',
        ''
    ).strip()

    description = request.form.get(
        'description',
        ''
    ).strip()

    status = request.form.get(
        'status',
        'Normal (Up)'
    ).strip()

    if not site_id:

        flash(
            'Site ID wajib diisi.',
            'danger'
        )

        return redirect(
            url_for('bts_sites')
        )

    if not site_name:

        flash(
            'Nama Site wajib diisi.',
            'danger'
        )

        return redirect(
            url_for('bts_sites')
        )

    existing = (
        BtsSite.query
        .filter_by(
            site_id=site_id
        )
        .first()
    )

    if existing:

        flash(
            'Site ID sudah digunakan.',
            'danger'
        )

        return redirect(
            url_for('bts_sites')
        )

    try:

        site = BtsSite(
            site_id=site_id,

            site_name=site_name,

            location=location,

            latitude=latitude,

            longitude=longitude,

            description=description,

            status=status
        )

        db.session.add(
            site
        )

        db.session.add(
            ActivityLog(
                username=current_user.username,
                action='ADD_SITE',
                description=(
                    f'Menambahkan Site '
                    f'{site_name} ({site_id}).'
                ),
                incident_level='NORMAL'
            )
        )

        db.session.commit()

        flash(
            'Site BTS berhasil ditambahkan.',
            'success'
        )

    except Exception as exc:

        db.session.rollback()

        print(
            'ADD SITE ERROR:',
            exc
        )

        flash(
            'Gagal menambahkan Site BTS.',
            'danger'
        )

    return redirect(
        url_for('bts_sites')
    )


# =========================================================
# EDIT BTS SITE
# =========================================================

@app.route(
    '/sites/edit/<int:site_id>',
    methods=['GET', 'POST']
)
@admin_required
def edit_bts_site(
    site_id
):

    site = BtsSite.query.get_or_404(
        site_id
    )

    if request.method == 'GET':

        return render_template(
            'edit_bts_site.html',
            site=site
        )

    new_site_id = request.form.get(
        'site_id',
        site.site_id
    ).strip()

    duplicate = (
        BtsSite.query
        .filter(
            BtsSite.site_id
            == new_site_id,

            BtsSite.id
            != site.id
        )
        .first()
    )

    if duplicate:

        flash(
            'Site ID sudah digunakan.',
            'danger'
        )

        return redirect(
            url_for(
                'edit_bts_site',
                site_id=site.id
            )
        )

    site.site_id = new_site_id

    site.site_name = request.form.get(
        'site_name',
        site.site_name
    ).strip()

    site.location = request.form.get(
        'location',
        site.location
    ).strip()

    site.latitude = request.form.get(
        'latitude',
        site.latitude
    ).strip()

    site.longitude = request.form.get(
        'longitude',
        site.longitude
    ).strip()

    site.description = request.form.get(
        'description',
        site.description
        or ''
    ).strip()

    site.status = request.form.get(
        'status',
        site.status
    ).strip()

    try:

        db.session.add(
            ActivityLog(
                username=current_user.username,
                action='EDIT_SITE',
                description=(
                    f'Mengubah Site '
                    f'{site.site_name} '
                    f'({site.site_id}).'
                ),
                incident_level='NORMAL'
            )
        )

        db.session.commit()

        flash(
            'Site BTS berhasil diperbarui.',
            'success'
        )

    except Exception as exc:

        db.session.rollback()

        print(
            'EDIT SITE ERROR:',
            exc
        )

        flash(
            'Gagal memperbarui Site.',
            'danger'
        )

    return redirect(
        url_for('bts_sites')
    )


# =========================================================
# DELETE BTS SITE
# =========================================================

@app.route(
    '/sites/delete/<int:site_id>'
)
@admin_required
def delete_bts_site(
    site_id
):

    site = BtsSite.query.get_or_404(
        site_id
    )

    has_logs = (
        AccessLog.query
        .filter_by(
            site_id=site.site_id
        )
        .first()
    )

    if has_logs:

        flash(
            (
                'Site tidak dapat dihapus karena '
                'sudah memiliki Access Log. '
                'Gunakan status Down.'
            ),
            'warning'
        )

        return redirect(
            url_for('bts_sites')
        )

    try:

        site_code = site.site_id

        site_name = site.site_name

        db.session.delete(
            site
        )

        db.session.add(
            ActivityLog(
                username=current_user.username,
                action='DELETE_SITE',
                description=(
                    f'Menghapus Site '
                    f'{site_name} ({site_code}).'
                ),
                incident_level='WARNING'
            )
        )

        db.session.commit()

        flash(
            'Site BTS berhasil dihapus.',
            'success'
        )

    except Exception as exc:

        db.session.rollback()

        print(
            'DELETE SITE ERROR:',
            exc
        )

        flash(
            'Gagal menghapus Site.',
            'danger'
        )

    return redirect(
        url_for('bts_sites')
    )


# =========================================================
# ACCESS SIMULATION
# =========================================================

@app.route(
    '/access',
    methods=['GET']
)
@login_required_any
def access_simulation():

    sites = (
        BtsSite.query
        .order_by(
            BtsSite.site_name.asc()
        )
        .all()
    )

    technicians = (
        Technician.query
        .filter_by(status='Active')
        .order_by(Technician.name.asc())
        .all()
    )

    return render_template(
        'access_simulation.html',
        sites=sites,
        technicians=technicians
    )


# =========================================================
# GATE KIOSK TERMINAL (ON-SITE PHYSICAL ACCESS DEVICE)
# =========================================================

@app.route('/kiosk', methods=['GET'])
@app.route('/kiosk/<site_id>', methods=['GET'])
def gate_kiosk(site_id=None):
    """
    Antarmuka Terminal Fisik Gerbang BTS (Kiosk Mode).
    Digunakan pada perangkat layar sentuh / tablet yang terpasang langsung di gerbang.
    """
    # Tandai sesi sebagai kiosk terminal
    session['is_kiosk'] = True

    selected_site = None
    if site_id:
        selected_site = BtsSite.query.filter(
            (BtsSite.site_id == site_id) | (BtsSite.site_id.ilike(site_id))
        ).first()

    sites = BtsSite.query.order_by(BtsSite.site_name.asc()).all()
    if not selected_site and sites:
        selected_site = sites[0]

    if selected_site:
        session['kiosk_site_id'] = selected_site.site_id

    technicians = (
        Technician.query
        .filter_by(status='Active')
        .order_by(Technician.name.asc())
        .all()
    )

    from app.api import get_csrf_token
    csrf_token = get_csrf_token()

    return render_template(
        'kiosk.html',
        site=selected_site,
        sites=sites,
        technicians=technicians,
        csrf_token=csrf_token,
    )


# =========================================================
# ACCESS LOG
# =========================================================

@app.route(
    '/access-logs',
    methods=['GET']
)
@admin_required
def access_logs():

    # Filter dasar
    site_id = request.args.get('site_id', '').strip()
    tech_id = request.args.get('tech_id', '').strip()
    access_type = request.args.get('access_type', '').strip().upper()
    result = request.args.get('result', '').strip().upper()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    # Filter Step 20
    search = request.args.get('q', '').strip()
    incident_level = request.args.get('incident_level', '').strip().upper()
    reason_code = request.args.get('reason_code', '').strip().upper()
    gate_status = request.args.get('gate_status', '').strip().upper()

    # Pagination
    try:
        page = max(int(request.args.get('page', 1)), 1)
    except (TypeError, ValueError):
        page = 1

    try:
        per_page = int(request.args.get('per_page', 25))
    except (TypeError, ValueError):
        per_page = 25

    if per_page not in (25, 50, 100):
        per_page = 25

    query = (
        AccessLog.query
        .outerjoin(
            Technician,
            AccessLog.technician_id == Technician.technician_id
        )
        .outerjoin(
            BtsSite,
            AccessLog.site_id == BtsSite.site_id
        )
        .order_by(AccessLog.timestamp.desc(), AccessLog.id.desc())
    )

    if site_id:
        query = query.filter(AccessLog.site_id == site_id)

    if tech_id:
        query = query.filter(AccessLog.technician_id == tech_id)

    if access_type in ('MASUK', 'KELUAR'):
        query = query.filter(AccessLog.access_type == access_type)

    if result in ('GRANTED', 'DENIED', 'PENDING'):
        query = query.filter(AccessLog.access_result == result)

    if incident_level in ('NORMAL', 'WARNING', 'HIGH', 'CRITICAL'):
        query = query.filter(AccessLog.incident_level == incident_level)

    if reason_code:
        query = query.filter(AccessLog.reason_code.ilike(f'%{reason_code}%'))

    if gate_status in ('OPEN', 'CLOSED'):
        query = query.filter(AccessLog.gate_status == gate_status)

    if date_from:
        start_date = parse_date(date_from)
        if start_date:
            query = query.filter(AccessLog.timestamp >= start_date)

    if date_to:
        end_date = parse_date(date_to)
        if end_date:
            query = query.filter(AccessLog.timestamp < (end_date + timedelta(days=1)))

    if search:
        like = f'%{search}%'
        query = query.filter(or_(
            AccessLog.site_id.ilike(like),
            AccessLog.technician_id.ilike(like),
            Technician.name.ilike(like),
            BtsSite.site_name.ilike(like),
            AccessLog.access_type.ilike(like),
            AccessLog.face_result.ilike(like),
            AccessLog.otp_result.ilike(like),
            AccessLog.access_result.ilike(like),
            AccessLog.reason_code.ilike(like),
            AccessLog.incident_level.ilike(like),
            AccessLog.gate_status.ilike(like),
            AccessLog.device_id.ilike(like),
            AccessLog.description.ilike(like),
        ))

    total = query.count()
    total_pages = max((total + per_page - 1) // per_page, 1)

    if page > total_pages:
        page = total_pages

    logs = (
        query
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    sites = BtsSite.query.order_by(BtsSite.site_name.asc()).all()
    technicians = Technician.query.order_by(Technician.name.asc()).all()

    filters = {
        'site_id': site_id,
        'tech_id': tech_id,
        'access_type': access_type,
        'result': result,
        'date_from': date_from,
        'date_to': date_to,
        'q': search,
        'incident_level': incident_level,
        'reason_code': reason_code,
        'gate_status': gate_status,
    }

    return render_template(
        'access_logs.html',
        logs=logs,
        sites=sites,
        techs=technicians,
        filters=filters,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )


# =========================================================
# EXPORT ACCESS LOG
# =========================================================

@app.route(
    '/export/access-logs',
    methods=['GET']
)
@admin_required
def export_access_logs():

    site_id = request.args.get(
        'site_id',
        ''
    ).strip()

    tech_id = request.args.get(
        'tech_id',
        ''
    ).strip()

    access_type = request.args.get(
        'access_type',
        ''
    ).strip().upper()

    result = request.args.get(
        'result',
        ''
    ).strip().upper()

    date_from = request.args.get(
        'date_from',
        ''
    ).strip()

    date_to = request.args.get(
        'date_to',
        ''
    ).strip()

    search = request.args.get('q', '').strip()
    incident_level = request.args.get('incident_level', '').strip().upper()
    reason_code = request.args.get('reason_code', '').strip().upper()
    gate_status = request.args.get('gate_status', '').strip().upper()

    query = (
        AccessLog.query
        .outerjoin(Technician, AccessLog.technician_id == Technician.technician_id)
        .outerjoin(BtsSite, AccessLog.site_id == BtsSite.site_id)
        .order_by(AccessLog.timestamp.desc(), AccessLog.id.desc())
    )

    if site_id:

        query = query.filter(
            AccessLog.site_id
            == site_id
        )

    if tech_id:

        query = query.filter(
            AccessLog.technician_id
            == tech_id
        )

    if access_type in (
        'MASUK',
        'KELUAR'
    ):

        query = query.filter(
            AccessLog.access_type
            == access_type
        )

    if result in (
        'GRANTED',
        'DENIED',
        'PENDING'
    ):

        query = query.filter(
            AccessLog.access_result
            == result
        )

    if incident_level in ('NORMAL', 'WARNING', 'HIGH', 'CRITICAL'):
        query = query.filter(AccessLog.incident_level == incident_level)

    if reason_code:
        query = query.filter(AccessLog.reason_code.ilike(f'%{reason_code}%'))

    if gate_status in ('OPEN', 'CLOSED'):
        query = query.filter(AccessLog.gate_status == gate_status)

    if search:
        like = f'%{search}%'
        query = query.filter(or_(
            AccessLog.site_id.ilike(like),
            AccessLog.technician_id.ilike(like),
            Technician.name.ilike(like),
            BtsSite.site_name.ilike(like),
            AccessLog.description.ilike(like),
            AccessLog.device_id.ilike(like),
            AccessLog.reason_code.ilike(like),
        ))

    start_date = parse_date(
        date_from
    )

    if start_date:

        query = query.filter(
            AccessLog.timestamp
            >= start_date
        )

    end_date = parse_date(
        date_to
    )

    if end_date:

        end_date += timedelta(
            days=1
        )

        query = query.filter(
            AccessLog.timestamp
            < end_date
        )

    logs = query.all()

    workbook = openpyxl.Workbook()

    worksheet = workbook.active

    worksheet.title = (
        'Access Log'
    )

    headers = [
        'No',
        'Waktu UTC',
        'Site ID',
        'Site Name',
        'Teknisi ID',
        'Teknisi',
        'Jenis Akses',
        'Face',
        'Face Distance',
        'OTP',
        'Hasil',
        'Reason Code',
        'Incident Level',
        'Gate Status',
        'Device / IP',
        'Keterangan'
    ]

    worksheet.append(
        headers
    )

    header_fill = PatternFill(
        fill_type='solid',
        fgColor='212529'
    )

    for cell in worksheet[1]:

        cell.font = Font(
            bold=True,
            color='FFFFFF'
        )

        cell.fill = header_fill

        cell.alignment = Alignment(
            horizontal='center',
            vertical='center'
        )

    for number, log in enumerate(
        logs,
        start=1
    ):

        worksheet.append([

            number,

            (
                log.timestamp.strftime(
                    '%Y-%m-%d %H:%M:%S'
                )
                if log.timestamp
                else ''
            ),

            (
                log.site.site_id
                if log.site
                else log.site_id
                or ''
            ),

            (
                log.site.site_name
                if log.site
                else ''
            ),

            (
                log.technician.technician_id
                if log.technician
                else log.technician_id
                or ''
            ),

            (
                log.technician.name
                if log.technician
                else 'Tidak dikenali'
            ),

            log.access_type
            or '',

            log.face_result
            or '',

            (
                round(
                    log.face_distance,
                    4
                )
                if log.face_distance
                is not None
                else ''
            ),

            log.otp_result
            or '',

            log.access_result
            or '',

            log.reason_code
            or '',

            log.incident_level
            or '',

            log.gate_status
            or '',

            log.device_id
            or '',

            log.description
            or ''
        ])

    for column_cells in worksheet.columns:

        max_length = 0

        for cell in column_cells:

            value = (
                str(cell.value)
                if cell.value is not None
                else ''
            )

            max_length = max(
                max_length,
                len(value)
            )

        column_letter = (
            openpyxl.utils.get_column_letter(
                column_cells[0].column
            )
        )

        worksheet.column_dimensions[
            column_letter
        ].width = min(
            max(
                15,
                max_length + 2
            ),
            50
        )

    excel_io = BytesIO()

    workbook.save(
        excel_io
    )

    excel_io.seek(0)

    db.session.add(
        ActivityLog(
            username=current_user.username,
            action='EXPORT_ACCESS_LOG',
            description=(
                'Mengunduh Access Log ke Excel.'
            ),
            incident_level='NORMAL'
        )
    )

    db.session.commit()

    response = app.response_class(
        excel_io.read(),

        mimetype=(
            'application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet'
        )
    )

    response.headers[
        'Content-Disposition'
    ] = (
        'attachment; '
        'filename=access_log_bts.xlsx'
    )

    return response


# =========================================================
# BERITA ACARA AKSES (BAA) - FITUR ENTERPRISE 5
# =========================================================

@app.route('/media/access/<int:log_id>', methods=['GET'])
@login_required_any
def access_evidence(log_id):
    log = AccessLog.query.get_or_404(log_id)
    if not _can_access_technician_record(log.technician_id):
        return 'Akses ditolak.', 403
    path = _resolve_private_media(log.evidence_image, 'evidence')
    if not path:
        return 'Bukti tidak ditemukan.', 404
    return send_file(path, conditional=True, max_age=0)


@app.route('/media/work-order/<int:wo_id>', methods=['GET'])
@login_required_any
def work_order_evidence(wo_id):
    work_order = WorkOrder.query.get_or_404(wo_id)
    if not _can_access_technician_record(work_order.technician_id):
        return 'Akses ditolak.', 403
    path = _resolve_private_media(work_order.evidence_image, 'work_orders')
    if not path:
        return 'Bukti tidak ditemukan.', 404
    return send_file(path, conditional=True, max_age=0)

@app.route('/access-logs/<int:log_id>/berita-acara', methods=['GET'])
@login_required_any
def view_berita_acara(log_id):
    log = AccessLog.query.get_or_404(log_id)
    if not _can_access_technician_record(log.technician_id):
        return 'Akses ditolak.', 403
    site = log.site or BtsSite.query.filter_by(site_id=log.site_id).first()
    tech = None
    if log.technician_id:
        tech = Technician.query.filter_by(technician_id=log.technician_id).first()

    is_embedded = request.args.get('embedded', '0') == '1'

    return render_template(
        'berita_acara.html',
        log=log,
        site=site,
        tech=tech,
        is_embedded=is_embedded
    )


@app.route('/access-logs/<int:log_id>/berita-acara/pdf', methods=['GET'])
@login_required_any
def download_berita_acara_pdf(log_id):
    log = AccessLog.query.get_or_404(log_id)
    if not _can_access_technician_record(log.technician_id):
        return 'Akses ditolak.', 403
    site = log.site or BtsSite.query.filter_by(site_id=log.site_id).first()
    tech = None
    if log.technician_id:
        tech = Technician.query.filter_by(technician_id=log.technician_id).first()

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    except ImportError:
        flash("Pustaka ReportLab belum terpasang.", "danger")
        return redirect(url_for('view_berita_acara', log_id=log_id))

    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=13,
        alignment=1,
        spaceAfter=4,
        textColor=colors.HexColor('#0F172A')
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        alignment=1,
        textColor=colors.HexColor('#64748B'),
        spaceAfter=12
    )
    kop_company = ParagraphStyle(
        'KopCompany',
        fontName='Helvetica-Bold',
        fontSize=10,
        textColor=colors.HexColor('#0F172A')
    )
    section_head = ParagraphStyle(
        'SectionHead',
        fontName='Helvetica-Bold',
        fontSize=8,
        textColor=colors.white,
        spaceAfter=2
    )
    cell_bold = ParagraphStyle('CellBold', fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor('#334155'))
    cell_val = ParagraphStyle('CellVal', fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#0F172A'))

    story = []

    kop_data = [
        [
            Paragraph("<b>PT TELEKOMUNIKASI INFRASTRUKTUR INDONESIA</b><br/><font size=7 color='#64748B'>DIVISI OPERASIONAL KEAMANAN MENARA & NOC (SMART GATE SYSTEM)<br/>Jl. Telekomunikasi No. 01, Jakarta 12950 &bull; ISO/IEC 27001 Certified</font>", kop_company),
            Paragraph("<b>BERITA ACARA RESMI</b><br/><font size=7 color='#64748B'>Kepatuhan Keamanan Fisik</font>", ParagraphStyle('KopRight', fontName='Helvetica', fontSize=8, alignment=2))
        ]
    ]
    t_kop = Table(kop_data, colWidths=[380, 142])
    t_kop.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(t_kop)
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#0F172A'), spaceAfter=10))

    story.append(Paragraph("BERITA ACARA AKSES GERBANG SITE BTS", title_style))
    baa_num = f"Nomor: BAA/BTS/{log.site_id}/{log.timestamp.strftime('%Y%m%d')}/{log.id:04d}"
    story.append(Paragraph(baa_num, subtitle_style))

    # I. DATA SITE
    h1 = Table([[Paragraph("I. DATA LOKASI SITE BTS", section_head)]], colWidths=[522])
    h1.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#1E293B')), ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4)]))
    story.append(h1)

    site_rows = [
        [Paragraph("Site ID", cell_bold), Paragraph(str(site.site_id if site else log.site_id), cell_val)],
        [Paragraph("Nama Menara BTS", cell_bold), Paragraph(str(site.site_name if site else '-'), cell_val)],
        [Paragraph("Alamat / Lokasi", cell_bold), Paragraph(str(site.location if site else '-'), cell_val)],
        [Paragraph("Koordinat GPS", cell_bold), Paragraph(f"{site.latitude if site and site.latitude else '-'}, {site.longitude if site and site.longitude else '-'}", cell_val)],
        [Paragraph("Status Tower", cell_bold), Paragraph(str(site.status if site else 'Normal'), cell_val)],
    ]
    t_site = Table(site_rows, colWidths=[140, 382])
    t_site.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F8FAFC')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t_site)
    story.append(Spacer(1, 8))

    # II. DATA TEKNISI
    h2 = Table([[Paragraph("II. DATA PERSONEL TEKNISI PEMOHON", section_head)]], colWidths=[522])
    h2.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#1E293B')), ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4)]))
    story.append(h2)

    tech_rows = [
        [Paragraph("ID Teknisi", cell_bold), Paragraph(str(tech.technician_id if tech else (log.technician_id or '-')), cell_val)],
        [Paragraph("Nama Lengkap", cell_bold), Paragraph(str(tech.name if tech else '-'), cell_val)],
        [Paragraph("Jabatan / Posisi", cell_bold), Paragraph(str(tech.position if tech else 'Teknisi Lapangan'), cell_val)],
        [Paragraph("Nomor Telepon", cell_bold), Paragraph(str(tech.phone if tech else '-'), cell_val)],
    ]
    t_tech = Table(tech_rows, colWidths=[140, 382])
    t_tech.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F8FAFC')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t_tech)
    story.append(Spacer(1, 8))

    # III. HASIL VERIFIKASI
    h3 = Table([[Paragraph("III. HASIL PEMERIKSAAN MULTI-FAKTOR & KEPUTUSAN AKSES", section_head)]], colWidths=[522])
    h3.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#1E293B')), ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4)]))
    story.append(h3)

    result_color = "#10B981" if log.access_result == "GRANTED" else "#EF4444"
    res_text = f"<b><font color='{result_color}'>{log.access_result} ({'DIIZINKAN' if log.access_result == 'GRANTED' else 'DITOLAK'})</font></b>"

    auth_rows = [
        [Paragraph("Waktu Permintaan", cell_bold), Paragraph(log.timestamp.strftime('%d/%m/%Y %H:%M:%S WIB'), cell_val)],
        [Paragraph("Jenis Akses", cell_bold), Paragraph(str(log.access_type), cell_val)],
        [Paragraph("Face Biometric Result", cell_bold), Paragraph(f"{log.face_result or '-'} (Distance: {log.face_distance:.3f})" if log.face_distance else str(log.face_result or '-'), cell_val)],
        [Paragraph("Anti-Spoofing / Liveness", cell_bold), Paragraph("BLOCKED (Foto Palsu Terdeteksi)" if 'SPOOF' in (log.reason_code or '') else "PASSED (Live Face Verified)", cell_val)],
        [Paragraph("TOTP 6-Digit Token", cell_bold), Paragraph(str(log.otp_result or '-'), cell_val)],
        [Paragraph("GPS Geofencing Status", cell_bold), Paragraph("DITOLAK DI LUAR GEOFENCE" if log.reason_code == 'OUT_OF_GEOFENCE' else "TERVERIFIKASI DI AREA BTS", cell_val)],
        [Paragraph("Keputusan Akses", cell_bold), Paragraph(res_text, cell_val)],
        [Paragraph("Status Aktuator Gate", cell_bold), Paragraph("OPEN (Dibuka 10 Detik)" if log.gate_status == 'OPEN' or log.access_result == 'GRANTED' else "LOCKED (Terkunci Rapat)", cell_val)],
        [Paragraph("Keterangan Reason", cell_bold), Paragraph(f"[{log.reason_code or '-'}] {log.description or '-'}", cell_val)],
    ]
    t_auth = Table(auth_rows, colWidths=[140, 382])
    t_auth.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F8FAFC')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t_auth)
    story.append(Spacer(1, 14))

    sig_data = [
        [
            Paragraph("<b>Teknisi Pemohon Akses,</b><br/><br/><br/><br/><b><u>" + str(tech.name if tech else '-') + "</u></b><br/><font size=7 color='#64748B'>ID: " + str(tech.technician_id if tech else '-') + "</font>", cell_val),
            Paragraph("<b>Security & NOC Controller,</b><br/><br/><br/><br/><b><u>SISTEM GERBANG SMART BTS</u></b><br/><font size=7 color='#64748B'>NOC Automated Auth Verified</font>", cell_val)
        ]
    ]
    t_sig = Table(sig_data, colWidths=[261, 261])
    t_sig.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(t_sig)

    doc.build(story)
    output.seek(0)

    filename = f"Berita_Acara_Akses_{log.site_id}_{log.timestamp.strftime('%Y%m%d')}_{log.id:04d}.pdf"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf"
    )


# =========================================================
# BERITA ACARA MANAGEMENT HUB (BAA & BAP) - FITUR ENTERPRISE
# =========================================================

@app.route('/berita-acara', methods=['GET'])
@login_required_any
def berita_acara_list():
    is_admin = current_user.is_authenticated
    worker_id = session.get('worker_id')
    site_id_filter = request.args.get('site_id', '').strip()
    doc_type = request.args.get('type', 'all').strip().lower()

    sites = BtsSite.query.order_by(BtsSite.site_name.asc()).all()
    technicians = Technician.query.order_by(Technician.name.asc()).all() if is_admin else []

    # 1. BAA - Berita Acara Akses (from AccessLog)
    access_query = AccessLog.query
    if not is_admin and worker_id:
        access_query = access_query.filter(
            (AccessLog.technician_id == worker_id) | 
            (AccessLog.technician_id == session.get('auth_username'))
        )
    if site_id_filter:
        access_query = access_query.filter_by(site_id=site_id_filter)
    
    baa_logs = access_query.order_by(AccessLog.timestamp.desc()).limit(150).all()

    # 2. BAP - Berita Acara Pekerjaan (from WorkOrder)
    wo_query = WorkOrder.query
    if not is_admin and worker_id:
        wo_query = wo_query.filter_by(technician_id=worker_id)
    if site_id_filter:
        wo_query = wo_query.filter_by(site_id=site_id_filter)
    
    bap_orders = wo_query.order_by(WorkOrder.scheduled_date.desc(), WorkOrder.id.desc()).limit(100).all()

    total_baa = access_query.count()
    total_granted = access_query.filter_by(access_result='GRANTED').count()
    total_bap_completed = wo_query.filter(WorkOrder.status.in_(['COMPLETED', 'VERIFIED'])).count()

    return render_template(
        'berita_acara_list.html',
        baa_logs=baa_logs,
        bap_orders=bap_orders,
        sites=sites,
        technicians=technicians,
        site_id_filter=site_id_filter,
        doc_type=doc_type,
        total_baa=total_baa,
        total_granted=total_granted,
        total_bap_completed=total_bap_completed,
        is_admin=is_admin
    )


@app.route('/work-orders/<int:wo_id>/berita-acara', methods=['GET'])
@login_required_any
def view_berita_acara_pekerjaan(wo_id):
    wo = WorkOrder.query.get_or_404(wo_id)
    if not _can_access_technician_record(wo.technician_id):
        return 'Akses ditolak.', 403
    site = BtsSite.query.filter_by(site_id=wo.site_id).first()
    tech = Technician.query.filter_by(technician_id=wo.technician_id).first()
    is_embedded = request.args.get('embedded', '0') == '1'

    return render_template(
        'berita_acara_pekerjaan.html',
        wo=wo,
        site=site,
        tech=tech,
        is_embedded=is_embedded
    )


@app.route('/work-orders/<int:wo_id>/berita-acara/pdf', methods=['GET'])
@login_required_any
def download_berita_acara_pekerjaan_pdf(wo_id):
    wo = WorkOrder.query.get_or_404(wo_id)
    if not _can_access_technician_record(wo.technician_id):
        return 'Akses ditolak.', 403
    site = BtsSite.query.filter_by(site_id=wo.site_id).first()
    tech = Technician.query.filter_by(technician_id=wo.technician_id).first()

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    except ImportError:
        flash("Pustaka ReportLab belum terpasang.", "danger")
        return redirect(url_for('view_berita_acara_pekerjaan', wo_id=wo_id))

    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=13,
        alignment=1,
        spaceAfter=4,
        textColor=colors.HexColor('#0F172A')
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        alignment=1,
        textColor=colors.HexColor('#64748B'),
        spaceAfter=12
    )
    kop_company = ParagraphStyle(
        'KopCompany',
        fontName='Helvetica-Bold',
        fontSize=10,
        textColor=colors.HexColor('#0F172A')
    )
    section_head = ParagraphStyle(
        'SectionHead',
        fontName='Helvetica-Bold',
        fontSize=8,
        textColor=colors.white,
        spaceAfter=2
    )
    cell_bold = ParagraphStyle('CellBold', fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor('#334155'))
    cell_val = ParagraphStyle('CellVal', fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#0F172A'))

    story = []

    kop_data = [
        [
            Paragraph("<b>PT TELEKOMUNIKASI INFRASTRUKTUR INDONESIA</b><br/><font size=7 color='#64748B'>DIVISI OPERASIONAL KEAMANAN MENARA & NOC (SMART GATE SYSTEM)<br/>Jl. Telekomunikasi No. 01, Jakarta 12950 &bull; ISO/IEC 27001 Certified</font>", kop_company),
            Paragraph("<b>BERITA ACARA RESMI</b><br/><font size=7 color='#64748B'>Penyelesaian Pekerjaan (BAP)</font>", ParagraphStyle('KopRight', fontName='Helvetica', fontSize=8, alignment=2))
        ]
    ]
    t_kop = Table(kop_data, colWidths=[380, 142])
    t_kop.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(t_kop)
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#0F172A'), spaceAfter=10))

    story.append(Paragraph("BERITA ACARA PENYELESAIAN PEKERJAAN (BAP)", title_style))
    bap_num = f"Nomor: BAP/WO/{wo.site_id}/{wo.scheduled_date.strftime('%Y%m%d') if wo.scheduled_date else '2026'}/{wo.id:04d}"
    story.append(Paragraph(bap_num, subtitle_style))

    # I. DATA SITE
    h1 = Table([[Paragraph("I. DATA LOKASI SITE BTS", section_head)]], colWidths=[522])
    h1.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#1E293B')), ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4)]))
    story.append(h1)

    site_rows = [
        [Paragraph("Site ID", cell_bold), Paragraph(str(site.site_id if site else wo.site_id), cell_val)],
        [Paragraph("Nama Menara BTS", cell_bold), Paragraph(str(site.site_name if site else wo.site_name), cell_val)],
        [Paragraph("Alamat / Lokasi", cell_bold), Paragraph(str(site.location if site else '-'), cell_val)],
        [Paragraph("Status Tower", cell_bold), Paragraph(str(site.status if site else 'Normal'), cell_val)],
    ]
    t_site = Table(site_rows, colWidths=[140, 382])
    t_site.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F8FAFC')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t_site)
    story.append(Spacer(1, 8))

    # II. DATA TEKNISI
    h2 = Table([[Paragraph("II. DATA PERSONEL TEKNISI PELAKSANA", section_head)]], colWidths=[522])
    h2.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#1E293B')), ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4)]))
    story.append(h2)

    tech_rows = [
        [Paragraph("ID Teknisi", cell_bold), Paragraph(str(tech.technician_id if tech else wo.technician_id), cell_val)],
        [Paragraph("Nama Lengkap", cell_bold), Paragraph(str(tech.name if tech else wo.technician_name), cell_val)],
        [Paragraph("Jabatan / Posisi", cell_bold), Paragraph(str(tech.position if tech else 'Field Maintenance Engineer'), cell_val)],
        [Paragraph("Nomor Telepon", cell_bold), Paragraph(str(tech.phone if tech else '-'), cell_val)],
    ]
    t_tech = Table(tech_rows, colWidths=[140, 382])
    t_tech.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F8FAFC')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t_tech)
    story.append(Spacer(1, 8))

    # III. RINCIAN PEKERJAAN
    h3 = Table([[Paragraph("III. RINCIAN PEKERJAAN & REALISASI LAPANGAN", section_head)]], colWidths=[522])
    h3.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#1E293B')), ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4)]))
    story.append(h3)

    wo_rows = [
        [Paragraph("Nomor Tiket SPK", cell_bold), Paragraph(str(wo.ticket_number), cell_val)],
        [Paragraph("Tujuan / Scope Kerja", cell_bold), Paragraph(str(wo.purpose), cell_val)],
        [Paragraph("Tanggal Terjadwal", cell_bold), Paragraph(wo.scheduled_date.strftime('%d/%m/%Y') if wo.scheduled_date else '-', cell_val)],
        [Paragraph("Waktu Dimulai", cell_bold), Paragraph(wo.started_at.strftime('%d/%m/%Y %H:%M:%S WIB') if wo.started_at else '-', cell_val)],
        [Paragraph("Waktu Diselesaikan", cell_bold), Paragraph(wo.completed_at.strftime('%d/%m/%Y %H:%M:%S WIB') if wo.completed_at else '-', cell_val)],
        [Paragraph("Status Akhir", cell_bold), Paragraph(f"<b><font color='#10B981'>{wo.status}</font></b>", cell_val)],
        [Paragraph("Catatan Penyelesaian", cell_bold), Paragraph(str(wo.completion_note or 'Pekerjaan selesai sesuai SOP.'), cell_val)],
        [Paragraph("Verifikator NOC", cell_bold), Paragraph(str(wo.verified_by or 'NOC Automatic Compliance'), cell_val)],
    ]
    t_wo = Table(wo_rows, colWidths=[140, 382])
    t_wo.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F8FAFC')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t_wo)
    story.append(Spacer(1, 14))

    sig_data = [
        [
            Paragraph("<b>Teknisi Pelaksana,</b><br/><br/><br/><br/><b><u>" + str(tech.name if tech else wo.technician_name) + "</u></b><br/><font size=7 color='#64748B'>ID: " + str(wo.technician_id) + "</font>", cell_val),
            Paragraph("<b>Pengawas Operasional & NOC,</b><br/><br/><br/><br/><b><u>" + str(wo.verified_by or 'SUPERVISOR NOC') + "</u></b><br/><font size=7 color='#64748B'>Validated Compliance Report</font>", cell_val)
        ]
    ]
    t_sig = Table(sig_data, colWidths=[261, 261])
    t_sig.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(t_sig)

    doc.build(story)
    output.seek(0)

    filename = f"Berita_Acara_Pekerjaan_{wo.site_id}_{wo.ticket_number}.pdf"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf"
    )


# =========================================================
# EXPORT ACTIVITY LOG
# =========================================================

@app.route(
    '/export/logs',
    methods=['GET']
)
@admin_required
def export_logs():

    logs = (
        ActivityLog.query
        .order_by(
            ActivityLog.timestamp.desc()
        )
        .all()
    )

    workbook = openpyxl.Workbook()

    worksheet = workbook.active

    worksheet.title = (
        'Log Aktivitas'
    )

    headers = [
        'No',
        'Waktu UTC',
        'Pengguna',
        'Aksi',
        'Incident Level',
        'Device',
        'Keterangan'
    ]

    worksheet.append(
        headers
    )

    header_fill = PatternFill(
        fill_type='solid',
        fgColor='212529'
    )

    for cell in worksheet[1]:

        cell.font = Font(
            bold=True,
            color='FFFFFF'
        )

        cell.fill = header_fill

        cell.alignment = Alignment(
            horizontal='center',
            vertical='center'
        )

    for number, log in enumerate(
        logs,
        start=1
    ):

        worksheet.append([

            number,

            (
                log.timestamp.strftime(
                    '%Y-%m-%d %H:%M:%S'
                )
                if log.timestamp
                else ''
            ),

            log.username
            or '',

            log.action
            or '',

            log.incident_level
            or '',

            log.device_id
            or '',

            log.description
            or ''
        ])

    for column_cells in worksheet.columns:

        max_length = 0

        for cell in column_cells:

            value = (
                str(cell.value)
                if cell.value is not None
                else ''
            )

            max_length = max(
                max_length,
                len(value)
            )

        column_letter = (
            openpyxl.utils.get_column_letter(
                column_cells[0].column
            )
        )

        worksheet.column_dimensions[
            column_letter
        ].width = min(
            max(
                15,
                max_length + 2
            ),
            60
        )

    excel_io = BytesIO()

    workbook.save(
        excel_io
    )

    excel_io.seek(0)

    db.session.add(
        ActivityLog(
            username=current_user.username,
            action='EXPORT_ACTIVITY_LOG',
            description=(
                'Mengunduh Activity Log ke Excel.'
            ),
            incident_level='NORMAL'
        )
    )

    db.session.commit()

    response = app.response_class(
        excel_io.read(),
        mimetype=(
            'application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet'
        )
    )

    response.headers[
        'Content-Disposition'
    ] = (
        'attachment; '
        'filename=log_aktivitas_bts.xlsx'
    )

    return response


# =========================================================
# SECURITY INCIDENTS
# =========================================================

@app.route(
    '/security-incidents',
    methods=['GET']
)
@admin_required
def security_incidents():

    selected_level = request.args.get(
        'level',
        ''
    ).strip().upper()

    levels = (
        'WARNING',
        'HIGH',
        'CRITICAL'
    )

    query = (
        AccessLog.query
        .filter(
            AccessLog.incident_level.in_(
                levels
            )
        )
        .order_by(
            AccessLog.timestamp.desc()
        )
    )

    if selected_level in levels:

        query = query.filter(
            AccessLog.incident_level
            == selected_level
        )

    incidents = query.limit(
        200
    ).all()

    return render_template(
        'security_incidents.html',

        incidents=incidents,

        selected_level=selected_level
    )


# =========================================================
# SECURITY ALERTS
# =========================================================

@app.route(
    '/security-alerts',
    methods=['GET']
)
@admin_required
def security_alerts():

    start_today = datetime.utcnow().replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    incidents = (
        AccessLog.query
        .filter(
            AccessLog.timestamp
            >= start_today,

            AccessLog.incident_level.in_(
                (
                    'WARNING',
                    'HIGH',
                    'CRITICAL'
                )
            )
        )
        .order_by(
            AccessLog.timestamp.desc()
        )
        .limit(100)
        .all()
    )

    alerts = []

    for log in incidents:

        if log.incident_level == 'CRITICAL':

            title = (
                'Insiden Kritis'
            )

        elif log.incident_level == 'HIGH':

            title = (
                'Peringatan Keamanan Tinggi'
            )

        else:

            title = (
                'Peringatan Keamanan'
            )

        alerts.append({

            'level': (
                log.incident_level
            ),

            'title': title,

            'message': (
                log.description
                or log.reason_code
                or 'Aktivitas keamanan terdeteksi.'
            ),

            'timestamp': log.timestamp,

            'log': log
        })

    return render_template(
        'security_alerts.html',
        alerts=alerts
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route('/logout', methods=['POST'])
def logout():

    username = (
        current_user.username
        if current_user.is_authenticated
        else session.get(
            'worker_name',
            session.get(
                'worker_id',
                'Unknown'
            )
        )
    )

    if current_user.is_authenticated:

        logout_user()

    session.clear()

    try:

        db.session.add(
            ActivityLog(
                username=username,
                action='LOGOUT',
                description=(
                    'User logout dari sistem.'
                ),
                incident_level='NORMAL'
            )
        )

        db.session.commit()

    except Exception as exc:

        db.session.rollback()

        print(
            'LOGOUT ERROR:',
            exc
        )

    flash(
        'Berhasil logout.',
        'success'
    )

    return redirect(
        url_for('login')
    )


# =========================================================
# DELETE / DEACTIVATE TECHNICIAN
# =========================================================

@app.route(
    '/technicians/delete/<string:tech_id>',
    methods=['GET']
)
@admin_required
def delete_technician(
    tech_id
):

    technician = (
        Technician.query
        .filter_by(
            technician_id=tech_id
        )
        .first_or_404()
    )

    # -----------------------------------------------------
    # Jangan menghapus fisik jika sudah punya log
    # -----------------------------------------------------

    has_logs = (
        AccessLog.query
        .filter_by(
            technician_id=tech_id
        )
        .first()
    )

    if has_logs:

        technician.status = (
            'Inactive'
        )

        db.session.add(
            ActivityLog(
                username=current_user.username,
                action='DEACTIVATE_TECHNICIAN',
                description=(
                    f'Teknisi {technician.name} '
                    f'({technician.technician_id}) '
                    'dinonaktifkan karena memiliki riwayat akses.'
                ),
                incident_level='WARNING'
            )
        )

        db.session.commit()

        flash(
            (
                'Teknisi memiliki riwayat akses. '
                'Status diubah menjadi Inactive.'
            ),
            'warning'
        )

        return redirect(
            url_for('dashboard')
        )

    try:

        technician_name = (
            technician.name
        )

        technician_code = (
            technician.technician_id
        )

        db.session.delete(
            technician
        )

        db.session.add(
            ActivityLog(
                username=current_user.username,
                action='DELETE_TECHNICIAN',
                description=(
                    f'Menghapus teknisi '
                    f'{technician_name} '
                    f'({technician_code}).'
                ),
                incident_level='WARNING'
            )
        )

        db.session.commit()

        flash(
            'Teknisi berhasil dihapus.',
            'success'
        )

    except Exception as exc:

        db.session.rollback()

        print(
            'DELETE TECH ERROR:',
            exc
        )

        flash(
            'Gagal menghapus teknisi.',
            'danger'
        )

    return redirect(
        url_for('dashboard')
    )


# =========================================================
# STEP 21-22: SECURITY ANALYTICS + BTS MAP
# =========================================================

def _get_period_logs(days):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7

    if days not in (7, 30, 90):
        days = 7

    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=days)

    logs = (
        AccessLog.query
        .filter(AccessLog.timestamp >= start_time)
        .filter(AccessLog.timestamp <= end_time)
        .order_by(AccessLog.timestamp.desc())
        .all()
    )
    return days, start_time, end_time, logs


@app.route('/security-analytics')
@admin_required
def security_analytics():
    days, start_time, end_time, logs = _get_period_logs(
        request.args.get('days', 7)
    )

    total = len(logs)
    granted = sum(1 for log in logs if str(log.access_result or '').upper() == 'GRANTED')
    denied = sum(1 for log in logs if str(log.access_result or '').upper() == 'DENIED')
    pending = sum(1 for log in logs if str(log.access_result or '').upper() == 'PENDING')

    grant_rate = round((granted / total * 100), 1) if total else 0
    deny_rate = round((denied / total * 100), 1) if total else 0

    successful_face = sum(
        1 for log in logs
        if str(log.face_result or '').upper() in ('SUCCESS', 'MATCH', 'VERIFIED')
    )
    successful_otp = sum(
        1 for log in logs
        if str(log.otp_result or '').upper() in ('SUCCESS', 'VALID', 'VERIFIED')
    )
    face_failure = total - successful_face
    otp_failure = total - successful_otp

    incidents = {
        'NORMAL': 0,
        'WARNING': 0,
        'HIGH': 0,
        'CRITICAL': 0,
    }
    for log in logs:
        level = str(log.incident_level or 'NORMAL').upper()
        incidents[level if level in incidents else 'NORMAL'] += 1

    security_incidents = incidents['WARNING'] + incidents['HIGH'] + incidents['CRITICAL']

    trend = {}
    for offset in range(days):
        day = (start_time + timedelta(days=offset)).date()
        trend[day.isoformat()] = {'total': 0, 'granted': 0, 'denied': 0, 'incident': 0}

    for log in logs:
        if not log.timestamp:
            continue
        key = log.timestamp.date().isoformat()
        if key not in trend:
            continue
        trend[key]['total'] += 1
        result = str(log.access_result or '').upper()
        if result == 'GRANTED':
            trend[key]['granted'] += 1
        elif result == 'DENIED':
            trend[key]['denied'] += 1
        level = str(log.incident_level or 'NORMAL').upper()
        if level in ('WARNING', 'HIGH', 'CRITICAL'):
            trend[key]['incident'] += 1

    trend_labels = list(trend.keys())
    trend_total = [trend[k]['total'] for k in trend_labels]
    trend_granted = [trend[k]['granted'] for k in trend_labels]
    trend_denied = [trend[k]['denied'] for k in trend_labels]
    trend_incident = [trend[k]['incident'] for k in trend_labels]

    reason_counter = {}
    for log in logs:
        reason = str(log.reason_code or '').strip().upper() or 'UNSPECIFIED'
        reason_counter[reason] = reason_counter.get(reason, 0) + 1
    top_reasons = sorted(reason_counter.items(), key=lambda x: (-x[1], x[0]))[:10]

    site_counter = {}
    for log in logs:
        site_id = str(log.site_id or '').strip() or 'UNKNOWN'
        site_counter[site_id] = site_counter.get(site_id, 0) + 1
    top_site_ids = sorted(site_counter.items(), key=lambda x: (-x[1], x[0]))[:10]
    site_name_map = {}
    ids = [sid for sid, _ in top_site_ids if sid != 'UNKNOWN']
    if ids:
        site_name_map = {str(x.site_id): (x.site_name or str(x.site_id)) for x in BtsSite.query.filter(BtsSite.site_id.in_(ids)).all()}
    top_sites = [
        {'site_id': sid, 'site_name': site_name_map.get(sid, sid), 'count': count}
        for sid, count in top_site_ids
    ]

    tech_counter = {}
    for log in logs:
        tech_id = str(log.technician_id or '').strip() or 'UNKNOWN'
        tech_counter[tech_id] = tech_counter.get(tech_id, 0) + 1
    top_tech_ids = sorted(tech_counter.items(), key=lambda x: (-x[1], x[0]))[:10]
    tech_name_map = {}
    tech_ids = [tid for tid, _ in top_tech_ids if tid != 'UNKNOWN']
    if tech_ids:
        tech_name_map = {str(x.technician_id): (x.name or str(x.technician_id)) for x in Technician.query.filter(Technician.technician_id.in_(tech_ids)).all()}
    top_technicians = [
        {'technician_id': tid, 'name': tech_name_map.get(tid, tid), 'count': count}
        for tid, count in top_tech_ids
    ]

    latest_incidents = [
        log for log in logs
        if str(log.incident_level or 'NORMAL').upper() in ('WARNING', 'HIGH', 'CRITICAL')
    ][:10]

    return render_template(
        'security_analytics.html',
        days=days,
        start_time=start_time,
        end_time=end_time,
        total=total,
        granted=granted,
        denied=denied,
        pending=pending,
        grant_rate=grant_rate,
        deny_rate=deny_rate,
        successful_face=successful_face,
        face_failure=face_failure,
        successful_otp=successful_otp,
        otp_failure=otp_failure,
        incidents=incidents,
        security_incidents=security_incidents,
        trend_labels=trend_labels,
        trend_total=trend_total,
        trend_granted=trend_granted,
        trend_denied=trend_denied,
        trend_incident=trend_incident,
        top_reasons=top_reasons,
        top_sites=top_sites,
        top_technicians=top_technicians,
        latest_incidents=latest_incidents,
    )


@app.route('/bts-map')
@admin_required
def bts_map():
    sites = BtsSite.query.order_by(BtsSite.site_name.asc()).all()
    now = datetime.utcnow()
    today = now.date()
    map_sites = []

    for site in sites:
        try:
            latitude = float(site.latitude)
            longitude = float(site.longitude)
        except (TypeError, ValueError):
            continue

        logs = AccessLog.query.filter(
            AccessLog.site_id == site.site_id,
            AccessLog.timestamp >= datetime.combine(today, datetime.min.time()),
            AccessLog.timestamp <= datetime.combine(today, datetime.max.time()),
        ).order_by(AccessLog.timestamp.desc()).all()

        denied_today = sum(1 for log in logs if str(log.access_result or '').upper() == 'DENIED')
        incident_count = sum(
            1 for log in logs
            if str(log.incident_level or 'NORMAL').upper() in ('WARNING', 'HIGH', 'CRITICAL')
        )
        critical_today = sum(1 for log in logs if str(log.incident_level or '').upper() == 'CRITICAL')

        if critical_today > 0:
            security_status = 'CRITICAL'
        elif incident_count > 0 or denied_today > 0:
            security_status = 'WARNING'
        else:
            security_status = 'NORMAL'

        last = logs[0] if logs else None

        map_sites.append({
            'site_id': site.site_id,
            'site_name': site.site_name or site.site_id,
            'location': site.location or '-',
            'latitude': latitude,
            'longitude': longitude,
            'status': site.status or 'Normal (Up)',
            'temperature': getattr(site, 'temperature', None) or '-',
            'power_output': getattr(site, 'power_output', None) or '-',
            'security_status': security_status,
            'access_today': len(logs),
            'granted_today': sum(1 for log in logs if str(log.access_result or '').upper() == 'GRANTED'),
            'denied_today': denied_today,
            'incident_count': incident_count,
            'critical_today': critical_today,
            'inside_count': 0,
            'last_access': last.timestamp.strftime('%d/%m/%Y %H:%M') if last and last.timestamp else None,
            'last_access_type': last.access_type if last else None,
        })

    return render_template(
        'bts_map.html',
        map_sites=map_sites,
        total_sites=len(sites),
        mapped_sites=len(map_sites),
    )


# =========================================================
# WORK ORDER MANAGEMENT
# =========================================================

@app.route('/work-orders', methods=['GET', 'POST'])
@admin_required
def work_orders():
    if request.method == 'POST':
        technician_id = request.form.get('technician_id', '').strip()
        site_id = request.form.get('site_id', '').strip()
        purpose = request.form.get('purpose', '').strip()
        scheduled_date_str = request.form.get('scheduled_date', '').strip()
        notes = request.form.get('notes', '').strip()

        if not technician_id or not site_id or not purpose:
            flash('Teknisi, Site BTS, dan Tujuan Pekerjaan wajib diisi.', 'danger')
            return redirect(url_for('work_orders'))

        scheduled_date = datetime.utcnow().date()
        if scheduled_date_str:
            try:
                scheduled_date = datetime.strptime(scheduled_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        # Generate ticket number
        import random
        random_suffix = random.randint(100, 999)
        ticket_number = f"WO-{scheduled_date.strftime('%Y%m%d')}-{random_suffix}"

        wo = WorkOrder(
            ticket_number=ticket_number,
            technician_id=technician_id,
            site_id=site_id,
            scheduled_date=scheduled_date,
            purpose=purpose,
            status='APPROVED',
            notes=notes
        )
        db.session.add(wo)
        db.session.commit()

        try:
            from app.notifications import send_work_order_notification
            send_work_order_notification('CREATED', wo)
        except Exception:
            pass

        flash(f'Surat Perintah Kerja {ticket_number} berhasil dibuat.', 'success')
        return redirect(url_for('work_orders'))

    status_filter = request.args.get('status', '').strip().upper()
    technician_filter = request.args.get('technician_id', '').strip()
    orders_query = WorkOrder.query
    if status_filter in ('APPROVED', 'PENDING', 'IN_PROGRESS', 'COMPLETED', 'VERIFIED', 'CANCELLED'):
        orders_query = orders_query.filter_by(status=status_filter)
    if technician_filter:
        orders_query = orders_query.filter_by(technician_id=technician_filter)

    orders = orders_query.order_by(WorkOrder.scheduled_date.desc(), WorkOrder.id.desc()).all()
    technicians = Technician.query.filter_by(status='Active').order_by(Technician.name.asc()).all()
    sites = BtsSite.query.order_by(BtsSite.site_name.asc()).all()

    return render_template(
        'work_orders.html',
        orders=orders,
        technicians=technicians,
        sites=sites,
        today=datetime.utcnow().strftime('%Y-%m-%d'),
        status_filter=status_filter,
        technician_filter=technician_filter
    )


@app.route('/work-orders/export.csv', methods=['GET'])
@admin_required
def export_work_orders():
    status_filter = request.args.get('status', '').strip().upper()
    technician_filter = request.args.get('technician_id', '').strip()
    orders_query = WorkOrder.query
    if status_filter in ('APPROVED', 'PENDING', 'IN_PROGRESS', 'COMPLETED', 'VERIFIED', 'CANCELLED'):
        orders_query = orders_query.filter_by(status=status_filter)
    if technician_filter:
        orders_query = orders_query.filter_by(technician_id=technician_filter)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Ticket', 'Tanggal', 'Teknisi', 'Teknisi ID', 'Site', 'Site ID', 'Tujuan', 'Status', 'Mulai Pada', 'GPS Mulai', 'Catatan Penyelesaian', 'Selesai Pada', 'GPS Selesai', 'Diverifikasi Oleh'])
    for work_order in orders_query.order_by(WorkOrder.scheduled_date.desc(), WorkOrder.id.desc()).all():
        writer.writerow([
            work_order.ticket_number,
            work_order.scheduled_date.isoformat() if work_order.scheduled_date else '',
            work_order.technician_name,
            work_order.technician_id,
            work_order.site_name,
            work_order.site_id,
            work_order.purpose,
            work_order.status,
            work_order.started_at.isoformat() if work_order.started_at else '',
            f'{work_order.start_latitude or ""},{work_order.start_longitude or ""}',
            work_order.completion_note or '',
            work_order.completed_at.isoformat() if work_order.completed_at else '',
            f'{work_order.completion_latitude or ""},{work_order.completion_longitude or ""}',
            work_order.verified_by or ''
        ])
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=work-orders.csv'

# =========================================================
# WORKER: TUGAS & WORK ORDER (MENU TERPISAH)
# =========================================================
@app.route('/worker/work-orders', methods=['GET'])
@technician_required
def worker_work_orders():
    worker_id = session.get('worker_id')
    
    # Ambil semua work order khusus untuk teknisi ini
    my_work_orders = (
        WorkOrder.query.filter_by(technician_id=worker_id)
        .order_by(WorkOrder.scheduled_date.desc(), WorkOrder.id.desc())
        .all()
    )
    
    return render_template('worker_work_orders.html', my_work_orders=my_work_orders)

# =========================================================
# WORKER: RIWAYAT AKSES (MENU TERPISAH)
# =========================================================
@app.route('/worker/access-logs', methods=['GET'])
@technician_required
def worker_access_logs():
    worker_id = session.get('worker_id')
    
    # Ambil semua riwayat akses khusus untuk teknisi ini
    my_access_logs = (
        AccessLog.query.filter_by(technician_id=worker_id)
        .order_by(AccessLog.timestamp.desc())
        .all()
    )
    
    return render_template('worker_access_logs.html', my_access_logs=my_access_logs)


@app.route('/work-orders/export/<format>')
@admin_required
def export_work_orders_format(format):
    format = format.lower()
    status_filter = request.args.get('status', '').strip().upper()
    technician_filter = request.args.get('technician_id', '').strip()
    orders_query = WorkOrder.query
    if status_filter in ('APPROVED', 'PENDING', 'IN_PROGRESS', 'COMPLETED', 'VERIFIED', 'CANCELLED'):
        orders_query = orders_query.filter_by(status=status_filter)
    if technician_filter:
        orders_query = orders_query.filter_by(technician_id=technician_filter)
    orders = orders_query.order_by(WorkOrder.scheduled_date.desc(), WorkOrder.id.desc()).all()

    if format == 'xlsx':
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Work Orders'
        headers = ['Ticket', 'Tanggal', 'Teknisi', 'Teknisi ID', 'Site', 'Site ID', 'Tujuan', 'Status', 'Mulai', 'GPS Mulai', 'Catatan', 'Selesai', 'GPS Selesai', 'Diverifikasi Oleh']
        header_fill = PatternFill(start_color='102B41', end_color='102B41', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center')
        ws.column_dimensions['A'].width = 16
        ws.column_dimensions['H'].width = 14
        for row_idx, wo in enumerate(orders, 2):
            ws.cell(row=row_idx, column=1, value=wo.ticket_number)
            ws.cell(row=row_idx, column=2, value=wo.scheduled_date.isoformat() if wo.scheduled_date else '')
            ws.cell(row=row_idx, column=3, value=wo.technician_name)
            ws.cell(row=row_idx, column=4, value=wo.technician_id)
            ws.cell(row=row_idx, column=5, value=wo.site_name)
            ws.cell(row=row_idx, column=6, value=wo.site_id)
            ws.cell(row=row_idx, column=7, value=wo.purpose)
            ws.cell(row=row_idx, column=8, value=wo.status)
            ws.cell(row=row_idx, column=9, value=wo.started_at.isoformat() if wo.started_at else '')
            ws.cell(row=row_idx, column=10, value=f'{wo.start_latitude or ""},{wo.start_longitude or ""}')
            ws.cell(row=row_idx, column=11, value=wo.completion_note or '')
            ws.cell(row=row_idx, column=12, value=wo.completed_at.isoformat() if wo.completed_at else '')
            ws.cell(row=row_idx, column=13, value=f'{wo.completion_latitude or ""},{wo.completion_longitude or ""}')
            ws.cell(row=row_idx, column=14, value=wo.verified_by or '')
        ws.freeze_panes = 'A2'
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        response.headers['Content-Disposition'] = f'attachment; filename=work-orders-{datetime.now().strftime("%Y%m%d")}.xlsx'
        return response

    elif format == 'pdf':
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        output = BytesIO()
        doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
        styles = getSampleStyleSheet()
        story = []
        story.append(Paragraph('Laporan Work Order', styles['Title']))
        story.append(Paragraph(f'Dicetak pada: {datetime.now().strftime("%d/%m/%Y %H:%M")}', styles['Normal']))
        story.append(Spacer(1, 12))
        data = [['Ticket', 'Tanggal', 'Site', 'Teknisi', 'Tujuan', 'Status', 'Selesai']]
        for wo in orders:
            data.append([
                wo.ticket_number,
                wo.scheduled_date.strftime('%d/%m/%Y') if wo.scheduled_date else '',
                wo.site_name,
                wo.technician_name or '-',
                wo.purpose,
                wo.status,
                wo.completed_at.strftime('%d/%m/%Y %H:%M') if wo.completed_at else '-'
            ])
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#102B41')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(table)
        doc.build(story)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=work-orders-{datetime.now().strftime("%Y%m%d")}.pdf'
        return response

    return redirect(url_for('export_work_orders'))


@app.route('/work-orders/status/<int:wo_id>/<string:new_status>', methods=['POST'])
@admin_required
def update_work_order_status(wo_id, new_status):
    wo = WorkOrder.query.get_or_404(wo_id)
    new_status = new_status.upper()
    allowed_transitions = {
        'PENDING': {'APPROVED', 'CANCELLED'},
        'APPROVED': {'IN_PROGRESS', 'COMPLETED', 'CANCELLED'},
        'IN_PROGRESS': {'COMPLETED', 'CANCELLED'},
        'COMPLETED': {'VERIFIED', 'CANCELLED'},
    }
    if new_status in allowed_transitions.get(wo.status, set()):
        wo.status = new_status
        if new_status == 'IN_PROGRESS' and not wo.started_at:
            wo.started_at = datetime.utcnow()
        if new_status == 'VERIFIED':
            wo.verified_at = datetime.utcnow()
            wo.verified_by = current_user.username
        db.session.commit()
        try:
            from app.notifications import send_work_order_notification
            send_work_order_notification(new_status, wo)
        except Exception:
            pass
        flash(f'Status Work Order {wo.ticket_number} diubah menjadi {new_status}.', 'success')
    else:
        flash(f'Perubahan status dari {wo.status} ke {new_status} tidak diizinkan.', 'warning')
    return redirect(url_for('work_orders'))


@app.route('/worker/work-orders/<int:wo_id>/complete', methods=['POST'])
@technician_required
def complete_worker_work_order(wo_id):
    worker_id = session.get('worker_id')
    work_order = (
        WorkOrder.query
        .filter_by(
            id=wo_id,
            technician_id=worker_id
        )
        .first_or_404()
    )

    if work_order.status != 'IN_PROGRESS':
        flash(
            f'Work Order {work_order.ticket_number} harus berstatus IN_PROGRESS '
            f'sebelum dapat diselesaikan (status saat ini: {work_order.status}).',
            'warning'
        )
    else:
        completion_note = request.form.get('completion_note', '').strip()
        evidence = request.files.get('evidence_image')
        if not completion_note:
            flash('Catatan penyelesaian wajib diisi.', 'danger')
            return redirect(url_for('worker_dashboard'))

        if evidence and evidence.filename:
            allowed_types = {'image/jpeg', 'image/png', 'image/webp'}
            if evidence.mimetype not in allowed_types:
                flash('Bukti pekerjaan harus berupa JPG, PNG, atau WEBP.', 'danger')
                return redirect(url_for('worker_dashboard'))
            evidence_bytes = evidence.read()
            if len(evidence_bytes) > 2_000_000:
                flash('Ukuran bukti pekerjaan maksimal 2 MB.', 'danger')
                return redirect(url_for('worker_dashboard'))
            upload_dir = os.path.join(app.instance_path, 'uploads', 'work_orders')
            os.makedirs(upload_dir, exist_ok=True)
            ext = 'jpg' if 'jpeg' in evidence.mimetype or 'jpg' in evidence.mimetype else ('png' if 'png' in evidence.mimetype else 'webp')
            valid_image = (
                (ext == 'jpg' and evidence_bytes.startswith(b'\xff\xd8\xff'))
                or (ext == 'png' and evidence_bytes.startswith(b'\x89PNG\r\n\x1a\n'))
                or (ext == 'webp' and len(evidence_bytes) >= 12 and evidence_bytes[:4] == b'RIFF' and evidence_bytes[8:12] == b'WEBP')
            )
            if not valid_image:
                flash('Isi file tidak sesuai dengan format gambar yang dipilih.', 'danger')
                return redirect(url_for('worker_dashboard'))
            filename = f"work_order_{uuid.uuid4().hex}.{ext}"
            filepath = os.path.join(upload_dir, filename)
            with open(filepath, 'wb') as f:
                f.write(evidence_bytes)
            work_order.evidence_image = f'private:work_orders:{filename}'

        work_order.status = 'COMPLETED'
        work_order.completion_note = completion_note
        work_order.completed_at = datetime.utcnow()
        work_order.completion_latitude = request.form.get('completion_latitude', '').strip() or None
        work_order.completion_longitude = request.form.get('completion_longitude', '').strip() or None
        db.session.commit()
        try:
            from app.notifications import send_work_order_notification
            send_work_order_notification('COMPLETED', work_order)
        except Exception:
            pass
        flash(
            f'Work Order {work_order.ticket_number} ditandai selesai.',
            'success'
        )

    return redirect(url_for('worker_dashboard'))


@app.route('/worker/work-orders/<int:wo_id>/start', methods=['POST'])
@technician_required
def start_worker_work_order(wo_id):
    worker_id = session.get('worker_id')
    work_order = WorkOrder.query.filter_by(
        id=wo_id,
        technician_id=worker_id
    ).first_or_404()

    if work_order.status != 'APPROVED':
        flash(f'Work Order {work_order.ticket_number} tidak dapat dimulai dari status {work_order.status}.', 'warning')
        return redirect(url_for('worker_dashboard'))

    work_order.status = 'IN_PROGRESS'
    work_order.started_at = datetime.utcnow()
    work_order.start_latitude = request.form.get('start_latitude', '').strip() or None
    work_order.start_longitude = request.form.get('start_longitude', '').strip() or None
    db.session.commit()
    try:
        from app.notifications import send_work_order_notification
        send_work_order_notification('STARTED', work_order)
    except Exception:
        pass
    flash(f'Work Order {work_order.ticket_number} dimulai.', 'success')
    return redirect(url_for('worker_dashboard'))


@app.route('/work-orders/<int:wo_id>/verify', methods=['POST'])
@admin_required
def verify_work_order(wo_id):
    work_order = WorkOrder.query.get_or_404(wo_id)
    if work_order.status != 'COMPLETED':
        flash('Hanya work order yang sudah selesai yang dapat diverifikasi.', 'warning')
        return redirect(url_for('work_orders'))

    work_order.status = 'VERIFIED'
    work_order.verified_at = datetime.utcnow()
    work_order.verified_by = current_user.username
    db.session.commit()
    try:
        from app.notifications import send_work_order_notification
        send_work_order_notification('VERIFIED', work_order)
    except Exception:
        pass
    flash(f'Work Order {work_order.ticket_number} berhasil diverifikasi.', 'success')
    return redirect(url_for('work_orders'))


# =========================================================
# DATABASE BACKUP & LOG MAINTENANCE
# =========================================================

@app.route('/admin/backup-db')
@admin_required
def admin_backup_db():
    import os
    db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if 'sqlite' in db_uri:
        db_path = os.path.join(app.instance_path, 'app.db')
        if not os.path.exists(db_path):
            db_path = os.path.join(app.root_path, '..', 'app.db')

        if os.path.exists(db_path):
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            return send_file(
                db_path,
                as_attachment=True,
                download_name=f"bts_security_backup_{timestamp}.sqlite"
            )
        else:
            flash('File database SQLite tidak ditemukan.', 'danger')
            return redirect(url_for('dashboard'))
    else:
        flash('Fitur download backup langsung saat ini dikhususkan untuk database SQLite.', 'warning')
        return redirect(url_for('dashboard'))


@app.route('/admin/cleanup-logs', methods=['POST'])
@admin_required
def admin_cleanup_logs():
    days = request.form.get('days', 30, type=int)
    if not days or days < 7:
        flash('Periode pembersihan minimal 7 hari untuk menjaga kelengkapan audit trail.', 'warning')
        return redirect(url_for('access_logs'))

    cutoff = datetime.utcnow() - timedelta(days=days)

    try:
        # Hapus file gambar bukti fisik di disk sebelum menghapus log dari database
        logs_to_clean = AccessLog.query.filter(AccessLog.timestamp < cutoff).all()
        for old_log in logs_to_clean:
            full_path = _resolve_private_media(old_log.evidence_image, 'evidence')
            if full_path:
                try:
                    os.remove(full_path)
                except Exception:
                    pass

        deleted_count = AccessLog.query.filter(AccessLog.timestamp < cutoff).delete()
        db.session.commit()

        log = ActivityLog(
            timestamp=datetime.utcnow(),
            username=current_user.username if current_user.is_authenticated else "ADMIN",
            action="CLEANUP_LOGS",
            description=f"Membersihkan {deleted_count} access log yang lebih dari {days} hari.",
            incident_level="NORMAL"
        )
        db.session.add(log)
        db.session.commit()

        flash(f'Berhasil menghapus {deleted_count} log akses lama (lebih dari {days} hari).', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Gagal membersihkan log: {exc}', 'danger')

    return redirect(url_for('access_logs'))


def _get_external_access_base_url():
    """
    Jika server diakses via localhost atau 127.0.0.1, gunakan IP LAN lokal PC
    agar QR Code dapat langsung di-scan dan dibuka oleh smartphone di Wi-Fi yang sama.
    """
    import os
    from config import Config
    custom_base = getattr(Config, 'SERVER_BASE_URL', None) or os.environ.get('SERVER_BASE_URL', '').strip()
    if custom_base:
        return custom_base.rstrip('/')

    host_url = request.host_url.rstrip('/')
    if 'localhost' in host_url or '127.0.0.1' in host_url:
        try:
            import socket
            from urllib.parse import urlparse
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            local_ip = s.getsockname()[0]
            s.close()
            parsed = urlparse(host_url)
            port_part = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{local_ip}{port_part}"
        except Exception:
            pass
    return host_url


def generate_qr(data):
    import qrcode
    from io import BytesIO
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf.getvalue()


def generate_site_badge_qr(site, access_url):
    """
    Menghasilkan gambar Stiker/Plat Gerbang resmi beresolusi tinggi (PNG)
    khusus untuk site ini lengkap dengan Nama Site, Site ID, Lokasi, dan QR Code.
    """
    import qrcode
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont
    import os

    def _get_font(size, bold=False):
        candidates = [
            'C:/Windows/Fonts/segoeuib.ttf' if bold else 'C:/Windows/Fonts/segoeui.ttf',
            'C:/Windows/Fonts/arialbd.ttf' if bold else 'C:/Windows/Fonts/arial.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        ]
        for p in candidates:
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(access_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color='#0B1F35', back_color='white').convert('RGB')

    W, H = 520, 680
    card = Image.new('RGB', (W, H), color='#FFFFFF')
    draw = ImageDraw.Draw(card)

    # Outer border
    draw.rectangle([0, 0, W - 1, H - 1], outline='#CBD5E1', width=2)

    # Header Plaque
    draw.rectangle([2, 2, W - 3, 85], fill='#0B1F35')
    draw.text((W // 2, 32), 'BTS SECURITY SYSTEM', fill='#FFFFFF', anchor='mm', font=_get_font(20, True))
    draw.text((W // 2, 60), 'OFFICIAL GATE ACCESS TAG', fill='#38BDF8', anchor='mm', font=_get_font(13, True))

    # Site Info
    draw.text((W // 2, 120), str(site.site_name or site.site_id).upper(), fill='#0F172A', anchor='mm', font=_get_font(22, True))

    # Pill badge for Site ID (dynamic width)
    id_text = f'SITE ID: {site.site_id}'
    id_font = _get_font(14, True)
    bbox = draw.textbbox((0, 0), id_text, font=id_font)
    pill_w = max(bbox[2] - bbox[0] + 36, 160)
    pill_x1 = (W - pill_w) // 2
    pill_x2 = pill_x1 + pill_w
    draw.rounded_rectangle([pill_x1, 146, pill_x2, 178], radius=16, fill='#EDE9FE', outline='#7C3AED', width=2)
    draw.text((W // 2, 162), id_text, fill='#6D28D9', anchor='mm', font=id_font)

    loc_display = str(site.location or 'Shelter BTS Site')[:50]
    draw.text((W // 2, 198), loc_display, fill='#64748B', anchor='mm', font=_get_font(13, False))

    # QR Code Frame
    qr_w, qr_h = qr_img.size
    qr_x = (W - qr_w) // 2
    qr_y = 225
    draw.rounded_rectangle([qr_x - 12, qr_y - 12, qr_x + qr_w + 12, qr_y + qr_h + 12], radius=16, outline='#E2E8F0', width=2)
    card.paste(qr_img, (qr_x, qr_y))

    # Footer Notice
    footer_y = H - 85
    draw.rectangle([2, footer_y, W - 3, H - 3], fill='#F8FAFC')
    draw.line([(2, footer_y), (W - 3, footer_y)], fill='#E2E8F0', width=1)
    draw.text((W // 2, footer_y + 28), 'Pindai dengan kamera smartphone saat di lokasi', fill='#0F172A', anchor='mm', font=_get_font(13, True))
    draw.text((W // 2, footer_y + 55), '2FA + Face ID Multi-Factor Gate Access', fill='#64748B', anchor='mm', font=_get_font(12, False))

    buf = BytesIO()
    card.save(buf, format='PNG')
    return buf.getvalue()


@app.route('/sites/<int:site_id>/qr-code')
@admin_required
def qr_code_site(site_id):
    site = BtsSite.query.get_or_404(site_id)
    base_url = _get_external_access_base_url()
    access_url = base_url + url_for('access_simulation') + f"?site_id={quote(str(site.site_id))}&from_qr=1"
    
    if request.args.get('raw') == '1':
        qr_png = generate_qr(access_url)
    else:
        qr_png = generate_site_badge_qr(site, access_url)

    response = app.response_class(qr_png, mimetype='image/png')
    response.headers['Content-Disposition'] = f'inline; filename="qr-tag-{site.site_id}.png"'
    return response


# =========================================================
# ACTIVITY LOG (AUDIT TRAIL)
# =========================================================

@app.route('/audit-log')
@admin_required
def activity_log():
    action_filter = request.args.get('action', '').strip().upper()
    username_filter = request.args.get('username', '').strip()

    query = ActivityLog.query.order_by(ActivityLog.timestamp.desc())

    if action_filter:
        query = query.filter(ActivityLog.action.ilike(f'%{action_filter}%'))
    if username_filter:
        query = query.filter(ActivityLog.username.ilike(f'%{username_filter}%'))

    page = max(int(request.args.get('page', 1)), 1)
    per_page = 50
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        'activity_log.html',
        logs=pagination.items,
        page=page,
        per_page=per_page,
        total=pagination.total,
        total_pages=pagination.pages,
        action_filter=action_filter,
        username_filter=username_filter,
    )


@app.route('/events')
@login_required_any
def sse_events():
    from app.sse_manager import subscribe, unsubscribe

    sid, q = subscribe()

    def event_stream():
        try:
            yield 'data: {"type": "connected"}\n\n'
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    yield ':ping\n\n'
        except GeneratorExit:
            pass
        finally:
            unsubscribe(sid)

    return Response(event_stream(), mimetype='text/event-stream')


# =========================================================
# PWA OFFLINE FALLBACK
# =========================================================

@app.route('/offline')
@app.route('/offline.html')
def offline():
    return render_template('offline.html')


# =========================================================
# MOBILE APP INSTALLATION & QR CODE
# =========================================================

def _get_local_ip_address():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


@app.route('/mobile-app')
@app.route('/install-mobile')
def mobile_app_guide():
    local_ip = _get_local_ip_address()
    host = request.host
    scheme = request.scheme
    if 'localhost' in host or '127.0.0.1' in host:
        local_url = f"{scheme}://{local_ip}:5000"
    else:
        local_url = f"{scheme}://{host}"
    return render_template('mobile_install.html', local_url=local_url, local_ip=local_ip)


@app.route('/api/mobile-qr')
def mobile_qr_image():
    import qrcode
    from io import BytesIO
    local_ip = _get_local_ip_address()
    host = request.host
    scheme = request.scheme
    if 'localhost' in host or '127.0.0.1' in host:
        target_url = f"{scheme}://{local_ip}:5000"
    else:
        target_url = f"{scheme}://{host}"

    qr = qrcode.make(target_url)
    buf = BytesIO()
    qr.save(buf, format='PNG')
    buf.seek(0)
    return Response(buf.getvalue(), mimetype='image/png')




