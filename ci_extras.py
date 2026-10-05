"""Contract Intelligence — signature depuis le dossier contrat, rapport PDF, calendrier .ics.

Enregistré par ci_features.init() ; réutilise ses aides (courriel, jetons, registre)."""
import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta

from flask import abort, flash, g, redirect, render_template, request, send_file, Response, url_for

import ci_crypto
import ci_esign
import ci_legal
import ci_pdf
import ci_reminders

m = None   # module app
F = None   # module ci_features

SIGNATURE_LINK_DAYS = 30
LEGAL_SOURCES = [
    ("Décret sur la signature électronique, 9 décembre 2015, publié au Moniteur le 29 janvier 2016 (Journal officiel)", "https://www.omrh.gouv.ht/Media/Publications/3-Decrets/moniteur_20_29012016.pdf"),
    ("HaitiLibre : Tout savoir sur les différents niveaux de signature électronique en Haïti", "https://www.haitilibre.com/article-47541-tout-savoir-sur-les-differents-niveaux-de-signature-electronique-en-haiti.html"),
    ("Juno7 : le CONATEL annonce la publication de l'arrêté relatif à la signature électronique", "https://www.juno7.ht/le-conatel-annonce-la-publication-de-larrete-relatif/"),
    ("Rezo Nòdwès : feuille de route du CONATEL pour la signature électronique", "https://rezonodwes.com/?p=367595"),
    ("Cabinet Volmar : la signature électronique et le début de la modernisation du droit haïtien des affaires", "https://hditcabinetvolmar.com/fr/la-signature-electronique-et-le-debut-de-la-modernisation-du-droit-haitien-des-affaires/"),
    ("Cabinet Volmar : validité du consentement contractuel dématérialisé", "https://hditcabinetvolmar.com/fr/analyse-de-la-validite-du-consentement-contractuel-dematerialise-au-regard-de-la-loi-du-17-mars-2017/"),
]
SIG_STATUS = {
    "fr": {"pending": "En cours", "completed": "Signé par tous", "declined": "Refusé", "cancelled": "Annulée"},
    "en": {"pending": "In progress", "completed": "Signed by all", "declined": "Declined", "cancelled": "Cancelled"},
}
SIGNER_STATUS = {
    "fr": {"pending": "En attente", "signed": "A signé", "declined": "A refusé"},
    "en": {"pending": "Waiting", "signed": "Signed", "declined": "Declined"},
}
CONSENT = {
    "fr": ("J'ai lu et compris l'intégralité du contrat ci-dessus.",
           "J'accepte que ma saisie de nom légal complet constitue ma signature de ce contrat, avec la même valeur qu'une signature manuscrite entre les parties, dans la mesure permise par la loi applicable."),
    "en": ("I have read and understood the entire contract above.",
           "I agree that typing my full legal name constitutes my signature of this contract, with the same value as a handwritten signature between the parties, to the extent permitted by applicable law."),
}


def init(app_module, features_module):
    global m, F
    m, F = app_module, features_module
    _register_signatures()
    _register_report()
    _register_calendar()
    _register_verify()


def _now():
    return m.dbm.now()


# ---------------------------------------------------------------------------
# Rapport d'analyse en PDF
# ---------------------------------------------------------------------------

