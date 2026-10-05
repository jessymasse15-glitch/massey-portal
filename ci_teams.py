"""Contract Intelligence : espaces d'équipe — membres avec rôles (administrateur, juriste, approbateur, lecteur),
fiches d'équipe, validation interne, playbooks partagés et modèles d'entreprise."""
import json

from flask import abort, flash, g, redirect, request

import ci_team

m = None
F = None

ROLES = ("admin", "juriste", "approbateur", "lecteur")
MAX_TEAMS = 3
MAX_MEMBERS = 15
ROLE_HELP = {
    "fr": {"admin": "gère l'équipe, les membres, les modèles ; tout ce que fait un juriste",
           "juriste": "crée et modifie les fiches d'équipe, les playbooks et les modèles",
           "approbateur": "lit les fiches, commente et donne la validation interne",
           "lecteur": "lit les fiches, playbooks et modèles ; ne modifie ni ne commente"},
    "en": {"admin": "manages the team, members and templates; everything a lawyer can do",
           "juriste": "creates and edits team records, playbooks and templates",
           "approbateur": "reads records, comments and gives internal sign-off",
           "lecteur": "reads records, playbooks and templates; cannot edit or comment"},
}


def init(app_module, features_module):
    global m, F
    m, F = app_module, features_module
    _register()


def _email(u):
    return ci_team._email(u)


def role_label(r):
    return ci_team.TEAM_ROLE_LABELS[r][1 if g.lang == "en" else 0]


def my_teams(conn, u):
    return conn.execute("SELECT t.*, tm.role AS my_role, (SELECT COUNT(*) FROM ci_team_members x WHERE x.team_id=t.id) AS n FROM ci_teams t "
                        "JOIN ci_team_members tm ON tm.team_id=t.id WHERE lower(tm.email)=? ORDER BY t.name", (_email(u),)).fetchall()


def team_access(conn, u, tid, need=None):
    """(équipe, rôle d'équipe). need : None (membre) | 'write' (admin/juriste) | 'admin'."""
    t = conn.execute("SELECT * FROM ci_teams WHERE id=?", (tid,)).fetchone()
    r = conn.execute("SELECT role FROM ci_team_members WHERE team_id=? AND lower(email)=?", (tid, _email(u))).fetchone() if t else None
    if not t or not r:
        conn.close()
        abort(404)
    role = r["role"]
    if (need == "admin" and role != "admin") or (need == "write" and role not in ("admin", "juriste")):
        conn.close()
        abort(403)
    return t, role


def team_playbook_ids(conn, u):
    return [r["id"] for r in conn.execute(
        "SELECT p.id FROM ci_playbooks p JOIN ci_team_members tm ON tm.team_id=p.team_id WHERE lower(tm.email)=?", (_email(u),)).fetchall()]


