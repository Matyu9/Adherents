"""Coordonnées bancaires : chiffrement des IBAN, clés, lecture en clair, mandats."""
import base64
import re
from datetime import date

import pytest

from app import create_app
from app.extensions import db
from app.models import Activity, BankAccount, Household, Role, SepaMandate
from app.services import bank, tasks

from .conftest import config_for, enroll_totp, login, make_account, totp_code

IBAN = "FR7630006000011234567890189"
OTHER_IBAN = "FR1420041010050500013M02606"
KEY_1 = "1:" + base64.urlsafe_b64encode(b"1" * 32).decode()
KEY_2 = "2:" + base64.urlsafe_b64encode(b"2" * 32).decode()


def household(name="Famille Martin"):
    record = Household(name=name)
    db.session.add(record)
    db.session.flush()
    return record


def bank_account(home, iban=IBAN, holder="Anne Martin"):
    account = BankAccount(household_id=home.id, holder_name=holder)
    bank.set_iban(account, iban)
    db.session.add(account)
    db.session.commit()
    return account


# ---------------------------------------------------------------------------
# IBAN, BIC et clés
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [IBAN, "fr76 3000 6000 0112 3456 7890 189", " FR76-3000-6000-0112-3456-7890-189 "])
def test_iban_is_normalized(text):
    assert bank.normalize_iban(text) == IBAN


@pytest.mark.parametrize("text", ["", "FR7630006000011234567890188", "FR763000600001123456789018", "1234",
                                  "FR76300060000112345678901899", "XX00"])
def test_invalid_iban_is_refused(text):
    with pytest.raises(bank.InvalidIban):
        bank.normalize_iban(text)


def test_foreign_iban_of_unknown_length_is_checked_by_its_key():
    assert bank.normalize_iban("NO9386011117947") == "NO9386011117947"
    with pytest.raises(bank.InvalidIban):
        bank.normalize_iban("NO9386011117948")


def test_bic():
    assert bank.normalize_bic(" agrifrpp882 ") == "AGRIFRPP882"
    assert bank.normalize_bic("") is None
    with pytest.raises(bank.InvalidIban):
        bank.normalize_bic("AGRI")


def test_iban_is_grouped_by_four():
    assert bank.group(IBAN) == "FR76 3000 6000 0112 3456 7890 189"


@pytest.mark.parametrize("text", ["clé", "1-abc", "0:" + "A" * 43, "1:" + base64.urlsafe_b64encode(b"x" * 16).decode(),
                                  KEY_1 + "," + KEY_1, "1:!!!"])
def test_malformed_keys_are_refused(text):
    with pytest.raises(bank.BankKeyError):
        bank.parse_keys(text)


def test_several_keys_and_the_highest_encrypts():
    assert set(bank.parse_keys(f"{KEY_1}, {KEY_2}")) == {1, 2}
    assert bank.parse_keys("") == {}
    assert len(base64.urlsafe_b64decode(bank.new_key() + "=")) == 32


# ---------------------------------------------------------------------------
# Chiffrement
# ---------------------------------------------------------------------------

def test_round_trip_shows_only_the_last_four(app):
    account = bank_account(household())
    assert account.iban_last4 == "0189" and account.masked_iban == "•••• •••• 0189"
    assert IBAN.encode() not in account.iban_ciphertext and account.key_version == 1
    assert bank.reveal(account) == IBAN
    assert len(account.iban_fingerprint) == 64 and IBAN not in account.iban_fingerprint


def test_same_iban_encrypts_differently_but_has_the_same_fingerprint(app):
    first, second = bank_account(household()), bank_account(household("Famille Durand"))
    assert first.iban_ciphertext != second.iban_ciphertext
    assert first.iban_fingerprint == second.iban_fingerprint
    assert bank.same_iban(IBAN, exclude=first) == [second]
    assert bank.same_iban(OTHER_IBAN) == []


def test_ciphertext_copied_to_another_household_cannot_be_read(app):
    first, second = bank_account(household()), bank_account(household("Famille Durand"), OTHER_IBAN)
    second.iban_ciphertext = first.iban_ciphertext
    with pytest.raises(bank.BankKeyError, match="recopié"):
        bank.reveal(second)


