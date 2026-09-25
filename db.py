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

# En production (Render), DATA_DIR pointe vers le disque persistant unique
# (un seul disque autorisé par service). En local, ça retombe sur instance/.
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "instance"))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "massey.db")

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

-- -----------------------------------------------------------------------
-- Massey Law Review (revue juridique en ligne, section du portail)
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS review_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    author_name TEXT NOT NULL,
    issue_label TEXT,
    abstract TEXT,
    body_html TEXT,
    pdf_stored_name TEXT,
    pdf_original_name TEXT,
    published INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS review_forum_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    author_name TEXT NOT NULL,
    email TEXT NOT NULL,
    title TEXT NOT NULL,
    abstract TEXT,
    stored_name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'recue',
    note_interne TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    plan TEXT NOT NULL DEFAULT 'lecteur',
    status TEXT NOT NULL DEFAULT 'pending',
    provider_session_id TEXT,
    created_at TEXT NOT NULL,
    paid_at TEXT
);
"""

REVIEW_SUBMISSION_STATUSES = [
    ("recue", "Reçue"),
    ("en_evaluation", "En évaluation par le comité"),
    ("revisions_demandees", "Révisions demandées"),
    ("acceptee", "Acceptée"),
    ("refusee", "Refusée"),
    ("publiee", "Publiée"),
]
REVIEW_SUBMISSION_LABELS = dict(REVIEW_SUBMISSION_STATUSES)

REVIEW_PLANS = [
    ("lecteur", "Massey Law Review — Lecteur", 999, "Accès complet aux numéros publiés, en ligne et en PDF."),
    ("institution", "Massey Law Review — Institution", 4999, "Accès multi-utilisateurs pour cabinets, universités et centres de recherche."),
]
REVIEW_PLAN_LABELS = {p[0]: p[1] for p in REVIEW_PLANS}

# -----------------------------------------------------------------------
# Corpus juridique (fondation de l'assistant de recherche IA — phase 0)
# -----------------------------------------------------------------------

CORPUS_SCHEMA = """
CREATE TABLE IF NOT EXISTS legal_corpus_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'autre',
    citation_reference TEXT,
    full_text TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CORPUS_SOURCE_TYPES = [
    ("constitution", "Constitution"),
    ("code", "Code (civil, commerce, travail, fiscal…)"),
    ("decret", "Décret / arrêté"),
    ("jurisprudence", "Jurisprudence"),
    ("doctrine", "Doctrine / article"),
    ("autre", "Autre"),
]
CORPUS_SOURCE_LABELS = dict(CORPUS_SOURCE_TYPES)

# -----------------------------------------------------------------------
# Contract Intelligence — bibliothèque de clauses et modèles
# -----------------------------------------------------------------------

PLATFORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS contract_clauses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    risk_level TEXT NOT NULL DEFAULT 'standard',
    body_text TEXT NOT NULL,
    guidance TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tax_obligations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    label TEXT NOT NULL,
    tax_type TEXT NOT NULL DEFAULT 'autre',
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'a_faire',
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS compliance_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    label TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'kyc',
    status TEXT NOT NULL DEFAULT 'a_faire',
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS due_diligence_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    label TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'juridique',
    status TEXT NOT NULL DEFAULT 'a_verifier',
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    document_a_id INTEGER NOT NULL REFERENCES documents(id),
    document_b_id INTEGER NOT NULL REFERENCES documents(id),
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS contact_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    subject TEXT,
    message TEXT NOT NULL,
    handled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    token TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
