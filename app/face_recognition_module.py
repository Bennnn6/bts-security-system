import base64
import json
import math
from flask import current_app

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import face_recognition
except ImportError:
    face_recognition = None

try:
    import numpy as np
except ImportError:
    np = None


# =========================================================
# BASE64 IMAGE -> OPENCV IMAGE
# =========================================================

def base64_to_image(base64_string):
    """
    Mengubah Data URI/base64 menjadi gambar OpenCV (BGR).
    Mendukung format data:image/jpeg;base64,/9j/... atau raw base64.
    """
    if cv2 is None or np is None:
        raise RuntimeError("Library pemrosesan citra (OpenCV/NumPy) belum tersedia.")

    if not base64_string or not isinstance(base64_string, str):
        raise ValueError("Data gambar tidak valid atau kosong.")

    base64_string = base64_string.strip()
    if "," in base64_string:
        _, encoded = base64_string.split(",", 1)
    else:
        encoded = base64_string

    encoded = encoded.strip()
    if not encoded:
        raise ValueError("Data base64 gambar kosong.")

    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Data base64 gambar tidak valid.") from exc

    if not data:
        raise ValueError("Gambar hasil decode kosong.")

    np_arr = np.frombuffer(data, np.uint8)
    image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Gambar tidak dapat dibaca oleh OpenCV.")

    return image


def _prepare_rgb_image(base64_image, max_width=1280):
    """
    Helper internal untuk decode, convert BGR->RGB, dan resize optimal.
    """
    img = base64_to_image(base64_image)
    rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    height, width = rgb_img.shape[:2]
    if width > max_width:
        scale = max_width / width
        new_width = max_width
        new_height = int(height * scale)
        rgb_img = cv2.resize(rgb_img, (new_width, new_height), interpolation=cv2.INTER_AREA)

    return rgb_img


# =========================================================
# ANTI-SPOOFING & LIVENESS DETECTION HELPERS
# =========================================================

def calculate_eye_aspect_ratio(eye_points):
    """
    Menghitung Eye Aspect Ratio (EAR) dari 6 koordinat landmark mata.
    EAR = (||p2-p6|| + ||p3-p5||) / (2 * ||p1-p4||)
    """
    if not eye_points or len(eye_points) < 6:
        return 0.0

    try:
        p1, p2, p3, p4, p5, p6 = eye_points[:6]

        def dist(a, b):
            return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

        vertical_1 = dist(p2, p6)
        vertical_2 = dist(p3, p5)
        horizontal = dist(p1, p4)

        if horizontal <= 0.0001:
            return 0.0

        return float((vertical_1 + vertical_2) / (2.0 * horizontal))
    except Exception:
        return 0.0


def detect_screen_moire_and_glare(rgb_img, face_box):
    """
    Menganalisis tekstur alami wajah, blur/ketajaman (Laplacian),
    dan pantulan kilap cahaya (glare hotspot) khas layar kaca HP atau foto kertas.
    """
    if cv2 is None or np is None or rgb_img is None:
        return {
            "texture_score": 0.8,
            "skin_ratio": 0.5,
            "glare_ratio": 0.0,
            "laplacian_var": 120.0
        }

    h, w = rgb_img.shape[:2]
    top, right, bottom, left = face_box

    top = max(0, top)
    left = max(0, left)
    bottom = min(h, bottom)
    right = min(w, right)

    if bottom <= top or right <= left:
        return {
            "texture_score": 0.5,
            "skin_ratio": 0.3,
            "glare_ratio": 0.0,
            "laplacian_var": 50.0
        }

    face_roi = rgb_img[top:bottom, left:right]
    face_area = float(face_roi.shape[0] * face_roi.shape[1])

    # 1. Variansi Laplacian (Ketajaman alami vs Foto buram / cetak resolusi rendah)
    gray_face = cv2.cvtColor(face_roi, cv2.COLOR_RGB2GRAY)
    laplacian_var = float(cv2.Laplacian(gray_face, cv2.CV_64F).var())

    if laplacian_var < 30.0:
        texture_score = max(0.15, laplacian_var / 50.0)
    elif laplacian_var > 2800.0:
        texture_score = 0.5
    else:
        texture_score = min(1.0, 0.65 + (laplacian_var / 1200.0) * 0.35)

    # 2. Deteksi Pantulan Kilap Layar Kaca (Glare Hotspot)
    hsv_face = cv2.cvtColor(face_roi, cv2.COLOR_RGB2HSV)
    _, s_channel, v_channel = cv2.split(hsv_face)
    glare_mask = (v_channel > 248) & (s_channel < 35)
    glare_ratio = float(np.sum(glare_mask)) / face_area if face_area > 0 else 0.0

    # 3. Rasio Rona Warna Kulit Alami (YCrCb)
    ycrcb_face = cv2.cvtColor(face_roi, cv2.COLOR_RGB2YCrCb)
    _, cr, cb = cv2.split(ycrcb_face)
    skin_mask = (cr >= 130) & (cr <= 175) & (cb >= 75) & (cb <= 130)
    skin_ratio = float(np.sum(skin_mask)) / face_area if face_area > 0 else 0.0

    return {
        "texture_score": round(texture_score, 3),
        "skin_ratio": round(skin_ratio, 3),
        "glare_ratio": round(glare_ratio, 4),
        "laplacian_var": round(laplacian_var, 1)
    }


