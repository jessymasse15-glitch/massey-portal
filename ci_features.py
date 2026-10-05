"""Fonctions avancées de Contract Intelligence : rappels, approbations, négociation,
playbooks, registre/archivage et rédaction par IA.

Chargé depuis app.py par init(app_module) ; réutilise les aides de app.py (routage
FR/EN, quotas, accès) sans import circulaire.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta

from flask import abort, flash, jsonify, redirect, render_template, request, send_file, url_for, g
from werkzeug.utils import secure_filename

import ci_crypto
import ci_reminders
import notifications

m = None  # module app, renseigné par init()


def init(app_module):
    global m
    m = app_module
    _register_reminders()
    _register_approvals()
    _register_negotiations()
    _register_playbooks()
    _register_registry()
    _register_ai_draft()
    _register_party_obligations()
    _register_dashboard()
    import ci_extras
    ci_extras.init(m, sys.modules[__name__])
    import ci_team
    ci_team.init(m, sys.modules[__name__])
    import ci_review
    ci_review.init(m, sys.modules[__name__])
    import ci_trust
    ci_trust.init(m, sys.modules[__name__])
    import ci_teams
    ci_teams.init(m, sys.modules[__name__])
    import ci_tools
    ci_tools.init(m, sys.modules[__name__])
    import ci_mobile
    ci_mobile.init(m, sys.modules[__name__])
    import site_search
    site_search.init(m)
    import ci_idcheck
    ci_idcheck.init(m, sys.modules[__name__], sys.modules["ci_extras"])
    _start_reminder_thread()


# ---------------------------------------------------------------------------
# Aides communes
# ---------------------------------------------------------------------------

def _site_link(path):
    return ci_reminders.site_url() + path


def _fresh_token():
    return secrets.token_urlsafe(24)


def _email_ok(addr):
    return bool(re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,200}\.[^@\s]{2,}", addr or ""))


def _monthly_gate(conn, u, kind):
    """(limite, utilisé, autorisé) pour un type d'usage mensuel."""
    return m._ci_quota(conn, u, kind)


# ---------------------------------------------------------------------------
# Rappels par courriel
# ---------------------------------------------------------------------------

def _register_reminders():
    app = m.app

    @m.ci_route("reminder_prefs", "/rappels/preferences", "/reminders/preferences", ("POST",))
    def ci_reminder_prefs():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        enabled = 1 if request.form.get("enabled") else 0
        offsets = [o for o in request.form.getlist("offsets") if o.isdigit() and int(o) in ci_reminders.ALLOWED_OFFSETS]
        offsets_s = ",".join(str(x) for x in sorted({int(o) for o in offsets}, reverse=True)) or ",".join(map(str, ci_reminders.DEFAULT_OFFSETS))
        conn = m.dbm.get_db()
        conn.execute(
            "INSERT INTO ci_reminder_prefs (user_id, enabled, offsets, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, offsets=excluded.offsets, updated_at=excluded.updated_at",
            (u["id"], enabled, offsets_s, m.dbm.now()))
        conn.commit()
        conn.close()
        flash(m._T("Préférences de rappel enregistrées.", "Reminder preferences saved."), "success")
        return m._ci_redirect("deadlines")

    @m.ci_route("reminder_test", "/rappels/test", "/reminders/test", ("POST",))
    def ci_reminder_test():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        if not notifications.is_configured():
            flash(m._T("L'envoi de courriels n'est pas encore activé sur ce site : aucun rappel ne peut partir.",
                       "Email sending is not enabled on this site yet: no reminder can be sent."), "error")
            return m._ci_redirect("deadlines")
        conn = m.dbm.get_db()
        today = ci_reminders.local_today()
        horizon = (today + timedelta(days=30)).isoformat()
        rows = conn.execute(
            "SELECT * FROM contract_obligations WHERE user_id=? AND status='a_faire' AND due_date IS NOT NULL AND due_date<>'' AND due_date<=? ORDER BY due_date",
            (u["id"], horizon)).fetchall()
        conn.close()
        items = []
        for r in rows:
            try:
                d = datetime.strptime(r["due_date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            items.append({"id": r["id"], "label": r["label"], "contract": r["contract_label"], "due": r["due_date"],
                          "days_left": (d - today).days, "kind": "test"})
        if not items:
            flash(m._T("Aucune échéance dans les 30 prochains jours (ou en retard) : rien à envoyer en test.",
                       "No deadline within 30 days (or overdue): nothing to send as a test."), "error")
            return m._ci_redirect("deadlines")
        subject, body = ci_reminders.build_email(u["full_name"], g.lang, items, m.app.config["SECRET_KEY"], u["id"], test=True)
        if notifications.send_email(u["email"], subject, body):
            flash(m._T("Courriel de test envoyé à %s." % u["email"], "Test email sent to %s." % u["email"]), "success")
        else:
            flash(m._T("L'envoi a échoué. Vérifiez la configuration du courriel.", "Sending failed. Check the email configuration."), "error")
        return m._ci_redirect("deadlines")

    @app.route("/contract-intelligence/rappels/desabonnement", endpoint="ci_unsubscribe")
    def ci_unsubscribe():
        uid = ci_reminders.read_unsubscribe_token(m.app.config["SECRET_KEY"], request.args.get("t", ""))
        ok = False
        if uid:
            conn = m.dbm.get_db()
            if conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                conn.execute(
                    "INSERT INTO ci_reminder_prefs (user_id, enabled, offsets, updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET enabled=0, updated_at=excluded.updated_at",
                    (uid, 0, ",".join(map(str, ci_reminders.DEFAULT_OFFSETS)), m.dbm.now()))
                conn.commit()
                ok = True
            conn.close()
        return render_template("contract/unsubscribed.html", ok=ok, ci_active="deadlines", level_labels={})

    @app.route("/contract-intelligence/rappels/executer", methods=["GET", "POST"], endpoint="ci_reminders_cron")
    def ci_reminders_cron():
        expected = os.environ.get("CRON_TOKEN", "")
        given = request.values.get("token", "")
        if not expected or not hmac.compare_digest(expected.encode(), given.encode()):
            abort(404)
        summary = ci_reminders.run(m.app.config["SECRET_KEY"])
        try:
            import ci_mobile
            summary["mobile"] = ci_mobile.run_messages()
        except Exception as exc:  # noqa: BLE001
            summary["mobile"] = "error: %s" % str(exc)[:80]
        try:
            import ci_idcheck
            summary["id_images_purged"] = ci_idcheck.purge_due()
        except Exception as exc:  # noqa: BLE001
            summary["id_images_purged"] = "error: %s" % str(exc)[:80]
        try:
            import ci_extras
            summary["signatures_polled"] = ci_extras.poll_certified()
        except Exception as exc:  # noqa: BLE001
            summary["signatures_polled"] = "error: %s" % str(exc)[:80]
        return jsonify(summary)


def _start_reminder_thread():
    if os.environ.get("CI_REMINDER_THREAD", "1") == "0":
        return
    secret_getter = lambda: m.app.config["SECRET_KEY"]

    def loop():
        time.sleep(90)
        while True:
            try:
                if 7 <= ci_reminders.local_hour() <= 20:
                    ci_reminders.run(secret_getter())
                    import ci_mobile
                    ci_mobile.run_messages()
                import ci_idcheck
                ci_idcheck.purge_due()
                import ci_extras
                ci_extras.poll_certified()
            except Exception as exc:  # noqa: BLE001 — la tâche de fond ne doit jamais s'arrêter
                try:
                    m.dbm.log_activity(None, "ci_reminder_loop_error", str(exc)[:200])
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(3600)

    threading.Thread(target=loop, name="ci-reminders", daemon=True).start()


# Les sections suivantes sont ajoutées plus bas dans ce fichier.

# ---------------------------------------------------------------------------
# Texte d'un document source (analyse ou brouillon) et courriels
# ---------------------------------------------------------------------------

def _ref_text(conn, u, ref_type, ref_id):
    """(titre, texte, langue) d'une analyse ou d'un brouillon appartenant à l'utilisateur."""
    if ref_type == "analysis":
        row = conn.execute("SELECT title, text_content AS t, language FROM contract_analyses WHERE id=? AND user_id=?", (ref_id, u["id"])).fetchone()
    elif ref_type == "draft":
        row = conn.execute("SELECT title, body_text AS t, language FROM contract_drafts WHERE id=? AND user_id=?", (ref_id, u["id"])).fetchone()
    else:
        row = None
    if not row:
        conn.close()
        abort(404)
    return row["title"], row["t"], row["language"]


def _notify(to, subject, body):
    try:
        return notifications.send_email(to, subject, body)
    except Exception:  # noqa: BLE001
        return False


def _parse_people(raw, limit):
    """Lignes « Nom <courriel> » ou « courriel » -> [(nom, courriel)]."""
    out = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        mt = re.match(r"^(.*?)\s*<([^>]+)>$", line)
        name, email = (mt.group(1).strip(), mt.group(2).strip()) if mt else ("", line)
        if _email_ok(email) and email.lower() not in [e.lower() for _, e in out]:
            out.append((name[:80], email))
    return out[:limit]


# ---------------------------------------------------------------------------
# Approbations internes (circuit séquentiel par lien courriel)
# ---------------------------------------------------------------------------

