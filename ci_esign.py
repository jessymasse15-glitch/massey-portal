"""Signature électronique certifiée via DocuSign (API REST eSignature, authentification JWT).

Variables d'environnement (toutes obligatoires sauf mention) :
  DOCUSIGN_INTEGRATION_KEY   clé d'intégration (client id)
  DOCUSIGN_USER_ID           GUID de l'utilisateur DocuSign qui envoie les enveloppes
  DOCUSIGN_ACCOUNT_ID        identifiant de compte (API Account ID)
  DOCUSIGN_PRIVATE_KEY       clé privée RSA (PEM ; les « \\n » littéraux sont acceptés)
  DOCUSIGN_ENV               « demo » (défaut, bac à sable) ou « production »
  DOCUSIGN_BASE_URI          (optionnel) ex. https://na3.docusign.net ; sinon lu via /oauth/userinfo
  DOCUSIGN_CONNECT_HMAC_KEY  (optionnel) clé HMAC Connect : active le webhook (sans elle, le suivi se fait par interrogation)
"""
import base64
import json
import os
import threading
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class ESignError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail


_lock = threading.Lock()
_cache = {"token": None, "exp": 0, "base": None}


def is_configured():
    return all(os.environ.get(k, "").strip() for k in ("DOCUSIGN_INTEGRATION_KEY", "DOCUSIGN_USER_ID", "DOCUSIGN_ACCOUNT_ID", "DOCUSIGN_PRIVATE_KEY"))


def webhook_enabled():
    return bool(os.environ.get("DOCUSIGN_CONNECT_HMAC_KEY", "").strip())


def is_production():
    return os.environ.get("DOCUSIGN_ENV", "demo").strip().lower() == "production"


def _auth_host():
    override = os.environ.get("DOCUSIGN_AUTH_HOST", "").strip()
    if override:
        return override
    return "account.docusign.com" if is_production() else "account-d.docusign.com"


def _auth_url(path):
    scheme = "http" if _auth_host().startswith("127.0.0.1") else "https"
    return "%s://%s%s" % (scheme, _auth_host(), path)


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _assertion():
    pem = os.environ["DOCUSIGN_PRIVATE_KEY"].replace("\\n", "\n").strip().encode()
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except Exception as exc:  # noqa: BLE001
        raise ESignError("bad_private_key", str(exc)[:120])
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"iss": os.environ["DOCUSIGN_INTEGRATION_KEY"].strip(), "sub": os.environ["DOCUSIGN_USER_ID"].strip(),
              "aud": _auth_host(), "iat": now, "exp": now + 3600, "scope": "signature impersonation"}
    signing_input = _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    sig = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(sig)


