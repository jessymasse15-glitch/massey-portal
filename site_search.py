"""Recherche sur tout le site (pages publiques, outils, articles de la Massey Law Review).

Indexe un petit catalogue de pages + les articles publiés ; si l'utilisateur est connecté, propose aussi
la recherche dans ses propres contrats. Aucun texte privé n'est indexé ici."""
import re
import unicodedata

from flask import render_template, request, url_for

m = None

# (endpoint FR, endpoint EN ou None, titre FR, titre EN, description FR, description EN, mots-clés)
PAGES = [
    ("services", "services_en", "Services", "Services", "Confiez vos opérations et vos dossiers à des avocats d'affaires.", "Hand your operations and files to business lawyers.", "dossier avocat confier juridique fiscal accompagnement"),
    ("produits", "produits_en", "Produits Massey AI", "Massey AI products", "Les outils en libre-service : contrats, transactions, fiscalité, conformité.", "Self-service tools: contracts, transactions, tax, compliance.", "outils libre-service intelligence"),
    ("espace_client_marketing", None, "Espace client", "Client area", "Suivre les dossiers confiés à notre équipe : documents, messagerie, paiement, signature.", "Follow the cases you entrusted to our team.", "portail suivi dossier documents messagerie"),
    ("pillar_contract_intelligence", "pillar_contract_intelligence_en", "Contract Intelligence", "Contract Intelligence", "Analyser, rédiger, comparer, négocier, faire signer et suivre vos contrats.", "Analyse, draft, compare, negotiate, sign and track your contracts.", "contrat analyse rédaction clause risque signature échéance registre playbook"),
    ("pillar_legal_intelligence", "pillar_legal_intelligence_en", "Legal Intelligence", "Legal Intelligence", "Veille et recherche juridique.", "Legal watch and research.", "droit loi jurisprudence recherche veille"),
    ("pillar_transaction_intelligence", "pillar_transaction_intelligence_en", "Transaction Intelligence", "Transaction Intelligence", "Opérations, due diligence et transactions.", "Deals, due diligence and transactions.", "transaction due diligence acquisition"),
    ("pillar_tax_intelligence", "pillar_tax_intelligence_en", "Tax Intelligence", "Tax Intelligence", "Obligations et échéances fiscales.", "Tax obligations and deadlines.", "fiscal impôt taxe dgi échéance"),
    ("pillar_regulatory_compliance", "pillar_regulatory_compliance_en", "Regulatory & Compliance", "Regulatory & Compliance", "Conformité réglementaire.", "Regulatory compliance.", "conformité réglementation kyc"),
    ("membership", "membership_en", "Abonnement Premium", "Premium membership", "Quotas élargis, IA approfondie, équipes.", "Higher limits, in-depth AI, teams.", "premium prix abonnement paiement équipe"),
    ("faq", "faq_en", "Questions fréquentes", "FAQ", "Réponses aux questions courantes.", "Answers to common questions.", "aide question faq"),
    ("about", "about_en", "À propos", "About", "Qui nous sommes.", "Who we are.", "équipe histoire mission"),
    ("contact", "contact_en", "Contact", "Contact", "Nous écrire.", "Write to us.", "contact message courriel"),
    ("securite_conformite", "securite_conformite_en", "Sécurité et conformité", "Security & compliance", "Comment nous protégeons vos données.", "How we protect your data.", "sécurité chiffrement confidentialité mfa"),
    ("ci_verify", "ci_verify_en", "Vérifier un document signé", "Verify a signed document", "Contrôler l'authenticité d'un PDF signé.", "Check the authenticity of a signed PDF.", "vérification signature empreinte sha"),
    ("ci_legal", "ci_legal_en", "Valeur juridique de la signature électronique", "Legal value of e-signatures", "Ce que vaut une signature électronique en Haïti.", "What an e-signature is worth in Haiti.", "signature électronique valeur juridique"),
    ("ci_method", "ci_method_en", "Comment fonctionne l'analyse", "How the analysis works", "Méthode, limites et sources.", "Method, limits and sources.", "méthode limites sources analyse"),
    ("revue_home", None, "Massey Law Review", "Massey Law Review", "Revue juridique.", "Law review.", "revue articles doctrine publication"),
    ("revue_articles", None, "Articles de la Massey Law Review", "Massey Law Review articles", "Les articles publiés.", "Published articles.", "articles doctrine"),
]
TOOLS = [  # nécessitent un compte : (nom ci_url, titre FR, titre EN, mots-clés)
    ("home", "Mon espace", "My workspace", "tableau de bord outils contrats"),
    ("analyze", "Analyser un contrat", "Analyse a contract", "analyse risque clause"),
    ("generate", "Rédiger un contrat", "Draft a contract", "rédaction modèle brouillon"),
    ("compare", "Comparer deux versions", "Compare two versions", "comparaison différences"),
    ("registry", "Registre des contrats", "Contract registry", "archive fichier"),
    ("deadlines", "Échéances", "Deadlines", "rappel calendrier délai"),
    ("signatures", "Signatures", "Signatures", "signer signature électronique"),
    ("teams", "Équipes", "Teams", "équipe rôle partage"),
    ("trust", "Vos données", "Your data", "export suppression confidentialité"),
]