APPROVAL_STATUS = {
    "fr": {"pending": "En cours", "approved": "Approuvé", "rejected": "Refusé", "cancelled": "Annulé"},
    "en": {"pending": "Pending", "approved": "Approved", "rejected": "Rejected", "cancelled": "Cancelled"},
}
STEP_STATUS = {
    "fr": {"waiting": "En attente de son tour", "pending": "À décider", "approved": "Approuvé", "rejected": "Refusé", "skipped": "Non requis"},
    "en": {"waiting": "Waiting for its turn", "pending": "To decide", "approved": "Approved", "rejected": "Rejected", "skipped": "Not required"},
}


def _approval_email(approval, step, owner_name):
    en = approval["lang"] == "en"
    link = _site_link("/contract-intelligence/approbation/" + step["token"])
    note = ("\n\nMessage: " if en else "\n\nMessage : ") + approval["message"] if approval["message"] else ""
    if en:
        return ("Approval requested: %s" % approval["title"],
                "Hello %s,\n\n%s asks for your approval of the contract \"%s\".%s\n\nReview and decide here (no account needed):\n%s\n\n"
                "Anyone with this link can view the contract and decide on your behalf: do not forward it.\n— Massey Contracts & Tax"
                % (step["approver_name"] or "", owner_name, approval["title"], note, link))
    return ("Approbation demandée : %s" % approval["title"],
            "Bonjour %s,\n\n%s vous demande d'approuver le contrat « %s ».%s\n\nConsultez-le et décidez ici (aucun compte requis) :\n%s\n\n"
            "Toute personne qui possède ce lien peut consulter le contrat et décider à votre place : ne le transférez pas.\n— Massey Contracts & Tax"
            % (step["approver_name"] or "", owner_name, approval["title"], note, link))


def _activate_step(conn, approval, step, owner_name):
    conn.execute("UPDATE ci_approval_steps SET status='pending', notified_at=? WHERE id=?", (m.dbm.now(), step["id"]))
    conn.commit()
    subject, body = _approval_email(approval, step, owner_name)
    return _notify(step["approver_email"], subject, body)


