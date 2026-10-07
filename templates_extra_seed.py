"""Modèles supplémentaires (trames générales à faire valider par un avocat)."""
from contract_templates_seed import V, PARTIES_FR

COMMON_END_FR = """
Article {n} — Droit applicable et litiges
Le présent contrat est régi par le droit haïtien. Les parties recherchent d'abord une solution amiable ; à défaut, les tribunaux de {{jurisdiction}} sont seuls compétents.

Article {m} — Dispositions finales
Le contrat exprime l'intégralité de l'accord des parties. Toute modification se fait par avenant écrit signé des deux parties. Si une stipulation est nulle, les autres demeurent applicables. Les notifications sont faites par écrit aux adresses indiquées en tête du contrat.

Fait à ________, le {{effective_date}}, en deux exemplaires.

Pour {{party_a}} : ____________________        Pour {{party_b}} : ____________________
"""
COMMON_END_EN = """
Article {n} — Governing law and disputes
This agreement is governed by Haitian law. The parties first seek an amicable solution; failing that, the courts of {{jurisdiction}} have exclusive jurisdiction.

Article {m} — Final provisions
This agreement is the entire agreement of the parties. Any amendment must be in a written document signed by both parties. If any provision is void, the others remain in force. Notices are given in writing to the addresses stated above.

Signed at ________, on {{effective_date}}, in two copies.

For {{party_a}}: ____________________        For {{party_b}}: ____________________
"""
JUR = V("jurisdiction", "Tribunal compétent (ville)", "Competent court (city)", "text", True, "Port-au-Prince")

def fin(txt, n, m, en=False):
    return txt + (COMMON_END_EN if en else COMMON_END_FR).replace("{n}", str(n)).replace("{m}", str(m))

HEAD_FR = """Entre les soussignés :
{{party_a}}, dont l'adresse est {{party_a_address}} (ci-après « {a} »),
et
{{party_b}}, dont l'adresse est {{party_b_address}} (ci-après « {b} »),
il a été convenu ce qui suit, avec effet au {{effective_date}}.
"""
HEAD_EN = """Between:
{{party_a}}, of {{party_a_address}} (hereinafter "{a}"),
and
{{party_b}}, of {{party_b_address}} (hereinafter "{b}"),
it is agreed as follows, effective {{effective_date}}.
"""
def head(a, b, en=False):
    return (HEAD_EN if en else HEAD_FR).replace("{a}", a).replace("{b}", b)

