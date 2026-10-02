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


# ---------------------------------------------------------------- TLE

ISS = ("ISS (ZARYA)",
       "1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927",
       "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537")


def test_tle_converti_en_omm():
    r = omm.tle_vers_omm(*ISS)
    assert r["NORAD_CAT_ID"] == 25544 and r["OBJECT_ID"] == "1998-067A"
    assert r["EPOCH"].startswith("2008-09-20T12:25:40")
    assert r["MEAN_MOTION"] == 15.72125391 and r["ECCENTRICITY"] == 0.0006703
    assert r["INCLINATION"] == 51.6416 and r["RA_OF_ASC_NODE"] == 247.4627
    assert r["ARG_OF_PERICENTER"] == 130.536 and r["MEAN_ANOMALY"] == 325.0288
    assert r["REV_AT_EPOCH"] == 56353 and r["ELEMENT_SET_NO"] == 292
    assert r["BSTAR"] == pytest.approx(-0.11606e-4) and r["MEAN_MOTION_DOT"] == -0.00002182
    assert omm.valide(r)


def test_tle_abime_refuse():
    faux = ISS[1][:-1] + "0"  # checksum
    with pytest.raises(omm.TleInvalide):
        omm.tle_vers_omm(ISS[0], faux, ISS[2])
    autre = ISS[2].replace("25544", "25545", 1)
    autre = autre[:-1] + str((int(autre[-1]) + 1) % 10)
    with pytest.raises(omm.TleInvalide):
        omm.tle_vers_omm(ISS[0], ISS[1], autre)


def test_alpha5():
    assert omm._alpha5("A0001") == 100001 and omm._alpha5("Z9999") == 339999
    assert omm._alpha5("J0000") == 180000  # I is skipped


def test_json_satnogs_reconnu():
    satnogs = [{"tle0": "0 " + ISS[0], "tle1": ISS[1], "tle2": ISS[2], "norad_cat_id": 25544}]
    r = omm.lit_source(json.dumps(satnogs).encode())
    assert r[0]["OBJECT_NAME"] == "ISS (ZARYA)" and r[0]["NORAD_CAT_ID"] == 25544


def test_tle_texte_avec_et_sans_nom():
    r = omm.lit_source(("\n".join(ISS) + "\n").encode())
    assert r[0]["OBJECT_NAME"] == "ISS (ZARYA)"
    r = omm.lit_source(("\r\n".join(ISS[1:]) + "\r\n").encode())
    assert r[0]["OBJECT_NAME"] == "25544"


def test_omm_toujours_lu_et_html_refuse():
    assert len(omm.lit_source(json.dumps([el(1)]).encode())) == 1
    for mauvais in (b"<html>Error</html>", b"[]", b"No GP data found"):
        with pytest.raises(omm.FichierRefuse):
            omm.lit_source(mauvais)


def test_source_satnogs_recuperee(store):
    f = Faux()
    satnogs = [{"tle0": ISS[0], "tle1": ISS[1], "tle2": ISS[2]}]
    f.reponses[url(store, "satnogs")] = (200, {}, json.dumps(satnogs).encode())
    assert "1 éléments" in fetch.recupere(store, store.source("satnogs"), f, maintenant=1000)
    assert store.catalogue()[25544]["OBJECT_NAME"] == "ISS (ZARYA)"


def test_supgp_suit_la_regle_celestrak(store):
    assert fetch.intervalle_min(store, store.source("supgp_iss")) >= 120


def test_nouvelle_source_par_defaut_ajoutee_une_fois(tmp_path):
    s = Store(tmp_path)
    s.supprime_source("satnogs")
    assert Store(tmp_path).source("satnogs") is None  # deleted stays deleted


def test_base_1_0_0_recoit_les_nouvelles_sources(tmp_path):
    s = Store(tmp_path)
    for sid in ("satnogs", "supgp_iss"):
        s.supprime_source(sid)
    s._db.execute("DELETE FROM reglages WHERE cle = 'sources_proposees'")
    s._db.commit()
    s.supprime_source("weather")  # removed by the administrator under 1.0.0
    s2 = Store(tmp_path)
    assert s2.source("satnogs") and s2.source("supgp_iss") and s2.source("weather") is None


def test_base_1_1_0_recoit_le_bulletin_tle_d_amsat(tmp_path):
    # A server installed with the eight sources of 1.1.0, as gp.f4ioz.fr.
    s = Store(tmp_path)
    s.supprime_source("amsat_tle")
    s._db.execute("UPDATE reglages SET valeur = ? WHERE cle = 'sources_proposees'",
                  ("amsat,amateur,stations,visual,weather,cubesat,satnogs,supgp_iss",))
    s._db.commit()
    assert Store(tmp_path).source("amsat_tle").url.endswith("nasabare.txt")


def test_le_bulletin_tle_d_amsat_se_lit(tmp_path):
    texte = ("ISS\n1 25544U 98067A   26275.01380287  .00003738  00000-0  76743-4 0  9993\n"
             "2 25544  51.6312 131.4121 0006946 211.9293 148.1275 15.48707684588318\n")
    r = omm.lit_source(texte.encode(), part_minimale=0.5)
    assert r[0]["NORAD_CAT_ID"] == 25544 and r[0]["EPOCH"].startswith("2026-10-02")


