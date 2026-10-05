"""Modèles de contrats de départ (Contract Intelligence).

Ce sont des trames générales à adapter, pas des contrats prêts à signer : ils
ne remplacent pas la relecture par un avocat. Syntaxe : {{variable}} et blocs
[[IF variable]]…[[ENDIF]] (variables de type bool).
"""
import json


def V(key, fr, en, type="text", required=True, default=""):
    return {"key": key, "label_fr": fr, "label_en": en, "type": type, "required": required, "default": default}


PARTIES_FR = [
    V("party_a", "Partie A (nom complet / raison sociale)", "Party A (full name / company)"),
    V("party_a_address", "Adresse de la Partie A", "Party A address"),
    V("party_b", "Partie B (nom complet / raison sociale)", "Party B (full name / company)"),
    V("party_b_address", "Adresse de la Partie B", "Party B address"),
    V("effective_date", "Date d'effet", "Effective date", "date"),
]

SERVICES_VARS = PARTIES_FR + [
    V("services", "Description des services", "Description of services", "textarea"),
    V("fee", "Rémunération (montant et devise)", "Fee (amount and currency)"),
    V("payment_days", "Délai de paiement (jours)", "Payment term (days)", "number", True, "30"),
    V("duration_months", "Durée initiale (mois)", "Initial term (months)", "number", True, "12"),
    V("notice_days", "Préavis de résiliation (jours)", "Termination notice (days)", "number", True, "30"),
    V("auto_renew", "Renouvellement tacite", "Automatic renewal", "bool", False),
    V("confidential", "Inclure une clause de confidentialité", "Include a confidentiality clause", "bool", False, "1"),
    V("liability_cap", "Plafond de responsabilité (montant, optionnel)", "Liability cap (amount, optional)", "text", False),
]

SERVICES_FR = """Entre les soussignés :
{{party_a}}, dont l'adresse est {{party_a_address}} (ci-après « le Prestataire »),
et
{{party_b}}, dont l'adresse est {{party_b_address}} (ci-après « le Client »),
il a été convenu ce qui suit, avec effet au {{effective_date}}.

Article 1 — Objet
Le Prestataire s'engage à fournir au Client les services suivants : {{services}}.

Article 2 — Durée
Le contrat est conclu pour une durée initiale de {{duration_months}} mois à compter de la date d'effet.
[[IF auto_renew]]À son terme, il est renouvelé pour des périodes successives de même durée, sauf dénonciation par l'une des parties par écrit au moins {{notice_days}} jours avant l'échéance.[[ENDIF]]

Article 3 — Prix et paiement
Le Client verse au Prestataire la rémunération suivante : {{fee}}. Les factures sont payables dans un délai de {{payment_days}} jours à compter de leur réception. Les taxes applicables sont en sus, sauf stipulation contraire.

Article 4 — Obligations des parties
Le Prestataire exécute les services avec diligence et selon les règles de l'art. Le Client fournit en temps utile les informations et accès nécessaires à l'exécution des services.

Article 5 — Résiliation
Chaque partie peut résilier le contrat moyennant un préavis écrit de {{notice_days}} jours. En cas de manquement grave non corrigé dans les {{notice_days}} jours d'une mise en demeure écrite, l'autre partie peut résilier sans délai supplémentaire. Les sommes dues pour les services exécutés restent exigibles.

Article 6 — Responsabilité
Chaque partie répond des dommages directs causés par son inexécution fautive.[[IF liability_cap]] La responsabilité du Prestataire est limitée à {{liability_cap}}, sauf faute lourde ou dolosive.[[ENDIF]]

[[IF confidential]]Article 7 — Confidentialité
Chaque partie garde confidentielles les informations non publiques reçues de l'autre dans le cadre du contrat, et ne les utilise que pour son exécution. Cette obligation subsiste pendant trois ans après la fin du contrat.

[[ENDIF]]Article 8 — Force majeure
Aucune partie n'est responsable d'un retard ou d'une inexécution causés par un événement de force majeure. La partie touchée en informe l'autre sans délai ; si l'événement dépasse soixante jours, l'une ou l'autre peut résilier le contrat.

Article 9 — Droit applicable et règlement des différends
Le contrat est régi par le droit haïtien. Les parties tentent d'abord de résoudre tout différend à l'amiable ; à défaut, les tribunaux compétents d'Haïti sont seuls compétents.

Fait en deux exemplaires, le {{effective_date}}.

Le Prestataire : ______________________        Le Client : ______________________"""

