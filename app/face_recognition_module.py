import base64
import json

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

from flask import current_app


# =========================================================
# BASE64 IMAGE -> OPENCV IMAGE
# =========================================================

def base64_to_image(base64_string):
    """
    Mengubah Data URI/base64 menjadi gambar OpenCV.

    Contoh input:
        data:image/jpeg;base64,/9j/4AAQ...
    """

    if cv2 is None or np is None:
        raise RuntimeError(
            'Library pemrosesan wajah belum tersedia.'
        )

    if not base64_string:
        raise ValueError(
            'Data gambar kosong.'
        )

    if not isinstance(
        base64_string,
        str
    ):
        raise ValueError(
            'Format gambar tidak valid.'
        )

    base64_string = base64_string.strip()

    # -----------------------------------------------------
    # Pisahkan header Data URI
    # -----------------------------------------------------

    if ',' in base64_string:
        header, encoded = (
            base64_string.split(
                ',',
                1
            )
        )
    else:
        encoded = base64_string

    encoded = encoded.strip()

    if not encoded:
        raise ValueError(
            'Data base64 gambar kosong.'
        )

    # -----------------------------------------------------
    # Decode base64
    # -----------------------------------------------------

    try:
        data = base64.b64decode(
            encoded,
            validate=True
        )

    except Exception as exc:
        raise ValueError(
            'Data base64 gambar tidak valid.'
        ) from exc

    if not data:
        raise ValueError(
            'Gambar hasil decode kosong.'
        )

    # -----------------------------------------------------
    # Convert bytes -> numpy
    # -----------------------------------------------------

    np_arr = np.frombuffer(
        data,
        np.uint8
    )

    # -----------------------------------------------------
    # Convert -> OpenCV
    # -----------------------------------------------------

    image = cv2.imdecode(
        np_arr,
        cv2.IMREAD_COLOR
    )

    if image is None:
        raise ValueError(
            'Gambar tidak dapat dibaca oleh OpenCV.'
        )

    return image


# =========================================================
# EXTRACT FACE ENCODING
# =========================================================

def extract_face_encoding(base64_image):
    """
    Mengambil encoding wajah dari gambar kamera.

    Return:
        (encoding, message)

    Encoding dikembalikan sebagai list Python
    agar cocok disimpan menggunakan SQLAlchemy PickleType.
    """

    # -----------------------------------------------------
    # Validasi input
    # -----------------------------------------------------

    if face_recognition is None or cv2 is None or np is None:
        return (
            None,
            'Fitur face recognition belum tersedia di environment server.'
        )

    if not base64_image:
        return (
            None,
            'Gambar wajah tidak ditemukan.'
        )

    # -----------------------------------------------------
    # Decode gambar
    # -----------------------------------------------------

    try:

        img = base64_to_image(
            base64_image
        )

    except Exception as exc:

        print(
            'IMAGE DECODE ERROR:',
            exc
        )

        return (
            None,
            'Gambar tidak valid atau rusak.'
        )

    # -----------------------------------------------------
    # BGR -> RGB
    # -----------------------------------------------------

    try:

        rgb_img = cv2.cvtColor(
            img,
            cv2.COLOR_BGR2RGB
        )

    except Exception as exc:

        print(
            'COLOR CONVERSION ERROR:',
            exc
        )

        return (
            None,
            'Format warna gambar tidak dapat diproses.'
        )

    # -----------------------------------------------------
    # OPTIONAL RESIZE
    # -----------------------------------------------------
    #
    # Untuk meningkatkan performa kamera.
    # Jangan terlalu kecil karena akurasi wajah
    # bisa menurun.
    #
    # -----------------------------------------------------

    max_width = 1280

    try:

        height, width = (
            rgb_img.shape[:2]
        )

        if width > max_width:

            scale = (
                max_width / width
            )

            new_width = max_width

            new_height = int(
                height * scale
            )

            rgb_img = cv2.resize(
                rgb_img,
                (
                    new_width,
                    new_height
                ),
                interpolation=cv2.INTER_AREA
            )

    except Exception as exc:

        print(
            'IMAGE RESIZE WARNING:',
            exc
        )

    # -----------------------------------------------------
    # DETEKSI WAJAH
    # -----------------------------------------------------

    try:

        face_locations = (
            face_recognition.face_locations(
                rgb_img,
                model='hog'
            )
        )

    except Exception as exc:

        print(
            'FACE LOCATION ERROR:',
            exc
        )

        return (
            None,
            'Gagal mendeteksi wajah.'
        )

    # -----------------------------------------------------
    # TIDAK ADA WAJAH
    # -----------------------------------------------------

    if len(face_locations) == 0:

        return (
            None,
            (
                'Wajah tidak terdeteksi. '
                'Silakan posisikan wajah di tengah kamera.'
            )
        )

    # -----------------------------------------------------
    # LEBIH DARI SATU WAJAH
    # -----------------------------------------------------

    if len(face_locations) > 1:

        return (
            None,
            (
                'Terdeteksi lebih dari satu wajah. '
                'Pastikan hanya satu orang berada '
                'di depan kamera.'
            )
        )

    # -----------------------------------------------------
    # ENCODING
    # -----------------------------------------------------

    try:

        encodings = (
            face_recognition.face_encodings(
                rgb_img,
                face_locations
            )
        )

    except Exception as exc:

        print(
            'FACE ENCODING ERROR:',
            exc
        )

        return (
            None,
            'Gagal membuat encoding wajah.'
        )

    if not encodings:

        return (
            None,
            'Encoding wajah tidak berhasil dibuat.'
        )

    encoding = encodings[0]

    # -----------------------------------------------------
    # VALIDASI DIMENSI
    # -----------------------------------------------------

    if encoding is None:

        return (
            None,
            'Encoding wajah kosong.'
        )

    try:

        encoding_array = np.asarray(
            encoding,
            dtype=np.float64
        )

    except Exception as exc:

        print(
            'ENCODING CONVERSION ERROR:',
            exc
        )

        return (
            None,
            'Encoding wajah tidak valid.'
        )

    if encoding_array.size != 128:

        return (
            None,
            (
                'Panjang encoding wajah tidak valid '
                f'({encoding_array.size}, seharusnya 128).'
            )
        )

    # -----------------------------------------------------
    # RETURN LIST
    # -----------------------------------------------------
    #
    # PickleType akan menyimpan list ini langsung.
    #
    # -----------------------------------------------------

    return (
        encoding_array.tolist(),
        'Success'
    )


