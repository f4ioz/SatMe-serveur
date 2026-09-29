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
from datetime import datetime, timedelta, timezone

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


# ------------------------------------------------------------------ TLE

class TleInvalide(ValueError):
    """Two lines that are not a usable TLE (checksum, columns, values)."""


def _somme(ligne: str) -> bool:
    """TLE checksum: digits added, a minus sign counts one, modulo 10."""
    if len(ligne) < 69 or not ligne[68].isdigit():
        return False
    t = sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in ligne[:68])
    return t % 10 == int(ligne[68])


def _alpha5(champ: str) -> int:
    """NORAD number, Alpha-5 included ("A0001" = 100001; I and O are skipped)."""
    champ = champ.strip()
    if champ[:1].isalpha():
        lettres = "ABCDEFGHJKLMNPQRSTUVWXYZ"
        return (lettres.index(champ[0].upper()) + 10) * 10_000 + int(champ[1:])
    return int(champ)


def _exposant(champ: str) -> float:
    """Implied-decimal field with exponent: " 12345-3" = 0.12345e-3."""
    champ = champ.strip()
    if not champ:
        return 0.0
    signe = -1.0 if champ[0] == "-" else 1.0
    champ = champ.lstrip("+-")
    mantisse, exposant = champ[:-2], champ[-2:]
    return signe * float("0." + mantisse.strip()) * 10 ** int(exposant)


def tle_vers_omm(nom: str, l1: str, l2: str) -> dict:
    """
    One TLE (name, line 1, line 2) as an OMM record with CelesTrak's keys,
    so sources publishing TLE only (SatNOGS) are served like the others.
    """
    l1, l2 = l1.rstrip(), l2.rstrip()
    if not (l1.startswith("1 ") and l2.startswith("2 ")):
        raise TleInvalide("lignes 1 et 2 attendues")
    if not (_somme(l1) and _somme(l2)):
        raise TleInvalide("somme de contrôle fausse")
    try:
        n = _alpha5(l1[2:7])
        if _alpha5(l2[2:7]) != n:
            raise TleInvalide("numéros différents sur les deux lignes")
        aa = int(l1[18:20])
        annee = 2000 + aa if aa < 57 else 1900 + aa
        jour = float(l1[20:32])
        epoque_ = datetime(annee, 1, 1, tzinfo=timezone.utc) + timedelta(days=jour - 1)
        desig = l1[9:17].strip()
        if len(desig) >= 5 and desig[:5].isdigit():
            an = int(desig[:2])
            objet = f"{2000 + an if an < 57 else 1900 + an}-{desig[2:5]}{desig[5:]}"
        else:
            objet = ""
        return {
            "OBJECT_NAME": nom.strip(),
            "OBJECT_ID": objet,
            "EPOCH": epoque_.strftime("%Y-%m-%dT%H:%M:%S.%f"),
            "MEAN_MOTION": float(l2[52:63]),
            "ECCENTRICITY": float("0." + l2[26:33].strip()),
            "INCLINATION": float(l2[8:16]),
            "RA_OF_ASC_NODE": float(l2[17:25]),
            "ARG_OF_PERICENTER": float(l2[34:42]),
            "MEAN_ANOMALY": float(l2[43:51]),
            "EPHEMERIS_TYPE": int(l1[62]) if l1[62].isdigit() else 0,
            "CLASSIFICATION_TYPE": l1[7],
            "NORAD_CAT_ID": n,
            "ELEMENT_SET_NO": int(l1[64:68]) if l1[64:68].strip().isdigit() else 999,
            "REV_AT_EPOCH": int(l2[63:68]) if l2[63:68].strip().isdigit() else 0,
            "BSTAR": _exposant(l1[53:61]),
            "MEAN_MOTION_DOT": float(l1[33:43]),
            "MEAN_MOTION_DDOT": _exposant(l1[44:52]),
        }
    except (ValueError, IndexError) as e:
        raise TleInvalide(f"colonnes illisibles : {e.__class__.__name__}") from e


def _depuis_tle(triplets, total: int, part_minimale: float) -> list[dict]:
    bons = []
    for nom, l1, l2 in triplets:
        nom = nom[2:] if nom.startswith("0 ") else nom
        try:
            r = tle_vers_omm(nom, l1, l2)
        except TleInvalide:
            continue
        if valide(r):
            bons.append(r)
    if not total:
        raise FichierRefuse("liste vide")
    if len(bons) < part_minimale * total:
        raise FichierRefuse(f"{total - len(bons)} éléments invalides sur {total}")
    return bons


def lit_tle_texte(texte: str, part_minimale: float = 0.9) -> list[dict]:
    """
    Plain TLE text, with or without name lines (3LE or 2LE), as OMM.
    Without a name line, the NORAD number stands for the name.
    """
    lignes = [x.rstrip() for x in texte.splitlines() if x.strip()]
    triplets, i = [], 0
    while i < len(lignes):
        if lignes[i].startswith("1 ") and i + 1 < len(lignes) and lignes[i + 1].startswith("2 "):
            triplets.append((lignes[i][2:7].strip(), lignes[i], lignes[i + 1]))
            i += 2
        elif i + 2 < len(lignes) and lignes[i + 1].startswith("1 ") and lignes[i + 2].startswith("2 "):
            triplets.append((lignes[i], lignes[i + 1], lignes[i + 2]))
            i += 3
        else:
            i += 1
    if not triplets:
        raise FichierRefuse("ni JSON ni TLE")
    return _depuis_tle(triplets, len(triplets), part_minimale)


def lit_source(contenu: bytes, part_minimale: float = 0.9) -> list[dict]:
    """
    Any source's file as checked OMM records, its format recognized:
    OMM JSON (CelesTrak, SupGP, AMSAT), SatNOGS DB's TLE JSON
    ({tle0, tle1, tle2…}), or plain TLE text. Same refusal rule as [lit].
    """
    try:
        donnees = json.loads(contenu)
    except UnicodeDecodeError as e:
        raise FichierRefuse(f"pas du texte : {e.__class__.__name__}") from e
    except json.JSONDecodeError:
        return lit_tle_texte(contenu.decode("utf-8", "replace"), part_minimale)
    if isinstance(donnees, list) and donnees and isinstance(donnees[0], dict) and "tle1" in donnees[0]:
        triplets = [(str(x.get("tle0") or ""), str(x.get("tle1") or ""), str(x.get("tle2") or ""))
                    for x in donnees if isinstance(x, dict)]
        return _depuis_tle(triplets, len(donnees), part_minimale)
    return lit(contenu, part_minimale)