SERVICES_EN = SERVICES_FR  # remplacé ci-dessous
SERVICES_EN = """Between:
{{party_a}}, whose address is {{party_a_address}} (the "Service Provider"),
and
{{party_b}}, whose address is {{party_b_address}} (the "Client"),
it is agreed as follows, effective {{effective_date}}.

Article 1 — Purpose
The Service Provider shall provide the Client with the following services: {{services}}.

Article 2 — Term
This agreement has an initial term of {{duration_months}} months from the effective date.
[[IF auto_renew]]At the end of the term it renews for successive periods of the same length, unless either party gives written notice at least {{notice_days}} days before expiry.[[ENDIF]]

Article 3 — Fees and payment
The Client shall pay the Service Provider the following fee: {{fee}}. Invoices are payable within {{payment_days}} days of receipt. Applicable taxes are additional unless otherwise stated.

Article 4 — Obligations
The Service Provider shall perform the services diligently and in line with professional standards. The Client shall provide in due time the information and access needed to perform the services.

Article 5 — Termination
Either party may terminate this agreement on {{notice_days}} days' written notice. In case of a material breach not remedied within {{notice_days}} days of written notice, the other party may terminate with immediate effect. Sums due for services already performed remain payable.

Article 6 — Liability
Each party is liable for direct damage caused by its faulty non-performance.[[IF liability_cap]] The Service Provider's liability is limited to {{liability_cap}}, except in case of gross negligence or wilful misconduct.[[ENDIF]]

[[IF confidential]]Article 7 — Confidentiality
Each party shall keep confidential the non-public information received from the other under this agreement and use it only to perform it. This obligation survives for three years after the agreement ends.

[[ENDIF]]Article 8 — Force majeure
Neither party is liable for delay or non-performance caused by force majeure. The affected party shall notify the other without delay; if the event lasts more than sixty days, either party may terminate.

Article 9 — Governing law and disputes
This agreement is governed by Haitian law. The parties shall first try to settle any dispute amicably; failing that, the competent courts of Haiti have exclusive jurisdiction.

Signed in two copies on {{effective_date}}.

Service Provider: ______________________        Client: ______________________"""

NDA_VARS = PARTIES_FR + [
    V("purpose", "Objet de l'échange d'informations", "Purpose of the disclosure", "textarea"),
    V("duration_years", "Durée de la confidentialité (années)", "Confidentiality period (years)", "number", True, "3"),
]

NDA_FR = """Entre les soussignés :
{{party_a}}, dont l'adresse est {{party_a_address}},
et
{{party_b}}, dont l'adresse est {{party_b_address}},
(ci-après ensemble « les Parties »), avec effet au {{effective_date}}.

Article 1 — Objet
Les Parties envisagent d'échanger des informations confidentielles dans le cadre suivant : {{purpose}}.

Article 2 — Informations confidentielles
Est confidentielle toute information non publique, quel qu'en soit le support, communiquée par une Partie à l'autre et identifiée comme confidentielle ou raisonnablement reconnaissable comme telle. Ne le sont pas les informations déjà publiques sans faute du destinataire, déjà connues de lui, reçues licitement d'un tiers ou développées indépendamment.

Article 3 — Obligations
Chaque Partie s'engage à garder les informations reçues confidentielles, à ne les utiliser que pour l'objet défini à l'article 1, et à n'en donner accès qu'aux personnes qui en ont besoin et sont tenues à une obligation équivalente.

Article 4 — Divulgation imposée
Une Partie peut divulguer des informations lorsqu'une loi ou une autorité compétente l'exige, à condition d'en informer l'autre Partie dans la mesure permise et de limiter la divulgation au strict nécessaire.

Article 5 — Restitution
À la demande d'une Partie, l'autre restitue ou détruit les informations confidentielles reçues et en atteste par écrit.

Article 6 — Durée
Les obligations de confidentialité s'appliquent pendant {{duration_years}} ans à compter de la date d'effet.

Article 7 — Droit applicable et règlement des différends
Le présent accord est régi par le droit haïtien. Les Parties tentent d'abord de résoudre tout différend à l'amiable ; à défaut, les tribunaux compétents d'Haïti sont seuls compétents.

Fait en deux exemplaires, le {{effective_date}}.

Pour {{party_a}} : ______________________        Pour {{party_b}} : ______________________"""

