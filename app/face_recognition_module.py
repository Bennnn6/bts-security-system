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