def _evaluate_liveness_from_face(rgb_img, face_box):
    """
    Evaluasi parameter liveness pada wajah yang sudah terdeteksi.
    """
    try:
        min_score = float(current_app.config.get('LIVENESS_MIN_SCORE', 0.55))
        strict_glare = bool(current_app.config.get('LIVENESS_STRICT_GLARE', True))
    except Exception:
        min_score = 0.55
        strict_glare = True

    analysis = detect_screen_moire_and_glare(rgb_img, face_box)
    texture_score = analysis.get("texture_score", 0.7)
    skin_ratio = analysis.get("skin_ratio", 0.5)
    glare_ratio = analysis.get("glare_ratio", 0.0)

    ear_avg = 0.30
    try:
        landmarks_list = face_recognition.face_landmarks(rgb_img, [face_box])
        if landmarks_list and len(landmarks_list) > 0:
            landmarks = landmarks_list[0]
            left_eye = landmarks.get('left_eye', [])
            right_eye = landmarks.get('right_eye', [])
            ear_l = calculate_eye_aspect_ratio(left_eye)
            ear_r = calculate_eye_aspect_ratio(right_eye)
            if ear_l > 0 and ear_r > 0:
                ear_avg = (ear_l + ear_r) / 2.0
    except Exception:
        ear_avg = 0.28

    if 0.16 <= ear_avg <= 0.42:
        eye_score = 0.95
    elif 0.10 <= ear_avg < 0.16:
        eye_score = 0.80
    else:
        eye_score = 0.50

    skin_score = min(1.0, max(0.2, skin_ratio * 1.6))
    composite_score = (texture_score * 0.40) + (skin_score * 0.35) + (eye_score * 0.25)

    glare_penalty = 0.0
    if strict_glare and glare_ratio > 0.05:
        glare_penalty = min(0.45, (glare_ratio - 0.05) * 5.0)
        composite_score = max(0.05, composite_score - glare_penalty)

    final_score = round(float(composite_score), 3)
    passed = bool(final_score >= min_score and glare_penalty <= 0.20)

    details = {
        "liveness_score": final_score,
        "texture_score": texture_score,
        "skin_ratio": skin_ratio,
        "glare_ratio": glare_ratio,
        "ear": round(float(ear_avg), 3),
        "laplacian_var": analysis.get("laplacian_var", 0.0),
        "passed": passed
    }

    if glare_penalty > 0.20:
        return False, final_score, details, "Terdeteksi pantulan kilap layar gadget atau foto cetak (Anti-Spoofing Alert)."

    if not passed:
        if texture_score < 0.35:
            msg = "Kualitas tekstur wajah tidak memenuhi syarat (Terlalu buram atau foto cetak)."
        elif skin_ratio < 0.20:
            msg = "Distribusi rona warna wajah tidak alami (Terindikasi tampilan layar digital)."
        else:
            msg = "Wajah tidak memenuhi parameter keaslian biometrik langsung (Liveness Failed)."
        return False, final_score, details, msg

    return True, final_score, details, "Wajah asli manusia hidup terverifikasi (Liveness Passed)."


# =========================================================
# HIGH-EFFICIENCY SINGLE-PASS FACE & LIVENESS PROCESSING
# =========================================================

