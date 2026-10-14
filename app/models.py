from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from app import db, login_manager


# =========================================================
# FLASK LOGIN
# =========================================================

@login_manager.user_loader
def load_user(user_id):
    try:
        return Admin.query.get(int(user_id))
    except (TypeError, ValueError):
        return None


# =========================================================
# ADMIN
# =========================================================

class Admin(UserMixin, db.Model):

    __tablename__ = 'admin'

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    username = db.Column(
        db.String(64),
        unique=True,
        index=True,
        nullable=False
    )

    password_hash = db.Column(
        db.String(256),
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    # -----------------------------------------------------
    # PASSWORD
    # -----------------------------------------------------

    def set_password(self, password):
        self.password_hash = generate_password_hash(
            password
        )

    def check_password(self, password):
        if not self.password_hash:
            return False

        return check_password_hash(
            self.password_hash,
            password
        )


# =========================================================
# TECHNICIAN
# =========================================================

class Technician(db.Model):

    __tablename__ = 'technician'

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # -----------------------------------------------------
    # IDENTITAS TEKNISI
    # -----------------------------------------------------

    technician_id = db.Column(
        db.String(20),
        unique=True,
        index=True,
        nullable=False
    )

    name = db.Column(
        db.String(100),
        nullable=False
    )

    position = db.Column(
        db.String(100),
        nullable=False
    )

    phone = db.Column(
        db.String(20),
        nullable=False
    )

    status = db.Column(
        db.String(20),
        default='Active',
        nullable=False
    )

    # -----------------------------------------------------
    # FACE RECOGNITION
    # -----------------------------------------------------
    #
    # Encoding disimpan sebagai object/list.
    # Modul face recognition kita akan menormalisasi
    # data ini sebelum dibandingkan.
    #

    face_encoding = db.Column(
        db.PickleType,
        nullable=True
    )

    # -----------------------------------------------------
    # TOTP
    # -----------------------------------------------------

    totp_secret = db.Column(
        db.String(64),
        nullable=True
    )

    # -----------------------------------------------------
    # LOGIN TEKNISI
    # -----------------------------------------------------

    username = db.Column(
        db.String(64),
        unique=True,
        nullable=True
    )

    password_hash = db.Column(
        db.String(256),
        nullable=True
    )

    # -----------------------------------------------------
    # TIMESTAMP
    # -----------------------------------------------------

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # -----------------------------------------------------
    # RELATIONSHIP
    # -----------------------------------------------------

    access_logs = db.relationship(
        'AccessLog',
        back_populates='technician',
        foreign_keys='AccessLog.technician_id',
        lazy='select'
    )

    # -----------------------------------------------------
    # PASSWORD
    # -----------------------------------------------------

    def set_password(self, password):
        self.password_hash = generate_password_hash(
            password
        )

    def check_password(self, password):
        if not self.password_hash:
            return False

        return check_password_hash(
            self.password_hash,
            password
        )

    # -----------------------------------------------------
    # DISPLAY
    # -----------------------------------------------------

    def __repr__(self):
        return (
            f'<Technician '
            f'{self.technician_id} '
            f'{self.name}>'
        )


# =========================================================
# BTS SITE
# =========================================================

class BtsSite(db.Model):

    __tablename__ = 'bts_site'

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # -----------------------------------------------------
    # IDENTITAS SITE
    # -----------------------------------------------------

    site_id = db.Column(
        db.String(50),
        unique=True,
        index=True,
        nullable=False
    )

    site_name = db.Column(
        db.String(100),
        nullable=False
    )

    location = db.Column(
        db.String(200),
        nullable=False
    )

    # -----------------------------------------------------
    # KOORDINAT
    # -----------------------------------------------------

    latitude = db.Column(
        db.String(50),
        nullable=False
    )

    longitude = db.Column(
        db.String(50),
        nullable=False
    )

    # -----------------------------------------------------
    # DESKRIPSI
    # -----------------------------------------------------

    description = db.Column(
        db.Text,
        nullable=True
    )

    # -----------------------------------------------------
    # STATUS SITE
    # -----------------------------------------------------
    #
    # Normal (Up)
    # Maintenance
    # Down
    #

    status = db.Column(
        db.String(30),
        default='Normal (Up)',
        nullable=False
    )

    # -----------------------------------------------------
    # FIELD LAMA
    # -----------------------------------------------------
    #
    # Dipertahankan agar database/project lama tetap
    # kompatibel. Tidak digunakan lagi untuk dashboard
    # keamanan baru.
    #

    temperature = db.Column(
        db.String(20),
        default='32°C',
        nullable=True
    )

    power_output = db.Column(
        db.String(20),
        default='48V / 50W',
        nullable=True
    )

    # -----------------------------------------------------
    # TIMESTAMP
    # -----------------------------------------------------

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # -----------------------------------------------------
    # RELATIONSHIP
    # -----------------------------------------------------

    access_logs = db.relationship(
        'AccessLog',
        back_populates='site',
        foreign_keys='AccessLog.site_id',
        lazy='select'
    )

    # -----------------------------------------------------
    # DISPLAY
    # -----------------------------------------------------

    def __repr__(self):
        return (
            f'<BtsSite '
            f'{self.site_id} '
            f'{self.site_name}>'
        )


# =========================================================
# ACCESS LOG
# =========================================================

class AccessLog(db.Model):

    __tablename__ = 'access_log'

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # -----------------------------------------------------
    # TEKNISI
    # -----------------------------------------------------

    technician_id = db.Column(
        db.String(20),
        db.ForeignKey(
            'technician.technician_id'
        ),
        nullable=True,
        index=True
    )

    # -----------------------------------------------------
    # SITE BTS
    # -----------------------------------------------------

    site_id = db.Column(
        db.String(50),
        db.ForeignKey(
            'bts_site.site_id'
        ),
        nullable=True,
        index=True
    )

    # -----------------------------------------------------
    # JENIS AKSES
    # -----------------------------------------------------
    #
    # MASUK
    # KELUAR
    #

    access_type = db.Column(
        db.String(20),
        nullable=True,
        index=True
    )

    # -----------------------------------------------------
    # WAKTU
    # -----------------------------------------------------

    timestamp = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True
    )

    # -----------------------------------------------------
    # FACE RESULT
    # -----------------------------------------------------
    #
    # SUCCESS
    # FAILED
    #

    face_result = db.Column(
        db.String(20),
        nullable=True,
        index=True
    )

    # -----------------------------------------------------
    # FACE DISTANCE
    # -----------------------------------------------------

    face_distance = db.Column(
        db.Float,
        nullable=True
    )

    # -----------------------------------------------------
    # OTP RESULT
    # -----------------------------------------------------
    #
    # SUCCESS
    # FAILED
    # PENDING
    # NOT_CHECKED
    #

    otp_result = db.Column(
        db.String(20),
        nullable=True,
        index=True
    )

    # -----------------------------------------------------
    # HASIL AKHIR
    # -----------------------------------------------------
    #
    # GRANTED
    # DENIED
    # PENDING
    #

    access_result = db.Column(
        db.String(20),
        nullable=True,
        index=True
    )

    # -----------------------------------------------------
    # ALASAN HASIL AKSES
    # -----------------------------------------------------
    #
    # Contoh:
    #
    # FACE_NOT_RECOGNIZED
    # FACE_NOT_DETECTED
    # OTP_INVALID
    # OTP_LOCKED
    # INVALID_ACCESS_STATE
    # SITE_DOWN
    # GATE_ERROR
    # LIVENESS_FAILED
    # GATE_COOLDOWN
    # SUCCESS
    #

    reason_code = db.Column(
        db.String(50),
        nullable=True,
        index=True
    )

    # -----------------------------------------------------
    # TINGKAT INSIDEN
    # -----------------------------------------------------
    #
    # NORMAL
    # WARNING
    # HIGH
    # CRITICAL
    #

    incident_level = db.Column(
        db.String(20),
        default='NORMAL',
        nullable=False,
        index=True
    )

    # -----------------------------------------------------
    # DEVICE / IP / USER AGENT
    # -----------------------------------------------------

    device_id = db.Column(
        db.String(500),
        nullable=True
    )

    # -----------------------------------------------------
    # KETERANGAN
    # -----------------------------------------------------

    description = db.Column(
        db.String(500),
        nullable=True
    )

    # -----------------------------------------------------
    # FOTO BUKTI
    # -----------------------------------------------------
    #
    # Menyimpan Data URI/Base64 snapshot kamera.
    #

    evidence_image = db.Column(
        db.Text,
        nullable=True
    )

    # -----------------------------------------------------
    # GATE STATUS
    # -----------------------------------------------------
    #
    # OPEN
    # CLOSED
    #

    gate_status = db.Column(
        db.String(20),
        default='CLOSED',
        nullable=True
    )

    # -----------------------------------------------------
    # GATE OPENED / CLOSED TIME
    # -----------------------------------------------------

    gate_opened_at = db.Column(
        db.DateTime,
        nullable=True
    )

    gate_closed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # -----------------------------------------------------
    # RELATIONSHIP TEKNISI
    # -----------------------------------------------------

    technician = db.relationship(
        'Technician',
        back_populates='access_logs',
        foreign_keys=[technician_id]
    )

    # -----------------------------------------------------
    # RELATIONSHIP SITE
    # -----------------------------------------------------

    site = db.relationship(
        'BtsSite',
        back_populates='access_logs',
        foreign_keys=[site_id]
    )

    # -----------------------------------------------------
    # HELPER
    # -----------------------------------------------------

    @property
    def technician_name(self):
        if self.technician:
            return self.technician.name

        return 'Tidak dikenali'

    @property
    def site_name(self):
        if self.site:
            return self.site.site_name

        return self.site_id or '-'

    @property
    def is_security_incident(self):
        return (
            self.incident_level
            in (
                'WARNING',
                'HIGH',
                'CRITICAL'
            )
        )

    def __repr__(self):
        return (
            f'<AccessLog '
            f'{self.id} '
            f'{self.access_type} '
            f'{self.access_result}>'
        )