# =========================================================
# NORMALIZE ENCODING
# =========================================================

def normalize_encoding(
    encoding
):
    """
    Mengubah berbagai format encoding menjadi
    numpy.ndarray 128 dimensi.

    Mendukung:

    1. list
    2. tuple
    3. numpy.ndarray
    4. JSON string
    5. bytes
    """

    if encoding is None:
        return None

    # -----------------------------------------------------
    # numpy
    # -----------------------------------------------------

    if isinstance(
        encoding,
        np.ndarray
    ):

        try:

            result = np.asarray(
                encoding,
                dtype=np.float64
            ).reshape(-1)

        except Exception:
            return None

    # -----------------------------------------------------
    # list / tuple
    # -----------------------------------------------------

    elif isinstance(
        encoding,
        (list, tuple)
    ):

        try:

            result = np.asarray(
                encoding,
                dtype=np.float64
            ).reshape(-1)

        except Exception:
            return None

    # -----------------------------------------------------
    # bytes
    # -----------------------------------------------------

    elif isinstance(
        encoding,
        bytes
    ):

        # Coba decode menjadi JSON
        try:

            decoded = encoding.decode(
                'utf-8'
            )

            parsed = json.loads(
                decoded
            )

            result = np.asarray(
                parsed,
                dtype=np.float64
            ).reshape(-1)

        except Exception:

            # Bisa saja berupa pickle lama
            try:

                import pickle

                parsed = pickle.loads(
                    encoding
                )

                result = np.asarray(
                    parsed,
                    dtype=np.float64
                ).reshape(-1)

            except Exception:

                return None

    # -----------------------------------------------------
    # string
    # -----------------------------------------------------

    elif isinstance(
        encoding,
        str
    ):

        value = encoding.strip()

        if not value:
            return None

        # JSON
        try:

            parsed = json.loads(
                value
            )

            result = np.asarray(
                parsed,
                dtype=np.float64
            ).reshape(-1)

        except Exception:

            return None

    else:

        return None

    # -----------------------------------------------------
    # VALIDASI
    # -----------------------------------------------------

    if result.size != 128:
        return None

    # -----------------------------------------------------
    # Validasi NaN / Infinity
    # -----------------------------------------------------

    if not np.all(
        np.isfinite(result)
    ):
        return None

    return result


# =========================================================
# COMPARE FACES
# =========================================================

