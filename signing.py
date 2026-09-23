"""
Signature électronique — mécanisme d'attestation interne avec piste d'audit.

IMPORTANT — à lire avant de t'en servir commercialement :
Ceci n'est PAS un service de signature électronique certifié comme DocuSign
ou Dropbox Sign / HelloSign. C'est un mécanisme d'attestation : le signataire
tape son nom légal complet, coche deux cases de consentement explicite, et le
serveur enregistre un horodatage, l'empreinte SHA-256 du document au moment
de la signature, l'adresse IP et le user-agent. Cela produit une piste
d'audit vérifiable, comparable à ce que beaucoup de petites plateformes
utilisent, mais sa valeur probante n'est pas garantie par un tiers de
confiance externe. Pour une signature électronique avec pleine valeur
probante et conformité réglementaire (eIDAS, UETA/ESIGN, etc.), il faut
intégrer un fournisseur certifié — voir la section "Passer à un vrai
fournisseur" du README.
"""
import hashlib


def compute_file_hash(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


CONSENT_TEXT = (
    "En tapant mon nom légal complet ci-dessous et en cochant les cases de "
    "consentement, je reconnais avoir lu et compris le document, et j'accepte "
    "que cette action constitue ma signature de ce document, avec la même "
    "valeur qu'une signature manuscrite entre les parties, dans la mesure "
    "permise par la loi applicable."
)
