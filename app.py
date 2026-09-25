import os
import re
import secrets
import functools
import unicodedata
import difflib
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash,
    send_from_directory, send_file, abort, g, Response
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import db as dbm
import totp
import payments
import signing
import notifications

APP_ROOT = os.path.dirname(__file__)
# En production (Render), DATA_DIR pointe vers le disque persistant unique
# (un seul disque autorisé par service) ; les fichiers vivent alors sous
# DATA_DIR/uploads plutôt que static/uploads. En local, ça retombe sur
# static/uploads comme avant.
if os.environ.get("DATA_DIR"):
    UPLOAD_DIR = os.path.join(os.environ["DATA_DIR"], "uploads")
else:
    UPLOAD_DIR = os.path.join(APP_ROOT, "static", "uploads")
ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "png", "jpg", "jpeg", "xls", "xlsx", "txt"}
MAX_CONTENT_LENGTH = 15 * 1024 * 1024  # 15 Mo par fichier

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
os.makedirs(UPLOAD_DIR, exist_ok=True)

with app.app_context():
    dbm.init_db()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def current_user():
    if "user_id" not in session:
        return None
    if not hasattr(g, "_user"):
        conn = dbm.get_db()
        row = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        conn.close()
        g._user = row
    return g._user


# Correspondance entre une page française et son équivalent anglais (et
# inversement), utilisée pour construire le lien du sélecteur de langue dans
# l'en-tête. Seules les pages vitrines/publiques ont un équivalent anglais à
# ce stade (le portail client, l'admin et la revue restent en français).
LANG_COUNTERPART = {
    "home": "home_en", "home_en": "home",
    "about": "about_en", "about_en": "about",
    "expertise": "expertise_en", "expertise_en": "expertise",
    "tarifs": "tarifs_en", "tarifs_en": "tarifs",
    "pillar_legal_intelligence": "pillar_legal_intelligence_en", "pillar_legal_intelligence_en": "pillar_legal_intelligence",
    "pillar_contract_intelligence": "pillar_contract_intelligence_en", "pillar_contract_intelligence_en": "pillar_contract_intelligence",
    "pillar_transaction_intelligence": "pillar_transaction_intelligence_en", "pillar_transaction_intelligence_en": "pillar_transaction_intelligence",
    "pillar_tax_intelligence": "pillar_tax_intelligence_en", "pillar_tax_intelligence_en": "pillar_tax_intelligence",
    "pillar_regulatory_compliance": "pillar_regulatory_compliance_en", "pillar_regulatory_compliance_en": "pillar_regulatory_compliance",
    "contact": "contact_en", "contact_en": "contact",
    "faq": "faq_en", "faq_en": "faq",
    "securite_conformite": "securite_conformite_en", "securite_conformite_en": "securite_conformite",
    "mentions_legales": "mentions_legales_en", "mentions_legales_en": "mentions_legales",
    "confidentialite": "confidentialite_en", "confidentialite_en": "confidentialite",
    "conditions_utilisation": "conditions_utilisation_en", "conditions_utilisation_en": "conditions_utilisation",
}


@app.before_request
def _set_lang():
    g.lang = "en" if request.path == "/en" or request.path.startswith("/en/") else "fr"


@app.context_processor
def inject_user():
    counterpart = LANG_COUNTERPART.get(request.endpoint)
    lang_switch_url = None
    if counterpart:
        try:
            lang_switch_url = url_for(counterpart)
        except Exception:
            lang_switch_url = None
    return {
        "current_user": current_user(), "status_labels": dbm.STATUS_LABELS,
        "lang": getattr(g, "lang", "fr"), "lang_switch_url": lang_switch_url,
        "site_settings": dbm.get_all_settings(),
    }


