"""Double authentification : codes TOTP (application sur téléphone) et codes de secours.

Le secret est chiffré en base avec une clé dérivée de SECRET_KEY : une copie de la base seule ne permet pas de générer
de codes. Changer SECRET_KEY rend donc les secrets et les codes de secours inutilisables (voir docs/configuration.md).
Les services ne font pas de commit : c'est la route qui valide.
"""
import base64
import hashlib
import hmac
import io
import re
import secrets
import time
from datetime import datetime

import pyotp
import qrcode
from cryptography.fernet import InvalidToken
from flask import current_app

from ..extensions import db
from ..models import RecoveryCode
from ..permissions import can
from . import branding, crypto, throttle

PERIOD = 30
RECOVERY_CODES = 10
# Peu d'essais : avec ±1 période de tolérance, trois codes sont valables à chaque instant.
failures = throttle.Limiter("totp", 5, 15 * 60)


def required(account):
    """Qui peut agir sur les comptes des autres doit protéger le sien : il ouvrira toutes les applis. De même pour qui
    lit les données personnelles des autres (annuaire, mineurs compris ; IBAN ; contrats des professeurs)."""
    from .guard import SENSITIVE_RIGHTS

    return (account.is_admin or can(account, "account.manage") or can(account, "group.manage")
            or can(account, "client.manage") or any(can(account, right) for right in SENSITIVE_RIGHTS))


def _fernet():
    return crypto.fernet(b"adherents/totp-secret")


def encrypt(secret):
    return _fernet().encrypt(secret.encode()).decode()


def secret_of(account):
    """Secret en clair, ou None s'il est absent ou illisible (SECRET_KEY changée)."""
    if not account.totp_secret:
        return None
    try:
        return _fernet().decrypt(account.totp_secret.encode()).decode()
    except InvalidToken:
        return None


def pending_secret(account):
    """Secret à enregistrer dans l'application du téléphone : recharger la page garde le même."""
    secret = secret_of(account)
    if secret is None:
        secret = pyotp.random_base32()
        account.totp_secret = encrypt(secret)
        account.totp_last_counter = None
    return secret


def uri(account, secret):
    return pyotp.TOTP(secret, interval=PERIOD).provisioning_uri(name=account.email, issuer_name=branding.name())


def qr_data_uri(text):
    """QR code en image intégrée : la CSP autorise `img-src data:`, et le secret ne passe par aucune autre requête."""
    buffer = io.BytesIO()
    qrcode.make(text, box_size=6, border=2).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def verify_code(account, code):
    """Vérifie un code à 6 chiffres. Un code accepté, ou un plus ancien, ne sera plus jamais accepté."""
    code = re.sub(r"\s", "", code or "")
    secret = secret_of(account)
    if secret is None or not re.fullmatch(r"\d{6}", code):
        return False
    generator = pyotp.TOTP(secret, interval=PERIOD)
    now = int(time.time()) // PERIOD
    for counter in (now - 1, now, now + 1):
        if account.totp_last_counter is not None and counter <= account.totp_last_counter:
            continue
        if hmac.compare_digest(generator.generate_otp(counter), code):
            account.totp_last_counter = counter
            return True
    return False


def _digest(code):
    return hmac.new(current_app.config["SECRET_KEY"].encode(), f"recovery:{code}".encode(), hashlib.sha256).hexdigest()


def new_recovery_codes(account):
    """Remplace les codes de secours. Renvoie les nouveaux en clair : ils ne seront plus jamais lisibles."""
    db.session.execute(db.delete(RecoveryCode).where(RecoveryCode.account_id == account.id))
    codes = [f"{secrets.token_hex(5)}-{secrets.token_hex(5)}" for _ in range(RECOVERY_CODES)]
    db.session.add_all(RecoveryCode(id=_digest(code), account_id=account.id) for code in codes)
    return codes


def use_recovery_code(account, code):
    normalized = (code or "").strip().lower()
    return db.session.execute(db.delete(RecoveryCode).where(
        RecoveryCode.id == _digest(normalized), RecoveryCode.account_id == account.id)).rowcount > 0


def remaining_recovery_codes(account):
    return db.session.scalar(db.select(db.func.count(RecoveryCode.id)).where(RecoveryCode.account_id == account.id))


def check(account, code):
    """À la connexion : renvoie "totp", "recovery", ou None si le code ne vaut rien."""
    if verify_code(account, code):
        return "totp"
    return "recovery" if use_recovery_code(account, code) else None


def activate(account):
    """Le code vient d'être vérifié : active la double authentification. Renvoie les codes de secours."""
    account.totp_enabled_at = datetime.now()
    return new_recovery_codes(account)


def disable(account):
    account.totp_secret = account.totp_enabled_at = account.totp_last_counter = None
    db.session.execute(db.delete(RecoveryCode).where(RecoveryCode.account_id == account.id))
