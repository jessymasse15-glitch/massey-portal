"""
Abstraction d'envoi de courriels transactionnels.

Aucune dépendance externe : utilise smtplib (bibliothèque standard). Le service
est "configuré" seulement si les variables d'environnement SMTP nécessaires
sont présentes (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, FROM_EMAIL) —
exactement le même principe que payments.is_configured() pour Stripe : en
l'absence de configuration, l'application continue de fonctionner normalement,
mais les courriels ne partent pas réellement (l'appelant doit prévoir un
repli, ex. afficher le lien à l'écran plutôt que de prétendre qu'un courriel
a été envoyé).
"""
import os
import smtplib
import ssl
from email.message import EmailMessage

import db as dbm


def is_configured():
    return bool(
        os.environ.get("SMTP_HOST")
        and os.environ.get("SMTP_USER")
        and os.environ.get("SMTP_PASSWORD")
        and os.environ.get("FROM_EMAIL")
    )


def send_email(to_email, subject, body_text):
    """Tente d'envoyer un courriel transactionnel. Retourne True si l'envoi a
    réussi, False sinon (y compris si le SMTP n'est pas configuré). N'importe
    jamais d'exception vers l'appelant — une notification qui échoue ne doit
    jamais casser un flux métier (création de dossier, réinitialisation de
    mot de passe, etc.)."""
    if not is_configured():
        dbm.log_activity(None, "email_not_sent_no_smtp", f"to={to_email} subject={subject}")
        return False
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    from_email = os.environ["FROM_EMAIL"]
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email
        msg.set_content(body_text)
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls(context=context)
            server.login(user, password)
            server.send_message(msg)
        dbm.log_activity(None, "email_sent", f"to={to_email} subject={subject}")
        return True
    except Exception as exc:  # noqa: BLE001 — on ne veut jamais planter l'appelant
        dbm.log_activity(None, "email_send_failed", f"to={to_email} subject={subject} error={exc}")
        return False
