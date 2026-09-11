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

def get_qr_code_base64(name, secret):
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(
        name=name,
        issuer_name="BTS Security System"
    )
    svg_bytes = _qr_to_svg(uri)
    return base64.b64encode(svg_bytes).decode("utf-8")


def _qr_to_svg(uri):
    try:
        from qrcode.image.svg import SvgImage as _SvgImage
        qr = qrcode.QRCode(image_factory=_SvgImage)
        qr.add_data(uri)
        img = qr.make_image()
        return str(img.to_string()).encode("utf-8")
    except Exception:
        pass
    buffered = BytesIO()
    qr = qrcode.QRCode()
    qr.add_data(uri)
    qr.make()
    img = qr.make_image()
    img.save(buffered, format="PNG")
    return buffered.getvalue()


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