NDA_EN = """Between:
{{party_a}}, whose address is {{party_a_address}},
and
{{party_b}}, whose address is {{party_b_address}},
(together "the Parties"), effective {{effective_date}}.

Article 1 — Purpose
The Parties intend to exchange confidential information in the following context: {{purpose}}.

Article 2 — Confidential information
Confidential information is any non-public information, in any form, disclosed by one Party to the other and marked confidential or reasonably recognisable as such. It excludes information that is public through no fault of the recipient, already known to it, lawfully received from a third party or independently developed.

Article 3 — Obligations
Each Party shall keep the information it receives confidential, use it only for the purpose in Article 1, and give access only to people who need it and are bound by an equivalent obligation.

Article 4 — Compelled disclosure
A Party may disclose information where required by law or a competent authority, provided it notifies the other Party where permitted and limits disclosure to what is strictly necessary.

Article 5 — Return
On a Party's request, the other shall return or destroy the confidential information received and confirm this in writing.

Article 6 — Term
The confidentiality obligations apply for {{duration_years}} years from the effective date.

Article 7 — Governing law and disputes
This agreement is governed by Haitian law. The Parties shall first try to settle any dispute amicably; failing that, the competent courts of Haiti have exclusive jurisdiction.

Signed in two copies on {{effective_date}}.

For {{party_a}}: ______________________        For {{party_b}}: ______________________"""

SALE_VARS = PARTIES_FR + [
    V("goods", "Description des marchandises (nature, quantité)", "Description of goods (nature, quantity)", "textarea"),
    V("price", "Prix total (montant et devise)", "Total price (amount and currency)"),
    V("delivery_place", "Lieu de livraison", "Place of delivery"),
    V("delivery_date", "Date limite de livraison", "Delivery deadline", "date"),
    V("payment_days", "Délai de paiement après livraison (jours)", "Payment term after delivery (days)", "number", True, "30"),
    V("deposit", "Acompte à la commande (montant, optionnel)", "Deposit on order (amount, optional)", "text", False),
]

SALE_FR = """Entre les soussignés :
{{party_a}}, dont l'adresse est {{party_a_address}} (ci-après « le Vendeur »),
et
{{party_b}}, dont l'adresse est {{party_b_address}} (ci-après « l'Acheteur »),
il a été convenu ce qui suit, avec effet au {{effective_date}}.

Article 1 — Objet
Le Vendeur vend à l'Acheteur, qui l'accepte, les marchandises suivantes : {{goods}}.

Article 2 — Prix
Le prix total est de {{price}}.[[IF deposit]] Un acompte de {{deposit}} est versé à la commande et déduit du solde.[[ENDIF]] Le solde est payable dans un délai de {{payment_days}} jours à compter de la livraison. Les taxes et droits applicables sont précisés sur la facture.

Article 3 — Livraison
Les marchandises sont livrées à {{delivery_place}} au plus tard le {{delivery_date}}. Le transfert des risques a lieu à la livraison. L'Acheteur vérifie les marchandises à la réception et signale par écrit toute non-conformité apparente dans les cinq jours.

Article 4 — Conformité et garantie
Le Vendeur garantit que les marchandises sont conformes à la description convenue et exemptes de vices cachés. En cas de non-conformité établie, il les remplace ou rembourse leur prix.

Article 5 — Retard
En cas de retard de livraison non justifié, l'Acheteur peut, après mise en demeure restée sans effet pendant dix jours, résilier la vente et obtenir remboursement des sommes versées.

Article 6 — Force majeure
Aucune partie n'est responsable d'un retard ou d'une inexécution causés par un événement de force majeure ; elle en informe l'autre sans délai.

Article 7 — Droit applicable et règlement des différends
Le contrat est régi par le droit haïtien. Les parties tentent d'abord de résoudre tout différend à l'amiable ; à défaut, les tribunaux compétents d'Haïti sont seuls compétents.

Fait en deux exemplaires, le {{effective_date}}.

Le Vendeur : ______________________        L'Acheteur : ______________________"""

