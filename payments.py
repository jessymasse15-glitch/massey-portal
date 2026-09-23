"""
Intégration Stripe Checkout — appelée directement via l'API REST (requests),
sans le SDK officiel `stripe` (non installable dans l'environnement où ce
code a été écrit, faute d'accès réseau à PyPI). L'API REST de Stripe est
stable et documentée publiquement ; ce module peut être remplacé par le SDK
officiel sans changer les routes de app.py si tu préfères.

Variables d'environnement attendues en production :
  STRIPE_SECRET_KEY       clé secrète (sk_live_... ou sk_test_...)
  STRIPE_WEBHOOK_SECRET   secret de signature du endpoint webhook (whsec_...)
  STRIPE_API_BASE         optionnel, pour pointer vers un serveur de test
"""
import hashlib
import hmac
import os
import time

import requests


class PaymentNotConfigured(Exception):
    pass


class PaymentProviderError(Exception):
    pass


def _api_base():
    return os.environ.get("STRIPE_API_BASE", "https://api.stripe.com")


def _secret_key():
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        raise PaymentNotConfigured(
            "Le paiement en ligne n'est pas configuré : la variable d'environnement "
            "STRIPE_SECRET_KEY est absente. Ajoute tes clés Stripe pour activer cette fonctionnalité."
        )
    return key


def create_checkout_session(amount_cents: int, currency: str, description: str,
                             success_url: str, cancel_url: str, client_email: str = None,
                             metadata: dict = None) -> dict:
    """Crée une session Stripe Checkout et renvoie {"id":..., "url":...}."""
    if amount_cents <= 0:
        raise ValueError("Le montant doit être positif.")
    secret_key = _secret_key()

    # Stripe attend un corps form-urlencoded avec des clés à crochets pour les
    # objets imbriqués (line_items[0][price_data][...]).
    data = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": currency,
        "line_items[0][price_data][unit_amount]": str(amount_cents),
        "line_items[0][price_data][product_data][name]": description,
    }
    if client_email:
        data["customer_email"] = client_email
    for key, value in (metadata or {}).items():
        data[f"metadata[{key}]"] = str(value)

    try:
        resp = requests.post(
            f"{_api_base()}/v1/checkout/sessions",
            data=data,
            auth=(secret_key, ""),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise PaymentProviderError(f"Impossible de joindre le fournisseur de paiement : {exc}") from exc

    if resp.status_code >= 400:
        try:
            err = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            err = resp.text
        raise PaymentProviderError(f"Erreur du fournisseur de paiement ({resp.status_code}) : {err}")

    payload = resp.json()
    return {"id": payload["id"], "url": payload["url"]}


def verify_webhook_signature(payload_bytes: bytes, sig_header: str, webhook_secret: str,
                              tolerance_seconds: int = 300) -> bool:
    """Vérifie une signature de webhook Stripe (schéma documenté par Stripe).

    En-tête attendu : "t=<timestamp>,v1=<signature_hex>[,v1=<autre>...]"
    signature = HMAC-SHA256(webhook_secret, f"{timestamp}.{payload}")
    """
    if not sig_header or not webhook_secret:
        return False
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    timestamp = parts.get("t")
    signatures = [v for k, v in [p.split("=", 1) for p in sig_header.split(",") if "=" in p] if k == "v1"]
    if not timestamp or not signatures:
        return False
    try:
        if abs(time.time() - int(timestamp)) > tolerance_seconds:
            return False
    except ValueError:
        return False

    signed_payload = f"{timestamp}.".encode() + payload_bytes
    expected = hmac.new(webhook_secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


def retrieve_checkout_session(session_id: str) -> dict:
    secret_key = _secret_key()
    resp = requests.get(
        f"{_api_base()}/v1/checkout/sessions/{session_id}",
        auth=(secret_key, ""),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def is_configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))
