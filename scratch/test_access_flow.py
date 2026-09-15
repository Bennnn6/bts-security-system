import os
import sys
from datetime import datetime

# Set path
sys.path.insert(0, os.path.abspath('.'))

import pyotp
from app import app, db
from app.models import Technician, BtsSite, AccessLog, Admin, WorkOrder

with app.app_context():
    client = app.test_client()

    # 1. Login admin to get session cookies
    admin = Admin.query.first()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(admin.id)
        sess['_fresh'] = True
        sess['csrf_token'] = 'test_token'

    # Get technician & site
    tech = Technician.query.filter_by(technician_id="TECH-001").first()
    site = BtsSite.query.first()

    print(f"Testing with Technician: {tech.technician_id} ({tech.name}), Site: {site.site_id}")

    # Ensure work order exists and is APPROVED / IN_PROGRESS for today so work order check passes
    today = datetime.utcnow().date()
    wo = WorkOrder.query.filter_by(technician_id=tech.technician_id, site_id=site.site_id).first()
    if not wo:
        wo = WorkOrder(
            work_order_number="WO-TEST-001",
            technician_id=tech.technician_id,
            site_id=site.site_id,
            scheduled_date=today,
            status="APPROVED",
            description="Test Access Flow",
        )
        db.session.add(wo)
        db.session.commit()
    else:
        wo.scheduled_date = today
        wo.status = "APPROVED"
        db.session.commit()

    # Reset any ongoing open gate state/cooldown on the site
    from app.access_control import close_gate
    close_gate(site_id=site.site_id)

    # If technician is currently recorded inside, log a clean exit first
    last_log = AccessLog.query.filter(
        AccessLog.technician_id == tech.technician_id,
        AccessLog.site_id == site.site_id,
        AccessLog.access_result == "GRANTED"
    ).order_by(AccessLog.timestamp.desc()).first()

    if last_log and last_log.access_type == "MASUK":
        reset_log = AccessLog(
            technician_id=tech.technician_id,
            site_id=site.site_id,
            access_type="KELUAR",
            face_result="SKIPPED",
            otp_result="VERIFIED",
            access_result="GRANTED",
            reason_code="TEST_RESET_EXIT",
            description="Reset exit for test",
            timestamp=datetime.utcnow(),
        )
        db.session.add(reset_log)
        db.session.commit()

    # 2. Test Standalone OTP Fallback Flow
    print("\n--- TEST 1: Fallback OTP Verification (Direct Gate Open) ---")
    valid_otp = pyotp.TOTP(tech.totp_secret).now()
    print(f"Generated TOTP for {tech.technician_id}: {valid_otp}")

    res = client.post(
        '/api/verify-otp',
        json={
            'tech_id': tech.technician_id,
            'otp': valid_otp,
            'site_id': site.site_id,
            'access_type': 'MASUK',
        },
        headers={'X-CSRFToken': 'test_token'}
    )
    print(f"OTP Status Code: {res.status_code}")
    data = res.get_json()
    print(f"OTP Response: {data}")

    assert res.status_code == 200, f"Expected 200 but got {res.status_code}"
    assert data.get('status') == 'success', "Expected status to be success"
    assert data.get('access_granted') is True, "Expected access_granted to be True"
    assert data.get('gate_opened') is True, "Expected gate_opened to be True"
    print("[ PASS ] Standalone Fallback OTP successfully opened the gate!")

    # Reset gate
    close_gate(site_id=site.site_id)

    # 2. Test Fallback OTP for KELUAR (Exit)
    print("\n--- TEST 2: Fallback OTP Verification for KELUAR (Exit) ---")
    valid_otp_keluar = pyotp.TOTP(tech.totp_secret).now()
    res_keluar = client.post(
        '/api/verify-otp',
        json={
            'tech_id': tech.technician_id,
            'otp': valid_otp_keluar,
            'site_id': site.site_id,
            'access_type': 'KELUAR',
        },
        headers={'X-CSRFToken': 'test_token'}
    )
    print(f"OTP KELUAR Status Code: {res_keluar.status_code}")
    data_keluar = res_keluar.get_json()
    print(f"OTP KELUAR Response: {data_keluar}")
    assert res_keluar.status_code == 200
    assert data_keluar.get('gate_opened') is True
    print("[ PASS ] Standalone Fallback OTP for KELUAR successfully opened the gate!")

    close_gate(site_id=site.site_id)

    # 3. Test Invalid OTP
    print("\n--- TEST 2: Invalid OTP Check ---")
    res_bad = client.post(
        '/api/verify-otp',
        json={
            'tech_id': tech.technician_id,
            'otp': '999999',
            'site_id': site.site_id,
            'access_type': 'MASUK',
        },
        headers={'X-CSRFToken': 'test_token'}
    )
    print(f"Invalid OTP Status Code: {res_bad.status_code}")
    data_bad = res_bad.get_json()
    assert res_bad.status_code in (403, 429), f"Expected 403 or 429 but got {res_bad.status_code}"
    assert data_bad.get('status') == 'error'
    print("[ PASS ] Invalid OTP properly rejected!")
    close_gate(site_id=site.site_id)

    # 4. Test Primary Face Recognition Flow (Direct Gate Open)
    print("\n--- TEST 4: Primary Face Recognition (Direct Gate Open) ---")
    from unittest.mock import patch

    with patch('app.api.extract_face_encoding') as mock_extract, \
         patch('app.api.compare_faces') as mock_compare:
        mock_extract.return_value = ([0.1] * 128, "")
        mock_compare.return_value = (True, 0.25)

        res_face = client.post(
            '/api/verify-face',
            json={
                'image': 'data:image/jpeg;base64,' + 'A' * 100,
                'site_id': site.site_id,
                'access_type': 'MASUK',
            },
            headers={'X-CSRFToken': 'test_token'}
        )
        print(f"Face Verify Status Code: {res_face.status_code}")
        data_face = res_face.get_json()
        print(f"Face Verify Response: {data_face}")
        assert res_face.status_code == 200
        assert data_face.get('status') == 'success'
        assert data_face.get('access_granted') is True
        assert data_face.get('gate_opened') is True
        assert "Verifikasi wajah berhasil" in data_face.get('message', '')
        print("[ PASS ] Primary Face Recognition successfully opened the gate without OTP!")

    close_gate(site_id=site.site_id)

    # Clean exit to reset state
    reset_log_exit = AccessLog(
        technician_id=tech.technician_id,
        site_id=site.site_id,
        access_type="KELUAR",
        face_result="MATCHED",
        otp_result="SKIPPED",
        access_result="GRANTED",
        reason_code="TEST_EXIT",
        description="Exit test",
        timestamp=datetime.utcnow(),
    )
    db.session.add(reset_log_exit)
    db.session.commit()

    print("\n==========================================")
    print("ALL ACCESS FLOW TESTS PASSED SUCCESSFULLY!")
    print("==========================================")