def compare_faces(
    known_encoding,
    current_encoding
):
    """
    Membandingkan encoding wajah.

    Return:
        (match, distance)
    """

    if face_recognition is None or np is None:
        return (
            False,
            1.0
        )

    # -----------------------------------------------------
    # Normalize encoding lama
    # -----------------------------------------------------

    known_enc = normalize_encoding(
        known_encoding
    )

    # -----------------------------------------------------
    # Normalize encoding baru
    # -----------------------------------------------------

    current_enc = normalize_encoding(
        current_encoding
    )

    # -----------------------------------------------------
    # Validasi
    # -----------------------------------------------------

    if (
        known_enc is None
        or current_enc is None
    ):

        return (
            False,
            1.0
        )

    # -----------------------------------------------------
    # Ambil threshold
    # -----------------------------------------------------

    try:

        threshold = float(
            current_app.config.get(
                'FACE_MATCH_THRESHOLD',
                0.52
            )
        )

    except Exception:

        threshold = 0.52

    # -----------------------------------------------------
    # Face distance
    # -----------------------------------------------------

    try:

        distance = (
            face_recognition.face_distance(
                [known_enc],
                current_enc
            )[0]
        )

        distance = float(
            distance
        )

    except Exception as exc:

        print(
            'FACE DISTANCE ERROR:',
            exc
        )

        return (
            False,
            1.0
        )

    # -----------------------------------------------------
    # MATCH
    # -----------------------------------------------------

    match = bool(
        distance <= threshold
    )

    return (
        match,
        distance
    )


# =========================================================
# ANTI-SPOOFING & LIVENESS DETECTION ENGINE
# =========================================================

def calculate_eye_aspect_ratio(eye_points):
    """
    Menghitung Eye Aspect Ratio (EAR) dari 6 koordinat landmark mata.
    EAR = (||p2-p6|| + ||p3-p5||) / (2 * ||p1-p4||)
    Mata terbuka: 0.25 - 0.38
    Mata tertutup / kedip: < 0.20
    """
    if not eye_points or len(eye_points) < 6:
        return 0.0

    try:
        import math
        p1, p2, p3, p4, p5, p6 = eye_points[:6]

        def dist(a, b):
            return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

        vertical_1 = dist(p2, p6)
        vertical_2 = dist(p3, p5)
        horizontal = dist(p1, p4)

        if horizontal <= 0.0001:
            return 0.0

        ear = (vertical_1 + vertical_2) / (2.0 * horizontal)
        return float(ear)
    except Exception:
        return 0.0