SALE_EN = """Between:
{{party_a}}, whose address is {{party_a_address}} (the "Seller"),
and
{{party_b}}, whose address is {{party_b_address}} (the "Buyer"),
it is agreed as follows, effective {{effective_date}}.

Article 1 — Purpose
The Seller sells to the Buyer, who accepts, the following goods: {{goods}}.

Article 2 — Price
The total price is {{price}}.[[IF deposit]] A deposit of {{deposit}} is paid on order and deducted from the balance.[[ENDIF]] The balance is payable within {{payment_days}} days of delivery. Applicable taxes and duties are shown on the invoice.

Article 3 — Delivery
The goods shall be delivered to {{delivery_place}} no later than {{delivery_date}}. Risk passes on delivery. The Buyer shall inspect the goods on receipt and notify any apparent non-conformity in writing within five days.

Article 4 — Conformity and warranty
The Seller warrants that the goods match the agreed description and are free of hidden defects. For established non-conformity, the Seller shall replace the goods or refund their price.

Article 5 — Delay
In case of unjustified late delivery, the Buyer may, after written notice remaining unanswered for ten days, terminate the sale and obtain a refund of sums paid.

Article 6 — Force majeure
Neither party is liable for delay or non-performance caused by force majeure; it shall notify the other without delay.

Article 7 — Governing law and disputes
This agreement is governed by Haitian law. The parties shall first try to settle any dispute amicably; failing that, the competent courts of Haiti have exclusive jurisdiction.

Signed in two copies on {{effective_date}}.

Seller: ______________________        Buyer: ______________________"""

TEMPLATES = [
    # (key, language, title, description, variables, body)
    ("services", "fr", "Contrat de prestation de services",
     "Prestation de services entre deux parties : durée, prix, résiliation, responsabilité, confidentialité.", SERVICES_VARS, SERVICES_FR),
    ("nda", "fr", "Accord de confidentialité bilatéral (NDA)",
     "Protection des informations échangées avant ou pendant une négociation.", NDA_VARS, NDA_FR),
    ("vente", "fr", "Contrat de vente de marchandises",
     "Vente de biens : prix, livraison, conformité, retard.", SALE_VARS, SALE_FR),
    ("services", "en", "Services agreement",
     "Services between two parties: term, fees, termination, liability, confidentiality.", SERVICES_VARS, SERVICES_EN),
    ("nda", "en", "Mutual non-disclosure agreement (NDA)",
     "Protection of information exchanged before or during a negotiation.", NDA_VARS, NDA_EN),
    ("vente", "en", "Sale of goods agreement",
     "Sale of goods: price, delivery, conformity, delay.", SALE_VARS, SALE_EN),
]


def seed_templates(conn, now):
    for key, lang, title, desc, variables, body in TEMPLATES:
        row = conn.execute("SELECT id FROM contract_templates WHERE key=? AND language=?", (key, lang)).fetchone()
        if row:
            continue
        conn.execute(
            "INSERT INTO contract_templates (key, language, title, description, variables_json, body_text, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (key, lang, title, desc, json.dumps(variables, ensure_ascii=False), body, now),
        )
    conn.commit()