# ---------------- Distribution
DIST_VARS = PARTIES_FR + [
    V("products", "Produits distribués", "Products distributed", "textarea"),
    V("territory", "Territoire", "Territory"),
    V("exclusive", "Exclusivité territoriale", "Territorial exclusivity", "bool", False),
    V("min_volume", "Objectif minimal annuel (quantité ou montant)", "Annual minimum (quantity or amount)", "text", False),
    V("payment_days", "Délai de paiement (jours)", "Payment term (days)", "number", True, "30"),
    V("duration_months", "Durée (mois)", "Term (months)", "number", True, "24"),
    V("notice_days", "Préavis de résiliation (jours)", "Termination notice (days)", "number", True, "90"),
    JUR,
]
DIST_FR = fin(head("le Fournisseur", "le Distributeur") + """
Article 1 — Objet
Le Fournisseur confie au Distributeur la distribution des produits suivants : {{products}}, sur le territoire de {{territory}}.

Article 2 — Exclusivité
[[IF exclusive]]Le Fournisseur concède au Distributeur l'exclusivité de la distribution sur le territoire. Le Distributeur s'interdit de distribuer des produits concurrents sur ce territoire.[[ENDIF]]Le Distributeur agit en son nom et pour son compte ; il n'est ni agent ni mandataire du Fournisseur.

Article 3 — Objectifs
[[IF min_volume]]Le Distributeur s'engage à acheter au moins {{min_volume}} par an. À défaut, après mise en demeure restée sans effet pendant 30 jours, le Fournisseur peut mettre fin à l'exclusivité ou résilier le contrat.[[ENDIF]]

Article 4 — Prix et paiement
Les produits sont vendus aux prix du tarif en vigueur communiqué par écrit. Les factures sont payables sous {{payment_days}} jours. Tout retard porte intérêt de plein droit au taux convenu par écrit ou, à défaut, au taux légal.

Article 5 — Livraison et risques
Les risques sont transférés à la livraison au lieu convenu. Le Distributeur signale toute non-conformité apparente par écrit dans les 3 jours de la réception.

Article 6 — Propriété intellectuelle
Le Distributeur peut utiliser les marques du Fournisseur pour la seule distribution des produits, selon sa charte. Il n'acquiert aucun droit sur elles.

Article 7 — Durée et résiliation
Le contrat est conclu pour {{duration_months}} mois. Chaque partie peut le résilier moyennant un préavis écrit de {{notice_days}} jours. En cas de manquement grave non corrigé dans les 30 jours d'une mise en demeure, l'autre partie peut résilier de plein droit.
""", 8, 9)
DIST_EN = fin(head("the Supplier", "the Distributor", True) + """
Article 1 — Purpose
The Supplier appoints the Distributor to distribute the following products: {{products}}, in the territory of {{territory}}.

Article 2 — Exclusivity
[[IF exclusive]]The Supplier grants the Distributor exclusive distribution rights in the territory. The Distributor shall not distribute competing products there.[[ENDIF]]The Distributor acts in its own name and for its own account; it is neither agent nor representative of the Supplier.

Article 3 — Targets
[[IF min_volume]]The Distributor shall purchase at least {{min_volume}} per year. Failing that, after a formal notice remaining unheeded for 30 days, the Supplier may end exclusivity or terminate the agreement.[[ENDIF]]

Article 4 — Price and payment
Products are sold at the current price list communicated in writing. Invoices are payable within {{payment_days}} days. Late payments bear interest at the rate agreed in writing or, failing that, the legal rate.

Article 5 — Delivery and risk
Risk passes on delivery at the agreed place. The Distributor reports any apparent non-conformity in writing within 3 days of receipt.

Article 6 — Intellectual property
The Distributor may use the Supplier's trademarks solely to distribute the products, in line with its guidelines. It acquires no rights in them.

Article 7 — Term and termination
The agreement runs for {{duration_months}} months. Either party may terminate it on {{notice_days}} days' written notice. For a serious breach not remedied within 30 days of a formal notice, the other party may terminate immediately.
""", 8, 9, True)

# ---------------- Agence commerciale / commission
AGENT_VARS = PARTIES_FR + [
    V("mission", "Mission de l'agent (produits / services)", "Agent's mission (products / services)", "textarea"),
    V("territory", "Territoire", "Territory"),
    V("commission", "Commission (taux ou montant)", "Commission (rate or amount)"),
    V("duration_months", "Durée (mois)", "Term (months)", "number", True, "12"),
    V("notice_days", "Préavis (jours)", "Notice (days)", "number", True, "60"),
    JUR,
]
AGENT_FR = fin(head("le Mandant", "l'Agent") + """
Article 1 — Mission
Le Mandant confie à l'Agent, de façon non exclusive sauf stipulation écrite contraire, la mission de prospecter et de négocier au nom et pour le compte du Mandant : {{mission}}, sur le territoire de {{territory}}. L'Agent est indépendant ; il organise librement son activité et supporte ses frais.

Article 2 — Pouvoirs
L'Agent ne peut conclure de contrat au nom du Mandant que s'il y est autorisé par écrit. Il transmet sans délai toute commande ou information utile.

Article 3 — Commission
L'Agent perçoit une commission de {{commission}} sur les opérations conclues grâce à son intervention et payées par le client. Elle est due dans les 30 jours de l'encaissement. Il remet un état de ses opérations chaque mois.

Article 4 — Obligations
L'Agent exécute sa mission loyalement, respecte les instructions raisonnables du Mandant et garde confidentielles les informations reçues.

Article 5 — Durée et fin du contrat
Le contrat dure {{duration_months}} mois. Chaque partie peut y mettre fin avec un préavis écrit de {{notice_days}} jours. [À valider : indemnité de fin de contrat éventuelle selon le droit applicable.]
""", 6, 7)
AGENT_EN = fin(head("the Principal", "the Agent", True) + """
Article 1 — Mission
The Principal appoints the Agent, on a non-exclusive basis unless otherwise agreed in writing, to prospect and negotiate on the Principal's behalf: {{mission}}, in the territory of {{territory}}. The Agent is independent, organises its own activity and bears its own costs.

Article 2 — Powers
The Agent may bind the Principal only if authorised in writing. It promptly passes on any order or useful information.

Article 3 — Commission
The Agent earns a commission of {{commission}} on transactions concluded through its efforts and paid by the customer. It is due within 30 days of collection. The Agent provides a monthly statement of its transactions.

Article 4 — Obligations
The Agent performs its mission loyally, follows the Principal's reasonable instructions and keeps received information confidential.

Article 5 — Term and ending
The agreement lasts {{duration_months}} months. Either party may end it on {{notice_days}} days' written notice. [To be checked: possible end-of-contract indemnity under applicable law.]
""", 6, 7, True)

