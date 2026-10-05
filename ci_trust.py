"""Contract Intelligence : confiance — page « méthode et limites », demande de relecture par un avocat
avec devis (côté client et côté administration)."""
from flask import abort, flash, g, redirect, render_template, request, url_for

m = None
F = None

SCOPES = {
    "fr": [("generale", "Relecture générale du contrat"), ("risques", "Seulement les points à risque signalés"),
           ("negociation", "Appui à la négociation (contre-propositions)"), ("haiti", "Conformité au droit haïtien")],
    "en": [("generale", "General review of the contract"), ("risques", "Only the flagged risk points"),
           ("negociation", "Negotiation support (counter-proposals)"), ("haiti", "Compliance with Haitian law")],
}
URGENCY = {"fr": [("normal", "Normale"), ("urgent", "Urgente (sous quelques jours)")], "en": [("normal", "Normal"), ("urgent", "Urgent (within days)")]}
STATUS = {
    "fr": {"new": "Devis en préparation", "quoted": "Devis reçu : à confirmer", "accepted": "Devis accepté", "declined": "Devis refusé", "done": "Relecture terminée"},
    "en": {"new": "Quote being prepared", "quoted": "Quote received: please confirm", "accepted": "Quote accepted", "declined": "Quote declined", "done": "Review completed"},
}


def init(app_module, features_module):
    global m, F
    m, F = app_module, features_module
    _register()


def _staff_emails(conn):
    out = [r["email"] for r in conn.execute("SELECT email FROM users WHERE role IN ('expert','admin') AND email<>''").fetchall()]
    try:
        extra = m.dbm.get_setting("contact_email")
        if extra:
            out.append(extra)
    except Exception:  # noqa: BLE001
        pass
    seen, uniq = set(), []
    for e in out:
        if e.lower() not in seen:
            seen.add(e.lower())
            uniq.append(e)
    return uniq


def _source(conn, u, ref_type, ref_id):
    """(titre, texte, mots, retour_url) d'une analyse, d'un brouillon ou d'une fiche du registre appartenant à l'utilisateur."""
    if ref_type == "registry":
        row = conn.execute("SELECT * FROM ci_registry WHERE id=? AND user_id=?", (ref_id, u["id"])).fetchone()
        if not row:
            conn.close()
            abort(404)
        import ci_extras
        text, _l = ci_extras.signable_text(conn, u, row)
        return row["title"], text or "", len((text or "").split()), m.ci_url("registry_entry", rid=ref_id)
    title, text, _lang = F._ref_text(conn, u, ref_type, ref_id)
    back = m.ci_url("analysis", aid=ref_id) if ref_type == "analysis" else m.ci_url("draft", did=ref_id)
    return title, text, len((text or "").split()), back


