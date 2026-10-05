"""Moteur de Contract Intelligence (Massey AI).

Analyse de contrats par règles déterministes — ce n'est PAS un modèle de
langage : les signaux relevés (clauses sensibles, clauses manquantes, délais,
dates) sont produits par des motifs textuels français/anglais, ce qui rend
chaque résultat explicable et reproductible. Aucun résultat ne constitue un
avis juridique : il sert à orienter la relecture humaine.

Ce module n'a aucune dépendance obligatoire. python-docx et pypdf sont
utilisés s'ils sont installés (extraction .docx/.pdf, export .docx).
"""
import datetime
import difflib
import io
import re
import unicodedata

MAX_BYTES = 5 * 1024 * 1024
MAX_CHARS = 200_000
MIN_CHARS = 40


class ExtractionError(Exception):
    """code : too_large | unsupported | empty | unreadable | missing_dependency"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# Extraction du texte
# ---------------------------------------------------------------------------

def _normalize_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _docx_text(data):
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        raise ExtractionError("missing_dependency")
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception:
        raise ExtractionError("unreadable")
    parts = []
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            parts.append(Paragraph(child, document).text)
        elif tag == "tbl":
            for row in Table(child, document).rows:
                cells = []
                for cell in row.cells:
                    if cell.text not in cells:
                        cells.append(cell.text)
                parts.append(" | ".join(c.strip() for c in cells if c.strip()))
    return "\n".join(parts)


def _pdf_text(data):
    try:
        import pypdf
    except ImportError:
        raise ExtractionError("missing_dependency")
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractionError("unreadable")
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("unreadable")


def extract_text(data, filename):
    """Texte brut d'un fichier .txt/.md/.docx/.pdf (octets en entrée)."""
    if len(data) > MAX_BYTES:
        raise ExtractionError("too_large")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("txt", "md"):
        text = data.decode("utf-8", errors="replace")
    elif ext == "docx":
        text = _docx_text(data)
    elif ext == "pdf":
        text = _pdf_text(data)
    else:
        raise ExtractionError("unsupported")
    text = _normalize_text(text)
    if len(text) < MIN_CHARS:
        # PDF numérisé (images) ou document vide : rien à analyser.
        raise ExtractionError("empty")
    return text[:MAX_CHARS]


# ---------------------------------------------------------------------------
# Utilitaires de texte
# ---------------------------------------------------------------------------