# ---------------- Licence
LIC_VARS = PARTIES_FR + [
    V("licensed_object", "Objet licencié (œuvre, logiciel, marque…)", "Licensed subject matter (work, software, mark…)", "textarea"),
    V("territory", "Territoire", "Territory"),
    V("scope", "Usages autorisés", "Permitted uses", "textarea"),
    V("royalty", "Redevance (montant et périodicité)", "Royalty (amount and frequency)"),
    V("exclusive", "Licence exclusive", "Exclusive licence", "bool", False),
    V("duration_months", "Durée (mois)", "Term (months)", "number", True, "36"),
    JUR,
]
LIC_FR = fin(head("le Concédant", "le Licencié") + """
Article 1 — Objet
Le Concédant accorde au Licencié une licence [[IF exclusive]]exclusive[[ENDIF]] d'utilisation sur : {{licensed_object}}, pour le territoire de {{territory}}, non transférable et non sous-licenciable sans accord écrit.

Article 2 — Usages autorisés
La licence est limitée aux usages suivants : {{scope}}. Tout autre usage est interdit.

Article 3 — Redevance
Le Licencié verse au Concédant : {{royalty}}. Il tient une comptabilité des ventes ou usages concernés et la met à disposition sur demande.

Article 4 — Propriété et garanties
Le Concédant reste titulaire de tous droits. Il garantit être titulaire des droits concédés et ne pas connaître de contrefaçon. Le Licencié informe le Concédant de toute atteinte constatée.

Article 5 — Durée et fin
La licence est consentie pour {{duration_months}} mois. Elle prend fin en cas de manquement grave non corrigé dans les 30 jours d'une mise en demeure. À la fin, le Licencié cesse tout usage.
""", 6, 7)
LIC_EN = fin(head("the Licensor", "the Licensee", True) + """
Article 1 — Subject matter
The Licensor grants the Licensee a[[IF exclusive]]n exclusive[[ENDIF]] licence to use: {{licensed_object}}, for the territory of {{territory}}, non-transferable and non-sublicensable without written consent.

Article 2 — Permitted uses
The licence is limited to the following uses: {{scope}}. Any other use is prohibited.

Article 3 — Royalty
The Licensee pays the Licensor: {{royalty}}. It keeps records of relevant sales or uses and makes them available on request.

Article 4 — Ownership and warranties
The Licensor remains owner of all rights. It warrants that it holds the licensed rights and knows of no infringement. The Licensee informs the Licensor of any infringement noticed.

Article 5 — Term and end
The licence runs for {{duration_months}} months. It ends on serious breach not remedied within 30 days of a formal notice. On ending, the Licensee ceases all use.
""", 6, 7, True)

