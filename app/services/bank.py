"""Coordonnées bancaires : IBAN chiffrés avec une clé dédiée, BANK_DATA_KEYS, distincte de SECRET_KEY.

- Chiffrement AES-GCM, avec l'identifiant du foyer en données associées : un chiffré recopié sur un autre foyer ne
  se déchiffre plus.
- BANK_DATA_KEYS est une liste de clés numérotées (« 2:…, 1:… ») : la plus haute chiffre, les autres ne servent qu'à
  relire. `flask bank-rotate-key` rechiffre tout avec la plus haute, puis on retire les anciennes.
- L'empreinte (HMAC) repère un même IBAN sur deux foyers sans rien déchiffrer. Elle dépend de la clé, donc elle est
  recalculée à la rotation.

Rien ici ne fait de commit, et un IBAN en clair ne doit jamais sortir de ce module ailleurs qu'à l'écran de lecture.
"""
import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
import string
from datetime import date

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import current_app
from sqlalchemy import inspect

from ..extensions import db
from ..models import BankAccount, SepaMandate

NONCE_BYTES = 12
# Mandat supprimé 13 mois après son dernier prélèvement (durée de contestation d'un prélèvement non autorisé).
MANDATE_RETENTION_MONTHS = 13
# Longueur de l'IBAN par pays, pour les pays de la zone SEPA les plus probables ; les autres : 15 à 34.
IBAN_LENGTHS = {"FR": 27, "MC": 27, "BE": 16, "LU": 20, "DE": 22, "CH": 21, "ES": 24, "IT": 27, "PT": 25, "NL": 18,
                "GB": 22, "IE": 22, "AT": 20}


class BankKeyError(RuntimeError):
    """Clé absente ou invalide."""


class InvalidIban(ValueError):
    pass


# ---------------------------------------------------------------------------
# Clés
# ---------------------------------------------------------------------------

def parse_keys(text):
    """« 2:clé, 1:clé » → {2: octets, 1: octets}. Chaque clé fait 32 octets, en base64."""
    keys = {}
    for item in re.split(r"[\s,;]+", (text or "").strip()):
        if not item:
            continue
        version, sep, encoded = item.partition(":")
        if not sep or not version.isdigit() or int(version) < 1:
            raise BankKeyError("BANK_DATA_KEYS : chaque clé s'écrit « numéro:clé », par exemple « 1:… ».")
        try:
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except (binascii.Error, ValueError):
            raise BankKeyError(f"BANK_DATA_KEYS : la clé n° {version} n'est pas en base64.") from None
        if len(raw) != 32:
            raise BankKeyError(f"BANK_DATA_KEYS : la clé n° {version} doit faire 32 octets (elle en fait {len(raw)}).")
        if int(version) in keys:
            raise BankKeyError(f"BANK_DATA_KEYS : le numéro {version} est utilisé deux fois.")
        keys[int(version)] = raw
    return keys


def new_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def keys():
    return parse_keys(current_app.config.get("BANK_DATA_KEYS"))


def current_version():
    available = keys()
    if not available:
        raise BankKeyError("BANK_DATA_KEYS n'est pas définie : impossible d'enregistrer un IBAN.")
    return max(available)


def enabled():
    try:
        return bool(keys())
    except BankKeyError:
        return False


def check_startup(app):
    """Au démarrage : la clé doit être lisible, et chaque IBAN en base doit avoir la sienne."""
    available = parse_keys(app.config.get("BANK_DATA_KEYS"))
    with app.app_context():
        try:
            if not inspect(db.engine).has_table("bank_account"):
                return  # base pas encore migrée
            used = set(db.session.scalars(db.select(BankAccount.key_version).distinct()))
        finally:
            db.session.remove()
    missing = sorted(used - set(available))
    if missing:
        raise BankKeyError(
            f"Des IBAN ont été chiffrés avec la clé n° {', '.join(map(str, missing))}, absente de BANK_DATA_KEYS : "
            "l'appli refuse de démarrer plutôt que de les rendre illisibles. Remettez cette clé dans le fichier .env.")


def _derive(key, purpose):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose).derive(key)


# ---------------------------------------------------------------------------
# IBAN
# ---------------------------------------------------------------------------