def _register():
    @m.ci_route("method", "/methode", "/method")
    def ci_method():
        conn = m.dbm.get_db()
        sources = [dict(id=r["id"], title=r["title"], kind=m.dbm.CORPUS_SOURCE_LABELS.get(r["source_type"], r["source_type"]), reference=r["citation_reference"] or "")
                   for r in conn.execute("SELECT id, title, source_type, citation_reference FROM legal_corpus_documents WHERE ci_enabled=1 ORDER BY title").fetchall()]
        conn.close()
        import ci_ai
        n_rules = len(m.contract_engine.RULES)
        n_topics = len(m.contract_engine.EXPECTED)
        user = m._ci_user()
        return m._ci_page("method", **m._ci_ctx("home", sources=sources, ai_on=ci_ai.is_configured(), n_rules=n_rules, n_topics=n_topics, logged=bool(user)))

    @m.ci_route("review_request", "/relecture", "/lawyer-review", ("GET", "POST"))
    def ci_review_request():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        lang = "en" if g.lang == "en" else "fr"
        ref_type = request.values.get("ref_type", "")
        try:
            ref_id = int(request.values.get("ref_id", "0"))
        except ValueError:
            abort(400)
        if ref_type not in ("analysis", "draft", "registry"):
            abort(404)
        conn = m.dbm.get_db()
        title, text, words, back = _source(conn, u, ref_type, ref_id)
        if request.method == "POST":
            scope = request.form.get("scope") if request.form.get("scope") in dict(SCOPES[lang]) else "generale"
            urgency = request.form.get("urgency") if request.form.get("urgency") in dict(URGENCY[lang]) else "normal"
            if not request.form.get("consent"):
                flash(m._T("Cochez l'autorisation de lecture du contrat.", "Tick the permission to read the contract."), "error")
            elif not text.strip():
                flash(m._T("Aucun texte de contrat à relire.", "No contract text to review."), "error")
            elif conn.execute("SELECT 1 FROM ci_review_requests WHERE user_id=? AND ref_type=? AND ref_id=? AND status IN ('new','quoted','accepted')", (u["id"], ref_type, ref_id)).fetchone():
                flash(m._T("Une demande est déjà en cours pour ce contrat.", "A request is already open for this contract."), "error")
            else:
                cur = conn.execute("INSERT INTO ci_review_requests (user_id, ref_type, ref_id, title, scope, urgency, message, phone, words, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                   (u["id"], ref_type, ref_id, title, scope, urgency, (request.form.get("message") or "").strip()[:1500],
                                    (request.form.get("phone") or "").strip()[:40], words, "new", m.dbm.now()))
                rid = cur.lastrowid
                conn.commit()
                staff = _staff_emails(conn)
                conn.close()
                link = F._site_link("/admin/relectures/%d" % rid)
                for to in staff:
                    F._notify(to, "Nouvelle demande de relecture : " + title,
                              "%s (%s) demande un devis de relecture.\nContrat : %s (%d mots)\nPortée : %s · urgence : %s\n\n%s\n\nRépondre avec un devis : %s"
                              % (u["full_name"], u["email"], title, words, dict(SCOPES["fr"]).get(scope), urgency, request.form.get("message") or "", link))
                F._notify(u["email"], m._T("Votre demande de relecture a bien été reçue", "Your review request was received"),
                          m._T("Bonjour,\n\nNous avons bien reçu votre demande de relecture pour « %s ». Un avocat vous enverra un devis par courriel ; vous pourrez l'accepter ou le refuser depuis Contract Intelligence.\n\n— Massey Contracts & Tax" % title,
                               "Hello,\n\nWe received your review request for \"%s\". A lawyer will email you a quote; you can accept or decline it from Contract Intelligence.\n\n— Massey Contracts & Tax" % title))
                flash(m._T("Demande envoyée. Vous recevrez un devis par courriel.", "Request sent. You will receive a quote by email."), "success")
                return redirect(m.ci_url("reviews"))
        conn.close()
        return m._ci_page("review_request", **m._ci_ctx("home", ref_type=ref_type, ref_id=ref_id, title=title, words=words, back=back,
                                                         scopes=SCOPES[lang], urgencies=URGENCY[lang], user=u))

    @m.ci_route("reviews", "/relectures", "/lawyer-reviews")
    def ci_reviews():
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        rows = conn.execute("SELECT * FROM ci_review_requests WHERE user_id=? ORDER BY id DESC", (u["id"],)).fetchall()
        conn.close()
        lang = "en" if g.lang == "en" else "fr"
        return m._ci_page("reviews", **m._ci_ctx("home", reviews=rows, status_labels=STATUS[lang], scope_labels=dict(SCOPES[lang])))

    @m.ci_route("review_decide", "/relectures/<int:rid>/decider", "/lawyer-reviews/<int:rid>/decide", ("POST",))
    def ci_review_decide(rid):
        u = m._ci_user()
        if not u:
            return m._ci_need_login()
        conn = m.dbm.get_db()
        row = conn.execute("SELECT * FROM ci_review_requests WHERE id=? AND user_id=?", (rid, u["id"])).fetchone()
        if not row:
            conn.close()
            abort(404)
        decision = request.form.get("decision")
        if row["status"] == "quoted" and decision in ("accept", "decline"):
            st = "accepted" if decision == "accept" else "declined"
            conn.execute("UPDATE ci_review_requests SET status=?, decided_at=? WHERE id=?", (st, m.dbm.now(), rid))
            conn.commit()
            for to in _staff_emails(conn):
                F._notify(to, ("Devis accepté : " if st == "accepted" else "Devis refusé : ") + row["title"],
                          "%s a %s le devis (%s) pour « %s ».\n%s" % (u["email"], "accepté" if st == "accepted" else "refusé", row["quote_amount"], row["title"], F._site_link("/admin/relectures/%d" % rid)))
            flash(m._T("Votre réponse a été transmise.", "Your answer was sent."), "success")
        conn.close()
        return redirect(m.ci_url("reviews"))

    # ---- Administration (juristes) ---------------------------------------
    @m.app.route("/admin/relectures", endpoint="ci_admin_reviews")
    @m.roles_required("expert", "admin")
    def ci_admin_reviews():
        conn = m.dbm.get_db()
        rows = conn.execute("SELECT r.*, u.full_name, u.email FROM ci_review_requests r JOIN users u ON u.id=r.user_id ORDER BY (r.status IN ('done','declined')), r.id DESC").fetchall()
        conn.close()
        return render_template("admin/reviews.html", reviews=rows, status_labels=STATUS["fr"], scope_labels=dict(SCOPES["fr"]), detail=None)

    @m.app.route("/admin/relectures/<int:rid>", methods=["GET", "POST"], endpoint="ci_admin_review")
    @m.roles_required("expert", "admin")
    def ci_admin_review(rid):
        conn = m.dbm.get_db()
        row = conn.execute("SELECT r.*, u.full_name, u.email FROM ci_review_requests r JOIN users u ON u.id=r.user_id WHERE r.id=?", (rid,)).fetchone()
        if not row:
            conn.close()
            abort(404)
        if request.method == "POST":
            act = request.form.get("action")
            site = F._site_link(m.ci_url("reviews"))
            if act == "quote":
                amount = (request.form.get("amount") or "").strip()[:60]
                note = (request.form.get("note") or "").strip()[:1500]
                if not amount:
                    flash("Indiquez le montant du devis (avec la devise).", "error")
                else:
                    conn.execute("UPDATE ci_review_requests SET status='quoted', quote_amount=?, quote_note=?, quoted_at=? WHERE id=?", (amount, note, m.dbm.now(), rid))
                    conn.commit()
                    F._notify(row["email"], "Devis de relecture : " + row["title"],
                              "Bonjour %s,\n\nVoici notre devis pour la relecture de « %s » : %s.\n\n%s\n\nAcceptez ou refusez ici : %s\n\n— Massey Contracts & Tax" % (row["full_name"], row["title"], amount, note, site))
                    flash("Devis envoyé au client.", "success")
            elif act == "done":
                conn.execute("UPDATE ci_review_requests SET status='done' WHERE id=?", (rid,))
                conn.commit()
                F._notify(row["email"], "Relecture terminée : " + row["title"], "Bonjour %s,\n\nLa relecture de « %s » est terminée. Nous vous contactons avec nos observations.\n\n— Massey Contracts & Tax" % (row["full_name"], row["title"]))
                flash("Marquée comme terminée.", "success")
            conn.close()
            return redirect(url_for("ci_admin_review", rid=rid))
        text = ""
        if row["ref_type"] == "analysis":
            r = conn.execute("SELECT text_content AS t FROM contract_analyses WHERE id=?", (row["ref_id"],)).fetchone()
        elif row["ref_type"] == "draft":
            r = conn.execute("SELECT body_text AS t FROM contract_drafts WHERE id=?", (row["ref_id"],)).fetchone()
        else:
            reg = conn.execute("SELECT * FROM ci_registry WHERE id=?", (row["ref_id"],)).fetchone()
            owner = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
            import ci_extras
            t, _l = ci_extras.signable_text(conn, owner, reg) if reg else (None, None)
            r = {"t": t}
        text = (r["t"] if r else "") or ""
        conn.close()
        return render_template("admin/reviews.html", reviews=[row], status_labels=STATUS["fr"], scope_labels=dict(SCOPES["fr"]), detail=row, text=text)
