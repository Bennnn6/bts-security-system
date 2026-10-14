from datetime import datetime
import threading


# =========================================================
# HARDWARE / IOT RELAY WEBHOOK
# =========================================================

def _send_gate_hardware_webhook(event, site_id, device_id):
    """
    Kirim sinyal trigger ke hardware fisik (ESP32 / Relay Solenoid) via Webhook
    secara asinkron dengan enkripsi HMAC-SHA256 agar tidak memblokir sistem.
    """
    def _worker():
        try:
            from app import app
            webhook_url = app.config.get("GATE_WEBHOOK_URL", "").strip()
            if not webhook_url:
                return

            import json
            import hmac
            import hashlib
            import requests

            webhook_secret = app.config.get("GATE_WEBHOOK_SECRET", "").strip()
            payload = {
                "event": event,
                "site_id": site_id,
                "device_id": device_id,
                "timestamp": datetime.utcnow().isoformat(),
            }
            body_bytes = json.dumps(payload, separators=(',', ':')).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
            }
            if webhook_secret:
                # B1 FIX: Gunakan hmac.new() bukan hmac.new (AttributeError)
                # B2 FIX: Hanya kirim HMAC signature, BUKAN raw secret plaintext
                signature = hmac.new(
                    webhook_secret.encode("utf-8"),
                    body_bytes,
                    hashlib.sha256
                ).hexdigest()
                headers["X-Gate-Signature"] = f"sha256={signature}"
                # JANGAN kirim X-Gate-Secret: headers ini bocorkan secret dalam plaintext!

            requests.post(webhook_url, data=body_bytes, headers=headers, timeout=3)
        except Exception as err:
            print("[HARDWARE WEBHOOK ERROR]", err)

    threading.Thread(target=_worker, daemon=True).start()



# =========================================================
# DEFAULT SITE
# =========================================================

DEFAULT_SITE_ID = "DEFAULT"


# =========================================================
# GATE STATE PER SITE
# =========================================================

_gate_states = {}


# =========================================================
# DEVICE NORMALIZATION
# =========================================================

def _normalize_device_id(device_id):
    """
    Memastikan device ID selalu berupa string
    dan tidak melebihi panjang yang aman.
    """

    if not device_id:
        return "LOCAL_SIMULATION"

    return str(device_id).strip()[:500]


# =========================================================
# SITE NORMALIZATION
# =========================================================

def _normalize_site_id(site_id):
    """
    Memastikan site ID selalu berupa string.
    """

    if not site_id:
        return DEFAULT_SITE_ID

    return str(site_id).strip()[:100]


# =========================================================
# CREATE / GET SITE STATE
# =========================================================

def _get_state(site_id=None):
    """
    Mengambil state gate berdasarkan site_id.

    Setiap Site BTS memiliki state sendiri.
    """

    site_id = _normalize_site_id(site_id)

    if site_id not in _gate_states:

        _gate_states[site_id] = {
            "status": "CLOSED",
            "health": "NORMAL",

            "opened_at": None,

            "closed_at": datetime.utcnow(),

            "last_error": None,

            "last_action": "SYSTEM_STARTUP",

            "device_id": "LOCAL_SIMULATION",

            "kiosk_online": False,
            "last_heartbeat": None,
            "door_sensor": "CLOSED",
            "sensor_updated_at": datetime.utcnow(),
        }

    return _gate_states[site_id]


# =========================================================
# INTERNAL STATE UPDATE
# =========================================================

def _set_gate_error(
    action,
    error,
    site_id=None
):
    """
    Menyimpan kondisi error gate
    untuk Site BTS tertentu.
    """

    state = _get_state(site_id)

    state["health"] = "ERROR"

    state["last_error"] = str(error)

    state["last_action"] = action


# =========================================================
# OPEN GATE
# =========================================================

def open_gate(
    device_id="LOCAL_SIMULATION",
    action_name="OPEN",
    site_id=None
):
    """
    Membuka gate untuk Site BTS tertentu.

    action_name:
    - OPEN
    - MANUAL_OPEN
    """

    device_id = _normalize_device_id(
        device_id
    )

    site_id = _normalize_site_id(
        site_id
    )

    state = _get_state(site_id)

    try:

        state["device_id"] = device_id

        state["status"] = "OPEN"

        state["health"] = "NORMAL"

        state["opened_at"] = (
            datetime.utcnow()
        )

        state["closed_at"] = None

        state["last_error"] = None

        state["last_action"] = (
            action_name
        )

        print(
            f"[HARDWARE] OPEN GATE | "
            f"SITE={site_id} | "
            f"DEVICE={device_id}"
        )

        print(
            f"[HARDWARE] "
            f"GATE STATUS [{site_id}] = OPEN"
        )

        _send_gate_hardware_webhook("GATE_OPEN", site_id, device_id)

        return True

    except Exception as exc:

        _set_gate_error(
            "MANUAL_OPEN_ERROR"
            if action_name == "MANUAL_OPEN"
            else "OPEN_ERROR",
            exc,
            site_id
        )

        print(
            "[HARDWARE] OPEN ERROR:",
            exc
        )

        return False


