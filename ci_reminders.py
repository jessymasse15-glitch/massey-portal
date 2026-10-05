"""Rappels par courriel des échéances Contract Intelligence.

Un seul courriel récapitulatif par utilisateur et par passage. Chaque rappel
n'est envoyé qu'une fois (journal ci_reminder_log) : à J-30, J-7, J-1 et le jour J
par défaut (réglable par l'utilisateur), puis chaque semaine de retard (8 fois au plus).

Déclenchement (voir README du déploiement) :
  - tâche de fond dans l'appli (toutes les heures, de 7 h à 20 h, heure d'Haïti) ;
  - ou appel planifié de /contract-intelligence/rappels/executer?token=CRON_TOKEN.
Nécessite la configuration SMTP (notifications.is_configured()).
"""
import datetime
import os

from itsdangerous import BadSignature, URLSafeSerializer

import db as dbm
import notifications

DEFAULT_OFFSETS = [30, 7, 1, 0]
ALLOWED_OFFSETS = [60, 30, 14, 7, 3, 1, 0]
MAX_LATE_WEEKS = 8


def site_url():
    return os.environ.get("SITE_URL", "https://massey-portal.onrender.com").rstrip("/")


def local_today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/Port-au-Prince")).date()
    except Exception:  # tzdata absent : Haïti = UTC-5 hors heure d'été, approximation acceptable
        return (datetime.datetime.utcnow() - datetime.timedelta(hours=5)).date()


def local_hour():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/Port-au-Prince")).hour
    except Exception:
        return (datetime.datetime.utcnow() - datetime.timedelta(hours=5)).hour


def parse_offsets(value):
    out = set()
    for part in (value or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) in ALLOWED_OFFSETS:
            out.add(int(part))
    return sorted(out, reverse=True)


def unsubscribe_token(secret, user_id):
    return URLSafeSerializer(secret, salt="ci-unsubscribe").dumps(int(user_id))


def read_unsubscribe_token(secret, token):
    try:
        return int(URLSafeSerializer(secret, salt="ci-unsubscribe").loads(token))
    except (BadSignature, ValueError, TypeError):
        return None


