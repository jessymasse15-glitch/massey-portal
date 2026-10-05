"""Rappels d'échéance par SMS / WhatsApp (Twilio) — Contract Intelligence.

- Fournisseur : Twilio (API REST, sans SDK). Variables d'environnement :
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, et au moins un expéditeur :
  TWILIO_SMS_FROM (numéro SMS, ex. +15551234567) et/ou TWILIO_WHATSAPP_FROM
  (ex. whatsapp:+14155238886 pour le bac à sable, ou votre expéditeur WhatsApp approuvé).
- Le numéro de l'utilisateur est vérifié par un code à 6 chiffres (valable 10 min,
  5 essais) avant tout rappel : personne ne peut faire écrire à un numéro tiers.
- Un seul message récapitulatif par passage ; chaque rappel n'est envoyé qu'une
  fois par canal (table ci_reminder_log_msg). Réponse STOP chez Twilio = opt-out natif.
- WhatsApp : hors fenêtre de 24 h, Twilio exige un modèle de message approuvé ;
  sans modèle approuvé l'envoi peut échouer (le rappel est alors retenté plus tard).
"""
import base64
import hmac
import json
import os
import re
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from flask import flash, request

import ci_reminders
import db as dbm

m = None
F = None

CODE_TTL_MIN = 10
MAX_TRIES = 5
_PHONE_RX = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone(raw):
    """E.164 strict : '+509 3712 3456' -> '+50937123456'. None si invalide."""
    s = re.sub(r"[\s().\-]", "", (raw or "").strip())
    if s.startswith("00"):
        s = "+" + s[2:]
    return s if _PHONE_RX.match(s) else None


def mask(phone):
    return phone[:4] + "•" * max(0, len(phone) - 7) + phone[-3:] if phone else ""


