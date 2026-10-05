"""Contract Intelligence : recherche globale et import en lot."""
import os
import re
from urllib.parse import quote  # noqa: F401

from flask import flash, g, redirect, request
from markupsafe import Markup, escape
from werkzeug.utils import secure_filename

import ci_crypto
import ci_team

m = None
F = None

MAX_IMPORT = 20


def init(app_module, features_module):
    global m, F
    m, F = app_module, features_module
    _register()


def snippet(text, q, width=90):
    """Extrait autour de la première occurrence de q (sans tenir compte des accents ni de la casse), terme surligné."""
    text = re.sub(r"\s+", " ", text or "")
    i = text.lower().find(q.lower())
    if i < 0:
        return Markup(escape(text[:2 * width]))
    a, b = max(0, i - width), min(len(text), i + len(q) + width)
    pre, hit, post = text[a:i], text[i:i + len(q)], text[i + len(q):b]
    return Markup(("…" if a else "") + str(escape(pre)) + "<mark>" + str(escape(hit)) + "</mark>" + str(escape(post)) + ("…" if b < len(text) else ""))


def _like(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _register():
    @m.ci_route("search", "/recherche", "/search")
    def ci_search():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        q = (request.args.get("q") or "").strip()[:80]
        groups = {"analyses": [], "drafts": [], "registry": [], "notes": []}
        if len(q) >= 2:
            lk, em, uid = _like(q), (u["email"] or "").lower(), u["id"]
            conn = m.dbm.get_db()
            esc = " ESCAPE '\\'"
            for r in conn.execute(
                    "SELECT a.id, a.title, a.overall, a.text_content, a.user_id FROM contract_analyses a WHERE (a.user_id=? OR a.id IN (SELECT analysis_id FROM ci_analysis_shares WHERE lower(email)=?)) "
                    "AND (a.title LIKE ?%s OR a.text_content LIKE ?%s) ORDER BY a.id DESC LIMIT 20" % (esc, esc), (uid, em, lk, lk)).fetchall():
                src = r["text_content"] if q.lower() in (r["text_content"] or "").lower() else r["title"]
                groups["analyses"].append({"title": r["title"], "url": m.ci_url("analysis", aid=r["id"]), "snip": snippet(src, q), "tag": m.contract_engine.LEVEL_LABELS[g.lang].get(r["overall"], r["overall"]) if False else "", "shared": r["user_id"] != uid})
            for r in conn.execute("SELECT id, title, body_text FROM contract_drafts WHERE user_id=? AND (title LIKE ?%s OR body_text LIKE ?%s) ORDER BY id DESC LIMIT 20" % (esc, esc), (uid, lk, lk)).fetchall():
                src = r["body_text"] if q.lower() in (r["body_text"] or "").lower() else r["title"]
                groups["drafts"].append({"title": r["title"], "url": m.ci_url("draft", did=r["id"]), "snip": snippet(src, q), "shared": False})
            reg_sql = ("SELECT r.id, r.title, r.counterparty, r.notes, r.file_text, r.file_name, r.user_id FROM ci_registry r WHERE (r.user_id=? OR r.id IN (SELECT registry_id FROM ci_shares WHERE lower(email)=?) "
                       "OR r.team_id IN (SELECT team_id FROM ci_team_members WHERE lower(email)=?)) AND (r.title LIKE ?{e} OR r.counterparty LIKE ?{e} OR r.notes LIKE ?{e} OR r.file_text LIKE ?{e} OR r.file_name LIKE ?{e}) "
                       "ORDER BY r.updated_at DESC LIMIT 20").format(e=esc)
            for r in conn.execute(reg_sql, (uid, em, em, lk, lk, lk, lk, lk)).fetchall():
                src = next((r[k] for k in ("title", "counterparty", "notes", "file_name", "file_text") if q.lower() in (r[k] or "").lower()), r["title"])
                groups["registry"].append({"title": r["title"], "url": m.ci_url("registry_entry", rid=r["id"]), "snip": snippet(src, q), "shared": r["user_id"] != uid})
            for r in conn.execute(
                    "SELECT n.analysis_id, n.clause_number, n.body, a.title FROM ci_clause_notes n JOIN contract_analyses a ON a.id=n.analysis_id WHERE (a.user_id=? OR a.id IN (SELECT analysis_id FROM ci_analysis_shares WHERE lower(email)=?)) "
                    "AND n.body LIKE ?%s ORDER BY n.id DESC LIMIT 15" % esc, (uid, em, lk)).fetchall():
                groups["notes"].append({"title": "%s — %s %s" % (r["title"], m._T("section", "section"), r["clause_number"]), "url": m.ci_url("analysis", aid=r["analysis_id"]), "snip": snippet(r["body"], q), "shared": False})
            for r in conn.execute(
                    "SELECT c.registry_id, c.body, r.title FROM ci_comments c JOIN ci_registry r ON r.id=c.registry_id WHERE (r.user_id=? OR r.id IN (SELECT registry_id FROM ci_shares WHERE lower(email)=?) "
                    "OR r.team_id IN (SELECT team_id FROM ci_team_members WHERE lower(email)=?)) AND c.body LIKE ?%s ORDER BY c.id DESC LIMIT 15" % esc, (uid, em, em, lk)).fetchall():
                groups["notes"].append({"title": r["title"], "url": m.ci_url("registry_entry", rid=r["registry_id"]) + "#equipe", "snip": snippet(r["body"], q), "shared": False})
            conn.close()
        total = sum(len(v) for v in groups.values())
        return m._ci_page("search", **m._ci_ctx("home", q=q, groups=groups, total=total))

    @m.ci_route("registry_import", "/registre/importer", "/registry/import", ("POST",))
    def ci_registry_import():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        files = [f for f in request.files.getlist("files") if f and f.filename]
        if not files:
            flash(m._T("Choisissez au moins un fichier.", "Choose at least one file."), "error")
            return m._ci_redirect("registry")
        conn = m.dbm.get_db()
        cap, count, _ok = F._registry_capacity(conn, u)
        done, problems = 0, []
        for f in files[:MAX_IMPORT]:
            ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
            if ext not in F.REGISTRY_EXT:
                problems.append("%s : %s" % (f.filename[:50], m._T("format refusé", "format refused")))
                continue
            if cap is not None and count + done >= cap:
                problems.append("%s : %s" % (f.filename[:50], m._T("limite du compte gratuit atteinte", "free account limit reached")))
                continue
            data = f.read(m.contract_engine.MAX_BYTES + 1)
            if len(data) > m.contract_engine.MAX_BYTES:
                problems.append("%s : %s" % (f.filename[:50], m._T("fichier trop volumineux (5 Mo)", "file too large (5 MB)")))
                continue
            import hashlib
            digest = hashlib.sha256(data).hexdigest()
            name = secure_filename(f.filename) or ("contrat." + ext)
            title = re.sub(r"[_\-]+", " ", name.rsplit(".", 1)[0]).strip()[:160] or name
            now = m.dbm.now()
            cur = conn.execute("INSERT INTO ci_registry (user_id, title, status, created_at, updated_at) VALUES (?,?,?,?,?)", (u["id"], title, "brouillon", now, now))
            rid = cur.lastrowid
            path = os.path.join(F._registry_dir(u["id"]), "%s_%s" % (digest[:16], name))
            ci_crypto.write(path, data)
            conn.execute("UPDATE ci_registry SET file_name=?, file_path=?, file_sha256=?, file_text=? WHERE id=?", (name, path, digest, F.index_text(data, name), rid))
            ci_team.log(conn, rid, u, "created", m._T("(import en lot)", "(bulk import)"))
            done += 1
        if len(files) > MAX_IMPORT:
            problems.append(m._T("%d fichiers ignorés (maximum %d par import)." % (len(files) - MAX_IMPORT, MAX_IMPORT), "%d files skipped (maximum %d per import)." % (len(files) - MAX_IMPORT, MAX_IMPORT)))
        conn.commit()
        conn.close()
        if done:
            flash(m._T("%d contrat(s) importé(s) dans le registre. Complétez chaque fiche (parties, dates)." % done, "%d contract(s) imported into the registry. Complete each record (parties, dates)." % done), "success")
        if problems:
            flash(m._T("Non importés : ", "Not imported: ") + " · ".join(problems), "error")
        return m._ci_redirect("registry")
