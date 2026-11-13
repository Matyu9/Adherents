"""Jours fériés calculés et périodes importées d'un fichier (.ics ou CSV), pour le calendrier d'une saison.

Rien n'est ajouté sans que l'association le demande : les jours fériés s'ajoutent d'un bouton, et un import montre
d'abord un aperçu. Les services ne font pas de commit.
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..models import SeasonBreak

MAX_FILE = 512 * 1024


class ImportError_(ValueError):
    """Fichier illisible, avec un message à afficher tel quel."""


def easter(year):
    """Dimanche de Pâques (calendrier grégorien, algorithme de Meeus)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def public_holidays(start, end):
    """[(jour, nom)] des jours fériés en France entre deux dates incluses."""
    found = []
    for year in range(start.year, end.year + 1):
        sunday = easter(year)
        days = [
            (date(year, 1, 1), "Jour de l'an"),
            (sunday + timedelta(days=1), "Lundi de Pâques"),
            (date(year, 5, 1), "Fête du Travail"),
            (date(year, 5, 8), "Victoire 1945"),
            (sunday + timedelta(days=39), "Ascension"),
            (sunday + timedelta(days=50), "Lundi de Pentecôte"),
            (date(year, 7, 14), "Fête nationale"),
            (date(year, 8, 15), "Assomption"),
            (date(year, 11, 1), "Toussaint"),
            (date(year, 11, 11), "Armistice 1918"),
            (date(year, 12, 25), "Noël"),
        ]
        found += [(day, name) for day, name in days if start <= day <= end]
    return sorted(found)


@dataclass
class Row:
    label: str
    starts_on: date
    ends_on: date
    status: str = "nouvelle"  # nouvelle, déjà là, hors saison

    @property
    def key(self):
        return f"{self.starts_on.isoformat()}|{self.ends_on.isoformat()}|{self.label}"


def _decode(raw):
    if len(raw) > MAX_FILE:
        raise ImportError_("Fichier trop gros (512 Ko au plus).")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportError_("Fichier illisible : enregistrez-le en UTF-8.")


def _ics_date(value):
    digits = re.match(r"(\d{8})", value or "")
    if not digits:
        return None
    return datetime.strptime(digits.group(1), "%Y%m%d").date()


def parse_ics(text):
    """Événements d'un agenda .ics : (nom, premier jour, dernier jour inclus). Une fin « journée entière » est exclue,
    comme le veut le format ; une fin avec une heure est gardée."""
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    rows = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, re.S):
        fields = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            fields.setdefault(name.split(";")[0].upper(), (name, value.strip()))
        start_name, start_value = fields.get("DTSTART", ("", ""))
        start = _ics_date(start_value)
        if start is None:
            continue
        end_name, end_value = fields.get("DTEND", ("", ""))
        end = _ics_date(end_value)
        all_day = "VALUE=DATE" in end_name.upper() or (end_value and "T" not in end_value)
        if end is None:
            end = start
        elif all_day and end > start:
            end -= timedelta(days=1)
        label = fields.get("SUMMARY", ("", "Période"))[1].replace("\\,", ",").replace("\;", ";").strip() or "Période"
        rows.append(Row(label[:80], start, max(start, end)))
    if not rows and "BEGIN:VCALENDAR" not in text:
        raise ImportError_("Ce n'est pas un agenda .ics.")
    return rows


def _csv_date(text):
    text = text.strip()
    for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def parse_csv(text):
    """Lignes « nom ; premier jour ; dernier jour » (dernier jour facultatif). Séparateur ; ou , ; dates JJ/MM/AAAA
    ou AAAA-MM-JJ. Une ligne d'en-tête est ignorée."""
    sample = text[:2000]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    rows = []
    for number, cells in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter), start=1):
        cells = [c.strip() for c in cells]
        if not any(cells):
            continue
        if len(cells) < 2:
            raise ImportError_(f"Ligne {number} : il faut au moins un nom et une date.")
        start = _csv_date(cells[1])
        if start is None:
            if number == 1:
                continue  # en-tête
            raise ImportError_(f"Ligne {number} : date illisible « {cells[1]} ».")
        end = _csv_date(cells[2]) if len(cells) > 2 and cells[2] else start
        if end is None:
            raise ImportError_(f"Ligne {number} : date illisible « {cells[2]} ».")
        if end < start:
            raise ImportError_(f"Ligne {number} : la période finit avant de commencer.")
        rows.append(Row((cells[0] or "Période")[:80], start, end))
    return rows


def parse(filename, raw):
    text = _decode(raw)
    if (filename or "").lower().endswith(".ics") or "BEGIN:VCALENDAR" in text[:200]:
        return parse_ics(text)
    return parse_csv(text)


def plan(season, rows):
    """Marque chaque ligne : déjà là (même jours), hors saison, ou nouvelle. Les doublons du fichier sont retirés."""
    existing = {(b.starts_on, b.ends_on) for b in season.breaks}
    seen, result = set(), []
    for row in sorted(rows, key=lambda r: (r.starts_on, r.ends_on)):
        if (row.starts_on, row.ends_on) in seen:
            continue
        seen.add((row.starts_on, row.ends_on))
        if row.ends_on < season.starts_on or row.starts_on > season.ends_on:
            row.status = "hors saison"
        elif (row.starts_on, row.ends_on) in existing:
            row.status = "déjà là"
        result.append(row)
    return result


def holidays_plan(season):
    """Jours fériés de la saison, marqués comme un import."""
    return plan(season, [Row(name, day, day) for day, name in public_holidays(season.starts_on, season.ends_on)])


def apply(season, rows, kind):
    """Ajoute les lignes nouvelles comme périodes sans cours. Renvoie le nombre ajouté."""
    from ..extensions import db

    added = 0
    for row in rows:
        if row.status != "nouvelle":
            continue
        db.session.add(
            SeasonBreak(season=season, label=row.label, starts_on=row.starts_on, ends_on=row.ends_on, kind=kind)
        )
        added += 1
    db.session.flush()
    return added
