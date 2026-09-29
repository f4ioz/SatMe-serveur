# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fetching the sources, politely.

**CelesTrak blocks addresses that ask too often** — it happened to SatMe's
author. So a CelesTrak group is never fetched twice within two hours, even
after a failure, and every request says who it is and whether it already
has the file (ETag / Last-Modified: an unchanged group costs one short 304).
"""

from __future__ import annotations

import collections
import gzip
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from . import omm
from .store import Source, Store

log = logging.getLogger("satme_gp")

# (status, headers, body): replaced by a fake in the tests.
Telecharge = Callable[[str, dict], tuple[int, dict, bytes]]

CELESTRAK_MIN_MIN = 120
ECHEC_REESSAI_MIN = 15
# After a 403 or 429 from CelesTrak, nothing is asked there for this long,
# groups and numbers alike: insisting while blocked is what lengthens a block.
PAUSE_BLOCAGE_S = 6 * 3600
# Between two CelesTrak requests of one round, rather than a burst.
ECART_CELESTRAK_S = 3.0


def telecharge_http(url: str, entetes: dict, delai_s: float = 60) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers=entetes)
    try:
        with urllib.request.urlopen(req, timeout=delai_s) as r:
            corps = r.read()
            h = {k.lower(): v for k, v in r.headers.items()}
            status = r.status
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, b""
    if h.get("content-encoding") == "gzip":
        corps = gzip.decompress(corps)
    return status, h, corps


def est_celestrak(url: str) -> bool:
    return "celestrak.org" in url or "celestrak.com" in url


def celestrak_en_pause(store: Store, maintenant: float) -> bool:
    return maintenant < store.reglage_float("celestrak_pause_jusqua")


def note_blocage(store: Store, url: str, status: int, h: dict, maintenant: float) -> None:
    """A 403 or 429 from CelesTrak pauses every CelesTrak request (Retry-After if longer)."""
    if not est_celestrak(url) or status not in (403, 429):
        return
    duree = PAUSE_BLOCAGE_S
    try:
        duree = max(duree, int(h.get("retry-after", "0")))
    except ValueError:
        pass
    store.pose_reglage("celestrak_pause_jusqua", str(maintenant + duree))
    store.pose_reglage("celestrak_pause_raison", f"HTTP {status}")
    log.warning("CelesTrak a répondu %s : plus aucune requête pendant %d h", status, duree // 3600)


def intervalle_min(store: Store, s: Source) -> int:
    n = s.intervalle_min or store.reglage_int("intervalle_min")
    return max(n, CELESTRAK_MIN_MIN) if est_celestrak(s.url) else max(n, 5)


def a_faire(store: Store, s: Source, maintenant: float) -> bool:
    """Is [s] due? After a failure, sooner — except CelesTrak, never sooner."""
    if not s.actif:
        return False
    if est_celestrak(s.url) and celestrak_en_pause(store, maintenant):
        return False
    if s.dernier_essai == 0:
        return True
    attente = intervalle_min(store, s)
    if s.erreur and not est_celestrak(s.url):
        attente = min(attente, ECHEC_REESSAI_MIN)
    return maintenant - s.dernier_essai >= attente * 60


def recupere(store: Store, s: Source, telecharge: Telecharge = telecharge_http,
             maintenant: float | None = None) -> str:
    """Fetches one source. Returns a short report; the last good file stays on failure."""
    maintenant = time.time() if maintenant is None else maintenant
    entetes = {"User-Agent": store.reglage("user_agent"), "Accept": "application/json, text/plain;q=0.5",
               "Accept-Encoding": "gzip"}
    # Only when a file is there to fall back on: otherwise a 304 leaves nothing.
    if store.lit_groupe(s.id) is not None:
        if s.etag:
            entetes["If-None-Match"] = s.etag
        if s.last_modified:
            entetes["If-Modified-Since"] = s.last_modified
    try:
        status, h, corps = telecharge(s.url, entetes)
    except Exception as e:  # network, DNS, timeout
        store.note_essai(s.id, maintenant, f"réseau : {e.__class__.__name__}")
        return f"{s.id} : réseau ({e.__class__.__name__})"
    if status == 304:
        store.note_succes(s.id, maintenant, s.etag, s.last_modified, s.nombre)
        return f"{s.id} : inchangé"
    if status != 200:
        note_blocage(store, s.url, status, h, maintenant)
        store.note_essai(s.id, maintenant, f"HTTP {status}")
        return f"{s.id} : HTTP {status}"
    try:
        bons = omm.lit_source(corps)
    except omm.FichierRefuse as e:
        store.note_essai(s.id, maintenant, f"refusé : {e}")
        return f"{s.id} : refusé ({e})"
    store.ecrit_groupe(s.id, bons)
    store.note_succes(s.id, maintenant, h.get("etag", ""), h.get("last-modified", ""), len(bons))
    return f"{s.id} : {len(bons)} éléments"


def tour(store: Store, telecharge: Telecharge = telecharge_http, maintenant: float | None = None,
         ecart_s: float = ECART_CELESTRAK_S) -> list[str]:
    """Fetches every source that is due, CelesTrak ones a few seconds apart."""
    maintenant = time.time() if maintenant is None else maintenant
    lignes, celestrak_avant = [], False
    for s in store.sources():
        # Checked again before each: a 403 on the first stops the others.
        if not a_faire(store, s, maintenant):
            continue
        if est_celestrak(s.url):
            if celestrak_avant and ecart_s:
                time.sleep(ecart_s)
            celestrak_avant = True
        lignes.append(recupere(store, s, telecharge, maintenant))
        log.info("%s", lignes[-1])  # as it happens, not once the round is over
    return lignes


class Planificateur(threading.Thread):
    """Background loop: checks every half minute which source is due."""

    def __init__(self, store: Store, periode_s: float = 30):
        super().__init__(name="satme-gp-fetch", daemon=True)
        self.store = store
        self.periode_s = periode_s
        self.arret = threading.Event()

    def run(self) -> None:
        while not self.arret.is_set():
            tour(self.store)
            self.arret.wait(self.periode_s)


# Alpha-5 ends at Z9999; a new satellite is never far above the known ones.
NUMERO_MAX = 339_999
NUMERO_MARGE = 5_000


class Amont:
    """
    A satellite outside every group, asked upstream by catalogue number and
    cached. Capped per hour for all clients together: a client asking for
    thousands of numbers must not get the server blocked upstream.
    """

    def __init__(self, store: Store, telecharge: Telecharge = telecharge_http):
        self.store = store
        self.telecharge = telecharge
        self._recents: collections.deque[float] = collections.deque()
        self._verrou = threading.Lock()

    def _quota(self, maintenant: float) -> bool:
        with self._verrou:
            while self._recents and maintenant - self._recents[0] > 3600:
                self._recents.popleft()
            if len(self._recents) >= self.store.reglage_int("catnr_max_heure"):
                return False
            self._recents.append(maintenant)
            return True

    def cache_min(self) -> int:
        """Minutes a lookup is kept: never under two hours for CelesTrak."""
        n = self.store.reglage_int("catnr_cache_min")
        return max(n, CELESTRAK_MIN_MIN) if est_celestrak(self.store.reglage("catnr_url")) else n

    def cherche(self, n: int, maintenant: float | None = None,
                plus_grand_connu: int | None = None) -> list[dict] | None:
        """
        The records for [n], or None when unknown or unreachable.
        A number far above every known one ([plus_grand_connu] + NUMERO_MARGE)
        or beyond Alpha-5 is not asked: it can only be an upstream error.
        """
        maintenant = time.time() if maintenant is None else maintenant
        cache = self.store.catnr_cache(n)
        if cache and maintenant - cache[1] < self.cache_min() * 60:
            return json.loads(cache[0]) or None
        # Stale copy when upstream may not be asked now: better than nothing.
        perime = (json.loads(cache[0]) or None) if cache else None
        url = self.store.reglage("catnr_url").replace("{n}", str(n))
        if n < 1 or n > NUMERO_MAX or (plus_grand_connu and n > plus_grand_connu + NUMERO_MARGE):
            return perime
        if est_celestrak(url) and celestrak_en_pause(self.store, maintenant):
            return perime
        if self.store.reglage("catnr_amont") != "1" or not self._quota(maintenant):
            return perime
        try:
            status, h, corps = self.telecharge(url, {"User-Agent": self.store.reglage("user_agent"),
                                                     "Accept": "application/json"})
        except (OSError, ValueError):
            # A network failure says nothing about the satellite: not cached.
            return perime
        if status not in (200, 404):
            note_blocage(self.store, url, status, h, maintenant)
            return perime
        try:
            bons = omm.lit(corps) if status == 200 else []
        except omm.FichierRefuse:
            # CelesTrak answers "No GP data found" in plain text: an unknown number.
            bons = []
        # "No such object" is cached too: asked again, it costs nothing upstream.
        self.store.pose_catnr(n, json.dumps(bons, separators=(",", ":")))
        return bons or None
