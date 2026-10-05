"""Niveau d'enjeu d'un contrat et recommandation de méthode de signature.

Heuristique volontairement prudente : elle propose, l'utilisateur décide, et un
contrat à forme imposée ne part jamais sans reconnaissance explicite du risque."""
import re

from contract_engine import fold

# Actes pour lesquels la loi exige en principe une forme particulière (acte authentique / notarié).
FORM_KEYWORDS = [
    "hypotheque", "vente d'immeuble", "vente immobiliere", "acte de vente", "titre de propriete", "terrain", "immeuble",
    "bail emphyteotique", "contrat de mariage", "testament", "donation", "succession",
    "statuts de la societe", "constitution de societe", "acte constitutif", "real estate", "mortgage", "deed of sale",
    "marriage contract", "last will", "articles of incorporation",
]
# Contrats qui engagent lourdement : garanties, financement, cessions, engagements longs.
IMPORTANT_KEYWORDS = [
    "caution", "garantie bancaire", "pret", "emprunt", "financement", "nantissement", "gage", "cession de parts", "cession d'actions",
    "propriete intellectuelle", "licence exclusive", "exclusivite", "non-concurrence", "clause penale", "responsabilite illimitee",
    "guarantee", "loan", "pledge", "assignment of shares", "exclusive licen", "non-compete", "penalty", "unlimited liability",
]
AMOUNT_RX = re.compile(r"(\d[\d\u00a0 .,]*\d)")
AMOUNT_THRESHOLD = 10000


def _hits(folded, keywords):
    out = []
    for k in keywords:
        if k not in out and re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])" if len(k) <= 6 else re.escape(k), folded):
            out.append(k)
    return out


def _to_int(token):
    t = token.strip().replace(" ", "").replace("\u00a0", "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t):
        t = t.replace(",", "").split(".")[0]
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", t):
        t = t.replace(".", "").split(",")[0]
    else:
        t = re.split(r"[.,]", t)[0]
    return int(t[:12]) if t.isdigit() else 0


def _max_amount(value_text):
    return max([_to_int(m) for m in AMOUNT_RX.findall(value_text or "")] or [0])


def suggest_stakes(text, value_text="", overall=None, lang="fr"):
    """-> (niveau 'courant'|'important'|'forme', [raisons])"""
    f = fold(text or "")
    en = lang == "en"
    form = _hits(f, FORM_KEYWORDS)
    if form:
        return "forme", [("Mots-clés d'un acte à forme imposée : " if not en else "Keywords of a formal act: ") + ", ".join(form[:4])]
    reasons = []
    imp = _hits(f, IMPORTANT_KEYWORDS)
    if imp:
        reasons.append(("Engagement lourd repéré : " if not en else "Heavy commitment spotted: ") + ", ".join(imp[:4]))
    if _max_amount(value_text) >= AMOUNT_THRESHOLD:
        reasons.append(("Montant indiqué élevé : " if not en else "High amount entered: ") + value_text.strip()[:40])
    if overall == "eleve":
        reasons.append("Analyse à risque élevé." if not en else "Analysis rated high risk.")
    if reasons:
        return "important", reasons
    return "courant", []


def advice(stakes, certified_available, lang="fr"):
    """Texte de recommandation affiché avant l'envoi."""
    en = lang == "en"
    if stakes == "forme":
        return ("This type of act normally requires a notarial (authentic) deed: an electronic signature does not replace it. See a notary before signing anything."
                if en else "Ce type d'acte exige en principe un acte notarié (authentique) : une signature électronique ne le remplace pas. Rapprochez-vous d'un notaire avant de signer quoi que ce soit.")
    if stakes == "important":
        if certified_available:
            return ("High stakes: use the certified signature (identity checked by the provider, sealed document, certificate of completion)."
                    if en else "Enjeu important : utilisez la signature certifiée (document scellé et certificat de réalisation par le fournisseur).")
        return ("High stakes: the certified signature is not enabled on this site. The simple signature leaves a weaker proof. Prefer a handwritten signature or ask your administrator to enable the certified provider."
                if en else "Enjeu important : la signature certifiée n'est pas activée sur ce site. La signature simple laisse une preuve plus faible. Préférez la signature manuscrite ou demandez à votre administrateur d'activer le fournisseur certifié.")
    return ("Everyday contract: the simple signature is usually enough; the certified one remains available."
            if en else "Contrat courant : la signature simple suffit en général ; la signature certifiée reste disponible.")


STAKES_LABELS = {
    "fr": {"courant": "Courant", "important": "Important", "forme": "Acte à forme imposée (notaire)"},
    "en": {"courant": "Everyday", "important": "Important", "forme": "Formal act (notary)"},
}
