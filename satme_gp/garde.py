# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The guard: who connects, and who is shut out.

A phone asks a few files a few times a day. What asks far more, or probes
for /.env and /wp-login.php, is a robot: it is banned for a day, and the
administrator sees it on the connections page (country, SatMe or not,
requests, refusals).

Addresses of the local network are never banned automatically: behind a
proxy that does not pass X-Forwarded-For, every visitor has the proxy's
address, and banning it would shut everyone out.

Connections are counted in memory and written every half minute: a flood
must not become a flood of database writes. Addresses are personal data:
kept [connexions_jours] days, then deleted.
"""

from __future__ import annotations

import collections
import re
import threading
import time

from .pays import Pays, drapeau, privee
from .store import Store

# SatMe says who it is: "SatMe/20.73 (Android 14)"; up to 20.72, "SatCombo/1.0".
SATME = re.compile(r"^(SatMe|SatCombo)/(\S+)")
# The element files: a 404 there is an answer (group off, unknown number), not a probe.
API = ("/gp/", "/gp.php", "/NORAD/")
# Paths only a scanner asks for: one of these, answered 404, bans at once.
PIEGES = re.compile(r"(^/\.env|^/\.git|/wp-|wp-login|xmlrpc|phpmyadmin|/cgi-bin/|/vendor/phpunit"
                    r"|\.(?:asp|aspx|jsp|cgi)$|^/(?!gp\.php$|NORAD/elements/gp\.php$).*\.php$)", re.I)
FLUSH_S = 30


def version_satme(ua: str) -> str | None:
    """SatMe's version from its User-Agent ("≤ 20.72" for the old SatCombo name), or None."""
    m = SATME.match(ua or "")
    if not m:
        return None
    return m.group(2) if m.group(1) == "SatMe" else "≤ 20.72"


