"""
Authentification à deux facteurs — implémentation TOTP (RFC 6238) en pur
stdlib (hmac, hashlib, base64, struct, time). Aucune dépendance externe :
compatible avec Google Authenticator, Authy, 1Password, etc.
"""
import base64
import hashlib
import hmac
import os
import struct
import time
import urllib.parse


def generate_secret() -> str:
    """Secret aléatoire encodé en base32 (format standard TOTP)."""
    raw = os.urandom(20)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int, digits: int = 6) -> str:
    # Le padding base32 doit être un multiple de 8 caractères.
    padded = secret_b32 + "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(padded.upper())
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code_int = (struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF)
    code = str(code_int % (10 ** digits)).zfill(digits)
    return code


def totp_now(secret_b32: str, step: int = 30, digits: int = 6, at_time: float | None = None) -> str:
    t = at_time if at_time is not None else time.time()
    counter = int(t // step)
    return _hotp(secret_b32, counter, digits)


def verify_totp(secret_b32: str, code: str, step: int = 30, digits: int = 6, window: int = 1) -> bool:
    """Vérifie un code, en tolérant +/- `window` pas de 30s pour la dérive d'horloge."""
    if not code or not code.isdigit():
        return False
    code = code.zfill(digits)
    now = time.time()
    for offset in range(-window, window + 1):
        candidate = totp_now(secret_b32, step, digits, at_time=now + offset * step)
        if hmac.compare_digest(candidate, code):
            return True
    return False


def provisioning_uri(secret_b32: str, account_email: str, issuer: str = "Massey Contracts & Tax") -> str:
    label = urllib.parse.quote(f"{issuer}:{account_email}")
    params = urllib.parse.urlencode({"secret": secret_b32, "issuer": issuer, "algorithm": "SHA1", "digits": 6, "period": 30})
    return f"otpauth://totp/{label}?{params}"