# =========================================================
# CLOSE GATE
# =========================================================

def close_gate(
    device_id="LOCAL_SIMULATION",
    action_name="CLOSE",
    site_id=None
):
    """
    Menutup gate untuk Site BTS tertentu.

    action_name:
    - CLOSE
    - MANUAL_CLOSE
    """

    device_id = _normalize_device_id(
        device_id
    )

    site_id = _normalize_site_id(
        site_id
    )

    state = _get_state(site_id)

    try:

        state["device_id"] = device_id

        state["status"] = "CLOSED"

        state["health"] = "NORMAL"

        state["closed_at"] = (
            datetime.utcnow()
        )

        state["opened_at"] = None

        state["last_error"] = None

        state["last_action"] = (
            action_name
        )

        print(
            f"[HARDWARE] CLOSE GATE | "
            f"SITE={site_id} | "
            f"DEVICE={device_id}"
        )

        print(
            f"[HARDWARE] "
            f"GATE STATUS [{site_id}] = CLOSED"
        )

        _send_gate_hardware_webhook("GATE_CLOSE", site_id, device_id)

        return True

    except Exception as exc:

        _set_gate_error(
            "MANUAL_CLOSE_ERROR"
            if action_name == "MANUAL_CLOSE"
            else "CLOSE_ERROR",
            exc,
            site_id
        )

        print(
            "[HARDWARE] CLOSE ERROR:",
            exc
        )

        return False


# =========================================================
# MANUAL OPEN
# =========================================================

def force_open_gate(
    device_id="LOCAL_SIMULATION",
    site_id=None
):
    """
    Manual override untuk membuka gate
    pada Site BTS tertentu.
    """

    return open_gate(
        device_id=device_id,
        action_name="MANUAL_OPEN",
        site_id=site_id
    )


# =========================================================
# MANUAL CLOSE
# =========================================================

def force_close_gate(
    device_id="LOCAL_SIMULATION",
    site_id=None
):
    """
    Manual override untuk menutup gate
    pada Site BTS tertentu.
    """

    return close_gate(
        device_id=device_id,
        action_name="MANUAL_CLOSE",
        site_id=site_id
    )


# =========================================================
# GET GATE STATUS
# =========================================================

def get_gate_status(
    site_id=None
):
    """
    Mengambil status gate berdasarkan Site BTS.
    """

    state = _get_state(site_id)

    return state["status"]


# =========================================================
# GET GATE HEALTH
# =========================================================

def get_gate_health(
    site_id=None
):
    """
    Mengambil health gate berdasarkan Site BTS.
    """

    state = _get_state(site_id)

    return state["health"]


# =========================================================
# GET OPENED TIME
# =========================================================

def get_gate_opened_at(
    site_id=None
):
    """
    Mengambil waktu gate dibuka
    berdasarkan Site BTS.
    """

    state = _get_state(site_id)

    return state["opened_at"]


# =========================================================
# GET CLOSED TIME
# =========================================================

def get_gate_closed_at(
    site_id=None
):
    """
    Mengambil waktu gate ditutup
    berdasarkan Site BTS.
    """

    state = _get_state(site_id)

    return state["closed_at"]


# =========================================================
# GET LAST ERROR
# =========================================================

def get_gate_last_error(
    site_id=None
):
    """
    Mengambil error terakhir
    berdasarkan Site BTS.
    """

    state = _get_state(site_id)

    return state["last_error"]


# =========================================================
# GET LAST ACTION
# =========================================================

def get_gate_last_action(
    site_id=None
):
    """
    Mengambil action terakhir
    berdasarkan Site BTS.
    """

    state = _get_state(site_id)

    return state["last_action"]


# =========================================================
# GET DEVICE
# =========================================================

def get_gate_device_id(
    site_id=None
):
    """
    Mengambil device ID
    berdasarkan Site BTS.
    """

    state = _get_state(site_id)

    return state["device_id"]


# =========================================================
# HEALTH CHECK
# =========================================================

def check_gate_health(
    site_id=None
):
    """
    Mengembalikan informasi kondisi gate
    untuk Site BTS tertentu.
    """

    site_id = _normalize_site_id(
        site_id
    )

    state = _get_state(site_id)

    if state["health"] == "ERROR":

        return {
            "healthy": False,

            "health": "ERROR",

            "status": state["status"],

            "message": (
                state["last_error"]
                or "Gate mengalami error."
            ),

            "last_action": (
                state["last_action"]
            ),

            "device_id": (
                state["device_id"]
            ),

            "site_id": site_id,
        }

    return {
        "healthy": True,

        "health": "NORMAL",

        "status": state["status"],

        "message": "Gate bekerja normal.",

        "last_action": (
            state["last_action"]
        ),

        "device_id": (
            state["device_id"]
        ),

        "site_id": site_id,
    }


# =========================================================
# COMPLETE GATE STATUS
# =========================================================