"""

# Paramètres du site, éditables par un admin depuis /admin/parametres, avec des
# valeurs par défaut volontairement vides ou explicitement "à compléter" — on ne
# fabrique jamais une fausse adresse, un faux numéro d'entreprise ou un faux
# numéro de téléphone à la place du vrai titulaire de la plateforme.
DEFAULT_SITE_SETTINGS = {
    "company_legal_name": "",
    "business_number": "",
    "address": "",
    "phone": "",
    "contact_email": "",
    "linkedin_url": "",
    "support_hours": "",
}

SITE_SETTINGS_LABELS = [
    ("company_legal_name", "Raison sociale complète"),
    ("business_number", "Numéro d'entreprise / immatriculation"),
    ("address", "Adresse postale"),
    ("phone", "Téléphone"),
    ("contact_email", "Courriel de contact public"),
    ("linkedin_url", "URL LinkedIn"),
    ("support_hours", "Horaires de disponibilité"),
]


def get_setting(key, default=""):
    conn = get_db()
    row = conn.execute("SELECT value FROM site_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row is None:
        return DEFAULT_SITE_SETTINGS.get(key, default)
    return row["value"]


def get_all_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM site_settings").fetchall()
    conn.close()
    values = dict(DEFAULT_SITE_SETTINGS)
    values.update({r["key"]: r["value"] for r in rows})
    return values


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO site_settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()

CLAUSE_CATEGORIES = [
    ("general", "Clauses générales"),
    ("paiement", "Paiement et facturation"),
    ("resiliation", "Résiliation"),
    ("penale", "Clause pénale / indemnisation"),
    ("confidentialite", "Confidentialité"),
    ("non_concurrence", "Non-concurrence"),
    ("force_majeure", "Force majeure"),
    ("reglement_differends", "Règlement des différends"),
    ("fiscalite", "Fiscalité et retenues"),
    ("conformite", "Conformité et déclarations"),
]
CLAUSE_CATEGORY_LABELS = dict(CLAUSE_CATEGORIES)

CLAUSE_RISK_LEVELS = [
    ("standard", "Standard"),
    ("a_negocier", "À négocier avec prudence"),
    ("sensible", "Sensible — validation requise"),
]
CLAUSE_RISK_LABELS = dict(CLAUSE_RISK_LEVELS)

# Pipeline transactionnel unifié (Transaction Intelligence) — chaque statut
# granulaire du suivi de dossier se rattache à l'une des 7 grandes étapes
# du moteur d'intégration CRÉER → NÉGOCIER → APPROUVER → SIGNER → EXÉCUTER
# → SURVEILLER → RENOUVELER.
TRANSACTION_STAGES = [
    ("creer", "Créer"),
    ("negocier", "Négocier"),
    ("approuver", "Approuver"),
    ("signer", "Signer"),
    ("executer", "Exécuter"),
    ("surveiller", "Surveiller"),
    ("renouveler", "Renouveler"),
]
TRANSACTION_STAGE_LABELS = dict(TRANSACTION_STAGES)

STATUS_TO_STAGE = {
    "demande_recue": "creer",
    "verification_conflit": "creer",
    "mandat_en_attente": "creer",
    "informations_requises": "negocier",
    "analyse_en_cours": "negocier",
    "projet_transmis": "negocier",
    "negociation": "negocier",
    "signature": "signer",
    "dossier_clos": "surveiller",
}

TAX_TYPES = [
    ("tca", "Taxe sur le chiffre d'affaires (TCA)"),
    ("impot_revenu", "Impôt sur le revenu"),
    ("retenue_source", "Retenue à la source"),
    ("patente", "Patente / licence commerciale"),
    ("douane", "Droits de douane / import-export"),
    ("autre", "Autre obligation fiscale"),
]
TAX_TYPE_LABELS = dict(TAX_TYPES)

TAX_OBLIGATION_STATUSES = [
    ("a_faire", "À faire"),
    ("en_cours", "En cours"),
    ("fait", "Acquittée"),
    ("en_retard", "En retard"),
]
TAX_OBLIGATION_STATUS_LABELS = dict(TAX_OBLIGATION_STATUSES)

COMPLIANCE_CATEGORIES = [
    ("kyc", "KYC — Connaissance du client"),
    ("aml", "AML — Lutte contre le blanchiment"),
    ("sanctions", "Sanctions et listes de contrôle"),
    ("autorisation", "Autorisations et licences"),
    ("reporting", "Reporting réglementaire"),
]
COMPLIANCE_CATEGORY_LABELS = dict(COMPLIANCE_CATEGORIES)

COMPLIANCE_STATUSES = [
    ("a_faire", "À faire"),
    ("en_cours", "En cours"),
    ("conforme", "Conforme"),
    ("non_conforme", "Non conforme"),
]
COMPLIANCE_STATUS_LABELS = dict(COMPLIANCE_STATUSES)

DILIGENCE_CATEGORIES = [
    ("juridique", "Juridique et corporate"),
    ("fiscal", "Fiscal"),
    ("commercial", "Commercial et contractuel"),
    ("financier", "Financier et comptable"),
    ("rh_social", "Ressources humaines et social"),
    ("environnemental", "Environnemental et réglementaire"),
    ("autre", "Autre"),
]
DILIGENCE_CATEGORY_LABELS = dict(DILIGENCE_CATEGORIES)

DILIGENCE_STATUSES = [
    ("a_verifier", "À vérifier"),
    ("en_cours", "En cours"),
    ("valide", "Validé"),
    ("probleme", "Problème identifié"),
]
DILIGENCE_STATUS_LABELS = dict(DILIGENCE_STATUSES)

# Checklist de due diligence standard, générée en un clic sur un dossier
# (Transaction Intelligence) — couvre les grands axes d'une revue préalable
# à une transaction, à affiner ensuite selon le dossier.
DEFAULT_DILIGENCE_CHECKLIST = [
    ("Statuts et registre des actionnaires à jour", "juridique"),
    ("Pouvoirs des signataires et résolutions habilitantes", "juridique"),
    ("Litiges en cours ou menaces de litige", "juridique"),
    ("Situation fiscale et déclarations des trois derniers exercices", "fiscal"),
    ("Dettes fiscales ou redressements en cours", "fiscal"),
    ("Contrats commerciaux significatifs et clauses de changement de contrôle", "commercial"),
    ("Propriété intellectuelle et licences détenues ou concédées", "commercial"),
    ("États financiers et engagements hors bilan", "financier"),
    ("Contrats de travail clés et engagements sociaux", "rh_social"),
    ("Autorisations, permis et conformité réglementaire sectorielle", "environnemental"),
]

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


# -----------------------------------------------------------------------
# Étiquettes anglaises — mêmes clés que les listes françaises ci-dessus,
# utilisées uniquement par les pages vitrines /en/... pour afficher les
# mêmes données (mêmes valeurs stockées en base) dans l'autre langue.
# -----------------------------------------------------------------------

CORPUS_SOURCE_TYPES_EN = [
    ("constitution", "Constitution"),
    ("code", "Code (civil, commercial, labor, tax…)"),
    ("decret", "Decree / order"),
    ("jurisprudence", "Case law"),
    ("doctrine", "Doctrine / article"),
    ("autre", "Other"),
]

CLAUSE_CATEGORIES_EN = [
    ("general", "General clauses"),
    ("paiement", "Payment and invoicing"),
    ("resiliation", "Termination"),
    ("penale", "Penalty / indemnification clause"),
    ("confidentialite", "Confidentiality"),
    ("non_concurrence", "Non-compete"),
    ("force_majeure", "Force majeure"),
    ("reglement_differends", "Dispute resolution"),
    ("fiscalite", "Taxation and withholding"),
    ("conformite", "Compliance and representations"),
]

CLAUSE_RISK_LABELS_EN = {
    "standard": "Standard",
    "a_negocier": "Negotiate with care",
    "sensible": "Sensitive — approval required",
}

TRANSACTION_STAGES_EN = [
    ("creer", "Create"),
    ("negocier", "Negotiate"),
    ("approuver", "Approve"),
    ("signer", "Sign"),
    ("executer", "Execute"),
    ("surveiller", "Monitor"),
    ("renouveler", "Renew"),
]

TAX_TYPE_LABELS_EN = {
    "tca": "Sales / turnover tax",
    "impot_revenu": "Income tax",
    "retenue_source": "Withholding tax",
    "patente": "Business license tax",
    "douane": "Customs duties / import-export",
    "autre": "Other tax obligation",
}

TAX_STATUS_LABELS_EN = {
    "a_faire": "To do",
    "en_cours": "In progress",
    "fait": "Completed",
    "en_retard": "Overdue",
}

COMPLIANCE_CATEGORY_LABELS_EN = {
    "kyc": "KYC — Know Your Customer",
    "aml": "AML — Anti-Money Laundering",
    "sanctions": "Sanctions and watchlists",
    "autorisation": "Authorizations and licenses",
    "reporting": "Regulatory reporting",
}

COMPLIANCE_STATUS_LABELS_EN = {
    "a_faire": "To do",
    "en_cours": "In progress",
    "conforme": "Compliant",
    "non_conforme": "Non-compliant",
}


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
    conn.executescript(CORPUS_SCHEMA)
    conn.executescript(PLATFORM_SCHEMA)
    conn.commit()
    _seed_review_demo_content(conn)
    _seed_clause_library(conn)
    conn.close()


def _seed_clause_library(conn):
    """Quelques clauses de départ pour que la bibliothèque Contract Intelligence
    ne soit jamais vide — sans effet si des clauses existent déjà."""
    count = conn.execute("SELECT COUNT(*) AS c FROM contract_clauses").fetchone()["c"]
    if count:
        return
    editorial = conn.execute("SELECT id FROM users WHERE email='revue@masseylawreview.local'").fetchone()
    creator_id = editorial["id"] if editorial else None
    clauses = [
        ("Clause pénale standard", "penale", "a_negocier",
         "En cas de manquement à l'une quelconque des obligations prévues aux présentes, la partie défaillante sera tenue "
         "de verser à l'autre partie une pénalité forfaitaire de [montant/pourcentage], sans préjudice de tout autre "
         "recours disponible en droit.",
         "Proportionner la pénalité au préjudice prévisible ; documenter la méthode de calcul pour limiter le risque de "
         "révision judiciaire (pouvoir modérateur du juge)."),
        ("Confidentialité réciproque", "confidentialite", "standard",
         "Chaque partie s'engage à garder strictement confidentielle toute information commerciale, financière ou "
         "technique communiquée par l'autre partie dans le cadre du présent contrat, et à ne l'utiliser qu'aux fins "
         "de l'exécution de ses obligations.",
         "Prévoir une durée de survie de l'obligation après la fin du contrat (généralement 2 à 5 ans)."),
        ("Résiliation pour manquement", "resiliation", "standard",
         "Chaque partie peut résilier le présent contrat de plein droit, après mise en demeure restée sans effet "
         "pendant [30] jours, en cas de manquement grave de l'autre partie à ses obligations essentielles.",
         "Définir précisément ce qui constitue un « manquement grave » pour éviter toute ambiguïté d'interprétation."),
        ("Non-concurrence territoriale et temporelle", "non_concurrence", "a_negocier",
         "Pendant la durée du contrat et pour une période de [12] mois suivant sa terminaison, la partie s'engage à "
         "ne pas exercer, directement ou indirectement, une activité concurrente dans le territoire de [territoire].",
         "L'étendue territoriale et temporelle doit rester raisonnable et proportionnée à l'intérêt légitime protégé, "
         "sous peine d'invalidation."),
        ("Force majeure", "force_majeure", "standard",
         "Aucune partie ne sera tenue responsable de l'inexécution de ses obligations si celle-ci résulte d'un cas de "
         "force majeure, entendu comme un événement imprévisible, irrésistible et extérieur aux parties.",
         "Lister des exemples non exhaustifs pertinents au contexte haïtien (catastrophe naturelle, instabilité "
         "politique majeure, décision gouvernementale) pour faciliter l'application de la clause."),
        ("Règlement des différends — médiation préalable", "reglement_differends", "a_negocier",
         "Tout différend relatif au présent contrat fera l'objet d'une tentative de médiation préalable avant toute "
         "action judiciaire ou arbitrale, les parties disposant d'un délai de [60] jours pour parvenir à un accord.",
         "Articuler soigneusement le délai de médiation avec les délais de prescription applicables, en particulier "
         "dans un contexte transfrontalier."),
        ("Retenue à la source sur paiements transfrontaliers", "fiscalite", "sensible",
         "Les paiements effectués au titre du présent contrat pourront faire l'objet d'une retenue à la source "
         "conformément à la législation fiscale applicable ; la partie payeuse remettra à l'autre partie tout "
         "justificatif de retenue applicable.",
         "Qualifier précisément la nature des flux (redevances, commissions, prix) pour déterminer le régime fiscal "
         "applicable et éviter tout risque de requalification."),
        ("Déclarations et garanties de conformité", "conformite", "sensible",
         "Chaque partie déclare et garantit qu'elle se conforme à l'ensemble des lois et réglementations applicables, "
         "y compris en matière de lutte contre le blanchiment de capitaux et de sanctions économiques.",
         "Prévoir une obligation d'information immédiate en cas de changement de situation affectant ces déclarations."),
    ]
    ts = now()
    for title, category, risk, body, guidance in clauses:
        conn.execute(
            "INSERT INTO contract_clauses (title, category, risk_level, body_text, guidance, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (title, category, risk, body, guidance, creator_id, ts, ts),
        )
    conn.commit()


def _seed_review_demo_content(conn):
    """Contenu de démonstration pour Massey Law Review (deux articles, deux billets de
    forum, deux auteurs, une réponse de forum) — inséré une seule fois, uniquement si les
    tables sont vides, pour que la revue ne parte jamais complètement à vide. Sans effet
    si du vrai contenu existe déjà."""
    editorial_email = "revue@masseylawreview.local"
    row = conn.execute("SELECT id FROM users WHERE email=?", (editorial_email,)).fetchone()
    if row:
        editorial_user_id = row["id"]
    else:
        from werkzeug.security import generate_password_hash
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, full_name, role, created_at) VALUES (?,?,?,?,?)",
            (editorial_email, generate_password_hash(os.urandom(32).hex()), "Équipe éditoriale", "expert", now()),
        )
        editorial_user_id = cur.lastrowid
        conn.commit()

    article_count = conn.execute("SELECT COUNT(*) AS c FROM review_articles").fetchone()["c"]
    if article_count == 0:
        conn.execute(
            "INSERT INTO review_articles "
            "(slug, title, author_name, issue_label, abstract, body_html, published, created_by, created_at, published_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "clause-penale-contrats-commerciaux-droit-haitien",
                "La clause pénale dans les contrats commerciaux : portée et limites en droit haïtien",
                "Me. Jessy J. Massé",
                "Vol. 1, n° 1",
                "Cet article examine les conditions de validité de la clause pénale en droit haïtien, son articulation avec le pouvoir modérateur du juge, et les précautions rédactionnelles recommandées dans les contrats commerciaux.",
                "<p>La clause pénale demeure l'un des mécanismes contractuels les plus utilisés pour sécuriser l'exécution d'une obligation, tout en restant l'un des plus mal maîtrisés dans la pratique rédactionnelle courante.</p>"
                "<h2>Fonction et validité</h2><p>La clause pénale fixe par avance le montant des dommages-intérêts dus en cas d'inexécution, évitant ainsi le recours à une évaluation judiciaire a posteriori. Sa validité suppose une rédaction claire de l'obligation principale et du fait générateur de la pénalité.</p>"
                "<h2>Le pouvoir modérateur du juge</h2><p>Le juge conserve la faculté de réduire une pénalité manifestement excessive, ou de l'augmenter si elle est dérisoire. Une rédaction équilibrée, adossée à une évaluation réaliste du préjudice prévisible, réduit le risque de révision judiciaire.</p>"
                "<h2>Recommandations pratiques</h2><p>Il est recommandé de documenter la méthode de calcul de la pénalité, de la proportionner à la gravité prévisible du manquement, et de la distinguer clairement des clauses de résiliation et d'indemnisation.</p>",
                1, editorial_user_id, now(), now(),
            ),
        )
        conn.execute(
            "INSERT INTO review_articles "
            "(slug, title, author_name, issue_label, abstract, body_html, published, created_by, created_at, published_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "structuration-fiscale-contrats-distribution-transfrontaliers",
                "Structuration fiscale des contrats de distribution transfrontaliers",
                "Équipe éditoriale — Massey Law Review",
                "Vol. 1, n° 1",
                "Une analyse des enjeux fiscaux propres aux accords de distribution impliquant des partenaires haïtiens et étrangers, et des pistes de structuration licite pour limiter les zones d'incertitude.",
                "<p>Les contrats de distribution transfrontaliers soulèvent des questions fiscales spécifiques, souvent sous-estimées au moment de la négociation commerciale.</p>"
                "<h2>Qualification des flux</h2><p>La qualification exacte des paiements échangés (redevances, commissions, prix de revente) conditionne leur traitement fiscal et les obligations déclaratives applicables à chaque partie.</p>"
                "<h2>Risques de requalification</h2><p>Une rédaction imprécise des obligations réciproques expose les parties à un risque de requalification par l'administration fiscale, avec des conséquences sur les retenues à la source applicables.</p>"
                "<h2>Pistes de structuration</h2><p>Une documentation contractuelle rigoureuse, assortie d'une analyse fiscale préalable, permet de sécuriser la relation commerciale tout en réduisant l'exposition aux risques de double imposition.</p>",
                1, editorial_user_id, now(), now(),
            ),
        )
        conn.commit()

    forum_count = conn.execute("SELECT COUNT(*) AS c FROM review_forum_posts").fetchone()["c"]
    if forum_count == 0:
        conn.execute(
            "INSERT INTO review_forum_posts (user_id, title, body, created_at) VALUES (?,?,?,?)",
            (
                editorial_user_id,
                "Quelle portée donner à une clause de médiation préalable dans un contrat CrossBorder ?",
                "Dans nos dossiers transfrontaliers récents, nous observons une multiplication des clauses de médiation préalable obligatoire avant toute action judiciaire ou arbitrale. Comment articulez-vous ces clauses avec les délais de prescription applicables, notamment lorsque les parties relèvent de juridictions différentes ? Le sujet mériterait un examen approfondi dans un prochain numéro.",
                now(),
            ),
        )
        conn.execute(
            "INSERT INTO review_forum_posts (user_id, title, body, created_at) VALUES (?,?,?,?)",
            (
                editorial_user_id,
                "Retour d'expérience : négociation d'une clause de non-concurrence avec un partenaire dominicain",
                "Sur un dossier récent impliquant un partenaire commercial basé en République dominicaine, la question de l'étendue territoriale et temporelle raisonnable d'une clause de non-concurrence a fait l'objet d'échanges approfondis. Quelle est votre pratique sur la durée maximale généralement admise dans ce type d'accord bilatéral ?",
                now(),
            ),
        )
        conn.commit()


def log_activity(user_id, action, detail=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO activity_log (user_id, action, detail, created_at) VALUES (?,?,?,?)",
        (user_id, action, detail, now()),
    )
    conn.commit()
    conn.close()
