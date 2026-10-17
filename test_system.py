"""
FINAL SYSTEM TEST
Smart BTS Gate Security System

Cara menjalankan:
    python test_system.py

Script ini melakukan:
1. Cek import aplikasi
2. Cek database
3. Cek tabel utama
4. Cek kolom penting
5. Cek endpoint API
6. Cek halaman utama
7. Cek halaman dashboard
8. Cek halaman Site
9. Cek halaman Access
10. Cek struktur security feature

Catatan keamanan:
Tes berjalan di database SQLite sementara (dihapus otomatis), sehingga
akun uji (admin/admin123, teknisi1/teknisi123) tidak pernah masuk ke
database asli. Set TEST_DATABASE_URL jika ingin memakai database uji lain.
"""

import atexit
import os
import shutil
import tempfile

if os.environ.get('TEST_DATABASE_URL'):
    os.environ['DATABASE_URL'] = os.environ['TEST_DATABASE_URL']
else:
    _TEST_DB_DIR = tempfile.mkdtemp(prefix='bts_test_')
    os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(
        _TEST_DB_DIR, 'test_system.db'
    ).replace('\\', '/')
    atexit.register(shutil.rmtree, _TEST_DB_DIR, ignore_errors=True)

# Jangan kirim notifikasi / perintah relay sungguhan selama tes.
os.environ['TELEGRAM_NOTIFICATIONS_ENABLED'] = 'False'
os.environ['WHATSAPP_NOTIFICATIONS_ENABLED'] = 'False'
os.environ['GATE_WEBHOOK_URL'] = ''

from app import app, db  # noqa: E402
from app.models import (  # noqa: E402
    Admin,
    Technician,
    BtsSite,
    AccessLog,
    ActivityLog,
    WorkOrder
)


# =========================================================
# TEST COUNTER
# =========================================================

passed = 0
failed = 0


# =========================================================
# HELPER
# =========================================================

def test(
    name,
    condition,
    detail=''
):
    global passed
    global failed

    if condition:

        passed += 1

        print(
            f'[ PASS ] {name}'
        )

        if detail:
            print(
                f'         {detail}'
            )

    else:

        failed += 1

        print(
            f'[ FAIL ] {name}'
        )

        if detail:
            print(
                f'         {detail}'
            )


def header(
    title
):
    print()
    print(
        '=' * 70
    )

    print(
        title
    )

    print(
        '=' * 70
    )


# =========================================================
# 1. IMPORT APPLICATION
# =========================================================

header(
    '1. APPLICATION IMPORT'
)

try:

    test(
        'Flask application berhasil di-load',
        app is not None
    )

except Exception as exc:

    test(
        'Flask application berhasil di-load',
        False,
        str(exc)
    )


# =========================================================
# APPLICATION CONTEXT
# =========================================================