def get_gate_info(
    site_id=None
):
    """
    Mengembalikan seluruh informasi gate
    dalam satu dictionary.
    """

    site_id = _normalize_site_id(
        site_id
    )

    state = _get_state(site_id)

    health = check_gate_health(
        site_id
    )

    return {
        "site_id": site_id,

        "status": state["status"],

        "health": state["health"],

        "healthy": health["healthy"],

        "opened_at": state["opened_at"],

        "closed_at": state["closed_at"],

        "last_error": state["last_error"],

        "last_action": state["last_action"],

        "device_id": state["device_id"],

        "message": health["message"],
    }


# =========================================================
# RESET ONE SITE
# =========================================================

def reset_gate(
    site_id=None
):
    """
    Reset gate untuk satu Site BTS.
    """

    site_id = _normalize_site_id(
        site_id
    )

    _gate_states[site_id] = {
        "status": "CLOSED",

        "health": "NORMAL",

        "opened_at": None,

        "closed_at": datetime.utcnow(),

        "last_error": None,

        "last_action": "RESET",

        "device_id": "LOCAL_SIMULATION",
    }

    return True


# =========================================================
# RESET ALL SITES
# =========================================================

def reset_all_gates():
    """
    Reset seluruh state gate.
    """

    _gate_states.clear()

    return True


# =========================================================
# GET ALL GATE STATES
# =========================================================

def get_all_gate_states():
    """
    Mengambil seluruh status gate dari
    semua Site BTS yang pernah digunakan.
    """

    result = {}

    for site_id, state in _gate_states.items():

        result[site_id] = {
            "site_id": site_id,

            "status": state["status"],

            "health": state["health"],

            "healthy": (
                state["health"]
                != "ERROR"
            ),

            "opened_at": state["opened_at"],

            "closed_at": state["closed_at"],

            "last_error": state["last_error"],

            "last_action": state["last_action"],

            "device_id": state["device_id"],
        }

    return result


# =========================================================
# KIOSK HEARTBEAT & MONITORING
# =========================================================

def record_kiosk_heartbeat(
    site_id=None,
    device_id="KIOSK_TERMINAL"
):
    """
    Mencatat heartbeat (ping) dari terminal fisik gerbang BTS.
    """
    site_id = _normalize_site_id(site_id)
    state = _get_state(site_id)

    state["kiosk_online"] = True
    state["last_heartbeat"] = datetime.utcnow()
    state["device_id"] = _normalize_device_id(device_id)
    return True


def get_kiosk_status(site_id=None):
    """
    Mengambil status liveness terminal kiosk untuk Site BTS.
    Jika tidak ada ping dalam 60 detik, status dianggap OFFLINE.
    """
    site_id = _normalize_site_id(site_id)
    state = _get_state(site_id)

    last_hb = state.get("last_heartbeat")
    is_online = False
    seconds_ago = None

    if last_hb:
        delta = (datetime.utcnow() - last_hb).total_seconds()
        seconds_ago = int(delta)
        is_online = delta <= 60

    state["kiosk_online"] = is_online

    return {
        "site_id": site_id,
        "is_online": is_online,
        "status": "ONLINE" if is_online else "OFFLINE",
        "last_heartbeat": last_hb.isoformat() if last_hb else None,
        "seconds_since_ping": seconds_ago,
        "gate_status": state.get("status", "CLOSED"),
        "door_sensor": state.get("door_sensor", "CLOSED"),
        "device_id": state.get("device_id", "LOCAL_SIMULATION"),
    }


# =========================================================
# DOOR SENSOR / REED SWITCH HANDLER
# =========================================================

def update_door_sensor(
    site_id=None,
    event="DOOR_CLOSED",
    device_id="KIOSK_TERMINAL"
):
    """
    Memperbarui status sensor magnetik kontak pintu pada gerbang BTS.
    event:
    - DOOR_CLOSED   : Pintu ditutup rapat secara normal
    - DOOR_OPENED   : Pintu terbuka secara normal
    - DOOR_FORCED   : Pintu dibuka paksa / didobrak (FORCED ENTRY ALARM)
    - DOOR_AJAR     : Pintu dibiarkan terbuka terlalu lama (DOOR AJAR ALERT)
    """
    site_id = _normalize_site_id(site_id)
    state = _get_state(site_id)

    event_upper = str(event).strip().upper()
    state["door_sensor"] = event_upper
    state["sensor_updated_at"] = datetime.utcnow()

    if event_upper == "DOOR_FORCED":
        state["health"] = "WARNING"
        state["last_error"] = "Sensor pintu mendeteksi pembobolan / paksa buka!"
    elif event_upper == "DOOR_CLOSED" and state["health"] != "ERROR":
        state["health"] = "NORMAL"
        state["last_error"] = None

    _send_gate_hardware_webhook(event_upper, site_id, device_id)
    return True


def get_door_sensor_status(site_id=None):
    """
    Mengambil status sensor magnet pintu gerbang.
    """
    site_id = _normalize_site_id(site_id)
    state = _get_state(site_id)
    return {
        "site_id": site_id,
        "sensor_status": state.get("door_sensor", "CLOSED"),
        "updated_at": state.get("sensor_updated_at", datetime.utcnow()).isoformat(),
    }