# ------------------------------------------------- CelesTrak politeness

def test_rafraichir_ne_force_pas_celestrak(client, store):
    connecte(client)
    store.note_essai("amsat", 1000)
    j = csrf(client, "/admin")
    client.post("/admin/rafraichir", data={"csrf": j})
    assert store.source("amsat").dernier_essai == 0
    assert store.source("amateur").dernier_essai == 1000  # fetched by the fixture, untouched


def test_403_met_celestrak_en_pause_groupes_et_numeros(store):
    f = Faux()
    f.reponses[url(store, "amateur")] = (403, {}, b"")
    lignes = fetch.tour(store, f, maintenant=1000, ecart_s=0)
    celestrak = [u for u, _ in f.appels if fetch.est_celestrak(u)]
    assert celestrak == [url(store, "amateur")]  # the others of the round are not asked
    assert any("HTTP 403" in x for x in lignes)
    assert not fetch.a_faire(store, store.source("stations"), 1000 + 5 * 3600)
    assert fetch.a_faire(store, store.source("stations"), 1000 + 6 * 3600)
    avant = len(f.appels)
    amont = fetch.Amont(store, f)
    assert amont.cherche(12345, maintenant=2000) is None
    assert len(f.appels) == avant  # no CATNR asked upstream


def test_retry_after_plus_long_respecte(store):
    f = Faux()
    f.reponses[url(store, "amateur")] = (429, {"retry-after": "86400"}, b"")
    fetch.recupere(store, store.source("amateur"), f, maintenant=1000)
    assert fetch.celestrak_en_pause(store, 1000 + 23 * 3600)


def test_catnr_403_met_en_pause(store):
    f = Faux()
    amont = fetch.Amont(store, f)
    f.reponses[store.reglage("catnr_url").replace("{n}", "7")] = (403, {}, b"")
    amont.cherche(7, maintenant=1000)
    amont.cherche(8, maintenant=1001)
    assert len(f.appels) == 1


def test_numero_hors_plage_pas_demande(store):
    f = Faux()
    amont = fetch.Amont(store, f)
    assert amont.cherche(0, maintenant=1000) is None
    assert amont.cherche(400_000, maintenant=1000) is None
    assert amont.cherche(90_000, maintenant=1000, plus_grand_connu=60_000) is None
    assert f.appels == []


def test_cache_des_numeros_jamais_sous_deux_heures_pour_celestrak(store):
    store.pose_reglage("catnr_cache_min", "10")
    assert fetch.Amont(store).cache_min() == 120
    store.pose_reglage("catnr_url", "https://exemple.org/gp?n={n}")
    assert fetch.Amont(store).cache_min() == 10


def test_ancien_plafond_60_abaisse_a_20(tmp_path):
    s = Store(tmp_path)
    s.pose_reglage("catnr_max_heure", "60")
    assert Store(tmp_path).reglage("catnr_max_heure") == "20"


def test_requetes_celestrak_espacees(store, monkeypatch):
    attentes = []
    monkeypatch.setattr(fetch.time, "sleep", attentes.append)
    f = Faux()
    fetch.tour(store, f, maintenant=1000, ecart_s=3)
    n = sum(1 for u, _ in f.appels if fetch.est_celestrak(u))
    assert n > 1 and attentes == [3] * (n - 1)


# ------------------------------------------------ sources at installation

def test_liste_des_sources_lue_par_le_script_d_installation():
    import subprocess
    from pathlib import Path
    racine = Path(__file__).parent.parent
    sortie = subprocess.run(["bash", "-c", '. deploy/sources.sh; sources_defaut .'], cwd=racine,
                            capture_output=True, text=True, check=True).stdout.split("\n")
    from satme_gp.store import SOURCES_DEFAUT
    assert [ligne.split("|")[0] for ligne in sortie if ligne][:-1] == [sid for sid, _, _ in SOURCES_DEFAUT]
    assert sortie[len(SOURCES_DEFAUT)].startswith("numero|")


def test_commande_sources(tmp_path, monkeypatch, capsys):
    from satme_gp.__main__ import main
    monkeypatch.setenv("SATME_GP_DATA", str(tmp_path))
    assert main(["sources", "--actives", "amsat,satnogs"]) == 0
    s = Store(tmp_path)
    assert [x.id for x in s.sources() if x.actif] == ["amsat", "satnogs"]
    assert s.reglage("catnr_amont") == "0"
    assert main(["sources", "--actives", "amsat,inconnue"]) == 1
    assert main(["sources", "--actives", "amsat,numero"]) == 0
    assert Store(tmp_path).reglage("catnr_amont") == "1"


def test_index_en_https_derriere_un_proxy_qui_ne_le_dit_pas(store):
    app = cree_app(store, fetch.Amont(store, Faux()), secret="t", derriere_proxy=True, https=True)
    r = app.test_client().get("/gp/index.json", base_url="http://gp.exemple.org")
    assert all(g["url"].startswith("https://gp.exemple.org/") for g in r.get_json()["groupes"])