# ---------------- Prêt / reconnaissance de dette
LOAN_VARS = PARTIES_FR + [
    V("amount", "Montant prêté (chiffres et lettres, devise)", "Amount lent (figures and words, currency)"),
    V("interest", "Taux d'intérêt (laisser vide si sans intérêt)", "Interest rate (leave blank if interest-free)", "text", False),
    V("repayment", "Modalités de remboursement (échéances, dates)", "Repayment schedule (instalments, dates)", "textarea"),
    V("guarantee", "Garantie ou caution (optionnel)", "Security or guarantee (optional)", "text", False),
    JUR,
]
LOAN_FR = fin(head("le Prêteur", "l'Emprunteur") + """
Article 1 — Prêt
Le Prêteur remet à l'Emprunteur, qui le reconnaît, la somme de {{amount}}. L'Emprunteur s'oblige à la rembourser.

Article 2 — Intérêts
[[IF interest]]La somme porte intérêt au taux de {{interest}}, calculé sur le capital restant dû.[[ENDIF]]Le prêt est consenti sans intérêt sauf stipulation ci-dessus.

Article 3 — Remboursement
L'Emprunteur rembourse selon le calendrier suivant : {{repayment}}. Il peut rembourser par anticipation sans pénalité.

Article 4 — Défaut
En cas de défaut de paiement d'une échéance resté sans effet 8 jours après mise en demeure, le solde devient immédiatement exigible, avec intérêts de retard au taux légal ou conventionnel.

Article 5 — Garantie
[[IF guarantee]]Garantie consentie : {{guarantee}}.[[ENDIF]]

Article 6 — Preuve
Le présent écrit fait preuve de la remise des fonds et de l'engagement de remboursement. [À valider : mention manuscrite « bon pour » et formalités d'enregistrement si exigées.]
""", 7, 8)
LOAN_EN = fin(head("the Lender", "the Borrower", True) + """
Article 1 — Loan
The Lender hands the Borrower, who acknowledges receipt, the sum of {{amount}}. The Borrower undertakes to repay it.

Article 2 — Interest
[[IF interest]]The sum bears interest at {{interest}}, calculated on the outstanding principal.[[ENDIF]]The loan is interest-free unless stated above.

Article 3 — Repayment
The Borrower repays according to the following schedule: {{repayment}}. Early repayment is allowed without penalty.

Article 4 — Default
If an instalment is unpaid 8 days after formal notice, the balance becomes immediately due, with default interest at the legal or agreed rate.

Article 5 — Security
[[IF guarantee]]Security granted: {{guarantee}}.[[ENDIF]]

Article 6 — Evidence
This document is evidence of the transfer of funds and the undertaking to repay. [To be checked: handwritten acknowledgement and registration formalities, if required.]
""", 7, 8, True)

# ---------------- Bail commercial
LEASE_VARS = PARTIES_FR + [
    V("premises", "Local loué (adresse et description)", "Premises (address and description)", "textarea"),
    V("use", "Destination des lieux", "Permitted use"),
    V("rent", "Loyer (montant, devise, périodicité)", "Rent (amount, currency, frequency)"),
    V("deposit", "Dépôt de garantie", "Security deposit", "text", False),
    V("duration_months", "Durée (mois)", "Term (months)", "number", True, "36"),
    V("notice_days", "Préavis (jours)", "Notice (days)", "number", True, "90"),
    JUR,
]
LEASE_FR = fin(head("le Bailleur", "le Preneur") + """
Article 1 — Objet
Le Bailleur donne à bail au Preneur les locaux suivants : {{premises}}. Ils sont destinés exclusivement à : {{use}}.

Article 2 — Durée
Le bail est conclu pour {{duration_months}} mois à compter de la date d'effet. Chaque partie peut y mettre fin à l'échéance avec un préavis écrit de {{notice_days}} jours.

Article 3 — Loyer et charges
Le loyer est de {{rent}}, payable d'avance. Les charges et taxes sont réparties selon l'annexe ou, à défaut, supportées par le Preneur pour celles liées à son exploitation. Un état des lieux d'entrée est établi contradictoirement.

Article 4 — Dépôt de garantie
[[IF deposit]]Le Preneur verse un dépôt de garantie de {{deposit}}, restitué à la sortie après déduction des sommes dues et des dégradations constatées.[[ENDIF]]

Article 5 — Entretien et travaux
Le Preneur entretient les lieux et ne fait aucune transformation sans l'accord écrit du Bailleur. Le Bailleur assure le clos et le couvert.

Article 6 — Sous-location et cession
Toute sous-location ou cession du bail est interdite sans accord écrit du Bailleur.

Article 7 — Assurance
Le Preneur assure les risques locatifs et son activité, et justifie de l'assurance sur demande. [À valider : règles d'ordre public applicables aux baux commerciaux en Haïti et formalités d'enregistrement.]
""", 8, 9)
LEASE_EN = fin(head("the Landlord", "the Tenant", True) + """
Article 1 — Purpose
The Landlord leases to the Tenant the following premises: {{premises}}. They are to be used exclusively for: {{use}}.

Article 2 — Term
The lease runs for {{duration_months}} months from the effective date. Either party may end it at expiry on {{notice_days}} days' written notice.

Article 3 — Rent and charges
The rent is {{rent}}, payable in advance. Charges and taxes are allocated as in the annex or, failing that, borne by the Tenant for those linked to its business. A joint move-in inspection report is drawn up.

Article 4 — Security deposit
[[IF deposit]]The Tenant pays a security deposit of {{deposit}}, returned at exit after deducting sums due and damage found.[[ENDIF]]

Article 5 — Upkeep and works
The Tenant maintains the premises and makes no alteration without the Landlord's written consent. The Landlord keeps the premises wind- and watertight.

Article 6 — Subletting and assignment
Subletting or assignment of the lease is prohibited without the Landlord's written consent.

Article 7 — Insurance
The Tenant insures rental risks and its business, and proves cover on request. [To be checked: mandatory rules for commercial leases in Haiti and registration formalities.]
""", 8, 9, True)

