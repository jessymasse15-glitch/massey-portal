"""Contract Intelligence : rendu annoté du contrat (risques surlignés dans le texte),
notes par clause partageables, partage d'une analyse, aperçu des fichiers."""
import json
import re

from flask import abort, flash, g, jsonify, redirect, request

import contract_engine

m = None
F = None

LEVEL_RANK = {"eleve": 3, "moyen": 2, "info": 1}
_NUM_LINE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})*)[.)]\s+\S")
SHARE_ROLES = ("viewer", "commenter")
MAX_SHARES = 10


def init(app_module, features_module):
    global m, F
    m, F = app_module, features_module
    _register()


def _email(u):
    try:
        return (u["email"] or "").strip().lower()
    except (KeyError, IndexError):
        return ""


# ---------------------------------------------------------------------------
# Accès
# ---------------------------------------------------------------------------

def analysis_access(conn, u, aid, need="viewer"):
    """(analyse, rôle) ; rôle ∈ owner | commenter | viewer. 404 sans accès, 403 si insuffisant."""
    row = conn.execute("SELECT * FROM contract_analyses WHERE id=?", (aid,)).fetchone()
    role = None
    if row:
        if row["user_id"] == u["id"]:
            role = "owner"
        else:
            sh = conn.execute("SELECT role FROM ci_analysis_shares WHERE analysis_id=? AND lower(email)=?", (aid, _email(u))).fetchone()
            role = sh["role"] if sh else None
    if not role:
        conn.close()
        abort(404)
    rank = {"viewer": 0, "commenter": 1, "owner": 2}
    if rank[role] < rank[need]:
        conn.close()
        abort(403)
    return row, role


# ---------------------------------------------------------------------------
# Texte annoté
# ---------------------------------------------------------------------------

def _locate(text, excerpt):
    ex = (excerpt or "").strip()
    if len(ex) < 12:
        return None
    i = text.find(ex)
    if i >= 0:
        return i, i + len(ex)
    words = ex.split()
    if len(words) < 3:
        return None
    mt = re.search(r"\s+".join(re.escape(w) for w in words), text)
    return (mt.start(), mt.end()) if mt else None


def annotate(text, findings):
    """Découpe le contrat en clauses ; chaque ligne est une suite de segments (texte, index du constat, niveau).
    Retourne (clauses, non_places) : non_places = index des constats dont le passage n'a pas été retrouvé."""
    spans, unplaced = [], []
    for i, f in enumerate(findings):
        loc = _locate(text, f.get("excerpt"))
        if loc:
            spans.append((loc[0], loc[1], i, f.get("level", "info")))
        else:
            unplaced.append(i)
    spans.sort(key=lambda s: (s[0], -LEVEL_RANK.get(s[3], 0)))
    kept, last_end = [], -1
    for s in spans:                      # chevauchements : on garde le premier (le plus grave à début égal)
        if s[0] >= last_end:
            kept.append(s)
            last_end = s[1]
        else:
            unplaced.append(s[2])
    clauses, current, pos = [], None, 0
    for raw in text.split("\n"):
        start, end = pos, pos + len(raw)
        pos = end + 1
        line = raw.strip()
        mt = contract_engine._HEAD_A.match(line) if line else None
        num = mt.group(1) if mt else None
        if not mt and line:
            mb = _NUM_LINE.match(line)
            if mb:
                num = mb.group(1)
        if num is not None or current is None:
            current = {"number": num if num is not None else "—", "title": line if num is not None else "", "lines": [], "level": None, "findings": []}
            clauses.append(current)
        if not line:
            continue
        segs, cur = [], start
        for (a, b, fi, lv) in kept:
            if b <= start or a >= end:
                continue
            a2, b2 = max(a, start), min(b, end)
            if a2 > cur:
                segs.append((text[cur:a2], None, None))
            segs.append((text[a2:b2], fi, lv))
            if fi not in current["findings"]:
                current["findings"].append(fi)
            if LEVEL_RANK.get(lv, 0) > LEVEL_RANK.get(current["level"], 0):
                current["level"] = lv
            cur = b2
        if cur < end:
            segs.append((text[cur:end], None, None))
        current["lines"].append({"segs": segs, "head": num is not None and raw.strip() == current["title"] and len(line) <= 90})
    return clauses, sorted(set(unplaced))


