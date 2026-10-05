"""Chiffrement au repos des fichiers archivés par Contract Intelligence (registre, PDF signés, certificats).

Algorithme : Fernet (AES-128-CBC + HMAC-SHA256, bibliothèque `cryptography`). Un fichier chiffré
commence par l'en-tête MCE1 ; un fichier sans cet en-tête (archivé avant cette fonction) reste lisible
et est chiffré à sa première lecture.

Clé : variable d'environnement CI_FILE_KEY (recommandé : elle reste hors du disque de données) ;
à défaut, une clé est générée une fois et conservée dans le dossier de données (ci_file.key)."""
import base64
import hashlib
import io
import os

from cryptography.fernet import Fernet

MAGIC = b"MCE1"
_fernet = None


def _key_path():
    import db
    base = db.DATA_DIR
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "ci_file.key")


def _get():
    global _fernet
    if _fernet:
        return _fernet
    raw = os.environ.get("CI_FILE_KEY", "").strip()
    if raw:
        try:
            Fernet(raw.encode())
            key = raw.encode()
        except Exception:  # noqa: BLE001 — n'importe quelle phrase secrète est acceptée, dérivée en clé
            key = base64.urlsafe_b64encode(hashlib.sha256(("mce1:" + raw).encode()).digest())
    else:
        path = _key_path()
        if os.path.exists(path):
            key = open(path, "rb").read().strip()
        else:
            key = Fernet.generate_key()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
    _fernet = Fernet(key)
    return _fernet


def key_source():
    return "env" if os.environ.get("CI_FILE_KEY", "").strip() else "file"


def is_encrypted(blob):
    return blob[:4] == MAGIC


def encrypt(data):
    return MAGIC + _get().encrypt(data)


def decrypt(blob):
    return _get().decrypt(blob[4:]) if is_encrypted(blob) else blob


def write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(encrypt(data))
    os.replace(tmp, path)


def read(path):
    with open(path, "rb") as fh:
        blob = fh.read()
    if is_encrypted(blob):
        return decrypt(blob)
    try:                      # fichier ancien, en clair : on le chiffre au passage
        write(path, blob)
    except OSError:
        pass
    return blob


def send(path, download_name, mimetype=None):
    from flask import send_file
    return send_file(io.BytesIO(read(path)), mimetype=mimetype or "application/octet-stream", as_attachment=True, download_name=download_name)


def erase(path):
    """Suppression définitive : écrase le contenu puis supprime le fichier."""
    try:
        size = os.path.getsize(path)
        with open(path, "r+b") as fh:
            fh.write(b"\0" * size)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        pass
    try:
        os.remove(path)
    except OSError:
        pass