def _register_approvals():
    app = m.app

    @m.ci_route("approvals", "/approbations", "/approvals")
    def ci_approvals():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        rows = conn.execute("SELECT * FROM ci_approvals WHERE owner_id=? ORDER BY id DESC LIMIT 100", (u["id"],)).fetchall()
        limit, used, allowed = _monthly_gate(conn, u, "approval")
        conn.close()
        return m._ci_page("approvals", **m._ci_ctx("approvals", approvals=rows, status_labels=APPROVAL_STATUS[g.lang],
                                                   limit=limit, used=used, allowed=allowed))

    @m.ci_route("approval_new", "/approbations/nouvelle", "/approvals/new", ("POST",))
    def ci_approval_new():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        ref_type = request.form.get("ref_type", "")
        try:
            ref_id = int(request.form.get("ref_id", "0"))
        except ValueError:
            abort(400)
        conn = m.dbm.get_db()
        title, text, _lang = _ref_text(conn, u, ref_type, ref_id)
        limit, used, allowed = _monthly_gate(conn, u, "approval")
        back = m.ci_url("analysis", aid=ref_id) if ref_type == "analysis" else m.ci_url("draft", did=ref_id)
        if not allowed:
            conn.close()
            flash(m._ci_quota_message("approval", limit), "error")
            return redirect(back)
        people = _parse_people(request.form.get("approvers", ""), 6)
        if not people:
            conn.close()
            flash(m._T("Indiquez au moins un approbateur (un courriel valide par ligne).", "Enter at least one approver (one valid email per line)."), "error")
            return redirect(back)
        cur = conn.execute(
            "INSERT INTO ci_approvals (owner_id, ref_type, ref_id, title, body_snapshot, lang, message, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (u["id"], ref_type, ref_id, title, text, g.lang, (request.form.get("message") or "").strip()[:500], "pending", m.dbm.now()))
        aid = cur.lastrowid
        for i, (name, email) in enumerate(people, start=1):
            conn.execute("INSERT INTO ci_approval_steps (approval_id, step_order, approver_name, approver_email, token, status) VALUES (?,?,?,?,?,?)",
                         (aid, i, name, email, _fresh_token(), "waiting"))
        m._ci_record_usage(conn, u, "approval")
        conn.commit()
        approval = conn.execute("SELECT * FROM ci_approvals WHERE id=?", (aid,)).fetchone()
        first = conn.execute("SELECT * FROM ci_approval_steps WHERE approval_id=? ORDER BY step_order LIMIT 1", (aid,)).fetchone()
        sent = _activate_step(conn, approval, first, u["full_name"])
        import ci_team
        ci_team.log_ref(conn, ref_type, ref_id, u, "approval_started", ", ".join(e for _n, e in people))
        conn.commit()
        conn.close()
        m.dbm.log_activity(u["id"], "ci_approval_created", "approbation #%s" % aid)
        flash(m._T("Circuit d'approbation lancé. Le premier approbateur a été prévenu par courriel.",
                   "Approval flow started. The first approver was notified by email.") if sent else
              m._T("Circuit lancé, mais le courriel n'a pas pu partir (courriel non configuré ?). Transmettez-lui le lien affiché sur la page de l'approbation.",
                   "Flow started, but the email could not be sent (email not configured?). Share the link shown on the approval page."),
              "success" if sent else "error")
        return m._ci_redirect("approval", aid=aid)

    def _owned(conn, u, aid):
        approval = conn.execute("SELECT * FROM ci_approvals WHERE id=? AND owner_id=?", (aid, u["id"])).fetchone()
        if not approval:
            conn.close()
            abort(404)
        return approval

    @m.ci_route("approval", "/approbations/<int:aid>", "/approvals/<int:aid>")
    def ci_approval(aid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        approval = _owned(conn, u, aid)
        steps = conn.execute("SELECT * FROM ci_approval_steps WHERE approval_id=? ORDER BY step_order", (aid,)).fetchall()
        conn.close()
        links = {s["id"]: _site_link("/contract-intelligence/approbation/" + s["token"]) for s in steps}
        return m._ci_page("approval", **m._ci_ctx("approvals", approval=approval, steps=steps, links=links,
                                                  status_labels=APPROVAL_STATUS[g.lang], step_labels=STEP_STATUS[g.lang]))

    @m.ci_route("approval_cancel", "/approbations/<int:aid>/annuler", "/approvals/<int:aid>/cancel", ("POST",))
    def ci_approval_cancel(aid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        approval = _owned(conn, u, aid)
        if approval["status"] == "pending":
            conn.execute("UPDATE ci_approvals SET status='cancelled', decided_at=? WHERE id=?", (m.dbm.now(), aid))
            conn.execute("UPDATE ci_approval_steps SET status='skipped' WHERE approval_id=? AND status IN ('waiting','pending')", (aid,))
            conn.commit()
            flash(m._T("Circuit d'approbation annulé : les liens ne fonctionnent plus.", "Approval flow cancelled: the links no longer work."), "success")
        conn.close()
        return m._ci_redirect("approval", aid=aid)

    @m.ci_route("approval_remind", "/approbations/<int:aid>/relancer", "/approvals/<int:aid>/remind", ("POST",))
    def ci_approval_remind(aid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        approval = _owned(conn, u, aid)
        step = conn.execute("SELECT * FROM ci_approval_steps WHERE approval_id=? AND status='pending'", (aid,)).fetchone()
        if approval["status"] == "pending" and step:
            subject, body = _approval_email(approval, step, u["full_name"])
            ok = _notify(step["approver_email"], "Rappel — " + subject, body)
            flash(m._T("Relance envoyée.", "Reminder sent.") if ok else m._T("La relance n'a pas pu partir.", "The reminder could not be sent."),
                  "success" if ok else "error")
        conn.close()
        return m._ci_redirect("approval", aid=aid)

    # Page publique de l'approbateur (jeton unique, sans compte)
    @app.route("/contract-intelligence/approbation/<token>", methods=["GET", "POST"], endpoint="ci_approval_public")
    def ci_approval_public(token):
        conn = m.dbm.get_db()
        step = conn.execute("SELECT * FROM ci_approval_steps WHERE token=?", (token,)).fetchone()
        if not step:
            conn.close()
            abort(404)
        approval = conn.execute("SELECT * FROM ci_approvals WHERE id=?", (step["approval_id"],)).fetchone()
        owner = conn.execute("SELECT full_name, email FROM users WHERE id=?", (approval["owner_id"],)).fetchone()
        lang = approval["lang"]
        g.lang = lang
        can_decide = step["status"] == "pending" and approval["status"] == "pending"
        if request.method == "POST" and can_decide:
            decision = request.form.get("decision")
            comment = (request.form.get("comment") or "").strip()[:1000]
            if decision not in ("approved", "rejected"):
                conn.close()
                abort(400)
            if decision == "rejected" and not comment:
                flash(m._T("Un motif est requis pour refuser.", "A reason is required to reject."), "error")
            else:
                conn.execute("UPDATE ci_approval_steps SET status=?, comment=?, decided_at=? WHERE id=?", (decision, comment, m.dbm.now(), step["id"]))
                import ci_team
                ci_team.log_ref(conn, approval["ref_type"], approval["ref_id"], None, "approval_" + decision, step["approver_name"] or step["approver_email"])
                en = lang == "en"
                if decision == "rejected":
                    conn.execute("UPDATE ci_approvals SET status='rejected', decided_at=? WHERE id=?", (m.dbm.now(), approval["id"]))
                    conn.execute("UPDATE ci_approval_steps SET status='skipped' WHERE approval_id=? AND status IN ('waiting','pending')", (approval["id"],))
                    conn.commit()
                    who = step["approver_name"] or step["approver_email"]
                    _notify(owner["email"], ("Approval rejected: " if en else "Approbation refusée : ") + approval["title"],
                            ("%s rejected the contract \"%s\".\nReason: %s\n" if en else "%s a refusé le contrat « %s ».\nMotif : %s\n") % (who, approval["title"], comment)
                            + "\n" + _site_link("/contract-intelligence/approbations/%d" % approval["id"]))
                else:
                    nxt = conn.execute("SELECT * FROM ci_approval_steps WHERE approval_id=? AND step_order>? ORDER BY step_order LIMIT 1",
                                       (approval["id"], step["step_order"])).fetchone()
                    conn.commit()
                    if nxt:
                        _activate_step(conn, approval, nxt, owner["full_name"])
                    else:
                        conn.execute("UPDATE ci_approvals SET status='approved', decided_at=? WHERE id=?", (m.dbm.now(), approval["id"]))
                        conn.commit()
                        _notify(owner["email"], ("Contract approved: " if en else "Contrat approuvé : ") + approval["title"],
                                ("All approvers approved \"%s\"." if en else "Tous les approbateurs ont approuvé « %s ».") % approval["title"]
                                + "\n" + _site_link("/contract-intelligence/approbations/%d" % approval["id"]))
                conn.close()
                return redirect(url_for("ci_approval_public", token=token))
        steps = conn.execute("SELECT * FROM ci_approval_steps WHERE approval_id=? ORDER BY step_order", (approval["id"],)).fetchall()
        conn.close()
        return render_template("contract/approval_public.html", approval=approval, step=step, steps=steps, owner=owner,
                               can_decide=can_decide, lang=lang, status_labels=APPROVAL_STATUS[lang], step_labels=STEP_STATUS[lang],
                               ci_active="", level_labels={})

# ---------------------------------------------------------------------------
# Négociation en ligne (versions, redline, acceptation)
# ---------------------------------------------------------------------------

NEG_STATUS = {
    "fr": {"open": "En négociation", "agreed": "Accord conclu", "closed": "Clôturée"},
    "en": {"open": "Under negotiation", "agreed": "Agreed", "closed": "Closed"},
}
MAX_VERSIONS = 40


def _neg_versions(conn, nid):
    return conn.execute("SELECT * FROM ci_neg_versions WHERE negotiation_id=? ORDER BY version_no", (nid,)).fetchall()


def _neg_view_ctx(conn, neg, side):
    versions = _neg_versions(conn, neg["id"])
    latest = versions[-1]
    prev = versions[-2] if len(versions) > 1 else None
    diff = m.contract_engine.compare_texts(prev["body_text"], latest["body_text"]) if prev else None
    can_act = neg["status"] == "open"
    can_accept = can_act and latest["author"] != side
    return dict(neg=neg, versions=versions, latest=latest, prev=prev, diff=diff, side=side,
                can_act=can_act, can_accept=can_accept, status_labels=NEG_STATUS[neg["lang"] if side == "counterparty" else g.lang])


def _other_side_notice(neg, owner, side, event, versions_no=None):
    """Courriel à l'autre partie après une nouvelle version / acceptation."""
    en = neg["lang"] == "en"
    if side == "owner":
        to = neg["counterparty_email"]
        link = _site_link("/contract-intelligence/negociation/c/" + neg["counterparty_token"])
        who = owner["full_name"]
    else:
        to = owner["email"]
        link = _site_link("/contract-intelligence/negociations/%d" % neg["id"])
        who = neg["counterparty_name"] or neg["counterparty_email"]
    if event == "version":
        subj = ("New version proposed: " if en else "Nouvelle version proposée : ") + neg["title"]
        body = ("%s proposed version %s of \"%s\".\nReview the changes and reply:\n%s\n" if en else
                "%s a proposé la version %s de « %s ».\nConsultez les modifications et répondez :\n%s\n") % (who, versions_no, neg["title"], link)
    else:
        subj = ("Agreement reached: " if en else "Accord conclu : ") + neg["title"]
        body = ("%s accepted the latest version of \"%s\". Negotiation is complete.\n%s\n" if en else
                "%s a accepté la dernière version de « %s ». La négociation est terminée.\n%s\n") % (who, neg["title"], link)
    return _notify(to, subj, body + ("\n— Massey Contracts & Tax"))


def _register_negotiations():
    app = m.app

    def _owned(conn, u, nid):
        neg = conn.execute("SELECT * FROM ci_negotiations WHERE id=? AND owner_id=?", (nid, u["id"])).fetchone()
        if not neg:
            conn.close()
            abort(404)
        return neg

    @m.ci_route("negotiations", "/negociations", "/negotiations")
    def ci_negotiations():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        rows = conn.execute(
            "SELECT n.*, (SELECT COUNT(*) FROM ci_neg_versions v WHERE v.negotiation_id=n.id) AS versions FROM ci_negotiations n WHERE owner_id=? ORDER BY updated_at DESC LIMIT 100",
            (u["id"],)).fetchall()
        limit, used, allowed = _monthly_gate(conn, u, "negotiation")
        conn.close()
        return m._ci_page("negotiations", **m._ci_ctx("negotiations", negotiations=rows, status_labels=NEG_STATUS[g.lang],
                                                      limit=limit, used=used, allowed=allowed))

    @m.ci_route("negotiation_new", "/negociations/nouvelle", "/negotiations/new", ("POST",))
    def ci_negotiation_new():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        ref_type = request.form.get("ref_type", "")
        try:
            ref_id = int(request.form.get("ref_id", "0"))
        except ValueError:
            abort(400)
        conn = m.dbm.get_db()
        title, text, _l = _ref_text(conn, u, ref_type, ref_id)
        back = m.ci_url("analysis", aid=ref_id) if ref_type == "analysis" else m.ci_url("draft", did=ref_id)
        limit, used, allowed = _monthly_gate(conn, u, "negotiation")
        if not allowed:
            conn.close()
            flash(m._ci_quota_message("negotiation", limit), "error")
            return redirect(back)
        email = (request.form.get("counterparty_email") or "").strip()
        if not _email_ok(email):
            conn.close()
            flash(m._T("Courriel de la contrepartie invalide.", "Invalid counterparty email."), "error")
            return redirect(back)
        now = m.dbm.now()
        cur = conn.execute(
            "INSERT INTO ci_negotiations (owner_id, title, counterparty_name, counterparty_email, counterparty_token, lang, status, created_at, updated_at, ref_type, ref_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (u["id"], title, (request.form.get("counterparty_name") or "").strip()[:80], email, _fresh_token(), g.lang, "open", now, now, ref_type, ref_id))
        nid = cur.lastrowid
        conn.execute("INSERT INTO ci_neg_versions (negotiation_id, version_no, author, body_text, note, created_at) VALUES (?,?,?,?,?,?)",
                     (nid, 1, "owner", text, (request.form.get("note") or "").strip()[:500], now))
        m._ci_record_usage(conn, u, "negotiation")
        import ci_team
        ci_team.log_ref(conn, ref_type, ref_id, u, "negotiation_started", email)
        conn.commit()
        neg = conn.execute("SELECT * FROM ci_negotiations WHERE id=?", (nid,)).fetchone()
        conn.close()
        sent = _other_side_notice(neg, u, "owner", "version", 1)
        m.dbm.log_activity(u["id"], "ci_negotiation_created", "négociation #%s" % nid)
        flash(m._T("Négociation ouverte. La contrepartie a été invitée par courriel.", "Negotiation opened. The counterparty was invited by email.") if sent else
              m._T("Négociation ouverte, mais le courriel n'a pas pu partir. Transmettez le lien affiché sur la page.", "Negotiation opened, but the email could not be sent. Share the link shown on the page."),
              "success" if sent else "error")
        return m._ci_redirect("negotiation", nid=nid)

    @m.ci_route("negotiation", "/negociations/<int:nid>", "/negotiations/<int:nid>")
    def ci_negotiation(nid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        neg = _owned(conn, u, nid)
        ctx = _neg_view_ctx(conn, neg, "owner")
        conn.close()
        ctx["share_link"] = _site_link("/contract-intelligence/negociation/c/" + neg["counterparty_token"])
        return m._ci_page("negotiation", **m._ci_ctx("negotiations", **ctx))

    def _do_propose(conn, neg, side, owner):
        body = (request.form.get("body_text") or "").strip()[:m.contract_engine.MAX_CHARS]
        note = (request.form.get("note") or "").strip()[:500]
        versions = _neg_versions(conn, neg["id"])
        if neg["status"] != "open" or not body or len(versions) >= MAX_VERSIONS:
            return None
        if body == versions[-1]["body_text"].strip():
            return "same"
        no = versions[-1]["version_no"] + 1
        conn.execute("INSERT INTO ci_neg_versions (negotiation_id, version_no, author, body_text, note, created_at) VALUES (?,?,?,?,?,?)",
                     (neg["id"], no, side, body, note, m.dbm.now()))
        conn.execute("UPDATE ci_negotiations SET updated_at=?, accepted_by=NULL WHERE id=?", (m.dbm.now(), neg["id"]))
        conn.commit()
        _other_side_notice(neg, owner, side, "version", no)
        return "ok"

    def _do_accept(conn, neg, side, owner):
        versions = _neg_versions(conn, neg["id"])
        if neg["status"] != "open" or versions[-1]["author"] == side:
            return False
        conn.execute("UPDATE ci_negotiations SET status='agreed', accepted_by=?, updated_at=? WHERE id=?", (side, m.dbm.now(), neg["id"]))
        try:
            import ci_team
            ci_team.log_ref(conn, neg["ref_type"], neg["ref_id"], owner if side == "owner" else None, "negotiation_agreed", "" if side == "owner" else neg["counterparty_email"])
        except Exception:  # noqa: BLE001
            pass
        conn.commit()
        _other_side_notice(neg, owner, side, "accept")
        return True

    @m.ci_route("negotiation_propose", "/negociations/<int:nid>/proposer", "/negotiations/<int:nid>/propose", ("POST",))
    def ci_negotiation_propose(nid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        neg = _owned(conn, u, nid)
        res = _do_propose(conn, neg, "owner", u)
        conn.close()
        flash({"ok": m._T("Nouvelle version envoyée à la contrepartie.", "New version sent to the counterparty."),
               "same": m._T("Le texte est identique à la version actuelle.", "The text is identical to the current version.")}.get(res, m._T("Impossible de proposer une version.", "Cannot propose a version.")),
              "success" if res == "ok" else "error")
        return m._ci_redirect("negotiation", nid=nid)

    @m.ci_route("negotiation_accept", "/negociations/<int:nid>/accepter", "/negotiations/<int:nid>/accept", ("POST",))
    def ci_negotiation_accept(nid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        neg = _owned(conn, u, nid)
        ok = _do_accept(conn, neg, "owner", u)
        conn.close()
        flash(m._T("Accord enregistré.", "Agreement recorded.") if ok else m._T("Vous ne pouvez pas accepter votre propre version.", "You cannot accept your own version."),
              "success" if ok else "error")
        return m._ci_redirect("negotiation", nid=nid)

    @m.ci_route("negotiation_close", "/negociations/<int:nid>/cloturer", "/negotiations/<int:nid>/close", ("POST",))
    def ci_negotiation_close(nid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        neg = _owned(conn, u, nid)
        if neg["status"] == "open":
            conn.execute("UPDATE ci_negotiations SET status='closed', updated_at=? WHERE id=?", (m.dbm.now(), nid))
            conn.commit()
        conn.close()
        return m._ci_redirect("negotiation", nid=nid)

    @m.ci_route("negotiation_download", "/negociations/<int:nid>/telecharger/<fmt>", "/negotiations/<int:nid>/download/<fmt>")
    def ci_negotiation_download(nid, fmt):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        neg = _owned(conn, u, nid)
        latest = _neg_versions(conn, nid)[-1]
        conn.close()
        base = secure_filename(neg["title"]) or "contract"
        if fmt == "docx":
            data = m.contract_engine.build_docx(neg["title"], latest["body_text"], "Version %d" % latest["version_no"])
            if data is None:
                abort(404)
            return m.Response(data, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                              headers={"Content-Disposition": 'attachment; filename="%s-v%d.docx"' % (base, latest["version_no"])})
        if fmt == "txt":
            return m.Response(latest["body_text"], mimetype="text/plain; charset=utf-8",
                              headers={"Content-Disposition": 'attachment; filename="%s-v%d.txt"' % (base, latest["version_no"])})
        abort(404)

    # Page publique de la contrepartie (jeton unique, sans compte)
    @app.route("/contract-intelligence/negociation/c/<token>", methods=["GET", "POST"], endpoint="ci_negotiation_public")
    def ci_negotiation_public(token):
        conn = m.dbm.get_db()
        neg = conn.execute("SELECT * FROM ci_negotiations WHERE counterparty_token=?", (token,)).fetchone()
        if not neg:
            conn.close()
            abort(404)
        g.lang = neg["lang"]
        owner = conn.execute("SELECT full_name, email FROM users WHERE id=?", (neg["owner_id"],)).fetchone()
        if request.method == "POST":
            action = request.form.get("action")
            if action == "propose":
                res = _do_propose(conn, neg, "counterparty", owner)
                flash(m._T("Nouvelle version envoyée.", "New version sent.") if res == "ok" else
                      (m._T("Le texte est identique à la version actuelle.", "The text is identical to the current version.") if res == "same" else
                       m._T("Impossible de proposer une version.", "Cannot propose a version.")), "success" if res == "ok" else "error")
            elif action == "accept":
                ok = _do_accept(conn, neg, "counterparty", owner)
                flash(m._T("Accord enregistré. Merci.", "Agreement recorded. Thank you.") if ok else m._T("Action impossible.", "Action not possible."), "success" if ok else "error")
            conn.close()
            return redirect(url_for("ci_negotiation_public", token=token))
        ctx = _neg_view_ctx(conn, neg, "counterparty")
        conn.close()
        return render_template("contract/negotiation.html", owner=owner, ci_active="", level_labels={}, lang=neg["lang"], public=True, **ctx)

# ---------------------------------------------------------------------------
# Playbooks (positions de négociation de l'utilisateur)
# ---------------------------------------------------------------------------

def _topic_choices(conn):
    custom = []
    labels = {}
    for r in conn.execute("SELECT id, topic FROM ci_custom_rules WHERE active=1 ORDER BY id").fetchall():
        tid = "custom_%s" % r["id"]
        custom.append({"id": tid, "fr": (r["topic"],)})
        labels[tid] = r["topic"]
    return m.contract_engine.topic_catalog(custom), labels


def load_playbook(conn, user_id, pid, email=None):
    pb = conn.execute("SELECT * FROM ci_playbooks WHERE id=? AND user_id=?", (pid, user_id)).fetchone()
    if not pb and email:   # playbook partagé par une équipe dont je suis membre
        pb = conn.execute("SELECT p.* FROM ci_playbooks p JOIN ci_team_members tm ON tm.team_id=p.team_id WHERE p.id=? AND lower(tm.email)=?", (pid, email.lower())).fetchone()
    if not pb:
        return None
    pos = conn.execute("SELECT topic_id, stance, note FROM ci_playbook_positions WHERE playbook_id=? ORDER BY id", (pid,)).fetchall()
    return {"id": pb["id"], "name": pb["name"], "max_payment_days": pb["max_payment_days"], "min_notice_days": pb["min_notice_days"],
            "positions": [dict(x) for x in pos]}


def evaluate_playbook(conn, pb, result, text):
    _, labels = _topic_choices(conn)
    obligations = m.contract_engine.extract_obligations(text, result.get("language"))
    return m.contract_engine.apply_playbook(result, obligations, pb, labels)


def apply_default_playbook(conn, user_id, result, text):
    """Applique le playbook par défaut de l'utilisateur à une analyse fraîchement calculée."""
    row = conn.execute("SELECT id FROM ci_playbooks WHERE user_id=? AND is_default=1 ORDER BY id LIMIT 1", (user_id,)).fetchone()
    if row:
        pb = load_playbook(conn, user_id, row["id"])
        if pb:
            result["playbook"] = evaluate_playbook(conn, pb, result, text)


def playbook_ai_lines(playbook_result):
    lines = []
    for d in (playbook_result or {}).get("deviations", []):
        if d["type"] == "forbid":
            lines.append("- forbidden by the client: %s (section %s) %s" % (d["topic"], d["clause_number"], d.get("note", "")))
        elif d["type"] == "require":
            lines.append("- required by the client but missing: %s %s" % (d["topic"], d.get("note", "")))
        elif d["type"] == "payment_days":
            lines.append("- payment term %s days exceeds the client's maximum of %s" % (d["value"], d["limit"]))
        elif d["type"] == "notice_days":
            lines.append("- notice %s days is below the client's minimum of %s" % (d["value"], d["limit"]))
    return lines


def _register_playbooks():
    @m.ci_route("playbooks", "/playbooks", "/playbooks", ("GET", "POST"))
    def ci_playbooks():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        cap = m.dbm.CI_PLAYBOOK_LIMITS[m._access_info(u)["tier"]]
        count = conn.execute("SELECT COUNT(*) AS c FROM ci_playbooks WHERE user_id=?", (u["id"],)).fetchone()["c"]
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()[:80]
            if not name:
                flash(m._T("Donnez un nom au playbook.", "Give the playbook a name."), "error")
            elif cap is not None and count >= cap:
                flash(m._T("Limite du compte gratuit atteinte (%d playbook). Le plan Premium la relève." % cap,
                           "Free account limit reached (%d playbook). Premium raises it." % cap), "error")
            else:
                cur = conn.execute("INSERT INTO ci_playbooks (user_id, name, is_default, created_at) VALUES (?,?,?,?)",
                                   (u["id"], name, 1 if count == 0 else 0, m.dbm.now()))
                conn.commit()
                pid = cur.lastrowid
                conn.close()
                return m._ci_redirect("playbook", pid=pid)
        rows = conn.execute("SELECT p.*, (SELECT COUNT(*) FROM ci_playbook_positions x WHERE x.playbook_id=p.id) AS n FROM ci_playbooks p WHERE user_id=? ORDER BY id", (u["id"],)).fetchall()
        team_rows = conn.execute("SELECT p.*, t.name AS team_name, (SELECT COUNT(*) FROM ci_playbook_positions x WHERE x.playbook_id=p.id) AS n FROM ci_playbooks p "
                                 "JOIN ci_team_members tm ON tm.team_id=p.team_id JOIN ci_teams t ON t.id=p.team_id WHERE lower(tm.email)=? AND p.user_id<>? ORDER BY p.name", ((u["email"] or "").lower(), u["id"])).fetchall()
        conn.close()
        return m._ci_page("playbooks", **m._ci_ctx("playbooks", playbooks=rows, team_playbooks=team_rows, cap=cap, count=count))

    @m.ci_route("playbook", "/playbooks/<int:pid>", "/playbooks/<int:pid>", ("GET", "POST"))
    def ci_playbook(pid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        pb = conn.execute("SELECT * FROM ci_playbooks WHERE id=? AND user_id=?", (pid, u["id"])).fetchone()
        is_mine = bool(pb)
        if not pb:
            pb = conn.execute("SELECT p.* FROM ci_playbooks p JOIN ci_team_members tm ON tm.team_id=p.team_id WHERE p.id=? AND lower(tm.email)=?", (pid, (u["email"] or "").lower())).fetchone()
        if not pb:
            conn.close()
            abort(404)
        topics, _labels = _topic_choices(conn)
        valid = {t for t, _ in topics}
        if request.method == "POST" and not is_mine:
            conn.close()
            abort(403)
        if request.method == "POST":
            def _int(v):
                v = (v or "").strip()
                return int(v) if v.isdigit() and 0 < int(v) <= 3650 else None
            name = (request.form.get("name") or "").strip()[:80] or pb["name"]
            conn.execute("UPDATE ci_playbooks SET name=?, max_payment_days=?, min_notice_days=? WHERE id=?",
                         (name, _int(request.form.get("max_payment_days")), _int(request.form.get("min_notice_days")), pid))
            if request.form.get("is_default"):
                conn.execute("UPDATE ci_playbooks SET is_default=0 WHERE user_id=?", (u["id"],))
                conn.execute("UPDATE ci_playbooks SET is_default=1 WHERE id=?", (pid,))
            conn.execute("DELETE FROM ci_playbook_positions WHERE playbook_id=?", (pid,))
            for tid in valid:
                stance = request.form.get("stance_" + tid, "")
                if stance in ("forbid", "require"):
                    conn.execute("INSERT INTO ci_playbook_positions (playbook_id, topic_id, stance, note) VALUES (?,?,?,?)",
                                 (pid, tid, stance, (request.form.get("note_" + tid) or "").strip()[:300]))
            conn.commit()
            flash(m._T("Playbook enregistré.", "Playbook saved."), "success")
            conn.close()
            return m._ci_redirect("playbook", pid=pid)
        positions = {r["topic_id"]: r for r in conn.execute("SELECT * FROM ci_playbook_positions WHERE playbook_id=?", (pid,)).fetchall()}
        import ci_teams
        write_teams = conn.execute("SELECT t.* FROM ci_teams t JOIN ci_team_members tm ON tm.team_id=t.id WHERE lower(tm.email)=? AND tm.role IN ('admin','juriste') ORDER BY t.name", ((u["email"] or "").lower(),)).fetchall() if is_mine else []
        conn.close()
        return m._ci_page("playbook", **m._ci_ctx("playbooks", pb=pb, topics=topics, positions=positions, is_mine=is_mine, write_teams=write_teams))

    @m.ci_route("playbook_delete", "/playbooks/<int:pid>/supprimer", "/playbooks/<int:pid>/delete", ("POST",))
    def ci_playbook_delete(pid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        conn.execute("DELETE FROM ci_playbook_positions WHERE playbook_id IN (SELECT id FROM ci_playbooks WHERE id=? AND user_id=?)", (pid, u["id"]))
        conn.execute("DELETE FROM ci_playbooks WHERE id=? AND user_id=?", (pid, u["id"]))
        conn.commit()
        conn.close()
        flash(m._T("Playbook supprimé.", "Playbook deleted."), "success")
        return m._ci_redirect("playbooks")

    @m.ci_route("analysis_playbook", "/analyse/<int:aid>/playbook", "/analysis/<int:aid>/playbook", ("POST",))
    def ci_analysis_playbook(aid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        row = m._ci_get_analysis(conn, u, aid)
        try:
            pid = int(request.form.get("playbook_id", "0"))
        except ValueError:
            pid = 0
        pb = load_playbook(conn, u["id"], pid, u["email"])
        if not pb:
            conn.close()
            flash(m._T("Playbook introuvable.", "Playbook not found."), "error")
            return m._ci_redirect("analysis", aid=aid)
        result = json.loads(row["result_json"])
        result["playbook"] = evaluate_playbook(conn, pb, result, row["text_content"])
        conn.execute("UPDATE contract_analyses SET result_json=? WHERE id=? AND user_id=?", (json.dumps(result, ensure_ascii=False), aid, u["id"]))
        conn.commit()
        conn.close()
        return redirect(m.ci_url("analysis", aid=aid) + "#playbook")

# ---------------------------------------------------------------------------
# Registre des contrats (archivage)
# ---------------------------------------------------------------------------

REGISTRY_EXT = {"pdf", "docx", "doc", "txt", "png", "jpg", "jpeg"}


def index_text(data, name):
    """Texte extrait d'un fichier archivé, pour la recherche (vide si illisible ou image)."""
    try:
        return m.contract_engine.extract_text(data, name, max_chars=60000)
    except Exception:  # noqa: BLE001
        return ""


def _registry_dir(uid):
    path = os.path.join(m.UPLOAD_DIR, "ci", str(int(uid)))
    os.makedirs(path, exist_ok=True)
    return path


def _reg_row(conn, u, rid):
    row = conn.execute("SELECT * FROM ci_registry WHERE id=? AND user_id=?", (rid, u["id"])).fetchone()
    if not row:
        conn.close()
        abort(404)
    return row


def _registry_capacity(conn, u):
    cap = m.dbm.CI_REGISTRY_LIMITS[m._access_info(u)["tier"]]
    count = conn.execute("SELECT COUNT(*) AS c FROM ci_registry WHERE user_id=?", (u["id"],)).fetchone()["c"]
    return cap, count, (cap is None or count < cap)


def _register_registry():
    def statuses():
        return dict(m.dbm.CI_REGISTRY_STATUSES_EN if g.lang == "en" else m.dbm.CI_REGISTRY_STATUSES)

    @m.ci_route("registry", "/registre", "/registry", ("GET", "POST"))
    def ci_registry():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        cap, count, ok = _registry_capacity(conn, u)
        if request.method == "POST":
            title = (request.form.get("title") or "").strip()[:160]
            if not title:
                flash(m._T("Le titre est requis.", "A title is required."), "error")
            elif not ok:
                flash(m._T("Limite du compte gratuit atteinte (%d contrats). Le plan Premium la lève." % cap,
                           "Free account limit reached (%d contracts). Premium lifts it." % cap), "error")
            else:
                now = m.dbm.now()
                cur = conn.execute("INSERT INTO ci_registry (user_id, title, counterparty, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                                   (u["id"], title, (request.form.get("counterparty") or "").strip()[:120], "brouillon", now, now))
                rid = cur.lastrowid
                import ci_team
                ci_team.log(conn, rid, u, "created")
                conn.commit()
                conn.close()
                return m._ci_redirect("registry_entry", rid=rid)
        q = (request.args.get("q") or "").strip()
        status = request.args.get("status", "")
        show_archived = request.args.get("archived") == "1"
        sql = "SELECT * FROM ci_registry WHERE user_id=?"
        params = [u["id"]]
        if q:
            sql += " AND (title LIKE ? OR counterparty LIKE ? OR notes LIKE ?)"
            params += ["%" + q + "%"] * 3
        if status in statuses():
            sql += " AND status=?"
            params.append(status)
        sql += " AND archived=?"
        params.append(1 if show_archived else 0)
        sql += " ORDER BY (end_date IS NULL), end_date, id DESC"
        rows = conn.execute(sql, params).fetchall()
        import ci_team
        shared = ci_team.shared_with_me(conn, u)
        conn.close()
        return m._ci_page("registry", **m._ci_ctx("registry", entries=rows, shared=shared, status_labels=statuses(), q=q, status=status,
                                                  show_archived=show_archived, cap=cap, count=count, can_add=ok,
                                                  today=datetime.utcnow().date().isoformat()))

    @m.ci_route("registry_from", "/registre/depuis", "/registry/from", ("POST",))
    def ci_registry_from():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        ref_type = request.form.get("ref_type", "")
        try:
            ref_id = int(request.form.get("ref_id", "0"))
        except ValueError:
            abort(400)
        conn = m.dbm.get_db()
        title, _t, _l = _ref_text(conn, u, ref_type, ref_id)
        cap, count, ok = _registry_capacity(conn, u)
        if not ok:
            conn.close()
            flash(m._T("Limite du compte gratuit atteinte (%d contrats)." % cap, "Free account limit reached (%d contracts)." % cap), "error")
            return m._ci_redirect("registry")
        now = m.dbm.now()
        cur = conn.execute("INSERT INTO ci_registry (user_id, title, status, analysis_id, draft_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                           (u["id"], title, "brouillon", ref_id if ref_type == "analysis" else None, ref_id if ref_type == "draft" else None, now, now))
        rid = cur.lastrowid
        import ci_team
        ci_team.log(conn, rid, u, "created")
        conn.commit()
        conn.close()
        flash(m._T("Ajouté au registre. Complétez la fiche.", "Added to the registry. Complete the record."), "success")
        return m._ci_redirect("registry_entry", rid=rid)

    @m.ci_route("registry_entry", "/registre/<int:rid>", "/registry/<int:rid>", ("GET", "POST"))
    def ci_registry_entry(rid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        import ci_team
        row, role = ci_team.reg_access(conn, u, rid, "editor" if request.method == "POST" else "read")
        owner = u if role == "owner" else conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        if request.method == "POST":
            f = request.form
            status = f.get("status") if f.get("status") in statuses() else row["status"]
            start = m._ci_iso_date(f.get("start_date"))
            end = m._ci_iso_date(f.get("end_date"))
            title = (f.get("title") or "").strip()[:160] or row["title"]
            conn.execute(
                "UPDATE ci_registry SET title=?, counterparty=?, status=?, start_date=?, end_date=?, value_text=?, notes=?, updated_at=? WHERE id=?",
                (title, (f.get("counterparty") or "").strip()[:120], status, start, end, (f.get("value_text") or "").strip()[:120],
                 (f.get("notes") or "").strip()[:2000], m.dbm.now(), rid))
            msgs = [m._T("Fiche enregistrée.", "Record saved.")]
            upload = request.files.get("file")
            if upload and upload.filename:
                ext = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
                if ext not in REGISTRY_EXT:
                    msgs.append(m._T("Format de fichier refusé (pdf, docx, doc, txt, png, jpg).", "File format refused (pdf, docx, doc, txt, png, jpg)."))
                else:
                    data = upload.read()
                    digest = hashlib.sha256(data).hexdigest()
                    name = secure_filename(upload.filename) or ("contrat." + ext)
                    path = os.path.join(_registry_dir(row["user_id"]), "%s_%s" % (digest[:16], name))
                    ci_crypto.write(path, data)
                    if row["file_path"] and row["file_path"] != path and os.path.exists(row["file_path"]):
                        ci_crypto.erase(row["file_path"])
                    conn.execute("UPDATE ci_registry SET file_name=?, file_path=?, file_sha256=?, file_text=? WHERE id=?", (name, path, digest, index_text(data, name), rid))
                    msgs.append(m._T("Fichier archivé (empreinte SHA-256 enregistrée).", "File archived (SHA-256 fingerprint stored)."))
                    ci_team.log(conn, rid, u, "file", name)
            ci_team.log(conn, rid, u, "edited")
            if f.get("track_end") and end and role == "owner":
                label = (m._T("Fin du contrat : ", "End of contract: ") + title)[:200]
                dup = conn.execute("SELECT 1 FROM contract_obligations WHERE user_id=? AND label=? AND due_date=?", (u["id"], label, end)).fetchone()
                if not dup:
                    conn.execute("INSERT INTO contract_obligations (user_id, contract_label, label, kind, due_date, status, created_at) VALUES (?,?,?,?,?,?,?)",
                                 (u["id"], title, label, "terme", end, "a_faire", m.dbm.now()))
                    msgs.append(m._T("Date de fin ajoutée à vos échéances.", "End date added to your deadlines."))
            conn.commit()
            conn.close()
            flash(" ".join(msgs), "success")
            return m._ci_redirect("registry_entry", rid=rid)
        links = {}
        if row["analysis_id"] and role == "owner":
            links["analysis"] = m.ci_url("analysis", aid=row["analysis_id"])
        if row["draft_id"] and role == "owner":
            links["draft"] = m.ci_url("draft", did=row["draft_id"])
        import ci_extras
        sigctx = ci_extras.registry_signature_context(conn, owner, row)
        flow = _stage_state(conn, owner, row)
        if role != "owner":
            ci_team.log_view(conn, rid, u)
        team = ci_team.entry_context(conn, u, row, role)
        ref = None if role != "owner" else ("analysis", row["analysis_id"]) if row["analysis_id"] else (("draft", row["draft_id"]) if row["draft_id"] else None)
        conn.close()
        return m._ci_page("registry_entry", **m._ci_ctx("registry", e=row, status_labels=statuses(), links=links, flow=flow, ref=ref,
                                                        neg_status=NEG_STATUS[g.lang], **sigctx, **team, can_edit=role in ("owner", "editor"),
                                                        has_text=bool(sigctx["sig_has_text"]),
                                                        appr_status={"pending": m._T("en cours", "in progress"), "approved": m._T("approuvée", "approved"),
                                                                     "rejected": m._T("refusée", "rejected"), "cancelled": m._T("annulée", "cancelled")}))

    @m.ci_route("registry_file", "/registre/<int:rid>/fichier", "/registry/<int:rid>/file")
    def ci_registry_file(rid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        import ci_team
        row, _role = ci_team.reg_access(conn, u, rid, "read")
        conn.close()
        if not row["file_path"] or not os.path.exists(row["file_path"]):
            abort(404)
        return ci_crypto.send(row["file_path"], row["file_name"] or "contrat")

    @m.ci_route("registry_archive", "/registre/<int:rid>/archiver", "/registry/<int:rid>/archive", ("POST",))
    def ci_registry_archive(rid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        row = _reg_row(conn, u, rid)
        import ci_team
        ci_team.log(conn, rid, u, "unarchived" if row["archived"] else "archived")
        conn.execute("UPDATE ci_registry SET archived=?, updated_at=? WHERE id=?", (0 if row["archived"] else 1, m.dbm.now(), rid))
        conn.commit()
        conn.close()
        return m._ci_redirect("registry")

    @m.ci_route("registry_delete", "/registre/<int:rid>/supprimer", "/registry/<int:rid>/delete", ("POST",))
    def ci_registry_delete(rid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        row = _reg_row(conn, u, rid)
        if row["file_path"] and os.path.exists(row["file_path"]):
            ci_crypto.erase(row["file_path"])
        conn.execute("DELETE FROM ci_registry WHERE id=? AND user_id=?", (rid, u["id"]))
        conn.commit()
        conn.close()
        flash(m._T("Fiche supprimée.", "Record deleted."), "success")
        return m._ci_redirect("registry")

# ---------------------------------------------------------------------------
# Rédaction d'un contrat par IA
# ---------------------------------------------------------------------------

def _register_ai_draft():
    @m.ci_route("generate_ai", "/generer/ia", "/generate/ai", ("GET", "POST"))
    def ci_generate_ai():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        limit, used, allowed = _monthly_gate(conn, u, "ai")
        configured = m.ci_ai.is_configured()
        premium = m._access_info(u)["is_premium"]
        form = {}
        if request.method == "POST":
            form = request.form
            brief_parts = []
            for key, label in (("kind", "Type of contract"), ("party_a", "Party A"), ("party_b", "Party B"), ("law", "Governing law"), ("terms", "Key terms and context")):
                val = (request.form.get(key) or "").strip()[:3000]
                if val:
                    brief_parts.append("%s: %s" % (label, val))
            lang = request.form.get("language") if request.form.get("language") in ("fr", "en") else g.lang
            error = None
            if not configured:
                error = m._T("La rédaction par IA n'est pas activée sur ce site.", "AI drafting is not enabled on this site.")
            elif not allowed:
                error = (m._T("La rédaction par IA est réservée au plan Premium.", "AI drafting is reserved for the Premium plan.") if limit == 0
                         else m._ci_quota_message("ai", limit))
            elif not request.form.get("consent"):
                error = m._T("Cochez la case de consentement.", "Tick the consent box.")
            elif not (request.form.get("kind") or "").strip() or not (request.form.get("terms") or "").strip():
                error = m._T("Indiquez le type de contrat et les conditions clés.", "Enter the contract type and key terms.")
            if error:
                flash(error, "error")
            else:
                brief = "\n".join(brief_parts)
                custom_rules, sources = m._ci_load_knowledge(conn, with_text=True)
                excerpts = m.contract_engine.select_excerpts(sources, set(), brief)
                try:
                    out = m.ci_ai.run_draft(brief, lang, excerpts)
                except m.ci_ai.AIError as exc:
                    m.app.logger.warning("Rédaction IA échouée (%s) %s", exc.code, exc.detail)
                    flash(m._T("La rédaction par IA a échoué (%s). Votre quota n'a pas été décompté. Réessayez." % exc.code,
                               "AI drafting failed (%s). Your quota was not used. Try again." % exc.code), "error")
                else:
                    cur = conn.execute(
                        "INSERT INTO contract_drafts (user_id, template_key, language, title, values_json, body_text, created_at) VALUES (?,?,?,?,?,?,?)",
                        (u["id"], "ai", lang, out["title"], json.dumps({"assumptions": out["assumptions"], "to_verify": out["to_verify"], "model": out["model"]}, ensure_ascii=False),
                         out["body"], m.dbm.now()))
                    m._ci_record_usage(conn, u, "ai")
                    conn.commit()
                    did = cur.lastrowid
                    conn.close()
                    m.dbm.log_activity(u["id"], "contract_ai_draft", "brouillon #%s" % did)
                    return m._ci_redirect("draft", did=did)
        conn.close()
        return m._ci_page("generate_ai", **m._ci_ctx("generate", form=form, configured=configured, premium=premium,
                                                     limit=limit, used=used, allowed=allowed))


# ---------------------------------------------------------------------------
# Obligations par partie
# ---------------------------------------------------------------------------

def _register_party_obligations():
    @m.ci_route("analysis_parties", "/analyse/<int:aid>/obligations", "/analysis/<int:aid>/obligations", ("POST",))
    def ci_analysis_parties(aid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        row = m._ci_get_analysis(conn, u, aid)
        found = m.contract_engine.extract_party_obligations(row["text_content"], row["language"])
        chosen = {int(v) for v in request.form.getlist("pick") if v.isdigit()}
        cap = m.dbm.CI_OBLIGATION_LIMITS[m._access_info(u)["tier"]]
        count = conn.execute("SELECT COUNT(*) AS c FROM contract_obligations WHERE user_id=?", (u["id"],)).fetchone()["c"]
        added, blocked = 0, False
        for i, o in enumerate(found):
            if i not in chosen:
                continue
            if cap is not None and count + added >= cap:
                blocked = True
                break
            label = ("%s : %s" % (o["party"], o["action"]))[:200]
            if conn.execute("SELECT 1 FROM contract_obligations WHERE user_id=? AND analysis_id=? AND label=?", (u["id"], aid, label)).fetchone():
                continue
            conn.execute(
                "INSERT INTO contract_obligations (user_id, analysis_id, contract_label, label, kind, due_date, delay_text, estimated, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (u["id"], aid, row["title"], label, "obligation", None, o["delay_text"], 0, "a_faire", m.dbm.now()))
            added += 1
        conn.commit()
        conn.close()
        if added:
            flash(m._T("%d obligation(s) ajoutée(s) à votre suivi (sans date : ajoutez-en une depuis la page Échéances si besoin)." % added,
                       "%d obligation(s) added to your tracker (no date: set one from the Deadlines page if needed)." % added), "success")
        elif not blocked:
            flash(m._T("Aucune nouvelle obligation sélectionnée.", "No new obligation selected."), "error")
        if blocked:
            flash(m._T("Limite du compte gratuit atteinte (%d). Le plan Premium la lève." % cap, "Free account limit reached (%d). Premium lifts it." % cap), "error")
        return m._ci_redirect("deadlines")


# ---------------------------------------------------------------------------
# Tableau de bord « Mon espace » et dossier contrat
# ---------------------------------------------------------------------------

SAMPLE_CONTRACT_FR = """CONTRAT DE PRESTATION DE SERVICES (EXEMPLE)
1. Objet. Le Prestataire s'engage à fournir au Client des services de maintenance informatique.
2. Paiement. Le Client doit payer chaque facture dans un délai de 90 jours suivant réception. Des intérêts de retard de 5 % par mois s'appliquent.
3. Durée. Le contrat est conclu pour un an et se renouvelle automatiquement par tacite reconduction.
4. Résiliation. Le Prestataire peut résilier à tout moment avec un préavis de 5 jours. Le Client ne peut pas résilier avant le terme.
5. Responsabilité. La responsabilité du Client est illimitée pour tout dommage direct ou indirect.
6. Confidentialité. Le Prestataire ne doit pas divulguer les informations confidentielles du Client.
7. Modification. Le Prestataire peut modifier unilatéralement les tarifs en cours de contrat.
"""
SAMPLE_CONTRACT_EN = """SERVICES AGREEMENT (SAMPLE)
1. Purpose. The Supplier shall provide IT maintenance services to the Customer.
2. Payment. The Customer must pay each invoice within 90 days of receipt. Late interest of 5% per month applies.
3. Term. The agreement runs for one year and renews automatically by tacit renewal.
4. Termination. The Supplier may terminate at any time on 5 days' notice. The Customer may not terminate before the end of the term.
5. Liability. The Customer's liability is unlimited for any direct or indirect damage.
6. Confidentiality. The Supplier shall not disclose the Customer's confidential information.
7. Amendment. The Supplier may unilaterally change prices during the term.
"""


def _days_until(iso, today):
    try:
        return (datetime.strptime(iso[:10], "%Y-%m-%d").date() - today).days
    except (ValueError, TypeError):
        return None


def _stage_state(conn, u, entry):
    """Étapes du parcours d'un contrat du registre, et prochaine action recommandée."""
    refs = []
    if entry["analysis_id"]:
        refs.append(("analysis", entry["analysis_id"]))
    if entry["draft_id"]:
        refs.append(("draft", entry["draft_id"]))
    negs, approvals = [], []
    for rt, rid in refs:
        negs += conn.execute("SELECT * FROM ci_negotiations WHERE owner_id=? AND ref_type=? AND ref_id=? ORDER BY id DESC", (u["id"], rt, rid)).fetchall()
        approvals += conn.execute("SELECT * FROM ci_approvals WHERE owner_id=? AND ref_type=? AND ref_id=? ORDER BY id DESC", (u["id"], rt, rid)).fetchall()
    if entry["negotiation_id"]:
        extra = conn.execute("SELECT * FROM ci_negotiations WHERE id=? AND owner_id=?", (entry["negotiation_id"], u["id"])).fetchone()
        if extra and extra["id"] not in [n["id"] for n in negs]:
            negs.append(extra)
    deadlines = []
    if entry["analysis_id"]:
        deadlines = conn.execute("SELECT * FROM contract_obligations WHERE user_id=? AND analysis_id=? ORDER BY due_date IS NULL, due_date LIMIT 20",
                                 (u["id"], entry["analysis_id"])).fetchall()
    has_doc = bool(refs)
    neg_state = "todo" if not negs else ("done" if any(n["status"] == "agreed" for n in negs) else "current")
    appr_state = "todo" if not approvals else ("done" if any(a["status"] == "approved" for a in approvals) else ("current" if any(a["status"] == "pending" for a in approvals) else "todo"))
    signed = bool(entry["file_path"])
    stages = [
        ("draft", m._T("Rédaction / analyse", "Drafting / analysis"), "done" if has_doc else "current"),
        ("negotiation", m._T("Négociation", "Negotiation"), neg_state),
        ("approval", m._T("Approbation", "Approval"), appr_state),
        ("signature", m._T("Signature et archivage", "Signature and archive"), "done" if signed else "todo"),
        ("active", m._T("Suivi des échéances", "Deadline tracking"), "done" if entry["status"] == "actif" and entry["end_date"] else "todo"),
    ]
    # première étape non terminée = étape courante ; les suivantes restent « à venir »
    marked = False
    out = []
    for key, label, st in stages:
        if signed and key in ("negotiation", "approval") and st != "done":
            st = "skipped"   # contrat déjà signé : étape non utilisée
        elif st != "done":
            st = "todo" if marked else "current"
            marked = True
        out.append({"key": key, "label": label, "state": st})
    nxt = None
    if signed:
        if not entry["end_date"]:
            nxt = ("dates", m._T("Renseignez la date de fin et cochez « ajouter à mes échéances » pour recevoir des rappels.", "Enter the end date and tick “add to my deadlines” to get reminders."))
    elif not has_doc:
        nxt = ("analyze", m._T("Analysez ou rédigez le contrat, puis rattachez-le à cette fiche depuis la page de l'analyse.", "Analyse or draft the contract, then attach it to this record from the analysis page."))
    elif not negs and not approvals and not signed:
        nxt = ("negotiate", m._T("Envoyez le texte à la contrepartie pour négocier, ou lancez directement une approbation interne.", "Send the text to the counterparty to negotiate, or start an internal approval right away."))
    elif any(n["status"] == "open" for n in negs):
        nxt = ("negotiation_open", m._T("Une négociation est en cours : suivez les versions et acceptez la dernière quand vous êtes d'accord.", "A negotiation is in progress: follow the versions and accept the latest when you agree."))
    elif not any(a["status"] == "approved" for a in approvals) and not any(a["status"] == "pending" for a in approvals):
        nxt = ("approve", m._T("Faites valider le texte final par vos approbateurs avant signature.", "Have your approvers sign off on the final text before signing."))
    elif any(a["status"] == "pending" for a in approvals):
        nxt = ("approval_pending", m._T("Une approbation est en cours : relancez l'approbateur si besoin.", "An approval is in progress: remind the approver if needed."))
    elif not signed:
        nxt = ("sign", m._T("Envoyez le contrat final en signature (encadré « Signature électronique » ci-dessous) ou téléversez le fichier déjà signé.", "Send the final contract for signature (“Electronic signature” box below) or upload the already signed file."))
    elif not entry["end_date"]:
        nxt = ("dates", m._T("Renseignez la date de fin et cochez « ajouter à mes échéances » pour recevoir des rappels.", "Enter the end date and tick “add to my deadlines” to get reminders."))
    return {"stages": out, "negotiations": negs, "approvals": approvals, "deadlines": deadlines, "next": nxt, "has_doc": has_doc}


def _register_dashboard():
    @m.ci_route("home", "/espace", "/home")
    def ci_home():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        today = ci_reminders.local_today()
        lang = g.lang
        todo = []

        # Échéances : en retard ou dans les 14 jours
        for o in conn.execute("SELECT * FROM contract_obligations WHERE user_id=? AND status='a_faire' AND due_date IS NOT NULL AND due_date<>'' ORDER BY due_date LIMIT 60", (u["id"],)).fetchall():
            d = _days_until(o["due_date"], today)
            if d is None or d > 14:
                continue
            todo.append({"rank": d, "tone": "late" if d < 0 else ("soon" if d <= 3 else "info"),
                         "title": o["label"],
                         "meta": (m._T("En retard de %d j" % -d, "%d d overdue" % -d) if d < 0 else (m._T("Aujourd'hui", "Today") if d == 0 else m._T("Dans %d j" % d, "In %d d" % d))) + " · " + o["due_date"],
                         "url": m.ci_url("deadlines"), "cta": m._T("Voir", "Open")})
        # Approbations en cours
        for a in conn.execute("SELECT * FROM ci_approvals WHERE owner_id=? AND status='pending' ORDER BY id DESC LIMIT 20", (u["id"],)).fetchall():
            st = conn.execute("SELECT approver_name, approver_email, notified_at FROM ci_approval_steps WHERE approval_id=? AND status='pending' ORDER BY step_order LIMIT 1", (a["id"],)).fetchone()
            who = (st["approver_name"] or st["approver_email"]) if st else "—"
            days = _days_until((st["notified_at"] or a["created_at"]) if st else a["created_at"], today)
            waited = -days if days is not None else 0
            todo.append({"rank": 5 - min(waited, 5), "tone": "soon" if waited >= 3 else "info",
                         "title": m._T("En attente de %s : %s" % (who, a["title"]), "Waiting for %s: %s" % (who, a["title"])),
                         "meta": m._T("Approbation · depuis %d j" % waited, "Approval · for %d d" % waited),
                         "url": m.ci_url("approval", aid=a["id"]), "cta": m._T("Relancer", "Remind")})
        # Signatures en cours
        for sr in conn.execute("SELECT r.*, (SELECT COUNT(*) FROM ci_signers s WHERE s.request_id=r.id) AS total, (SELECT COUNT(*) FROM ci_signers s WHERE s.request_id=r.id AND s.status='signed') AS signed FROM ci_signature_requests r WHERE owner_id=? AND status='pending' ORDER BY id DESC LIMIT 20", (u["id"],)).fetchall():
            waited = -(_days_until(sr["created_at"], today) or 0)
            todo.append({"rank": 4 - min(waited, 4), "tone": "soon" if waited >= 3 else "info", "title": sr["title"],
                         "meta": m._T("Signature · %d/%d signé(s) · depuis %d j" % (sr["signed"], sr["total"], waited), "Signature · %d/%d signed · for %d d" % (sr["signed"], sr["total"], waited)),
                         "url": m.ci_url("signature", sid=sr["id"]), "cta": m._T("Relancer", "Remind")})
        # Négociations où la balle est dans mon camp
        for n in conn.execute("SELECT * FROM ci_negotiations WHERE owner_id=? AND status='open' ORDER BY updated_at DESC LIMIT 20", (u["id"],)).fetchall():
            last = conn.execute("SELECT author FROM ci_neg_versions WHERE negotiation_id=? ORDER BY version_no DESC LIMIT 1", (n["id"],)).fetchone()
            mine = bool(last) and last["author"] == "counterparty"
            todo.append({"rank": 2 if mine else 9, "tone": "soon" if mine else "info",
                         "title": n["title"],
                         "meta": m._T("Négociation · la contrepartie a répondu : à vous", "Negotiation · the counterparty replied: your turn") if mine else m._T("Négociation · en attente de la contrepartie", "Negotiation · waiting for the counterparty"),
                         "url": m.ci_url("negotiation", nid=n["id"]), "cta": m._T("Répondre", "Reply") if mine else m._T("Ouvrir", "Open")})
        # Contrats du registre qui se terminent bientôt
        reg = conn.execute("SELECT * FROM ci_registry WHERE user_id=? AND archived=0 ORDER BY updated_at DESC", (u["id"],)).fetchall()
        for r in reg:
            if r["end_date"] and r["status"] == "actif":
                d = _days_until(r["end_date"], today)
                if d is not None and d <= 90:
                    todo.append({"rank": d if d >= 0 else -1, "tone": "late" if d < 0 else ("soon" if d <= 30 else "info"),
                                 "title": r["title"], "meta": (m._T("Se termine dans %d j" % d, "Ends in %d d" % d) if d >= 0 else m._T("Terme dépassé", "Past its end date")) + " · " + r["end_date"],
                                 "url": m.ci_url("registry_entry", rid=r["id"]), "cta": m._T("Décider : renouveler ?", "Decide: renew?")})
        todo.sort(key=lambda t: t["rank"])

        analyses = conn.execute("SELECT id, title, overall, created_at, result_json FROM contract_analyses WHERE user_id=? ORDER BY id DESC", (u["id"],)).fetchall()
        high = sum(1 for a in analyses if a["overall"] == "eleve")
        active = [r for r in reg if r["status"] == "actif"]
        due30 = sum(1 for o in conn.execute("SELECT due_date FROM contract_obligations WHERE user_id=? AND status='a_faire' AND due_date IS NOT NULL AND due_date<>''", (u["id"],)).fetchall()
                    if (_days_until(o["due_date"], today) is not None and _days_until(o["due_date"], today) <= 30))
        pending_appr = conn.execute("SELECT COUNT(*) AS c FROM ci_approvals WHERE owner_id=? AND status='pending'", (u["id"],)).fetchone()["c"]
        open_neg = conn.execute("SELECT COUNT(*) AS c FROM ci_negotiations WHERE owner_id=? AND status='open'", (u["id"],)).fetchone()["c"]
        drafts = conn.execute("SELECT id, title, created_at FROM contract_drafts WHERE user_id=? ORDER BY id DESC LIMIT 3", (u["id"],)).fetchall()
        pref = ci_reminders.get_prefs(conn, u["id"])
        empty = not (analyses or reg or drafts)
        recent = []
        for a in analyses[:4]:
            recent.append({"kind": m._T("Analyse", "Analysis"), "title": a["title"], "level": a["overall"], "url": m.ci_url("analysis", aid=a["id"]), "date": a["created_at"][:10]})
        for d_ in drafts:
            recent.append({"kind": m._T("Brouillon", "Draft"), "title": d_["title"], "level": None, "url": m.ci_url("draft", did=d_["id"]), "date": d_["created_at"][:10]})
        recent.sort(key=lambda x: x["date"], reverse=True)
        import ci_team
        activity = ci_team.home_activity(conn, u)
        conn.close()
        smtp_ok = notifications.is_configured()
        return m._ci_page("home", **m._ci_ctx("home", todo=todo[:12], todo_more=max(0, len(todo) - 12), recent=recent[:6], empty=empty, activity=activity,
                                              kpi={"analyses": len(analyses), "high": high, "active": len(active), "due30": due30,
                                                   "approvals": pending_appr, "negotiations": open_neg},
                                              reminders_on=bool(pref["enabled"]) if pref else True, smtp_ok=smtp_ok, user=u))

    @m.ci_route("sample", "/espace/exemple", "/home/sample", ("POST",))
    def ci_sample():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        if conn.execute("SELECT COUNT(*) AS c FROM contract_analyses WHERE user_id=?", (u["id"],)).fetchone()["c"]:
            conn.close()
            return m._ci_redirect("home")
        lang = g.lang
        text = SAMPLE_CONTRACT_EN if lang == "en" else SAMPLE_CONTRACT_FR
        custom_rules, kb_sources = m._ci_load_knowledge(conn)
        result = m.contract_engine.analyze_contract(text, custom_rules=custom_rules, sources=kb_sources)
        apply_default_playbook(conn, u["id"], result, text)
        cur = conn.execute(
            "INSERT INTO contract_analyses (user_id, title, language, source_name, text_content, result_json, overall, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (u["id"], m._T("Exemple : contrat de services", "Sample: services agreement"), result["language"], None, text, json.dumps(result, ensure_ascii=False), result["summary"]["overall"], m.dbm.now()))
        conn.commit()
        aid = cur.lastrowid
        conn.close()
        flash(m._T("Voici une analyse d'exemple (non décomptée de votre quota). Essayez ensuite avec votre propre contrat.", "Here is a sample analysis (not counted against your quota). Then try your own contract."), "success")
        return m._ci_redirect("analysis", aid=aid)