def _token():
    with _lock:
        if _cache["token"] and _cache["exp"] - 120 > time.time():
            return _cache["token"]
        if not is_configured():
            raise ESignError("not_configured")
        try:
            resp = requests.post(_auth_url("/oauth/token"),
                                 data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": _assertion()}, timeout=(10, 30))
        except requests.RequestException as exc:
            raise ESignError("unreachable", str(exc)[:160])
        if resp.status_code != 200:
            detail = resp.text[:200]
            raise ESignError("consent_required" if "consent_required" in detail else "auth", detail)
        data = resp.json()
        _cache["token"] = data["access_token"]
        _cache["exp"] = time.time() + int(data.get("expires_in", 3600))
        return _cache["token"]


def consent_url(redirect_uri):
    """Lien à ouvrir une seule fois (connecté à DocuSign) pour autoriser l'intégration."""
    return _auth_url("/oauth/auth") + "?response_type=code&scope=signature%%20impersonation&client_id=%s&redirect_uri=%s" % (
        os.environ.get("DOCUSIGN_INTEGRATION_KEY", ""), redirect_uri)


def _base():
    if _cache["base"]:
        return _cache["base"]
    configured = os.environ.get("DOCUSIGN_BASE_URI", "").strip().rstrip("/")
    if not configured and not is_production():
        configured = "https://demo.docusign.net"
    if not configured:
        try:
            resp = requests.get(_auth_url("/oauth/userinfo"), headers={"Authorization": "Bearer " + _token()}, timeout=(10, 30))
            resp.raise_for_status()
            accounts = resp.json().get("accounts", [])
            wanted = os.environ["DOCUSIGN_ACCOUNT_ID"].strip()
            acc = next((a for a in accounts if a.get("account_id") == wanted), accounts[0] if accounts else None)
            configured = (acc or {}).get("base_uri", "").rstrip("/")
        except (requests.RequestException, ValueError):
            configured = ""
        if not configured:
            raise ESignError("no_base_uri")
    _cache["base"] = configured
    return configured


def _url(path):
    return "%s/restapi/v2.1/accounts/%s%s" % (_base(), os.environ["DOCUSIGN_ACCOUNT_ID"].strip(), path)


def _call(method, path, **kw):
    for attempt in (1, 2):
        try:
            resp = requests.request(method, _url(path), headers={"Authorization": "Bearer " + _token()}, timeout=(10, 60), **kw)
        except requests.RequestException as exc:
            raise ESignError("unreachable", str(exc)[:160])
        if resp.status_code == 401 and attempt == 1:
            with _lock:
                _cache["token"] = None
            continue
        break
    if resp.status_code in (401, 403):
        raise ESignError("auth", resp.text[:200])
    if resp.status_code == 429:
        raise ESignError("rate")
    if resp.status_code >= 500:
        raise ESignError("unavailable")
    if resp.status_code >= 400:
        raise ESignError("http_%d" % resp.status_code, resp.text[:300])
    return resp


def create_envelope(title, pdf_bytes, signers, subject, blurb, webhook_url=None):
    """signers : [{'name','email'}]. Les zones de signature sont posées sur les ancres « /sigN/ » et « /datN/ » du PDF."""
    recipients = []
    for i, s in enumerate(signers, start=1):
        recipients.append({
            "email": s["email"], "name": s.get("name") or s["email"].split("@")[0], "recipientId": str(i), "routingOrder": "1",
            "tabs": {
                "signHereTabs": [{"anchorString": "/sig%d/" % i, "anchorUnits": "pixels", "anchorXOffset": "0", "anchorYOffset": "0", "anchorIgnoreIfNotPresent": "false"}],
                "dateSignedTabs": [{"anchorString": "/dat%d/" % i, "anchorUnits": "pixels", "anchorXOffset": "0", "anchorYOffset": "0", "anchorIgnoreIfNotPresent": "true"}],
            }})
    body = {
        "emailSubject": subject[:100], "emailBlurb": blurb[:1000], "status": "sent",
        "documents": [{"documentBase64": base64.b64encode(pdf_bytes).decode(), "name": (title[:90] or "Contrat") + ".pdf", "fileExtension": "pdf", "documentId": "1"}],
        "recipients": {"signers": recipients},
    }
    if webhook_url and webhook_enabled():
        body["eventNotification"] = {
            "url": webhook_url, "loggingEnabled": "true", "requireAcknowledgment": "true", "includeHMAC": "true",
            "envelopeEvents": [{"envelopeEventStatusCode": c} for c in ("completed", "declined", "voided")],
            "eventData": {"version": "restv2.1"},
        }
    resp = _call("POST", "/envelopes", json=body)
    try:
        return resp.json()["envelopeId"]
    except (ValueError, KeyError):
        raise ESignError("bad_response")


def get_envelope(envelope_id):
    """-> {'status': str, 'recipients': [{'email','status','declined_reason','signed_at'}]}"""
    env = _call("GET", "/envelopes/%s" % envelope_id).json()
    rec = _call("GET", "/envelopes/%s/recipients" % envelope_id).json()
    out = []
    for s in rec.get("signers", []):
        out.append({"email": (s.get("email") or "").lower(), "status": (s.get("status") or "").lower(),
                    "declined_reason": s.get("declinedReason") or "", "signed_at": s.get("signedDateTime") or ""})
    return {"status": (env.get("status") or "").lower(), "recipients": out}


def download(envelope_id, which):
    """which : 'combined' (documents signés) ou 'certificate' (certificat de réalisation). -> bytes PDF"""
    resp = _call("GET", "/envelopes/%s/documents/%s" % (envelope_id, which))
    if not resp.content.startswith(b"%PDF"):
        raise ESignError("bad_document")
    return resp.content


def resend(envelope_id, signers):
    body = {"signers": [{"recipientId": str(i), "name": s.get("name") or s["email"].split("@")[0], "email": s["email"]} for i, s in enumerate(signers, start=1)]}
    _call("PUT", "/envelopes/%s/recipients?resend_envelope=true" % envelope_id, json=body)


def void(envelope_id, reason):
    _call("PUT", "/envelopes/%s" % envelope_id, json={"status": "voided", "voidedReason": reason[:200]})


def verify_hmac(raw_body, header_values):
    """Vérifie X-DocuSign-Signature-1..n : Base64(HMAC-SHA256(corps brut, clé))."""
    import hashlib
    import hmac
    key = os.environ.get("DOCUSIGN_CONNECT_HMAC_KEY", "").strip()
    if not key:
        return False
    expected = base64.b64encode(hmac.new(key.encode(), raw_body, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(expected, (v or "").strip()) for v in header_values)