def normalize_iban(text):
    """Sans espaces, en majuscules, contrôlé (clé modulo 97). Lève InvalidIban sinon."""
    iban = re.sub(r"[\s.-]", "", text or "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        raise InvalidIban("IBAN invalide : il commence par le code du pays (FR76…) et compte 15 à 34 caractères.")
    expected = IBAN_LENGTHS.get(iban[:2])
    if expected and len(iban) != expected:
        raise InvalidIban(f"IBAN invalide : un IBAN {iban[:2]} compte {expected} caractères (celui-ci {len(iban)}).")
    digits = "".join(str(int(c, 36)) for c in iban[4:] + iban[:4])
    if int(digits) % 97 != 1:
        raise InvalidIban("IBAN invalide : la clé de contrôle ne correspond pas. Vérifiez chaque caractère.")
    return iban


def normalize_bic(text):
    bic = re.sub(r"\s", "", text or "").upper()
    if not bic:
        return None
    if not re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?", bic):
        raise InvalidIban("BIC invalide : 8 ou 11 caractères, par exemple AGRIFRPP882.")
    return bic


def group(iban):
    """« FR7630006000011234567890189 » → « FR76 3000 6000 0112 3456 7890 189 »."""
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def _aad(household_id):
    return f"adherents/iban/household:{household_id}".encode()


def fingerprint(iban, version=None):
    version = version or current_version()
    key = _derive(keys()[version], b"adherents/iban-fingerprint")
    return hmac.new(key, normalize_iban(iban).encode(), hashlib.sha256).hexdigest()


def fingerprints(iban):
    """Empreintes de cet IBAN avec chaque clé disponible : avant une rotation, les lignes n'ont pas toutes la même."""
    return {fingerprint(iban, version) for version in keys()}


def set_iban(account, iban):
    """Chiffre `iban` sur `account`, dont le foyer doit déjà avoir un identifiant."""
    if account.household_id is None:
        raise ValueError("Le compte bancaire doit être rattaché à un foyer enregistré.")
    iban = normalize_iban(iban)
    version = current_version()
    key = keys()[version]
    nonce = os.urandom(NONCE_BYTES)
    account.iban_ciphertext = nonce + AESGCM(_derive(key, b"adherents/iban")).encrypt(
        nonce, iban.encode(), _aad(account.household_id))
    account.iban_last4 = iban[-4:]
    account.iban_fingerprint = fingerprint(iban, version)
    account.key_version = version


def reveal(account):
    """IBAN en clair. Réservé à l'écran de lecture (droit bank.view, TOTP, journal) et aux rechiffrements."""
    key = keys().get(account.key_version)
    if key is None:
        raise BankKeyError(f"La clé n° {account.key_version} n'est plus dans BANK_DATA_KEYS.")
    blob = account.iban_ciphertext
    try:
        return AESGCM(_derive(key, b"adherents/iban")).decrypt(
            blob[:NONCE_BYTES], blob[NONCE_BYTES:], _aad(account.household_id)).decode()
    except InvalidTag:
        raise BankKeyError("IBAN indéchiffrable : il a été modifié ou recopié d'un autre foyer.") from None


def move(account, household_id, iban=None):
    """Change le foyer d'un compte bancaire (fusion) : son chiffré, lié au foyer, est refait."""
    iban = iban or reveal(account)
    account.household_id = household_id
    set_iban(account, iban)


def same_iban(iban, exclude=None):
    """Comptes bancaires portant déjà cet IBAN, retrouvés par leur empreinte."""
    query = db.select(BankAccount).where(BankAccount.iban_fingerprint.in_(fingerprints(iban)))
    if exclude is not None:
        query = query.where(BankAccount.id != exclude.id)
    return db.session.scalars(query).all()


def rotate():
    """Rechiffre tous les IBAN avec la clé la plus haute. Renvoie le nombre de lignes rechiffrées."""
    version = current_version()
    count = 0
    for account in db.session.scalars(db.select(BankAccount)):
        if account.key_version != version or account.iban_fingerprint != fingerprint(reveal(account), version):
            set_iban(account, reveal(account))
            count += 1
    return count


# ---------------------------------------------------------------------------
# Mandats
# ---------------------------------------------------------------------------

RUM_ALPHABET = string.ascii_uppercase + string.digits


def new_rum():
    """Référence unique de mandat (35 caractères au plus, lettres et chiffres)."""
    while True:
        rum = f"ADH-{date.today():%Y}-" + "".join(secrets.choice(RUM_ALPHABET) for _ in range(10))
        if db.session.scalar(db.select(SepaMandate.id).filter_by(rum=rum)) is None:
            return rum


def _months_ago(today, months):
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    month += 1
    day = min(today.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30, 31,
                          31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def purge_expired_mandates(today=None):
    """Supprime les mandats dont le dernier prélèvement date de plus de 13 mois, puis le compte bancaire qui
    n'avait plus que celui-là. Un compte sans aucun mandat (pas encore signé) n'est pas touché."""
    limit = _months_ago(today or date.today(), MANDATE_RETENTION_MONTHS)
    expired = db.session.scalars(db.select(SepaMandate).where(SepaMandate.last_debit_on < limit)).all()
    accounts = {mandate.bank_account for mandate in expired}
    for mandate in expired:
        db.session.delete(mandate)
    db.session.flush()
    removed = 0
    for account in accounts:
        db.session.refresh(account)
        if not account.mandates:
            db.session.delete(account)
            removed += 1
    return len(expired), removed