def clause_notes(conn, aid):
    out = {}
    for n in conn.execute("SELECT * FROM ci_clause_notes WHERE analysis_id=? ORDER BY id", (aid,)).fetchall():
        out.setdefault(n["clause_number"], []).append(n)
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _register():
    @m.ci_route("analysis_note", "/analyse/<int:aid>/note", "/analysis/<int:aid>/note", ("POST",))
    def ci_analysis_note(aid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        row, role = analysis_access(conn, u, aid, "commenter")
        number = (request.form.get("clause") or "").strip()[:12]
        body = (request.form.get("body") or "").strip()[:1000]
        if not number or not body:
            flash(m._T("Écrivez une note.", "Write a note."), "error")
        else:
            name = (u["full_name"] or u["email"] or "?")[:80]
            conn.execute("INSERT INTO ci_clause_notes (analysis_id, clause_number, author_id, author_name, body, created_at) VALUES (?,?,?,?,?,?)",
                         (aid, number, u["id"], name, body, m.dbm.now()))
            conn.commit()
            owner = conn.execute("SELECT email FROM users WHERE id=?", (row["user_id"],)).fetchone()
            targets = {s["email"].lower() for s in conn.execute("SELECT email FROM ci_analysis_shares WHERE analysis_id=?", (aid,)).fetchall()}
            if owner:
                targets.add(owner["email"].lower())
            targets.discard(_email(u))
            link = F._site_link(m.ci_url("analysis", aid=aid)) + "#c" + re.sub(r"[^0-9A-Za-z]", "_", number)
            for to in targets:
                F._notify(to, m._T("Note sur une clause : %s" % row["title"], "Clause note: %s" % row["title"]),
                          m._T("%s a écrit sur la section %s de « %s » :\n\n%s\n\n%s\n\n— Massey Contracts & Tax" % (name, number, row["title"], body, link),
                               "%s wrote on section %s of \"%s\":\n\n%s\n\n%s\n\n— Massey Contracts & Tax" % (name, number, row["title"], body, link)))
        conn.close()
        return redirect(m.ci_url("analysis", aid=aid) + "#c" + re.sub(r"[^0-9A-Za-z]", "_", number or ""))

    @m.ci_route("analysis_share", "/analyse/<int:aid>/partager", "/analysis/<int:aid>/share", ("POST",))
    def ci_analysis_share(aid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        row, _r = analysis_access(conn, u, aid, "owner")
        email = (request.form.get("email") or "").strip().lower()[:160]
        role = request.form.get("role") if request.form.get("role") in SHARE_ROLES else "commenter"
        n = conn.execute("SELECT COUNT(*) AS c FROM ci_analysis_shares WHERE analysis_id=?", (aid,)).fetchone()["c"]
        if not F._email_ok(email):
            flash(m._T("Adresse courriel invalide.", "Invalid email address."), "error")
        elif email == _email(u):
            flash(m._T("Cette analyse est déjà la vôtre.", "This analysis is already yours."), "error")
        elif n >= MAX_SHARES and not conn.execute("SELECT 1 FROM ci_analysis_shares WHERE analysis_id=? AND lower(email)=?", (aid, email)).fetchone():
            flash(m._T("Maximum %d personnes." % MAX_SHARES, "Maximum %d people." % MAX_SHARES), "error")
        else:
            ex = conn.execute("SELECT id FROM ci_analysis_shares WHERE analysis_id=? AND lower(email)=?", (aid, email)).fetchone()
            if ex:
                conn.execute("UPDATE ci_analysis_shares SET role=? WHERE id=?", (role, ex["id"]))
            else:
                conn.execute("INSERT INTO ci_analysis_shares (analysis_id, owner_id, email, role, created_at) VALUES (?,?,?,?,?)", (aid, u["id"], email, role, m.dbm.now()))
            conn.commit()
            who = (u["full_name"] or u["email"])
            link = F._site_link(m.ci_url("analysis", aid=aid))
            sent = F._notify(email, m._T("%s a partagé une analyse de contrat avec vous" % who, "%s shared a contract analysis with you" % who),
                             m._T("Bonjour,\n\n%s a partagé avec vous l'analyse « %s » (%s).\n\nOuvrez-la (connectez-vous avec cette adresse courriel) :\n%s\n\n— Massey Contracts & Tax" % (who, row["title"], "lecture et notes" if role == "commenter" else "lecture seule", link),
                                  "Hello,\n\n%s shared the analysis \"%s\" with you (%s).\n\nOpen it (sign in with this email address):\n%s\n\n— Massey Contracts & Tax" % (who, row["title"], "read and notes" if role == "commenter" else "read only", link)))
            flash(m._T("Analyse partagée avec %s." % email, "Analysis shared with %s." % email) + ("" if sent else " " + m._T("(Courriel non envoyé : prévenez la personne.)", "(Email not sent: let the person know.)")), "success")
        conn.close()
        return redirect(m.ci_url("analysis", aid=aid) + "#partage")

    @m.ci_route("analysis_unshare", "/analyse/<int:aid>/partager/<int:shid>/retirer", "/analysis/<int:aid>/share/<int:shid>/remove", ("POST",))
    def ci_analysis_unshare(aid, shid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        analysis_access(conn, u, aid, "owner")
        conn.execute("DELETE FROM ci_analysis_shares WHERE id=? AND analysis_id=?", (shid, aid))
        conn.commit()
        conn.close()
        return redirect(m.ci_url("analysis", aid=aid) + "#partage")

    @m.app.route("/contract-intelligence/apercu", methods=["POST"], endpoint="ci_preview")
    @m.app.route("/en/contract-intelligence/preview", methods=["POST"], endpoint="ci_preview_en")
    def ci_preview():
        u = m._ci_user()
        if not u:
            return jsonify({"ok": False, "error": "login"}), 401
        g.lang = "en" if request.path.startswith("/en/") else "fr"
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"ok": False, "error": m._T("Aucun fichier.", "No file.")}), 400
        data = f.read(contract_engine.MAX_BYTES + 1)
        try:
            text = contract_engine.extract_text(data, f.filename)
        except contract_engine.ExtractionError as exc:
            msgs = {"too_large": m._T("Fichier trop volumineux (5 Mo maximum).", "File too large (5 MB maximum)."),
                    "unsupported": m._T("Format non pris en charge (.docx, .pdf, .txt).", "Unsupported format (.docx, .pdf, .txt)."),
                    "empty": m._T("Aucun texte exploitable : PDF numérisé sans couche texte ? Collez le texte à la place.", "No usable text: scanned PDF without a text layer? Paste the text instead.")}
            return jsonify({"ok": False, "error": msgs.get(exc.code, m._T("Fichier illisible (corrompu ou protégé).", "Unreadable file (corrupted or protected)."))}), 200
        clauses = contract_engine.split_clauses(text)
        return jsonify({"ok": True, "name": f.filename[:120], "words": len(text.split()), "chars": len(text), "clauses": len(clauses),
                        "text": text[:8000], "truncated": len(text) > 8000})
