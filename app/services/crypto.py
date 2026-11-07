"""Chiffrement des secrets gardés en base, avec une clé dérivée de SECRET_KEY : une copie de la base seule ne suffit pas.

Changer SECRET_KEY les rend donc illisibles (voir docs/configuration.md).
"""
import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import current_app


def fernet(purpose):
    """Une clé distincte par usage (`purpose`, en octets)."""
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose).derive(
        current_app.config["SECRET_KEY"].encode())
    return Fernet(base64.urlsafe_b64encode(key))
