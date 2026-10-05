"""Vérification d'identité en direct pour la signature simple (option « pièce d'identité + selfie »).

Ce que fait le module :
  - le signataire choisit son pays de résidence et un type de pièce accepté pour ce pays, déclare la date
    d'expiration (doit être future), puis photographie sa pièce et prend un selfie AVEC LA CAMÉRA du
    navigateur (aucun envoi de fichier existant) ; un geste aléatoire est demandé pour le selfie ;
  - les images sont chiffrées au repos (ci_crypto), visibles uniquement par l'expéditeur, qui compare
    pièce, selfie et nom saisi puis confirme ou rejette ;
  - la signature n'est finalisée que lorsque l'expéditeur a confirmé chaque identité ;
  - les images sont supprimées automatiquement (IDCHECK_RETENTION_DAYS, 30 j par défaut après la clôture)
    ou sur demande ; seules les métadonnées (type, pays, dates, décision) restent dans le certificat.

Limites (affichées à l'utilisateur) : aucune reconnaissance faciale ni contrôle automatique d'authenticité ;
la « prise en direct » est imposée par l'interface et par un défi à usage unique (délai minimal, nonce),
mais un utilisateur techniquement habile peut contourner un navigateur : la décision reste humaine.
"""
import hashlib
import io
import os
import random
import secrets
from datetime import date, datetime, timedelta

from flask import abort, flash, jsonify, make_response, redirect, request

import ci_crypto

m = None
F = None
X = None  # ci_extras

MIN_SECONDS = 8          # délai minimal entre l'affichage du défi et l'envoi
MAX_SECONDS = 1800
MAX_ATTEMPTS = 8
MAX_IMG = 3 * 1024 * 1024
MIN_IMG = 5 * 1024

DOC_LABELS = {
    "passport": ("Passeport", "Passport"),
    "national_id": ("Carte d'identité nationale", "National ID card"),
    "driver": ("Permis de conduire (avec photo)", "Driver's licence (with photo)"),
    "residence_permit": ("Titre / carte de séjour", "Residence permit / card"),
    "cin": ("Carte d'identification nationale (CIN)", "National ID card (CIN)"),
    "state_id": ("Carte d'identité provinciale / d'État (avec photo)", "Provincial / state photo ID"),
    "cedula": ("Cédula", "Cédula"),
}
COUNTRIES = {  # code -> (nom fr, nom en, types acceptés)
    "HT": ("Haïti", "Haiti", ["cin", "passport", "driver"]),
    "CA": ("Canada", "Canada", ["passport", "driver", "state_id", "residence_permit"]),
    "US": ("États-Unis", "United States", ["passport", "driver", "state_id", "residence_permit"]),
    "FR": ("France", "France", ["national_id", "passport", "driver", "residence_permit"]),
    "DO": ("République dominicaine", "Dominican Republic", ["cedula", "passport", "driver"]),
    "XX": ("Autre pays", "Other country", ["passport", "national_id", "driver", "residence_permit"]),
}
GESTURES = {
    "fr": ["Levez la main droite à côté de votre visage.", "Touchez votre nez avec l'index.", "Montrez %d doigts à côté de votre visage.",
           "Tournez lentement la tête vers la gauche.", "Faites un clin d'œil de l'œil droit."],
    "en": ["Raise your right hand next to your face.", "Touch your nose with your index finger.", "Show %d fingers next to your face.",
           "Slowly turn your head to the left.", "Wink with your right eye."],
}


def retention_days():
    try:
        return max(1, int(os.environ.get("IDCHECK_RETENTION_DAYS", "30")))
    except ValueError:
        return 30


def doc_options(lang):
    i = 1 if lang == "en" else 0
    return {c: {"name": v[i], "types": [(t, DOC_LABELS[t][i]) for t in v[2]]} for c, v in COUNTRIES.items()}


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _idir(owner_id):
    p = os.path.join(m.UPLOAD_DIR, "ci_id", str(int(owner_id)))
    os.makedirs(p, exist_ok=True)
    return p


def _valid_jpeg(blob):
    if not blob or not (MIN_IMG <= len(blob) <= MAX_IMG) or blob[:3] != b"\xff\xd8\xff":
        return False
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(blob))
        im.verify()
        im = Image.open(io.BytesIO(blob))
        return im.format == "JPEG" and im.size[0] >= 480 and im.size[1] >= 360
    except ImportError:
        return True
    except Exception:  # noqa: BLE001
        return False