def extract_face_encoding_and_liveness(base64_image, check_liveness=True):
    """
    Ekstraksi encoding wajah dan evaluasi keaslian (liveness) dalam SATU PASS.
    Menghindari decoding gambar dan kalkulasi HOG face location dua kali,
    menghemat ~50% beban CPU dan latensi per request.

    Return:
        (face_encoding: list | None, liveness_tuple: tuple, error_message: str | None)
    """
    default_liveness = (True, 1.0, {"mode": "bypass"}, "Liveness tidak aktif.")

    if face_recognition is None or cv2 is None or np is None:
        return None, (True, 1.0, {"mode": "bypass"}, "Face recognition engine tidak aktif."), "Engine belum tersedia di server."

    if not base64_image:
        return None, (False, 0.0, {}, "Gambar kosong."), "Gambar wajah tidak ditemukan."

    try:
        rgb_img = _prepare_rgb_image(base64_image)
    except Exception as exc:
        return None, (False, 0.0, {"error": str(exc)}, "Gagal memproses gambar."), "Gambar tidak valid atau rusak."

    try:
        face_locations = face_recognition.face_locations(rgb_img, model="hog")
    except Exception as exc:
        return None, (False, 0.0, {"error": str(exc)}, "Gagal mendeteksi lokasi wajah."), f"Gagal mendeteksi wajah: {exc}"

    if len(face_locations) == 0:
        return None, (False, 0.0, {"reason": "NO_FACE"}, "Wajah tidak terdeteksi."), "Wajah tidak terdeteksi. Silakan posisikan wajah di tengah kamera."

    if len(face_locations) > 1:
        return None, (False, 0.0, {"reason": "MULTIPLE_FACES"}, "Terdeteksi lebih dari satu wajah."), "Terdeteksi lebih dari satu wajah. Pastikan hanya satu orang berada di depan kamera."

    main_face = face_locations[0]

    # Evaluasi liveness jika diaktifkan
    liveness_tuple = default_liveness
    if check_liveness:
        liveness_tuple = _evaluate_liveness_from_face(rgb_img, main_face)

    # Ekstraksi encoding 128-D
    try:
        encodings = face_recognition.face_encodings(rgb_img, [main_face])
    except Exception as exc:
        return None, liveness_tuple, f"Gagal membuat encoding wajah: {exc}"

    if not encodings:
        return None, liveness_tuple, "Encoding wajah tidak berhasil dibuat."

    encoding_array = np.asarray(encodings[0], dtype=np.float64)
    if encoding_array.size != 128 or not np.all(np.isfinite(encoding_array)):
        return None, liveness_tuple, "Encoding wajah tidak valid."

    return encoding_array.tolist(), liveness_tuple, None


# =========================================================
# STANDARD PUBLIC INTERFACES (BACKWARD COMPATIBLE)
# =========================================================

def extract_face_encoding(base64_image):
    """
    Mengambil encoding wajah dari gambar kamera.
    Return: (encoding, message)
    """
    encoding, _, error_msg = extract_face_encoding_and_liveness(base64_image, check_liveness=False)
    if error_msg:
        return None, error_msg
    return encoding, "Success"


def analyze_liveness(base64_image):
    """
    Menganalisis keaslian wajah (Liveness & Anti-Spoofing).
    Return: (is_live: bool, score: float, details: dict, message: str)
    """
    _, liveness_tuple, _ = extract_face_encoding_and_liveness(base64_image, check_liveness=True)
    return liveness_tuple


def normalize_encoding(encoding):
    """
    Mengubah berbagai format encoding (list, tuple, ndarray, json, bytes)
    menjadi numpy.ndarray 128 dimensi bertipe float64.
    """
    if encoding is None or np is None:
        return None

    try:
        if isinstance(encoding, np.ndarray):
            result = np.asarray(encoding, dtype=np.float64).reshape(-1)
        elif isinstance(encoding, (list, tuple)):
            result = np.asarray(encoding, dtype=np.float64).reshape(-1)
        elif isinstance(encoding, bytes):
            try:
                decoded = encoding.decode("utf-8")
                result = np.asarray(json.loads(decoded), dtype=np.float64).reshape(-1)
            except Exception:
                import pickle
                result = np.asarray(pickle.loads(encoding), dtype=np.float64).reshape(-1)
        elif isinstance(encoding, str):
            val = encoding.strip()
            if not val:
                return None
            result = np.asarray(json.loads(val), dtype=np.float64).reshape(-1)
        else:
            return None

        if result.size != 128 or not np.all(np.isfinite(result)):
            return None

        return result
    except Exception:
        return None