# ---------------- Sous-traitance
SUB_VARS = PARTIES_FR + [
    V("work", "Travaux ou prestations sous-traités", "Subcontracted work or services", "textarea"),
    V("price", "Prix (montant et devise)", "Price (amount and currency)"),
    V("deadline", "Délai d'exécution", "Completion deadline"),
    V("payment_days", "Délai de paiement (jours)", "Payment term (days)", "number", True, "30"),
    V("penalty", "Pénalité de retard par jour (optionnel)", "Daily delay penalty (optional)", "text", False),
    JUR,
]
SUB_FR = fin(head("l'Entrepreneur principal", "le Sous-traitant") + """
Article 1 — Objet
L'Entrepreneur principal confie au Sous-traitant, qui l'accepte, l'exécution de : {{work}}.

Article 2 — Exécution
Le Sous-traitant exécute les travaux selon les règles de l'art et les instructions techniques de l'Entrepreneur principal, avec son propre personnel et matériel. Il ne sous-traite pas à son tour sans accord écrit.

Article 3 — Prix et paiement
Le prix est de {{price}}. Chaque facture est payable sous {{payment_days}} jours après réception des travaux correspondants.

Article 4 — Délai
Les travaux sont achevés au plus tard le {{deadline}}. [[IF penalty]]En cas de retard imputable au Sous-traitant, une pénalité de {{penalty}} par jour est due, sans préjudice du droit de résilier.[[ENDIF]]

Article 5 — Réception et garantie
Une réception contradictoire a lieu à l'achèvement. Le Sous-traitant corrige à ses frais les défauts signalés dans un délai raisonnable.

Article 6 — Assurance et sécurité
Le Sous-traitant est assuré pour sa responsabilité et respecte la réglementation applicable en matière de sécurité et de droit du travail.

Article 7 — Confidentialité
Le Sous-traitant garde confidentielles les informations reçues et ne les utilise que pour l'exécution du contrat.
""", 8, 9)
SUB_EN = fin(head("the Main Contractor", "the Subcontractor", True) + """
Article 1 — Purpose
The Main Contractor entrusts the Subcontractor, who accepts, with: {{work}}.

Article 2 — Performance
The Subcontractor performs the work to professional standards and the Main Contractor's technical instructions, with its own staff and equipment. It does not subcontract further without written consent.

Article 3 — Price and payment
The price is {{price}}. Each invoice is payable within {{payment_days}} days of receipt of the corresponding work.

Article 4 — Deadline
The work is completed no later than {{deadline}}. [[IF penalty]]For delay attributable to the Subcontractor, a penalty of {{penalty}} per day is due, without prejudice to the right to terminate.[[ENDIF]]

Article 5 — Acceptance and warranty
A joint acceptance takes place at completion. The Subcontractor corrects reported defects at its own cost within a reasonable time.

Article 6 — Insurance and safety
The Subcontractor is insured for its liability and complies with applicable safety and labour regulations.

Article 7 — Confidentiality
The Subcontractor keeps received information confidential and uses it only to perform the agreement.
""", 8, 9, True)

