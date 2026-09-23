import os
import secrets
import functools
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash,
    send_from_directory, send_file, abort, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import db as dbm
import totp
import payments
import signing

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


@app.context_processor
def inject_user():
    return {"current_user": current_user(), "status_labels": dbm.STATUS_LABELS}


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
    return render_template("marketing/home.html")


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
    conn.close()
    return render_template("portal/dashboard.html", dossiers=dossiers)


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
    conn.close()
    return render_template(
        "portal/dossier.html",
        dossier=dossier, documents=documents, messages=messages, tasks=tasks,
        status_log=status_log, is_staff=is_staff, staff=staff,
        status_flow=dbm.STATUS_FLOW, doc_categories=dbm.DOC_CATEGORIES,
        payment_rows=payment_rows, signature_rows=signature_rows,
        payment_purposes=dbm.PAYMENT_PURPOSES, payment_purposes_map=dict(dbm.PAYMENT_PURPOSES),
        payments_configured=payments.is_configured(),
    )


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
