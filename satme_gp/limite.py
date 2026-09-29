# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Requests per client address over a sliding window.

A phone asks a handful of groups a few times a day; a client asking hundreds
of times is broken or abusive, and slows everyone. In memory: a restart
forgets, which is fine for a limit counted in minutes.
"""

from __future__ import annotations

import collections
import threading
import time


class Limiteur:
    def __init__(self):
        self._vus: dict[str, collections.deque[float]] = {}
        self._verrou = threading.Lock()

    def autorise(self, cle: str, limite: int, fenetre_s: float,
                 maintenant: float | None = None) -> tuple[bool, int]:
        """(allowed, seconds before the next request would be) for [cle]."""
        maintenant = time.time() if maintenant is None else maintenant
        with self._verrou:
            q = self._vus.setdefault(cle, collections.deque())
            while q and maintenant - q[0] >= fenetre_s:
                q.popleft()
            if len(q) >= limite:
                return False, max(1, int(fenetre_s - (maintenant - q[0])) + 1)
            q.append(maintenant)
            # Keep the table small: forget addresses with nothing left.
            if len(self._vus) > 10_000:
                for k in [k for k, v in self._vus.items() if not v]:
                    del self._vus[k]
            return True, 0

    def oublie(self, cle: str) -> None:
        with self._verrou:
            self._vus.pop(cle, None)