# ---------------- Partenariat / co-entreprise
JV_VARS = PARTIES_FR + [
    V("project", "Projet commun", "Joint project", "textarea"),
    V("contrib_a", "Apport de la Partie A", "Party A's contribution", "textarea"),
    V("contrib_b", "Apport de la Partie B", "Party B's contribution", "textarea"),
    V("split", "Répartition des résultats (ex. 60/40)", "Profit split (e.g. 60/40)"),
    V("duration_months", "Durée (mois)", "Term (months)", "number", True, "24"),
    V("exclusivity", "Exclusivité réciproque pour le projet", "Mutual exclusivity for the project", "bool", False),
    JUR,
]
JV_FR = fin(head("la Partie A", "la Partie B") + """
Article 1 — Objet
Les parties s'associent pour réaliser le projet suivant : {{project}}. Le présent contrat ne crée pas de société, sauf constitution ultérieure par acte séparé.

Article 2 — Apports
La Partie A apporte : {{contrib_a}}. La Partie B apporte : {{contrib_b}}. Chaque apport reste la propriété de son apporteur sauf stipulation contraire.

Article 3 — Gouvernance
Un comité composé d'un représentant de chaque partie se réunit au moins chaque trimestre. Les décisions importantes (budget, engagement financier, entrée d'un tiers) requièrent l'accord écrit des deux parties.

Article 4 — Résultats et charges
Les résultats et charges du projet sont répartis selon : {{split}}. Un compte du projet est tenu et communiqué à chaque partie.

Article 5 — Exclusivité et confidentialité
[[IF exclusivity]]Pendant la durée du contrat, aucune partie ne mène de projet concurrent avec un tiers.[[ENDIF]]Les informations échangées sont confidentielles.

Article 6 — Durée et sortie
Le contrat dure {{duration_months}} mois. Chaque partie peut se retirer en cas de manquement grave de l'autre non corrigé dans les 30 jours d'une mise en demeure. Les modalités de liquidation et de partage des actifs sont fixées par accord ou, à défaut, proportionnellement aux apports.
""", 7, 8)
JV_EN = fin(head("Party A", "Party B", True) + """
Article 1 — Purpose
The parties join to carry out the following project: {{project}}. This agreement does not create a company, unless one is later formed by a separate deed.

Article 2 — Contributions
Party A contributes: {{contrib_a}}. Party B contributes: {{contrib_b}}. Each contribution remains the property of its contributor unless otherwise stated.

Article 3 — Governance
A committee with one representative from each party meets at least quarterly. Major decisions (budget, financial commitment, admission of a third party) require both parties' written consent.

Article 4 — Results and costs
Results and costs of the project are shared as follows: {{split}}. A project account is kept and shared with each party.

Article 5 — Exclusivity and confidentiality
[[IF exclusivity]]During the term, neither party runs a competing project with a third party.[[ENDIF]]Information exchanged is confidential.

Article 6 — Term and exit
The agreement lasts {{duration_months}} months. Either party may withdraw on the other's serious breach not remedied within 30 days of a formal notice. Winding-up and asset sharing are agreed or, failing that, proportional to contributions.
""", 7, 8, True)

EXTRA_TEMPLATES = [
    ("distribution", "Contrat de distribution", "Distribution agreement",
     "Distribution de produits : territoire, exclusivité, objectifs, prix, livraison, résiliation.",
     "Distribution of products: territory, exclusivity, targets, price, delivery, termination.", DIST_VARS, DIST_FR, DIST_EN),
    ("agence", "Contrat d'agence commerciale", "Commercial agency agreement",
     "Agent indépendant : mission, pouvoirs, commission, durée et fin.",
     "Independent agent: mission, powers, commission, term and end.", AGENT_VARS, AGENT_FR, AGENT_EN),
    ("licence", "Contrat de licence", "Licence agreement",
     "Licence d'une œuvre, d'un logiciel ou d'une marque : usages, redevance, garanties.",
     "Licence of a work, software or mark: uses, royalty, warranties.", LIC_VARS, LIC_FR, LIC_EN),
    ("pret", "Contrat de prêt / reconnaissance de dette", "Loan agreement / acknowledgement of debt",
     "Prêt d'argent : intérêts, remboursement, défaut, garantie, preuve.",
     "Money loan: interest, repayment, default, security, evidence.", LOAN_VARS, LOAN_FR, LOAN_EN),
    ("bail_commercial", "Bail commercial", "Commercial lease",
     "Location de local à usage professionnel : durée, loyer, dépôt, travaux, assurance.",
     "Lease of business premises: term, rent, deposit, works, insurance.", LEASE_VARS, LEASE_FR, LEASE_EN),
    ("sous_traitance", "Contrat de sous-traitance", "Subcontracting agreement",
     "Travaux ou prestations sous-traités : prix, délai, réception, assurance.",
     "Subcontracted work or services: price, deadline, acceptance, insurance.", SUB_VARS, SUB_FR, SUB_EN),
    ("partenariat", "Contrat de partenariat / co-entreprise", "Partnership / joint venture agreement",
     "Projet commun : apports, gouvernance, répartition, exclusivité, sortie.",
     "Joint project: contributions, governance, split, exclusivity, exit.", JV_VARS, JV_FR, JV_EN),
]
