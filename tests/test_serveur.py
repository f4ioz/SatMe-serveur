# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the server promises: never serve a broken file, spare the sources,
answer like CelesTrak, and keep the admin pages shut."""

import gzip
import json
import re

import pytest
from werkzeug.security import generate_password_hash

from satme_gp import fetch, omm
from satme_gp.app import cree_app
from satme_gp.store import Store


def el(n, epoch="2026-09-28T12:00:00.000000", nom="SAT"):
    return {"OBJECT_NAME": nom, "NORAD_CAT_ID": n, "EPOCH": epoch, "MEAN_MOTION": 14.9,
            "ECCENTRICITY": 0.001, "INCLINATION": 97.5, "RA_OF_ASC_NODE": 10.0,
            "ARG_OF_PERICENTER": 20.0, "MEAN_ANOMALY": 30.0, "BSTAR": 0.0001}


class Faux:
    """A source answering what the test says, and counting requests."""

    def __init__(self):
        self.reponses = {}
        self.appels = []

    def __call__(self, url, entetes):
        self.appels.append((url, dict(entetes)))
        r = self.reponses.get(url)
        if isinstance(r, Exception):
            raise r
        return r or (404, {}, b"")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def url(store, sid):
    return store.source(sid).url


# ------------------------------------------------------------ records

def test_un_fichier_valide_est_garde_et_un_invalide_refuse():
    assert len(omm.lit(json.dumps([el(1), el(2)]).encode())) == 2
    for mauvais in (b"<html>Error</html>", b"[]", b"{}", json.dumps([el(1), {"x": 1}]).encode()):
        with pytest.raises(omm.FichierRefuse):
            omm.lit(mauvais)


def test_le_plus_recent_gagne_entre_sources():
    vieux, neuf = el(7, "2026-09-20T00:00:00"), el(7, "2026-09-28T00:00:00", "NEUF")
    assert omm.plus_recents([[neuf], [vieux]])[7]["OBJECT_NAME"] == "NEUF"


# ------------------------------------------------------------- fetch

def test_un_fichier_abime_ne_remplace_pas_la_bonne_copie(store):
    f = Faux()
    f.reponses[url(store, "amsat")] = (200, {"etag": '"a"'}, json.dumps([el(1), el(2)]).encode())
    assert "2 éléments" in fetch.recupere(store, store.source("amsat"), f, maintenant=1000)
    f.reponses[url(store, "amsat")] = (200, {}, b"<html>maintenance</html>")
    assert "refusé" in fetch.recupere(store, store.source("amsat"), f, maintenant=2000)
    assert len(store.lit_groupe("amsat")) == 2
    assert store.source("amsat").erreur.startswith("refusé")


def test_etag_renvoye_et_304_garde_le_fichier(store):
    f = Faux()
    f.reponses[url(store, "amsat")] = (200, {"etag": '"v1"'}, json.dumps([el(1)]).encode())
    fetch.recupere(store, store.source("amsat"), f, maintenant=1000)
    f.reponses[url(store, "amsat")] = (304, {}, b"")
    assert "inchangé" in fetch.recupere(store, store.source("amsat"), f, maintenant=9000)
    assert f.appels[-1][1]["If-None-Match"] == '"v1"'
    assert store.lit_groupe("amsat") and store.source("amsat").dernier_ok == 9000


def test_celestrak_jamais_deux_fois_en_deux_heures_meme_apres_echec(store):
    s = store.source("amateur")
    store.note_essai("amateur", 1000, "HTTP 403")
    s = store.source("amateur")
    assert not fetch.a_faire(store, s, 1000 + 119 * 60)
    assert fetch.a_faire(store, s, 1000 + 120 * 60)
    # A short interval set by hand is raised to two hours for CelesTrak.
    store.pose_source("amateur", s.nom, s.url, True, 10)
    assert fetch.intervalle_min(store, store.source("amateur")) == 120


def test_une_autre_source_en_echec_reessaie_plus_tot(store):
    store.note_essai("amsat", 1000, "réseau : URLError")
    s = store.source("amsat")
    assert fetch.a_faire(store, s, 1000 + 15 * 60)


def test_recherche_par_numero_en_cache_et_plafonnee(store):
    f = Faux()
    store.pose_reglage("catnr_max_heure", "2")
    amont = fetch.Amont(store, f)
    u = lambda n: store.reglage("catnr_url").replace("{n}", str(n))
    f.reponses[u(99999)] = (200, {}, json.dumps([el(99999)]).encode())
    assert amont.cherche(99999, maintenant=1000)[0]["NORAD_CAT_ID"] == 99999
    assert amont.cherche(99999, maintenant=1100)  # from the cache
    assert len(f.appels) == 1
    f.reponses[u(1)] = (200, {}, b"No GP data found")
    assert amont.cherche(1, maintenant=1200) is None
    # Quota spent (2 per hour): a third number is not asked upstream.
    assert amont.cherche(2, maintenant=1300) is None
    assert len(f.appels) == 2


def test_panne_reseau_non_mise_en_cache(store):
    f = Faux()
    amont = fetch.Amont(store, f)
    u = store.reglage("catnr_url").replace("{n}", "5")
    f.reponses[u] = OSError("réseau")
    assert amont.cherche(5, maintenant=1000) is None
    f.reponses[u] = (200, {}, json.dumps([el(5)]).encode())
    assert amont.cherche(5, maintenant=1001)


# --------------------------------------------------------------- web

@pytest.fixture
def client(store):
    f = Faux()
    f.reponses[url(store, "amateur")] = (200, {}, json.dumps([el(n) for n in range(1, 60)]).encode())
    fetch.recupere(store, store.source("amateur"), f, maintenant=1000)
    store.pose_admin("f4ioz", generate_password_hash("un-bon-mot-de-passe"))
    app = cree_app(store, fetch.Amont(store, Faux()), secret="test")
    app.config["TESTING"] = True
    return app.test_client()


def test_memes_adresses_que_celestrak(client):
    a = client.get("/gp.php?GROUP=amateur&FORMAT=json")
    b = client.get("/NORAD/elements/gp.php?GROUP=amateur&FORMAT=json")
    c = client.get("/gp/amateur.json")
    assert a.status_code == b.status_code == c.status_code == 200
    assert a.get_json() == c.get_json() and len(a.get_json()) == 59
    assert client.get("/gp.php?CATNR=12&FORMAT=json").get_json()[0]["NORAD_CAT_ID"] == 12
    assert client.get("/gp.php?GROUP=amateur&FORMAT=tle").status_code == 400
    assert client.get("/gp.php?CATNR=424242&FORMAT=json").status_code == 404


def test_304_et_gzip(client):
    r = client.get("/gp/amateur.json", headers={"Accept-Encoding": "gzip"})
    assert r.headers["Content-Encoding"] == "gzip"
    assert len(json.loads(gzip.decompress(r.data))) == 59
    etag = r.headers["ETag"]
    assert client.get("/gp/amateur.json", headers={"If-None-Match": etag}).status_code == 304


def test_un_groupe_pas_encore_recupere_dit_503_pas_une_liste_vide(client):
    r = client.get("/gp/weather.json")
    assert r.status_code == 503


def test_limite_de_requetes(client, store):
    store.pose_reglage("limite_requetes", "3")
    codes = [client.get("/gp/amateur.json").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


# ------------------------------------------------------------- admin

def csrf(client, chemin="/admin/connexion"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(chemin).get_data(as_text=True)).group(1)


def connecte(client):
    j = csrf(client)
    client.post("/admin/connexion", data={"csrf": j, "nom": "f4ioz", "mot_de_passe": "un-bon-mot-de-passe"})


def test_admin_ferme_sans_connexion(client):
    assert client.get("/admin").status_code == 302
    assert client.post("/admin/reglages", data={}).status_code == 302


def test_connexion_puis_reglage(client, store):
    connecte(client)
    assert client.get("/admin").status_code == 200
    j = csrf(client, "/admin")
    client.post("/admin/reglages", data={"csrf": j, "limite_requetes": "77", "catnr_amont": "0"})
    assert store.reglage("limite_requetes") == "77" and store.reglage("catnr_amont") == "0"


def test_formulaire_sans_jeton_refuse(client, store):
    connecte(client)
    assert client.post("/admin/reglages", data={"limite_requetes": "5"}).status_code == 400
    assert store.reglage("limite_requetes") != "5"


def test_trop_d_essais_de_mot_de_passe(client):
    j = csrf(client)
    codes = [client.post("/admin/connexion", data={"csrf": j, "nom": "f4ioz", "mot_de_passe": "faux"}).status_code
             for _ in range(6)]
    assert codes[-1] == 429


def test_source_en_http_refusee(client, store):
    connecte(client)
    j = csrf(client, "/admin")
    client.post("/admin/source", data={"csrf": j, "id": "x", "url": "http://exemple.org/gp.json", "actif": "1"})
    assert store.source("x") is None


def test_activer_desactiver_une_source(client, store):
    connecte(client)
    j = csrf(client, "/admin")
    client.post("/admin/source/amateur/bascule", data={"csrf": j})
    assert not store.source("amateur").actif
    assert client.get("/gp/amateur.json").status_code == 404
    client.post("/admin/source/amateur/bascule", data={"csrf": j})
    assert store.source("amateur").actif