def fold(text):
    """Minuscules sans accents, longueur strictement conservée (index alignés
    avec le texte d'origine, pour pouvoir citer l'extrait exact)."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFD", ch)[0]
        out.append(base.lower())
    return "".join(out).replace("’", "'").replace("‘", "'")


def detect_language(text):
    f = " " + fold(text[:8000]) + " "
    fr = sum(f.count(w) for w in (" le ", " la ", " les ", " des ", " du ", " et ", " est ", " au ", " pour ", " dans "))
    en = sum(f.count(w) for w in (" the ", " and ", " of ", " to ", " shall ", " is ", " for ", " in ", " by ", " with "))
    return "en" if en > fr else "fr"


_SENT_END = ".;\n"


def _sentence_around(text, start, end, radius=260):
    lo = start
    while lo > 0 and start - lo < radius and text[lo - 1] not in _SENT_END:
        lo -= 1
    hi = end
    while hi < len(text) and hi - end < radius and text[hi] not in _SENT_END:
        hi += 1
    if hi < len(text) and text[hi] == ".":
        hi += 1
    return re.sub(r"\s+", " ", text[lo:hi]).strip()


# ---------------------------------------------------------------------------
# Segmentation en clauses
# ---------------------------------------------------------------------------

_HEAD_A = re.compile(
    r"^(?:article|art\.|clause|section)\s+([0-9]+(?:\.[0-9]+)*|[ivxlc]+|premier|1er)\b\s*[:.\-–—)]*\s*(.*)$",
    re.IGNORECASE,
)
_HEAD_B = re.compile(r"^(\d{1,2}(?:\.\d{1,2})*)[.)]\s+(\S.{0,90})$")


def split_clauses(text):
    """Découpe en clauses numérotées : « Article N », « Clause N », « N. Titre ».
    À défaut de titres reconnaissables, découpe par paragraphes."""
    lines = text.split("\n")
    clauses = []
    current = None
    preamble = []
    for raw in lines:
        line = raw.strip()
        m = _HEAD_A.match(line) if line else None
        title_only = None
        if m:
            number, title = m.group(1), m.group(2).strip()
        else:
            mb = _HEAD_B.match(line) if line else None
            if mb and not mb.group(2).rstrip().endswith((",", ";")) and len(mb.group(2).split()) <= 14:
                number, title = mb.group(1), mb.group(2).strip()
            else:
                number = None
        if number is not None and len(title) <= 120:
            current = {"number": number, "title": title, "lines": []}
            clauses.append(current)
        else:
            (current["lines"] if current else preamble).append(raw)
    if len(clauses) >= 2:
        out = []
        pre = "\n".join(preamble).strip()
        if pre:
            out.append({"index": 0, "number": "—", "title": "Préambule", "text": pre})
        for c in clauses:
            body = "\n".join(c["lines"]).strip()
            out.append({"index": len(out), "number": c["number"], "title": c["title"],
                        "text": (c["title"] + "\n" + body).strip() if not body else body})
        return out
    # Repli : paragraphes (les très courts sont fusionnés avec le suivant).
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    merged, carry = [], ""
    for p in paras:
        p = (carry + "\n" + p).strip() if carry else p
        if len(p) < 80:
            carry = p
            continue
        merged.append(p)
        carry = ""
    if carry:
        merged.append(carry)
    return [
        {"index": i, "number": f"¶{i + 1}", "title": (p.split("\n", 1)[0][:70]), "text": p}
        for i, p in enumerate(merged)
    ]


# ---------------------------------------------------------------------------
# Règles de risque
# ---------------------------------------------------------------------------

LEVEL_ORDER = {"eleve": 0, "moyen": 1, "info": 2}
LEVEL_LABELS = {
    "fr": {"eleve": "Élevé", "moyen": "Moyen", "info": "À noter"},
    "en": {"eleve": "High", "moyen": "Medium", "info": "Note"},
}


def _days_at_least(minimum):
    def check(m):
        vals = [int(g) for g in m.groups() if g and g.isdigit()]
        return bool(vals) and max(vals) >= minimum
    return check


def _days_at_most(maximum):
    def check(m):
        vals = [int(g) for g in m.groups() if g and g.isdigit()]
        return bool(vals) and max(vals) <= maximum
    return check


def _pct_at_least(minimum):
    def check(m):
        try:
            return float(m.group(1).replace(",", ".")) >= minimum
        except (ValueError, IndexError):
            return False
    return check


# Motifs écrits SANS accents, appliqués au texte « fold »-é.
RULES = [
    {
        "id": "resiliation_unilaterale", "level": "eleve",
        "patterns": [
            r"resili\w+[^.;\n]{0,100}(?:sans preavis|a tout moment|unilateral\w*|sans motif|discretion)",
            r"(?:sans preavis|a tout moment|sans motif)[^.;\n]{0,80}resili",
            r"terminat\w+[^.;\n]{0,100}(?:without (?:prior )?notice|at any time|sole discretion|without cause)",
        ],
        "fr": ("Résiliation unilatérale ou sans préavis",
               "Une partie peut mettre fin au contrat sans préavis ou sans motif, ce qui fragilise l'autre partie (investissements, continuité d'activité).",
               "Exiger un préavis raisonnable, un motif sérieux ou une indemnité de sortie, et la réciprocité du droit de résiliation."),
        "en": ("Unilateral or no-notice termination",
               "One party may end the contract without notice or cause, which exposes the other party (investments, business continuity).",
               "Require reasonable notice, a serious ground or an exit payment, and make the termination right reciprocal."),
    },
    {
        "id": "modification_unilaterale", "level": "eleve",
        "patterns": [
            r"(?:modifi\w+|reviser|ajuster)[^.;\n]{0,80}(?:unilateral\w*|a tout moment|sans (?:l'accord|accord|preavis)|discretion)",
            r"se reserve le droit de (?:modifier|reviser|changer)",
            r"(?:amend|modify|change|revise)[^.;\n]{0,80}(?:unilateral\w*|at any time|sole discretion|without (?:the )?(?:consent|notice|agreement))",
            r"reserves? the right to (?:amend|modify|change|revise)",
        ],
        "fr": ("Modification unilatérale des conditions",
               "Une partie peut changer prix ou conditions sans l'accord de l'autre : l'équilibre du contrat peut être remis en cause à tout moment.",
               "Subordonner toute modification à un avenant écrit signé par les deux parties, ou prévoir un droit de résiliation sans pénalité."),
        "en": ("Unilateral change of terms",
               "One party can change price or terms without the other's agreement, so the contract's balance can shift at any time.",
               "Make any change subject to a written amendment signed by both parties, or add a penalty-free exit right."),
    },
    {
        "id": "indemnisation_illimitee", "level": "eleve",
        "patterns": [
            r"indemnis\w+[^.;\n]{0,120}(?:integral\w*|sans limit\w*|illimit\w*|tous (?:les )?(?:dommages|prejudices|couts|frais))",
            r"tenir\b[^.;\n]{0,40}quitte[^.;\n]{0,20}indemne|quitte et indemne",
            r"indemnif\w+[^.;\n]{0,140}(?:any and all|all losses|unlimited|without limit)",
            r"hold harmless",
        ],
        "fr": ("Indemnisation ou garantie sans plafond",
               "L'engagement d'indemniser couvre tous les dommages sans limite : l'exposition financière est potentiellement illimitée.",
               "Plafonner l'indemnisation (montant ou multiple du prix), exclure les dommages indirects et limiter aux préjudices prouvés et prévisibles."),
        "en": ("Uncapped indemnity or hold-harmless",
               "The duty to indemnify covers all losses without limit, so financial exposure is potentially unlimited.",
               "Cap the indemnity (amount or multiple of fees), exclude indirect losses and limit it to proven, foreseeable harm."),
    },
    {
        "id": "limitation_responsabilite", "level": "moyen",
        "patterns": [
            r"limit\w+ de (?:la |sa |leur )?responsabilit",
            r"responsabilit\w+[^.;\n]{0,90}(?:ne (?:pourra|saurait|peut)[^.;\n]{0,40}exceder|est limitee|sont limitees|plafonn\w+)",
            r"exclu\w+[^.;\n]{0,50}responsabilit",
            r"limitation of liability|shall not exceed|aggregate liability|liabilit\w+[^.;\n]{0,70}(?:limited|capped)|exclude\w* (?:all )?liabilit",
        ],
        "fr": ("Limitation ou exclusion de responsabilité",
               "La responsabilité d'une partie est plafonnée ou exclue ; selon le plafond retenu, une inexécution grave pourrait n'être que très partiellement réparée.",
               "Vérifier que le plafond est proportionné à la valeur du contrat et prévoir qu'il ne s'applique ni à la faute lourde ou dolosive ni à la confidentialité."),
        "en": ("Limitation or exclusion of liability",
               "A party's liability is capped or excluded; depending on the cap, a serious breach might be only marginally compensated.",
               "Check the cap is proportionate to the contract value and carve out gross negligence, wilful misconduct and confidentiality."),
    },
    {
        "id": "clause_penale", "level": "moyen",
        "patterns": [
            r"clause penale|penalite\w*|\bdedit\b|indemnite forfaitaire|dommages[- ]interets forfaitaires|astreinte",
            r"liquidated damages|penalt(?:y|ies)|late fees?",
        ],
        "fr": ("Clause pénale ou pénalités",
               "Des pénalités fixées à l'avance s'appliquent ; mal calibrées, elles peuvent être disproportionnées ou contestées devant le juge.",
               "Documenter le calcul, proportionner la pénalité au préjudice prévisible, la plafonner et la distinguer des clauses de résiliation."),
        "en": ("Penalty or liquidated damages",
               "Pre-agreed penalties apply; poorly calibrated, they may be disproportionate or open to challenge.",
               "Document how the amount is computed, keep it proportionate to foreseeable loss, cap it and separate it from termination clauses."),
    },
    {
        "id": "renouvellement_tacite", "level": "moyen",
        "patterns": [
            r"tacite\w*|reconduction|renouvel\w+ (?:automatiquement|par tacite)|reconduit\w*[^.;\n]{0,60}automatique",
            r"automatic(?:ally)? renew\w*|auto-?renew\w*|evergreen|renew\w* automatically",
        ],
        "fr": ("Renouvellement ou reconduction tacite",
               "Le contrat se prolonge automatiquement faute de dénonciation dans un délai souvent court : risque de rester engagé sans l'avoir voulu.",
               "Noter la date limite de dénonciation dans le suivi des échéances et envisager un préavis plus long ou un renouvellement exprès."),
        "en": ("Automatic renewal",
               "The contract extends automatically unless notice is given within a window that is often short, so you may stay bound unintentionally.",
               "Diarise the opt-out deadline and consider a longer notice window or express renewal."),
    },
    {
        "id": "non_concurrence", "level": "moyen",
        "patterns": [
            r"non[- ]concurrence|exclusivite|ne (?:pas )?(?:pourra|devra)[^.;\n]{0,40}concurrenc",
            r"non-?compete|exclusiv\w+|shall not compete",
        ],
        "fr": ("Non-concurrence ou exclusivité",
               "Une restriction d'activité ou une exclusivité limite la liberté commerciale d'une partie ; sa validité dépend de sa durée, de son étendue géographique et de sa contrepartie.",
               "Limiter la durée, le territoire et le champ d'activité au strict nécessaire, et prévoir une contrepartie."),
        "en": ("Non-compete or exclusivity",
               "A restriction or exclusivity limits a party's commercial freedom; its enforceability depends on duration, territory and consideration.",
               "Limit duration, territory and scope to what is necessary and provide consideration."),
    },
    {
        "id": "cession_pi", "level": "moyen",
        "patterns": [
            r"\bcession\b[^.;\n]{0,110}(?:propriete intellectuelle|droits d'auteur|droits patrimoniaux|ensemble des droits)",
            r"propriete intellectuelle[^.;\n]{0,110}(?:cede\w*|transfer\w*|appartien\w+ (?:exclusivement|au|a)\b)",
            r"assign\w*[^.;\n]{0,90}(?:intellectual property|all rights)|work made for hire|intellectual property[^.;\n]{0,70}(?:belong|vest)",
        ],
        "fr": ("Cession de propriété intellectuelle",
               "Les droits sur les créations sont cédés à l'autre partie, parfois dans leur totalité et sans limite de durée ou de territoire.",
               "Préciser l'étendue (droits, durée, territoire, supports), conserver les outils et connaissances préexistants, lier la cession au paiement intégral."),
        "en": ("Intellectual property assignment",
               "Rights in the work are assigned to the other party, sometimes in full and without limit of time or territory.",
               "Specify scope (rights, term, territory, media), keep pre-existing tools and know-how, and tie the assignment to full payment."),
    },
    {
        "id": "garantie_solidaire", "level": "moyen",
        "patterns": [r"solidair\w+", r"jointly and severally|joint and several"],
        "fr": ("Engagement solidaire",
               "Chaque débiteur peut être poursuivi pour la totalité de la dette : le risque ne se divise pas entre les parties.",
               "Vérifier qui est solidaire de qui, et limiter si possible l'engagement à une part ou à un plafond."),
        "en": ("Joint and several liability",
               "Each obligor can be pursued for the whole debt, so risk is not shared pro rata.",
               "Confirm who is jointly liable with whom and, where possible, limit each party to a share or cap."),
    },
    {
        "id": "juridiction_etrangere", "level": "moyen",
        "patterns": [
            r"(?:tribunaux?|juridictions?|cours?)[^.;\n]{0,70}(?:de (?:paris|new york|miami|londres|montreal|quebec|geneve|bruxelles)|etranger\w*)",
            r"arbitrage[^.;\n]{0,110}(?:cci|icc|ciadi|icsid|aaa|lcia|new york|paris|londres|geneve|miami)",
            r"(?:regi\w+|soumis\w*|gouverne\w*)[^.;\n]{0,70}(?:droit|lois?) (?:francais|americain|de l'etat|quebecois|canadien|anglais|suisse|belge|de new york)",
            r"governed by[^.;\n]{0,60}laws? of (?!(?:the republic of )?haiti)\w+|exclusive jurisdiction[^.;\n]{0,70}courts? of (?!(?:the republic of )?haiti)\w+",
        ],
        "fr": ("Droit ou juridiction étrangers",
               "Le contrat est soumis à un droit ou à une juridiction étrangers : plus coûteux à mettre en œuvre, et l'exécution d'une décision étrangère en Haïti peut exiger une procédure d'exequatur.",
               "Vérifier que ce choix est voulu, mesurer les coûts de litige à distance, et comparer avec une clause d'arbitrage ou le droit haïtien."),
        "en": ("Foreign law or forum",
               "The contract is governed by foreign law or courts: costlier to enforce, and a foreign judgment may need exequatur proceedings to be enforced in Haiti.",
               "Confirm this is intended, weigh remote-litigation costs, and compare with arbitration or Haitian law."),
    },
    {
        "id": "paiement_long", "level": "moyen", "check": _days_at_least(60),
        "patterns": [
            r"(?:paiement|payable|payer|regl\w+|factures?)[^.;\n]{0,100}?(?:\((\d{2,3})\)|\b(\d{2,3})\b)\s*jours",
            r"(?:payment|payable|invoices?)[^.;\n]{0,100}?(?:\((\d{2,3})\)|\b(\d{2,3})\b)\s*(?:calendar |business )?days",
        ],
        "fr": ("Délai de paiement long",
               "Le paiement intervient 60 jours ou plus après l'échéance de départ : tension de trésorerie pour la partie qui fournit.",
               "Raccourcir le délai, prévoir un acompte ou des paiements échelonnés et des intérêts de retard."),
        "en": ("Long payment term",
               "Payment falls due 60 days or more after the starting point, straining cash flow for the supplier.",
               "Shorten the term, add a deposit or milestone payments, and provide late-payment interest."),
    },
    {
        "id": "interets_eleves", "level": "moyen", "check": _pct_at_least(2),
        "patterns": [
            r"(\d{1,3}(?:[.,]\d+)?)\s*%[^.;\n]{0,70}(?:par mois|mensuel\w*)",
            r"(\d{1,3}(?:[.,]\d+)?)\s*%[^.;\n]{0,70}(?:per month|monthly)",
        ],
        "fr": ("Taux d'intérêt mensuel élevé",
               "Un taux d'au moins 2 % par mois est stipulé : à vérifier au regard des règles applicables sur le taux d'intérêt.",
               "Vérifier le plafond légal applicable et privilégier un taux annuel clairement exprimé."),
        "en": ("High monthly interest rate",
               "A rate of at least 2% per month is stipulated; check it against the rules that apply to interest rates.",
               "Check the applicable legal ceiling and prefer a clearly stated annual rate."),
    },
    {
        "id": "preavis_court", "level": "info", "check": _days_at_most(15),
        "patterns": [
            r"preavis[^.;\n]{0,60}?(?:\((\d{1,2})\)|\b(\d{1,2})\b)\s*jours",
            r"(?:\((\d{1,2})\)|\b(\d{1,2})\b)\s*(?:calendar |business )?days'?[^.;\n]{0,30}notice",
        ],
        "fr": ("Préavis court",
               "Le préavis est de 15 jours ou moins : peu de temps pour trouver une alternative ou organiser la sortie.",
               "Allonger le préavis (30 à 90 jours selon l'enjeu) et l'adapter à la durée de la relation."),
        "en": ("Short notice period",
               "Notice is 15 days or less, leaving little time to find an alternative or plan an exit.",
               "Lengthen notice (30 to 90 days depending on stakes) and scale it to the length of the relationship."),
    },
    {
        "id": "duree_indeterminee", "level": "info",
        "patterns": [
            r"duree indeterminee|a duree indefinie|sans limitation de duree",
            r"indefinite (?:term|period|duration)|perpetual",
        ],
        "fr": ("Durée indéterminée",
               "Sans terme, le contrat n'a pas de fin prévue : l'équilibre repose sur le droit de résilier et son préavis.",
               "Vérifier que les conditions et le préavis de résiliation sont clairs, ou fixer une durée avec révision périodique."),
        "en": ("Indefinite term",
               "With no end date, the contract's balance rests on the right to terminate and its notice period.",
               "Make sure termination conditions and notice are clear, or set a fixed term with periodic review."),
    },
]

# Clauses attendues dans la plupart des contrats : leur absence est un signal.
EXPECTED = [
    {"id": "droit_applicable", "level": "moyen", "library": "reglement_differends",
     "patterns": [r"droit applicable|loi applicable|(?:regi|gouverne|soumis)\w* (?:par|au) (?:le )?droit|droit haitien|lois? de la republique d'haiti",
                  r"governing law|governed by|laws of"],
     "fr": ("Droit applicable", "Aucune clause ne précise le droit qui régit le contrat.",
            "Ajouter une clause de droit applicable (par exemple le droit haïtien)."),
     "en": ("Governing law", "No clause states which law governs the contract.",
            "Add a governing-law clause (for example Haitian law).")},
    {"id": "reglement_differends", "level": "moyen", "library": "reglement_differends",
     "patterns": [r"juridiction|tribunal|tribunaux|arbitrage|mediation|reglement des differends|litige",
                  r"jurisdiction|arbitration|dispute|courts?\b|mediation"],
     "fr": ("Règlement des différends", "Aucune clause ne prévoit comment les litiges seront réglés (médiation, arbitrage, tribunaux compétents).",
            "Prévoir une étape de négociation ou de médiation puis la juridiction ou l'arbitrage compétent."),
     "en": ("Dispute resolution", "No clause says how disputes will be resolved (mediation, arbitration, competent courts).",
            "Provide for negotiation or mediation first, then the competent court or arbitration.")},
    {"id": "resiliation", "level": "moyen", "library": "resiliation",
     "patterns": [r"resili|terminaison|mettre fin|fin du contrat", r"terminat|terminate"],
     "fr": ("Résiliation", "Aucune clause ne prévoit les cas et conditions de fin anticipée du contrat.",
            "Ajouter les causes de résiliation, le préavis et les conséquences (restitutions, sommes dues)."),
     "en": ("Termination", "No clause covers early termination grounds and conditions.",
            "Add termination grounds, notice and consequences (returns, sums due).")},
    {"id": "duree", "level": "moyen", "library": "general",
     "patterns": [r"duree|prend effet|entre en vigueur|date d'effet|\bterme\b|jusqu'au", r"\bterm\b|term of|effective date|commenc|enters? into force|until"],
     "fr": ("Durée et entrée en vigueur", "La durée du contrat ou sa date d'entrée en vigueur n'est pas identifiée.",
            "Préciser la date d'effet, la durée et les conditions de prolongation."),
     "en": ("Term and effective date", "The contract's term or effective date is not identified.",
            "State the effective date, term and extension conditions.")},
    {"id": "prix_paiement", "level": "moyen", "library": "paiement",
     "patterns": [r"\bprix\b|remuneration|honoraires|paiement|redevance|montant|\bpayer\b", r"\bprice\b|\bfees?\b|payment|remuneration|consideration"],
     "fr": ("Prix et modalités de paiement", "Aucun prix, rémunération ou modalité de paiement n'est identifié.",
            "Fixer le prix, la devise, l'échéancier, les pénalités de retard et les taxes applicables."),
     "en": ("Price and payment terms", "No price, fee or payment term is identified.",
            "Set the price, currency, schedule, late-payment terms and applicable taxes.")},
    {"id": "responsabilite", "level": "info", "library": "penale",
     "patterns": [r"responsabilit", r"liabilit"],
     "fr": ("Responsabilité", "Le contrat ne traite pas de la responsabilité des parties.",
            "Préciser les cas de responsabilité, d'exclusion et de plafonnement."),
     "en": ("Liability", "The contract does not address the parties' liability.",
            "Specify liability, exclusions and caps.")},
    {"id": "confidentialite", "level": "info", "library": "confidentialite",
     "patterns": [r"confidentialit|informations? confidentielles?|secret des affaires", r"confidential"],
     "fr": ("Confidentialité", "Aucune obligation de confidentialité n'est prévue.",
            "Ajouter une clause de confidentialité si des informations sensibles sont échangées."),
     "en": ("Confidentiality", "No confidentiality obligation is provided.",
            "Add a confidentiality clause if sensitive information is exchanged.")},
    {"id": "force_majeure", "level": "info", "library": "force_majeure",
     "patterns": [r"force majeure|cas fortuit", r"force majeure|act of god"],
     "fr": ("Force majeure", "Aucune clause de force majeure : le sort des obligations en cas d'événement imprévisible n'est pas précisé.",
            "Définir les événements couverts, la notification et les effets (suspension, résiliation)."),
     "en": ("Force majeure", "No force majeure clause: the fate of obligations in an unforeseeable event is unaddressed.",
            "Define covered events, notice and effects (suspension, termination).")},
]

_WEIGHTS = {"eleve": 3, "moyen": 2, "info": 1}


def _compile_rules():
    for rule in RULES:
        rule["_re"] = [re.compile(p) for p in rule["patterns"]]
    for rule in EXPECTED:
        rule["_re"] = [re.compile(p) for p in rule["patterns"]]


_compile_rules()


def analyze_contract(text, lang=None):
    """Analyse un contrat. Retourne un dict sérialisable en JSON :
    {language, clause_count, word_count, clauses:[...], findings:[...],
     missing:[...], summary:{counts, score, overall}}."""
    lang = lang or detect_language(text)
    clauses = split_clauses(text)
    findings = []
    seen = set()
    for clause in clauses:
        original = clause["text"]
        folded = fold(original)
        for rule in RULES:
            for rx in rule["_re"]:
                for m in rx.finditer(folded):
                    check = rule.get("check")
                    if check and not check(m):
                        continue
                    key = (rule["id"], clause["index"])
                    if key in seen:
                        break
                    seen.add(key)
                    topic, why, fix = rule["fr" if lang == "fr" else "en"]
                    findings.append({
                        "rule": rule["id"], "level": rule["level"], "topic": topic,
                        "why": why, "fix": fix,
                        "clause_index": clause["index"], "clause_number": clause["number"],
                        "clause_title": clause["title"],
                        "excerpt": _sentence_around(original, m.start(), m.end())[:420],
                    })
                    break
                if (rule["id"], clause["index"]) in seen:
                    break
    folded_all = fold(text)
    missing = []
    for rule in EXPECTED:
        if not any(rx.search(folded_all) for rx in rule["_re"]):
            topic, why, fix = rule["fr" if lang == "fr" else "en"]
            missing.append({"rule": rule["id"], "level": rule["level"], "topic": topic,
                            "why": why, "fix": fix, "library": rule["library"]})
    findings.sort(key=lambda f: (LEVEL_ORDER[f["level"]], f["clause_index"]))
    missing.sort(key=lambda f: LEVEL_ORDER[f["level"]])
    counts = {"eleve": 0, "moyen": 0, "info": 0}
    score = 0
    for f in findings + missing:
        counts[f["level"]] += 1
        score += _WEIGHTS[f["level"]]
    if counts["eleve"] >= 2 or score >= 12:
        overall = "eleve"
    elif counts["eleve"] == 1 or score >= 5:
        overall = "moyen"
    else:
        overall = "faible"
    return {
        "language": lang,
        "clause_count": len(clauses),
        "word_count": len(text.split()),
        "clauses": [{"index": c["index"], "number": c["number"], "title": c["title"]} for c in clauses],
        "findings": findings,
        "missing": missing,
        "summary": {"counts": counts, "score": score, "overall": overall},
    }


# ---------------------------------------------------------------------------
# Échéances et obligations
# ---------------------------------------------------------------------------

_MONTHS_FR = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
              "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}
_MONTHS_EN = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
              "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}

_WORD_NUM = {
    "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9,
    "dix": 10, "douze": 12, "quinze": 15, "vingt": 20, "trente": 30, "quarante": 40, "quarante-cinq": 45,
    "cinquante": 50, "soixante": 60, "soixante-dix": 70, "quatre-vingts": 80, "quatre-vingt-dix": 90,
    "cent": 100, "cent vingt": 120, "cent quatre-vingts": 180,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty-five": 45, "forty": 40, "fifty": 50, "sixty": 60,
    "ninety": 90, "one hundred twenty": 120, "one hundred eighty": 180,
}
_UNIT_DAYS = {"jour": 1, "jours": 1, "semaine": 7, "semaines": 7, "mois": 30, "an": 365, "ans": 365,
              "annee": 365, "annees": 365, "day": 1, "days": 1, "week": 7, "weeks": 7,
              "month": 30, "months": 30, "year": 365, "years": 365}

_DATE_PATTERNS = [
    ("iso", re.compile(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b")),
    ("num", re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.]((?:19|20)\d{2})\b")),
    ("fr", re.compile(r"\b(1er|\d{1,2})\s+(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre)\s+((?:19|20)\d{2})\b")),
    ("en1", re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d{2})\b")),
    ("en2", re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(january|february|march|april|may|june|july|august|september|october|november|december),?\s+((?:19|20)\d{2})\b")),
]

_NUM_WORDS_RX = "|".join(sorted((re.escape(w) for w in _WORD_NUM), key=len, reverse=True))
_UNITS_RX = r"(jours?|semaines?|mois|ans?|annees?|days?|weeks?|months?|years?)"
_QUALIF_RX = r"(?:\s+(ouvrables|ouvres|calendaires|business|calendar|working))?"
_DURATION_DIGITS = re.compile(r"(?:\(\s*(\d{1,3})\s*\)|\b(\d{1,3})\b)\s*(?:" + _UNITS_RX + r")" + _QUALIF_RX)
_DURATION_WORDS = re.compile(r"\b(" + _NUM_WORDS_RX + r")\s+" + _UNITS_RX + _QUALIF_RX)
_DURATION_WORDS_EN = re.compile(r"(?:\(\s*(\d{1,3})\s*\)|\b(\d{1,3})\b)\s*(?:" + _UNITS_RX + r")" + _QUALIF_RX)

_TRIGGER_KINDS = [
    ("preavis", re.compile(r"preavis|notice")),
    ("renouvellement", re.compile(r"renouvel|reconduc|tacite|renew")),
    ("paiement", re.compile(r"paie|payer|payable|reglement|regler|verse|facture|rembours|pay|invoice|instal")),
]
_EVENT_DEPENDENT = re.compile(r"facture|reception|notification|mise en demeure|invoice|receipt|demand|upon notice|apres la livraison|delivery")
_EFFECT_ANCHOR = re.compile(r"signature|entree en vigueur|date d'effet|conclusion du (?:present )?contrat|effective date|execution of this|signing|entry into force")


def _to_date(kind, g, lang):
    try:
        if kind == "iso":
            return datetime.date(int(g[0]), int(g[1]), int(g[2]))
        if kind == "num":
            a, b, y = int(g[0]), int(g[1]), int(g[2])
            if a > 12:
                d, mth = a, b
            elif b > 12:
                mth, d = a, b
            elif lang == "en":
                mth, d = a, b
            else:
                d, mth = a, b
            return datetime.date(y, mth, d)
        if kind == "fr":
            d = 1 if g[0] == "1er" else int(g[0])
            return datetime.date(int(g[2]), _MONTHS_FR[g[1]], d)
        if kind == "en1":
            return datetime.date(int(g[2]), _MONTHS_EN[g[0]], int(g[1]))
        if kind == "en2":
            return datetime.date(int(g[2]), _MONTHS_EN[g[1]], int(g[0]))
    except (ValueError, KeyError):
        return None
    return None


def _add_business_days(start, n):
    d = start
    added = 0
    while added < n:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _kind_for(folded_sentence, default):
    for kind, rx in _TRIGGER_KINDS:
        if rx.search(folded_sentence):
            return kind
    return default


def extract_obligations(text, lang=None, effective_date=None):
    """Dates et délais repérés dans le contrat. Retourne une liste de dicts :
    {kind, label, due_date (ISO|None), delay_days, delay_text, estimated, excerpt}.
    Les délais relatifs ne reçoivent une date que si la phrase les rattache à
    la signature/date d'effet ET que effective_date est fournie (estimé)."""
    lang = lang or detect_language(text)
    folded = fold(text)
    results = []
    seen = set()

    def push(item):
        key = (item["kind"], item["due_date"], item["delay_days"], item["excerpt"][:80])
        if key not in seen and len(results) < 60:
            seen.add(key)
            results.append(item)

    # 1) Dates absolues
    for kind, rx in _DATE_PATTERNS:
        for m in rx.finditer(folded):
            d = _to_date(kind, m.groups(), lang)
            if not d:
                continue
            sentence = _sentence_around(text, m.start(), m.end())
            fsent = fold(sentence)
            if re.search(r"expir|prend fin|fin du contrat|terme|terminat|expire|until", fsent):
                k = "terme"
            elif re.search(r"entre en vigueur|prend effet|date d'effet|effective|signature|signed|conclu", fsent):
                k = "date_effet"
            elif re.search(r"paie|payer|verse|echeance|livr|remise|depos|avant le|au plus tard|deliver|due|pay", fsent):
                k = "echeance"
            else:
                k = "date"
            push({"kind": k, "label": sentence[:160], "due_date": d.isoformat(), "delay_days": None,
                  "delay_text": None, "estimated": False, "excerpt": sentence[:420]})

    # 2) Délais relatifs
    def handle_duration(m, number, unit, qualif):
        days_per_unit = _UNIT_DAYS.get(unit)
        if not days_per_unit or number <= 0:
            return
        sentence = _sentence_around(text, m.start(), m.end())
        fsent = fold(sentence)
        if unit in ("jour", "jours", "day", "days") and number > 400:
            return
        kind = _kind_for(fsent, "delai")
        # Une durée de contrat (« pour une durée de 2 ans ») n'est pas un délai d'action.
        if kind == "delai" and re.search(r"duree|term of|pour une periode|for a period", fsent) and unit in ("an", "ans", "annee", "annees", "year", "years", "mois", "month", "months"):
            kind = "duree"
        days = number * days_per_unit
        text_unit = unit + (f" {qualif}" if qualif else "")
        delay_text = f"{number} {text_unit}"
        due = None
        estimated = False
        business = qualif in ("ouvrables", "ouvres", "business", "working")
        if (effective_date and kind in ("delai", "paiement")
                and _EFFECT_ANCHOR.search(fsent) and not _EVENT_DEPENDENT.search(fsent)):
            due = (_add_business_days(effective_date, number) if business and unit in ("jour", "jours", "day", "days")
                   else effective_date + datetime.timedelta(days=days)).isoformat()
            estimated = True
        push({"kind": kind, "label": sentence[:160], "due_date": due, "delay_days": days,
              "delay_text": delay_text, "estimated": estimated, "excerpt": sentence[:420]})

    for m in _DURATION_DIGITS.finditer(folded):
        number = int(m.group(1) or m.group(2))
        handle_duration(m, number, m.group(3), m.group(4))
    for m in _DURATION_WORDS.finditer(folded):
        handle_duration(m, _WORD_NUM[m.group(1)], m.group(2), m.group(3))

    results.sort(key=lambda r: (r["due_date"] is None, r["due_date"] or "", r["kind"]))
    return results