def test_tampered_ciphertext_cannot_be_read(app):
    account = bank_account(household())
    blob = bytearray(account.iban_ciphertext)
    blob[-1] ^= 1
    account.iban_ciphertext = bytes(blob)
    with pytest.raises(bank.BankKeyError):
        bank.reveal(account)


def test_saving_without_key_is_refused(app):
    app.config["BANK_DATA_KEYS"] = ""
    with pytest.raises(bank.BankKeyError):
        bank.set_iban(BankAccount(household_id=household().id), IBAN)
    assert not bank.enabled()


def test_iban_never_appears_in_clear_in_the_database_file(tmp_path):
    path = tmp_path / "base.db"
    app = create_app(config_for(tmp_path, SQLALCHEMY_DATABASE_URI=f"sqlite:///{path}"))
    with app.app_context():
        db.create_all()
        bank_account(household())
        db.session.execute(db.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        db.session.remove()
        db.engine.dispose()
    content = b"".join(p.read_bytes() for p in tmp_path.glob("base.db*"))
    for form in (IBAN, bank.group(IBAN), IBAN[4:], "30006000011234567890"):
        assert form.encode() not in content and form.encode("utf-16-le") not in content


def test_rotation_reencrypts_everything_and_the_old_key_can_go(app):
    app.config["BANK_DATA_KEYS"] = KEY_1
    account = bank_account(household())
    old_fingerprint = account.iban_fingerprint
    app.config["BANK_DATA_KEYS"] = f"{KEY_2},{KEY_1}"
    assert bank.same_iban(IBAN) == [account]  # avant la rotation, les deux empreintes sont essayées
    new = bank_account(household("Famille Durand"))
    assert new.key_version == 2 and bank.reveal(account) == IBAN
    assert bank.rotate() == 1
    db.session.commit()
    assert account.key_version == 2 and account.iban_fingerprint != old_fingerprint
    assert bank.rotate() == 0
    app.config["BANK_DATA_KEYS"] = KEY_2
    assert bank.reveal(account) == IBAN and len(bank.same_iban(IBAN)) == 2


def test_reading_with_a_removed_key_fails_clearly(app):
    app.config["BANK_DATA_KEYS"] = KEY_1
    account = bank_account(household())
    app.config["BANK_DATA_KEYS"] = KEY_2
    with pytest.raises(bank.BankKeyError, match="n° 1"):
        bank.reveal(account)


def _file_app(tmp_path, keys):
    return config_for(tmp_path, SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'base.db'}", BANK_DATA_KEYS=keys)


def test_app_refuses_to_start_without_the_key_of_stored_ibans(tmp_path):
    app = create_app(_file_app(tmp_path, KEY_1))
    with app.app_context():
        db.create_all()
        bank_account(household())
        db.session.remove()
    with pytest.raises(bank.BankKeyError, match="refuse de démarrer"):
        create_app(_file_app(tmp_path, ""))
    with pytest.raises(bank.BankKeyError, match="n° 1"):
        create_app(_file_app(tmp_path, KEY_2))
    create_app(_file_app(tmp_path, f"{KEY_2},{KEY_1}"))  # la clé est là, même si ce n'est plus la plus haute


def test_app_starts_without_key_when_no_iban_is_stored(tmp_path):
    app = create_app(_file_app(tmp_path, ""))
    with app.app_context():
        db.create_all()
    create_app(_file_app(tmp_path, ""))


def test_app_refuses_a_malformed_key(tmp_path):
    with pytest.raises(bank.BankKeyError):
        create_app(_file_app(tmp_path, "1:trop-courte"))


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------

@pytest.fixture
def home(app):
    record = household()
    db.session.commit()
    return record


def test_adding_an_iban_keeps_it_out_of_the_journal(client, admin, home):
    response = client.post(f"/annuaire/foyers/{home.id}/banque/nouveau",
                           data={"holder_person_id": 0, "holder_name": "Anne Martin", "iban": bank.group(IBAN),
                                 "bic": "agrifrpp882"})
    assert response.status_code == 302
    account = db.session.scalar(db.select(BankAccount))
    assert bank.reveal(account) == IBAN and account.bic == "AGRIFRPP882"
    page = client.get(f"/annuaire/foyers/{home.id}").get_data(as_text=True)
    assert "•••• •••• 0189" in page and IBAN not in page and bank.group(IBAN) not in page
    journal = " ".join(f"{a.message} {a.changes}" for a in db.session.scalars(db.select(Activity)))
    assert "0189" in journal and IBAN not in journal and "30006000" not in journal
    assert IBAN not in client.get("/journal").get_data(as_text=True)


def test_invalid_iban_is_explained(client, admin, home):
    response = client.post(f"/annuaire/foyers/{home.id}/banque/nouveau",
                           data={"holder_person_id": 0, "holder_name": "A", "iban": "FR7630006000011234567890188"})
    assert response.status_code == 200 and "clé de contrôle" in response.get_data(as_text=True)
    assert db.session.scalar(db.select(db.func.count(BankAccount.id))) == 0


def test_same_iban_on_two_households_is_flagged(client, admin, home):
    bank_account(household("Famille Durand"))
    response = client.post(f"/annuaire/foyers/{home.id}/banque/nouveau",
                           data={"holder_person_id": 0, "holder_name": "A", "iban": IBAN}, follow_redirects=True)
    assert "déjà enregistré pour le foyer Famille Durand" in response.get_data(as_text=True)


def test_without_key_the_form_is_not_offered(client, admin, home, app):
    app.config["BANK_DATA_KEYS"] = ""
    response = client.get(f"/annuaire/foyers/{home.id}/banque/nouveau", follow_redirects=True)
    assert "BANK_DATA_KEYS" in response.get_data(as_text=True)


def test_reading_an_iban_needs_a_fresh_code_and_is_logged(client, admin, home):
    account = bank_account(home)
    url = f"/annuaire/banque/{account.id}/afficher"
    page = client.get(url)
    assert page.headers["Cache-Control"] == "no-store" and IBAN not in page.get_data(as_text=True)
    wrong = client.post(url, data={"code": "000000"}).get_data(as_text=True)
    assert "Code incorrect" in wrong and bank.group(IBAN) not in wrong
    admin.totp_last_counter = None
    db.session.commit()
    shown = client.post(url, data={"code": totp_code()})
    assert bank.group(IBAN) in shown.get_data(as_text=True) and shown.headers["Cache-Control"] == "no-store"
    entry = db.session.scalar(db.select(Activity).filter_by(action="bank.view"))
    assert entry.account_id == admin.id and entry.household_id == home.id and IBAN not in entry.message
    replay = client.post(url, data={"code": totp_code()}).get_data(as_text=True)  # un code ne sert qu'une fois
    assert bank.group(IBAN) not in replay


def test_reading_is_throttled(client, admin, home):
    account = bank_account(home)
    for _ in range(5):
        client.post(f"/annuaire/banque/{account.id}/afficher", data={"code": "000000"})
    admin.totp_last_counter = None
    db.session.commit()
    response = client.post(f"/annuaire/banque/{account.id}/afficher", data={"code": totp_code()})
    assert "Trop de tentatives" in response.get_data(as_text=True) and IBAN not in response.get_data(as_text=True)


def test_reading_without_two_factor_is_refused(client, app, home):
    db.session.add(Role(key="tresorier", name="Trésorier", permissions=["bank.view", "directory.view"]))
    db.session.commit()
    treasurer = make_account("tresorier", email="t@asso.test")
    login(client, treasurer)  # bank.view exige la double authentification : il est renvoyé la configurer
    account = bank_account(home)
    response = client.get(f"/annuaire/banque/{account.id}/afficher")
    assert response.status_code == 302 and IBAN not in response.get_data(as_text=True)
    enroll_totp(treasurer)
    login(client, treasurer)
    assert client.get(f"/annuaire/banque/{account.id}/afficher").status_code == 200


def test_manager_without_bank_right_sees_no_bank_section(client, accounts, home):
    login(client, accounts[Role.GESTIONNAIRE])
    account = bank_account(home)
    assert "Coordonnées bancaires" not in client.get(f"/annuaire/foyers/{home.id}").get_data(as_text=True)
    assert client.get(f"/annuaire/banque/{account.id}/afficher").status_code == 403
    assert client.post(f"/annuaire/banque/{account.id}/supprimer").status_code == 403
    assert db.session.get(BankAccount, account.id) is not None


def test_mandate_and_deletion(client, admin, home):
    account = bank_account(home)
    form = client.get(f"/annuaire/banque/{account.id}/mandats/nouveau").get_data(as_text=True)
    rum = re.search(r'name="rum"[^>]*value="([^"]+)"', form).group(1)
    assert rum.startswith("ADH-")
    client.post(f"/annuaire/banque/{account.id}/mandats/nouveau", data={"rum": rum, "signed_on": "2026-09-01"})
    mandate = db.session.scalar(db.select(SepaMandate))
    assert mandate.rum == rum and mandate.signed_on == date(2026, 9, 1)
    duplicate = client.post(f"/annuaire/banque/{account.id}/mandats/nouveau", data={"rum": rum, "signed_on": "2026-09-01"})
    assert "Cette référence est déjà" in duplicate.get_data(as_text=True)
    client.post(f"/annuaire/mandats/{mandate.id}/modifier", data={"rum": rum, "signed_on": "2026-09-01",
                                                                  "revoked_on": "2026-10-01"})
    assert db.session.get(SepaMandate, mandate.id).revoked_on == date(2026, 10, 1)
    client.post(f"/annuaire/banque/{account.id}/supprimer")
    assert db.session.scalar(db.select(db.func.count(SepaMandate.id))) == 0
    assert db.session.scalar(db.select(db.func.count(BankAccount.id))) == 0


def test_expired_mandates_are_purged_with_their_last_bank_account(app):
    home = household()
    expired, recent, waiting, mixed = (bank_account(home, holder=name) for name in ("A", "B", "C", "D"))
    db.session.add_all([
        SepaMandate(bank_account=expired, rum="R1", signed_on=date(2023, 1, 1), last_debit_on=date(2025, 9, 30)),
        SepaMandate(bank_account=recent, rum="R2", signed_on=date(2023, 1, 1), last_debit_on=date(2025, 10, 10)),
        SepaMandate(bank_account=mixed, rum="R3", signed_on=date(2023, 1, 1), last_debit_on=date(2024, 1, 1)),
        SepaMandate(bank_account=mixed, rum="R4", signed_on=date(2024, 2, 1)),
    ])
    db.session.commit()
    assert bank.purge_expired_mandates(date(2026, 11, 9)) == (2, 1)
    db.session.commit()
    left = {a.holder_name for a in db.session.scalars(db.select(BankAccount))}
    assert left == {"B", "C", "D"}  # C n'a pas encore de mandat : il n'est pas touché
    assert {m.rum for m in db.session.scalars(db.select(SepaMandate))} == {"R2", "R4"}


def test_months_ago_handles_month_ends():
    assert bank._months_ago(date(2026, 3, 31), 13) == date(2025, 2, 28)
    assert bank._months_ago(date(2025, 3, 31), 13) == date(2024, 2, 29)
    assert bank._months_ago(date(2026, 11, 9), 13) == date(2025, 10, 9)


def test_scheduler_runs_the_purge(app):
    assert "mandat(s) SEPA" in tasks.run()


def test_cli_rotate_and_generate(app):
    app.config["BANK_DATA_KEYS"] = KEY_1
    account = bank_account(household())
    runner = app.test_cli_runner()
    generated = runner.invoke(args=["bank-generate-key"])
    line = generated.stdout.strip().splitlines()[0]
    assert line.startswith("2:") and bank.parse_keys(line)
    app.config["BANK_DATA_KEYS"] = f"{line},{KEY_1}"
    result = runner.invoke(args=["bank-rotate-key"])
    assert result.exit_code == 0 and "1 IBAN rechiffré(s) avec la clé n° 2" in result.output
    app.config["BANK_DATA_KEYS"] = line
    assert bank.reveal(db.session.get(BankAccount, account.id)) == IBAN
    assert db.session.scalar(db.select(Activity).filter_by(action="bank.rotate")) is not None
    app.config["BANK_DATA_KEYS"] = ""
    assert runner.invoke(args=["bank-rotate-key"]).exit_code != 0