def compare_faces(known_encoding, current_encoding):
    """
    Membandingkan 2 encoding wajah.
    Return: (match: bool, distance: float)
    """
    if face_recognition is None or np is None:
        return False, 1.0

    known_enc = normalize_encoding(known_encoding)
    current_enc = normalize_encoding(current_encoding)

    if known_enc is None or current_enc is None:
        return False, 1.0

    try:
        threshold = float(current_app.config.get("FACE_MATCH_THRESHOLD", 0.52))
    except Exception:
        threshold = 0.52

    try:
        distance = float(face_recognition.face_distance([known_enc], current_enc)[0])
        return (distance <= threshold), distance
    except Exception as exc:
        print("[FACE DISTANCE ERROR]", exc)
        return False, 1.0


# =========================================================
# VECTORIZED BATCH COMPARISON (ULTRA-FAST)
# =========================================================

def match_face_against_technicians(face_encoding, technicians, threshold=None):
    """
    Mencocokkan satu face_encoding terhadap daftar teknisi aktif secara sekaligus
    menggunakan kalkulasi matriks NumPy/BLAS.
    Return:
        (best_technician, best_distance, best_matched)
    """
    if face_recognition is None or np is None or not technicians or face_encoding is None:
        return None, None, False

    current_enc = normalize_encoding(face_encoding)
    if current_enc is None:
        return None, None, False

    if threshold is None:
        try:
            threshold = float(current_app.config.get("FACE_MATCH_THRESHOLD", 0.52))
        except Exception:
            threshold = 0.52

    known_encs = []
    valid_techs = []

    for tech in technicians:
        if not getattr(tech, "face_encoding", None):
            continue
        norm = normalize_encoding(tech.face_encoding)
        if norm is not None:
            known_encs.append(norm)
            valid_techs.append(tech)

    if not known_encs:
        return None, None, False

    try:
        # Menghitung seluruh jarak sekaligus dalam matriks
        distances = face_recognition.face_distance(known_encs, current_enc)
        best_idx = int(np.argmin(distances))
        best_distance = float(distances[best_idx])
        best_matched = bool(best_distance <= threshold)

        return valid_techs[best_idx], best_distance, best_matched
    except Exception as exc:
        print("[VECTORIZED MATCH ERROR]", exc)
        return None, None, False


# =========================================================
# HIGH-EFFICIENCY SINGLE-PASS FACE & LIVENESS PROCESSING
# =========================================================

def extract_face_encoding_and_liveness(base64_image, check_liveness=True):
    """
    Ekstraksi encoding wajah dan evaluasi keaslian (liveness) dalam SATU PASS.
    Menghindari decoding gambar dan kalkulasi HOG face location dua kali,
    menghemat ~50% beban CPU dan latensi per request.

    Return:
        (face_encoding: list | None, liveness_tuple: tuple, error_message: str | None)
    """
    default_liveness = (True, 1.0, {"mode": "bypass"}, "Liveness tidak aktif.")

    if face_recognition is None or cv2 is None or np is None:
        return None, (True, 1.0, {"mode": "bypass"}, "Face recognition engine tidak aktif."), "Engine belum tersedia di server."

    if not base64_image:
        return None, (False, 0.0, {}, "Gambar kosong."), "Gambar wajah tidak ditemukan."

    try:
        rgb_img = _prepare_rgb_image(base64_image)
    except Exception as exc:
        return None, (False, 0.0, {"error": str(exc)}, "Gagal memproses gambar."), "Gambar tidak valid atau rusak."

    try:
        face_locations = face_recognition.face_locations(rgb_img, model="hog")
    except Exception as exc:
        return None, (False, 0.0, {"error": str(exc)}, "Gagal mendeteksi lokasi wajah."), f"Gagal mendeteksi wajah: {exc}"

    if len(face_locations) == 0:
        return None, (False, 0.0, {"reason": "NO_FACE"}, "Wajah tidak terdeteksi."), "Wajah tidak terdeteksi. Silakan posisikan wajah di tengah kamera."

    if len(face_locations) > 1:
        return None, (False, 0.0, {"reason": "MULTIPLE_FACES"}, "Terdeteksi lebih dari satu wajah."), "Terdeteksi lebih dari satu wajah. Pastikan hanya satu orang berada di depan kamera."

    main_face = face_locations[0]

    # Evaluasi liveness jika diaktifkan
    liveness_tuple = default_liveness
    if check_liveness:
        liveness_tuple = _evaluate_liveness_from_face(rgb_img, main_face)

    # Ekstraksi encoding 128-D
    try:
        encodings = face_recognition.face_encodings(rgb_img, [main_face])
    except Exception as exc:
        return None, liveness_tuple, f"Gagal membuat encoding wajah: {exc}"

    if not encodings:
        return None, liveness_tuple, "Encoding wajah tidak berhasil dibuat."

    encoding_array = np.asarray(encodings[0], dtype=np.float64)
    if encoding_array.size != 128 or not np.all(np.isfinite(encoding_array)):
        return None, liveness_tuple, "Encoding wajah tidak valid."

    return encoding_array.tolist(), liveness_tuple, None


