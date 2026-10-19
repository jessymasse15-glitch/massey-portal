"""Routes du corpus juridique : import en lot, réindexation, journal des questions et assistant sourcé."""
import io
import os
import zipfile

from flask import abort, flash, redirect, render_template, request, url_for

import legal_corpus as lc

m = None
EXTS = ("txt", "md", "docx", "pdf")
MAX_FILE = 15 * 1024 * 1024
MAX_ZIP_TOTAL = 100 * 1024 * 1024
MAX_FILES = 300

OUTCOME_LABELS = {"ok": "Réponse sourcée", "insufficient": "Sources insuffisantes", "no_source": "Aucune source",
                  "search_only": "Passages seulement (IA non configurée)"}


def _title_from(name):
    base = os.path.basename(name).rsplit(".", 1)[0]
    return " ".join(base.replace("_", " ").replace("-", " ").split())[:200] or "Sans titre"


def _iter_uploads(files):
    """Produit (nom, octets) pour chaque fichier, en dépliant les archives .zip (bornées)."""
    total = 0
    for f in files:
        if not f or not f.filename:
            continue
        name = f.filename
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        data = f.read(MAX_FILE * 7 + 1)
        if ext == "zip":
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                yield name, None, "archive illisible"
                continue
            infos = [i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX")
                     and i.filename.rsplit(".", 1)[-1].lower() in EXTS]
            for i in infos[:MAX_FILES]:
                total += i.file_size
                if i.file_size > MAX_FILE or total > MAX_ZIP_TOTAL:
                    yield i.filename, None, "trop volumineux"
                    continue
                yield i.filename, zf.read(i), None
        elif ext in EXTS:
            if len(data) > MAX_FILE:
                yield name, None, "trop volumineux (15 Mo max)"
            else:
                yield name, data, None
        else:
            yield name, None, "format non pris en charge"


