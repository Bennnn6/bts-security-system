# 🛡️ Smart BTS Gate Security System
**Sistem Keamanan Akses Gerbang Gardu BTS Telekomunikasi Berbasis Multi-Factor Authentication (Face Recognition + TOTP + Work Order)**

---

## 📌 Gambaran Umum
**Smart BTS Gate Security System** adalah platform keamanan gardu dan shelter *Base Transceiver Station* (BTS) berbasis web yang dirancang untuk mencegah akses ilegal, pencurian baterai/genset, dan kelalaian prosedur di lokasi BTS telekomunikasi.

Sistem ini menerapkan standar keamanan **Telco-Grade** dengan autentikasi bertingkat:
1. **Pengenalan Wajah (Face Recognition)** dengan ekstraksi fitur 128-vektor menggunakan algoritma deep learning Dlib.
2. **Deteksi Keaslian Wajah (Challenge Liveness Detection)** untuk mencegah *spoofing* menggunakan foto atau layar handphone.
3. **Time-based One-Time Password (TOTP)** via Google Authenticator / Microsoft Authenticator (RFC 6238).
4. **Izin Kerja / Surat Perintah Kerja (Work Order)**: Memastikan teknisi hanya dapat membuka gerbang jika memiliki jadwal penugasan aktif di site tersebut.
5. **Peringatan Instan Telegram Bot**: Mengirim pesan darurat dan foto snapshot bukti (*evidence image*) saat terdeteksi pelanggaran keamanan.
6. **Integrasi Hardware / IoT Relay Webhook**: Pemicu relay fisik (solenoid / magnetic door lock) melalui ESP32 / Raspberry Pi.

---

## 🚀 Fitur Unggulan

- 📊 **Monitoring Dashboard NOC**: Pantau status gerbang, suhu shelter, daya, dan status keamanan seluruh site BTS secara real-time.
- 📍 **Peta Interaktif BTS (Leaflet GIS)**: Visualisasi lokasi geografis site BTS dan status risiko keamanan per lokasi.
- 📋 **Sistem Surat Perintah Kerja (Work Orders)**: Penerbitan dan pelacakan tiket perawatan teknisi per site BTS.
- 🚨 **Security Incident & Analytics**: Grafik tren insiden harian, persentase granted/denied, deteksi anomali, dan filter tingkat keparahan (NORMAL, WARNING, HIGH, CRITICAL).
- 📑 **Ekspor Laporan**: Unduh rekap log akses ke format **Excel (.xlsx)** dan **PDF (.pdf)**.
- 💾 **Backup Database & Auto-Purge**: Download snapshot database SQLite secara instan dan bersihkan log lama dari antarmuka Admin.
- 📱 **PWA (Progressive Web App)**: Akses mobile-friendly bagi teknisi lapangan dengan manifest dan service worker.

---

## 🛠️ Panduan Instalasi (Windows)

### 1. Prasyarat
- **Python 3.10** atau **Python 3.11** (disarankan 64-bit).
- Git (opsional).

### 2. Buat Virtual Environment
`powershell
python -m venv venv
.\venv\Scripts\activate
`

### 3. Instalasi Pustaka Dlib (Khusus Windows)
Pustaka `face-recognition` membutuhkan dlib. Di Windows, gunakan wheel yang sudah dikompilasi:
`powershell
pip install https://github.com/z-mahmud22/Dlib_Windows_Python3.x/raw/main/dlib-19.24.1-cp311-cp311-win_amd64.whl
`

### 4. Instalasi Seluruh Dependensi
`powershell
pip install -r requirements.txt
`

---

## ⚙️ Konfigurasi .env
Salin atau sesuaikan file konfigurasi .env pada folder root proyek:

`ini
# Secret key aplikasi Flask
SECRET_KEY=<buat-kunci-acak-64-karakter>
APP_ENV=development
FLASK_DEBUG=False

# Password bootstrap akun admin lokal (minimal 12 karakter).
# Hapus dari .env setelah akun admin berhasil dibuat lewat /setup_admin.
INITIAL_ADMIN_PASSWORD=<password-unik>

# Database (Default: SQLite instance/app.db)
DATABASE_URL=sqlite:///app.db
# Opsi MySQL: DATABASE_URL=mysql+pymysql://root:@localhost/bts_security

# Sensitivitas Wajah (0.40 = ketat, 0.45 = standar, 0.50 = fleksibel)
FACE_MATCH_THRESHOLD=0.45

# Durasi buka gerbang (detik)
GATE_OPEN_SECONDS=10

# Keamanan OTP
OTP_MAX_FAILURES=3
OTP_LOCKOUT_MINUTES=5
STRICT_2FA_MODE=True
DURESS_PIN=
GEOFENCE_ENABLED=True
GEOFENCE_REQUIRE_COORDINATES=True
GEOFENCE_RADIUS_METERS=150

# Token acak minimal 32 karakter untuk sensor pintu fisik.
GATE_SENSOR_TOKEN=

# Sistem Work Order (False = bebas, True = wajib ada tiket aktif hari ini)
ENABLE_WORK_ORDER_CHECK=False

# Notifikasi Telegram Bot Alert (Opsional)
TELEGRAM_NOTIFICATIONS_ENABLED=False
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Integrasi IoT Hardware Relay (Opsional)
GATE_WEBHOOK_URL=http://192.168.1.100/gate/relay
`

---

## 🚦 Menjalankan Aplikasi

### 1. Jalankan Server Flask
`powershell
python run.py
`
Buka browser pada alamat: **http://localhost:5000**
Untuk akses kamera dan GPS dari perangkat LAN, jalankan dengan HTTPS memakai `python run.py --https`.
Server development Flask ditujukan untuk pengembangan; gunakan WSGI server dan TLS reverse proxy saat deployment.

### 2. Menjalankan Pengujian Otomatis (*Test Suite*)
`powershell
python test_system.py
`
*Skrip ini mengubah database dan membuat data demo. Jalankan hanya dengan database SQLite sementara, bukan database produksi.*

---

## 🔑 Akun & Kredensial

| Peran | Username / ID | Password | Keterangan |
| :--- | :--- | :--- | :--- |
| **Admin NOC** | `admin` atau username pilihan | *(Password dari `INITIAL_ADMIN_PASSWORD` saat bootstrap lokal)* | Mengakses seluruh fitur monitoring & kontrol |
| **Teknisi** | TECH-001 | *(Tergantung akun teknisi)* | Teknisi Budi Santoso (Face + TOTP terdaftar) |
| **Site BTS** | BTS-001 | - | BTS BOJONG |

---

## 📁 Struktur Direktori
`	ext
bts-security-system/
├── app/
│   ├── static/               # Aset CSS, JS, manifest.json, sw.js
│   ├── templates/            # Template HTML (Dashboard, Map, Logs, Work Orders, dll.)
│   ├── access_control.py     # Kontrol gerbang, timer cooldown, hardware webhook
│   ├── api.py                # REST API (Face Verify, OTP Verify, Status, Sync)
│   ├── auth_helpers.py       # Role-based access control (Admin/Technician/Guest)
│   ├── face_recognition_module.py # Ekstraksi & perbandingan fitur wajah
│   ├── feature_routes.py     # Endpoint ekspor Excel/PDF dan audit security
│   ├── models.py             # Model Database SQLAlchemy (Admin, Technician, Site, Logs, WorkOrder)
│   ├── notifications.py      # Layanan pengiriman notifikasi Telegram Bot Alert
│   ├── routes.py             # Kontroller rute halaman web utama
│   └── totp_module.py        # Pengelola secret TOTP & QR code
├── config.py                 # Konfigurasi aplikasi Flask
├── run.py                    # Entry point aplikasi
├── test_system.py            # Automated test suite (98 Test Cases)
├── requirements.txt          # Daftar paket dependensi Python
├── .env                      # Variabel lingkungan
└── README.md                 # Dokumentasi sistem
`

---

## 📄 Lisensi & Hak Cipta
Dikembangkan untuk sistem keamanan dan pemantauan infrastruktur telekomunikasi BTS.
