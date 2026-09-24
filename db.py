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

CREATE TABLE IF NOT EXISTS review_authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    title TEXT,
    bio TEXT,
    bio_en TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    title_en TEXT,
    author_name TEXT NOT NULL,
    author_slug TEXT REFERENCES review_authors(slug),
    issue_label TEXT,
    tags TEXT,
    abstract TEXT,
    abstract_en TEXT,
    body_html TEXT,
    body_html_en TEXT,
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
    title_en TEXT,
    body TEXT NOT NULL,
    body_en TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_forum_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES review_forum_posts(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
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
    conn.executescript(CORPUS_SCHEMA)
    conn.commit()
    _seed_review_demo_content(conn)
    conn.close()


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

    author_count = conn.execute("SELECT COUNT(*) AS c FROM review_authors").fetchone()["c"]
    if author_count == 0:
        conn.execute(
            "INSERT INTO review_authors (slug, name, title, bio, bio_en, created_at) VALUES (?,?,?,?,?,?)",
            (
                "jessy-j-masse",
                "Me. Jessy J. Massé",
                "Fondateur, Massey Contracts & Tax",
                "Juriste spécialisé en ingénierie contractuelle et fiscale, fondateur de Massey Contracts & Tax. Ses travaux portent sur le droit des contrats commerciaux, la fiscalité des affaires et les transactions transfrontalières en Haïti.",
                "Lawyer specializing in contract and tax engineering, founder of Massey Contracts & Tax. His work focuses on commercial contract law, business taxation and cross-border transactions in Haiti.",
                now(),
            ),
        )
        conn.execute(
            "INSERT INTO review_authors (slug, name, title, bio, bio_en, created_at) VALUES (?,?,?,?,?,?)",
            (
                "equipe-editoriale",
                "Équipe éditoriale — Massey Law Review",
                "Comité éditorial",
                "L'équipe éditoriale de Massey Law Review coordonne la relecture, la publication et l'animation du forum de la revue, en appui aux contributions des praticiens et chercheurs invités.",
                "The Massey Law Review editorial team coordinates review, publication and forum moderation for the journal, supporting contributions from invited practitioners and researchers.",
                now(),
            ),
        )
        conn.commit()

    article_count = conn.execute("SELECT COUNT(*) AS c FROM review_articles").fetchone()["c"]
    if article_count == 0:
        conn.execute(
            "INSERT INTO review_articles "
            "(slug, title, title_en, author_name, author_slug, issue_label, tags, abstract, abstract_en, body_html, body_html_en, published, created_by, created_at, published_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "clause-penale-contrats-commerciaux-droit-haitien",
                "La clause pénale dans les contrats commerciaux : portée et limites en droit haïtien",
                "Penalty Clauses in Commercial Contracts: Scope and Limits under Haitian Law",
                "Me. Jessy J. Massé",
                "jessy-j-masse",
                "Vol. 1, n° 1",
                "droit des contrats,clause pénale,droit haïtien",
                "Cet article examine les conditions de validité de la clause pénale en droit haïtien, son articulation avec le pouvoir modérateur du juge, et les précautions rédactionnelles recommandées dans les contrats commerciaux.",
                "This article examines the conditions of validity of penalty clauses under Haitian law, their interaction with the judge's moderating power, and recommended drafting precautions in commercial contracts.",
                "<p>La clause pénale demeure l'un des mécanismes contractuels les plus utilisés pour sécuriser l'exécution d'une obligation, tout en restant l'un des plus mal maîtrisés dans la pratique rédactionnelle courante.</p>"
                "<h2 id=\"fonction-et-validite\">Fonction et validité</h2><p>La clause pénale fixe par avance le montant des dommages-intérêts dus en cas d'inexécution, évitant ainsi le recours à une évaluation judiciaire a posteriori<sup id=\"fnref-1\"><a href=\"#fn-1\">1</a></sup>. Sa validité suppose une rédaction claire de l'obligation principale et du fait générateur de la pénalité.</p>"
                "<h2 id=\"pouvoir-moderateur-du-juge\">Le pouvoir modérateur du juge</h2><p>Le juge conserve la faculté de réduire une pénalité manifestement excessive, ou de l'augmenter si elle est dérisoire<sup id=\"fnref-2\"><a href=\"#fn-2\">2</a></sup>. Une rédaction équilibrée, adossée à une évaluation réaliste du préjudice prévisible, réduit le risque de révision judiciaire.</p>"
                "<h2 id=\"recommandations-pratiques\">Recommandations pratiques</h2><p>Il est recommandé de documenter la méthode de calcul de la pénalité, de la proportionner à la gravité prévisible du manquement, et de la distinguer clairement des clauses de résiliation et d'indemnisation.</p>"
                "<div class=\"footnotes\"><ol>"
                "<li id=\"fn-1\">Code civil haïtien, dispositions relatives aux obligations conditionnelles et aux clauses pénales. <a href=\"#fnref-1\">↩</a></li>"
                "<li id=\"fn-2\">Voir la jurisprudence constante des tribunaux civils sur le pouvoir modérateur, par analogie avec les principes du droit civil français dont s'inspire le droit haïtien. <a href=\"#fnref-2\">↩</a></li>"
                "</ol></div>",
                "<p>Penalty clauses remain one of the most widely used contractual mechanisms to secure performance of an obligation, while also being one of the least well handled in current drafting practice.</p>"
                "<h2 id=\"function-and-validity\">Function and validity</h2><p>A penalty clause sets in advance the amount of damages owed in case of non-performance, avoiding a later judicial assessment. Its validity requires a clear drafting of the principal obligation and the triggering event.</p>"
                "<h2 id=\"the-judges-moderating-power\">The judge's moderating power</h2><p>The judge retains the power to reduce a manifestly excessive penalty, or increase one that is derisory. Balanced drafting, backed by a realistic assessment of foreseeable harm, reduces the risk of judicial revision.</p>"
                "<h2 id=\"practical-recommendations\">Practical recommendations</h2><p>It is recommended to document the penalty's calculation method, proportion it to the foreseeable severity of the breach, and clearly distinguish it from termination and indemnification clauses.</p>",
                1, editorial_user_id, now(), now(),
            ),
        )
        conn.execute(
            "INSERT INTO review_articles "
            "(slug, title, title_en, author_name, author_slug, issue_label, tags, abstract, abstract_en, body_html, body_html_en, published, created_by, created_at, published_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "structuration-fiscale-contrats-distribution-transfrontaliers",
                "Structuration fiscale des contrats de distribution transfrontaliers",
                "Tax Structuring of Cross-Border Distribution Agreements",
                "Équipe éditoriale — Massey Law Review",
                "equipe-editoriale",
                "Vol. 1, n° 1",
                "fiscalité des affaires,transactions transfrontalières,droit des contrats",
                "Une analyse des enjeux fiscaux propres aux accords de distribution impliquant des partenaires haïtiens et étrangers, et des pistes de structuration licite pour limiter les zones d'incertitude.",
                "An analysis of the tax issues specific to distribution agreements involving Haitian and foreign partners, and lawful structuring approaches to reduce areas of uncertainty.",
                "<p>Les contrats de distribution transfrontaliers soulèvent des questions fiscales spécifiques, souvent sous-estimées au moment de la négociation commerciale.</p>"
                "<h2 id=\"qualification-des-flux\">Qualification des flux</h2><p>La qualification exacte des paiements échangés (redevances, commissions, prix de revente) conditionne leur traitement fiscal et les obligations déclaratives applicables à chaque partie.</p>"
                "<h2 id=\"risques-de-requalification\">Risques de requalification</h2><p>Une rédaction imprécise des obligations réciproques expose les parties à un risque de requalification par l'administration fiscale, avec des conséquences sur les retenues à la source applicables.</p>"
                "<h2 id=\"pistes-de-structuration\">Pistes de structuration</h2><p>Une documentation contractuelle rigoureuse, assortie d'une analyse fiscale préalable, permet de sécuriser la relation commerciale tout en réduisant l'exposition aux risques de double imposition.</p>",
                "<p>Cross-border distribution agreements raise specific tax questions that are often underestimated at the time of commercial negotiation.</p>"
                "<h2 id=\"characterizing-payment-flows\">Characterizing payment flows</h2><p>The precise characterization of payments exchanged (royalties, commissions, resale prices) determines their tax treatment and the reporting obligations applicable to each party.</p>"
                "<h2 id=\"requalification-risks\">Requalification risks</h2><p>Imprecise drafting of reciprocal obligations exposes parties to a risk of requalification by the tax administration, with consequences for applicable withholding taxes.</p>"
                "<h2 id=\"structuring-approaches\">Structuring approaches</h2><p>Rigorous contractual documentation, combined with an upfront tax analysis, helps secure the business relationship while reducing exposure to double-taxation risk.</p>",
                1, editorial_user_id, now(), now(),
            ),
        )
        conn.commit()

    forum_count = conn.execute("SELECT COUNT(*) AS c FROM review_forum_posts").fetchone()["c"]
    if forum_count == 0:
        cur1 = conn.execute(
            "INSERT INTO review_forum_posts (user_id, title, title_en, body, body_en, created_at) VALUES (?,?,?,?,?,?)",
            (
                editorial_user_id,
                "Quelle portée donner à une clause de médiation préalable dans un contrat CrossBorder ?",
                "How much weight should a mandatory mediation clause carry in a cross-border contract?",
                "Dans nos dossiers transfrontaliers récents, nous observons une multiplication des clauses de médiation préalable obligatoire avant toute action judiciaire ou arbitrale. Comment articulez-vous ces clauses avec les délais de prescription applicables, notamment lorsque les parties relèvent de juridictions différentes ? Le sujet mériterait un examen approfondi dans un prochain numéro.",
                "In our recent cross-border files, we're seeing more mandatory mediation clauses required before any judicial or arbitral action. How do you reconcile these clauses with applicable limitation periods, especially when the parties are subject to different jurisdictions? This would be worth an in-depth look in an upcoming issue.",
                now(),
            ),
        )
        post1_id = cur1.lastrowid
        conn.execute(
            "INSERT INTO review_forum_posts (user_id, title, title_en, body, body_en, created_at) VALUES (?,?,?,?,?,?)",
            (
                editorial_user_id,
                "Retour d'expérience : négociation d'une clause de non-concurrence avec un partenaire dominicain",
                "Field notes: negotiating a non-compete clause with a Dominican partner",
                "Sur un dossier récent impliquant un partenaire commercial basé en République dominicaine, la question de l'étendue territoriale et temporelle raisonnable d'une clause de non-concurrence a fait l'objet d'échanges approfondis. Quelle est votre pratique sur la durée maximale généralement admise dans ce type d'accord bilatéral ?",
                "On a recent file involving a commercial partner based in the Dominican Republic, the reasonable territorial and time scope of a non-compete clause led to extensive discussion. What's your practice on the maximum duration generally accepted in this type of bilateral agreement?",
                now(),
            ),
        )
        conn.execute(
            "INSERT INTO review_forum_replies (post_id, user_id, body, created_at) VALUES (?,?,?,?)",
            (
                post1_id,
                editorial_user_id,
                "Bonne question — dans la pratique, nous recommandons de prévoir une clause de suspension expresse du délai de prescription pendant la durée de la médiation, plutôt que de s'en remettre au seul droit commun. Un prochain numéro pourrait effectivement approfondir l'articulation entre médiation préalable et délais de prescription en contexte transfrontalier.",
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
