# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checking OMM JSON records before they reach anyone.

A source may answer an HTML error page, a truncated file, or an empty array
during maintenance. Served as is, it would erase every phone's satellites.
Only a file whose records hold what SGP4 needs replaces the last good copy.
"""

from __future__ import annotations

import json
from datetime import datetime

# What SatMe's parser (OmmParser.kt) and SGP4 cannot do without.
CHAMPS_NOMBRES = (
    "MEAN_MOTION", "ECCENTRICITY", "INCLINATION", "RA_OF_ASC_NODE",
    "ARG_OF_PERICENTER", "MEAN_ANOMALY",
)


class FichierRefuse(ValueError):
    """The downloaded file is not usable; the last good copy stays."""


def _nombre(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def epoque(r: dict) -> datetime | None:
    """EPOCH as a datetime, or None when unreadable."""
    e = r.get("EPOCH")
    if not isinstance(e, str):
        return None
    try:
        return datetime.fromisoformat(e.replace("Z", "+00:00"))
    except ValueError:
        return None


def norad(r: dict) -> int | None:
    """NORAD_CAT_ID as an int (6 digits and more are fine), or None."""
    v = r.get("NORAD_CAT_ID")
    if isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def valide(r) -> bool:
    """One record usable for propagation."""
    if not isinstance(r, dict) or norad(r) is None or epoque(r) is None:
        return False
    for c in CHAMPS_NOMBRES:
        if _nombre(r.get(c)) is None:
            return False
    mm = _nombre(r["MEAN_MOTION"])
    ecc = _nombre(r["ECCENTRICITY"])
    return 0 < mm < 20 and 0 <= ecc < 1


def lit(contenu: bytes, part_minimale: float = 0.9) -> list[dict]:
    """
    Parses and checks a downloaded file. Returns its valid records.

    Refused when it is not a JSON array, when it is empty, or when fewer than
    [part_minimale] of its records are valid: a source that half-broke says
    more about the source than about the satellites.
    """
    try:
        donnees = json.loads(contenu)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FichierRefuse(f"pas du JSON : {e.__class__.__name__}") from e
    if not isinstance(donnees, list):
        raise FichierRefuse("pas une liste d'éléments OMM")
    if not donnees:
        raise FichierRefuse("liste vide")
    bons = [r for r in donnees if valide(r)]
    if len(bons) < part_minimale * len(donnees):
        raise FichierRefuse(f"{len(donnees) - len(bons)} éléments invalides sur {len(donnees)}")
    return bons


def plus_recents(listes: list[list[dict]]) -> dict[int, dict]:
    """Across sources, the most recent record of each satellite (by EPOCH)."""
    meilleurs: dict[int, dict] = {}
    for liste in listes:
        for r in liste:
            n = norad(r)
            e = epoque(r)
            if n is None or e is None:
                continue
            deja = meilleurs.get(n)
            if deja is None or epoque(deja) < e:
                meilleurs[n] = r
    return meilleurs
