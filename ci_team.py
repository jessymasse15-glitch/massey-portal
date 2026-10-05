"""Contract Intelligence : travail en équipe (partage de dossier, commentaires),
journal d'activité, rendu du contrat (Word/PDF/texte) et page « Vos données »."""
import io
import json
import os
import re
import zipfile
from datetime import datetime

from flask import abort, flash, g, redirect, render_template, request, Response, url_for
from werkzeug.utils import secure_filename

import ci_crypto
import ci_pdf

m = None   # module app
F = None   # ci_features

MAX_SHARES = 10
ROLES = ("viewer", "editor")

ACTIONS = {
    "created": ("Fiche créée", "Record created"),
    "edited": ("Fiche modifiée", "Record edited"),
    "file": ("Fichier archivé", "File archived"),
    "archived": ("Fiche archivée", "Record archived"),
    "unarchived": ("Fiche désarchivée", "Record unarchived"),
    "shared": ("Partagée avec", "Shared with"),
    "unshared": ("Partage retiré pour", "Sharing removed for"),
    "comment": ("A commenté", "Commented"),
    "sig_requested": ("Signature demandée", "Signature requested"),
    "sig_completed": ("Signature terminée", "Signature completed"),
    "sig_declined": ("Signature refusée", "Signature declined"),
    "id_captured": ("Pièce d'identité et selfie reçus", "ID document and selfie received"),
    "id_approved": ("Identité confirmée", "Identity confirmed"),
    "id_rejected": ("Identité rejetée", "Identity rejected"),
    "id_viewed": ("Pièce d'identité consultée", "ID document viewed"),
    "rendered": ("Document généré", "Document generated"),
    "viewed": ("A consulté la fiche", "Viewed the record"),
    "approval_started": ("Approbation lancée auprès de", "Approval started with"),
    "approval_approved": ("A approuvé :", "Approved:"),
    "approval_rejected": ("A refusé :", "Rejected:"),
    "negotiation_started": ("Négociation ouverte avec", "Negotiation opened with"),
    "negotiation_agreed": ("Accord conclu", "Agreement reached"),
    "signoff": ("Validation interne", "Internal sign-off"),
}
ROLE_LABELS = {"viewer": ("Lecture et commentaires", "Read and comment"), "editor": ("Modification", "Edit"), "reader": ("Lecture seule", "Read only")}
TEAM_ROLE_LABELS = {"admin": ("Administrateur", "Administrator"), "juriste": ("Juriste", "Lawyer"), "approbateur": ("Approbateur", "Approver"), "lecteur": ("Lecteur", "Reader")}


def init(app_module, features_module):
    global m, F
    m, F = app_module, features_module
    _register()


# ---------------------------------------------------------------------------
# Accès et journal
# ---------------------------------------------------------------------------

def _email(u):
    try:
        return (u["email"] or "").strip().lower()
    except (KeyError, IndexError):
        return ""


RANK = {"reader": 0, "viewer": 1, "editor": 2, "owner": 3}
NEED = {"read": 0, "comment": 1, "editor": 2, "owner": 3}
TEAM_ENTRY_ROLE = {"admin": "editor", "juriste": "editor", "approbateur": "viewer", "lecteur": "reader"}


def team_role_for(conn, u, row):
    """Rôle d'équipe de l'utilisateur sur la fiche (admin | juriste | approbateur | lecteur) ou None."""
    try:
        tid = row["team_id"]
    except (KeyError, IndexError):
        tid = None
    if not tid:
        return None
    r = conn.execute("SELECT role FROM ci_team_members WHERE team_id=? AND lower(email)=?", (tid, _email(u))).fetchone()
    return r["role"] if r else None


def role_for(conn, u, row):
    """owner | editor | viewer (lecture + commentaires) | reader (lecture seule) | None — le plus élevé des accès directs et d'équipe."""
    if row["user_id"] == u["id"]:
        return "owner"
    cands = []
    sh = conn.execute("SELECT role FROM ci_shares WHERE registry_id=? AND lower(email)=?", (row["id"], _email(u))).fetchone()
    if sh:
        cands.append(sh["role"])
    tr = team_role_for(conn, u, row)
    if tr:
        cands.append(TEAM_ENTRY_ROLE.get(tr, "reader"))
    return max(cands, key=lambda r: RANK[r]) if cands else None