# =========================================================
# ACTIVITY LOG
# =========================================================

class ActivityLog(db.Model):

    __tablename__ = 'activity_log'

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # -----------------------------------------------------
    # WAKTU
    # -----------------------------------------------------

    timestamp = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True
    )

    # -----------------------------------------------------
    # USER
    # -----------------------------------------------------

    username = db.Column(
        db.String(100),
        nullable=True
    )

    # -----------------------------------------------------
    # AKSI
    # -----------------------------------------------------

    action = db.Column(
        db.String(100),
        nullable=True,
        index=True
    )

    # -----------------------------------------------------
    # KETERANGAN
    # -----------------------------------------------------

    description = db.Column(
        db.String(500),
        nullable=True
    )

    # -----------------------------------------------------
    # INCIDENT
    # -----------------------------------------------------

    incident_level = db.Column(
        db.String(20),
        default='NORMAL',
        nullable=False,
        index=True
    )

    # -----------------------------------------------------
    # DEVICE
    # -----------------------------------------------------

    device_id = db.Column(
        db.String(500),
        nullable=True
    )

    # -----------------------------------------------------
    # HELPER
    # -----------------------------------------------------

    @property
    def is_security_incident(self):
        return (
            self.incident_level
            in (
                'WARNING',
                'HIGH',
                'CRITICAL'
            )
        )

    def __repr__(self):
        return (
            f'<ActivityLog '
            f'{self.action}>'
        )