def init(app_module):
    global m
    m = app_module
    app = m.app
    dbm = m.dbm

    @app.route("/admin/corpus/import", methods=["GET", "POST"])
    @m.roles_required("expert", "admin")
    def admin_corpus_import():
        ctx = dict(source_types=dbm.CORPUS_SOURCE_TYPES, jurisdictions=lc.JURIS_CHOICES,
                   statuses=lc.STATUS_CHOICES, rights_list=lc.RIGHTS_CHOICES)
        if request.method == "GET":
            return render_template("admin/corpus_import.html", **ctx)
        f = request.form
        st = f.get("source_type", "autre")
        st = st if st in m.dbm.CORPUS_SOURCE_LABELS else "autre"
        lang = "en" if f.get("language") == "en" else "fr"
        juris = f.get("jurisdiction", "HT")
        juris = juris if juris in lc.JURIS_FR else "autre"
        status = f.get("status", "inconnu")
        status = status if status in lc.STATUS_FR else "inconnu"
        rights = f.get("rights", "interne")
        rights = rights if rights in lc.RIGHTS_FR else "interne"
        anon = 1 if f.get("anonymized") else 0
        u = m.current_user()
        conn = dbm.get_db()
        have = {r["title"].lower() for r in conn.execute("SELECT title FROM legal_corpus_documents")}
        done, skipped, failed, passages = 0, 0, [], 0
        for name, data, err in _iter_uploads(request.files.getlist("files")):
            if err:
                failed.append("%s (%s)" % (name, err)); continue
            title = _title_from(name)
            if title.lower() in have:
                skipped += 1; continue
            try:
                text = m.contract_engine.extract_text(data, os.path.basename(name), max_chars=2_000_000, max_bytes=MAX_FILE)
            except m.contract_engine.ExtractionError as exc:
                failed.append("%s (%s)" % (name, {"empty": "aucun texte — PDF scanné ?", "unsupported": "format", "too_large": "volumineux"}.get(exc.code, "illisible")))
                continue
            cur = conn.execute(
                "INSERT INTO legal_corpus_documents (title, source_type, citation_reference, full_text, ci_enabled, ci_tags, language, created_by, created_at, updated_at, "
                "status, jurisdiction, rights, anonymized) VALUES (?,?,?,?,0,'',?,?,?,?,?,?,?,?)",
                (title, st, title, text, lang, u["id"], dbm.now(), dbm.now(), status, juris, rights, anon))
            conn.commit()
            passages += lc.index_document(conn, cur.lastrowid)
            have.add(title.lower()); done += 1
        conn.close()
        dbm.log_activity(u["id"], "corpus_batch_import", "%d textes" % done)
        flash("%d texte(s) importé(s), %d passage(s) indexé(s)%s." % (done, passages, ", %d déjà présent(s) ignoré(s)" % skipped if skipped else ""), "success")
        if failed:
            flash("Non importés : " + " ; ".join(failed[:8]) + (" …" if len(failed) > 8 else ""), "error")
        return redirect(url_for("admin_corpus_list"))

    @app.route("/admin/corpus/reindex", methods=["POST"])
    @m.roles_required("admin")
    def admin_corpus_reindex():
        conn = dbm.get_db()
        n = lc.reindex_all(conn)
        conn.close()
        flash("%d texte(s) réindexé(s)." % n, "success")
        return redirect(url_for("admin_corpus_list"))

    @app.route("/admin/corpus/<int:doc_id>/reindex", methods=["POST"])
    @m.roles_required("expert", "admin")
    def admin_corpus_reindex_one(doc_id):
        conn = dbm.get_db()
        if conn.execute("SELECT 1 FROM legal_corpus_documents WHERE id=?", (doc_id,)).fetchone() is None:
            conn.close(); abort(404)
        n = lc.index_document(conn, doc_id)
        conn.close()
        flash("%d passage(s) indexé(s)." % n, "success")
        return redirect(url_for("admin_corpus_view", doc_id=doc_id))

    @app.route("/admin/corpus/questions")
    @m.roles_required("expert", "admin")
    def admin_corpus_questions():
        conn = dbm.get_db()
        rows = conn.execute("SELECT * FROM legal_questions ORDER BY id DESC LIMIT 300").fetchall()
        conn.close()
        return render_template("admin/corpus_questions.html", rows=rows, labels=OUTCOME_LABELS)

    # ------------------------------------------------------------ assistant
    def _limit_for(u):
        if u["role"] in ("expert", "admin"):
            return None
        premium = m._access_info(u)["is_premium"]
        try:
            return int(os.environ.get("LEGAL_QA_DAILY_PREMIUM" if premium else "LEGAL_QA_DAILY_FREE", "50" if premium else "5"))
        except ValueError:
            return 50 if premium else 5

    def _used_today(conn, uid):
        return conn.execute("SELECT COUNT(*) FROM legal_questions WHERE user_id=? AND model_called=1 AND substr(created_at,1,10)=?",
                            (uid, dbm.now()[:10])).fetchone()[0]

    def _assistant(en):
        u = m.current_user()
        if u is None:
            flash("Connectez-vous pour interroger l'assistant juridique." if not en else "Sign in to use the legal assistant.", "error")
            return redirect(url_for("login", next=request.path))
        conn = dbm.get_db()
        limit = _limit_for(u)
        used = _used_today(conn, u["id"])
        ctx = dict(en=en, result=None, question="", limit=limit, used=used, include_old=False,
                   source_types=dbm.CORPUS_SOURCE_TYPES_EN if en else dbm.CORPUS_SOURCE_TYPES, source_type="",
                   status_labels=lc.STATUS_EN if en else lc.STATUS_FR, juris_labels=lc.JURIS_EN if en else lc.JURIS_FR,
                   ai_on=m.ci_ai.is_configured(), stats=lc.stats(conn), outcome_labels=OUTCOME_LABELS)
        if request.method == "POST":
            q = request.form.get("question", "").strip()[:1500]
            ctx.update(question=q, include_old=bool(request.form.get("anciens")), source_type=request.form.get("source_type", ""))
            if ctx["source_type"] not in m.dbm.CORPUS_SOURCE_LABELS:
                ctx["source_type"] = ""
            if len(q) < 8:
                flash("Posez une question complète (quelques mots au moins)." if not en else "Please ask a complete question.", "error")
            elif limit is not None and used >= limit:
                ctx["result"] = {"outcome": "quota"}
            else:
                res = lc.answer_question(conn, q, "en" if en else "fr", source_type=ctx["source_type"] or None,
                                         include_inactive=ctx["include_old"])
                conn.execute("INSERT INTO legal_questions (user_id, question, lang, outcome, n_sources, model_called, created_at) VALUES (?,?,?,?,?,?,?)",
                             (u["id"], q, "en" if en else "fr", res["outcome"], len(res["passages"]), 1 if res["model_called"] else 0, dbm.now()))
                conn.commit()
                ctx["result"] = res
                ctx["used"] = used + (1 if res["model_called"] else 0)
        conn.close()
        return render_template("legal/assistant.html", **ctx)

    @app.route("/assistant-juridique", methods=["GET", "POST"])
    def legal_assistant():
        return _assistant(False)

    @app.route("/en/legal-assistant", methods=["GET", "POST"])
    def legal_assistant_en():
        return _assistant(True)