def reg_access(conn, u, rid, need="read"):
    """(fiche, rôle). 404 si aucun accès ; 403 si le rôle est insuffisant. need : read | comment | editor | owner."""
    row = conn.execute("SELECT * FROM ci_registry WHERE id=?", (rid,)).fetchone()
    role = role_for(conn, u, row) if row else None
    if not role:
        conn.close()
        abort(404)
    if RANK[role] < NEED[need]:
        conn.close()
        abort(403)
    return row, role


def actor_label(u):
    try:
        return (u["full_name"] or u["email"] or "?")[:80]
    except (KeyError, IndexError):
        return "?"


def log(conn, rid, actor, action, detail=""):
    """Ajoute une ligne au journal (ne lève jamais : le journal ne doit pas bloquer l'action)."""
    try:
        conn.execute("INSERT INTO ci_activity (registry_id, actor_id, actor_label, action, detail, created_at) VALUES (?,?,?,?,?,?)",
                     (rid, actor["id"] if actor else None, actor_label(actor) if actor else (m._T("Système", "System") if m else "System"),
                      action, (detail or "")[:200], m.dbm.now()))
    except Exception:  # noqa: BLE001
        pass


def activity_rows(conn, rid, limit=40):
    en = g.lang == "en"
    out = []
    for r in conn.execute("SELECT * FROM ci_activity WHERE registry_id=? ORDER BY id DESC LIMIT ?", (rid, limit)).fetchall():
        lab = ACTIONS.get(r["action"], (r["action"], r["action"]))[1 if en else 0]
        out.append({"who": r["actor_label"], "what": lab + ((" " + r["detail"]) if r["detail"] else ""), "when": (r["created_at"] or "")[:16].replace("T", " ")})
    return out


def shared_with_me(conn, u):
    """Fiches des autres auxquelles j'ai accès : partages directs et fiches d'équipe (avec « my_role » et « via »)."""
    out, seen = [], set()
    for r in conn.execute(
            "SELECT r.*, s.role AS my_role, o.full_name AS owner_name, o.email AS owner_email FROM ci_shares s "
            "JOIN ci_registry r ON r.id=s.registry_id JOIN users o ON o.id=r.user_id "
            "WHERE lower(s.email)=? AND r.archived=0 ORDER BY r.updated_at DESC", (_email(u),)).fetchall():
        d = dict(r)
        d["via"] = ""
        out.append(d)
        seen.add(r["id"])
    for r in conn.execute(
            "SELECT r.*, tm.role AS team_role, t.name AS team_name, o.full_name AS owner_name, o.email AS owner_email FROM ci_registry r "
            "JOIN ci_team_members tm ON tm.team_id=r.team_id JOIN ci_teams t ON t.id=r.team_id JOIN users o ON o.id=r.user_id "
            "WHERE lower(tm.email)=? AND r.user_id<>? AND r.archived=0 ORDER BY r.updated_at DESC", (_email(u), u["id"])).fetchall():
        if r["id"] in seen:
            continue
        d = dict(r)
        d["my_role"] = TEAM_ENTRY_ROLE.get(r["team_role"], "reader")
        d["via"] = r["team_name"]
        out.append(d)
    return out


def _participants(conn, row):
    """Courriels du propriétaire et des personnes avec qui la fiche est partagée."""
    owner = conn.execute("SELECT email, full_name FROM users WHERE id=?", (row["user_id"],)).fetchone()
    out = [owner["email"]] if owner else []
    out += [s["email"] for s in conn.execute("SELECT email FROM ci_shares WHERE registry_id=?", (row["id"],)).fetchall()]
    return out