def issue_challenge(conn, signer, lang):
    """Nouveau défi (nonce + geste) ; renvoie (nonce, texte du geste)."""
    if signer["id_challenge"] and signer["id_challenge_at"]:
        try:
            if (datetime.utcnow() - datetime.fromisoformat(signer["id_challenge_at"])).total_seconds() < 600:
                return signer["id_challenge"], current_gesture(signer, lang)
        except ValueError:
            pass
    nonce = secrets.token_urlsafe(16)
    k = random.randrange(len(GESTURES["fr"]))
    n = random.randint(1, 5)
    conn.execute("UPDATE ci_signers SET id_challenge=?, id_challenge_at=?, id_gesture=? WHERE id=?", (nonce, _now(), "%d:%d" % (k, n), signer["id"]))
    conn.commit()
    return nonce, gesture_text(k, n, lang)


def gesture_text(k, n, lang):
    s = GESTURES["en" if lang == "en" else "fr"][k]
    return s % n if "%d" in s else s


def current_gesture(signer, lang):
    try:
        k, n = (signer["id_gesture"] or "0:1").split(":")
        return gesture_text(int(k), int(n), lang)
    except (ValueError, IndexError):
        return ""


def id_state(signer):
    return (signer["id_status"] if "id_status" in signer.keys() else "") or ""


def request_needs_id(req):
    return bool(req["id_check"]) if "id_check" in req.keys() else False


def all_identities_ok(conn, req):
    if not request_needs_id(req):
        return True
    rows = conn.execute("SELECT id_status FROM ci_signers WHERE request_id=?", (req["id"],)).fetchall()
    return all((r["id_status"] or "") == "approved" for r in rows)


def purge_images(conn, signer_ids=None, due_only=False):
    """Supprime définitivement les images (écrasement + suppression). due_only : seulement les demandes closes depuis > rétention."""
    sql = ("SELECT s.id, s.id_doc_path, s.id_selfie_path FROM ci_signers s JOIN ci_signature_requests r ON r.id=s.request_id "
           "WHERE (s.id_doc_path IS NOT NULL OR s.id_selfie_path IS NOT NULL)")
    args = []
    if signer_ids is not None:
        if not signer_ids:
            return 0
        sql += " AND s.id IN (%s)" % ",".join("?" * len(signer_ids))
        args += list(signer_ids)
    if due_only:
        limit = (datetime.utcnow() - timedelta(days=retention_days())).isoformat(timespec="seconds")
        sql += " AND r.status IN ('completed','cancelled','declined') AND COALESCE(r.completed_at, r.created_at) < ?"
        args.append(limit)
    n = 0
    for r in conn.execute(sql, args).fetchall():
        for p in (r["id_doc_path"], r["id_selfie_path"]):
            if p and os.path.exists(p):
                ci_crypto.erase(p)
        conn.execute("UPDATE ci_signers SET id_doc_path=NULL, id_selfie_path=NULL, id_purged_at=? WHERE id=?", (_now(), r["id"]))
        n += 1
    conn.commit()
    return n


def purge_due():
    conn = m.dbm.get_db()
    try:
        return purge_images(conn, due_only=True)
    finally:
        conn.close()


def identity_summary(s):
    """Texte pour le certificat PDF (sans image)."""
    if (s["id_status"] or "") != "approved":
        return ""
    country = COUNTRIES.get(s["id_country"] or "XX", COUNTRIES["XX"])[0]
    return "%s — %s (%s)" % (DOC_LABELS.get(s["id_doc_type"], ("", ""))[0] or s["id_doc_type"], country, (s["id_reviewed_at"] or "")[:10])


# ---------------------------------------------------------------------------