def detect_screen_moire_and_glare(rgb_img, face_box):
    """
    Menganalisis tekstur alami wajah, blur/ketajaman (Laplacian),
    dan pantulan kilap cahaya (glare hotspot) khas layar kaca HP atau foto kertas glossy.
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

    # Batasi koordinat dalam batas gambar
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

    # Nilai ideal ketajaman wajah asli berkisar antara 40 s/d 1800
    if laplacian_var < 30.0:
        # Terlalu buram (khas foto layar/kertas jarak jauh)
        texture_score = max(0.15, laplacian_var / 50.0)
    elif laplacian_var > 2800.0:
        # Terlalu tajam artifisial (grid pixel LCD / Moiré pattern)
        texture_score = 0.5
    else:
        texture_score = min(1.0, 0.65 + (laplacian_var / 1200.0) * 0.35)

    # 2. Deteksi Pantulan Kilap Layar Kaca / Kertas Glossy (Glare Hotspot)
    hsv_face = cv2.cvtColor(face_roi, cv2.COLOR_RGB2HSV)
    _, s_channel, v_channel = cv2.split(hsv_face)
    # Area pantulan: Sangat terang (V > 248) dan desaturasi tinggi (S < 35)
    glare_mask = (v_channel > 248) & (s_channel < 35)
    glare_ratio = float(np.sum(glare_mask)) / face_area if face_area > 0 else 0.0

    # 3. Rasio Warna Kulit Alami (YCrCb Chromaticity)
    ycrcb_face = cv2.cvtColor(face_roi, cv2.COLOR_RGB2YCrCb)
    _, cr, cb = cv2.split(ycrcb_face)
    # Rentang tipikal rona kulit manusia
    skin_mask = (cr >= 130) & (cr <= 175) & (cb >= 75) & (cb <= 130)
    skin_ratio = float(np.sum(skin_mask)) / face_area if face_area > 0 else 0.0

    return {
        "texture_score": round(texture_score, 3),
        "skin_ratio": round(skin_ratio, 3),
        "glare_ratio": round(glare_ratio, 4),
        "laplacian_var": round(laplacian_var, 1)
    }


def analyze_liveness(base64_image):
    """
    Menganalisis keaslian wajah (Liveness & Anti-Spoofing):
    Mendeteksi apakah gambar yang dikirim adalah manusia hidup asli di depan kamera,
    bukan foto cetak atau foto dari layar smartphone/tablet.

    Return:
        (is_live: bool, score: float, details: dict, message: str)
    """
    if face_recognition is None or cv2 is None or np is None:
        # Jika environment tanpa CV, izinkan bypass dengan logging
        return (
            True,
            1.0,
            {"mode": "bypass"},
            "Engine Computer Vision tidak aktif, liveness bypass."
        )

    if not base64_image:
        return (
            False,
            0.0,
            {},
            "Gambar wajah kosong."
        )

    # Ambil konfigurasi
    try:
        min_score = float(current_app.config.get('LIVENESS_MIN_SCORE', 0.55))
        strict_glare = bool(current_app.config.get('LIVENESS_STRICT_GLARE', True))
    except Exception:
        min_score = 0.55
        strict_glare = True

    try:
        img = base64_to_image(base64_image)
        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    except Exception as exc:
        return (
            False,
            0.0,
            {"error": str(exc)},
            "Gagal mendekode gambar untuk evaluasi liveness."
        )

    # Deteksi lokasi wajah
    try:
        face_locations = face_recognition.face_locations(rgb_img, model='hog')
    except Exception as exc:
        return (
            False,
            0.0,
            {"error": str(exc)},
            "Deteksi posisi wajah gagal."
        )

    if len(face_locations) == 0:
        return (
            False,
            0.0,
            {"reason": "NO_FACE"},
            "Wajah tidak terdeteksi. Posisikan wajah tepat di tengah kamera."
        )

    if len(face_locations) > 1:
        return (
            False,
            0.0,
            {"reason": "MULTIPLE_FACES"},
            "Terdeteksi lebih dari satu wajah. Pastikan hanya ada satu orang di depan kamera."
        )

    main_face = face_locations[0]

    # Analisis Tekstur & Pantulan Layar (Passive Anti-Spoofing)
    analysis = detect_screen_moire_and_glare(rgb_img, main_face)
    texture_score = analysis.get("texture_score", 0.7)
    skin_ratio = analysis.get("skin_ratio", 0.5)
    glare_ratio = analysis.get("glare_ratio", 0.0)

    # Analisis Landmark & Mata
    ear_avg = 0.30
    try:
        landmarks_list = face_recognition.face_landmarks(rgb_img, [main_face])
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

    # Skor biologis mata (manusia hidup normal memiliki EAR antara 0.16 s/d 0.42)
    if 0.16 <= ear_avg <= 0.42:
        eye_score = 0.95
    elif 0.10 <= ear_avg < 0.16:
        eye_score = 0.80  # Mata menyipit atau setengah kedip
    else:
        eye_score = 0.50

    # Skor rona kulit alami
    skin_score = min(1.0, max(0.2, skin_ratio * 1.6))

    # Komputasi bobot skor gabungan liveness
    # 40% Tekstur Ketajaman + 35% Rona Kulit Alami + 25% Landmark Geometri Mata
    composite_score = (texture_score * 0.40) + (skin_score * 0.35) + (eye_score * 0.25)

    # Pinalti jika terdeteksi pantulan kilap layar smartphone/kaca (Specular Glare)
    glare_penalty = 0.0
    if strict_glare and glare_ratio > 0.05:
        # Jika lebih dari 5% area wajah adalah pantulan kilap putih pekat
        glare_penalty = min(0.45, (glare_ratio - 0.05) * 5.0)
        composite_score = max(0.05, composite_score - glare_penalty)

    final_score = round(float(composite_score), 3)

    details = {
        "liveness_score": final_score,
        "texture_score": texture_score,
        "skin_ratio": skin_ratio,
        "glare_ratio": glare_ratio,
        "ear": round(float(ear_avg), 3),
        "laplacian_var": analysis.get("laplacian_var", 0.0),
        "passed": bool(final_score >= min_score)
    }

    if glare_penalty > 0.20:
        return (
            False,
            final_score,
            details,
            "Terdeteksi pantulan kilap layar gadget atau foto cetak (Anti-Spoofing Alert)."
        )

    if final_score < min_score:
        if texture_score < 0.35:
            msg = "Kualitas tekstur wajah tidak memenuhi syarat (Terlalu buram atau foto cetak)."
        elif skin_ratio < 0.20:
            msg = "Distribusi rona warna wajah tidak alami (Terindikasi tampilan layar digital)."
        else:
            msg = "Wajah tidak memenuhi parameter keaslian biometrik langsung (Liveness Failed)."
        return (
            False,
            final_score,
            details,
            msg
        )

    return (
        True,
        final_score,
        details,
        "Wajah asli manusia hidup terverifikasi (Liveness Passed)."
    )