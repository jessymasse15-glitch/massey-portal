"""
Couche base de données — SQLite via le module stdlib sqlite3.
Aucune dépendance externe requise : c'est voulu, l'environnement de
build n'a pas d'accès réseau pour installer des paquets. En production,
ce module peut être remplacé par une couche Postgres sans changer les
routes (les fonctions ci-dessous sont le seul point de contact SQL).
"""
import sqlite3
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "massey.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('client','expert','admin')) DEFAULT 'client',
    language TEXT DEFAULT 'fr',
    mfa_secret TEXT,
    mfa_enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dossiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES users(id),
    assigned_to INTEGER REFERENCES users(id),
    title TEXT NOT NULL,
    service_type TEXT NOT NULL,
    pack TEXT,
    territory TEXT,
    value_estimate TEXT,
    urgency TEXT,
    language TEXT DEFAULT 'fr',
    status TEXT NOT NULL DEFAULT 'demande_recue',
    conflict_checked INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    uploaded_by INTEGER NOT NULL REFERENCES users(id),
    category TEXT NOT NULL DEFAULT 'autre',
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    is_final INTEGER NOT NULL DEFAULT 0,
    locked INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    sender_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    is_internal INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    description TEXT NOT NULL,
    owner TEXT NOT NULL CHECK(owner IN ('client','massey')) DEFAULT 'massey',
    done INTEGER NOT NULL DEFAULT 0,
    due_date TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS status_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    status TEXT NOT NULL,
    changed_by INTEGER NOT NULL REFERENCES users(id),
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    created_by INTEGER NOT NULL REFERENCES users(id),
    purpose TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'cad',
    provider_session_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    paid_at TEXT
);

CREATE TABLE IF NOT EXISTS signatures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    document_id INTEGER NOT NULL REFERENCES documents(id),
    signer_id INTEGER NOT NULL REFERENCES users(id),
    full_legal_name TEXT NOT NULL,
    document_sha256 TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    created_at TEXT NOT NULL
);
"""

STATUS_FLOW = [
    ("demande_recue", "Demande reçue"),
    ("verification_conflit", "Vérification des conflits d'intérêts"),
    ("mandat_en_attente", "Mandat en attente"),
    ("informations_requises", "Informations requises"),
    ("analyse_en_cours", "Analyse en cours"),
    ("projet_transmis", "Projet transmis"),
    ("negociation", "Négociation"),
    ("signature", "Signature"),
    ("dossier_clos", "Dossier clos"),
]
STATUS_LABELS = dict(STATUS_FLOW)

DOC_CATEGORIES = ["contrat", "annexe", "facture", "fiscalite", "conformite", "preuve", "correspondance", "version_finale"]

SERVICE_TYPES = [
    ("redaction_contrat", "Rédaction de contrat commercial"),
    ("revision_contrat", "Révision contractuelle"),
    ("negociation_contrat", "Négociation de contrat"),
    ("audit_portefeuille", "Audit de portefeuille contractuel"),
    ("diagnostic_fiscal", "Diagnostic fiscal précontractuel"),
    ("revue_fiscale", "Revue fiscale d'un contrat"),
    ("structuration_fiscale", "Structuration fiscale licite"),
    ("transaction_internationale", "Transaction internationale / transfrontalière"),
    ("due_diligence", "Due diligence"),
    ("autre", "Autre demande"),
]

PACKS = ["Massey Start", "Massey Review", "Massey Business", "Massey CrossBorder", "Massey Counsel", "Massey Due Diligence", "Non déterminé"]

PAYMENT_PURPOSES = [
    ("consultation", "Consultation de cadrage"),
    ("acompte", "Acompte sur mandat"),
    ("solde", "Solde de facture"),
    ("abonnement_counsel", "Abonnement Massey Counsel (mensuel)"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def log_activity(user_id, action, detail=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO activity_log (user_id, action, detail, created_at) VALUES (?,?,?,?)",
        (user_id, action, detail, now()),
    )
    conn.commit()
    conn.close()
