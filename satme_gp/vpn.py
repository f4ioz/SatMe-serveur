# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The optional Mullvad tunnel, seen from the admin pages.

The server runs unprivileged and cannot touch the network itself: it writes
a one-line request, and deploy/satme-gp-vpn (root, started by a systemd path
unit) checks it and does the work, then writes its state where this module
reads it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

from .store import Store

ETAT = Path(os.environ.get("SATME_GP_VPN_ETAT", "/var/lib/satme-gp-vpn")) / "etat.json"
RELAIS_URL = "https://api.mullvad.net/public/relays/wireguard/v2/"
VERIF_URL = "https://am.i.mullvad.net/json"
RELAIS_CACHE_S = 86_400


def etat() -> dict:
    """What the root helper last wrote; {} when it is not installed."""
    try:
        return json.loads(ETAT.read_text())
    except (OSError, ValueError):
        return {}


def demande(store: Store, ligne: str) -> str:
    """Writes a request for the root helper. Returns its token, echoed in the state."""
    jeton = secrets.token_urlsafe(9)
    d = store.dossier / "vpn"
    d.mkdir(exist_ok=True)
    # Written in place (not renamed): the path unit reacts to the file being closed.
    (d / "demande").write_text(f"{ligne} {jeton}\n")
    return jeton


def verifie_fichier(texte: str) -> str:
    """A WireGuard file from mullvad.net, roughly checked before the root helper
    checks it strictly. Returns the reason it is refused, or ""."""
    if len(texte) > 4096:
        return "fichier trop long pour un fichier WireGuard"
    if not re.search(r"^\s*PrivateKey\s*=\s*[A-Za-z0-9+/]{43}=\s*$", texte, re.M):
        return "pas de PrivateKey lisible : est-ce bien le fichier WireGuard de mullvad.net ?"
    if not re.search(r"^\s*Address\s*=", texte, re.M):
        return "pas de ligne Address dans le fichier"
    return ""


def envoie_cle(store: Store, texte: str, lieu: str) -> str:
    """Leaves the file for the root helper (which removes it) and asks for key + exit."""
    d = store.dossier / "vpn"
    d.mkdir(exist_ok=True)
    f = d / "import.conf"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as w:
        w.write(texte)
    return demande(store, f"cle {lieu}")


def pays(sorties_: list[dict]) -> list[tuple[str, str]]:
    """(code, country) of the exits, for the country choice: ("fr", "France")…"""
    vus = {}
    for x in sorties_:
        vus.setdefault(x["hostname"].split("-")[0], x["pays"])
    return sorted(vus.items(), key=lambda p: p[1])


def attend(jeton: str, delai_s: float = 20) -> dict:
    """The state once the helper has handled [jeton], or the current one after [delai_s]."""
    fin = time.monotonic() + delai_s
    while time.monotonic() < fin:
        e = etat()
        if e.get("jeton") == jeton:
            return e
        time.sleep(0.5)
    return etat()


def sorties(store: Store, telecharge) -> list[dict]:
    """Mullvad's WireGuard exits in service, by country then city, cached a day."""
    cache = store.dossier / "vpn" / "relais.json"
    try:
        if time.time() - cache.stat().st_mtime < RELAIS_CACHE_S:
            return json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    try:
        status, _, corps = telecharge(RELAIS_URL, {"User-Agent": store.reglage("user_agent")})
        if status != 200:
            raise OSError(f"HTTP {status}")
        d = json.loads(corps)
    except (OSError, ValueError):
        try:
            return json.loads(cache.read_text())  # stale beats nothing
        except (OSError, ValueError):
            return []
    lieux = d.get("locations", {})
    liste = []
    for r in d.get("wireguard", {}).get("relays", []):
        if not r.get("active"):
            continue
        lieu = lieux.get(r.get("location"), {})
        liste.append({"hostname": r.get("hostname", ""), "pays": lieu.get("country", "?"),
                      "ville": lieu.get("city", "?"), "fournisseur": r.get("provider", ""),
                      "possede": bool(r.get("owned"))})
    liste.sort(key=lambda x: (x["pays"], x["ville"], x["hostname"]))
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(liste))
    return liste


def teste(store: Store, telecharge) -> dict:
    """Mullvad's own check, asked from this process: through the tunnel when it is up."""
    try:
        status, _, corps = telecharge(VERIF_URL, {"User-Agent": store.reglage("user_agent")})
    except Exception as e:  # tunnel down with the lock on: the request cannot leave
        return {"erreur": f"aucune sortie ({e.__class__.__name__})"}
    if status != 200:
        return {"erreur": f"HTTP {status}"}
    try:
        d = json.loads(corps)
    except ValueError:
        return {"erreur": "réponse illisible"}
    return {"ip": d.get("ip", ""), "pays": d.get("country", ""), "ville": d.get("city", ""),
            "mullvad": bool(d.get("mullvad_exit_ip")), "sortie": d.get("mullvad_exit_ip_hostname", ""),
            "liste_noire": bool((d.get("blacklisted") or {}).get("blacklisted"))}
