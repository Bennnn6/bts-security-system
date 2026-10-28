# PANDUAN IMPLEMENTASI APLIKASI STANDALONE (BACS ENTERPRISE)
**Base Transceiver Station Access Control System (BACS)**  
*Dokumen Resmi Rekayasa Perangkat Lunak untuk Sidang Tugas Akhir & Implementasi Perusahaan*

---

## 1. Arsitektur Dual-Platform Aplikasi (Non-Browser)

Sistem keamanan BTS dirancang agar **tidak berjalan melalui browser web biasa**, melainkan sebagai dua aplikasi mandiri (*native standalone applications*) sesuai peran pengguna di lapangan:

```
                                  +---------------------------------------+
                                  |   BACS Backend Engine & Database      |
                                  |   (Flask, OpenCV, Face ID, TOTP, DB)  |
                                  +---------------------------------------+
                                                      |
                    +---------------------------------+---------------------------------+
                    |                                                                   |
                    v                                                                   v
     +------------------------------+                                    +------------------------------+
     |   APLIKASI DESKTOP WINDOWS   |                                    |    APLIKASI MOBILE ANDROID   |
     |        (.EXE / DESKTOP)      |                                    |             (.APK)           |
     +------------------------------+                                    +------------------------------+
     | - Terminal Kiosk Gerbang     |                                    | - Smartphone Teknisi         |
     | - Dashboard NOC Ruang Kontrol|                                    | - Work Orders Lapangan       |
     | - Fullscreen Kiosk e-Gate    |                                    | - Remote Buka Gerbang / QR   |
     | - Tanpa URL/Address Bar      |                                    | - Terpasang di App Drawer HP |
     +------------------------------+                                    +------------------------------+
```

---

## 2. Bagian A: Aplikasi Desktop Windows (.exe)

Aplikasi Desktop menggunakan engine **Microsoft Edge Chromium WebView2** via `pywebview`. Backend Flask berjalan terisolasi di background thread dan langsung menampilkan jendela native Windows tanpa address bar, tanpa tombol navigasi browser, dan tanpa perlu membuka Chrome/Edge secara manual.

### Cara Menjalankan Langsung (1-Klik Tanpa Perlu Kompilasi)
Di dalam folder root proyek telah disediakan file peluncur aplikasi satu pintu:
1. **Opsi 1 (Senyap Tanpa Layar Hitam CMD - Rekomendasi Utama):**
   - Klik ganda file: `BACS_App.vbs`
   - Jendela aplikasi desktop native **BACS Enterprise** akan langsung terbuka bersih di layar Anda.
2. **Opsi 2 (Dengan Terminal Log):**
   - Klik ganda file: `BACS_App.bat`

Di dalam satu aplikasi ini, Anda dapat mengakses seluruh fitur sistem tanpa perlu membuka file terpisah:
- Masuk sebagai Admin NOC $\rightarrow$ Menampilkan Dashboard NOC, Peta GIS BTS, Kelola Teknisi, Kelola Site, Work Orders, Audit Log, dan Terminal Kiosk Gerbang.
- Masuk sebagai Teknisi $\rightarrow$ Menampilkan Work Order Tugas Saya, Riwayat Akses, dan Buka Gerbang.

### Cara Mengompilasi Menjadi File `.exe` Mandiri (*Portable Executable*)
Untuk menghasilkan file executable murni (`.exe`) yang siap dibagikan ke penguji sidang atau dipasang di shelter BTS:
1. Klik ganda file `build_exe.bat` di root direktori proyek.
2. Skrip akan memicu **PyInstaller** untuk mengemas backend Python, template UI, model Face Recognition, dan WebView2 ke dalam folder:
   ```
   dist\BACS_Enterprise\BACS_Enterprise.exe
   ```
3. File `BACS_Enterprise.exe` tersebut dapat langsung dijalankan di komputer Windows manapun tanpa perlu instalasi Python lagi.

---

## 3. Bagian B: Aplikasi Mobile Android (.apk) untuk Teknisi

Untuk teknisi lapangan yang membawa smartphone Android, tersedia 2 metode implementasi:

### Metode 1: Instalasi PWA Native (Instan & Tanpa Perlu Android Studio)
Aplikasi BACS telah dilengkapi standar **Progressive Web App (PWA)** dengan Service Worker dan Web App Manifest (`manifest.json`):
1. Hubungkan smartphone teknisi ke jaringan WiFi BTS atau hotspot server.
2. Buka URL server (misal `http://192.168.1.100:5000`) satu kali di browser smartphone (Chrome).
3. Notifikasi otomatis atau menu akan muncul: **"Tambahkan ke Layar Utama"** atau **"Install BACS Technician"**.
4. Ketuk **Install**.
5. **Hasil:** Aplikasi langsung terpasang di smartphone teknisi dengan ikon di App Drawer Android, splash screen formal BACS, dan berjalan *fullscreen* tanpa address bar browser.

### Metode 2: Kompilasi File `.apk` Fisik via Android Studio
Jika untuk keperluan sidang Tugas Akhir Anda wajib melampirkan **source code proyek Android asli** atau membagikan file `BACS_Technician.apk`:
1. Source code proyek native Android telah disediakan lengkap di folder:
   ```
   d:\GASKEN TA\NYOBA CODING\bts-security-system\android_technician_app\
   ```
2. Buka software **Android Studio** $\rightarrow$ Pilih **Open** $\rightarrow$ Arahkan ke folder `android_technician_app`.
3. Tunggu proses sinkronisasi Gradle selesai.
4. Klik menu: **Build** $\rightarrow$ **Build Bundle(s) / APK(s)** $\rightarrow$ **Build APK(s)**.
5. Android Studio akan menghasilkan file:
   ```
   android_technician_app\app\build\outputs\apk\debug\app-debug.apk
   ```
6. Kirim file `.apk` tersebut ke smartphone teknisi via WhatsApp/kabel USB lalu pasang (*Install*).

---

## 4. Keunggulan untuk Sidang Tugas Akhir & Perusahaan

| Aspek | Tampilan Browser Biasa (Sebelumnya) | Aplikasi Standalone BACS (Sekarang) |
| :--- | :--- | :--- |
| **User Experience** | Terlihat seperti website lokal biasa (`localhost:5000`) | Tampil sebagai software desktop mandiri & aplikasi Android berkelas enterprise |
| **Keamanan Terminal** | Teknisi bisa menutup tab, mengetik URL lain, membuka inspect element | Terkunci dalam mode Kiosk e-Gate borderless (*Kiosk Locked*) |
| **Mobilitas Teknisi** | Harus membuka browser HP dan mengetik IP berkali-kali | Cukup ketuk ikon aplikasi di menu Android smartphone teknisi |
| **Nilai Akademik (TA)** | Dinilai hanya sebagai "Web App biasa" | Dinilai tinggi sebagai **Sistem Terpadu Multi-Platform (IoT, Desktop Kiosk, & Mobile Client)** |
