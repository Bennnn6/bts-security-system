import pyotp
import qrcode
import base64
from io import BytesIO


# =========================================================
# GENERATE SECRET TOTP
# =========================================================

def generate_secret():
    return pyotp.random_base32()


# =========================================================
# GENERATE QR CODE (SVG format - no image/png)
# =========================================================

def get_qr_code_base64(name_or_secret, secret=None):
    if secret is None:
        secret = name_or_secret
        name = "BTS Technician"
    else:
        name = name_or_secret

    if not secret:
        return ""

    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(
        name=name or "BTS Technician",
        issuer_name="BTS Security System"
    )
    svg_bytes = _qr_to_svg(uri)
    if not svg_bytes:
        return ""
    return base64.b64encode(svg_bytes).decode("utf-8")


def _qr_to_svg(uri):
    try:
        from qrcode.image.svg import SvgImage as _SvgImage
        qr = qrcode.QRCode(image_factory=_SvgImage)
        qr.add_data(uri)
        img = qr.make_image()
        data = img.to_string()
        if isinstance(data, str):
            return data.encode("utf-8")
        return data
    except Exception:
        pass

    try:
        from qrcode.image.svg import SvgPathImage as _SvgPathImage
        qr = qrcode.QRCode(image_factory=_SvgPathImage)
        qr.add_data(uri)
        img = qr.make_image()
        data = img.to_string()
        if isinstance(data, str):
            return data.encode("utf-8")
        return data
    except Exception:
        pass

    try:
        buffered = BytesIO()
        qr = qrcode.QRCode()
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image()
        img.save(buffered, format="PNG")
        return buffered.getvalue()
    except Exception:
        return b""


# =========================================================
# VERIFY TOTP
# =========================================================

def verify_totp(secret, user_code):
    if not secret or not user_code:
        return False
    totp = pyotp.TOTP(secret)
    user_code = str(user_code).strip()
    if not user_code.isdigit() or len(user_code) != 6:
        return False
    return totp.verify(
        user_code,
        valid_window=1
    )
