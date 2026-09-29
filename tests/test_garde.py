# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The guard: robots banned, the local network never, SatMe recognized,
and the connections page shows who comes."""

import json
import re

import pytest
from werkzeug.security import generate_password_hash

from satme_gp import fetch
from satme_gp.app import cree_app
from satme_gp.garde import Garde, version_satme
from satme_gp.pays import drapeau
from satme_gp.store import Store
from test_serveur import Faux, csrf, el, url

DEHORS = {"REMOTE_ADDR": "203.0.113.9"}
SATME = {"User-Agent": "SatMe/20.74 (Android 14)"}


class PaysFaux:
    def de(self, ip):
        return ("LAN", "réseau local") if ip.startswith(("127.", "192.168.")) else ("FR", "France")

    def a_jour(self):
        return True


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


@pytest.fixture
def client(store):
    f = Faux()
    f.reponses[url(store, "amateur")] = (200, {}, json.dumps([el(1)]).encode())
    fetch.recupere(store, store.source("amateur"), f, maintenant=1000)
    store.pose_admin("f4ioz", generate_password_hash("un-bon-mot-de-passe"))
    app = cree_app(store, fetch.Amont(store, Faux()), secret="t", garde=Garde(store, PaysFaux()))
    app.config["TESTING"] = True
    c = app.test_client()
    c.garde = app.extensions["garde"]
    return c


def test_version_satme_reconnue():
    assert version_satme("SatMe/20.74 (Android 14)") == "20.74"
    assert version_satme("SatCombo/1.0 (F4IOZ)") == "≤ 20.72"
    assert version_satme("curl/8.5") is None and version_satme("") is None
    assert drapeau("FR") == "🇫🇷"


def test_sonde_bannie_aussitot(client):
    assert client.get("/.env", environ_base=DEHORS).status_code == 404
    assert client.get("/gp/amateur.json", environ_base=DEHORS).status_code == 403
    assert "sonde" in client.garde.liste_bannis()[0][2]


def test_gp_php_n_est_pas_une_sonde(client):
    client.get("/gp.php?GROUP=inconnu&FORMAT=json", environ_base=DEHORS)
    client.get("/wordpress/index.php", environ_base={"REMOTE_ADDR": "203.0.113.10"})
    assert [b[0] for b in client.garde.liste_bannis()] == ["203.0.113.10"]


def test_trop_de_refus_bannit(client, store):
    store.pose_reglage("limite_requetes", "2")
    codes = [client.get("/gp/amateur.json", environ_base=DEHORS).status_code for _ in range(6)]
    assert codes[:2] == [200, 200] and 429 in codes and codes[-1] == 403


def test_reseau_local_jamais_banni_automatiquement(client, store):
    store.pose_reglage("limite_requetes", "1")
    for _ in range(6):
        client.get("/gp/amateur.json")          # 127.0.0.1, like a proxy without X-Forwarded-For
    client.get("/.env")
    assert client.garde.liste_bannis() == []


def test_ban_expire(store):
    g = Garde(store, PaysFaux())
    g.bannit("198.51.100.1", "essai", 3600, maintenant=1000)
    assert g.refuse("198.51.100.1", "", "/", maintenant=2000)
    assert g.refuse("198.51.100.1", "", "/", maintenant=5000) is None
    assert "198.51.100.1" not in store.bannis()


def test_ban_garde_au_redemarrage(store):
    Garde(store, PaysFaux()).bannit("198.51.100.2", "essai", 3600)
    assert Garde(store, PaysFaux()).refuse("198.51.100.2", "", "/")


def test_reserve_a_satme(client, store):
    store.pose_reglage("satme_seul", "1")
    assert client.get("/gp/amateur.json", environ_base=DEHORS).status_code == 403
    assert client.get("/gp/amateur.json", environ_base=DEHORS, headers=SATME).status_code == 200
    assert client.get("/", environ_base=DEHORS).status_code == 200  # the home page stays open


def connecte(client):
    j = csrf(client)
    client.post("/admin/connexion", data={"csrf": j, "nom": "f4ioz", "mot_de_passe": "un-bon-mot-de-passe"})


def test_page_des_connexions(client):
    client.get("/gp/amateur.json", environ_base=DEHORS, headers=SATME)
    client.get("/gp/amateur.json", environ_base={"REMOTE_ADDR": "198.51.100.3"},
               headers={"User-Agent": "python-requests/2.31"})
    connecte(client)
    page = client.get("/admin/connexions").get_data(as_text=True)
    assert "203.0.113.9" in page and "SatMe 20.74" in page
    assert "198.51.100.3" in page and "python-requests" in page
    assert "🇫🇷 France" in page


def test_page_fermee_sans_connexion(client):
    assert client.get("/admin/connexions").status_code == 302


def test_bannir_et_debannir_depuis_la_page(client):
    connecte(client)
    j = csrf(client, "/admin/connexions")
    client.post("/admin/bannir", data={"csrf": j, "ip": "198.51.100.4", "heures": "2"})
    assert client.get("/", environ_base={"REMOTE_ADDR": "198.51.100.4"}).status_code == 403
    client.post("/admin/debannir", data={"csrf": j, "ip": "198.51.100.4"})
    assert client.get("/", environ_base={"REMOTE_ADDR": "198.51.100.4"}).status_code == 200


def test_on_ne_se_bannit_pas_soi_meme(client):
    connecte(client)
    j = csrf(client, "/admin/connexions")
    client.post("/admin/bannir", data={"csrf": j, "ip": "127.0.0.1"})
    assert client.garde.liste_bannis() == []


def test_avertissement_proxy_sans_adresse_du_visiteur(store):
    store.pose_admin("f4ioz", generate_password_hash("un-bon-mot-de-passe"))
    app = cree_app(store, fetch.Amont(store, Faux()), secret="t", derriere_proxy=True,
                   garde=Garde(store, PaysFaux()))
    c = app.test_client()
    c.get("/", environ_base={"REMOTE_ADDR": "192.168.1.1"})
    connecte(c)
    assert "X-Forwarded-For" in c.get("/admin/connexions",
                                      environ_base={"REMOTE_ADDR": "192.168.1.1"}).get_data(as_text=True)


def test_connexions_oubliees_apres_la_duree(store):
    store.note_connexions([("2000-01-01", "203.0.113.1", "FR", 1, 0, "", "", 0, 0)])
    Garde(store, PaysFaux()).vide()
    assert store.connexions(30000) == []


def test_numeros_inconnus_ne_bannissent_pas(client, store):
    store.pose_reglage("catnr_amont", "0")
    for n in range(40):
        client.get(f"/gp/catnr/{90000 + n}.json", environ_base=DEHORS, headers=SATME)
    client.get("/gp/stations.json", environ_base=DEHORS, headers=SATME)   # group off: 404
    assert client.garde.liste_bannis() == []


def diagnostic(store, proxy, entetes, remote="192.168.1.2"):
    store.pose_admin("f4ioz", generate_password_hash("un-bon-mot-de-passe"))
    app = cree_app(store, fetch.Amont(store, Faux()), secret="t", derriere_proxy=proxy,
                   garde=Garde(store, PaysFaux()))
    c = app.test_client()
    env = {"REMOTE_ADDR": remote}
    j = re.search(r'name="csrf" value="([^"]+)"',
                  c.get("/admin/connexion", environ_base=env, headers=entetes).get_data(as_text=True)).group(1)
    c.post("/admin/connexion", data={"csrf": j, "nom": "f4ioz", "mot_de_passe": "un-bon-mot-de-passe"},
           environ_base=env, headers=entetes)
    return c.get("/admin/connexions", environ_base=env, headers=entetes).get_data(as_text=True)


def test_diagnostic_proxy(store):
    assert "SATME_GP_PROXY=1" in diagnostic(store, False, {"X-Forwarded-For": "88.1.2.3"})
    assert "envoie pas X-Forwarded-For" in diagnostic(store, True, {})
    assert "network_mode: host" in diagnostic(store, True, {"X-Forwarded-For": "172.18.0.1"})
    assert "arrive bien (88.1.2.3)" in diagnostic(store, True, {"X-Forwarded-For": "88.1.2.3",
                                                               "X-Forwarded-Proto": "https"})


# ------------------------------------------------ through a real Waitress

def servi_par_waitress(store, proxy, entetes):
    """One request through Waitress itself (the test client skips it): the address the guard counted."""
    import threading
    import urllib.request
    from waitress import create_server
    from satme_gp.__main__ import options_waitress
    garde = Garde(store, PaysFaux())
    app = cree_app(store, fetch.Amont(store, Faux()), secret="t", derriere_proxy=proxy, garde=garde)
    o = options_waitress(proxy)
    o.pop("threads")
    srv = create_server(app, host="127.0.0.1", port=0, threads=1, **{k: v for k, v in o.items() if k != "ident"})
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    try:
        port = srv.effective_port
        req = urllib.request.Request(f"http://127.0.0.1:{port}/", headers=entetes)
        urllib.request.urlopen(req, timeout=5).read()
    finally:
        srv.close()
    garde.vide()
    return [r["ip"] for r in store.connexions(1)]


def test_derriere_un_proxy_l_adresse_du_visiteur_passe_waitress(store):
    assert servi_par_waitress(store, True, {"X-Forwarded-For": "88.1.2.3",
                                            "X-Forwarded-Proto": "https"}) == ["88.1.2.3"]


def test_sans_proxy_un_faux_x_forwarded_for_est_ignore(store):
    assert servi_par_waitress(store, False, {"X-Forwarded-For": "88.1.2.3"}) == ["127.0.0.1"]