def entry_context(conn, u, row, role):
    """Données d'équipe pour la fiche : partages, commentaires, journal."""
    shares = conn.execute("SELECT * FROM ci_shares WHERE registry_id=? ORDER BY id", (row["id"],)).fetchall() if role == "owner" else []
    comments = conn.execute("SELECT * FROM ci_comments WHERE registry_id=? ORDER BY id DESC LIMIT 100", (row["id"],)).fetchall()
    owner = conn.execute("SELECT full_name, email FROM users WHERE id=?", (row["user_id"],)).fetchone()
    en = g.lang == "en"
    team = conn.execute("SELECT * FROM ci_teams WHERE id=?", (row["team_id"],)).fetchone() if row["team_id"] else None
    trole = team_role_for(conn, u, row) if team else None
    my_teams = []
    if role == "owner":
        my_teams = conn.execute("SELECT t.* FROM ci_teams t JOIN ci_team_members tm ON tm.team_id=t.id WHERE lower(tm.email)=? AND tm.role IN ('admin','juriste') ORDER BY t.name", (_email(u),)).fetchall()
    signoffs = conn.execute("SELECT * FROM ci_team_signoffs WHERE registry_id=? ORDER BY id DESC LIMIT 20", (row["id"],)).fetchall() if team else []
    return {"role": role, "shares": shares, "comments": comments, "activity": activity_rows(conn, row["id"]),
            "team": team, "team_role": trole, "team_role_label": TEAM_ROLE_LABELS[trole][1 if en else 0] if trole else "", "my_teams": my_teams, "signoffs": signoffs,
            "can_signoff": bool(team and trole in ("admin", "juriste", "approbateur")), "can_comment": role != "reader",
            "owner_name": (owner["full_name"] or owner["email"]) if owner else "", "role_labels": {k: v[1 if g.lang == "en" else 0] for k, v in ROLE_LABELS.items()}}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _register():
    @m.ci_route("registry_share", "/registre/<int:rid>/partager", "/registry/<int:rid>/share", ("POST",))
    def ci_registry_share(rid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        row, _role = reg_access(conn, u, rid, "owner")
        email = (request.form.get("email") or "").strip().lower()[:160]
        role = request.form.get("role") if request.form.get("role") in ROLES else "viewer"
        if not F._email_ok(email):
            flash(m._T("Adresse courriel invalide.", "Invalid email address."), "error")
        elif email == _email(u):
            flash(m._T("Ce dossier est déjà le vôtre.", "This record is already yours."), "error")
        elif conn.execute("SELECT COUNT(*) AS c FROM ci_shares WHERE registry_id=?", (rid,)).fetchone()["c"] >= MAX_SHARES and \
                not conn.execute("SELECT 1 FROM ci_shares WHERE registry_id=? AND lower(email)=?", (rid, email)).fetchone():
            flash(m._T("Maximum %d personnes par dossier." % MAX_SHARES, "Maximum %d people per record." % MAX_SHARES), "error")
        else:
            existing = conn.execute("SELECT id FROM ci_shares WHERE registry_id=? AND lower(email)=?", (rid, email)).fetchone()
            if existing:
                conn.execute("UPDATE ci_shares SET role=? WHERE id=?", (role, existing["id"]))
            else:
                conn.execute("INSERT INTO ci_shares (registry_id, owner_id, email, role, created_at) VALUES (?,?,?,?,?)", (rid, u["id"], email, role, m.dbm.now()))
            log(conn, rid, u, "shared", "%s (%s)" % (email, ROLE_LABELS[role][1 if g.lang == "en" else 0]))
            conn.commit()
            en = g.lang == "en"
            link = F._site_link(m.ci_url("registry_entry", rid=rid))
            who = actor_label(u)
            if en:
                subj = "%s shared a contract with you: %s" % (who, row["title"])
                body = "Hello,\n\n%s shared the contract record \"%s\" with you (%s).\n\nOpen it (sign in with this email address):\n%s\n\n— Massey Contracts & Tax" % (who, row["title"], ROLE_LABELS[role][1], link)
            else:
                subj = "%s a partagé un contrat avec vous : %s" % (who, row["title"])
                body = "Bonjour,\n\n%s a partagé avec vous la fiche contrat « %s » (%s).\n\nOuvrez-la (connectez-vous avec cette adresse courriel) :\n%s\n\n— Massey Contracts & Tax" % (who, row["title"], ROLE_LABELS[role][0], link)
            sent = F._notify(email, subj, body)
            flash(m._T("Dossier partagé avec %s." % email, "Record shared with %s." % email) + ("" if sent else " " + m._T("(Le courriel d'invitation n'a pas pu être envoyé : prévenez la personne.)", "(The invitation email could not be sent: let the person know.)")), "success")
        conn.close()
        return redirect(m.ci_url("registry_entry", rid=rid) + "#equipe")

    @m.ci_route("registry_unshare", "/registre/<int:rid>/partager/<int:shid>/retirer", "/registry/<int:rid>/share/<int:shid>/remove", ("POST",))
    def ci_registry_unshare(rid, shid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        reg_access(conn, u, rid, "owner")
        sh = conn.execute("SELECT * FROM ci_shares WHERE id=? AND registry_id=?", (shid, rid)).fetchone()
        if sh:
            conn.execute("DELETE FROM ci_shares WHERE id=?", (shid,))
            log(conn, rid, u, "unshared", sh["email"])
            conn.commit()
        conn.close()
        return redirect(m.ci_url("registry_entry", rid=rid) + "#equipe")

    @m.ci_route("registry_comment", "/registre/<int:rid>/commentaire", "/registry/<int:rid>/comment", ("POST",))
    def ci_registry_comment(rid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        row, _role = reg_access(conn, u, rid, "comment")
        body = (request.form.get("body") or "").strip()[:1000]
        if not body:
            flash(m._T("Écrivez un commentaire.", "Write a comment."), "error")
        else:
            conn.execute("INSERT INTO ci_comments (registry_id, author_id, author_name, body, created_at) VALUES (?,?,?,?,?)", (rid, u["id"], actor_label(u), body, m.dbm.now()))
            log(conn, rid, u, "comment")
            conn.commit()
            link = F._site_link(m.ci_url("registry_entry", rid=rid))
            for to in _participants(conn, row):
                if to.lower() != _email(u):
                    F._notify(to, m._T("Nouveau commentaire : %s" % row["title"], "New comment: %s" % row["title"]),
                              m._T("%s a écrit sur « %s » :\n\n%s\n\nRépondre : %s\n\n— Massey Contracts & Tax" % (actor_label(u), row["title"], body, link),
                                   "%s wrote on \"%s\":\n\n%s\n\nReply: %s\n\n— Massey Contracts & Tax" % (actor_label(u), row["title"], body, link)))
        conn.close()
        return redirect(m.ci_url("registry_entry", rid=rid) + "#equipe")

    @m.ci_route("registry_render", "/registre/<int:rid>/document/<fmt>", "/registry/<int:rid>/document/<fmt>")
    def ci_registry_render(rid, fmt):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        if fmt not in ("docx", "pdf", "txt"):
            abort(404)
        conn = m.dbm.get_db()
        row, role = reg_access(conn, u, rid, "read")
        owner = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        import ci_extras
        text, label = ci_extras.signable_text(conn, owner, row)
        if not text:
            conn.close()
            flash(m._T("Aucun texte de contrat n'est rattaché à cette fiche.", "No contract text is attached to this record."), "error")
            return redirect(m.ci_url("registry_entry", rid=rid))
        lang = "en" if g.lang == "en" else "fr"
        parties = [x for x in (row["counterparty"], ) if x]
        sub = (row["counterparty"] and (m._T("Contrepartie : ", "Counterparty: ") + row["counterparty"]) or "") or None
        stamp = m._T("Document de travail", "Working copy") + " · " + datetime.utcnow().strftime("%Y-%m-%d")
        subtitle = (sub + " · " if sub else "") + stamp
        base = secure_filename(row["title"]) or "contrat"
        log(conn, rid, u, "rendered", fmt.upper())
        conn.commit()
        conn.close()
        note = m._T("Massey Contracts & Tax — document de travail, à faire relire avant signature", "Massey Contracts & Tax — working copy, have it reviewed before signing")
        if fmt == "pdf":
            return Response(ci_pdf.contract_document(row["title"], text, lang, subtitle=subtitle, parties=parties or None), mimetype="application/pdf",
                            headers={"Content-Disposition": 'attachment; filename="%s.pdf"' % base})
        if fmt == "docx":
            data = m.contract_engine.build_docx(row["title"], text, note, subtitle=subtitle, parties=parties or None, lang=lang)
            if data is None:
                flash(m._T("L'export Word n'est pas disponible sur ce serveur.", "Word export is not available on this server."), "error")
                return redirect(m.ci_url("registry_entry", rid=rid))
            return Response(data, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            headers={"Content-Disposition": 'attachment; filename="%s.docx"' % base})
        return Response(row["title"].upper() + "\n\n" + text + "\n", mimetype="text/plain; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="%s.txt"' % base})

    # ---- Confiance : vos données ------------------------------------------
    @m.ci_route("trust", "/vos-donnees", "/your-data")
    def ci_trust():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        import ci_ai
        conn = m.dbm.get_db()
        counts = _counts(conn, u)
        conn.close()
        return m._ci_page("trust", **m._ci_ctx("home", counts=counts, ai_on=ci_ai.is_configured(), key_env=(__import__("ci_crypto").key_source() == "env")))

    @m.ci_route("data_export", "/vos-donnees/export", "/your-data/export")
    def ci_data_export():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        data = _export(conn, u)
        conn.close()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("contract-intelligence.json", json.dumps(data, ensure_ascii=False, indent=2, default=str))
            z.writestr("LISEZMOI.txt", "Export de vos données Contract Intelligence (Massey Contracts & Tax), généré le %s UTC.\n"
                       "Les fichiers téléversés au registre ne sont pas inclus ici : téléchargez-les depuis chaque fiche.\n" % datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
        return Response(buf.getvalue(), mimetype="application/zip", headers={"Content-Disposition": 'attachment; filename="contract-intelligence-export.zip"'})

    @m.ci_route("data_delete", "/vos-donnees/supprimer", "/your-data/delete", ("POST",))
    def ci_data_delete():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        if (request.form.get("confirm") or "").strip().upper() != ("DELETE" if g.lang == "en" else "SUPPRIMER"):
            flash(m._T("Tapez SUPPRIMER pour confirmer.", "Type DELETE to confirm."), "error")
            return redirect(m.ci_url("trust"))
        conn = m.dbm.get_db()
        n = _delete_all(conn, u)
        conn.commit()
        conn.close()
        flash(m._T("Vos données Contract Intelligence ont été supprimées (%d éléments)." % n, "Your Contract Intelligence data was deleted (%d items)." % n), "success")
        return redirect(m.ci_url("home"))


def _counts(conn, u):
    q = lambda sql: conn.execute(sql, (u["id"],)).fetchone()[0]  # noqa: E731
    return {"analyses": q("SELECT COUNT(*) FROM contract_analyses WHERE user_id=?"), "drafts": q("SELECT COUNT(*) FROM contract_drafts WHERE user_id=?"),
            "registry": q("SELECT COUNT(*) FROM ci_registry WHERE user_id=?"), "obligations": q("SELECT COUNT(*) FROM contract_obligations WHERE user_id=?"),
            "signatures": q("SELECT COUNT(*) FROM ci_signature_requests WHERE owner_id=?"), "completed": q("SELECT COUNT(*) FROM ci_signature_requests WHERE owner_id=? AND status='completed'"),
            "negotiations": q("SELECT COUNT(*) FROM ci_negotiations WHERE owner_id=?"), "approvals": q("SELECT COUNT(*) FROM ci_approvals WHERE owner_id=?"),
            "playbooks": q("SELECT COUNT(*) FROM ci_playbooks WHERE user_id=?")}


_HIDE = {"file_path", "cert_path", "token", "password_hash", "id_doc_path", "id_selfie_path", "id_challenge", "id_capture_ip"}


def _rows(rows):
    return [{k: r[k] for k in r.keys() if k not in _HIDE} for r in rows]


def _export(conn, u):
    uid = u["id"]
    sel = lambda sql, *a: _rows(conn.execute(sql, a or (uid,)).fetchall())  # noqa: E731
    reg = sel("SELECT * FROM ci_registry WHERE user_id=?")
    ids = [r["id"] for r in reg]
    ph = ",".join("?" * len(ids)) or "NULL"
    return {"exported_at": datetime.utcnow().isoformat() + "Z", "account": {"email": u["email"], "name": u["full_name"]},
            "analyses": sel("SELECT * FROM contract_analyses WHERE user_id=?"), "drafts": sel("SELECT * FROM contract_drafts WHERE user_id=?"),
            "obligations": sel("SELECT * FROM contract_obligations WHERE user_id=?"), "registry": reg,
            "signature_requests": sel("SELECT * FROM ci_signature_requests WHERE owner_id=?"),
            "signers": _rows(conn.execute("SELECT s.* FROM ci_signers s JOIN ci_signature_requests r ON r.id=s.request_id WHERE r.owner_id=?", (uid,)).fetchall()),
            "negotiations": sel("SELECT * FROM ci_negotiations WHERE owner_id=?"), "approvals": sel("SELECT * FROM ci_approvals WHERE owner_id=?"),
            "playbooks": sel("SELECT * FROM ci_playbooks WHERE user_id=?"),
            "shares": _rows(conn.execute("SELECT * FROM ci_shares WHERE registry_id IN (%s)" % ph, ids).fetchall()),
            "comments": _rows(conn.execute("SELECT * FROM ci_comments WHERE registry_id IN (%s)" % ph, ids).fetchall()),
            "activity": _rows(conn.execute("SELECT * FROM ci_activity WHERE registry_id IN (%s)" % ph, ids).fetchall())}


def _delete_all(conn, u):
    uid = u["id"]
    n = 0
    regs = conn.execute("SELECT id, file_path FROM ci_registry WHERE user_id=?", (uid,)).fetchall()
    for r in regs:
        if r["file_path"] and os.path.exists(r["file_path"]):
            ci_crypto.erase(r["file_path"])
    try:
        import ci_idcheck
        ci_idcheck.purge_images(conn, [r["id"] for r in conn.execute(
            "SELECT s.id FROM ci_signers s JOIN ci_signature_requests q ON q.id=s.request_id WHERE q.owner_id=?", (uid,)).fetchall()])
    except Exception:  # noqa: BLE001
        pass
    sigs = conn.execute("SELECT cert_path FROM ci_signature_requests WHERE owner_id=?", (uid,)).fetchall()
    for s in sigs:
        if s["cert_path"] and os.path.exists(s["cert_path"]):
            ci_crypto.erase(s["cert_path"])
    for r in regs:
        conn.execute("DELETE FROM ci_activity WHERE registry_id=?", (r["id"],))
    for sql in ("DELETE FROM ci_registry WHERE user_id=?", "DELETE FROM ci_signature_requests WHERE owner_id=?", "DELETE FROM ci_negotiations WHERE owner_id=?",
                "DELETE FROM ci_approvals WHERE owner_id=?", "DELETE FROM ci_playbooks WHERE user_id=?", "DELETE FROM contract_obligations WHERE user_id=?",
                "DELETE FROM contract_drafts WHERE user_id=?", "DELETE FROM contract_analyses WHERE user_id=?", "DELETE FROM ci_calendar_tokens WHERE user_id=?"):
        n += conn.execute(sql, (uid,)).rowcount
    return n


def home_activity(conn, u, limit=8):
    """Dernières actions sur mes dossiers et sur ceux partagés avec moi (hors mes propres actions)."""
    en = g.lang == "en"
    ids = [r["id"] for r in conn.execute("SELECT id FROM ci_registry WHERE user_id=?", (u["id"],)).fetchall()]
    ids += [r["registry_id"] for r in conn.execute("SELECT registry_id FROM ci_shares WHERE lower(email)=?", (_email(u),)).fetchall()]
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    out = []
    for r in conn.execute("SELECT a.*, r.title FROM ci_activity a JOIN ci_registry r ON r.id=a.registry_id WHERE a.registry_id IN (%s) "
                          "AND (a.actor_id IS NULL OR a.actor_id<>?) AND a.action NOT IN ('rendered','edited','viewed') ORDER BY a.id DESC LIMIT ?" % ph, ids + [u["id"], limit]).fetchall():
        lab = ACTIONS.get(r["action"], (r["action"], r["action"]))[1 if en else 0]
        out.append({"who": r["actor_label"], "what": lab, "title": r["title"], "when": (r["created_at"] or "")[:16].replace("T", " "),
                    "url": m.ci_url("registry_entry", rid=r["registry_id"])})
    return out


def log_ref(conn, ref_type, ref_id, actor, action, detail=""):
    """Journalise sur toutes les fiches du registre liées à une analyse (ref_type='analysis') ou à un brouillon."""
    if ref_type not in ("analysis", "draft") or not ref_id:
        return
    col = "analysis_id" if ref_type == "analysis" else "draft_id"
    for r in conn.execute("SELECT id FROM ci_registry WHERE %s=?" % col, (ref_id,)).fetchall():
        log(conn, r["id"], actor, action, detail)


def log_view(conn, rid, u, window_minutes=30):
    """Une consultation par personne et par fenêtre de 30 minutes (évite de noyer le journal)."""
    try:
        last = conn.execute("SELECT created_at FROM ci_activity WHERE registry_id=? AND actor_id=? AND action='viewed' ORDER BY id DESC LIMIT 1", (rid, u["id"])).fetchone()
        if last:
            prev = datetime.fromisoformat(last["created_at"].replace("Z", "")[:19])
            if (datetime.utcnow() - prev).total_seconds() < window_minutes * 60:
                return
        log(conn, rid, u, "viewed")
        conn.commit()
    except Exception:  # noqa: BLE001
        pass
