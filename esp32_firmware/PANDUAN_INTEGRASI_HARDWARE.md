# PANDUAN LENGKAP INTEGRASI HARDWARE (ESP32, SOLENOID & BUZZER)
**Sistem Keamanan Akses Gerbang Menara BTS (BACS Enterprise)**

Dokumen ini menjelaskan langkah demi langkah cara menyambungkan alat fisik Anda (**ESP32 / ESP8266**, **Relay Solenoid Lock**, **Buzzer**, dan **LED Status**) ke Web Application BACS.

---

## 1. Arsitektur Komunikasi Web ke Alat

```
+------------------------------------+               HTTP POST Webhook              +------------------------------------+
|         WEB SERVER FLASK           | -------------------------------------------> |           ESP32 HARDWARE           |
| (Face Recognition / OTP / Kiosk)   |  URL: http://<IP_ESP32>/gate/relay           | (Web Server Port 80)               |
|                                    |  JSON: {"event": "GATE_OPEN", ...}           |                                    |
+------------------------------------+                                              +------------------------------------+
                                                                                               |         |         |
                                                                                    +----------+         |         +----------+
                                                                                    v                    v                    v
                                                                             +--------------+     +--------------+     +--------------+
                                                                             | RELAY 5V &   |     |    BUZZER    |     |  LED STATUS  |
                                                                             | SOLENOID 12V |     | (Sirine/Beep)|     | (Hijau/Merah)|
                                                                             +--------------+     +--------------+     +--------------+
```

Alur kerja:
1. Teknisi melakukan verifikasi wajah (**Face ID**) atau memasukkan kode OTP di Web/Kiosk.
2. Jika akses dinyatakan **GRANTED**, backend Flask secara otomatis mengirimkan request `HTTP POST` ke URL ESP32 (`GATE_WEBHOOK_URL`).
3. ESP32 menerima sinyal, menyalakan relay (menarik kunci solenoid gerbang), menyalakan LED Hijau, dan membunyikan nada konfirmasi buzzer.
4. Setelah durasi waktu habis (default 10 detik, sesuai setting `GATE_OPEN_SECONDS`), server mengirim `GATE_CLOSE` agar relay mati dan gerbang terkunci kembali.

---

## 2. Skema Rangkaian Perkabelan (Wiring Pinout)

### A. Tabel Sambungan Pin ESP32

| Komponen | Pin Komponen | Terhubung ke Pin ESP32 | Keterangan |
| :--- | :--- | :--- | :--- |
| **Modul Relay 5V** | **VCC** | **VIN (5V)** | Sumber daya modul relay |
| | **GND** | **GND** | Ground ESP32 |
| | **IN** | **GPIO 23** | Sinyal pemicu relay buka gerbang |
| **Buzzer Aktif 5V** | **Positif (+)** | **GPIO 19** | Bunyi konfirmasi & alarm |
| | **Negatif (-)** | **GND** | Ground ESP32 |
| **LED Hijau (Buka)** | **Anoda (+)** | **GPIO 18** (via Resistor 220Ω) | Indikator akses diterima |
| | **Katoda (-)** | **GND** | Ground ESP32 |
| **LED Merah (Kunci)** | **Anoda (+)** | **GPIO 4** (via Resistor 220Ω) | Indikator standby / terkunci |
| | **Katoda (-)** | **GND** | Ground ESP32 |

---

### B. Rangkaian Daya Solenoid Door Lock (PENTING!)

> [!CAUTION]
> **JANGAN PERNAH** memberi daya Solenoid Door Lock 12V langsung dari pin ESP32 (3.3V atau 5V)! Arus solenoid yang besar (~1A - 2A) dapat merusak mikrokontroler ESP32 secara permanen.

Gunakan adaptor eksternal **12V DC (minimal 2 Ampere)** dengan skema berikut:

```
[Adaptor 12V (+)] ---------> [Pin COM pada Modul Relay]
                             [Pin NO pada Modul Relay] ---------> [Kabel Merah Solenoid (+)]

[Adaptor 12V (-)] ----------------------------------------------> [Kabel Hitam Solenoid (-)]
       |
       +--------------------> [Pin GND pada ESP32] (Wajib Common Ground)
```

