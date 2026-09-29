# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Settings, sources, admin account and statistics: one SQLite file.

The group files themselves are plain JSON next to it, written atomically
(temporary file, then rename): a reader never sees half a file.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import omm

# SatMe's own list (Sources.kt), so the app can switch host without anything
# else changing. Two hours: CelesTrak asks not to fetch a group more often.
SOURCES_DEFAUT = [
    ("amsat", "Bulletin AMSAT", "https://newark192.amsat.org/gpdata/current/daily-bulletin.json"),
    ("amateur", "CelesTrak Amateur", "https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=json"),
    ("stations", "CelesTrak Stations", "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=json"),
    ("visual", "CelesTrak Visual", "https://celestrak.org/NORAD/elements/gp.php?GROUP=visual&FORMAT=json"),
    ("weather", "CelesTrak Weather", "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=json"),
    ("cubesat", "CelesTrak CubeSat", "https://celestrak.org/NORAD/elements/gp.php?GROUP=cubesat&FORMAT=json"),
    # TLE converted to OMM; every satellite SatNOGS follows, with the TLE source it trusts.
    ("satnogs", "SatNOGS DB", "https://db.satnogs.org/api/tle/?format=json"),
    # Operator ephemeris fitted by CelesTrak: often more accurate than the public GP.
    ("supgp_iss", "CelesTrak SupGP ISS",
     "https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?FILE=iss&FORMAT=json"),
]

REGLAGES_DEFAUT = {
    # Minutes between two fetches of one source (never below 120 for CelesTrak).
    "intervalle_min": "120",
    # Requests allowed per client IP per window.
    "limite_requetes": "120",
    "fenetre_s": "600",
    # A satellite outside every group, looked up upstream on demand.
    "catnr_amont": "1",
    "catnr_url": "https://celestrak.org/NORAD/elements/gp.php?CATNR={n}&FORMAT=json",
    "catnr_cache_min": "360",
    # Upstream lookups allowed per hour, all clients together.
    "catnr_max_heure": "20",
    # Sent upstream: says who fetches, as CelesTrak asks.
    "user_agent": "SatMe-GP-server/1.0 (+https://github.com/f4ioz/SatMe)",
    "nom_public": "Serveur GP SatMe",
}


@dataclass
class Source:
    id: str
    nom: str
    url: str
    actif: bool
    intervalle_min: int | None
    dernier_essai: float
    dernier_ok: float
    etag: str
    last_modified: str
    nombre: int
    erreur: str