def fold(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _score(q_tokens, title, desc, kw):
    t, d, k = fold(title), fold(desc), fold(kw)
    sc = 0
    for tok in q_tokens:
        if tok in t:
            sc += 5 if t.startswith(tok) or (" " + tok) in t else 3
        if tok in k:
            sc += 3
        if tok in d:
            sc += 1
    return sc


def search(q, lang, logged):
    toks = [t for t in re.split(r"\W+", fold(q)) if len(t) >= 2]
    out = []
    if not toks:
        return out
    en = lang == "en"
    for fr_ep, en_ep, tf, te, df, de, kw in PAGES:
        ep = (en_ep if en and en_ep else fr_ep)
        try:
            url = url_for(ep)
        except Exception:  # noqa: BLE001
            continue
        sc = _score(toks, te if en else tf, de if en else df, kw + " " + tf + " " + te)
        if sc:
            out.append({"score": sc, "title": te if en else tf, "desc": de if en else df, "url": url, "kind": "Page"})
    for name, tf, te, kw in TOOLS:
        sc = _score(toks, te if en else tf, "", kw + " " + tf + " " + te)
        if sc:
            out.append({"score": sc + (0 if logged else -1), "title": te if en else tf, "desc": ("Tool — sign in required" if en else "Outil — connexion requise") if not logged else ("Tool" if en else "Outil"),
                        "url": m.ci_url(name), "kind": "Outil" if not en else "Tool"})
    conn = m.dbm.get_db()
    try:
        rows = conn.execute("SELECT slug, title, author_name, abstract FROM review_articles WHERE published=1").fetchall()
    finally:
        conn.close()
    for r in rows:
        sc = _score(toks, r["title"], (r["abstract"] or "") + " " + (r["author_name"] or ""), "")
        if sc:
            out.append({"score": sc, "title": r["title"], "desc": ((r["abstract"] or "")[:200] + "…") if len(r["abstract"] or "") > 200 else (r["abstract"] or ""),
                        "url": url_for("revue_article_detail", slug=r["slug"]), "kind": "Article"})
    out.sort(key=lambda x: -x["score"])
    return out[:25]


def init(app_module):
    global m
    m = app_module
    app = m.app

    def view():
        q = (request.args.get("q") or "").strip()[:100]
        lang = "en" if request.path.startswith("/en/") else "fr"
        results = search(q, lang, bool(m.current_user())) if q else []
        return render_template("site_search.html", q=q, results=results, logged=bool(m.current_user()))

    app.add_url_rule("/recherche-site", "site_search", view)
    app.add_url_rule("/en/site-search", "site_search_en", view)
    m.LANG_COUNTERPART["site_search"] = "site_search_en"
    m.LANG_COUNTERPART["site_search_en"] = "site_search"