# ---------------------------------------------------------------------------
# Génération de contrats à partir d'un modèle
# ---------------------------------------------------------------------------

_FR_MONTH_NAMES = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                   "septembre", "octobre", "novembre", "décembre"]
_EN_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
                   "September", "October", "November", "December"]


def format_date(value, lang):
    try:
        d = datetime.date.fromisoformat(value)
    except (ValueError, TypeError):
        return value
    if lang == "en":
        return f"{_EN_MONTH_NAMES[d.month - 1]} {d.day}, {d.year}"
    return f"{'1er' if d.day == 1 else d.day} {_FR_MONTH_NAMES[d.month - 1]} {d.year}"


_IF_RX = re.compile(r"\[\[IF (\w+)\]\](.*?)\[\[ENDIF\]\]", re.DOTALL)
_VAR_RX = re.compile(r"\{\{(\w+)\}\}")


def render_contract(body, variables, values, lang):
    """Remplace {{var}} et les blocs [[IF var]]…[[ENDIF]] (variables booléennes).
    Une variable obligatoire laissée vide devient « [à compléter : libellé] »."""
    by_key = {v["key"]: v for v in variables}

    def truthy(key):
        return str(values.get(key, "")).lower() in ("1", "on", "true", "oui", "yes")

    def if_sub(m):
        return m.group(2) if truthy(m.group(1)) else ""

    text = body
    for _ in range(3):  # blocs éventuellement imbriqués d'un niveau
        new = _IF_RX.sub(if_sub, text)
        if new == text:
            break
        text = new

    def var_sub(m):
        key = m.group(1)
        spec = by_key.get(key, {})
        raw = str(values.get(key, "")).strip()
        if not raw:
            label = spec.get("label_en" if lang == "en" else "label_fr", key)
            return f"[{'to complete' if lang == 'en' else 'à compléter'} : {label}]"
        if spec.get("type") == "date":
            return format_date(raw, lang)
        return raw

    text = _VAR_RX.sub(var_sub, text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def append_clauses(text, extra, lang):
    """Ajoute des clauses de la bibliothèque comme articles supplémentaires."""
    if not extra:
        return text
    last = 0
    for m in re.finditer(r"^(?:Article|ARTICLE)\s+(\d+)", text, re.MULTILINE):
        last = max(last, int(m.group(1)))
    head = "Clauses additionnelles" if lang == "fr" else "Additional clauses"
    parts = [text.rstrip(), "", head.upper()]
    for i, (title, body) in enumerate(extra, start=1):
        label = "Article" if lang == "fr" else "Article"
        parts += ["", f"{label} {last + i} — {title}", body.strip()]
    return "\n".join(parts)


def validate_values(variables, values):
    """Retourne la liste des clés obligatoires manquantes."""
    missing = []
    for v in variables:
        if v.get("type") == "bool":
            continue
        if v.get("required") and not str(values.get(v["key"], "")).strip():
            missing.append(v["key"])
    return missing


def build_docx(title, text, footer_note=None):
    """Retourne les octets d'un .docx, ou None si python-docx est absent."""
    try:
        import docx
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt
    except ImportError:
        return None
    document = docx.Document()
    style = document.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(11)
    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = heading.add_run(title.upper())
    run.bold = True
    run.font.size = Pt(14)
    document.add_paragraph()
    for block in text.split("\n"):
        stripped = block.strip()
        if not stripped:
            document.add_paragraph()
            continue
        p = document.add_paragraph()
        if re.match(r"^(?:ARTICLE|Article)\s+\d+\b", stripped) or stripped.isupper():
            r = p.add_run(stripped)
            r.bold = True
        else:
            p.add_run(stripped)
        p.paragraph_format.space_after = Pt(4)
    if footer_note:
        document.sections[0].footer.paragraphs[0].text = footer_note
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Comparaison de versions (redlining)
# ---------------------------------------------------------------------------

_TOKEN_RX = re.compile(r"\s+|[^\s]+")


def _words(s):
    return _TOKEN_RX.findall(s)


def _inline_diff(a, b):
    """Diff mot à mot entre deux paragraphes : liste de (kind, texte)."""
    wa, wb = _words(a), _words(b)
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    segs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            segs.append(("equal", "".join(wa[i1:i2])))
        else:
            if i2 > i1:
                segs.append(("del", "".join(wa[i1:i2])))
            if j2 > j1:
                segs.append(("ins", "".join(wb[j1:j2])))
    return segs


def compare_texts(old, new):
    """Compare deux versions d'un texte, paragraphe par paragraphe.
    Retourne {blocks:[{status, segments:[(kind,text)]}], stats:{...}}."""
    pa = [p.strip() for p in re.split(r"\n+", old) if p.strip()]
    pb = [p.strip() for p in re.split(r"\n+", new) if p.strip()]
    sm = difflib.SequenceMatcher(None, pa, pb, autojunk=False)
    blocks = []
    added_words = removed_words = 0
    changed = added = removed = unchanged = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for p in pa[i1:i2]:
                blocks.append({"status": "equal", "segments": [("equal", p)]})
                unchanged += 1
        elif tag == "delete":
            for p in pa[i1:i2]:
                blocks.append({"status": "removed", "segments": [("del", p)]})
                removed += 1
                removed_words += len(p.split())
        elif tag == "insert":
            for p in pb[j1:j2]:
                blocks.append({"status": "added", "segments": [("ins", p)]})
                added += 1
                added_words += len(p.split())
        else:  # replace : on apparie les paragraphes un à un quand c'est possible
            olds, news = pa[i1:i2], pb[j1:j2]
            n = max(len(olds), len(news))
            for k in range(n):
                if k < len(olds) and k < len(news):
                    segs = _inline_diff(olds[k], news[k])
                    blocks.append({"status": "changed", "segments": segs})
                    changed += 1
                    for kind, s in segs:
                        if kind == "ins":
                            added_words += len(s.split())
                        elif kind == "del":
                            removed_words += len(s.split())
                elif k < len(olds):
                    blocks.append({"status": "removed", "segments": [("del", olds[k])]})
                    removed += 1
                    removed_words += len(olds[k].split())
                else:
                    blocks.append({"status": "added", "segments": [("ins", news[k])]})
                    added += 1
                    added_words += len(news[k].split())
    stats = {
        "changed": changed, "added": added, "removed": removed, "unchanged": unchanged,
        "added_words": added_words, "removed_words": removed_words,
        "identical": changed == added == removed == 0,
    }
    return {"blocks": blocks, "stats": stats}