def _register_report():
    @m.ci_route("analysis_pdf", "/analyse/<int:aid>/rapport.pdf", "/analysis/<int:aid>/report.pdf")
    def ci_analysis_pdf(aid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        import ci_review
        row, _role = ci_review.analysis_access(conn, u, aid, "viewer")
        conn.close()
        result = json.loads(row["result_json"])
        lang = "en" if g.lang == "en" else "fr"
        flat = m.contract_engine.extract_party_obligations(row["text_content"], result["language"])
        obligations = m.contract_engine.extract_obligations(row["text_content"], result["language"], None)
        data = ci_pdf.analysis_report(row["title"], result, lang, m.contract_engine.party_groups(flat), obligations,
                                      row["created_at"], m.contract_engine.LEVEL_LABELS[lang])
        name = "rapport-analyse-%d.pdf" % aid if lang == "fr" else "analysis-report-%d.pdf" % aid
        return Response(data, mimetype="application/pdf", headers={"Content-Disposition": 'attachment; filename="%s"' % name})


# ---------------------------------------------------------------------------
# Calendrier .ics
# ---------------------------------------------------------------------------

def _ics_escape(text):
    return (str(text or "").replace("\\", "\\\\").replace(";", "\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n"))


def _ics_fold(line):
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        limit = 75 if not parts else 74
        if len(cur) + len(b) > limit:
            parts.append(cur)
            cur = b
        else:
            cur += b
    parts.append(cur)
    return "\r\n ".join(p.decode("utf-8") for p in parts)


def build_ics(rows, lang, calname):
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    en = lang == "en"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Massey Contracts & Tax//Contract Intelligence//%s" % ("EN" if en else "FR"),
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:" + _ics_escape(calname), "X-WR-TIMEZONE:America/Port-au-Prince"]
    for r in rows:
        try:
            d = datetime.strptime(r["due_date"][:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        summary = r["label"] + ((" - " + r["contract_label"]) if r["contract_label"] and r["contract_label"] not in r["label"] else "")
        desc = ("Tracked in Massey Contract Intelligence. Always check the date in the original contract." if en
                else "Suivi dans Massey Contract Intelligence. Vérifiez toujours la date dans le contrat d'origine.")
        if r["estimated"]:
            desc = ("Estimated date. " if en else "Date estimée. ") + desc
        lines += ["BEGIN:VEVENT", "UID:ob-%s@massey-contracts" % r["id"], "DTSTAMP:" + stamp,
                  "DTSTART;VALUE=DATE:" + d.strftime("%Y%m%d"), "DTEND;VALUE=DATE:" + (d + timedelta(days=1)).strftime("%Y%m%d"),
                  "SUMMARY:" + _ics_escape(summary), "DESCRIPTION:" + _ics_escape(desc), "TRANSP:TRANSPARENT"]
        for days in (7, 1):
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + _ics_escape(summary), "TRIGGER:-P%dD" % days, "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(l) for l in lines) + "\r\n"


def _open_deadlines(conn, uid):
    return conn.execute("SELECT * FROM contract_obligations WHERE user_id=? AND status='a_faire' AND due_date IS NOT NULL AND due_date<>'' ORDER BY due_date", (uid,)).fetchall()


def calendar_context(conn, u):
    row = conn.execute("SELECT token FROM ci_calendar_tokens WHERE user_id=?", (u["id"],)).fetchone()
    if not row:
        return {"feed_url": None, "webcal_url": None}
    url = F._site_link("/contract-intelligence/calendrier/" + row["token"] + ".ics")
    return {"feed_url": url, "webcal_url": url.replace("https://", "webcal://").replace("http://", "webcal://")}


def _register_calendar():
    app = m.app

    @m.ci_route("calendar_ics", "/echeances/calendrier.ics", "/deadlines/calendar.ics")
    def ci_calendar_ics():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        rows = _open_deadlines(conn, u["id"])
        conn.close()
        lang = "en" if g.lang == "en" else "fr"
        data = build_ics(rows, lang, "Massey - Contract deadlines" if lang == "en" else "Massey - Échéances de contrats")
        return Response(data, mimetype="text/calendar", headers={"Content-Disposition": 'attachment; filename="echeances-massey.ics"'})

    @m.ci_route("calendar_link", "/echeances/calendrier/lien", "/deadlines/calendar/link", ("POST",))
    def ci_calendar_link():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        conn.execute("INSERT INTO ci_calendar_tokens (user_id, token, created_at) VALUES (?,?,?) "
                     "ON CONFLICT(user_id) DO UPDATE SET token=excluded.token, created_at=excluded.created_at",
                     (u["id"], secrets.token_urlsafe(24), _now()))
        conn.commit()
        conn.close()
        flash(m._T("Lien d'abonnement créé. L'ancien lien, s'il existait, ne fonctionne plus.", "Subscription link created. Any previous link no longer works."), "success")
        return m._ci_redirect("deadlines")

    @m.ci_route("calendar_revoke", "/echeances/calendrier/revoquer", "/deadlines/calendar/revoke", ("POST",))
    def ci_calendar_revoke():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        conn.execute("DELETE FROM ci_calendar_tokens WHERE user_id=?", (u["id"],))
        conn.commit()
        conn.close()
        flash(m._T("Lien d'abonnement supprimé.", "Subscription link deleted."), "success")
        return m._ci_redirect("deadlines")

    @app.route("/contract-intelligence/calendrier/<token>.ics", endpoint="ci_calendar_feed")
    def ci_calendar_feed(token):
        conn = m.dbm.get_db()
        row = conn.execute("SELECT user_id FROM ci_calendar_tokens WHERE token=?", (token,)).fetchone()
        if not row:
            conn.close()
            abort(404)
        rows = _open_deadlines(conn, row["user_id"])
        user = conn.execute("SELECT language FROM users WHERE id=?", (row["user_id"],)).fetchone()
        conn.close()
        lang = "en" if user and user["language"] == "en" else "fr"
        data = build_ics(rows, lang, "Massey - Contract deadlines" if lang == "en" else "Massey - Échéances de contrats")
        return Response(data, mimetype="text/calendar", headers={"Cache-Control": "private, max-age=900"})


# ---------------------------------------------------------------------------
# Signature électronique depuis le dossier contrat
# ---------------------------------------------------------------------------

def signable_text(conn, u, entry):
    """(texte, libellé de la source) du contrat à signer, ou (None, None)."""
    flow = F._stage_state(conn, u, entry)
    agreed = [n for n in flow["negotiations"] if n["status"] == "agreed"]
    if agreed:
        n = agreed[0]
        v = conn.execute("SELECT * FROM ci_neg_versions WHERE negotiation_id=? ORDER BY version_no DESC LIMIT 1", (n["id"],)).fetchone()
        if v:
            return v["body_text"], m._T("version %d de la négociation conclue" % v["version_no"], "version %d of the agreed negotiation" % v["version_no"])
    if entry["draft_id"]:
        d = conn.execute("SELECT body_text FROM contract_drafts WHERE id=? AND user_id=?", (entry["draft_id"], u["id"])).fetchone()
        if d and d["body_text"].strip():
            return d["body_text"], m._T("brouillon lié", "linked draft")
    if entry["analysis_id"]:
        a = conn.execute("SELECT text_content FROM contract_analyses WHERE id=? AND user_id=?", (entry["analysis_id"], u["id"])).fetchone()
        if a and a["text_content"].strip():
            return a["text_content"], m._T("texte de l'analyse liée", "text of the linked analysis")
    return None, None


def registry_signature_context(conn, u, entry):
    req = conn.execute("SELECT * FROM ci_signature_requests WHERE registry_id=? AND owner_id=? ORDER BY id DESC LIMIT 1", (entry["id"], u["id"])).fetchone()
    signers = conn.execute("SELECT * FROM ci_signers WHERE request_id=?  ORDER BY id", (req["id"],)).fetchall() if req else []
    text, label = signable_text(conn, u, entry)
    limit, used, allowed = m._ci_quota(conn, u, "signature")
    overall = None
    if entry["analysis_id"]:
        a = conn.execute("SELECT overall FROM contract_analyses WHERE id=? AND user_id=?", (entry["analysis_id"], u["id"])).fetchone()
        overall = a["overall"] if a else None
    lang = "en" if g.lang == "en" else "fr"
    stakes, reasons = ci_legal.suggest_stakes(text or "", entry["value_text"] or "", overall, lang)
    certified = ci_esign.is_configured()
    return {"sig_req": req, "sig_signers": signers, "sig_source": label, "sig_has_text": bool(text),
            "sig_status": SIG_STATUS[g.lang], "signer_status": SIGNER_STATUS[g.lang], "sig_allowed": allowed, "sig_limit": limit,
            "stakes": stakes, "stakes_reasons": reasons, "stakes_labels": ci_legal.STAKES_LABELS[lang],
            "stakes_advice": {k: ci_legal.advice(k, certified, lang) for k in ("courant", "important", "forme")},
            "certified_available": certified, "legal_url": m.ci_url("legal")}


def _invite_email(req, signer, owner_name):
    en = req["lang"] == "en"
    link = F._site_link("/contract-intelligence/signature/" + signer["token"])
    note = (("\n\nMessage: " if en else "\n\nMessage : ") + req["message"]) if req["message"] else ""
    if en:
        return ("Signature requested: %s" % req["title"],
                "Hello %s,\n\n%s asks you to sign the contract \"%s\".%s\n\nRead it and sign here (no account needed, link valid %d days):\n%s\n\n"
                "Anyone with this link can sign on your behalf: do not forward it.\n— Massey Contracts & Tax"
                % (signer["signer_name"] or "", owner_name, req["title"], note, SIGNATURE_LINK_DAYS, link))
    return ("Signature demandée : %s" % req["title"],
            "Bonjour %s,\n\n%s vous demande de signer le contrat « %s ».%s\n\nLisez-le et signez ici (aucun compte requis, lien valable %d jours) :\n%s\n\n"
            "Toute personne qui possède ce lien peut signer à votre place : ne le transférez pas.\n— Massey Contracts & Tax"
            % (signer["signer_name"] or "", owner_name, req["title"], note, SIGNATURE_LINK_DAYS, link))


def _link_valid(signer):
    try:
        start = datetime.fromisoformat(signer["notified_at"])
        if start.tzinfo:
            start = start.replace(tzinfo=None) - (start.utcoffset() or timedelta(0))
    except (TypeError, ValueError):
        return True
    return datetime.utcnow() - start < timedelta(days=SIGNATURE_LINK_DAYS)


def _archive_name(req):
    base = ("contrat-certifie-%d.pdf" if req["lang"] == "fr" else "certified-contract-%d.pdf") if (req["method"] if "method" in req.keys() else "simple") == "certified" \
        else ("contrat-signe-%d.pdf" if req["lang"] == "fr" else "signed-contract-%d.pdf")
    return base % req["id"]


def _verify_url(req):
    return F._site_link("/contract-intelligence/verifier?t=" + req["body_sha256"])


def _store_certificate(conn, req, cert_bytes):
    digest = hashlib.sha256(cert_bytes).hexdigest()
    base = "certificat-realisation-%d.pdf" if req["lang"] == "fr" else "completion-certificate-%d.pdf"
    path = os.path.join(F._registry_dir(req["owner_id"]), "%s_%s" % (digest[:16], base % req["id"]))
    ci_crypto.write(path, cert_bytes)
    conn.execute("UPDATE ci_signature_requests SET cert_path=?, cert_sha256=? WHERE id=?", (path, digest, req["id"]))
    conn.commit()


def _archive_certified(conn, req):
    """Télécharge chez DocuSign le PDF signé (laissé intact pour conserver le sceau numérique) et le certificat (à part)."""
    combined = ci_esign.download(req["provider_ref"], "combined")
    cert = ci_esign.download(req["provider_ref"], "certificate")
    _archive_pdf(conn, req, combined)
    _store_certificate(conn, req, cert)


def _archive_pdf(conn, req, data):
    """Écrit le PDF final dans le registre (empreinte SHA-256), passe le contrat à « Actif »."""
    digest = hashlib.sha256(data).hexdigest()
    entry = conn.execute("SELECT * FROM ci_registry WHERE id=?", (req["registry_id"],)).fetchone()
    name = _archive_name(req)
    path = os.path.join(F._registry_dir(req["owner_id"]), "%s_%s" % (digest[:16], name))
    ci_crypto.write(path, data)
    if entry["file_path"] and entry["file_path"] != path and os.path.exists(entry["file_path"]):
        ci_crypto.erase(entry["file_path"])
    conn.execute("UPDATE ci_registry SET file_name=?, file_path=?, file_sha256=?, file_text=?, status='actif', start_date=COALESCE(NULLIF(start_date,''), ?), updated_at=? WHERE id=?",
                 (name, path, digest, req["body_snapshot"][:60000], datetime.utcnow().strftime("%Y-%m-%d"), _now(), req["registry_id"]))
    conn.execute("UPDATE ci_signature_requests SET final_sha256=? WHERE id=?", (digest, req["id"]))
    conn.commit()


def _notify_completed(conn, req):
    en = req["lang"] == "en"
    owner = conn.execute("SELECT * FROM users WHERE id=?", (req["owner_id"],)).fetchone()
    for s in conn.execute("SELECT * FROM ci_signers WHERE request_id=?", (req["id"],)).fetchall():
        if (req["method"] if "method" in req.keys() else "simple") == "certified":
            body = ("Everyone has signed \"%s\". The signed document and its certificate of completion were sent to you by the signature provider.\n" if en
                    else "Tout le monde a signé « %s ». Le document signé et son certificat de réalisation vous ont été transmis par le fournisseur de signature.\n") % req["title"]
        else:
            link = F._site_link("/contract-intelligence/signature/" + s["token"])
            body = (("Everyone has signed \"%s\". Download the signed PDF (with the signature certificate) here:\n%s\n" if en
                     else "Tout le monde a signé « %s ». Téléchargez le PDF signé (avec le certificat de signature) ici :\n%s\n") % (req["title"], link))
        F._notify(s["signer_email"], ("Contract signed by all: " if en else "Contrat signé par tous : ") + req["title"], body)
    if owner:
        F._notify(owner["email"], ("Contract signed by all: " if en else "Contrat signé par tous : ") + req["title"],
                  (("All signatories signed \"%s\". The signed PDF is archived in your registry.\n%s\n" if en
                    else "Tous les signataires ont signé « %s ». Le PDF signé est archivé dans votre registre.\n%s\n")
                   % (req["title"], F._site_link("/contract-intelligence/registre/%d" % req["registry_id"]))))
    m.dbm.log_activity(req["owner_id"], "ci_signature_completed", "signature #%s" % req["id"])


def _log_sig(conn, req, action):
    try:
        import ci_team
        ci_team.log(conn, req["registry_id"], None, action, "SIG-%d" % req["id"])
        conn.commit()
    except Exception:  # noqa: BLE001
        pass


def _complete(conn, req):
    """Finalise une signature simple quand tous ont signé : PDF archivé dans le registre, courriels."""
    cur = conn.execute("UPDATE ci_signature_requests SET status='completed', completed_at=? WHERE id=? AND status='pending'", (_now(), req["id"]))
    conn.commit()
    if not cur.rowcount:
        return False
    req = conn.execute("SELECT * FROM ci_signature_requests WHERE id=?", (req["id"],)).fetchone()
    _log_sig(conn, req, "sig_completed")
    signers = [{"name": s["signer_name"], "email": s["signer_email"], "signed_name": s["signed_name"], "signed_at": s["signed_at"], "ip": s["ip_address"]}
               for s in conn.execute("SELECT * FROM ci_signers WHERE request_id=? ORDER BY id", (req["id"],)).fetchall()]
    try:
        data = ci_pdf.signed_contract(req["title"], req["body_snapshot"], req["lang"], req["body_sha256"], signers, "SIG-%d" % req["id"], req["completed_at"], _verify_url(req))
        _archive_pdf(conn, req, data)
    except Exception as exc:  # noqa: BLE001 — la signature reste valide ; le PDF se régénère à la demande
        m.app.logger.warning("PDF signé non archivé (%s)", exc)
    _notify_completed(conn, req)
    return True


# --- Signature certifiée (DocuSign) : suivi et finalisation ------------------

def _merge_pdfs(*chunks):
    import io
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for c in chunks:
        for page in PdfReader(io.BytesIO(c)).pages:
            w.add_page(page)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def refresh_certified(conn, req):
    """Interroge le fournisseur et met à jour la demande. Retourne le statut de la demande après mise à jour."""
    if req["status"] != "pending" or (req["method"] if "method" in req.keys() else "simple") != "certified" or not req["provider_ref"]:
        return req["status"]
    try:
        env = ci_esign.get_envelope(req["provider_ref"])
    except ci_esign.ESignError as exc:
        m.app.logger.warning("Suivi DocuSign impossible (%s) %s", exc.code, exc.detail)
        return req["status"]
    for r in env["recipients"]:
        st = "signed" if r["status"] in ("signed", "completed") else ("declined" if r["status"] == "declined" else None)
        if st:
            conn.execute("UPDATE ci_signers SET status=?, signed_at=COALESCE(signed_at, ?), decline_reason=COALESCE(NULLIF(decline_reason,''), ?), signed_name=COALESCE(signed_name, signer_name) "
                         "WHERE request_id=? AND lower(signer_email)=? AND status='pending'",
                         (st, r["signed_at"] or _now(), r["declined_reason"], req["id"], r["email"]))
    conn.commit()
    en = req["lang"] == "en"
    owner = conn.execute("SELECT * FROM users WHERE id=?", (req["owner_id"],)).fetchone()
    if env["status"] == "completed":
        claimed = conn.execute("UPDATE ci_signature_requests SET status='completed', completed_at=? WHERE id=? AND status='pending'", (_now(), req["id"]))
        conn.commit()
        if claimed.rowcount:
            fresh = conn.execute("SELECT * FROM ci_signature_requests WHERE id=?", (req["id"],)).fetchone()
            _log_sig(conn, fresh, "sig_completed")
            try:
                _archive_certified(conn, fresh)
            except Exception as exc:  # noqa: BLE001 — réessayé à la demande depuis la page de la demande
                m.app.logger.warning("PDF certifié non archivé (%s)", exc)
            _notify_completed(conn, fresh)
        return "completed"
    if env["status"] == "declined":
        reason = next((r["declined_reason"] for r in env["recipients"] if r["status"] == "declined" and r["declined_reason"]), "")
        if conn.execute("UPDATE ci_signature_requests SET status='declined' WHERE id=? AND status='pending'", (req["id"],)).rowcount:
            conn.commit()
            _log_sig(conn, req, "sig_declined")
            if owner:
                F._notify(owner["email"], ("Signature declined: " if en else "Signature refusée : ") + req["title"],
                          ("A signatory declined to sign \"%s\".\n%s\n" if en else "Un signataire a refusé de signer « %s ».\n%s\n") % (req["title"], reason)
                          + "\n" + F._site_link("/contract-intelligence/signatures/%d" % req["id"]))
        return "declined"
    if env["status"] == "voided":
        conn.execute("UPDATE ci_signature_requests SET status='cancelled' WHERE id=? AND status='pending'", (req["id"],))
        conn.commit()
        return "cancelled"
    return "pending"


def poll_certified():
    """Interroge toutes les demandes certifiées en cours (tâche de fond / appel planifié)."""
    if not ci_esign.is_configured():
        return 0
    conn = m.dbm.get_db()
    n = 0
    try:
        for req in conn.execute("SELECT * FROM ci_signature_requests WHERE status='pending' AND method='certified' AND provider_ref IS NOT NULL").fetchall():
            refresh_certified(conn, req)
            n += 1
    finally:
        conn.close()
    return n


def _owned_request(conn, u, sid):
    req = conn.execute("SELECT * FROM ci_signature_requests WHERE id=? AND owner_id=?", (sid, u["id"])).fetchone()
    if not req:
        conn.close()
        abort(404)
    return req


def _register_signatures():
    app = m.app

    @m.ci_route("signatures", "/signatures", "/signatures")
    def ci_signatures():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        rows = conn.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM ci_signers s WHERE s.request_id=r.id) AS total, "
            "(SELECT COUNT(*) FROM ci_signers s WHERE s.request_id=r.id AND s.status='signed') AS signed "
            "FROM ci_signature_requests r WHERE owner_id=? ORDER BY id DESC LIMIT 100", (u["id"],)).fetchall()
        conn.close()
        return m._ci_page("signatures", **m._ci_ctx("signatures", requests=rows, status_labels=SIG_STATUS[g.lang]))

    @m.ci_route("signature_new", "/registre/<int:rid>/signature", "/registry/<int:rid>/signature", ("POST",))
    def ci_signature_new(rid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        entry = F._reg_row(conn, u, rid)
        back = m.ci_url("registry_entry", rid=rid)
        text, source = signable_text(conn, u, entry)
        limit, used, allowed = m._ci_quota(conn, u, "signature")
        people = F._parse_people(request.form.get("signers", ""), 8)
        if request.form.get("include_me") and u["email"].lower() not in [e.lower() for _, e in people]:
            people.insert(0, (u["full_name"], u["email"]))
        method = "certified" if request.form.get("method") == "certified" else "simple"
        stakes = request.form.get("stakes") if request.form.get("stakes") in ("courant", "important", "forme") else "courant"
        certified_ok = ci_esign.is_configured()
        err = None
        if not text:
            err = m._T("Aucun texte à signer : rattachez d'abord une analyse ou un brouillon à cette fiche.", "No text to sign: attach an analysis or a draft to this record first.")
        elif not allowed:
            err = m._ci_quota_message("signature", limit)
        elif not people:
            err = m._T("Indiquez au moins un signataire (un courriel valide par ligne).", "Enter at least one signatory (one valid email per line).")
        elif method == "certified" and not certified_ok:
            err = m._T("La signature certifiée n'est pas activée sur ce site.", "The certified signature is not enabled on this site.")
        elif stakes == "forme" and not request.form.get("ack_form"):
            err = m._T("Cet acte exige en principe un acte notarié. Cochez la case de reconnaissance si vous voulez quand même l'envoyer (version de travail).", "This act normally requires a notarial deed. Tick the acknowledgment box if you still want to send it (working copy).")
        elif stakes == "important" and method == "simple" and not request.form.get("ack_risk"):
            err = m._T("Enjeu important : cochez la case de reconnaissance pour utiliser la signature simple, ou choisissez la signature certifiée.", "High stakes: tick the acknowledgment box to use the simple signature, or choose the certified signature.")
        elif conn.execute("SELECT 1 FROM ci_signature_requests WHERE registry_id=? AND status='pending'", (rid,)).fetchone():
            err = m._T("Une demande de signature est déjà en cours pour ce contrat : annulez-la d'abord.", "A signature request is already in progress for this contract: cancel it first.")
        if err:
            conn.close()
            flash(err, "error")
            return redirect(back)
        text = text.strip()
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        message = (request.form.get("message") or "").strip()[:500]
        provider_ref = None
        if method == "certified":
            en = g.lang == "en"
            signer_dicts = [{"name": n, "email": e} for n, e in people]
            try:
                pdf = ci_pdf.contract_for_signature(entry["title"], text, g.lang, sha, signer_dicts)
                provider_ref = ci_esign.create_envelope(
                    entry["title"], pdf, signer_dicts,
                    ("Signature requested: " if en else "Signature demandée : ") + entry["title"],
                    ("%s asks you to sign this contract. Read it, then sign electronically.\n%s" if en else "%s vous demande de signer ce contrat. Lisez-le puis signez électroniquement.\n%s") % (u["full_name"], message),
                    F._site_link("/contract-intelligence/signature/webhook/docusign"))
            except ci_esign.ESignError as exc:
                m.app.logger.warning("Envoi DocuSign échoué (%s) %s", exc.code, exc.detail)
                conn.close()
                hint = {"consent_required": m._T("L'administrateur doit d'abord autoriser l'intégration chez DocuSign (consentement unique).", "The administrator must first grant consent to the integration at DocuSign (one time)."),
                        "auth": m._T("Identifiants DocuSign refusés.", "DocuSign credentials rejected."),
                        "unreachable": m._T("DocuSign est injoignable pour le moment.", "DocuSign is unreachable right now.")}.get(exc.code, "")
                flash(m._T("L'envoi à DocuSign a échoué (%s). %s Votre quota n'a pas été décompté." % (exc.code, hint), "Sending to DocuSign failed (%s). %s Your quota was not used." % (exc.code, hint)), "error")
                return redirect(back)
        now = _now()
        cur = conn.execute(
            "INSERT INTO ci_signature_requests (owner_id, registry_id, title, body_snapshot, body_sha256, source_label, lang, message, status, created_at, method, provider_ref, stakes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (u["id"], rid, entry["title"], text, sha, source, g.lang, message, "pending", now, method, provider_ref, stakes))
        sid = cur.lastrowid
        for name, email in people:
            conn.execute("INSERT INTO ci_signers (request_id, signer_name, signer_email, token, status, notified_at) VALUES (?,?,?,?,?,?)",
                         (sid, name, email, F._fresh_token(), "pending", now))
        conn.execute("UPDATE ci_registry SET status='signature', updated_at=? WHERE id=?", (now, rid))
        m._ci_record_usage(conn, u, "signature")
        import ci_team
        ci_team.log(conn, rid, u, "sig_requested", "(%s, %d)" % (method, len(people)))
        conn.commit()
        sent = len(people)
        if method == "simple":
            req = conn.execute("SELECT * FROM ci_signature_requests WHERE id=?", (sid,)).fetchone()
            sent = 0
            for s in conn.execute("SELECT * FROM ci_signers WHERE request_id=?", (sid,)).fetchall():
                subject, body = _invite_email(req, s, u["full_name"])
                sent += 1 if F._notify(s["signer_email"], subject, body) else 0
        conn.close()
        m.dbm.log_activity(u["id"], "ci_signature_created", "signature #%s (%s, %s)" % (sid, method, stakes))
        if method == "certified":
            flash(m._T("Envoyé à DocuSign : chaque signataire reçoit un courriel de DocuSign. Le contrat signé sera archivé ici automatiquement.",
                       "Sent to DocuSign: each signatory receives an email from DocuSign. The signed contract will be archived here automatically."), "success")
        else:
            flash(m._T("Demande de signature envoyée à %d signataire(s)." % sent, "Signature request sent to %d signatory(ies)." % sent) if sent == len(people) else
                  m._T("Demande créée, mais certains courriels n'ont pas pu partir. Transmettez les liens affichés sur la page de la demande.",
                       "Request created, but some emails could not be sent. Share the links shown on the request page."),
                  "success" if sent == len(people) else "error")
        return m._ci_redirect("signature", sid=sid)

    @m.ci_route("signature", "/signatures/<int:sid>", "/signatures/<int:sid>")
    def ci_signature(sid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        req = _owned_request(conn, u, sid)
        if req["method"] == "certified" and req["status"] == "pending":
            refresh_certified(conn, req)
            req = conn.execute("SELECT * FROM ci_signature_requests WHERE id=?", (sid,)).fetchone()
        signers = conn.execute("SELECT * FROM ci_signers WHERE request_id=? ORDER BY id", (sid,)).fetchall()
        entry = conn.execute("SELECT id, file_name FROM ci_registry WHERE id=?", (req["registry_id"],)).fetchone()
        conn.close()
        links = {s["id"]: F._site_link("/contract-intelligence/signature/" + s["token"]) for s in signers}
        return m._ci_page("signature", **m._ci_ctx("signatures", req=req, signers=signers, links=links, entry=entry,
                                                    status_labels=SIG_STATUS[g.lang], signer_labels=SIGNER_STATUS[g.lang]))

    @m.ci_route("signature_remind", "/signatures/<int:sid>/relancer", "/signatures/<int:sid>/remind", ("POST",))
    def ci_signature_remind(sid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        req = _owned_request(conn, u, sid)
        sent = 0
        if req["status"] == "pending" and req["method"] == "certified":
            try:
                ci_esign.resend(req["provider_ref"], [{"name": x["signer_name"], "email": x["signer_email"]} for x in conn.execute("SELECT * FROM ci_signers WHERE request_id=? ORDER BY id", (sid,)).fetchall()])
                sent = conn.execute("SELECT COUNT(*) AS c FROM ci_signers WHERE request_id=? AND status='pending'", (sid,)).fetchone()["c"]
            except ci_esign.ESignError as exc:
                m.app.logger.warning("Relance DocuSign échouée (%s) %s", exc.code, exc.detail)
        elif req["status"] == "pending":
            for s in conn.execute("SELECT * FROM ci_signers WHERE request_id=? AND status='pending'", (sid,)).fetchall():
                conn.execute("UPDATE ci_signers SET notified_at=? WHERE id=?", (_now(), s["id"]))
                subject, body = _invite_email(req, s, u["full_name"])
                sent += 1 if F._notify(s["signer_email"], "Rappel : " + subject if req["lang"] == "fr" else "Reminder: " + subject, body) else 0
            conn.commit()
        conn.close()
        flash(m._T("Rappel envoyé à %d signataire(s)." % sent, "Reminder sent to %d signatory(ies)." % sent) if sent else
              m._T("Aucun rappel envoyé (courriel non configuré, ou rien en attente).", "No reminder sent (email not configured, or nothing pending)."),
              "success" if sent else "error")
        return m._ci_redirect("signature", sid=sid)

    @m.ci_route("signature_cancel", "/signatures/<int:sid>/annuler", "/signatures/<int:sid>/cancel", ("POST",))
    def ci_signature_cancel(sid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        req = _owned_request(conn, u, sid)
        if req["status"] == "pending":
            if req["method"] == "certified" and req["provider_ref"]:
                try:
                    ci_esign.void(req["provider_ref"], "Annulée par l'expéditeur")
                except ci_esign.ESignError as exc:
                    m.app.logger.warning("Annulation DocuSign échouée (%s) %s", exc.code, exc.detail)
                    conn.close()
                    flash(m._T("Impossible d'annuler chez DocuSign pour le moment (%s). Réessayez." % exc.code, "Could not cancel at DocuSign right now (%s). Try again." % exc.code), "error")
                    return m._ci_redirect("signature", sid=sid)
            conn.execute("UPDATE ci_signature_requests SET status='cancelled' WHERE id=?", (sid,))
            conn.commit()
            flash(m._T("Demande annulée : les liens ne fonctionnent plus.", "Request cancelled: the links no longer work."), "success")
        conn.close()
        return m._ci_redirect("signature", sid=sid)

    @m.ci_route("signature_refresh", "/signatures/<int:sid>/actualiser", "/signatures/<int:sid>/refresh", ("POST",))
    def ci_signature_refresh(sid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        req = _owned_request(conn, u, sid)
        if req["status"] == "completed" and req["method"] == "certified":
            # finalisation interrompue (PDF non archivé) : on retente le téléchargement
            entry = conn.execute("SELECT file_name FROM ci_registry WHERE id=?", (req["registry_id"],)).fetchone()
            if not entry or entry["file_name"] != _archive_name(req):
                try:
                    _archive_certified(conn, req)
                    flash(m._T("PDF signé archivé dans le registre.", "Signed PDF archived in the registry."), "success")
                except Exception as exc:  # noqa: BLE001
                    m.app.logger.warning("Archivage PDF certifié échoué (%s)", exc)
                    flash(m._T("Le PDF n'a pas pu être récupéré pour le moment.", "The PDF could not be retrieved right now."), "error")
        else:
            refresh_certified(conn, req)
            flash(m._T("Statut mis à jour.", "Status updated."), "success")
        conn.close()
        return m._ci_redirect("signature", sid=sid)

    @app.route("/contract-intelligence/signature/webhook/docusign", methods=["POST"], endpoint="ci_docusign_webhook")
    def ci_docusign_webhook():
        raw = request.get_data()
        headers = [request.headers.get("X-DocuSign-Signature-%d" % i) for i in range(1, 6)]
        if not ci_esign.webhook_enabled() or not ci_esign.verify_hmac(raw, [h for h in headers if h]):
            abort(404)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            return "ok", 200
        env_id = ((payload.get("data") or {}).get("envelopeId") or payload.get("envelopeId") or "") if isinstance(payload, dict) else ""
        if env_id:
            conn = m.dbm.get_db()
            req = conn.execute("SELECT * FROM ci_signature_requests WHERE provider_ref=?", (env_id,)).fetchone()
            if req:
                refresh_certified(conn, req)
            conn.close()
        return "ok", 200

    m.LANG_COUNTERPART["ci_legal"] = "ci_legal_en"
    m.LANG_COUNTERPART["ci_legal_en"] = "ci_legal"

    @app.route("/contract-intelligence/valeur-juridique", endpoint="ci_legal")
    @app.route("/en/contract-intelligence/legal-value", endpoint="ci_legal_en")
    def ci_legal_page():
        en = request.path.startswith("/en/")
        g.lang = "en" if en else "fr"
        return render_template("contract/legal_value.html", lang=g.lang, ci_active="", level_labels={}, certified_available=ci_esign.is_configured(),
                               sources=LEGAL_SOURCES)

    @m.ci_route("signature_cert", "/signatures/<int:sid>/certificat", "/signatures/<int:sid>/certificate")
    def ci_signature_cert(sid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        req = _owned_request(conn, u, sid)
        if req["status"] != "completed" or req["method"] != "certified":
            conn.close()
            abort(404)
        name = "certificat-realisation-%d.pdf" % sid if req["lang"] == "fr" else "completion-certificate-%d.pdf" % sid
        if req["cert_path"] and os.path.exists(req["cert_path"]):
            conn.close()
            return ci_crypto.send(req["cert_path"], name, "application/pdf")
        try:
            data = ci_esign.download(req["provider_ref"], "certificate")
        except ci_esign.ESignError:
            conn.close()
            abort(503)
        conn.close()
        return Response(data, mimetype="application/pdf", headers={"Content-Disposition": 'attachment; filename="%s"' % name})

    @m.ci_route("signature_pdf", "/signatures/<int:sid>/pdf", "/signatures/<int:sid>/pdf")
    def ci_signature_pdf(sid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        req = _owned_request(conn, u, sid)
        resp = _signed_pdf_response(conn, req)
        conn.close()
        return resp

    @app.route("/contract-intelligence/signature/<token>", methods=["GET", "POST"], endpoint="ci_signature_public")
    def ci_signature_public(token):
        conn = m.dbm.get_db()
        signer = conn.execute("SELECT * FROM ci_signers WHERE token=?", (token,)).fetchone()
        if not signer:
            conn.close()
            abort(404)
        req = conn.execute("SELECT * FROM ci_signature_requests WHERE id=?", (signer["request_id"],)).fetchone()
        owner = conn.execute("SELECT full_name, email FROM users WHERE id=?", (req["owner_id"],)).fetchone()
        lang = req["lang"]
        g.lang = lang
        en = lang == "en"
        certified = req["method"] == "certified"
        can_sign = signer["status"] == "pending" and req["status"] == "pending" and _link_valid(signer) and not certified
        expired = signer["status"] == "pending" and req["status"] == "pending" and not _link_valid(signer) and not certified
        if request.method == "POST" and can_sign:
            action = request.form.get("action")
            if action == "decline":
                reason = (request.form.get("reason") or "").strip()[:500]
                if not reason:
                    flash(m._T("Un motif est requis pour refuser.", "A reason is required to decline."), "error")
                else:
                    conn.execute("UPDATE ci_signers SET status='declined', decline_reason=?, signed_at=? WHERE id=?", (reason, _now(), signer["id"]))
                    conn.execute("UPDATE ci_signature_requests SET status='declined' WHERE id=? AND status='pending'", (req["id"],))
                    _log_sig(conn, req, "sig_declined")
                    conn.commit()
                    F._notify(owner["email"], ("Signature declined: " if en else "Signature refusée : ") + req["title"],
                              ("%s declined to sign \"%s\".\nReason: %s\n" if en else "%s a refusé de signer « %s ».\nMotif : %s\n")
                              % (signer["signer_name"] or signer["signer_email"], req["title"], reason)
                              + "\n" + F._site_link("/contract-intelligence/signatures/%d" % req["id"]))
                    conn.close()
                    return redirect(url_for("ci_signature_public", token=token))
            elif action == "sign":
                typed = (request.form.get("full_legal_name") or "").strip()[:120]
                if len(typed) < 3 or not request.form.get("consent_read") or not request.form.get("consent_binding"):
                    flash(m._T("Le nom légal complet et les deux cases de consentement sont requis.", "Your full legal name and both consent boxes are required."), "error")
                else:
                    conn.execute("UPDATE ci_signers SET status='signed', signed_name=?, ip_address=?, user_agent=?, signed_at=? WHERE id=? AND status='pending'",
                                 (typed, request.remote_addr, request.headers.get("User-Agent", "")[:300], _now(), signer["id"]))
                    conn.commit()
                    pending = conn.execute("SELECT COUNT(*) AS c FROM ci_signers WHERE request_id=? AND status<>'signed'", (req["id"],)).fetchone()["c"]
                    if pending == 0:
                        _complete(conn, req)
                    else:
                        F._notify(owner["email"], ("Signature received: " if en else "Signature reçue : ") + req["title"],
                                  ("%s signed \"%s\" (%d still pending).\n" if en else "%s a signé « %s » (%d en attente).\n") % (typed, req["title"], pending)
                                  + "\n" + F._site_link("/contract-intelligence/signatures/%d" % req["id"]))
                    conn.close()
                    return redirect(url_for("ci_signature_public", token=token))
        signers = conn.execute("SELECT * FROM ci_signers WHERE request_id=? ORDER BY id", (req["id"],)).fetchall()
        conn.close()
        return render_template("contract/signature_public.html", req=req, signer=signer, signers=signers, owner=owner, lang=lang,
                               can_sign=can_sign, expired=expired, certified=certified, consent=CONSENT[lang], status_labels=SIG_STATUS[lang],
                               signer_labels=SIGNER_STATUS[lang], ci_active="", level_labels={}, link_days=SIGNATURE_LINK_DAYS)

    @app.route("/contract-intelligence/signature/<token>/pdf", endpoint="ci_signature_public_pdf")
    def ci_signature_public_pdf(token):
        conn = m.dbm.get_db()
        signer = conn.execute("SELECT * FROM ci_signers WHERE token=?", (token,)).fetchone()
        if not signer:
            conn.close()
            abort(404)
        req = conn.execute("SELECT * FROM ci_signature_requests WHERE id=?", (signer["request_id"],)).fetchone()
        resp = _signed_pdf_response(conn, req)
        conn.close()
        return resp


def _signed_pdf_response(conn, req):
    if req["status"] != "completed":
        conn.close()
        abort(404)
    entry = conn.execute("SELECT file_path, file_name FROM ci_registry WHERE id=?", (req["registry_id"],)).fetchone()
    name = _archive_name(req)
    if entry and entry["file_path"] and os.path.exists(entry["file_path"]) and (entry["file_name"] or "") == name:
        return ci_crypto.send(entry["file_path"], name, "application/pdf")
    if req["method"] == "certified":
        try:
            data = ci_esign.download(req["provider_ref"], "combined")
        except ci_esign.ESignError:
            conn.close()
            abort(503)
        return Response(data, mimetype="application/pdf", headers={"Content-Disposition": 'attachment; filename="%s"' % name})
    signers = [{"name": s["signer_name"], "email": s["signer_email"], "signed_name": s["signed_name"], "signed_at": s["signed_at"], "ip": s["ip_address"]}
               for s in conn.execute("SELECT * FROM ci_signers WHERE request_id=? ORDER BY id", (req["id"],)).fetchall()]
    data = ci_pdf.signed_contract(req["title"], req["body_snapshot"], req["lang"], req["body_sha256"], signers, "SIG-%d" % req["id"], req["completed_at"], _verify_url(req))
    return Response(data, mimetype="application/pdf", headers={"Content-Disposition": 'attachment; filename="%s"' % name})


# ---------------------------------------------------------------------------
# Vérification publique d'un document signé
# ---------------------------------------------------------------------------

_verify_hits = {}
VERIFY_MAX = 30          # vérifications
VERIFY_WINDOW = 600      # par fenêtre de 10 minutes et par adresse IP
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _throttled(ip):
    now = time.time()
    hits = [t for t in _verify_hits.get(ip, []) if now - t < VERIFY_WINDOW]
    if len(hits) >= VERIFY_MAX:
        _verify_hits[ip] = hits
        return True
    hits.append(now)
    _verify_hits[ip] = hits
    if len(_verify_hits) > 5000:   # borne mémoire
        for k in [k for k, v in _verify_hits.items() if not v or now - v[-1] > VERIFY_WINDOW]:
            _verify_hits.pop(k, None)
    return False


def _mask_email(addr):
    local, _, domain = (addr or "").partition("@")
    return (local[:1] + "***@" + domain) if domain else "***"


def normalize_hash(raw):
    h = re.sub(r"\s+", "", (raw or "")).lower()
    if h.startswith("sha256:"):
        h = h[7:]
    return h if HEX64.match(h) else None


def verify_digest(conn, digest):
    """Retrouve une signature terminée à partir d'une empreinte (PDF final, certificat ou texte)."""
    req = conn.execute("SELECT * FROM ci_signature_requests WHERE status='completed' AND (final_sha256=? OR cert_sha256=? OR body_sha256=?) ORDER BY id LIMIT 1",
                       (digest, digest, digest)).fetchone()
    if not req:
        return None
    kind = "file" if req["final_sha256"] == digest else ("certificate" if req["cert_sha256"] == digest else "text")
    signers = [{"name": x["signed_name"] or x["signer_name"] or "", "email": _mask_email(x["signer_email"]), "signed_at": (x["signed_at"] or "")[:16].replace("T", " ")}
               for x in conn.execute("SELECT * FROM ci_signers WHERE request_id=? AND status='signed' ORDER BY id", (req["id"],)).fetchall()]
    return {"kind": kind, "title": req["title"], "method": req["method"], "ref": "SIG-%d" % req["id"], "completed_at": (req["completed_at"] or "")[:16].replace("T", " "),
            "signers": signers, "body_sha256": req["body_sha256"], "final_sha256": req["final_sha256"]}


def _register_verify():
    app = m.app

    m.LANG_COUNTERPART["ci_verify"] = "ci_verify_en"
    m.LANG_COUNTERPART["ci_verify_en"] = "ci_verify"

    @app.route("/contract-intelligence/verifier", methods=["GET", "POST"], endpoint="ci_verify")
    @app.route("/en/contract-intelligence/verify", methods=["GET", "POST"], endpoint="ci_verify_en")
    def ci_verify():
        en = request.path.startswith("/en/")
        g.lang = "en" if en else "fr"
        result, digest, error, checked, source = None, None, None, False, None
        if request.method == "POST" or request.args.get("t") or request.args.get("h"):
            if _throttled(request.remote_addr or "?"):
                error = m._T("Trop de vérifications depuis cette adresse. Réessayez dans quelques minutes.", "Too many checks from this address. Try again in a few minutes.")
            else:
                upload = request.files.get("file") if request.method == "POST" else None
                if upload and upload.filename:
                    digest = hashlib.sha256(upload.read()).hexdigest()
                    source = upload.filename[:120]
                else:
                    raw = (request.form.get("hash") if request.method == "POST" else (request.args.get("t") or request.args.get("h"))) or ""
                    digest = normalize_hash(raw)
                    if not digest:
                        error = m._T("Déposez un fichier PDF, ou collez une empreinte SHA-256 de 64 caractères (0-9, a-f).", "Drop a PDF file, or paste a 64-character SHA-256 fingerprint (0-9, a-f).")
                if digest:
                    conn = m.dbm.get_db()
                    result = verify_digest(conn, digest)
                    conn.close()
                    checked = True
        return render_template("contract/verify.html", lang=g.lang, ci_active="", level_labels={}, result=result, digest=digest, error=error, checked=checked,
                               source=source, legal_url=url_for("ci_legal_en" if en else "ci_legal"))