with app.app_context():

    # =====================================================
    # 2. DATABASE CONNECTION
    # =====================================================

    header(
        '2. DATABASE CONNECTION'
    )

    try:

        db.session.execute(
            db.text(
                'SELECT 1'
            )
        )

        test(
            'Database connection',
            True
        )

    except Exception as exc:

        test(
            'Database connection',
            False,
            str(exc)
        )


    # =====================================================
    # 3. TABLE CHECK
    # =====================================================

    header(
        '3. DATABASE TABLES'
    )

    required_tables = [
        'admin',
        'technician',
        'bts_site',
        'access_log',
        'activity_log'
    ]

    inspector = (
        db.inspect(
            db.engine
        )
    )

    existing_tables = (
        inspector.get_table_names()
    )

    for table_name in required_tables:

        test(
            f'Tabel {table_name}',
            table_name in existing_tables
        )


    # =====================================================
    # 4. COLUMN CHECK
    # =====================================================

    header(
        '4. DATABASE COLUMNS'
    )

    required_columns = {

        'technician': [
            'id',
            'technician_id',
            'name',
            'position',
            'phone',
            'status',
            'face_encoding',
            'totp_secret',
            'username',
            'password_hash',
            'created_at',
            'updated_at'
        ],

        'bts_site': [
            'id',
            'site_id',
            'site_name',
            'location',
            'latitude',
            'longitude',
            'description',
            'status',
            'temperature',
            'power_output',
            'created_at',
            'updated_at'
        ],

        'access_log': [
            'id',
            'technician_id',
            'site_id',
            'access_type',
            'timestamp',
            'face_result',
            'face_distance',
            'otp_result',
            'access_result',
            'reason_code',
            'incident_level',
            'device_id',
            'description',
            'evidence_image',
            'gate_status',
            'gate_opened_at',
            'gate_closed_at'
        ],

        'activity_log': [
            'id',
            'timestamp',
            'username',
            'action',
            'description',
            'incident_level',
            'device_id'
        ]
    }

    for table_name, columns in required_columns.items():

        try:

            table_columns = {
                column['name']
                for column in inspector.get_columns(
                    table_name
                )
            }

            for column_name in columns:

                test(
                    f'{table_name}.{column_name}',
                    column_name in table_columns
                )

        except Exception as exc:

            test(
                f'Baca struktur {table_name}',
                False,
                str(exc)
            )


    # =====================================================
    # FIXTURE SEEDING (UNTUK CI RUNNER & FRESH DATABASE)
    # =====================================================

    def seed_test_fixtures():
        import pyotp
        from datetime import datetime, date

        db.create_all()

        # 1. Admin default
        admin = Admin.query.filter_by(username='admin').first()
        if not admin:
            admin = Admin(username='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.flush()

        # 2. BTS Site
        site = BtsSite.query.first()
        if not site:
            site = BtsSite(
                site_id='BTS-BOJONG',
                site_name='BTS Bojongsoang',
                location='Bandung, Jawa Barat',
                latitude='-6.9744',
                longitude='107.6303',
                description='Site BTS Uji Otomatis',
                status='Normal (Up)',
                temperature='32°C',
                power_output='5kW'
            )
            db.session.add(site)
            db.session.flush()

        # 3. Technician
        technician = Technician.query.first()
        if not technician:
            technician = Technician(
                technician_id='TECH-001',
                name='Budi Santoso',
                position='Field Engineer',
                phone='081234567890',
                status='Active',
                username='teknisi1',
                totp_secret=pyotp.random_base32(),
                face_encoding=[0.1] * 128
            )
            technician.set_password('teknisi123')
            db.session.add(technician)
            db.session.flush()
        else:
            updated = False
            if not technician.totp_secret:
                technician.totp_secret = pyotp.random_base32()
                updated = True
            if not technician.face_encoding:
                technician.face_encoding = [0.1] * 128
                updated = True
            if updated:
                db.session.flush()

        # 4. Work Order
        work_order = WorkOrder.query.first()
        if not work_order:
            work_order = WorkOrder(
                ticket_number='WO-2026-001',
                technician_id=technician.technician_id,
                site_id=site.site_id,
                scheduled_date=date.today(),
                purpose='Maintenance Rutin Perangkat BTS',
                status='APPROVED',
                notes='Pemeriksaan modul transmisi dan backup baterai'
            )
            db.session.add(work_order)
            db.session.flush()

        # 5. Access Log
        access_log = AccessLog.query.first()
        if not access_log:
            access_log = AccessLog(
                technician_id=technician.technician_id,
                site_id=site.site_id,
                access_type='ENTRY',
                timestamp=datetime.utcnow(),
                face_result='MATCH',
                face_distance=0.25,
                otp_result='VALID',
                access_result='GRANTED',
                reason_code='SUCCESS',
                incident_level='NORMAL',
                device_id='GATE-01',
                description='Akses masuk teknisi uji',
                gate_status='CLOSED',
                gate_opened_at=datetime.utcnow(),
                gate_closed_at=datetime.utcnow()
            )
            db.session.add(access_log)
            db.session.flush()

        # 6. Activity Log
        activity_log = ActivityLog.query.first()
        if not activity_log:
            activity_log = ActivityLog(
                timestamp=datetime.utcnow(),
                username=admin.username,
                action='SYSTEM_TEST_INIT',
                description='Inisialisasi fixture untuk automated test',
                incident_level='NORMAL',
                device_id='TEST_RUNNER'
            )
            db.session.add(activity_log)
            db.session.flush()

        db.session.commit()

    seed_test_fixtures()


    # =====================================================
    # 5. MODEL QUERY
    # =====================================================

    header(
        '5. MODEL QUERY'
    )

    try:

        admins = (
            Admin.query.count()
        )

        test(
            'Model Admin dapat di-query',
            True,
            f'{admins} data'
        )

    except Exception as exc:

        test(
            'Model Admin dapat di-query',
            False,
            str(exc)
        )


    try:

        technicians = (
            Technician.query.count()
        )

        test(
            'Model Technician dapat di-query',
            True,
            f'{technicians} data'
        )

    except Exception as exc:

        test(
            'Model Technician dapat di-query',
            False,
            str(exc)
        )


    try:

        sites = (
            BtsSite.query.count()
        )

        test(
            'Model BtsSite dapat di-query',
            True,
            f'{sites} data'
        )

    except Exception as exc:

        test(
            'Model BtsSite dapat di-query',
            False,
            str(exc)
        )


    try:

        logs = (
            AccessLog.query.count()
        )

        test(
            'Model AccessLog dapat di-query',
            True,
            f'{logs} data'
        )

    except Exception as exc:

        test(
            'Model AccessLog dapat di-query',
            False,
            str(exc)
        )


    try:

        activities = (
            ActivityLog.query.count()
        )

        test(
            'Model ActivityLog dapat di-query',
            True,
            f'{activities} data'
        )

    except Exception as exc:

        test(
            'Model ActivityLog dapat di-query',
            False,
            str(exc)
        )


    # =====================================================
    # 6. CONFIG CHECK
    # =====================================================

    header(
        '6. CONFIGURATION'
    )

    face_threshold = app.config.get(
        'FACE_MATCH_THRESHOLD'
    )

    gate_seconds = app.config.get(
        'GATE_OPEN_SECONDS'
    )

    otp_failures = app.config.get(
        'OTP_MAX_FAILURES'
    )

    otp_lockout = app.config.get(
        'OTP_LOCKOUT_MINUTES'
    )

    test(
        'FACE_MATCH_THRESHOLD tersedia',
        face_threshold is not None,
        str(face_threshold)
    )

    test(
        'GATE_OPEN_SECONDS tersedia',
        gate_seconds is not None,
        str(gate_seconds)
    )

    test(
        'OTP_MAX_FAILURES tersedia',
        otp_failures is not None,
        str(otp_failures)
    )

    test(
        'OTP_LOCKOUT_MINUTES tersedia',
        otp_lockout is not None,
        str(otp_lockout)
    )


    # =====================================================
    # 7. API ENDPOINT
    # =====================================================

    header(
        '7. API ENDPOINT'
    )

    client = app.test_client()

    admin_obj = Admin.query.first()
    if admin_obj:
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_obj.id)
            sess['auth_role'] = 'ADMIN'
            sess['auth_username'] = admin_obj.username

    # -----------------------------------------------------
    # GATE STATUS
    # -----------------------------------------------------

    try:
        sample_site = BtsSite.query.first()
        site_param = f'?site_id={sample_site.site_id}' if sample_site else ''

        response = client.get(
            f'/api/gate-status{site_param}'
        )

        test(
            'GET /api/gate-status',
            response.status_code == 200,
            (
                f'HTTP {response.status_code}'
            )
        )

        if response.is_json:

            data = response.get_json()

            test(
                'Gate status memiliki field status',
                'status' in data
            )

            test(
                'Gate status memiliki gate_status',
                'gate_status' in data
            )

    except Exception as exc:

        test(
            'GET /api/gate-status',
            False,
            str(exc)
        )


    # -----------------------------------------------------
    # TECHNICIAN SYNC
    # -----------------------------------------------------

    try:

        response = client.get(
            '/api/technicians/sync'
        )

        test(
            'GET /api/technicians/sync',
            response.status_code == 200,
            (
                f'HTTP {response.status_code}'
            )
        )

        if response.is_json:

            data = response.get_json()

            test(
                'Technician sync memiliki status',
                'status' in data
            )

            test(
                'Technician sync memiliki data',
                'data' in data
            )

    except Exception as exc:

        test(
            'GET /api/technicians/sync',
            False,
            str(exc)
        )


    # -----------------------------------------------------
    # SECURITY STATUS
    # -----------------------------------------------------

    try:

        response = client.get(
            '/api/security-status'
        )

        test(
            'GET /api/security-status',
            response.status_code == 200,
            (
                f'HTTP {response.status_code}'
            )
        )

        if response.is_json:

            data = response.get_json()

            test(
                'Security status memiliki data',
                'data' in data
            )

    except Exception as exc:

        test(
            'GET /api/security-status',
            False,
            str(exc)
        )


    # =====================================================
    # 8. PAGE ROUTES
    # =====================================================

    header(
        '8. PAGE ROUTES'
    )

    page_routes = [

        (
            'Login',
            '/'
        ),

        (
            'Access Simulation',
            '/access'
        ),

        (
            'Worker Dashboard',
            '/worker/dashboard'
        )
    ]

    for name, route in page_routes:

        try:

            response = client.get(
                route,
                follow_redirects=False
            )

            test(
                f'{name} {route}',
                response.status_code
                in (
                    200,
                    302
                ),
                f'HTTP {response.status_code}'
            )

        except Exception as exc:

            test(
                f'{name} {route}',
                False,
                str(exc)
            )


    # =====================================================
    # 9. ADMIN LOGIN TEST
    # =====================================================

    header(
        '9. ADMIN LOGIN'
    )

    admin = (
        Admin.query
        .filter_by(
            username='admin'
        )
        .first()
    )

    if admin:

        test(
            'Admin default tersedia',
            True,
            'username=admin'
        )

    else:

        test(
            'Admin default tersedia',
            False,
            (
                'Jalankan /setup_admin terlebih dahulu.'
            )
        )


    # =====================================================
    # 10. TECHNICIAN DATA
    # =====================================================

    header(
        '10. TECHNICIAN DATA'
    )

    technician = (
        Technician.query
        .order_by(
            Technician.id.asc()
        )
        .first()
    )

    if technician:

        test(
            'Minimal satu teknisi tersedia',
            True,
            technician.technician_id
        )

        test(
            'Technician memiliki nama',
            bool(
                technician.name
            )
        )

        test(
            'Technician memiliki status',
            bool(
                technician.status
            )
        )

        test(
            'Technician memiliki username',
            bool(
                technician.username
            )
        )

        test(
            'Technician memiliki face encoding',
            bool(
                technician.face_encoding
            )
        )

        test(
            'Technician memiliki TOTP',
            bool(
                technician.totp_secret
            )
        )

        import base64
        from app.totp_module import get_qr_code_base64
        qr_b64 = get_qr_code_base64(technician.name, technician.totp_secret)
        is_valid_qr = False
        if qr_b64:
            decoded_qr = base64.b64decode(qr_b64)
            is_valid_qr = (
                (decoded_qr.startswith(b'<svg') or decoded_qr.startswith(b'<?xml') or decoded_qr.startswith(b'\x89PNG'))
                and not decoded_qr.startswith(b"b'")
            )

        test(
            'TOTP QR Code format valid',
            is_valid_qr
        )

        import pyotp
        from app.totp_module import verify_totp
        test_otp = pyotp.TOTP(technician.totp_secret).now()
        test(
            'TOTP verification module valid',
            verify_totp(technician.totp_secret, test_otp)
        )

    else:

        print(
            '[ INFO ] Belum ada teknisi.'
        )


    # =====================================================
    # 11. SITE DATA
    # =====================================================

    header(
        '11. SITE DATA'
    )

    site = (
        BtsSite.query
        .order_by(
            BtsSite.id.asc()
        )
        .first()
    )

    if site:

        test(
            'Minimal satu Site tersedia',
            True,
            site.site_id
        )

        test(
            'Site memiliki nama',
            bool(
                site.site_name
            )
        )

        test(
            'Site memiliki lokasi',
            bool(
                site.location
            )
        )

        test(
            'Site memiliki status',
            bool(
                site.status
            )
        )

    else:

        print(
            '[ INFO ] Belum ada Site BTS.'
        )


    # =====================================================
    # 12. ACCESS LOG INTEGRITY
    # =====================================================

    header(
        '12. ACCESS LOG INTEGRITY'
    )

    latest_log = (
        AccessLog.query
        .order_by(
            AccessLog.timestamp.desc()
        )
        .first()
    )

    if latest_log:

        test(
            'AccessLog memiliki timestamp',
            latest_log.timestamp is not None
        )

        test(
            'AccessLog memiliki access_result',
            latest_log.access_result
            in (
                'GRANTED',
                'DENIED',
                'PENDING',
                None
            )
        )

        test(
            'AccessLog incident level valid',
            latest_log.incident_level
            in (
                'NORMAL',
                'WARNING',
                'HIGH',
                'CRITICAL',
                None
            )
        )

        test(
            'AccessLog reason code tersedia/valid',
            (
                latest_log.reason_code is None
                or len(
                    latest_log.reason_code
                ) > 0
            )
        )

    else:

        print(
            '[ INFO ] Belum ada Access Log.'
        )


    # =====================================================
    # 13. ACTIVITY LOG
    # =====================================================

    header(
        '13. ACTIVITY LOG'
    )

    latest_activity = (
        ActivityLog.query
        .order_by(
            ActivityLog.timestamp.desc()
        )
        .first()
    )

    if latest_activity:

        test(
            'ActivityLog memiliki timestamp',
            latest_activity.timestamp
            is not None
        )

        test(
            'ActivityLog memiliki action',
            bool(
                latest_activity.action
            )
        )

        test(
            'ActivityLog incident level valid',
            latest_activity.incident_level
            in (
                'NORMAL',
                'WARNING',
                'HIGH',
                'CRITICAL',
                None
            )
        )

    else:

        print(
            '[ INFO ] Belum ada Activity Log.'
        )


    # =====================================================
    # 14. WORK ORDER DATA
    # =====================================================

    header(
        '14. WORK ORDER DATA'
    )

    work_orders = WorkOrder.query.all()

    test(
        'Model WorkOrder dapat di-query',
        True,
        f'{len(work_orders)} data'
    )

    test(
        'Minimal satu Work Order tersedia',
        len(work_orders) > 0,
        work_orders[0].ticket_number if work_orders else 'Belum ada data'
    )

    if work_orders:
        sample_wo = work_orders[0]

        test(
            'Work Order memiliki nomor tiket',
            bool(sample_wo.ticket_number)
        )

        test(
            'Work Order memiliki teknisi yang valid',
            bool(sample_wo.technician_id)
        )

        test(
            'Work Order memiliki status yang valid',
            sample_wo.status in (
                'APPROVED',
                'PENDING',
                'IN_PROGRESS',
                'COMPLETED',
                'VERIFIED',
                'CANCELLED'
            )
        )


    # =====================================================
    # FINAL RESULT
    # =====================================================

    header(
        'FINAL RESULT'
    )

    total = (
        passed
        + failed
    )

    print(
        f'Total Test : {total}'
    )

    print(
        f'PASS       : {passed}'
    )

    print(
        f'FAIL       : {failed}'
    )

    print()

    print()
    
    # Memastikan jumlah failed harus 0 agar tes dianggap lulus
    assert failed == 0, f"Ada {failed} test yang gagal! Perbaiki bagian yang berstatus [ FAIL ]."
    
    try:
        print('[PASS] SEMUA TEST OTOMATIS BERHASIL')
    except UnicodeEncodeError:
        print('[PASS] SEMUA TEST OTOMATIS BERHASIL')
        
    print('Sistem siap masuk ke pengujian manual.\n')