EXEMPT_FROM_MFA_ENFORCEMENT = {"mfa_setup_prompt", "logout", "static"}


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if u is None:
            flash("Connectez-vous pour accéder à cette page.", "error")
            return redirect(url_for("login", next=request.path))
        if u["role"] in ("expert", "admin") and not u["mfa_enabled"] and request.endpoint not in EXEMPT_FROM_MFA_ENFORCEMENT:
            flash("L'authentification à deux facteurs est obligatoire pour votre rôle.", "error")
            return redirect(url_for("mfa_setup_prompt"))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            u = current_user()
            if u is None:
                return redirect(url_for("login", next=request.path))
            if u["role"] in ("expert", "admin") and not u["mfa_enabled"]:
                flash("L'authentification à deux facteurs est obligatoire pour votre rôle.", "error")
                return redirect(url_for("mfa_setup_prompt"))
            if u["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def can_view_dossier(user, dossier):
    if user["role"] in ("expert", "admin"):
        return True
    return dossier["client_id"] == user["id"]


# ---------------------------------------------------------------------------
# Marketing / public pages
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return render_template("marketing/home.html", corpus_source_types=dbm.CORPUS_SOURCE_TYPES)


@app.route("/en/")
def home_en():
    return render_template("marketing/en/home.html", corpus_source_types=dbm.CORPUS_SOURCE_TYPES_EN)


@app.route("/a-propos")
def about():
    return render_template("marketing/about.html")


@app.route("/en/about")
def about_en():
    return render_template("marketing/en/about.html")


@app.route("/expertise")
def expertise():
    return render_template("marketing/expertise.html")


@app.route("/en/expertise")
def expertise_en():
    return render_template("marketing/en/expertise.html")


@app.route("/tarifs")
def tarifs():
    return render_template("marketing/tarifs.html")


@app.route("/en/pricing")
def tarifs_en():
    return render_template("marketing/en/tarifs.html")


@app.route("/espace-client")
def espace_client_marketing():
    return render_template("marketing/espace_client.html")


# ---------------------------------------------------------------------------
# Plateforme — les 5 piliers Massey AI
# ---------------------------------------------------------------------------

def _legal_intelligence_context():
    q = request.args.get("q", "").strip()
    source_type = request.args.get("source_type", "")
    results = []
    if q or source_type:
        conn = dbm.get_db()
        query = "SELECT * FROM legal_corpus_documents WHERE 1=1"
        params = []
        if q:
            query += " AND (title LIKE ? OR full_text LIKE ? OR citation_reference LIKE ?)"
            like = f"%{q}%"
            params += [like, like, like]
        if source_type:
            query += " AND source_type=?"
            params.append(source_type)
        query += " ORDER BY created_at DESC LIMIT 30"
        results = conn.execute(query, params).fetchall()
        conn.close()
    return dict(
        q=q, source_type=source_type, results=results,
        source_types=dbm.CORPUS_SOURCE_TYPES, searched=bool(q or source_type),
    )


@app.route("/legal-intelligence")
def pillar_legal_intelligence():
    return render_template("marketing/pillar_legal.html", **_legal_intelligence_context())


@app.route("/en/legal-intelligence")
def pillar_legal_intelligence_en():
    ctx = _legal_intelligence_context()
    ctx["source_types"] = dbm.CORPUS_SOURCE_TYPES_EN
    return render_template("marketing/en/pillar_legal.html", **ctx)


def _contract_intelligence_context():
    conn = dbm.get_db()
    category = request.args.get("category", "")
    query = "SELECT * FROM contract_clauses WHERE 1=1"
    params = []
    if category:
        query += " AND category=?"
        params.append(category)
    query += " ORDER BY created_at ASC"
    clauses = conn.execute(query, params).fetchall()
    my_documents = []
    u = current_user()
    if u:
        my_documents = conn.execute(
            "SELECT doc.*, d.title AS dossier_title, d.id AS dossier_id FROM documents doc "
            "JOIN dossiers d ON d.id=doc.dossier_id WHERE doc.category='contrat' AND "
            + ("1=1" if u["role"] in ("expert", "admin") else "d.client_id=?")
            + " ORDER BY doc.created_at DESC LIMIT 20",
            [] if u["role"] in ("expert", "admin") else [u["id"]],
        ).fetchall()
    conn.close()
    return dict(
        clauses=clauses, category=category,
        categories=dbm.CLAUSE_CATEGORIES, risk_labels=dbm.CLAUSE_RISK_LABELS,
        my_documents=my_documents,
    )


@app.route("/contract-intelligence")
def pillar_contract_intelligence():
    return render_template("marketing/pillar_contract.html", **_contract_intelligence_context())


@app.route("/en/contract-intelligence")
def pillar_contract_intelligence_en():
    ctx = _contract_intelligence_context()
    ctx["categories"] = dbm.CLAUSE_CATEGORIES_EN
    ctx["risk_labels"] = dbm.CLAUSE_RISK_LABELS_EN
    return render_template("marketing/en/pillar_contract.html", **ctx)


def _transaction_intelligence_context():
    u = current_user()
    conn = dbm.get_db()
    by_stage = {key: [] for key, _ in dbm.TRANSACTION_STAGES}
    if u:
        if u["role"] in ("expert", "admin"):
            dossiers = conn.execute(
                "SELECT d.*, c.full_name AS client_name FROM dossiers d JOIN users c ON c.id=d.client_id ORDER BY d.updated_at DESC"
            ).fetchall()
        else:
            dossiers = conn.execute(
                "SELECT d.*, c.full_name AS client_name FROM dossiers d JOIN users c ON c.id=d.client_id WHERE d.client_id=? ORDER BY d.updated_at DESC",
                (u["id"],),
            ).fetchall()
        for d in dossiers:
            stage = dbm.STATUS_TO_STAGE.get(d["status"], "creer")
            by_stage[stage].append(d)
    conn.close()
    return dict(
        stages=dbm.TRANSACTION_STAGES, by_stage=by_stage,
        logged_in=bool(u), is_staff=bool(u and u["role"] in ("expert", "admin")),
    )


@app.route("/transaction-intelligence")
def pillar_transaction_intelligence():
    return render_template("marketing/pillar_transaction.html", **_transaction_intelligence_context())


@app.route("/en/transaction-intelligence")
def pillar_transaction_intelligence_en():
    ctx = _transaction_intelligence_context()
    ctx["stages"] = dbm.TRANSACTION_STAGES_EN  # mêmes clés que TRANSACTION_STAGES, libellés EN
    return render_template("marketing/en/pillar_transaction.html", **ctx)


def _tax_intelligence_context():
    u = current_user()
    conn = dbm.get_db()
    obligations = []
    if u:
        if u["role"] in ("expert", "admin"):
            obligations = conn.execute(
                "SELECT t.*, d.title AS dossier_title, d.id AS dossier_id FROM tax_obligations t "
                "JOIN dossiers d ON d.id=t.dossier_id ORDER BY (t.due_date IS NULL), t.due_date ASC LIMIT 40"
            ).fetchall()
        else:
            obligations = conn.execute(
                "SELECT t.*, d.title AS dossier_title, d.id AS dossier_id FROM tax_obligations t "
                "JOIN dossiers d ON d.id=t.dossier_id WHERE d.client_id=? ORDER BY (t.due_date IS NULL), t.due_date ASC",
                (u["id"],),
            ).fetchall()
    conn.close()
    return dict(
        obligations=obligations, logged_in=bool(u),
        tax_type_labels=dbm.TAX_TYPE_LABELS, tax_status_labels=dbm.TAX_OBLIGATION_STATUS_LABELS,
    )


@app.route("/tax-intelligence")
def pillar_tax_intelligence():
    return render_template("marketing/pillar_tax.html", **_tax_intelligence_context())


@app.route("/en/tax-intelligence")
def pillar_tax_intelligence_en():
    ctx = _tax_intelligence_context()
    ctx["tax_type_labels"] = dbm.TAX_TYPE_LABELS_EN
    ctx["tax_status_labels"] = dbm.TAX_STATUS_LABELS_EN
    return render_template("marketing/en/pillar_tax.html", **ctx)


def _regulatory_compliance_context():
    u = current_user()
    conn = dbm.get_db()
    items = []
    if u:
        if u["role"] in ("expert", "admin"):
            items = conn.execute(
                "SELECT c.*, d.title AS dossier_title, d.id AS dossier_id FROM compliance_items c "
                "JOIN dossiers d ON d.id=c.dossier_id ORDER BY c.created_at DESC LIMIT 40"
            ).fetchall()
        else:
            items = conn.execute(
                "SELECT c.*, d.title AS dossier_title, d.id AS dossier_id FROM compliance_items c "
                "JOIN dossiers d ON d.id=c.dossier_id WHERE d.client_id=? ORDER BY c.created_at DESC",
                (u["id"],),
            ).fetchall()
    conn.close()
    return dict(
        items=items, logged_in=bool(u),
        compliance_category_labels=dbm.COMPLIANCE_CATEGORY_LABELS, compliance_status_labels=dbm.COMPLIANCE_STATUS_LABELS,
    )


@app.route("/regulatory-compliance")
def pillar_regulatory_compliance():
    return render_template("marketing/pillar_compliance.html", **_regulatory_compliance_context())


@app.route("/en/regulatory-compliance")
def pillar_regulatory_compliance_en():
    ctx = _regulatory_compliance_context()
    ctx["compliance_category_labels"] = dbm.COMPLIANCE_CATEGORY_LABELS_EN
    ctx["compliance_status_labels"] = dbm.COMPLIANCE_STATUS_LABELS_EN
    return render_template("marketing/en/pillar_compliance.html", **ctx)


# ---------------------------------------------------------------------------
# Pages institutionnelles — contact, FAQ, sécurité, mentions légales
# ---------------------------------------------------------------------------

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        if name and email and message:
            conn = dbm.get_db()
            conn.execute(
                "INSERT INTO contact_messages (name, email, subject, message, created_at) VALUES (?,?,?,?,?)",
                (name, email, subject, message, dbm.now()),
            )
            conn.commit()
            conn.close()
            dbm.log_activity(None, "contact_message_received", f"{name} <{email}>")
            flash(
                "Votre message a été envoyé. Nous vous répondrons dans les meilleurs délais." if g.lang == "fr"
                else "Your message has been sent. We will get back to you shortly.",
                "success",
            )
            return redirect(url_for("contact_en") if g.lang == "en" else url_for("contact"))
        flash(
            "Merci de remplir tous les champs obligatoires." if g.lang == "fr"
            else "Please fill in all required fields.",
            "error",
        )
    return render_template("marketing/en/contact.html" if g.lang == "en" else "marketing/contact.html")


@app.route("/en/contact", methods=["GET", "POST"])
def contact_en():
    return contact()


@app.route("/faq")
def faq():
    return render_template("marketing/faq.html")


@app.route("/en/faq")
def faq_en():
    return render_template("marketing/en/faq.html")


@app.route("/securite-conformite")
def securite_conformite():
    return render_template("marketing/securite_conformite.html")


@app.route("/en/security")
def securite_conformite_en():
    return render_template("marketing/en/securite_conformite.html")


@app.route("/mentions-legales")
def mentions_legales():
    return render_template("marketing/mentions_legales.html")


@app.route("/en/legal-notice")
def mentions_legales_en():
    return render_template("marketing/en/mentions_legales.html")


@app.route("/confidentialite")
def confidentialite():
    return render_template("marketing/confidentialite.html")


@app.route("/en/privacy-policy")
def confidentialite_en():
    return render_template("marketing/en/confidentialite.html")


@app.route("/conditions-utilisation")
def conditions_utilisation():
    return render_template("marketing/conditions_utilisation.html")


@app.route("/en/terms-of-service")
def conditions_utilisation_en():
    return render_template("marketing/en/conditions_utilisation.html")


@app.route("/admin/parametres", methods=["GET", "POST"])
@roles_required("admin")
def admin_settings():
    if request.method == "POST":
        for key, _label in dbm.SITE_SETTINGS_LABELS:
            dbm.set_setting(key, request.form.get(key, "").strip())
        flash("Paramètres du site mis à jour.", "success")
        return redirect(url_for("admin_settings"))
    return render_template(
        "admin/settings.html", settings=dbm.get_all_settings(), fields=dbm.SITE_SETTINGS_LABELS,
    )


@app.route("/admin/messages")
@roles_required("expert", "admin")
def admin_contact_messages():
    conn = dbm.get_db()
    messages = conn.execute("SELECT * FROM contact_messages ORDER BY handled ASC, created_at DESC").fetchall()
    conn.close()
    return render_template("admin/contact_messages.html", messages=messages)


@app.route("/admin/messages/<int:message_id>/traiter", methods=["POST"])
@roles_required("expert", "admin")
def admin_mark_message_handled(message_id):
    conn = dbm.get_db()
    conn.execute("UPDATE contact_messages SET handled=1 WHERE id=?", (message_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_contact_messages"))


@app.route("/robots.txt")
def robots_txt():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /portail",
        "Disallow: /admin",
        "Disallow: /compte",
        f"Sitemap: {request.url_root.rstrip('/')}/sitemap.xml",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    pages = [
        "/", "/a-propos", "/expertise", "/tarifs", "/espace-client", "/rendez-vous", "/connexion", "/inscription",
        "/legal-intelligence", "/contract-intelligence", "/transaction-intelligence", "/tax-intelligence", "/regulatory-compliance",
        "/revue", "/revue/articles", "/revue/forum", "/revue/references", "/revue/a-propos",
        "/revue/soumissions", "/revue/abonnement", "/revue/medias",
        "/contact", "/faq", "/securite-conformite", "/mentions-legales", "/confidentialite", "/conditions-utilisation",
        "/en/", "/en/about", "/en/expertise", "/en/pricing",
        "/en/legal-intelligence", "/en/contract-intelligence", "/en/transaction-intelligence", "/en/tax-intelligence", "/en/regulatory-compliance",
        "/en/contact", "/en/faq", "/en/security", "/en/legal-notice", "/en/privacy-policy", "/en/terms-of-service",
    ]
    root = request.url_root.rstrip("/")
    urls = "".join(f"<url><loc>{root}{p}</loc></url>" for p in pages)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(xml, mimetype="application/xml")


# ---------------------------------------------------------------------------
# Massey Law Review — revue juridique de la LegalTech
# ---------------------------------------------------------------------------

def _slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or secrets.token_hex(4)


@app.route("/revue")
def revue_home():
    conn = dbm.get_db()
    articles = conn.execute(
        "SELECT * FROM review_articles WHERE published=1 ORDER BY published_at DESC LIMIT 3"
    ).fetchall()
    posts = conn.execute(
        "SELECT p.*, u.full_name FROM review_forum_posts p JOIN users u ON u.id=p.user_id "
        "WHERE p.hidden=0 ORDER BY p.created_at DESC LIMIT 3"
    ).fetchall()
    conn.close()
    return render_template("revue/home.html", articles=articles, posts=posts)


@app.route("/revue/infolettre", methods=["POST"])
def revue_newsletter_signup():
    email = request.form.get("email", "").strip().lower()
    if email and "@" in email:
        conn = dbm.get_db()
        try:
            conn.execute(
                "INSERT INTO review_subscribers (email, created_at) VALUES (?,?)",
                (email, dbm.now()),
            )
            conn.commit()
            flash("Inscription confirmée — vous recevrez les nouvelles publications par courriel.", "success")
        except Exception:
            flash("Cette adresse est déjà inscrite.", "error")
        conn.close()
    else:
        flash("Adresse courriel invalide.", "error")
    return redirect(request.referrer or url_for("revue_home"))


@app.route("/revue/articles")
def revue_articles():
    conn = dbm.get_db()
    articles = conn.execute(
        "SELECT * FROM review_articles WHERE published=1 ORDER BY published_at DESC"
    ).fetchall()
    conn.close()
    return render_template("revue/articles.html", articles=articles)


@app.route("/revue/articles/<slug>")
def revue_article_detail(slug):
    conn = dbm.get_db()
    article = conn.execute(
        "SELECT * FROM review_articles WHERE slug=? AND published=1", (slug,)
    ).fetchone()
    conn.close()
    if article is None:
        abort(404)
    return render_template("revue/article_detail.html", article=article)


@app.route("/revue/articles/<slug>/pdf")
def revue_article_pdf(slug):
    conn = dbm.get_db()
    article = conn.execute(
        "SELECT * FROM review_articles WHERE slug=? AND published=1", (slug,)
    ).fetchone()
    conn.close()
    if article is None or not article["pdf_stored_name"]:
        abort(404)
    return send_from_directory(
        os.path.join(UPLOAD_DIR, "revue", "articles"), article["pdf_stored_name"],
        as_attachment=True, download_name=article["pdf_original_name"] or f"{slug}.pdf",
    )


@app.route("/revue/rss.xml")
def revue_rss():
    conn = dbm.get_db()
    articles = conn.execute(
        "SELECT * FROM review_articles WHERE published=1 ORDER BY published_at DESC LIMIT 30"
    ).fetchall()
    conn.close()
    root = request.url_root.rstrip("/")
    items = "".join(
        f"<item><title>{a['title']}</title><link>{root}/revue/articles/{a['slug']}</link>"
        f"<guid>{root}/revue/articles/{a['slug']}</guid>"
        f"<pubDate>{a['published_at'] or a['created_at']}</pubDate>"
        f"<description><![CDATA[{a['abstract'] or ''}]]></description></item>"
        for a in articles
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>Massey Law Review</title><link>{root}/revue</link>"
        "<description>Revue juridique de Massey Contracts &amp; Tax</description>"
        f"{items}</channel></rss>"
    )
    return Response(xml, mimetype="application/rss+xml")


@app.route("/revue/forum")
def revue_forum():
    conn = dbm.get_db()
    posts = conn.execute(
        "SELECT p.*, u.full_name FROM review_forum_posts p JOIN users u ON u.id=p.user_id "
        "WHERE p.hidden=0 ORDER BY p.created_at DESC"
    ).fetchall()
    conn.close()
    return render_template("revue/forum.html", posts=posts)


@app.route("/revue/forum/nouveau", methods=["POST"])
@login_required
def revue_forum_post():
    u = current_user()
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    if title and body:
        conn = dbm.get_db()
        conn.execute(
            "INSERT INTO review_forum_posts (user_id, title, body, created_at) VALUES (?,?,?,?)",
            (u["id"], title, body, dbm.now()),
        )
        conn.commit()
        conn.close()
        dbm.log_activity(u["id"], "revue_forum_post", title)
        flash("Billet publié sur le forum.", "success")
    else:
        flash("Titre et message requis.", "error")
    return redirect(url_for("revue_forum"))


@app.route("/revue/forum/<int:post_id>/masquer", methods=["POST"])
@roles_required("expert", "admin")
def revue_forum_hide(post_id):
    conn = dbm.get_db()
    conn.execute("UPDATE review_forum_posts SET hidden=1 WHERE id=?", (post_id,))
    conn.commit()
    conn.close()
    flash("Billet masqué.", "success")
    return redirect(url_for("revue_forum"))


@app.route("/revue/references")
def revue_references():
    return render_template("revue/references.html")


@app.route("/revue/a-propos")
def revue_about():
    return render_template("revue/about.html")


@app.route("/revue/medias")
def revue_medias():
    return render_template("revue/medias.html")


@app.route("/revue/forum/admin/nouveau", methods=["POST"])
@roles_required("expert", "admin")
def admin_revue_forum_new():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    if not title or not body:
        flash("Titre et message sont requis.", "error")
        return redirect(url_for("admin_revue_forum"))
    conn = dbm.get_db()
    conn.execute(
        "INSERT INTO review_forum_posts (user_id, title, body, created_at) VALUES (?,?,?,?)",
        (current_user()["id"], title, body, dbm.now()),
    )
    conn.commit()
    conn.close()
    flash("Billet publié sur le forum.", "success")
    return redirect(url_for("admin_revue_forum"))


@app.route("/revue/forum/admin")
@roles_required("expert", "admin")
def admin_revue_forum():
    conn = dbm.get_db()
    posts = conn.execute(
        "SELECT p.*, u.full_name FROM review_forum_posts p JOIN users u ON u.id = p.user_id ORDER BY p.created_at DESC"
    ).fetchall()
    conn.close()
    return render_template("admin/revue_forum.html", posts=posts)


@app.route("/revue/forum/admin/<int:post_id>/masquer", methods=["POST"])
@roles_required("expert", "admin")
def admin_revue_forum_hide(post_id):
    conn = dbm.get_db()
    conn.execute("UPDATE review_forum_posts SET hidden=1 WHERE id=?", (post_id,))
    conn.commit()
    conn.close()
    flash("Billet masqué.", "success")
    return redirect(url_for("admin_revue_forum"))


@app.route("/revue/forum/admin/<int:post_id>/afficher", methods=["POST"])
@roles_required("expert", "admin")
def admin_revue_forum_unhide(post_id):
    conn = dbm.get_db()
    conn.execute("UPDATE review_forum_posts SET hidden=0 WHERE id=?", (post_id,))
    conn.commit()
    conn.close()
    flash("Billet réaffiché.", "success")
    return redirect(url_for("admin_revue_forum"))


@app.route("/revue/forum/admin/<int:post_id>/supprimer", methods=["POST"])
@roles_required("expert", "admin")
def admin_revue_forum_delete(post_id):
    conn = dbm.get_db()
    conn.execute("DELETE FROM review_forum_posts WHERE id=?", (post_id,))
    conn.commit()
    conn.close()
    flash("Billet supprimé.", "success")
    return redirect(url_for("admin_revue_forum"))


@app.route("/revue/soumissions", methods=["GET", "POST"])
def revue_submissions():
    if request.method == "POST":
        author_name = request.form.get("author_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        title = request.form.get("title", "").strip()
        abstract = request.form.get("abstract", "").strip()
        file = request.files.get("file")

        if not (author_name and email and title and file and file.filename):
            flash("Merci de remplir tous les champs obligatoires et de joindre un fichier.", "error")
            return redirect(url_for("revue_submissions"))
        if not allowed_file(file.filename):
            flash("Type de fichier non autorisé (formats acceptés : pdf, doc, docx).", "error")
            return redirect(url_for("revue_submissions"))

        original_name = secure_filename(file.filename)
        ext = original_name.rsplit(".", 1)[1].lower()
        stored_name = f"{secrets.token_hex(16)}.{ext}"
        sub_dir = os.path.join(UPLOAD_DIR, "revue", "soumissions")
        os.makedirs(sub_dir, exist_ok=True)
        file.save(os.path.join(sub_dir, stored_name))

        u = current_user()
        conn = dbm.get_db()
        conn.execute(
            "INSERT INTO review_submissions (user_id, author_name, email, title, abstract, stored_name, original_name, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (u["id"] if u else None, author_name, email, title, abstract, stored_name, original_name, dbm.now()),
        )
        conn.commit()
        conn.close()
        flash("Votre soumission a été reçue. Le comité éditorial vous contactera à l'adresse fournie.", "success")
        return redirect(url_for("revue_submissions"))

    return render_template("revue/submissions.html")


@app.route("/revue/abonnement")
def revue_subscription():
    u = current_user()
    active = None
    if u:
        conn = dbm.get_db()
        active = conn.execute(
            "SELECT * FROM review_subscriptions WHERE user_id=? AND status='paid' ORDER BY paid_at DESC LIMIT 1",
            (u["id"],),
        ).fetchone()
        conn.close()
    return render_template(
        "revue/subscription.html", plans=dbm.REVIEW_PLANS, active=active, plan_labels=dbm.REVIEW_PLAN_LABELS
    )


@app.route("/revue/abonnement/nouveau", methods=["POST"])
@login_required
def revue_subscription_new():
    u = current_user()
    plan = request.form.get("plan", "lecteur")
    plan_info = dict((p[0], p) for p in dbm.REVIEW_PLANS).get(plan)
    if not plan_info:
        flash("Formule invalide.", "error")
        return redirect(url_for("revue_subscription"))
    _, plan_label, amount_cents, _ = plan_info

    conn = dbm.get_db()
    cur = conn.execute(
        "INSERT INTO review_subscriptions (user_id, plan, status, created_at) VALUES (?,?,?,?)",
        (u["id"], plan, "pending", dbm.now()),
    )
    sub_id = cur.lastrowid
    conn.commit()

    if not payments.is_configured():
        conn.close()
        flash("Le paiement en ligne n'est pas encore configuré (clés Stripe manquantes).", "error")
        return redirect(url_for("revue_subscription"))

    try:
        success_url = url_for("revue_subscription_success", sub_id=sub_id, _external=True) + "&session_id={CHECKOUT_SESSION_ID}"
        cancel_url = url_for("revue_subscription_cancel", sub_id=sub_id, _external=True)
        checkout = payments.create_checkout_session(
            amount_cents=amount_cents, currency="cad",
            description=f"{plan_label} (mensuel)",
            success_url=success_url, cancel_url=cancel_url,
            client_email=u["email"],
            metadata={"review_subscription_id": sub_id},
        )
        conn.execute("UPDATE review_subscriptions SET provider_session_id=? WHERE id=?", (checkout["id"], sub_id))
        conn.commit()
        conn.close()
        return redirect(checkout["url"])
    except (payments.PaymentNotConfigured, payments.PaymentProviderError) as exc:
        conn.execute("UPDATE review_subscriptions SET status='failed' WHERE id=?", (sub_id,))
        conn.commit()
        conn.close()
        flash(str(exc), "error")
        return redirect(url_for("revue_subscription"))


@app.route("/revue/abonnement/succes")
@login_required
def revue_subscription_success():
    flash("Paiement en cours de confirmation — votre abonnement sera activé sous peu.", "success")
    return redirect(url_for("revue_subscription"))


@app.route("/revue/abonnement/annule")
@login_required
def revue_subscription_cancel():
    sub_id = request.args.get("sub_id")
    if sub_id:
        conn = dbm.get_db()
        conn.execute("UPDATE review_subscriptions SET status='cancelled' WHERE id=?", (sub_id,))
        conn.commit()
        conn.close()
    flash("Abonnement annulé.", "error")
    return redirect(url_for("revue_subscription"))


@app.route("/rendez-vous", methods=["GET", "POST"])
def intake():
    """Formulaire public de préqualification -> crée un compte + un dossier réel."""
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        service_type = request.form.get("service_type", "autre")
        pack = request.form.get("pack", "Non déterminé")
        territory = request.form.get("territory", "").strip()
        value_estimate = request.form.get("value_estimate", "").strip()
        urgency = request.form.get("urgency", "normale")
        language = request.form.get("language", "fr")
        description = request.form.get("description", "").strip()

        if not full_name or not email or not description:
            flash("Nom, courriel et description du besoin sont requis.", "error")
            return render_template("marketing/intake.html", service_types=dbm.SERVICE_TYPES, packs=dbm.PACKS, form=request.form)

        conn = dbm.get_db()
        existing = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        temp_password = None
        if existing:
            client_id = existing["id"]
        else:
            temp_password = secrets.token_urlsafe(9)
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, full_name, role, language, created_at) VALUES (?,?,?,?,?,?)",
                (email, generate_password_hash(temp_password), full_name, "client", language, dbm.now()),
            )
            client_id = cur.lastrowid

        title = f"{dict(dbm.SERVICE_TYPES).get(service_type, 'Demande')} — {full_name}"
        cur = conn.execute(
            """INSERT INTO dossiers
               (client_id, title, service_type, pack, territory, value_estimate, urgency, language, status, summary, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (client_id, title, service_type, pack, territory, value_estimate, urgency, language,
             "demande_recue", description, dbm.now(), dbm.now()),
        )
        dossier_id = cur.lastrowid
        conn.execute(
            "INSERT INTO status_log (dossier_id, status, changed_by, note, created_at) VALUES (?,?,?,?,?)",
            (dossier_id, "demande_recue", client_id, "Demande reçue via le formulaire public.", dbm.now()),
        )
        # Checklist de départ, visible du dossier
        for desc in ["Vérification des conflits d'intérêts", "Confirmation de la lettre de mission", "Réception des documents de base"]:
            conn.execute(
                "INSERT INTO tasks (dossier_id, description, owner, created_at) VALUES (?,?,?,?)",
                (dossier_id, desc, "massey", dbm.now()),
            )
        conn.commit()
        conn.close()
        dbm.log_activity(client_id, "intake_submitted", f"dossier #{dossier_id}")

        if temp_password:
            session["flash_credentials"] = {"email": email, "password": temp_password}
            return redirect(url_for("intake_success", dossier_id=dossier_id, new=1))
        return redirect(url_for("intake_success", dossier_id=dossier_id, new=0))

    return render_template("marketing/intake.html", service_types=dbm.SERVICE_TYPES, packs=dbm.PACKS, form={})


@app.route("/rendez-vous/confirmation")
def intake_success():
    dossier_id = request.args.get("dossier_id")
    is_new = request.args.get("new") == "1"
    creds = session.pop("flash_credentials", None) if is_new else None
    return render_template("marketing/intake_success.html", dossier_id=dossier_id, creds=creds)


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/connexion", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = dbm.get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            if user["mfa_enabled"]:
                # Deuxième facteur requis avant d'ouvrir une session complète.
                session.clear()
                session["mfa_pending_user_id"] = user["id"]
                session["mfa_next"] = request.args.get("next") or url_for("portal_dashboard")
                return redirect(url_for("mfa_verify"))
            session.clear()
            session["user_id"] = user["id"]
            dbm.log_activity(user["id"], "login", "")
            if user["role"] in ("expert", "admin"):
                return redirect(url_for("mfa_setup_prompt"))
            nxt = request.args.get("next") or url_for("portal_dashboard")
            return redirect(nxt)
        flash("Courriel ou mot de passe invalide.", "error")
    return render_template("auth/login.html")


@app.route("/connexion/verification", methods=["GET", "POST"])
def mfa_verify():
    pending_id = session.get("mfa_pending_user_id")
    if not pending_id:
        return redirect(url_for("login"))
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        conn = dbm.get_db()
        user = conn.execute("SELECT * FROM users WHERE id=?", (pending_id,)).fetchone()
        conn.close()
        if user and user["mfa_secret"] and totp.verify_totp(user["mfa_secret"], code):
            nxt = session.pop("mfa_next", url_for("portal_dashboard"))
            session.clear()
            session["user_id"] = user["id"]
            dbm.log_activity(user["id"], "login_mfa", "")
            return redirect(nxt)
        flash("Code invalide. Réessayez.", "error")
    return render_template("auth/mfa_verify.html")


RESET_TOKEN_TTL_MINUTES = 60


@app.route("/mot-de-passe-oublie", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        conn = dbm.get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        reset_url = None
        if user:
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.utcnow() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)).isoformat(timespec="seconds")
            conn.execute(
                "INSERT INTO password_resets (user_id, token, created_at, expires_at) VALUES (?,?,?,?)",
                (user["id"], token, dbm.now(), expires_at),
            )
            conn.commit()
            reset_url = url_for("reset_password", token=token, _external=True)
            sent = notifications.send_email(
                user["email"], "Réinitialisation de votre mot de passe — Massey Contracts & Tax",
                f"Bonjour {user['full_name']},\n\nVoici votre lien de réinitialisation (valide {RESET_TOKEN_TTL_MINUTES} minutes) :\n{reset_url}\n\nSi vous n'êtes pas à l'origine de cette demande, ignorez ce courriel.",
            )
            dbm.log_activity(user["id"], "password_reset_requested", "")
            if not sent:
                # Pas de service courriel configuré : on affiche le lien directement,
                # plutôt que de prétendre qu'un courriel invisible a été envoyé.
                session["flash_reset_link"] = reset_url
        conn.close()
        # Message volontairement identique que le compte existe ou non, pour ne pas
        # révéler si une adresse courriel est enregistrée sur la plateforme.
        flash(
            "Si un compte existe pour cette adresse, un lien de réinitialisation a été généré.",
            "success",
        )
        return redirect(url_for("forgot_password_sent"))
    return render_template("auth/forgot_password.html")


@app.route("/mot-de-passe-oublie/confirmation")
def forgot_password_sent():
    reset_link = session.pop("flash_reset_link", None)
    return render_template("auth/forgot_password_sent.html", reset_link=reset_link)


@app.route("/reinitialiser/<token>", methods=["GET", "POST"])
def reset_password(token):
    conn = dbm.get_db()
    row = conn.execute("SELECT * FROM password_resets WHERE token=?", (token,)).fetchone()
    valid = bool(row) and not row["used"] and row["expires_at"] >= datetime.utcnow().isoformat(timespec="seconds")
    if request.method == "POST" and valid:
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if len(password) < 8:
            flash("Le mot de passe doit contenir au moins 8 caractères.", "error")
        elif password != confirm:
            flash("Les deux mots de passe ne correspondent pas.", "error")
        else:
            conn.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (generate_password_hash(password), row["user_id"]),
            )
            conn.execute("UPDATE password_resets SET used=1 WHERE id=?", (row["id"],))
            conn.commit()
            dbm.log_activity(row["user_id"], "password_reset_completed", "")
            conn.close()
            flash("Mot de passe mis à jour. Vous pouvez maintenant vous connecter.", "success")
            return redirect(url_for("login"))
    conn.close()
    return render_template("auth/reset_password.html", valid=valid)


@app.route("/compte/mfa/activer", methods=["GET", "POST"])
@login_required
def mfa_setup_prompt():
    """Étape d'activation obligatoire du MFA pour experts/admins ; facultative pour un client."""
    u = current_user()
    if u["mfa_enabled"]:
        return redirect(url_for("portal_dashboard"))
    if "mfa_pending_secret" not in session:
        session["mfa_pending_secret"] = totp.generate_secret()
    secret = session["mfa_pending_secret"]
    uri = totp.provisioning_uri(secret, u["email"])
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if totp.verify_totp(secret, code):
            conn = dbm.get_db()
            conn.execute("UPDATE users SET mfa_secret=?, mfa_enabled=1 WHERE id=?", (secret, u["id"]))
            conn.commit()
            conn.close()
            session.pop("mfa_pending_secret", None)
            dbm.log_activity(u["id"], "mfa_enabled", "")
            flash("Authentification à deux facteurs activée.", "success")
            return redirect(url_for("portal_dashboard"))
        flash("Code invalide — vérifiez l'heure de votre application d'authentification.", "error")
    mandatory = u["role"] in ("expert", "admin")
    return render_template("auth/mfa_setup.html", secret=secret, uri=uri, mandatory=mandatory, qr_available=_qrcode_available())


def _qrcode_available():
    try:
        import qrcode  # noqa: F401
        return True
    except ImportError:
        return False


@app.route("/compte/mfa/qr.png")
@login_required
def mfa_qr():
    """Génère le QR code côté serveur — le secret MFA n'est jamais envoyé à un tiers."""
    secret = session.get("mfa_pending_secret")
    if not secret:
        abort(404)
    try:
        import qrcode
        import io
    except ImportError:
        abort(404)
    u = current_user()
    uri = totp.provisioning_uri(secret, u["email"])
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/compte/mfa/desactiver", methods=["POST"])
@login_required
def mfa_disable():
    u = current_user()
    password = request.form.get("password", "")
    if u["role"] in ("expert", "admin"):
        flash("Le MFA est obligatoire pour les comptes expert et admin.", "error")
        return redirect(url_for("portal_dashboard"))
    if not check_password_hash(u["password_hash"], password):
        flash("Mot de passe incorrect.", "error")
        return redirect(url_for("portal_dashboard"))
    conn = dbm.get_db()
    conn.execute("UPDATE users SET mfa_secret=NULL, mfa_enabled=0 WHERE id=?", (u["id"],))
    conn.commit()
    conn.close()
    dbm.log_activity(u["id"], "mfa_disabled", "")
    flash("Authentification à deux facteurs désactivée.", "success")
    return redirect(url_for("portal_dashboard"))


@app.route("/inscription", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if len(password) < 8:
            flash("Le mot de passe doit contenir au moins 8 caractères.", "error")
            return render_template("auth/register.html", form=request.form)
        conn = dbm.get_db()
        existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            conn.close()
            flash("Un compte existe déjà avec ce courriel.", "error")
            return render_template("auth/register.html", form=request.form)
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, full_name, role, created_at) VALUES (?,?,?,?,?)",
            (email, generate_password_hash(password), full_name, "client", dbm.now()),
        )
        conn.commit()
        user_id = cur.lastrowid
        conn.close()
        session.clear()
        session["user_id"] = user_id
        dbm.log_activity(user_id, "register", "")
        return redirect(url_for("portal_dashboard"))
    return render_template("auth/register.html", form={})


@app.route("/deconnexion")
def logout():
    u = current_user()
    if u:
        dbm.log_activity(u["id"], "logout", "")
    session.clear()
    return redirect(url_for("home"))


# ---------------------------------------------------------------------------
# Client & staff portal
# ---------------------------------------------------------------------------

def _compute_alerts(conn, u):
    """Alertes Tax & Compliance Intelligence : échéances fiscales proches/en retard
    et points de conformité non résolus, tous dossiers visibles par l'utilisateur."""
    is_staff = u["role"] in ("expert", "admin")
    horizon = (datetime.utcnow().date() + timedelta(days=14)).isoformat()
    today = datetime.utcnow().date().isoformat()
    if is_staff:
        tax_rows = conn.execute(
            "SELECT t.*, d.title AS dossier_title FROM tax_obligations t JOIN dossiers d ON d.id=t.dossier_id "
            "WHERE t.status != 'fait' AND (t.status='en_retard' OR (t.due_date IS NOT NULL AND t.due_date <= ?)) "
            "ORDER BY (t.due_date IS NULL), t.due_date ASC", (horizon,),
        ).fetchall()
        compliance_rows = conn.execute(
            "SELECT c.*, d.title AS dossier_title FROM compliance_items c JOIN dossiers d ON d.id=c.dossier_id "
            "WHERE c.status IN ('a_faire','non_conforme') ORDER BY c.status='non_conforme' DESC, c.created_at ASC",
        ).fetchall()
    else:
        tax_rows = conn.execute(
            "SELECT t.*, d.title AS dossier_title FROM tax_obligations t JOIN dossiers d ON d.id=t.dossier_id "
            "WHERE d.client_id=? AND t.status != 'fait' AND (t.status='en_retard' OR (t.due_date IS NOT NULL AND t.due_date <= ?)) "
            "ORDER BY (t.due_date IS NULL), t.due_date ASC", (u["id"], horizon),
        ).fetchall()
        compliance_rows = conn.execute(
            "SELECT c.*, d.title AS dossier_title FROM compliance_items c JOIN dossiers d ON d.id=c.dossier_id "
            "WHERE d.client_id=? AND c.status IN ('a_faire','non_conforme') ORDER BY c.status='non_conforme' DESC, c.created_at ASC",
            (u["id"],),
        ).fetchall()
    alerts = []
    for t in tax_rows:
        overdue = t["status"] == "en_retard" or (t["due_date"] and t["due_date"] < today)
        alerts.append({
            "kind": "fiscal", "dossier_id": t["dossier_id"], "dossier_title": t["dossier_title"],
            "label": t["label"], "detail": dbm.TAX_TYPE_LABELS.get(t["tax_type"], t["tax_type"]),
            "due_date": t["due_date"], "urgent": overdue,
        })
    for c in compliance_rows:
        alerts.append({
            "kind": "conformite", "dossier_id": c["dossier_id"], "dossier_title": c["dossier_title"],
            "label": c["label"], "detail": dbm.COMPLIANCE_CATEGORY_LABELS.get(c["category"], c["category"]),
            "due_date": None, "urgent": c["status"] == "non_conforme",
        })
    alerts.sort(key=lambda a: not a["urgent"])
    return alerts


@app.route("/portail")
@login_required
def portal_dashboard():
    u = current_user()
    conn = dbm.get_db()
    if u["role"] in ("expert", "admin"):
        dossiers = conn.execute(
            "SELECT d.*, c.full_name AS client_name FROM dossiers d JOIN users c ON c.id=d.client_id ORDER BY d.updated_at DESC"
        ).fetchall()
    else:
        dossiers = conn.execute(
            "SELECT d.*, c.full_name AS client_name FROM dossiers d JOIN users c ON c.id=d.client_id WHERE d.client_id=? ORDER BY d.updated_at DESC",
            (u["id"],),
        ).fetchall()
    alerts = _compute_alerts(conn, u)
    conn.close()
    return render_template("portal/dashboard.html", dossiers=dossiers, alerts=alerts)


@app.route("/portail/nouveau", methods=["GET", "POST"])
@login_required
def portal_new_dossier():
    u = current_user()
    if request.method == "POST":
        service_type = request.form.get("service_type", "autre")
        pack = request.form.get("pack", "Non déterminé")
        territory = request.form.get("territory", "").strip()
        value_estimate = request.form.get("value_estimate", "").strip()
        urgency = request.form.get("urgency", "normale")
        description = request.form.get("description", "").strip()
        if not description:
            flash("Merci de décrire brièvement votre besoin.", "error")
            return render_template("portal/new_dossier.html", service_types=dbm.SERVICE_TYPES, packs=dbm.PACKS)
        title = f"{dict(dbm.SERVICE_TYPES).get(service_type, 'Demande')} — {u['full_name']}"
        conn = dbm.get_db()
        cur = conn.execute(
            """INSERT INTO dossiers
               (client_id, title, service_type, pack, territory, value_estimate, urgency, language, status, summary, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (u["id"], title, service_type, pack, territory, value_estimate, urgency, u["language"] or "fr",
             "demande_recue", description, dbm.now(), dbm.now()),
        )
        dossier_id = cur.lastrowid
        conn.execute(
            "INSERT INTO status_log (dossier_id, status, changed_by, note, created_at) VALUES (?,?,?,?,?)",
            (dossier_id, "demande_recue", u["id"], "Demande créée depuis l'espace client.", dbm.now()),
        )
        conn.commit()
        conn.close()
        dbm.log_activity(u["id"], "dossier_created", f"#{dossier_id}")
        flash("Votre dossier a été créé.", "success")
        return redirect(url_for("portal_dossier", dossier_id=dossier_id))
    return render_template("portal/new_dossier.html", service_types=dbm.SERVICE_TYPES, packs=dbm.PACKS)


def _get_dossier_or_404(dossier_id, user):
    conn = dbm.get_db()
    dossier = conn.execute(
        "SELECT d.*, c.full_name AS client_name, c.email AS client_email FROM dossiers d JOIN users c ON c.id=d.client_id WHERE d.id=?",
        (dossier_id,),
    ).fetchone()
    if dossier is None:
        conn.close()
        abort(404)
    if not can_view_dossier(user, dossier):
        conn.close()
        abort(403)
    return conn, dossier


@app.route("/portail/dossier/<int:dossier_id>")
@login_required
def portal_dossier(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    documents = conn.execute("SELECT d.*, u.full_name AS uploader FROM documents d JOIN users u ON u.id=d.uploaded_by WHERE dossier_id=? ORDER BY created_at DESC", (dossier_id,)).fetchall()
    is_staff = u["role"] in ("expert", "admin")
    if is_staff:
        messages = conn.execute("SELECT m.*, u.full_name AS sender_name, u.role AS sender_role FROM messages m JOIN users u ON u.id=m.sender_id WHERE dossier_id=? ORDER BY created_at ASC", (dossier_id,)).fetchall()
    else:
        messages = conn.execute("SELECT m.*, u.full_name AS sender_name, u.role AS sender_role FROM messages m JOIN users u ON u.id=m.sender_id WHERE dossier_id=? AND is_internal=0 ORDER BY created_at ASC", (dossier_id,)).fetchall()
    tasks = conn.execute("SELECT * FROM tasks WHERE dossier_id=? ORDER BY done ASC, created_at ASC", (dossier_id,)).fetchall()
    status_log = conn.execute("SELECT s.*, u.full_name AS changed_by_name FROM status_log s JOIN users u ON u.id=s.changed_by WHERE dossier_id=? ORDER BY created_at DESC", (dossier_id,)).fetchall()
    staff = conn.execute("SELECT id, full_name FROM users WHERE role IN ('expert','admin')").fetchall() if is_staff else []
    payment_rows = conn.execute("SELECT * FROM payments WHERE dossier_id=? ORDER BY created_at DESC", (dossier_id,)).fetchall()
    signature_rows = conn.execute(
        "SELECT s.*, doc.original_name FROM signatures s JOIN documents doc ON doc.id=s.document_id WHERE s.dossier_id=? ORDER BY s.created_at DESC",
        (dossier_id,),
    ).fetchall()
    tax_obligations = conn.execute(
        "SELECT * FROM tax_obligations WHERE dossier_id=? ORDER BY (due_date IS NULL), due_date ASC", (dossier_id,)
    ).fetchall()
    compliance_items = conn.execute(
        "SELECT * FROM compliance_items WHERE dossier_id=? ORDER BY created_at ASC", (dossier_id,)
    ).fetchall()
    diligence_items = conn.execute(
        "SELECT * FROM due_diligence_items WHERE dossier_id=? ORDER BY category ASC, created_at ASC", (dossier_id,)
    ).fetchall()
    comparisons = conn.execute(
        "SELECT c.*, a.original_name AS a_name, b.original_name AS b_name FROM document_comparisons c "
        "JOIN documents a ON a.id=c.document_a_id JOIN documents b ON b.id=c.document_b_id "
        "WHERE c.dossier_id=? ORDER BY c.created_at DESC", (dossier_id,)
    ).fetchall()
    conn.close()
    stage = dbm.STATUS_TO_STAGE.get(dossier["status"], "creer")
    stage_keys = [s[0] for s in dbm.TRANSACTION_STAGES]
    stage_index = stage_keys.index(stage) if stage in stage_keys else 0
    return render_template(
        "portal/dossier.html",
        dossier=dossier, documents=documents, messages=messages, tasks=tasks,
        status_log=status_log, is_staff=is_staff, staff=staff,
        status_flow=dbm.STATUS_FLOW, doc_categories=dbm.DOC_CATEGORIES,
        payment_rows=payment_rows, signature_rows=signature_rows,
        payment_purposes=dbm.PAYMENT_PURPOSES, payment_purposes_map=dict(dbm.PAYMENT_PURPOSES),
        payments_configured=payments.is_configured(),
        tax_obligations=tax_obligations, compliance_items=compliance_items,
        transaction_stages=dbm.TRANSACTION_STAGES, current_stage=stage, current_stage_index=stage_index,
        tax_types=dbm.TAX_TYPES, tax_status_labels=dbm.TAX_OBLIGATION_STATUS_LABELS,
        tax_statuses=dbm.TAX_OBLIGATION_STATUSES, tax_type_labels=dbm.TAX_TYPE_LABELS,
        compliance_categories=dbm.COMPLIANCE_CATEGORIES, compliance_status_labels=dbm.COMPLIANCE_STATUS_LABELS,
        compliance_statuses=dbm.COMPLIANCE_STATUSES, compliance_category_labels=dbm.COMPLIANCE_CATEGORY_LABELS,
        diligence_items=diligence_items, diligence_categories=dbm.DILIGENCE_CATEGORIES,
        diligence_statuses=dbm.DILIGENCE_STATUSES, diligence_status_labels=dbm.DILIGENCE_STATUS_LABELS,
        diligence_category_labels=dbm.DILIGENCE_CATEGORY_LABELS,
        comparisons=comparisons,
    )


@app.route("/portail/dossier/<int:dossier_id>/diligence/generer", methods=["POST"])
@roles_required("expert", "admin")
def generate_due_diligence_checklist(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    existing = conn.execute("SELECT COUNT(*) AS c FROM due_diligence_items WHERE dossier_id=?", (dossier_id,)).fetchone()["c"]
    if existing:
        flash("Une checklist de due diligence existe déjà pour ce dossier.", "error")
    else:
        ts = dbm.now()
        for label, category in dbm.DEFAULT_DILIGENCE_CHECKLIST:
            conn.execute(
                "INSERT INTO due_diligence_items (dossier_id, label, category, status, created_by, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (dossier_id, label, category, "a_verifier", u["id"], ts),
            )
        conn.commit()
        dbm.log_activity(u["id"], "due_diligence_checklist_generated", f"dossier #{dossier_id}")
        flash("Checklist de due diligence standard générée.", "success")
    conn.close()
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/diligence/nouvelle", methods=["POST"])
@roles_required("expert", "admin")
def add_due_diligence_item(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    label = request.form.get("label", "").strip()
    category = request.form.get("category", "juridique")
    if category not in dbm.DILIGENCE_CATEGORY_LABELS:
        category = "juridique"
    if label:
        conn.execute(
            "INSERT INTO due_diligence_items (dossier_id, label, category, status, created_by, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (dossier_id, label, category, "a_verifier", u["id"], dbm.now()),
        )
        conn.commit()
        dbm.log_activity(u["id"], "due_diligence_item_created", f"dossier #{dossier_id} — {label}")
        flash("Point de due diligence ajouté.", "success")
    else:
        flash("La description du point de due diligence est requise.", "error")
    conn.close()
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/diligence/<int:item_id>/statut", methods=["POST"])
@roles_required("expert", "admin")
def update_due_diligence_item(dossier_id, item_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    new_status = request.form.get("status", "a_verifier")
    note = request.form.get("note", "").strip()
    if new_status not in dbm.DILIGENCE_STATUS_LABELS:
        new_status = "a_verifier"
    conn.execute(
        "UPDATE due_diligence_items SET status=?, note=? WHERE id=? AND dossier_id=?",
        (new_status, note or None, item_id, dossier_id),
    )
    conn.commit()
    conn.close()
    flash("Point de due diligence mis à jour.", "success")
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/comparer")
@roles_required("expert", "admin")
def compare_documents(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    documents = conn.execute(
        "SELECT d.*, u.full_name AS uploader FROM documents d JOIN users u ON u.id=d.uploaded_by WHERE dossier_id=? ORDER BY created_at DESC",
        (dossier_id,),
    ).fetchall()
    doc_a_id = request.args.get("a", type=int)
    doc_b_id = request.args.get("b", type=int)
    doc_a = doc_b = None
    diff_lines = None
    text_compared = False
    if doc_a_id and doc_b_id and doc_a_id != doc_b_id:
        doc_a = conn.execute("SELECT * FROM documents WHERE id=? AND dossier_id=?", (doc_a_id, dossier_id)).fetchone()
        doc_b = conn.execute("SELECT * FROM documents WHERE id=? AND dossier_id=?", (doc_b_id, dossier_id)).fetchone()
        if doc_a and doc_b:
            text_exts = (".txt", ".md", ".csv")
            if doc_a["original_name"].lower().endswith(text_exts) and doc_b["original_name"].lower().endswith(text_exts):
                try:
                    path_a = os.path.join(UPLOAD_DIR, str(dossier_id), doc_a["stored_name"])
                    path_b = os.path.join(UPLOAD_DIR, str(dossier_id), doc_b["stored_name"])
                    with open(path_a, "r", encoding="utf-8", errors="replace") as fa:
                        lines_a = fa.readlines()
                    with open(path_b, "r", encoding="utf-8", errors="replace") as fb:
                        lines_b = fb.readlines()
                    diff_lines = list(difflib.unified_diff(
                        lines_a, lines_b,
                        fromfile=doc_a["original_name"], tofile=doc_b["original_name"], lineterm="",
                    ))
                    text_compared = True
                except OSError:
                    diff_lines = None
    conn.close()
    return render_template(
        "portal/compare_documents.html", dossier=dossier, documents=documents,
        doc_a=doc_a, doc_b=doc_b, doc_a_id=doc_a_id, doc_b_id=doc_b_id,
        diff_lines=diff_lines, text_compared=text_compared,
    )


@app.route("/portail/dossier/<int:dossier_id>/comparer/note", methods=["POST"])
@roles_required("expert", "admin")
def save_document_comparison(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    doc_a_id = request.form.get("document_a_id", type=int)
    doc_b_id = request.form.get("document_b_id", type=int)
    note = request.form.get("note", "").strip()
    valid = doc_a_id and doc_b_id and doc_a_id != doc_b_id and note
    if valid:
        row_a = conn.execute("SELECT id FROM documents WHERE id=? AND dossier_id=?", (doc_a_id, dossier_id)).fetchone()
        row_b = conn.execute("SELECT id FROM documents WHERE id=? AND dossier_id=?", (doc_b_id, dossier_id)).fetchone()
        valid = bool(row_a and row_b)
    if valid:
        conn.execute(
            "INSERT INTO document_comparisons (dossier_id, document_a_id, document_b_id, note, created_by, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (dossier_id, doc_a_id, doc_b_id, note, u["id"], dbm.now()),
        )
        conn.commit()
        dbm.log_activity(u["id"], "document_comparison_saved", f"dossier #{dossier_id}")
        flash("Note de comparaison enregistrée.", "success")
    else:
        flash("Merci de sélectionner deux documents différents et de renseigner une observation.", "error")
    conn.close()
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/fiscalite/nouvelle", methods=["POST"])
@roles_required("expert", "admin")
def add_tax_obligation(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    label = request.form.get("label", "").strip()
    tax_type = request.form.get("tax_type", "autre")
    if tax_type not in dbm.TAX_TYPE_LABELS:
        tax_type = "autre"
    due_date = request.form.get("due_date", "").strip() or None
    if label:
        conn.execute(
            "INSERT INTO tax_obligations (dossier_id, label, tax_type, due_date, status, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (dossier_id, label, tax_type, due_date, "a_faire", u["id"], dbm.now()),
        )
        conn.commit()
        dbm.log_activity(u["id"], "tax_obligation_created", f"dossier #{dossier_id} — {label}")
        flash("Obligation fiscale ajoutée.", "success")
    else:
        flash("La description de l'obligation est requise.", "error")
    conn.close()
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/fiscalite/<int:obligation_id>/statut", methods=["POST"])
@roles_required("expert", "admin")
def update_tax_obligation(dossier_id, obligation_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    new_status = request.form.get("status", "a_faire")
    if new_status not in dbm.TAX_OBLIGATION_STATUS_LABELS:
        new_status = "a_faire"
    conn.execute(
        "UPDATE tax_obligations SET status=? WHERE id=? AND dossier_id=?", (new_status, obligation_id, dossier_id)
    )
    conn.commit()
    conn.close()
    flash("Statut fiscal mis à jour.", "success")
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/conformite/nouvelle", methods=["POST"])
@roles_required("expert", "admin")
def add_compliance_item(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    label = request.form.get("label", "").strip()
    category = request.form.get("category", "kyc")
    if category not in dbm.COMPLIANCE_CATEGORY_LABELS:
        category = "kyc"
    if label:
        conn.execute(
            "INSERT INTO compliance_items (dossier_id, label, category, status, created_by, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (dossier_id, label, category, "a_faire", u["id"], dbm.now()),
        )
        conn.commit()
        dbm.log_activity(u["id"], "compliance_item_created", f"dossier #{dossier_id} — {label}")
        flash("Point de conformité ajouté.", "success")
    else:
        flash("La description du point de conformité est requise.", "error")
    conn.close()
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/conformite/<int:item_id>/statut", methods=["POST"])
@roles_required("expert", "admin")
def update_compliance_item(dossier_id, item_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    new_status = request.form.get("status", "a_faire")
    if new_status not in dbm.COMPLIANCE_STATUS_LABELS:
        new_status = "a_faire"
    conn.execute(
        "UPDATE compliance_items SET status=? WHERE id=? AND dossier_id=?", (new_status, item_id, dossier_id)
    )
    conn.commit()
    conn.close()
    flash("Statut de conformité mis à jour.", "success")
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/document", methods=["POST"])
@login_required
def upload_document(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    file = request.files.get("file")
    category = request.form.get("category", "autre")
    if category not in dbm.DOC_CATEGORIES:
        category = "autre"
    if not file or file.filename == "":
        flash("Aucun fichier sélectionné.", "error")
        conn.close()
        return redirect(url_for("portal_dossier", dossier_id=dossier_id))
    if not allowed_file(file.filename):
        flash("Type de fichier non autorisé.", "error")
        conn.close()
        return redirect(url_for("portal_dossier", dossier_id=dossier_id))

    original_name = secure_filename(file.filename)
    ext = original_name.rsplit(".", 1)[1].lower()
    # Nom interne aléatoire : on ne fait jamais confiance au nom fourni par l'utilisateur (OWASP).
    stored_name = f"{secrets.token_hex(16)}.{ext}"
    dossier_dir = os.path.join(UPLOAD_DIR, str(dossier_id))
    os.makedirs(dossier_dir, exist_ok=True)
    file.save(os.path.join(dossier_dir, stored_name))
    stored_path = os.path.join(dossier_dir, stored_name)
    size_bytes = os.path.getsize(stored_path)
    file_hash = signing.compute_file_hash(stored_path)

    conn.execute(
        "INSERT INTO documents (dossier_id, uploaded_by, category, original_name, stored_name, size_bytes, sha256, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (dossier_id, u["id"], category, original_name, stored_name, size_bytes, file_hash, dbm.now()),
    )
    conn.execute("UPDATE dossiers SET updated_at=? WHERE id=?", (dbm.now(), dossier_id))
    conn.commit()
    conn.close()
    dbm.log_activity(u["id"], "document_uploaded", f"dossier #{dossier_id} / {original_name}")
    flash("Document téléversé.", "success")
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/document/<int:doc_id>/telecharger")
@login_required
def download_document(dossier_id, doc_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    doc = conn.execute("SELECT * FROM documents WHERE id=? AND dossier_id=?", (doc_id, dossier_id)).fetchone()
    conn.close()
    if doc is None:
        abort(404)
    dbm.log_activity(u["id"], "document_downloaded", f"dossier #{dossier_id} / doc #{doc_id}")
    return send_from_directory(
        os.path.join(UPLOAD_DIR, str(dossier_id)), doc["stored_name"],
        as_attachment=True, download_name=doc["original_name"],
    )


@app.route("/portail/dossier/<int:dossier_id>/message", methods=["POST"])
@login_required
def post_message(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    body = request.form.get("body", "").strip()
    is_internal = 1 if (request.form.get("is_internal") == "on" and u["role"] in ("expert", "admin")) else 0
    if body:
        conn.execute(
            "INSERT INTO messages (dossier_id, sender_id, body, is_internal, created_at) VALUES (?,?,?,?,?)",
            (dossier_id, u["id"], body, is_internal, dbm.now()),
        )
        conn.execute("UPDATE dossiers SET updated_at=? WHERE id=?", (dbm.now(), dossier_id))
        conn.commit()
        dbm.log_activity(u["id"], "message_posted", f"dossier #{dossier_id}")
    conn.close()
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/tache", methods=["POST"])
@login_required
def add_or_toggle_task(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    action = request.form.get("action")
    if action == "create" and u["role"] in ("expert", "admin"):
        description = request.form.get("description", "").strip()
        owner = request.form.get("owner", "massey")
        if description:
            conn.execute(
                "INSERT INTO tasks (dossier_id, description, owner, created_at) VALUES (?,?,?,?)",
                (dossier_id, description, owner, dbm.now()),
            )
    elif action == "toggle":
        task_id = request.form.get("task_id")
        task = conn.execute("SELECT * FROM tasks WHERE id=? AND dossier_id=?", (task_id, dossier_id)).fetchone()
        if task:
            # Un client ne peut cocher que ses propres tâches ("owner"='client')
            if u["role"] in ("expert", "admin") or task["owner"] == "client":
                conn.execute("UPDATE tasks SET done=? WHERE id=?", (0 if task["done"] else 1, task_id))
    conn.commit()
    conn.close()
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/statut", methods=["POST"])
@roles_required("expert", "admin")
def change_status(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    new_status = request.form.get("status")
    note = request.form.get("note", "").strip()
    assigned_to = request.form.get("assigned_to") or None
    if new_status in dbm.STATUS_LABELS:
        conn.execute("UPDATE dossiers SET status=?, updated_at=? WHERE id=?", (new_status, dbm.now(), dossier_id))
        conn.execute(
            "INSERT INTO status_log (dossier_id, status, changed_by, note, created_at) VALUES (?,?,?,?,?)",
            (dossier_id, new_status, u["id"], note, dbm.now()),
        )
    if assigned_to:
        conn.execute("UPDATE dossiers SET assigned_to=? WHERE id=?", (assigned_to, dossier_id))
    conn.commit()
    conn.close()
    dbm.log_activity(u["id"], "status_changed", f"dossier #{dossier_id} -> {new_status}")
    flash("Statut mis à jour.", "success")
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/conflit", methods=["POST"])
@roles_required("expert", "admin")
def toggle_conflict_check(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    conn.execute("UPDATE dossiers SET conflict_checked=? WHERE id=?", (0 if dossier["conflict_checked"] else 1, dossier_id))
    conn.commit()
    conn.close()
    dbm.log_activity(u["id"], "conflict_check_toggled", f"dossier #{dossier_id}")
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/document/<int:doc_id>/finaliser", methods=["POST"])
@roles_required("expert", "admin")
def mark_document_final(dossier_id, doc_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    doc = conn.execute("SELECT * FROM documents WHERE id=? AND dossier_id=?", (doc_id, dossier_id)).fetchone()
    if doc:
        conn.execute("UPDATE documents SET is_final=1 WHERE id=?", (doc_id,))
        conn.commit()
        dbm.log_activity(u["id"], "document_marked_final", f"dossier #{dossier_id} / doc #{doc_id}")
        flash("Document marqué comme version finale, prêt pour signature.", "success")
    conn.close()
    return redirect(url_for("portal_dossier", dossier_id=dossier_id))


# ---------------------------------------------------------------------------
# Signature électronique (attestation interne avec piste d'audit — voir signing.py)
# ---------------------------------------------------------------------------

@app.route("/portail/dossier/<int:dossier_id>/document/<int:doc_id>/signer", methods=["GET", "POST"])
@login_required
def sign_document(dossier_id, doc_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    doc = conn.execute("SELECT * FROM documents WHERE id=? AND dossier_id=?", (doc_id, dossier_id)).fetchone()
    if doc is None:
        conn.close()
        abort(404)
    if not doc["is_final"]:
        conn.close()
        flash("Ce document n'a pas encore été marqué comme version finale par Massey.", "error")
        return redirect(url_for("portal_dossier", dossier_id=dossier_id))
    if doc["locked"]:
        conn.close()
        flash("Ce document a déjà été signé et est verrouillé.", "error")
        return redirect(url_for("portal_dossier", dossier_id=dossier_id))

    stored_path = os.path.join(UPLOAD_DIR, str(dossier_id), doc["stored_name"])
    current_hash = signing.compute_file_hash(stored_path) if os.path.exists(stored_path) else None

    if request.method == "POST":
        full_legal_name = request.form.get("full_legal_name", "").strip()
        consent_read = request.form.get("consent_read") == "on"
        consent_binding = request.form.get("consent_binding") == "on"
        if not full_legal_name or not consent_read or not consent_binding:
            conn.close()
            flash("Le nom légal complet et les deux cases de consentement sont requis.", "error")
            return redirect(url_for("sign_document", dossier_id=dossier_id, doc_id=doc_id))

        conn.execute(
            """INSERT INTO signatures
               (dossier_id, document_id, signer_id, full_legal_name, document_sha256, ip_address, user_agent, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (dossier_id, doc_id, u["id"], full_legal_name, current_hash,
             request.remote_addr, request.headers.get("User-Agent", "")[:300], dbm.now()),
        )
        conn.execute("UPDATE documents SET locked=1 WHERE id=?", (doc_id,))
        conn.execute(
            "INSERT INTO messages (dossier_id, sender_id, body, is_internal, created_at) VALUES (?,?,?,?,?)",
            (dossier_id, u["id"], f"« {doc['original_name']} » a été signé électroniquement par {full_legal_name}.", 0, dbm.now()),
        )
        conn.execute("UPDATE dossiers SET updated_at=? WHERE id=?", (dbm.now(), dossier_id))
        conn.commit()
        conn.close()
        dbm.log_activity(u["id"], "document_signed", f"dossier #{dossier_id} / doc #{doc_id}")
        flash("Document signé.", "success")
        return redirect(url_for("signature_certificate", dossier_id=dossier_id, doc_id=doc_id))

    conn.close()
    return render_template(
        "portal/sign_document.html", dossier=dossier, doc=doc,
        current_hash=current_hash, consent_text=signing.CONSENT_TEXT,
    )


@app.route("/portail/dossier/<int:dossier_id>/document/<int:doc_id>/certificat")
@login_required
def signature_certificate(dossier_id, doc_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    doc = conn.execute("SELECT * FROM documents WHERE id=? AND dossier_id=?", (doc_id, dossier_id)).fetchone()
    sigs = conn.execute(
        "SELECT s.*, usr.email AS signer_email FROM signatures s JOIN users usr ON usr.id=s.signer_id WHERE document_id=? ORDER BY s.created_at ASC",
        (doc_id,),
    ).fetchall()
    conn.close()
    if doc is None or not sigs:
        abort(404)
    return render_template("portal/signature_certificate.html", dossier=dossier, doc=doc, sigs=sigs)


# ---------------------------------------------------------------------------
# Paiement en ligne (Stripe Checkout — voir payments.py)
# ---------------------------------------------------------------------------

@app.route("/portail/dossier/<int:dossier_id>/paiement/nouveau", methods=["POST"])
@login_required
def create_payment(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    purpose = request.form.get("purpose", "acompte")
    try:
        amount = float(request.form.get("amount", "0").replace(",", "."))
    except ValueError:
        amount = 0
    currency = request.form.get("currency", "cad")
    if amount <= 0:
        conn.close()
        flash("Montant invalide.", "error")
        return redirect(url_for("portal_dossier", dossier_id=dossier_id))
    amount_cents = int(round(amount * 100))
    purpose_label = dict(dbm.PAYMENT_PURPOSES).get(purpose, purpose)

    cur = conn.execute(
        "INSERT INTO payments (dossier_id, created_by, purpose, amount_cents, currency, status, created_at) VALUES (?,?,?,?,?,?,?)",
        (dossier_id, u["id"], purpose, amount_cents, currency, "pending", dbm.now()),
    )
    payment_id = cur.lastrowid
    conn.commit()

    if not payments.is_configured():
        conn.close()
        flash("Le paiement en ligne n'est pas encore configuré (clés Stripe manquantes) — voir le README.", "error")
        return redirect(url_for("portal_dossier", dossier_id=dossier_id))

    try:
        success_url = url_for("payment_success", dossier_id=dossier_id, payment_id=payment_id, _external=True) + "&session_id={CHECKOUT_SESSION_ID}"
        cancel_url = url_for("payment_cancel", dossier_id=dossier_id, payment_id=payment_id, _external=True)
        checkout = payments.create_checkout_session(
            amount_cents=amount_cents, currency=currency,
            description=f"Massey Contracts & Tax — {purpose_label} (dossier #{dossier_id})",
            success_url=success_url, cancel_url=cancel_url,
            client_email=dossier["client_email"],
            metadata={"dossier_id": dossier_id, "payment_id": payment_id},
        )
        conn.execute("UPDATE payments SET provider_session_id=? WHERE id=?", (checkout["id"], payment_id))
        conn.commit()
        conn.close()
        dbm.log_activity(u["id"], "payment_initiated", f"dossier #{dossier_id} / {amount_cents/100} {currency}")
        return redirect(checkout["url"])
    except (payments.PaymentNotConfigured, payments.PaymentProviderError) as exc:
        conn.execute("UPDATE payments SET status='failed' WHERE id=?", (payment_id,))
        conn.commit()
        conn.close()
        flash(str(exc), "error")
        return redirect(url_for("portal_dossier", dossier_id=dossier_id))


@app.route("/portail/dossier/<int:dossier_id>/paiement/succes")
@login_required
def payment_success(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    payment_id = request.args.get("payment_id")
    session_id = request.args.get("session_id")
    # Le webhook est la source de vérité pour marquer un paiement "paid" (voir stripe_webhook).
    # Ici on affiche simplement un message de confirmation à l'utilisateur.
    payment = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    conn.close()
    return render_template("portal/payment_result.html", dossier=dossier, payment=payment, ok=True, session_id=session_id)


@app.route("/portail/dossier/<int:dossier_id>/paiement/annule")
@login_required
def payment_cancel(dossier_id):
    u = current_user()
    conn, dossier = _get_dossier_or_404(dossier_id, u)
    payment_id = request.args.get("payment_id")
    conn.execute("UPDATE payments SET status='cancelled' WHERE id=?", (payment_id,))
    conn.commit()
    payment = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    conn.close()
    return render_template("portal/payment_result.html", dossier=dossier, payment=payment, ok=False, session_id=None)


@app.route("/webhooks/stripe", methods=["POST"])
def stripe_webhook():
    """Endpoint appelé par Stripe (pas par le navigateur) — c'est la source de
    vérité pour marquer un paiement comme payé, jamais la page de retour du client."""
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    if not webhook_secret or not payments.verify_webhook_signature(payload, sig_header, webhook_secret):
        return {"error": "invalid signature"}, 400

    event = request.get_json(silent=True) or {}
    event_type = event.get("type", "")
    if event_type == "checkout.session.completed":
        session_obj = event.get("data", {}).get("object", {})
        session_id = session_obj.get("id")
        conn = dbm.get_db()
        payment = conn.execute("SELECT * FROM payments WHERE provider_session_id=?", (session_id,)).fetchone()
        if payment and payment["status"] != "paid":
            conn.execute("UPDATE payments SET status='paid', paid_at=? WHERE id=?", (dbm.now(), payment["id"]))
            conn.execute(
                "INSERT INTO messages (dossier_id, sender_id, body, is_internal, created_at) VALUES (?,?,?,?,?)",
                (payment["dossier_id"], payment["created_by"],
                 f"Paiement confirmé : {payment['amount_cents']/100} {payment['currency'].upper()} ({dict(dbm.PAYMENT_PURPOSES).get(payment['purpose'], payment['purpose'])}).",
                 0, dbm.now()),
            )
            conn.commit()
            dbm.log_activity(payment["created_by"], "payment_confirmed", f"payment #{payment['id']}")
        else:
            sub = conn.execute(
                "SELECT * FROM review_subscriptions WHERE provider_session_id=?", (session_id,)
            ).fetchone()
            if sub and sub["status"] != "paid":
                conn.execute(
                    "UPDATE review_subscriptions SET status='paid', paid_at=? WHERE id=?",
                    (dbm.now(), sub["id"]),
                )
                conn.commit()
                dbm.log_activity(sub["user_id"], "revue_subscription_confirmed", f"subscription #{sub['id']}")
        conn.close()
    return {"received": True}, 200


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.route("/admin/utilisateurs")
@roles_required("admin")
def admin_users():
    conn = dbm.get_db()
    users = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template("admin/users.html", users=users)


@app.route("/admin/utilisateurs/<int:user_id>/role", methods=["POST"])
@roles_required("admin")
def admin_change_role(user_id):
    new_role = request.form.get("role")
    if new_role in ("client", "expert", "admin"):
        conn = dbm.get_db()
        conn.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))
        conn.commit()
        conn.close()
        dbm.log_activity(current_user()["id"], "role_changed", f"user #{user_id} -> {new_role}")
        flash("Rôle mis à jour.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/journal")
@roles_required("admin")
def admin_activity():
    conn = dbm.get_db()
    logs = conn.execute(
        "SELECT a.*, u.full_name, u.email FROM activity_log a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.created_at DESC LIMIT 300"
    ).fetchall()
    conn.close()
    return render_template("admin/activity.html", logs=logs)


# ---------------------------------------------------------------------------
# Admin — Massey Law Review
# ---------------------------------------------------------------------------

@app.route("/admin/revue/articles")
@roles_required("expert", "admin")
def admin_revue_articles():
    conn = dbm.get_db()
    articles = conn.execute("SELECT * FROM review_articles ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template("admin/revue_articles.html", articles=articles)


@app.route("/admin/revue/articles/nouveau", methods=["GET", "POST"])
@roles_required("expert", "admin")
def admin_revue_article_new():
    if request.method == "POST":
        return _save_revue_article(None)
    return render_template("admin/revue_article_form.html", article=None)


@app.route("/admin/revue/articles/<int:article_id>/modifier", methods=["GET", "POST"])
@roles_required("expert", "admin")
def admin_revue_article_edit(article_id):
    conn = dbm.get_db()
    article = conn.execute("SELECT * FROM review_articles WHERE id=?", (article_id,)).fetchone()
    conn.close()
    if article is None:
        abort(404)
    if request.method == "POST":
        return _save_revue_article(article_id)
    return render_template("admin/revue_article_form.html", article=article)


def _save_revue_article(article_id):
    u = current_user()
    title = request.form.get("title", "").strip()
    author_name = request.form.get("author_name", "").strip()
    issue_label = request.form.get("issue_label", "").strip()
    abstract = request.form.get("abstract", "").strip()
    body_html = request.form.get("body_html", "").strip()
    published = 1 if request.form.get("published") == "on" else 0
    file = request.files.get("pdf")

    if not (title and author_name):
        flash("Titre et auteur sont obligatoires.", "error")
        return redirect(request.referrer or url_for("admin_revue_articles"))

    conn = dbm.get_db()
    pdf_stored_name = None
    pdf_original_name = None
    if file and file.filename:
        if not allowed_file(file.filename) or not file.filename.lower().endswith(".pdf"):
            flash("Le fichier joint doit être un PDF.", "error")
            conn.close()
            return redirect(request.referrer or url_for("admin_revue_articles"))
        pdf_original_name = secure_filename(file.filename)
        pdf_stored_name = f"{secrets.token_hex(16)}.pdf"
        art_dir = os.path.join(UPLOAD_DIR, "revue", "articles")
        os.makedirs(art_dir, exist_ok=True)
        file.save(os.path.join(art_dir, pdf_stored_name))

    if article_id is None:
        slug = _slugify(title)
        conn2 = conn.execute("SELECT id FROM review_articles WHERE slug=?", (slug,)).fetchone()
        if conn2:
            slug = f"{slug}-{secrets.token_hex(3)}"
        published_at = dbm.now() if published else None
        conn.execute(
            "INSERT INTO review_articles (slug, title, author_name, issue_label, abstract, body_html, "
            "pdf_stored_name, pdf_original_name, published, created_by, created_at, published_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (slug, title, author_name, issue_label, abstract, body_html, pdf_stored_name, pdf_original_name,
             published, u["id"], dbm.now(), published_at),
        )
        conn.commit()
        dbm.log_activity(u["id"], "revue_article_created", title)
        flash("Article créé.", "success")
    else:
        existing = conn.execute("SELECT * FROM review_articles WHERE id=?", (article_id,)).fetchone()
        new_published_at = existing["published_at"]
        if published and not existing["published"]:
            new_published_at = dbm.now()
        if pdf_stored_name is None:
            pdf_stored_name = existing["pdf_stored_name"]
            pdf_original_name = existing["pdf_original_name"]
        conn.execute(
            "UPDATE review_articles SET title=?, author_name=?, issue_label=?, abstract=?, body_html=?, "
            "pdf_stored_name=?, pdf_original_name=?, published=?, published_at=? WHERE id=?",
            (title, author_name, issue_label, abstract, body_html, pdf_stored_name, pdf_original_name,
             published, new_published_at, article_id),
        )
        conn.commit()
        dbm.log_activity(u["id"], "revue_article_updated", title)
        flash("Article mis à jour.", "success")
    conn.close()
    return redirect(url_for("admin_revue_articles"))


@app.route("/admin/revue/articles/<int:article_id>/supprimer", methods=["POST"])
@roles_required("admin")
def admin_revue_article_delete(article_id):
    conn = dbm.get_db()
    conn.execute("DELETE FROM review_articles WHERE id=?", (article_id,))
    conn.commit()
    conn.close()
    flash("Article supprimé.", "success")
    return redirect(url_for("admin_revue_articles"))


@app.route("/admin/revue/soumissions")
@roles_required("expert", "admin")
def admin_revue_submissions():
    conn = dbm.get_db()
    subs = conn.execute("SELECT * FROM review_submissions ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template(
        "admin/revue_submissions.html", subs=subs, statuses=dbm.REVIEW_SUBMISSION_STATUSES
    )


@app.route("/admin/revue/soumissions/<int:sub_id>/statut", methods=["POST"])
@roles_required("expert", "admin")
def admin_revue_submission_status(sub_id):
    new_status = request.form.get("status")
    note = request.form.get("note_interne", "").strip()
    if new_status in dbm.REVIEW_SUBMISSION_LABELS:
        conn = dbm.get_db()
        conn.execute(
            "UPDATE review_submissions SET status=?, note_interne=? WHERE id=?",
            (new_status, note, sub_id),
        )
        conn.commit()
        conn.close()
        flash("Statut mis à jour.", "success")
    return redirect(url_for("admin_revue_submissions"))


@app.route("/admin/revue/soumissions/<int:sub_id>/telecharger")
@roles_required("expert", "admin")
def admin_revue_submission_download(sub_id):
    conn = dbm.get_db()
    sub = conn.execute("SELECT * FROM review_submissions WHERE id=?", (sub_id,)).fetchone()
    conn.close()
    if sub is None:
        abort(404)
    return send_from_directory(
        os.path.join(UPLOAD_DIR, "revue", "soumissions"), sub["stored_name"],
        as_attachment=True, download_name=sub["original_name"],
    )


# ---------------------------------------------------------------------------
# Admin — Corpus juridique (fondation de l'assistant de recherche IA)
# ---------------------------------------------------------------------------

@app.route("/admin/corpus")
@roles_required("expert", "admin")
def admin_corpus_list():
    q = request.args.get("q", "").strip()
    source_type = request.args.get("source_type", "")
    conn = dbm.get_db()
    query = "SELECT * FROM legal_corpus_documents WHERE 1=1"
    params = []
    if q:
        query += " AND (title LIKE ? OR full_text LIKE ? OR citation_reference LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like]
    if source_type:
        query += " AND source_type=?"
        params.append(source_type)
    query += " ORDER BY created_at DESC"
    docs = conn.execute(query, params).fetchall()
    conn.close()
    return render_template(
        "admin/corpus_list.html", docs=docs, source_types=dbm.CORPUS_SOURCE_TYPES,
        q=q, source_type=source_type,
    )


@app.route("/admin/corpus/nouveau", methods=["GET", "POST"])
@roles_required("expert", "admin")
def admin_corpus_new():
    if request.method == "POST":
        return _save_corpus_document(None)
    return render_template("admin/corpus_form.html", doc=None, source_types=dbm.CORPUS_SOURCE_TYPES)


@app.route("/admin/corpus/<int:doc_id>/modifier", methods=["GET", "POST"])
@roles_required("expert", "admin")
def admin_corpus_edit(doc_id):
    conn = dbm.get_db()
    doc = conn.execute("SELECT * FROM legal_corpus_documents WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    if doc is None:
        abort(404)
    if request.method == "POST":
        return _save_corpus_document(doc_id)
    return render_template("admin/corpus_form.html", doc=doc, source_types=dbm.CORPUS_SOURCE_TYPES)


@app.route("/admin/corpus/<int:doc_id>")
@roles_required("expert", "admin")
def admin_corpus_view(doc_id):
    conn = dbm.get_db()
    doc = conn.execute("SELECT * FROM legal_corpus_documents WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    if doc is None:
        abort(404)
    return render_template("admin/corpus_view.html", doc=doc)


def _save_corpus_document(doc_id):
    u = current_user()
    title = request.form.get("title", "").strip()
    source_type = request.form.get("source_type", "autre")
    if source_type not in dbm.CORPUS_SOURCE_LABELS:
        source_type = "autre"
    citation_reference = request.form.get("citation_reference", "").strip()
    full_text = request.form.get("full_text", "").strip()

    if not (title and full_text):
        flash("Titre et texte intégral sont obligatoires.", "error")
        return redirect(request.referrer or url_for("admin_corpus_list"))

    conn = dbm.get_db()
    if doc_id is None:
        conn.execute(
            "INSERT INTO legal_corpus_documents (title, source_type, citation_reference, full_text, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (title, source_type, citation_reference, full_text, u["id"], dbm.now(), dbm.now()),
        )
        conn.commit()
        dbm.log_activity(u["id"], "corpus_document_created", title)
        flash("Texte ajouté au corpus.", "success")
    else:
        conn.execute(
            "UPDATE legal_corpus_documents SET title=?, source_type=?, citation_reference=?, full_text=?, updated_at=? WHERE id=?",
            (title, source_type, citation_reference, full_text, dbm.now(), doc_id),
        )
        conn.commit()
        dbm.log_activity(u["id"], "corpus_document_updated", title)
        flash("Texte mis à jour.", "success")
    conn.close()
    return redirect(url_for("admin_corpus_list"))


@app.route("/admin/corpus/<int:doc_id>/supprimer", methods=["POST"])
@roles_required("admin")
def admin_corpus_delete(doc_id):
    conn = dbm.get_db()
    conn.execute("DELETE FROM legal_corpus_documents WHERE id=?", (doc_id,))
    conn.commit()
    conn.close()
    flash("Texte supprimé du corpus.", "success")
    return redirect(url_for("admin_corpus_list"))


# ---------------------------------------------------------------------------
# Contract Intelligence — administration de la bibliothèque de clauses
# ---------------------------------------------------------------------------

@app.route("/admin/clauses")
@roles_required("expert", "admin")
def admin_clauses_list():
    conn = dbm.get_db()
    clauses = conn.execute("SELECT * FROM contract_clauses ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template(
        "admin/clauses_list.html", clauses=clauses, categories=dbm.CLAUSE_CATEGORIES,
        risk_labels=dbm.CLAUSE_RISK_LABELS, category_labels=dbm.CLAUSE_CATEGORY_LABELS,
    )


@app.route("/admin/clauses/nouvelle", methods=["GET", "POST"])
@roles_required("expert", "admin")
def admin_clause_new():
    if request.method == "POST":
        return _save_clause(None)
    return render_template("admin/clause_form.html", clause=None, categories=dbm.CLAUSE_CATEGORIES, risk_levels=dbm.CLAUSE_RISK_LEVELS)


@app.route("/admin/clauses/<int:clause_id>/modifier", methods=["GET", "POST"])
@roles_required("expert", "admin")
def admin_clause_edit(clause_id):
    conn = dbm.get_db()
    clause = conn.execute("SELECT * FROM contract_clauses WHERE id=?", (clause_id,)).fetchone()
    conn.close()
    if clause is None:
        abort(404)
    if request.method == "POST":
        return _save_clause(clause_id)
    return render_template("admin/clause_form.html", clause=clause, categories=dbm.CLAUSE_CATEGORIES, risk_levels=dbm.CLAUSE_RISK_LEVELS)


def _save_clause(clause_id):
    u = current_user()
    title = request.form.get("title", "").strip()
    category = request.form.get("category", "general")
    if category not in dbm.CLAUSE_CATEGORY_LABELS:
        category = "general"
    risk_level = request.form.get("risk_level", "standard")
    if risk_level not in dbm.CLAUSE_RISK_LABELS:
        risk_level = "standard"
    body_text = request.form.get("body_text", "").strip()
    guidance = request.form.get("guidance", "").strip()

    if not (title and body_text):
        flash("Titre et texte de la clause sont obligatoires.", "error")
        return redirect(request.referrer or url_for("admin_clauses_list"))

    conn = dbm.get_db()
    if clause_id is None:
        conn.execute(
            "INSERT INTO contract_clauses (title, category, risk_level, body_text, guidance, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (title, category, risk_level, body_text, guidance, u["id"], dbm.now(), dbm.now()),
        )
        conn.commit()
        dbm.log_activity(u["id"], "clause_created", title)
        flash("Clause ajoutée à la bibliothèque.", "success")
    else:
        conn.execute(
            "UPDATE contract_clauses SET title=?, category=?, risk_level=?, body_text=?, guidance=?, updated_at=? WHERE id=?",
            (title, category, risk_level, body_text, guidance, dbm.now(), clause_id),
        )
        conn.commit()
        dbm.log_activity(u["id"], "clause_updated", title)
        flash("Clause mise à jour.", "success")
    conn.close()
    return redirect(url_for("admin_clauses_list"))


@app.route("/admin/clauses/<int:clause_id>/supprimer", methods=["POST"])
@roles_required("admin")
def admin_clause_delete(clause_id):
    conn = dbm.get_db()
    conn.execute("DELETE FROM contract_clauses WHERE id=?", (clause_id,))
    conn.commit()
    conn.close()
    flash("Clause supprimée.", "success")
    return redirect(url_for("admin_clauses_list"))


# ---------------------------------------------------------------------------
# CLI helper: create first admin
# ---------------------------------------------------------------------------

@app.cli.command("create-admin")
def create_admin():
    import getpass
    email = input("Courriel admin: ").strip().lower()
    full_name = input("Nom complet: ").strip()
    password = getpass.getpass("Mot de passe: ")
    conn = dbm.get_db()
    existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if existing:
        conn.execute("UPDATE users SET role='admin' WHERE id=?", (existing["id"],))
        print(f"Utilisateur existant {email} promu admin.")
    else:
        conn.execute(
            "INSERT INTO users (email, password_hash, full_name, role, created_at) VALUES (?,?,?,?,?)",
            (email, generate_password_hash(password), full_name, "admin", dbm.now()),
        )
        print(f"Admin {email} créé.")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
