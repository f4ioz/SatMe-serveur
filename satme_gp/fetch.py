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


def intervalle_min(store: Store, s: Source) -> int:
    n = s.intervalle_min or store.reglage_int("intervalle_min")
    return max(n, CELESTRAK_MIN_MIN) if est_celestrak(s.url) else max(n, 5)


def a_faire(store: Store, s: Source, maintenant: float) -> bool:
    """Is [s] due? After a failure, sooner — except CelesTrak, never sooner."""
    if not s.actif:
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
    entetes = {"User-Agent": store.reglage("user_agent"), "Accept": "application/json",
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
        store.note_essai(s.id, maintenant, f"HTTP {status}")
        return f"{s.id} : HTTP {status}"
    try:
        bons = omm.lit(corps)
    except omm.FichierRefuse as e:
        store.note_essai(s.id, maintenant, f"refusé : {e}")
        return f"{s.id} : refusé ({e})"
    store.ecrit_groupe(s.id, bons)
    store.note_succes(s.id, maintenant, h.get("etag", ""), h.get("last-modified", ""), len(bons))
    return f"{s.id} : {len(bons)} éléments"


def tour(store: Store, telecharge: Telecharge = telecharge_http, maintenant: float | None = None) -> list[str]:
    """Fetches every source that is due."""
    maintenant = time.time() if maintenant is None else maintenant
    return [recupere(store, s, telecharge, maintenant)
            for s in store.sources() if a_faire(store, s, maintenant)]


class Planificateur(threading.Thread):
    """Background loop: checks every half minute which source is due."""

    def __init__(self, store: Store, periode_s: float = 30):
        super().__init__(name="satme-gp-fetch", daemon=True)
        self.store = store
        self.periode_s = periode_s
        self.arret = threading.Event()

    def run(self) -> None:
        while not self.arret.is_set():
            for ligne in tour(self.store):
                log.info("%s", ligne)
            self.arret.wait(self.periode_s)


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

    def cherche(self, n: int, maintenant: float | None = None) -> list[dict] | None:
        """The records for [n], or None when unknown or unreachable."""
        maintenant = time.time() if maintenant is None else maintenant
        cache = self.store.catnr_cache(n)
        if cache and maintenant - cache[1] < self.store.reglage_int("catnr_cache_min") * 60:
            return json.loads(cache[0]) or None
        # Stale copy when upstream may not be asked now: better than nothing.
        perime = (json.loads(cache[0]) or None) if cache else None
        if self.store.reglage("catnr_amont") != "1" or not self._quota(maintenant):
            return perime
        url = self.store.reglage("catnr_url").replace("{n}", str(n))
        try:
            status, _, corps = self.telecharge(url, {"User-Agent": self.store.reglage("user_agent"),
                                                     "Accept": "application/json"})
        except (OSError, ValueError):
            # A network failure says nothing about the satellite: not cached.
            return perime
        if status not in (200, 404):
            return perime
        try:
            bons = omm.lit(corps) if status == 200 else []
        except omm.FichierRefuse:
            # CelesTrak answers "No GP data found" in plain text: an unknown number.
            bons = []
        # "No such object" is cached too: asked again, it costs nothing upstream.
        self.store.pose_catnr(n, json.dumps(bons, separators=(",", ":")))
        return bons or None