> [!TIP]
> **Dioda Flyback (1N4007):** Pasang 1 buah dioda 1N4007 secara paralel terbalik di kedua kutub kabel solenoid (ujung bergaris/katoda ke kabel +, ujung polos/anoda ke kabel -) untuk meredam lonjakan arus induksi saat solenoid mati.

---

## 3. Langkah Upload Firmware ke ESP32

1. Buka aplikasi **Arduino IDE**.
2. Masuk ke menu **Tools** -> **Board** -> pilih **DOIT ESP32 DEVKIT V1** (atau tipe board ESP32 Anda).
3. Instal library yang dibutuhkan:
   - Masuk ke menu **Sketch** -> **Include Library** -> **Manage Libraries...**
   - Cari: **ArduinoJson** (oleh *Benoit Blanchon*), lalu klik **Install** (disarankan versi 6 atau 7).
4. Buka file firmware proyek:
   ```
   esp32_firmware/esp32_firmware.ino
   ```
5. Pada baris 24-25, ubah nama WiFi dan Password sesuai WiFi yang digunakan laptop dan ESP32:
   ```cpp
   const char* WIFI_SSID     = "NAMA_WIFI_ANDA";
   const char* WIFI_PASSWORD = "PASSWORD_WIFI_ANDA";
   ```
   *(Pastikan laptop server dan ESP32 berada di satu jaringan Wi-Fi yang sama).*
6. Hubungkan kabel USB ESP32 ke laptop, pilih Port COM yang sesuai, lalu klik tombol **Upload**.

---

## 4. Melihat IP Address & Pengujian Mandiri ESP32

1. Setelah upload selesai, buka **Serial Monitor** di Arduino IDE (atur baud rate ke **115200**).
2. Tekan tombol **EN / RST** pada board ESP32.
3. Anda akan melihat log koneksi seperti ini:
   ```text
   [WIFI] Menghubungkan ke SSID: MyWiFi
   ....
   [WIFI] BERHASIL TERHUBUNG!
   [WIFI] IP Address ESP32 : 192.168.1.15
   [WIFI] URL Webhook Relay : http://192.168.1.15/gate/relay
   ```
4. Buka browser di laptop/HP Anda, lalu buka alamat: `http://192.168.1.15` (sesuaikan dengan IP yang muncul).
5. Akan muncul tampilan **BACS ESP32 Gate Controller**:
   - Klik **TEST BUKA GERBANG** -> Relay akan berbunyi *klik*, lampu hijau menyala, dan solenoid tertarik.
   - Klik **TEST KUNCI GERBANG** -> Relay mati, lampu merah menyala, dan solenoid mengunci kembali.
   - Klik **TEST SIRINE ALARM** -> Buzzer berbunyi sirine darurat.

---

## 5. Menghubungkan ke Web Aplikasi Flask (File `.env`)

1. Buka file `.env` di folder utama proyek `bts-security-system`.
2. Cari baris `GATE_WEBHOOK_URL` (paling bawah), lalu isi dengan alamat IP ESP32 Anda:
   ```env
   # Ganti 192.168.1.15 dengan IP ESP32 yang didapatkan di Serial Monitor
   GATE_WEBHOOK_URL=http://192.168.1.15/gate/relay
   ```
3. Simpan file `.env`.
4. Jalankan ulang server Flask:
   ```powershell
   python run.py
   ```
   *(atau buka aplikasi desktop via `BACS_App.bat` / `BACS_App.vbs`)*.

---

## 6. Verifikasi Integrasi End-to-End

Sekarang coba buka gerbang melalui web:
1. Buka halaman **Simulasi Akses / Kiosk** (`http://localhost:5000/access` atau `http://localhost:5000/kiosk`).
2. Masukkan Kode OTP Teknisi yang valid (atau lakukan Face Recognition).
3. Saat layar menampilkan **Akses Diterima (GRANTED)**:
   - Server Flask akan langsung memicu ESP32 melalui webhook.
   - Relay akan menyala secara fisik membuka kunci gerbang!
   - Buzzer membunyikan nada konfirmasi ganda.
   - Setelah 10 detik, relay akan otomatis mati dan mengunci kembali gerbang.