def channels_available():
    if not (os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN")):
        return []
    out = []
    if os.environ.get("TWILIO_SMS_FROM"):
        out.append("sms")
    if os.environ.get("TWILIO_WHATSAPP_FROM"):
        out.append("whatsapp")
    return out


def send_message(channel, to_phone, body):
    """Envoie un message via Twilio. True si accepté. Ne lève jamais."""
    if channel not in channels_available():
        return False
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    if channel == "whatsapp":
        frm = os.environ["TWILIO_WHATSAPP_FROM"]
        frm = frm if frm.startswith("whatsapp:") else "whatsapp:" + frm
        to = "whatsapp:" + to_phone
    else:
        frm, to = os.environ["TWILIO_SMS_FROM"], to_phone
    data = urllib.parse.urlencode({"From": frm, "To": to, "Body": body[:1500]}).encode()
    req = urllib.request.Request("https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % sid, data=data, method="POST")
    req.add_header("Authorization", "Basic " + base64.b64encode(("%s:%s" % (sid, token)).encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Données utilisateur
# ---------------------------------------------------------------------------

def get_mobile(conn, user_id):
    r = conn.execute("SELECT * FROM ci_mobile_prefs WHERE user_id=?", (user_id,)).fetchone()
    if r is None:
        return {"phone": "", "channel": "sms", "verified": False, "enabled": False, "pending": False}
    return {"phone": r["phone"] or "", "channel": r["channel"] or "sms", "verified": bool(r["verified"]),
            "enabled": bool(r["enabled"]) and bool(r["verified"]), "pending": bool(r["code_hash"])}


def _code_hash(secret, user_id, phone, code):
    return hmac.new(secret.encode(), ("%s|%s|%s" % (user_id, phone, code)).encode(), "sha256").hexdigest()


def build_text(language, items):
    en = language == "en"
    items = sorted(items, key=lambda i: i["due"])
    lines = []
    for i in items[:6]:
        d = i["days_left"]
        if d < 0:
            when = ("overdue %dd" if en else "en retard %dj") % (-d)
        elif d == 0:
            when = "today" if en else "aujourd'hui"
        else:
            when = ("in %dd" if en else "dans %dj") % d
        lines.append("- %s (%s)" % (i["label"][:60], when))
    more = len(items) - 6
    if more > 0:
        lines.append("+ %d %s" % (more, "more" if en else "autres"))
    head = "Massey Contracts: deadlines" if en else "Massey Contracts : échéances"
    link = ci_reminders.site_url() + ("/en/contract-intelligence/deadlines" if en else "/contract-intelligence/echeances")
    return "%s\n%s\n%s" % (head, "\n".join(lines), link)


def run_messages(send=None, today=None):
    """Envoie les rappels SMS/WhatsApp dus. Réservation préalable dans le journal."""
    send = send or send_message
    today = today or ci_reminders.local_today()
    summary = {"users": 0, "items": 0, "sent": 0, "failed": 0, "skipped_no_provider": False}
    if send is send_message and not channels_available():
        summary["skipped_no_provider"] = True
        return summary
    conn = dbm.get_db()
    try:
        prefs = {r["user_id"]: r for r in conn.execute("SELECT * FROM ci_mobile_prefs WHERE enabled=1 AND verified=1").fetchall()}
        if not prefs:
            return summary
        for uid, u in ci_reminders.collect(conn, today).items():
            p = prefs.get(uid)
            if not p or (send is send_message and p["channel"] not in channels_available()):
                continue
            claimed = []
            for it in u["items"]:
                cur = conn.execute("INSERT OR IGNORE INTO ci_reminder_log_msg (obligation_id, kind, channel, sent_at) VALUES (?,?,?,?)",
                                   (it["id"], it["kind"], p["channel"], dbm.now()))
                if cur.rowcount:
                    claimed.append(it)
            conn.commit()
            if not claimed:
                continue
            summary["users"] += 1
            if send(p["channel"], p["phone"], build_text(u["language"], claimed)):
                summary["sent"] += 1
                summary["items"] += len(claimed)
            else:
                summary["failed"] += 1
                for it in claimed:
                    conn.execute("DELETE FROM ci_reminder_log_msg WHERE obligation_id=? AND kind=? AND channel=?",
                                 (it["id"], it["kind"], p["channel"]))
                conn.commit()
    finally:
        conn.close()
    return summary


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def init(app_module, features_module):
    global m, F
    m, F = app_module, features_module

    @m.ci_route("mobile_start", "/rappels/mobile", "/reminders/mobile", ("POST",))
    def ci_mobile_start():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        chans = channels_available()
        if not chans:
            flash(m._T("Les rappels SMS / WhatsApp ne sont pas encore activés sur ce site.",
                       "SMS / WhatsApp reminders are not enabled on this site yet."), "error")
            return m._ci_redirect("deadlines")
        phone = normalize_phone(request.form.get("phone"))
        channel = request.form.get("channel") if request.form.get("channel") in chans else chans[0]
        if not phone:
            flash(m._T("Numéro invalide. Format international attendu, par ex. +509 3712 3456.",
                       "Invalid number. International format expected, e.g. +509 3712 3456."), "error")
            return m._ci_redirect("deadlines")
        conn = m.dbm.get_db()
        try:
            cur = conn.execute("SELECT code_sent_at FROM ci_mobile_prefs WHERE user_id=?", (u["id"],)).fetchone()
            if cur and cur["code_sent_at"]:
                try:
                    if datetime.utcnow() - datetime.fromisoformat(cur["code_sent_at"]) < timedelta(seconds=60):
                        flash(m._T("Patientez une minute avant de redemander un code.", "Wait a minute before asking for another code."), "error")
                        return m._ci_redirect("deadlines")
                except ValueError:
                    pass
            n = conn.execute("SELECT COUNT(*) AS c FROM ci_mobile_prefs WHERE phone=? AND verified=1 AND user_id<>?", (phone, u["id"])).fetchone()["c"]
            if n:
                flash(m._T("Ce numéro est déjà utilisé par un autre compte.", "This number is already used by another account."), "error")
                return m._ci_redirect("deadlines")
            code = "%06d" % secrets.randbelow(1000000)
            h = _code_hash(m.app.config["SECRET_KEY"], u["id"], phone, code)
            now_iso = datetime.utcnow().isoformat()
            conn.execute(
                "INSERT INTO ci_mobile_prefs (user_id, phone, channel, verified, enabled, code_hash, code_exp, code_tries, code_sent_at, updated_at) "
                "VALUES (?,?,?,0,0,?,?,0,?,?) ON CONFLICT(user_id) DO UPDATE SET phone=excluded.phone, channel=excluded.channel, verified=0, enabled=0, "
                "code_hash=excluded.code_hash, code_exp=excluded.code_exp, code_tries=0, code_sent_at=excluded.code_sent_at, updated_at=excluded.updated_at",
                (u["id"], phone, channel, h, (datetime.utcnow() + timedelta(minutes=CODE_TTL_MIN)).isoformat(), now_iso, m.dbm.now()))
            conn.commit()
        finally:
            conn.close()
        txt = m._T("Massey Contracts : votre code de vérification est %s (valable %d min)." % (code, CODE_TTL_MIN),
                   "Massey Contracts: your verification code is %s (valid %d min)." % (code, CODE_TTL_MIN))
        if send_message(channel, phone, txt):
            flash(m._T("Code envoyé au %s. Saisissez-le ci-dessous." % mask(phone), "Code sent to %s. Enter it below." % mask(phone)), "success")
        else:
            flash(m._T("L'envoi du code a échoué. Vérifiez le numéro (et pour WhatsApp, que vous avez rejoint le bac à sable / l'expéditeur).",
                       "Could not send the code. Check the number (for WhatsApp, that you joined the sandbox / sender)."), "error")
        return m._ci_redirect("deadlines")

    @m.ci_route("mobile_verify", "/rappels/mobile/verifier", "/reminders/mobile/verify", ("POST",))
    def ci_mobile_verify():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        code = re.sub(r"\D", "", request.form.get("code") or "")
        conn = m.dbm.get_db()
        try:
            r = conn.execute("SELECT * FROM ci_mobile_prefs WHERE user_id=?", (u["id"],)).fetchone()
            ok = False
            msg = m._T("Aucun code en attente : demandez-en un nouveau.", "No pending code: request a new one.")
            if r and r["code_hash"]:
                expired = datetime.utcnow().isoformat() > (r["code_exp"] or "")
                if expired or (r["code_tries"] or 0) >= MAX_TRIES:
                    msg = m._T("Code expiré ou trop d'essais : demandez-en un nouveau.", "Code expired or too many attempts: request a new one.")
                    conn.execute("UPDATE ci_mobile_prefs SET code_hash=NULL WHERE user_id=?", (u["id"],))
                elif hmac.compare_digest(r["code_hash"], _code_hash(m.app.config["SECRET_KEY"], u["id"], r["phone"], code)):
                    ok = True
                    conn.execute("UPDATE ci_mobile_prefs SET verified=1, enabled=1, code_hash=NULL, code_tries=0, updated_at=? WHERE user_id=?", (m.dbm.now(), u["id"]))
                    msg = m._T("Numéro vérifié : les rappels mobiles sont activés.", "Number verified: mobile reminders are on.")
                else:
                    conn.execute("UPDATE ci_mobile_prefs SET code_tries=code_tries+1 WHERE user_id=?", (u["id"],))
                    msg = m._T("Code incorrect.", "Incorrect code.")
            conn.commit()
        finally:
            conn.close()
        flash(msg, "success" if ok else "error")
        return m._ci_redirect("deadlines")

    @m.ci_route("mobile_toggle", "/rappels/mobile/statut", "/reminders/mobile/toggle", ("POST",))
    def ci_mobile_toggle():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        action = request.form.get("action")
        conn = m.dbm.get_db()
        try:
            if action == "remove":
                conn.execute("DELETE FROM ci_mobile_prefs WHERE user_id=?", (u["id"],))
                flash(m._T("Numéro supprimé.", "Number removed."), "success")
            else:
                en = 1 if action == "on" else 0
                conn.execute("UPDATE ci_mobile_prefs SET enabled=? WHERE user_id=? AND verified=1", (en, u["id"]))
                flash(m._T("Préférence enregistrée.", "Preference saved."), "success")
            conn.commit()
        finally:
            conn.close()
        return m._ci_redirect("deadlines")