class Garde:
    def __init__(self, store: Store, pays: Pays | None = None):
        self.store = store
        self.pays = pays or Pays(store.dossier)
        self._verrou = threading.Lock()
        self._bannis: dict[str, tuple[float, str]] = store.bannis()
        self._refus: dict[str, collections.deque[float]] = {}
        self._inconnus: dict[str, collections.deque[float]] = {}
        # (day, ip) → [requests, blocked, user agent, first, last]
        self._journal: dict[tuple[str, str], list] = {}
        self._vide_le = time.time()

    # ------------------------------------------------------------ bans

    def banni(self, ip: str, maintenant: float | None = None) -> tuple[float, str] | None:
        maintenant = time.time() if maintenant is None else maintenant
        with self._verrou:
            b = self._bannis.get(ip)
            if b and b[0] <= maintenant:
                del self._bannis[ip]
                self.store.debannit(ip)
                return None
            return b

    def bannit(self, ip: str, raison: str, duree_s: float | None = None,
               maintenant: float | None = None, auto: bool = True) -> bool:
        """Bans [ip]. An automatic ban never touches a local address."""
        if auto and privee(ip):
            return False
        maintenant = time.time() if maintenant is None else maintenant
        duree_s = self.store.reglage_int("ban_heures") * 3600 if duree_s is None else duree_s
        with self._verrou:
            self._bannis[ip] = (maintenant + duree_s, raison)
        self.store.bannit(ip, maintenant + duree_s, raison)
        return True

    def debannit(self, ip: str) -> None:
        with self._verrou:
            self._bannis.pop(ip, None)
            self._refus.pop(ip, None)
            self._inconnus.pop(ip, None)
        self.store.debannit(ip)

    def liste_bannis(self, maintenant: float | None = None) -> list[tuple[str, float, str]]:
        maintenant = time.time() if maintenant is None else maintenant
        with self._verrou:
            return sorted(((ip, j, r) for ip, (j, r) in self._bannis.items() if j > maintenant),
                          key=lambda x: -x[1])

    @staticmethod
    def _compte(table: dict, ip: str, maintenant: float, fenetre_s: float) -> int:
        q = table.setdefault(ip, collections.deque())
        q.append(maintenant)
        while q and maintenant - q[0] > fenetre_s:
            q.popleft()
        if len(table) > 20_000:
            for k in [k for k, v in table.items() if not v]:
                del table[k]
        return len(q)

    # -------------------------------------------------------- requests

    def refuse(self, ip: str, ua: str, chemin: str, maintenant: float | None = None) -> str | None:
        """Why this request is refused before anything else, or None."""
        maintenant = time.time() if maintenant is None else maintenant
        b = self.banni(ip, maintenant)
        if b:
            return f"adresse bannie jusqu'au {time.strftime('%d/%m %H:%M', time.gmtime(b[0]))} UTC"
        if (self.store.reglage("satme_seul") == "1" and chemin.startswith(("/gp", "/NORAD"))
                and version_satme(ua) is None):
            return "réservé à l'application SatMe"
        return None

    def note(self, ip: str, ua: str, chemin: str, statut: int, maintenant: float | None = None) -> None:
        """Counts one answered request; bans what behaves like a robot."""
        maintenant = time.time() if maintenant is None else maintenant
        cle = (time.strftime("%Y-%m-%d", time.gmtime(maintenant)), ip)
        with self._verrou:
            ligne = self._journal.get(cle)
            if ligne is None:
                ligne = self._journal[cle] = [0, 0, "", maintenant, maintenant]
            ligne[0] += 1
            ligne[1] += statut in (403, 429)
            ligne[2] = (ua or "")[:160]
            ligne[4] = maintenant
            n429 = self._compte(self._refus, ip, maintenant, 3600) if statut == 429 else 0
            n404 = (self._compte(self._inconnus, ip, maintenant, 600)
                    if statut == 404 and not chemin.startswith(API) else 0)
        if statut == 404 and PIEGES.search(chemin):
            self.bannit(ip, f"sonde {chemin[:60]}", maintenant=maintenant)
        elif n429 >= self.store.reglage_int("ban_refus"):
            self.bannit(ip, f"{n429} refus pour excès en une heure", maintenant=maintenant)
        elif n404 >= self.store.reglage_int("ban_inconnus"):
            self.bannit(ip, f"{n404} pages inexistantes en 10 min", maintenant=maintenant)
        if maintenant - self._vide_le >= FLUSH_S:
            self.vide(maintenant)

    def vide(self, maintenant: float | None = None) -> None:
        """Writes the counted connections, with their country, and forgets old ones."""
        maintenant = time.time() if maintenant is None else maintenant
        with self._verrou:
            journal, self._journal = self._journal, {}
            self._vide_le = maintenant
        lignes = []
        for (jour, ip), (n, bloques, ua, premier, dernier) in journal.items():
            code, _ = self.pays.de(ip)
            lignes.append((jour, ip, code, n, bloques, ua, version_satme(ua) or "", premier, dernier))
        if lignes:
            self.store.note_connexions(lignes)
        self.store.oublie_connexions(self.store.reglage_int("connexions_jours"))

    # ------------------------------------------------------------ admin

    def tableau(self, jours: int) -> dict:
        """What the connections page shows, over the last [jours] days."""
        self.vide()
        par_ip = self.store.connexions(jours)
        noms: dict[str, str] = {}
        pays: dict[str, dict] = {}
        for r in par_ip:
            code = r["pays"]
            if code not in noms:
                noms[code] = self.pays.de(r["ip"])[1] if code not in ("?",) else "inconnu"
            p = pays.setdefault(code, {"code": code, "nom": noms[code], "drapeau": drapeau(code),
                                       "ips": 0, "n": 0, "satme": 0, "bloques": 0})
            p["ips"] += 1
            p["n"] += r["n"]
            p["bloques"] += r["bloques"]
            p["satme"] += r["n"] if r["satme"] else 0
        total = sum(r["n"] for r in par_ip)
        tous_prives = bool(par_ip) and all(privee(r["ip"]) for r in par_ip)
        return {
            "ips": [dict(r) | {"drapeau": drapeau(r["pays"]), "nom_pays": noms.get(r["pays"], "?")}
                    for r in par_ip[:300]],
            "pays": sorted(pays.values(), key=lambda p: -p["n"]),
            "total": total, "nb_ips": len(par_ip),
            "satme": sum(r["n"] for r in par_ip if r["satme"]),
            "bloques": sum(r["bloques"] for r in par_ip),
            "tous_prives": tous_prives,
        }
