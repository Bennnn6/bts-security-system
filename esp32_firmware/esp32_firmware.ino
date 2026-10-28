/*
 * ======================================================================================
 * BACS (Base Transceiver Station Access Control System) - ESP32 Gate Controller Firmware
 * ======================================================================================
 * Firmware ini dipasang pada mikrokontroler ESP32 untuk mengontrol:
 * 1. Relay Kunci Solenoid Gerbang (Solenoid Door Lock 12V)
 * 2. Buzzer Peringatan & Sirine Alarm
 * 3. Lampu LED Indikator Status (Hijau: Terbuka, Merah: Terkunci/Standby)
 * 
 * Sistem menerima perintah HTTP POST Webhook dari Server Flask BACS:
 * URL Endpoint: http://<IP_ESP32>/gate/relay
 * Payload JSON : {"event": "GATE_OPEN" | "GATE_CLOSE", "site_id": "...", "device_id": "..."}
 * ======================================================================================
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>

// ======================================================================================
// 1. KONFIGURASI WI-FI (Sesuaikan dengan Wi-Fi / Hotspot Anda)
// ======================================================================================
const char* WIFI_SSID     = "NAMA_WIFI_ATAU_HOTSPOT_ANDA";
const char* WIFI_PASSWORD = "PASSWORD_WIFI_ANDA";

// ======================================================================================
// 2. KONFIGURASI PINOUT HARDWARE (ESP32)
// ======================================================================================
const int PIN_RELAY     = 23;  // Terhubung ke pin IN modul relay (Solenoid Lock)
const int PIN_BUZZER    = 19;  // Terhubung ke pin Positif (+) Buzzer aktif
const int PIN_LED_GREEN = 18;  // Terhubung ke Anoda LED Hijau (Akses Diterima/Buka)
const int PIN_LED_RED   = 4;   // Terhubung ke Anoda LED Merah (Standby/Terkunci)
const int PIN_LED_BOARD = 2;   // LED Onboard ESP32

// Modul relay umumnya ACTIVE LOW (LOW = Relay Menyala/Tertutup kontak, HIGH = Mati)
// Ubah menjadi false jika modul relay Anda berjenis ACTIVE HIGH.
const bool RELAY_ACTIVE_LOW = true;

// Web server berjalan pada port 80 (standar HTTP)
WebServer server(80);

// Status gerbang fisik
bool isGateOpen = false;
unsigned long lastAlarmTrigger = 0;
bool isAlarmActive = false;

// ======================================================================================
// HELPER HARDWARE
// ======================================================================================
void setRelay(bool turnOn) {
    if (RELAY_ACTIVE_LOW) {
        digitalWrite(PIN_RELAY, turnOn ? LOW : HIGH);
    } else {
        digitalWrite(PIN_RELAY, turnOn ? HIGH : LOW);
    }
}

void beep(int frequency, int durationMs) {
    tone(PIN_BUZZER, frequency, durationMs);
    delay(durationMs);
    noTone(PIN_BUZZER);
}

void soundAccessGranted() {
    tone(PIN_BUZZER, 1800, 100);
    delay(120);
    tone(PIN_BUZZER, 2400, 150);
    delay(160);
    noTone(PIN_BUZZER);
}

void soundAccessLocked() {
    tone(PIN_BUZZER, 1000, 120);
    delay(150);
    tone(PIN_BUZZER, 800, 150);
    delay(160);
    noTone(PIN_BUZZER);
}

void executeGateOpen() {
    isGateOpen = true;
    isAlarmActive = false;
    
    // Tarik kunci solenoid (Relay Aktif)
    setRelay(true);
    
    // Indikator LED
    digitalWrite(PIN_LED_GREEN, HIGH);
    digitalWrite(PIN_LED_RED, LOW);
    digitalWrite(PIN_LED_BOARD, HIGH);
    
    // Nada konfirmasi
    soundAccessGranted();
    
    Serial.println("[HARDWARE] GERBANG DIBUKA (Relay ON / Solenoid Unlocked)");
}

void executeGateClose() {
    isGateOpen = false;
    isAlarmActive = false;
    
    // Lepas solenoid (Relay Mati -> Solenoid Mengunci Rapat)
    setRelay(false);
    
    // Indikator LED
    digitalWrite(PIN_LED_GREEN, LOW);
    digitalWrite(PIN_LED_RED, HIGH);
    digitalWrite(PIN_LED_BOARD, LOW);
    
    // Nada konfirmasi
    soundAccessLocked();
    
    Serial.println("[HARDWARE] GERBANG TERKUNCI (Relay OFF / Solenoid Locked)");
}

void triggerAlarm() {
    isAlarmActive = true;
    lastAlarmTrigger = millis();
    Serial.println("[HARDWARE] ALARM DIAKTIFKAN!");
}

// ======================================================================================
// ENDPOINTS WEB SERVER
// ======================================================================================

// 1. Endpoint Webhook: POST /gate/relay (Dipanggil otomatis oleh server Flask BACS)
void handleGateRelayWebhook() {
    if (server.method() != HTTP_POST) {
        server.send(405, "application/json", "{\"status\":\"ERROR\",\"message\":\"Method Not Allowed. Use POST.\"}");
        return;
    }

    String requestBody = server.arg("plain");
    Serial.println("\n[WEBHOOK RECEIVED] Payload:");
    Serial.println(requestBody);

    StaticJsonDocument<512> doc;
    DeserializationError error = deserializeJson(doc, requestBody);

    if (error) {
        Serial.print("[JSON ERROR] Parse failed: ");
        Serial.println(error.f_str());
        server.send(400, "application/json", "{\"status\":\"ERROR\",\"message\":\"Invalid JSON payload\"}");
        return;
    }

    String event = doc["event"] | "";
    String siteId = doc["site_id"] | "UNKNOWN";
    String deviceId = doc["device_id"] | "UNKNOWN";

    event.toUpperCase();

    if (event == "GATE_OPEN" || event == "OPEN") {
        executeGateOpen();
        
        StaticJsonDocument<256> res;
        res["status"] = "SUCCESS";
        res["event"] = "GATE_OPEN";
        res["site_id"] = siteId;
        res["gate_status"] = "OPEN";
        res["message"] = "Relay gerbang berhasil dibuka!";
        
        String responseStr;
        serializeJson(res, responseStr);
        server.send(200, "application/json", responseStr);
        
    } else if (event == "GATE_CLOSE" || event == "CLOSE") {
        executeGateClose();
        
        StaticJsonDocument<256> res;
        res["status"] = "SUCCESS";
        res["event"] = "GATE_CLOSE";
        res["site_id"] = siteId;
        res["gate_status"] = "CLOSED";
        res["message"] = "Relay gerbang berhasil dikunci!";
        
        String responseStr;
        serializeJson(res, responseStr);
        server.send(200, "application/json", responseStr);
        
    } else if (event == "DOOR_FORCED" || event == "ALARM") {
        triggerAlarm();
        
        StaticJsonDocument<256> res;
        res["status"] = "SUCCESS";
        res["event"] = "ALARM_TRIGGERED";
        res["site_id"] = siteId;
        res["message"] = "Sirine alarm darurat dibunyikan!";
        
        String responseStr;
        serializeJson(res, responseStr);
        server.send(200, "application/json", responseStr);
        
    } else {
        server.send(400, "application/json", "{\"status\":\"ERROR\",\"message\":\"Unknown event type\"}");
    }
}

// 2. Endpoint Halaman Uji Manual via Browser (GET /)
void handleRootPage() {
    String html = "<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>";
    html += "<title>BACS ESP32 Gate Controller</title>";
    html += "<style>body{font-family:Segoe UI,sans-serif;background:#0f172a;color:#f8fafc;padding:24px;text-align:center}";
    html += ".card{max-width:440px;margin:20px auto;background:#1e293b;padding:24px;border-radius:16px;box-shadow:0 10px 25px rgba(0,0,0,0.5);border:1px solid #334155}";
    html += ".btn{display:block;width:100%;padding:14px;margin:10px 0;font-size:16px;font-weight:bold;border:none;border-radius:10px;cursor:pointer;transition:0.2s}";
    html += ".btn-open{background:#10b981;color:#fff}.btn-close{background:#ef4444;color:#fff}.btn-alarm{background:#f59e0b;color:#111}";
    html += ".badge{display:inline-block;padding:6px 14px;border-radius:20px;font-weight:bold;margin:12px 0}";
    html += ".badge-open{background:#064e3b;color:#34d399}.badge-closed{background:#450a0a;color:#f87171}";
    html += "pre{background:#0b1120;padding:12px;border-radius:8px;font-size:13px;text-align:left;overflow-x:auto;color:#38bdf8}";
    html += "</style></head><body><div class='card'>";
    html += "<h2>🏢 BACS Gate Controller</h2>";
    html += "<p style='color:#94a3b8'>ESP32 Hardware Relay & Alarm Node</p>";
    
    if (isGateOpen) {
        html += "<div class='badge badge-open'>🔓 STATUS: GERBANG TERBUKA (RELAY ON)</div>";
    } else {
        html += "<div class='badge badge-closed'>🔒 STATUS: GERBANG TERKUNCI (STANDBY)</div>";
    }
    
    html += "<a href='/test/open'><button class='btn btn-open'>⚡ TEST BUKA GERBANG (RELAY ON)</button></a>";
    html += "<a href='/test/close'><button class='btn btn-close'>🔒 TEST KUNCI GERBANG (RELAY OFF)</button></a>";
    html += "<a href='/test/alarm'><button class='btn btn-alarm'>🚨 TEST SIRINE ALARM</button></a>";
    
    html += "<hr style='border:0;border-top:1px solid #334155;margin:20px 0'>";
    html += "<div style='text-align:left;font-size:13px;color:#94a3b8'>Konfigurasi di file <b>.env</b> server Anda:</div>";
    html += "<pre>GATE_WEBHOOK_URL=http://" + WiFi.localIP().toString() + "/gate/relay</pre>";
    html += "</div></body></html>";
    
    server.send(200, "text/html", html);
}

// 3. Endpoint Test Cepat Manual
void handleTestOpen() {
    executeGateOpen();
    server.sendHeader("Location", "/");
    server.send(303);
}

void handleTestClose() {
    executeGateClose();
    server.sendHeader("Location", "/");
    server.send(303);
}

void handleTestAlarm() {
    triggerAlarm();
    server.sendHeader("Location", "/");
    server.send(303);
}

// ======================================================================================
// SETUP
// ======================================================================================
void setup() {
    Serial.begin(115200);
    delay(500);

    Serial.println("\n\n========================================================");
    Serial.println("  BACS (BTS Access Control System) - ESP32 Gate Node    ");
    Serial.println("========================================================");

    // Inisialisasi Pinout
    pinMode(PIN_RELAY, OUTPUT);
    pinMode(PIN_BUZZER, OUTPUT);
    pinMode(PIN_LED_GREEN, OUTPUT);
    pinMode(PIN_LED_RED, OUTPUT);
    pinMode(PIN_LED_BOARD, OUTPUT);

    // Set Kondisi Awal (Terkunci / Standby)
    setRelay(false);
    digitalWrite(PIN_LED_GREEN, LOW);
    digitalWrite(PIN_LED_RED, HIGH);
    digitalWrite(PIN_LED_BOARD, LOW);
    noTone(PIN_BUZZER);

    // Hubungkan ke Wi-Fi
    Serial.print("[WIFI] Menghubungkan ke SSID: ");
    Serial.println(WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 40) {
        delay(500);
        Serial.print(".");
        digitalWrite(PIN_LED_BOARD, !digitalRead(PIN_LED_BOARD));
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\n[WIFI] BERHASIL TERHUBUNG!");
        Serial.print("[WIFI] IP Address ESP32 : ");
        Serial.println(WiFi.localIP());
        Serial.print("[WIFI] URL Webhook Relay : http://");
        Serial.print(WiFi.localIP());
        Serial.println("/gate/relay");
        
        // Bunyi konfirmasi startup berhasil (3 beep cepat)
        for (int i = 0; i < 3; i++) {
            tone(PIN_BUZZER, 2000, 80);
            delay(120);
        }
        noTone(PIN_BUZZER);
        digitalWrite(PIN_LED_BOARD, LOW);
    } else {
        Serial.println("\n[WIFI ERROR] Gagal terhubung ke Wi-Fi. Periksa SSID & Password!");
        // Bunyi peringatan error
        tone(PIN_BUZZER, 500, 1000);
    }

    // Registrasi Rute Web Server
    server.on("/", HTTP_GET, handleRootPage);
    server.on("/gate/relay", HTTP_POST, handleGateRelayWebhook);
    server.on("/test/open", HTTP_GET, handleTestOpen);
    server.on("/test/close", HTTP_GET, handleTestClose);
    server.on("/test/alarm", HTTP_GET, handleTestAlarm);

    server.begin();
    Serial.println("[HTTP SERVER] Web Server ESP32 Siap menerima perintah!");
    Serial.println("========================================================\n");
}

// ======================================================================================
// LOOP UTAMA
// ======================================================================================
void loop() {
    server.handleClient();

    // Logika sirine saat alarm aktif (berbunyi selama 5 detik)
    if (isAlarmActive) {
        if (millis() - lastAlarmTrigger < 5000) {
            // Bunyi sirine bergelombang
            for (int hz = 800; hz < 2200; hz += 50) {
                tone(PIN_BUZZER, hz, 15);
                delay(15);
            }
            digitalWrite(PIN_LED_RED, !digitalRead(PIN_LED_RED));
        } else {
            isAlarmActive = false;
            noTone(PIN_BUZZER);
            digitalWrite(PIN_LED_RED, isGateOpen ? LOW : HIGH);
            Serial.println("[HARDWARE] Alarm sirine berhenti.");
        }
    }

    // Auto-reconnect Wi-Fi jika terputus
    if (WiFi.status() != WL_CONNECTED) {
        static unsigned long lastReconnect = 0;
        if (millis() - lastReconnect > 10000) {
            lastReconnect = millis();
            Serial.println("[WIFI] Reconnecting...");
            WiFi.reconnect();
        }
    }
}
