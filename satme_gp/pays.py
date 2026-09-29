# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Country of a client address, for the admin's connection page.

DB-IP's free "IP to Country Lite" database (CC BY 4.0, monthly): downloaded
by the server itself, kept in the data folder, replaced once a month. Without
it (not yet downloaded, maxminddb missing), countries show as "?".
"""

from __future__ import annotations

import gzip
import ipaddress
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("satme_gp")

URL = "https://download.db-ip.com/free/dbip-country-lite-{mois}.mmdb.gz"
ATTRIBUTION = "IP Geolocation by DB-IP (db-ip.com), CC BY 4.0"
AGE_MAX_S = 32 * 86_400
REESSAI_S = 6 * 3600


def drapeau(code: str) -> str:
    """"FR" → 🇫🇷 ; anything else → "🏳"."""
    if len(code) != 2 or not code.isalpha():
        return "\U0001F3F3"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code.upper())


LOCAUX = [ipaddress.ip_network(n) for n in
          ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")]


def privee(ip: str) -> bool:
    """LAN, loopback, link-local: the proxy's own address, never a visitor's."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_loopback or a.is_link_local or any(a in n for n in LOCAUX if n.version == a.version)


class Pays:
    def __init__(self, dossier: Path):
        self.fichier = Path(dossier) / "geo" / "dbip-country-lite.mmdb"
        self._lecteur = None
        self._charge_le = 0.0
        self._essai = 0.0
        self._cache: dict[str, tuple[str, str]] = {}
        self._verrou = threading.Lock()

    def _ouvre(self):
        try:
            m = self.fichier.stat().st_mtime
        except OSError:
            return None
        if self._lecteur is None or m != self._charge_le:
            try:
                import maxminddb
                self._lecteur = maxminddb.open_database(str(self.fichier))
                self._charge_le = m
                self._cache.clear()
            except Exception as e:  # missing module, damaged file
                log.warning("base des pays illisible : %s", e.__class__.__name__)
                self._lecteur = None
        return self._lecteur

    def de(self, ip: str) -> tuple[str, str]:
        """(ISO code, name in French) of [ip]; ("LAN", "réseau local") or ("?", "inconnu")."""
        if privee(ip):
            return "LAN", "réseau local"
        with self._verrou:
            if ip in self._cache:
                return self._cache[ip]
            lecteur = self._ouvre()
            r = ("?", "inconnu")
            if lecteur is not None:
                try:
                    d = lecteur.get(ip) or {}
                    c = d.get("country") or {}
                    noms = c.get("names") or {}
                    if c.get("iso_code"):
                        r = (c["iso_code"], noms.get("fr") or noms.get("en") or c["iso_code"])
                except ValueError:
                    pass
            if len(self._cache) > 50_000:
                self._cache.clear()
            self._cache[ip] = r
            return r

    def a_jour(self, maintenant: float | None = None) -> bool:
        maintenant = time.time() if maintenant is None else maintenant
        try:
            return maintenant - self.fichier.stat().st_mtime < AGE_MAX_S
        except OSError:
            return False

    def mise_a_jour(self, telecharge, user_agent: str, maintenant: float | None = None) -> str:
        """Downloads this month's file (or last month's) when ours is over a month old."""
        maintenant = time.time() if maintenant is None else maintenant
        if self.a_jour(maintenant) or maintenant - self._essai < REESSAI_S:
            return ""
        self._essai = maintenant
        t = time.gmtime(maintenant)
        mois = [f"{t.tm_year}-{t.tm_mon:02d}",
                f"{t.tm_year - (t.tm_mon == 1)}-{(t.tm_mon - 2) % 12 + 1:02d}"]
        for m in mois:
            try:
                status, _, corps = telecharge(URL.format(mois=m), {"User-Agent": user_agent})
            except Exception as e:
                return f"pays : réseau ({e.__class__.__name__})"
            if status != 200:
                continue
            try:
                brut = gzip.decompress(corps)
            except OSError:
                return "pays : fichier illisible"
            if b"MaxMind.com" not in brut[-200_000:]:  # the mmdb metadata marker
                return "pays : fichier inattendu"
            self.fichier.parent.mkdir(exist_ok=True)
            tmp = self.fichier.with_suffix(".tmp")
            tmp.write_bytes(brut)
            os.replace(tmp, self.fichier)
            return f"pays : base {m} installée"
        return "pays : base introuvable"