class Store:
    def __init__(self, dossier: str | os.PathLike):
        self.dossier = Path(dossier)
        (self.dossier / "gp").mkdir(parents=True, exist_ok=True)
        self._verrou = threading.RLock()
        self._db = sqlite3.connect(self.dossier / "satme-gp.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._verrou, self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS reglages (cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, nom TEXT NOT NULL, url TEXT NOT NULL,
                    actif INTEGER NOT NULL DEFAULT 1, intervalle_min INTEGER,
                    dernier_essai REAL NOT NULL DEFAULT 0, dernier_ok REAL NOT NULL DEFAULT 0,
                    etag TEXT NOT NULL DEFAULT '', last_modified TEXT NOT NULL DEFAULT '',
                    nombre INTEGER NOT NULL DEFAULT 0, erreur TEXT NOT NULL DEFAULT '',
                    ordre INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS admin (nom TEXT PRIMARY KEY, hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS stats (jour TEXT, route TEXT, n INTEGER NOT NULL,
                    PRIMARY KEY (jour, route));
                CREATE TABLE IF NOT EXISTS catnr (norad INTEGER PRIMARY KEY, contenu TEXT NOT NULL,
                    quand REAL NOT NULL);
            """)
            for cle, valeur in REGLAGES_DEFAUT.items():
                self._db.execute("INSERT OR IGNORE INTO reglages VALUES (?, ?)", (cle, valeur))
            # 1.0.0 allowed 60 lookups an hour by default: lowered unless chosen.
            self._db.execute("UPDATE reglages SET valeur = '20' WHERE cle = 'catnr_max_heure' AND valeur = '60'")
            # A default source added by a newer version appears once; one the
            # administrator deleted does not come back.
            r = self._db.execute("SELECT valeur FROM reglages WHERE cle = 'sources_proposees'").fetchone()
            if r is None and self._db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]:
                # Database from 1.0.0: its first six were proposed already.
                proposees = {sid for sid, _, _ in SOURCES_DEFAUT[:6]}
            else:
                proposees = set(r["valeur"].split(",")) if r and r["valeur"] else set()
            for i, (sid, nom, url) in enumerate(SOURCES_DEFAUT):
                if sid not in proposees:
                    self._db.execute("INSERT OR IGNORE INTO sources (id, nom, url, ordre) VALUES (?, ?, ?, ?)",
                                     (sid, nom, url, i))
            self._db.execute("INSERT OR REPLACE INTO reglages VALUES ('sources_proposees', ?)",
                             (",".join(sid for sid, _, _ in SOURCES_DEFAUT),))

    # ---- settings ----

    def reglage(self, cle: str) -> str:
        with self._verrou:
            r = self._db.execute("SELECT valeur FROM reglages WHERE cle = ?", (cle,)).fetchone()
        return r["valeur"] if r else REGLAGES_DEFAUT.get(cle, "")

    def reglage_int(self, cle: str) -> int:
        try:
            return int(self.reglage(cle))
        except ValueError:
            return int(REGLAGES_DEFAUT[cle])

    def reglage_float(self, cle: str) -> float:
        try:
            return float(self.reglage(cle) or 0)
        except ValueError:
            return 0.0

    def pose_reglage(self, cle: str, valeur: str) -> None:
        with self._verrou, self._db:
            self._db.execute("INSERT OR REPLACE INTO reglages VALUES (?, ?)", (cle, valeur))

    # ---- sources ----

    def sources(self) -> list[Source]:
        with self._verrou:
            rows = self._db.execute("SELECT * FROM sources ORDER BY ordre, id").fetchall()
        return [Source(r["id"], r["nom"], r["url"], bool(r["actif"]), r["intervalle_min"],
                       r["dernier_essai"], r["dernier_ok"], r["etag"], r["last_modified"],
                       r["nombre"], r["erreur"]) for r in rows]

    def source(self, sid: str) -> Source | None:
        return next((s for s in self.sources() if s.id == sid), None)

    def pose_source(self, sid: str, nom: str, url: str, actif: bool, intervalle_min: int | None) -> None:
        with self._verrou, self._db:
            ordre = self._db.execute("SELECT COALESCE(MAX(ordre), 0) + 1 FROM sources").fetchone()[0]
            self._db.execute("""
                INSERT INTO sources (id, nom, url, actif, intervalle_min, ordre) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET nom = excluded.nom, url = excluded.url,
                    actif = excluded.actif, intervalle_min = excluded.intervalle_min""",
                             (sid, nom, url, int(actif), intervalle_min, ordre))

    def supprime_source(self, sid: str) -> None:
        with self._verrou, self._db:
            self._db.execute("DELETE FROM sources WHERE id = ?", (sid,))
        self.fichier(sid).unlink(missing_ok=True)

    def note_essai(self, sid: str, quand: float, erreur: str = "") -> None:
        with self._verrou, self._db:
            self._db.execute("UPDATE sources SET dernier_essai = ?, erreur = ? WHERE id = ?",
                             (quand, erreur, sid))

    def note_succes(self, sid: str, quand: float, etag: str, last_modified: str, nombre: int) -> None:
        with self._verrou, self._db:
            self._db.execute("""UPDATE sources SET dernier_essai = ?, dernier_ok = ?, etag = ?,
                                last_modified = ?, nombre = ?, erreur = '' WHERE id = ?""",
                             (quand, quand, etag, last_modified, nombre, sid))

    def force(self, ids: list[str]) -> None:
        """Makes these sources due now (admin's "refresh")."""
        with self._verrou, self._db:
            self._db.executemany("UPDATE sources SET dernier_essai = 0, etag = '', last_modified = '' WHERE id = ?",
                                 [(i,) for i in ids])

    # ---- group files ----

    def fichier(self, sid: str) -> Path:
        return self.dossier / "gp" / f"{sid}.json"

    def ecrit_groupe(self, sid: str, enregistrements: list[dict]) -> None:
        cible = self.fichier(sid)
        tmp = cible.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(enregistrements, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, cible)

    def lit_groupe(self, sid: str) -> list[dict] | None:
        try:
            return json.loads(self.fichier(sid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def catalogue(self) -> dict[int, dict]:
        """Most recent record of every satellite across active sources."""
        listes = [self.lit_groupe(s.id) or [] for s in self.sources() if s.actif]
        return omm.plus_recents(listes)

    # ---- upstream lookups cache ----

    def catnr_cache(self, n: int) -> tuple[str, float] | None:
        with self._verrou:
            r = self._db.execute("SELECT contenu, quand FROM catnr WHERE norad = ?", (n,)).fetchone()
        return (r["contenu"], r["quand"]) if r else None

    def pose_catnr(self, n: int, contenu: str) -> None:
        with self._verrou, self._db:
            self._db.execute("INSERT OR REPLACE INTO catnr VALUES (?, ?, ?)", (n, contenu, time.time()))

    # ---- admin ----

    def admin_hash(self, nom: str) -> str | None:
        with self._verrou:
            r = self._db.execute("SELECT hash FROM admin WHERE nom = ?", (nom,)).fetchone()
        return r["hash"] if r else None

    def pose_admin(self, nom: str, hash_: str) -> None:
        with self._verrou, self._db:
            self._db.execute("DELETE FROM admin")
            self._db.execute("INSERT INTO admin VALUES (?, ?)", (nom, hash_))

    def a_un_admin(self) -> bool:
        with self._verrou:
            return self._db.execute("SELECT COUNT(*) FROM admin").fetchone()[0] > 0

    # ---- statistics ----

    def compte(self, route: str) -> None:
        jour = time.strftime("%Y-%m-%d", time.gmtime())
        with self._verrou, self._db:
            self._db.execute("""INSERT INTO stats VALUES (?, ?, 1)
                                ON CONFLICT(jour, route) DO UPDATE SET n = n + 1""", (jour, route))

    def statistiques(self, jours: int = 7) -> list[sqlite3.Row]:
        with self._verrou:
            return self._db.execute("""SELECT jour, route, n FROM stats
                                       WHERE jour >= date('now', ?) ORDER BY jour DESC, n DESC""",
                                    (f"-{jours - 1} day",)).fetchall()
