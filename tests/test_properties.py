"""Propriétés vérifiées sur des données générées (Hypothesis)."""

import unicodedata
from datetime import date, datetime

from flask import Flask
from hypothesis import given
from hypothesis import strategies as st

from app.blueprints.auth import safe_next
from app.extensions import fold
from app.filters import date_fr


@given(st.text())
def test_fold_is_stable_and_has_no_accent(text):
    folded = fold(text)
    assert fold(folded) == folded
    assert not any(unicodedata.combining(c) for c in unicodedata.normalize("NFKD", folded))


@given(st.text(alphabet="aeiouéèêëàâîïôöûüçÉÈÀÇ", min_size=1))
def test_fold_ignores_case_and_accents_of_french_names(name):
    assert fold(name.upper()) == fold(name.lower())
    assert fold(name).isascii()


def _app():
    app = Flask(__name__)
    app.add_url_rule("/", "dashboard.index", lambda: "")
    return app


APP = _app()


@given(st.text(alphabet="/\\:@.?#%aé htps", max_size=30))
def test_safe_next_never_leaves_the_site(target):
    from urllib.parse import urlsplit

    with APP.test_request_context():
        result = safe_next(target)
    parts = urlsplit(result)
    assert result.startswith("/") and not result.startswith("//") and "\\" not in result
    assert not parts.netloc and not parts.scheme


@given(st.dates(min_value=date(1900, 1, 1), max_value=date(2100, 12, 31)))
def test_date_fr_always_shows_day_and_year(value):
    text = date_fr(value)
    assert str(value.year) in text and text.startswith(f"{value.day} ")


@given(st.datetimes(min_value=datetime(1900, 1, 1), max_value=datetime(2100, 12, 31)))
def test_date_fr_with_time(value):
    assert date_fr(value, with_time=True).endswith(f"à {value:%H:%M}")


def test_date_fr_empty_values():
    assert date_fr(None) == "" and date_fr("texte") == "texte"