def get_prefs(conn, user_id):
    row = conn.execute("SELECT * FROM ci_reminder_prefs WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        return {"enabled": True, "offsets": list(DEFAULT_OFFSETS)}
    return {"enabled": bool(row["enabled"]), "offsets": parse_offsets(row["offsets"]) or list(DEFAULT_OFFSETS)}


def reminder_kind(days_left, offsets):
    """Type de rappel dû pour une échéance, ou None."""
    if days_left >= 0:
        eligible = [k for k in offsets if days_left <= k]
        return "d%d" % min(eligible) if eligible else None
    weeks = (-days_left - 1) // 7
    return "late%d" % weeks if weeks < MAX_LATE_WEEKS else None


def collect(conn, today):
    """{user_id: {'email','name','language','items':[...]}} des rappels dus aujourd'hui,
    sans tenir compte du journal (le filtrage se fait à l'envoi)."""
    users = {}
    rows = conn.execute(
        "SELECT o.*, u.email, u.full_name, u.language FROM contract_obligations o JOIN users u ON u.id=o.user_id "
        "WHERE o.status='a_faire' AND o.due_date IS NOT NULL AND o.due_date<>''"
    ).fetchall()
    prefs_cache = {}
    for r in rows:
        try:
            due = datetime.date.fromisoformat(r["due_date"])
        except ValueError:
            continue
        uid = r["user_id"]
        if uid not in prefs_cache:
            prefs_cache[uid] = get_prefs(conn, uid)
        prefs = prefs_cache[uid]
        if not prefs["enabled"]:
            continue
        days_left = (due - today).days
        kind = reminder_kind(days_left, prefs["offsets"])
        if not kind:
            continue
        u = users.setdefault(uid, {"email": r["email"], "name": r["full_name"], "language": r["language"] or "fr", "items": []})
        u["items"].append({"id": r["id"], "label": r["label"], "contract": r["contract_label"], "due": r["due_date"],
                           "days_left": days_left, "kind": kind})
    return users


def build_email(name, language, items, secret, user_id, test=False):
    en = language == "en"
    items = sorted(items, key=lambda i: i["due"])
    lines = []
    for i in items:
        if i["days_left"] < 0:
            when = ("overdue by %d day(s)" if en else "en retard de %d jour(s)") % (-i["days_left"])
        elif i["days_left"] == 0:
            when = "due today" if en else "à échéance aujourd'hui"
        else:
            when = ("in %d day(s)" if en else "dans %d jour(s)") % i["days_left"]
        ctr = (" — " + i["contract"]) if i["contract"] else ""
        lines.append("• %s%s\n  %s (%s)" % (i["label"], ctr, i["due"], when))
    base = site_url()
    link = base + ("/en/contract-intelligence/deadlines" if en else "/contract-intelligence/echeances")
    unsub = "%s/contract-intelligence/rappels/desabonnement?t=%s" % (base, unsubscribe_token(secret, user_id))
    if en:
        subject = "Contract deadlines: %d item(s) need your attention" % len(items)
        body = ("Hello %s,\n\nHere are your upcoming or overdue contract deadlines:\n\n%s\n\nOpen your tracker: %s\n\n"
                "These reminders come from the dates you tracked; check the original contracts.\n"
                "Stop these emails: %s\n— Massey Contracts & Tax") % (name or "", "\n".join(lines), link, unsub)
    else:
        subject = "Échéances de contrats : %d élément(s) à traiter" % len(items)
        body = ("Bonjour %s,\n\nVoici vos échéances de contrats à venir ou en retard :\n\n%s\n\nOuvrir votre suivi : %s\n\n"
                "Ces rappels reposent sur les dates que vous avez suivies ; vérifiez toujours dans les contrats d'origine.\n"
                "Ne plus recevoir ces courriels : %s\n— Massey Contracts & Tax") % (name or "", "\n".join(lines), link, unsub)
    if test:
        subject = ("[TEST] " if True else "") + subject
    return subject, body


def run(secret, today=None, send=None):
    """Envoie les rappels dus. Retourne {'users':n, 'items':n, 'sent':n, 'failed':n, 'skipped_no_smtp':bool}.
    Chaque rappel est « réservé » dans le journal avant l'envoi (INSERT OR IGNORE) : deux
    processus simultanés n'envoient donc jamais le même rappel deux fois ; en cas d'échec
    d'envoi la réservation est retirée pour un nouvel essai au prochain passage."""
    send = send or notifications.send_email
    today = today or local_today()
    summary = {"users": 0, "items": 0, "sent": 0, "failed": 0, "skipped_no_smtp": False}
    if send is notifications.send_email and not notifications.is_configured():
        summary["skipped_no_smtp"] = True
        return summary
    conn = dbm.get_db()
    try:
        for uid, u in collect(conn, today).items():
            claimed = []
            for it in u["items"]:
                cur = conn.execute("INSERT OR IGNORE INTO ci_reminder_log (obligation_id, kind, sent_at) VALUES (?,?,?)",
                                   (it["id"], it["kind"], dbm.now()))
                if cur.rowcount:
                    claimed.append(it)
            conn.commit()
            if not claimed:
                continue
            summary["users"] += 1
            subject, body = build_email(u["name"], u["language"], claimed, secret, uid)
            if send(u["email"], subject, body):
                summary["sent"] += 1
                summary["items"] += len(claimed)
            else:
                summary["failed"] += 1
                for it in claimed:
                    conn.execute("DELETE FROM ci_reminder_log WHERE obligation_id=? AND kind=?", (it["id"], it["kind"]))
                conn.commit()
    finally:
        conn.close()
    return summary
