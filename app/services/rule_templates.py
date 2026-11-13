"""Modèles de règles de tarifs : les cas courants en deux ou trois champs. Un modèle produit une règle ordinaire, en
brouillon, que le formulaire complet peut ensuite affiner."""

from . import fees
from .fees import FeeError

TEMPLATES = {
    "fratrie": {
        "title": "Réduction pour les frères et sœurs",
        "text": "À partir du n-ième élève du foyer, un pourcentage de réduction.",
        "fields": ("rank", "percent", "category"),
    },
    "quotient": {
        "title": "Réduction selon le quotient familial",
        "text": "Un pourcentage de réduction pour une tranche de quotient familial.",
        "fields": ("band", "percent", "category"),
    },
    "plusieurs": {
        "title": "Remise à partir du n-ième cours",
        "text": "Chez un même élève, à partir du n-ième élément d'une catégorie, un pourcentage de réduction.",
        "fields": ("rank", "percent", "category"),
    },
    "ajout": {
        "title": "Ajouter un élément (adhésion, frais de dossier…)",
        "text": "Un élément ajouté d'office, pour chaque élève ou une fois pour le foyer.",
        "fields": ("item", "per"),
    },
    "etiquette": {
        "title": "Prix fixe pour une étiquette",
        "text": "Un élément à un prix fixe pour les élèves qui ont une étiquette (personnel, bénévole…).",
        "fields": ("tag", "item", "price"),
    },
    "supplement": {
        "title": "Supplément hors commune",
        "text": "Un montant ajouté aux éléments d'une catégorie pour les foyers qui ne résident pas dans la commune.",
        "fields": ("amount", "category"),
    },
}


def _int(form, key, label, minimum=1):
    value = (form.get(key) or "").strip()
    if not value.lstrip("-").isdigit() or int(value) < minimum:
        raise FeeError(f"{label} : un nombre entier, {minimum} au moins.")
    return int(value)


def _percent(form):
    value = (form.get("percent") or "").strip().replace(",", ".").replace("%", "").strip().lstrip("-")
    if not value:
        raise FeeError("Indiquez le pourcentage de réduction.")
    return f"-{value}"


def _target(form):
    category = (form.get("category") or "").strip()
    return ({"categories": [category]}, f" ({category})") if category else ({}, "")


def build(key, form, season):
    """Règle brute (à faire valider par fees.save_rule) tirée d'un modèle et des champs saisis."""
    if key not in TEMPLATES:
        raise FeeError("Modèle inconnu.")
    if key == "fratrie":
        rank = _int(form, "rank", "Rang", 2)
        target, suffix = _target(form)
        return {
            "name": f"{_percent(form).lstrip('-')} % à partir du {rank}ᵉ élève{suffix}",
            "conditions": [[{"field": "eleve.rang", "op": "ge", "value": rank}]],
            "effect": {"kind": "percent", "value": _percent(form), "base": "current"},
            "target": target,
            "group": "fratrie",
        }
    if key == "quotient":
        band = (form.get("band") or "").strip()
        if not band:
            raise FeeError("Choisissez la tranche.")
        target, suffix = _target(form)
        return {
            "name": f"Tranche {band} : {_percent(form).lstrip('-')} %{suffix}",
            "conditions": [[{"field": "foyer.tranche_qf", "op": "in", "value": [band]}]],
            "effect": {"kind": "percent", "value": _percent(form), "base": "current"},
            "target": target,
            "group": "quotient",
        }
    if key == "plusieurs":
        rank = _int(form, "rank", "Rang", 2)
        target, suffix = _target(form)
        return {
            "name": f"{_percent(form).lstrip('-')} % à partir du {rank}ᵉ élément{suffix}",
            "conditions": [[{"field": "ligne.rang_eleve", "op": "ge", "value": rank}]],
            "effect": {"kind": "percent", "value": _percent(form), "base": "current"},
            "target": target,
        }
    if key == "ajout":
        item = form.get("item", type=int)
        per = "household" if form.get("per") == "household" else "student"
        label = next((i.label for i in fees.items(season) if i.id == item), None)
        if label is None:
            raise FeeError("Choisissez l'élément à ajouter.")
        return {
            "name": f"{label} {'une fois par foyer' if per == 'household' else 'pour chaque élève'}",
            "conditions": [],
            "effect": {"kind": "add", "item": item, "per": per},
            "target": {},
        }
    if key == "etiquette":
        tag = (form.get("tag") or "").strip()
        item = form.get("item", type=int)
        price = fees.parse_euros(form.get("price"))
        label = next((i.label for i in fees.items(season) if i.id == item), None)
        if not tag or label is None or price is None:
            raise FeeError("Indiquez l'étiquette, l'élément et son prix.")
        return {
            "name": f"{label} à prix fixe ({tag})",
            "conditions": [[{"field": "eleve.etiquettes", "op": "has_any", "value": [tag]}]],
            "effect": {"kind": "fixed", "value": price},
            "target": {"items": [item]},
        }
    amount = fees.parse_euros(form.get("amount"))
    if amount is None or amount <= 0:
        raise FeeError("Indiquez le montant du supplément.")
    target, suffix = _target(form)
    return {
        "name": f"Supplément hors commune{suffix}",
        "conditions": [[{"field": "foyer.resident", "op": "is", "value": False}]],
        "effect": {"kind": "amount", "value": amount},
        "target": target,
    }