# =========================================================
# WORK ORDER / SURAT PERINTAH KERJA
# =========================================================

class WorkOrder(db.Model):

    __tablename__ = 'work_order'

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    ticket_number = db.Column(
        db.String(50),
        unique=True,
        index=True,
        nullable=False
    )

    technician_id = db.Column(
        db.String(20),
        db.ForeignKey('technician.technician_id'),
        nullable=False,
        index=True
    )

    site_id = db.Column(
        db.String(50),
        db.ForeignKey('bts_site.site_id'),
        nullable=False,
        index=True
    )

    scheduled_date = db.Column(
        db.Date,
        nullable=False,
        default=datetime.utcnow
    )

    purpose = db.Column(
        db.String(255),
        nullable=False
    )

    status = db.Column(
        db.String(30),
        default='APPROVED',
        nullable=False
    )  # APPROVED, PENDING, COMPLETED, VERIFIED, CANCELLED

    notes = db.Column(
        db.Text,
        nullable=True
    )

    completion_note = db.Column(
        db.Text,
        nullable=True
    )

    evidence_image = db.Column(
        db.Text,
        nullable=True
    )

    completed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    started_at = db.Column(
        db.DateTime,
        nullable=True
    )

    start_latitude = db.Column(
        db.String(40),
        nullable=True
    )

    start_longitude = db.Column(
        db.String(40),
        nullable=True
    )

    completion_latitude = db.Column(
        db.String(40),
        nullable=True
    )

    completion_longitude = db.Column(
        db.String(40),
        nullable=True
    )

    verified_at = db.Column(
        db.DateTime,
        nullable=True
    )

    verified_by = db.Column(
        db.String(64),
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    technician = db.relationship(
        'Technician',
        foreign_keys=[technician_id]
    )

    site = db.relationship(
        'BtsSite',
        foreign_keys=[site_id]
    )

    @property
    def technician_name(self):
        return self.technician.name if self.technician else self.technician_id

    @property
    def site_name(self):
        return self.site.site_name if self.site else self.site_id

    def __repr__(self):
        return f'<WorkOrder {self.ticket_number} {self.status}>'