# =========================================================
# STANDARD PUBLIC INTERFACES (BACKWARD COMPATIBLE)
# =========================================================

def extract_face_encoding(base64_image):
    """
    Mengambil encoding wajah dari gambar kamera.
    Return: (encoding, message)
    """
    encoding, _, error_msg = extract_face_encoding_and_liveness(base64_image, check_liveness=False)
    if error_msg:
        return None, error_msg
    return encoding, "Success"


def analyze_liveness(base64_image):
    """
    Menganalisis keaslian wajah (Liveness & Anti-Spoofing).
    Return: (is_live: bool, score: float, details: dict, message: str)
    """
    _, liveness_tuple, _ = extract_face_encoding_and_liveness(base64_image, check_liveness=True)
    return liveness_tuple


def normalize_encoding(encoding):
    """
    Mengubah berbagai format encoding (list, tuple, ndarray, json, bytes)
    menjadi numpy.ndarray 128 dimensi bertipe float64.
    """
    if encoding is None or np is None:
        return None

    try:
        if isinstance(encoding, np.ndarray):
            result = np.asarray(encoding, dtype=np.float64).reshape(-1)
        elif isinstance(encoding, (list, tuple)):
            result = np.asarray(encoding, dtype=np.float64).reshape(-1)
        elif isinstance(encoding, bytes):
            try:
                decoded = encoding.decode("utf-8")
                result = np.asarray(json.loads(decoded), dtype=np.float64).reshape(-1)
            except Exception:
                import pickle
                result = np.asarray(pickle.loads(encoding), dtype=np.float64).reshape(-1)
        elif isinstance(encoding, str):
            val = encoding.strip()
            if not val:
                return None
            result = np.asarray(json.loads(val), dtype=np.float64).reshape(-1)
        else:
            return None

        if result.size != 128 or not np.all(np.isfinite(result)):
            return None

        return result
    except Exception:
        return None


def compare_faces(known_encoding, current_encoding):
    """
    Membandingkan 2 encoding wajah.
    Return: (match: bool, distance: float)
    """
    if face_recognition is None or np is None:
        return False, 1.0

    known_enc = normalize_encoding(known_encoding)
    current_enc = normalize_encoding(current_encoding)

    if known_enc is None or current_enc is None:
        return False, 1.0

    try:
        threshold = float(current_app.config.get("FACE_MATCH_THRESHOLD", 0.52))
    except Exception:
        threshold = 0.52

    try:
        distance = float(face_recognition.face_distance([known_enc], current_enc)[0])
        return (distance <= threshold), distance
    except Exception as exc:
        print("[FACE DISTANCE ERROR]", exc)
        return False, 1.0


# =========================================================
# VECTORIZED BATCH COMPARISON (ULTRA-FAST)
# =========================================================

def match_face_against_technicians(face_encoding, technicians, threshold=None):
    """
    Mencocokkan satu face_encoding terhadap daftar teknisi aktif secara sekaligus
    menggunakan kalkulasi matriks NumPy/BLAS.
    Return:
        (best_technician, best_distance, best_matched)
    """
    if face_recognition is None or np is None or not technicians or face_encoding is None:
        return None, None, False

    current_enc = normalize_encoding(face_encoding)
    if current_enc is None:
        return None, None, False

    if threshold is None:
        try:
            threshold = float(current_app.config.get("FACE_MATCH_THRESHOLD", 0.52))
        except Exception:
            threshold = 0.52

    known_encs = []
    valid_techs = []

    for tech in technicians:
        if not getattr(tech, "face_encoding", None):
            continue
        norm = normalize_encoding(tech.face_encoding)
        if norm is not None:
            known_encs.append(norm)
            valid_techs.append(tech)

    if not known_encs:
        return None, None, False

    try:
        # Menghitung seluruh jarak sekaligus dalam matriks
        distances = face_recognition.face_distance(known_encs, current_enc)
        best_idx = int(np.argmin(distances))
        best_distance = float(distances[best_idx])
        best_matched = bool(best_distance <= threshold)

        return valid_techs[best_idx], best_distance, best_matched
    except Exception as exc:
        print("[VECTORIZED MATCH ERROR]", exc)
        return None, None, False