def _register():
    @m.ci_route("teams", "/equipes", "/teams", ("GET", "POST"))
    def ci_teams():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        premium = m._access_info(u)["is_premium"]
        owned = conn.execute("SELECT COUNT(*) AS c FROM ci_teams WHERE owner_id=?", (u["id"],)).fetchone()["c"]
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()[:80]
            if not premium:
                flash(m._T("Créer un espace d'équipe fait partie du plan Premium. Vous pouvez en rejoindre un gratuitement sur invitation.", "Creating a team space is part of the Premium plan. You can join one for free when invited."), "error")
            elif not name:
                flash(m._T("Donnez un nom à l'équipe.", "Give the team a name."), "error")
            elif owned >= MAX_TEAMS:
                flash(m._T("Maximum %d équipes." % MAX_TEAMS, "Maximum %d teams." % MAX_TEAMS), "error")
            else:
                now = m.dbm.now()
                cur = conn.execute("INSERT INTO ci_teams (name, owner_id, created_at) VALUES (?,?,?)", (name, u["id"], now))
                tid = cur.lastrowid
                conn.execute("INSERT INTO ci_team_members (team_id, email, role, created_at) VALUES (?,?,?,?)", (tid, _email(u), "admin", now))
                conn.commit()
                conn.close()
                return m._ci_redirect("team", tid=tid)
        teams = my_teams(conn, u)
        conn.close()
        return m._ci_page("teams", **m._ci_ctx("teams", teams=teams, premium=premium, role_label=role_label))

    @m.ci_route("team", "/equipes/<int:tid>", "/teams/<int:tid>")
    def ci_team_page(tid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        t, role = team_access(conn, u, tid)
        members = conn.execute("SELECT * FROM ci_team_members WHERE team_id=? ORDER BY id", (tid,)).fetchall()
        entries = conn.execute("SELECT r.*, o.full_name AS owner_name FROM ci_registry r JOIN users o ON o.id=r.user_id WHERE r.team_id=? AND r.archived=0 ORDER BY r.updated_at DESC", (tid,)).fetchall()
        playbooks = conn.execute("SELECT * FROM ci_playbooks WHERE team_id=? ORDER BY name", (tid,)).fetchall()
        templates = conn.execute("SELECT * FROM ci_team_templates WHERE team_id=? ORDER BY id DESC", (tid,)).fetchall()
        en = g.lang == "en"
        statuses = dict(m.dbm.CI_REGISTRY_STATUSES_EN if en else m.dbm.CI_REGISTRY_STATUSES)
        conn.close()
        return m._ci_page("team", **m._ci_ctx("teams", team=t, my_role=role, members=members, entries=entries, playbooks=playbooks, templates=templates,
                                              status_labels=statuses, role_label=role_label, roles=ROLES, role_help=ROLE_HELP["en" if en else "fr"], can_write=role in ("admin", "juriste")))

    @m.ci_route("team_member_add", "/equipes/<int:tid>/membres", "/teams/<int:tid>/members", ("POST",))
    def ci_team_member_add(tid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        t, _r = team_access(conn, u, tid, "admin")
        email = (request.form.get("email") or "").strip().lower()[:160]
        role = request.form.get("role") if request.form.get("role") in ROLES else "lecteur"
        n = conn.execute("SELECT COUNT(*) AS c FROM ci_team_members WHERE team_id=?", (tid,)).fetchone()["c"]
        ex = conn.execute("SELECT id, role FROM ci_team_members WHERE team_id=? AND lower(email)=?", (tid, email)).fetchone()
        if not F._email_ok(email):
            flash(m._T("Adresse courriel invalide.", "Invalid email address."), "error")
        elif ex and ex["role"] == "admin" and role != "admin" and t["owner_id"] == (conn.execute("SELECT id FROM users WHERE lower(email)=?", (email,)).fetchone() or {"id": None})["id"]:
            flash(m._T("Le créateur de l'équipe reste administrateur.", "The team's creator remains administrator."), "error")
        elif not ex and n >= MAX_MEMBERS:
            flash(m._T("Maximum %d membres." % MAX_MEMBERS, "Maximum %d members." % MAX_MEMBERS), "error")
        else:
            if ex:
                conn.execute("UPDATE ci_team_members SET role=? WHERE id=?", (role, ex["id"]))
            else:
                conn.execute("INSERT INTO ci_team_members (team_id, email, role, created_at) VALUES (?,?,?,?)", (tid, email, role, m.dbm.now()))
            conn.commit()
            if not ex:
                link = F._site_link(m.ci_url("team", tid=tid))
                who = ci_team.actor_label(u)
                F._notify(email, m._T("%s vous invite dans l'équipe « %s »" % (who, t["name"]), "%s invites you to the team \"%s\"" % (who, t["name"])),
                          m._T("Bonjour,\n\n%s vous a ajouté à l'équipe « %s » (rôle : %s).\n\nOuvrez l'espace d'équipe (connectez-vous ou créez un compte gratuit avec cette adresse courriel) :\n%s\n\n— Massey Contracts & Tax" % (who, t["name"], role_label(role), link),
                               "Hello,\n\n%s added you to the team \"%s\" (role: %s).\n\nOpen the team space (sign in or create a free account with this email address):\n%s\n\n— Massey Contracts & Tax" % (who, t["name"], role_label(role), link)))
            flash(m._T("Membre enregistré : %s." % email, "Member saved: %s." % email), "success")
        conn.close()
        return redirect(m.ci_url("team", tid=tid) + "#membres")

    @m.ci_route("team_member_remove", "/equipes/<int:tid>/membres/<int:mid>/retirer", "/teams/<int:tid>/members/<int:mid>/remove", ("POST",))
    def ci_team_member_remove(tid, mid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        t, role = team_access(conn, u, tid)
        mem = conn.execute("SELECT * FROM ci_team_members WHERE id=? AND team_id=?", (mid, tid)).fetchone()
        own = mem and mem["email"].lower() == _email(u)
        owner_email = (conn.execute("SELECT email FROM users WHERE id=?", (t["owner_id"],)).fetchone() or {"email": ""})["email"].lower()
        if mem and (role == "admin" or own) and mem["email"].lower() != owner_email:
            conn.execute("DELETE FROM ci_team_members WHERE id=?", (mid,))
            conn.commit()
            flash(m._T("Membre retiré.", "Member removed."), "success")
        elif mem:
            flash(m._T("Le créateur de l'équipe ne peut pas être retiré.", "The team's creator cannot be removed."), "error")
        conn.close()
        return redirect(m.ci_url("teams") if own else m.ci_url("team", tid=tid) + "#membres")

    @m.ci_route("team_delete", "/equipes/<int:tid>/supprimer", "/teams/<int:tid>/delete", ("POST",))
    def ci_team_delete(tid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        t, _r = team_access(conn, u, tid, "admin")
        if t["owner_id"] != u["id"]:
            conn.close()
            abort(403)
        conn.execute("UPDATE ci_registry SET team_id=NULL WHERE team_id=?", (tid,))
        conn.execute("UPDATE ci_playbooks SET team_id=NULL WHERE team_id=?", (tid,))
        conn.execute("DELETE FROM ci_teams WHERE id=?", (tid,))
        conn.commit()
        conn.close()
        flash(m._T("Équipe supprimée. Les fiches et playbooks restent à leurs propriétaires.", "Team deleted. Records and playbooks stay with their owners."), "success")
        return m._ci_redirect("teams")

    @m.ci_route("team_template_add", "/equipes/<int:tid>/modeles", "/teams/<int:tid>/templates", ("POST",))
    def ci_team_template_add(tid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        team_access(conn, u, tid, "write")
        title = (request.form.get("title") or "").strip()[:120]
        body = (request.form.get("body") or "").strip()[:60000]
        up = request.files.get("file")
        if up and up.filename:
            try:
                body = m.contract_engine.extract_text(up.read(m.contract_engine.MAX_BYTES + 1), up.filename)
                title = title or up.filename.rsplit(".", 1)[0][:120]
            except m.contract_engine.ExtractionError:
                flash(m._T("Fichier illisible : collez le texte à la place.", "Unreadable file: paste the text instead."), "error")
                conn.close()
                return redirect(m.ci_url("team", tid=tid) + "#modeles")
        if not title or len(body) < 40:
            flash(m._T("Un titre et un texte d'au moins 40 caractères sont requis.", "A title and a text of at least 40 characters are required."), "error")
        else:
            conn.execute("INSERT INTO ci_team_templates (team_id, title, body_text, created_by, created_at) VALUES (?,?,?,?,?)", (tid, title, body, u["id"], m.dbm.now()))
            conn.commit()
            flash(m._T("Modèle ajouté.", "Template added."), "success")
        conn.close()
        return redirect(m.ci_url("team", tid=tid) + "#modeles")

    @m.ci_route("team_template_delete", "/equipes/<int:tid>/modeles/<int:xid>/supprimer", "/teams/<int:tid>/templates/<int:xid>/delete", ("POST",))
    def ci_team_template_delete(tid, xid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        team_access(conn, u, tid, "write")
        conn.execute("DELETE FROM ci_team_templates WHERE id=? AND team_id=?", (xid, tid))
        conn.commit()
        conn.close()
        return redirect(m.ci_url("team", tid=tid) + "#modeles")

    @m.ci_route("team_template_use", "/equipes/<int:tid>/modeles/<int:xid>/utiliser", "/teams/<int:tid>/templates/<int:xid>/use", ("POST",))
    def ci_team_template_use(tid, xid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        team_access(conn, u, tid)
        tpl = conn.execute("SELECT * FROM ci_team_templates WHERE id=? AND team_id=?", (xid, tid)).fetchone()
        if not tpl:
            conn.close()
            abort(404)
        limit, used, allowed = m._ci_quota(conn, u, "draft")
        if not allowed:
            conn.close()
            flash(m._ci_quota_message("draft", limit), "error")
            return redirect(m.ci_url("team", tid=tid) + "#modeles")
        cur = conn.execute("INSERT INTO contract_drafts (user_id, template_key, language, title, values_json, body_text, created_at) VALUES (?,?,?,?,?,?,?)",
                           (u["id"], "team:%d" % xid, g.lang, tpl["title"], json.dumps({}), tpl["body_text"], m.dbm.now()))
        m._ci_record_usage(conn, u, "draft")
        conn.commit()
        did = cur.lastrowid
        conn.close()
        flash(m._T("Brouillon créé à partir du modèle de l'équipe.", "Draft created from the team template."), "success")
        return m._ci_redirect("draft", did=did)

    @m.ci_route("playbook_team", "/playbooks/<int:pid>/equipe", "/playbooks/<int:pid>/team", ("POST",))
    def ci_playbook_team(pid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        pb = conn.execute("SELECT * FROM ci_playbooks WHERE id=? AND user_id=?", (pid, u["id"])).fetchone()
        if not pb:
            conn.close()
            abort(404)
        raw = (request.form.get("team_id") or "").strip()
        tid = None
        if raw:
            t, _r = team_access(conn, u, int(raw) if raw.isdigit() else 0, "write")
            tid = t["id"]
        conn.execute("UPDATE ci_playbooks SET team_id=? WHERE id=?", (tid, pid))
        conn.commit()
        conn.close()
        flash(m._T("Playbook partagé avec l'équipe." if tid else "Playbook de nouveau privé.", "Playbook shared with the team." if tid else "Playbook private again."), "success")
        return m._ci_redirect("playbook", pid=pid)

    @m.ci_route("registry_team", "/registre/<int:rid>/equipe", "/registry/<int:rid>/team", ("POST",))
    def ci_registry_team(rid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        row, _role = ci_team.reg_access(conn, u, rid, "owner")
        raw = (request.form.get("team_id") or "").strip()
        tid = None
        name = ""
        if raw:
            t, _r = team_access(conn, u, int(raw) if raw.isdigit() else 0, "write")
            tid, name = t["id"], t["name"]
        conn.execute("UPDATE ci_registry SET team_id=?, updated_at=? WHERE id=?", (tid, m.dbm.now(), rid))
        ci_team.log(conn, rid, u, "shared", ("(%s)" % name) if tid else m._T("(équipe retirée)", "(team removed)"))
        conn.commit()
        conn.close()
        flash(m._T("Fiche rattachée à l'équipe « %s »." % name if tid else "Fiche retirée de l'équipe.", "Record attached to the team \"%s\"." % name if tid else "Record removed from the team."), "success")
        return redirect(m.ci_url("registry_entry", rid=rid) + "#equipe")

    @m.ci_route("registry_signoff", "/registre/<int:rid>/validation", "/registry/<int:rid>/signoff", ("POST",))
    def ci_registry_signoff(rid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        row, role = ci_team.reg_access(conn, u, rid, "read")
        trole = ci_team.team_role_for(conn, u, row)
        if trole not in ("admin", "juriste", "approbateur"):
            conn.close()
            abort(403)
        decision = request.form.get("decision")
        comment = (request.form.get("comment") or "").strip()[:500]
        if decision not in ("approved", "changes"):
            conn.close()
            abort(400)
        if decision == "changes" and not comment:
            flash(m._T("Précisez les changements demandés.", "Say which changes you request."), "error")
        else:
            name = ci_team.actor_label(u)
            conn.execute("INSERT INTO ci_team_signoffs (registry_id, user_id, user_name, decision, comment, created_at) VALUES (?,?,?,?,?,?)", (rid, u["id"], name, decision, comment, m.dbm.now()))
            ci_team.log(conn, rid, u, "signoff", m._T("validé", "approved") if decision == "approved" else m._T("changements demandés", "changes requested"))
            conn.commit()
            owner = conn.execute("SELECT email FROM users WHERE id=?", (row["user_id"],)).fetchone()
            if owner and owner["email"].lower() != _email(u):
                F._notify(owner["email"], m._T("Validation interne : %s" % row["title"], "Internal sign-off: %s" % row["title"]),
                          m._T("%s a %s « %s ».\n%s\n\n%s" % (name, "validé" if decision == "approved" else "demandé des changements sur", row["title"], comment, F._site_link(m.ci_url("registry_entry", rid=rid))),
                               "%s %s \"%s\".\n%s\n\n%s" % (name, "approved" if decision == "approved" else "requested changes on", row["title"], comment, F._site_link(m.ci_url("registry_entry", rid=rid)))))
            flash(m._T("Votre décision est enregistrée.", "Your decision is recorded."), "success")
        conn.close()
        return redirect(m.ci_url("registry_entry", rid=rid) + "#equipe")