def init(app_module, features_module, extras_module):
    global m, F, X
    m, F, X = app_module, features_module, extras_module
    app = m.app

    @app.route("/contract-intelligence/signature/<token>/identite", methods=["POST"], endpoint="ci_idcheck_submit")
    def ci_idcheck_submit(token):
        conn = m.dbm.get_db()
        try:
            signer = conn.execute("SELECT * FROM ci_signers WHERE token=?", (token,)).fetchone()
            if not signer:
                abort(404)
            req = conn.execute("SELECT * FROM ci_signature_requests WHERE id=?", (signer["request_id"],)).fetchone()
            en = req["lang"] == "en"
            T = (lambda fr, e: e if en else fr)

            def fail(msg, code=400):
                return jsonify(ok=False, error=msg), code
            if not request_needs_id(req) or req["status"] != "pending" or signer["status"] != "pending" or not X._link_valid(signer) or req["method"] == "certified":
                return fail(T("Cette étape n'est pas disponible.", "This step is not available."), 403)
            if id_state(signer) in ("captured", "approved"):
                return fail(T("Votre identité est déjà enregistrée.", "Your identity is already recorded."), 409)
            if (signer["id_attempts"] or 0) >= MAX_ATTEMPTS:
                return fail(T("Trop de tentatives. Demandez à l'expéditeur de vous renvoyer un lien.", "Too many attempts. Ask the sender to resend the link."), 429)
            nonce = request.form.get("challenge", "")
            started = signer["id_challenge_at"] or ""
            if not signer["id_challenge"] or not secrets.compare_digest(signer["id_challenge"], nonce):
                return fail(T("Session de capture expirée : rechargez la page.", "Capture session expired: reload the page."))
            try:
                age = (datetime.utcnow() - datetime.fromisoformat(started)).total_seconds()
            except ValueError:
                age = -1
            if age < MIN_SECONDS or age > MAX_SECONDS:
                return fail(T("Capture trop rapide ou trop ancienne : rechargez la page et recommencez avec la caméra.", "Capture too fast or too old: reload the page and retry with the camera."))
            conn.execute("UPDATE ci_signers SET id_attempts=COALESCE(id_attempts,0)+1 WHERE id=?", (signer["id"],))
            conn.commit()
            country = request.form.get("country", "")
            doc_type = request.form.get("doc_type", "")
            if country not in COUNTRIES or doc_type not in COUNTRIES[country][2]:
                return fail(T("Ce type de pièce n'est pas accepté pour ce pays de résidence.", "This document type is not accepted for this country of residence."))
            try:
                expiry = date.fromisoformat(request.form.get("expiry", ""))
            except ValueError:
                return fail(T("Date d'expiration invalide.", "Invalid expiry date."))
            if expiry <= date.today():
                return fail(T("La pièce est expirée : seule une pièce en cours de validité est acceptée.", "The document has expired: only a valid document is accepted."))
            if expiry > date.today() + timedelta(days=366 * 25):
                return fail(T("Date d'expiration invalide.", "Invalid expiry date."))
            if not request.form.get("consent"):
                return fail(T("Le consentement est requis.", "Consent is required."))
            doc = request.files.get("doc_image")
            selfie = request.files.get("selfie_image")
            doc_b = doc.read(MAX_IMG + 1) if doc else b""
            selfie_b = selfie.read(MAX_IMG + 1) if selfie else b""
            if not (_valid_jpeg(doc_b) and _valid_jpeg(selfie_b)):
                return fail(T("Images invalides ou trop petites : reprenez les photos avec la caméra.", "Invalid or too small images: retake the photos with the camera."))
            if hashlib.sha256(doc_b).digest() == hashlib.sha256(selfie_b).digest():
                return fail(T("La photo de la pièce et le selfie doivent être différents.", "The document photo and the selfie must be different."))
            d = _idir(req["owner_id"])
            tag = secrets.token_hex(8)
            pd, ps = os.path.join(d, "doc_%d_%s.bin" % (signer["id"], tag)), os.path.join(d, "selfie_%d_%s.bin" % (signer["id"], tag))
            for old in (signer["id_doc_path"], signer["id_selfie_path"]):
                if old and os.path.exists(old):
                    ci_crypto.erase(old)
            ci_crypto.write(pd, doc_b)
            ci_crypto.write(ps, selfie_b)
            conn.execute("UPDATE ci_signers SET id_status='captured', id_country=?, id_doc_type=?, id_doc_expiry=?, id_doc_path=?, id_selfie_path=?, id_captured_at=?, "
                         "id_capture_ip=?, id_challenge=NULL, id_note=NULL, id_purged_at=NULL WHERE id=?",
                         (country, doc_type, expiry.isoformat(), pd, ps, _now(), request.remote_addr, signer["id"]))
            conn.commit()
            X._log_sig(conn, req, "id_captured")
            return jsonify(ok=True)
        finally:
            conn.close()

    def _owner_signer(conn, sid, signer_id):
        u = m._ci_user()
        if not u:
            return None, None, None
        req = X._owned_request(conn, u, sid)
        s = conn.execute("SELECT * FROM ci_signers WHERE id=? AND request_id=?", (signer_id, sid)).fetchone()
        if not s:
            abort(404)
        return u, req, s

    @m.ci_route("idcheck_image", "/signatures/<int:sid>/identite/<int:signer_id>/<kind>", "/signatures/<int:sid>/identity/<int:signer_id>/<kind>")
    def ci_idcheck_image(sid, signer_id, kind):
        conn = m.dbm.get_db()
        try:
            u = m._ci_user()
            if not u:
                return m._ci_need_login()
            u, req, s = _owner_signer(conn, sid, signer_id)
            path = s["id_doc_path"] if kind == "piece" else (s["id_selfie_path"] if kind == "selfie" else None)
            if not path or not os.path.exists(path):
                abort(404)
            data = ci_crypto.read(path)
            import ci_team
            ci_team.log(conn, req["registry_id"], u, "id_viewed", "%s #%d" % (kind, signer_id))
            conn.commit()
        finally:
            conn.close()
        resp = make_response(data)
        resp.headers["Content-Type"] = "image/jpeg"
        resp.headers["Cache-Control"] = "no-store, private"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @m.ci_route("idcheck_decide", "/signatures/<int:sid>/identite/<int:signer_id>", "/signatures/<int:sid>/identity/<int:signer_id>", ("POST",))
    def ci_idcheck_decide(sid, signer_id):
        conn = m.dbm.get_db()
        try:
            u = m._ci_user()
            if not u:
                return m._ci_need_login()
            u, req, s = _owner_signer(conn, sid, signer_id)
            back = m._ci_redirect("signature", sid=sid)
            en = req["lang"] == "en"
            if request.form.get("action") != "purge" and (req["status"] != "pending" or id_state(s) != "captured"):
                flash(m._T("Rien à décider pour ce signataire.", "Nothing to decide for this signatory."), "error")
                return back
            action = request.form.get("action")
            if action == "approve":
                if not request.form.get("checked"):
                    flash(m._T("Cochez la case pour confirmer que vous avez comparé la pièce, le selfie et le nom.", "Tick the box to confirm you compared the document, the selfie and the name."), "error")
                    return back
                conn.execute("UPDATE ci_signers SET id_status='approved', id_reviewed_at=? WHERE id=?", (_now(), s["id"]))
                conn.commit()
                X._log_sig(conn, req, "id_approved")
                flash(m._T("Identité confirmée.", "Identity confirmed."), "success")
                left = conn.execute("SELECT COUNT(*) AS c FROM ci_signers WHERE request_id=? AND status<>'signed'", (sid,)).fetchone()["c"]
                if left == 0 and all_identities_ok(conn, req):
                    X._complete(conn, req)
                    flash(m._T("Tous ont signé et ont été vérifiés : le contrat signé est archivé.", "Everyone signed and was verified: the signed contract is archived."), "success")
            elif action == "reject":
                reason = (request.form.get("reason") or "").strip()[:300]
                if not reason:
                    flash(m._T("Indiquez un motif (le signataire le recevra).", "Give a reason (the signatory will receive it)."), "error")
                    return back
                purge_images(conn, [s["id"]])
                conn.execute("UPDATE ci_signers SET id_status='rejected', id_note=?, id_reviewed_at=?, id_attempts=0, status='pending', signed_name=NULL, signed_at=NULL, "
                             "ip_address=NULL, user_agent=NULL, notified_at=? WHERE id=?", (reason, _now(), _now(), s["id"]))
                conn.commit()
                X._log_sig(conn, req, "id_rejected")
                F._notify(s["signer_email"], ("Identity check to redo: " if en else "Vérification d'identité à refaire : ") + req["title"],
                          (("The sender could not confirm your identity.\nReason: %s\n\nPlease retake the photos with your camera and sign again:\n%s\n") if en else
                           ("L'expéditeur n'a pas pu confirmer votre identité.\nMotif : %s\n\nReprenez les photos avec votre caméra puis signez à nouveau :\n%s\n")) % (reason, F._site_link("/contract-intelligence/signature/" + s["token"])))
                flash(m._T("Identité rejetée : le signataire doit recommencer.", "Identity rejected: the signatory must start over."), "success")
            elif action == "purge":
                if id_state(s) == "captured":
                    flash(m._T("Décidez d'abord (confirmer ou rejeter) avant de supprimer les images.", "Decide first (confirm or reject) before deleting the images."), "error")
                else:
                    purge_images(conn, [s["id"]])
                    flash(m._T("Images supprimées définitivement.", "Images permanently deleted."), "success")
            return back
        finally:
            conn.close()
