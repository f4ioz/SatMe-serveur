# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Mullvad option: the admin only ever writes a checked one-line request,
and the root helper refuses anything else."""

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from satme_gp import fetch, vpn
from satme_gp.app import cree_app
from satme_gp.store import Store
from test_serveur import Faux, csrf, connecte

RELAIS = {
    "locations": {"fr-par": {"country": "France", "city": "Paris"},
                  "se-got": {"country": "Sweden", "city": "Gothenburg"}},
    "wireguard": {"relays": [
        {"hostname": "fr-par-wg-001", "location": "fr-par", "active": True, "owned": True,
         "provider": "M247", "ipv4_addr_in": "193.32.126.66",
         "public_key": "ov323GyDOEHLT0sNRUUPYiE3BkvFDjpmi1a4fzv49hE="},
        {"hostname": "fr-par-wg-002", "location": "fr-par", "active": False, "owned": False,
         "provider": "M247", "ipv4_addr_in": "193.32.126.67",
         "public_key": "R5LUBgM/1UjeAR4lt+L/yA30Gee6/VqVZ9eAB3ZTajs="},
        {"hostname": "se-got-wg-001", "location": "se-got", "active": True, "owned": True,
         "provider": "31173", "ipv4_addr_in": "185.213.154.66",
         "public_key": "5JMPeO7gXIbR5CnUa/NPNK4L5GqUnreF0/Bozai4pl4="}]}}


def helper():
    chemin = Path(__file__).parent.parent / "deploy" / "satme-gp-vpn"
    chargeur = importlib.machinery.SourceFileLoader("satme_gp_vpn", str(chemin))
    spec = importlib.util.spec_from_loader("satme_gp_vpn", chargeur)
    m = importlib.util.module_from_spec(spec)
    chargeur.exec_module(m)
    return m


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    monkeypatch.setattr(vpn, "ETAT", tmp_path / "etat.json")
    (tmp_path / "etat.json").write_text(json.dumps({"helper": True, "cle": True}))
    f = Faux()
    f.reponses[vpn.RELAIS_URL] = (200, {}, json.dumps(RELAIS).encode())
    f.reponses[vpn.VERIF_URL] = (200, {}, json.dumps(
        {"ip": "193.32.126.70", "country": "France", "city": "Paris", "mullvad_exit_ip": True,
         "mullvad_exit_ip_hostname": "fr-par-wg-001", "blacklisted": {"blacklisted": False}}).encode())
    store.pose_admin("f4ioz", generate_password_hash("un-bon-mot-de-passe"))
    app = cree_app(store, fetch.Amont(store, f), secret="test")
    app.config["TESTING"] = True
    c = app.test_client()
    c.store, c.faux = store, f
    connecte(c)
    return c


def test_sorties_en_service_seulement_et_groupees(client):
    page = client.get("/admin").get_data(as_text=True)
    assert "fr-par-wg-001" in page and "se-got-wg-001" in page
    assert "fr-par-wg-002" not in page  # out of service
    assert '<optgroup label="France">' in page and "2 sorties en service" in page


def test_liste_gardee_un_jour(client):
    client.get("/admin")
    client.get("/admin")
    assert [u for u, _ in client.faux.appels].count(vpn.RELAIS_URL) == 1


def test_demande_ecrite_pour_une_sortie_connue(client, monkeypatch):
    monkeypatch.setattr(vpn, "attend", lambda jeton, delai_s=20: {"jeton": jeton, "actif": True,
                                                                  "sortie": "se-got-wg-001"})
    client.get("/admin")
    j = csrf(client, "/admin")
    client.post("/admin/vpn", data={"csrf": j, "action": "sortie", "sortie": "se-got-wg-001"})
    ligne = (client.store.dossier / "vpn" / "demande").read_text().split()
    assert ligne[:2] == ["sortie", "se-got-wg-001"] and len(ligne) == 3


def test_sortie_inconnue_ou_hors_service_refusee(client):
    j = csrf(client, "/admin")
    for hote in ("fr-par-wg-002", "x; rm -rf /", "../../etc/passwd"):
        client.post("/admin/vpn", data={"csrf": j, "action": "sortie", "sortie": hote})
    assert not (client.store.dossier / "vpn" / "demande").exists()


def test_sans_jeton_csrf_rien(client):
    assert client.post("/admin/vpn", data={"action": "arret"}).status_code == 400
    assert not (client.store.dossier / "vpn" / "demande").exists()


def test_tester_affiche_la_sortie(client):
    j = csrf(client, "/admin")
    client.post("/admin/vpn/test", data={"csrf": j})
    page = client.get("/admin").get_data(as_text=True)
    assert "193.32.126.70" in page and "Mullvad fr-par-wg-001" in page


def test_tester_quand_rien_ne_sort(client):
    client.faux.reponses[vpn.VERIF_URL] = OSError("unreachable")
    j = csrf(client, "/admin")
    client.post("/admin/vpn/test", data={"csrf": j})
    assert "aucune sortie" in client.get("/admin").get_data(as_text=True)


def test_carte_absente_sans_le_programme_root(client, tmp_path):
    (tmp_path / "etat.json").unlink()
    assert "VPN Mullvad" not in client.get("/admin").get_data(as_text=True)


# --------------------------------------------------------- root helper

@pytest.fixture
def h(tmp_path, monkeypatch):
    m = helper()
    monkeypatch.setattr(m, "DEMANDE", str(tmp_path / "demande"))
    return m


def test_demande_lue_strictement(h, tmp_path):
    d = tmp_path / "demande"
    d.write_text("sortie fr-par-wg-001 abc_DEF-1\n")
    assert h.lit_demande() == ("sortie", "fr-par-wg-001", "abc_DEF-1")
    d.write_text("arret j1\n")
    assert h.lit_demande() == ("arret", "", "j1")
    for mauvais in ("sortie fr-par-wg-001", "sortie FR;reboot j", "sortie ../x j", "sortie a j k",
                    "autre chose j", "sortie " + "a" * 41 + " j", "sortie fr-par-wg-001 j$"):
        d.write_text(mauvais)
        with pytest.raises(h.Refus):
            h.lit_demande()


def test_demande_lien_symbolique_refusee(h, tmp_path):
    cible = tmp_path / "secret"
    cible.write_text("sortie fr-par-wg-001 j\n")
    os.symlink(cible, tmp_path / "demande")
    with pytest.raises(h.Refus):
        h.lit_demande()


def test_sortie_verifiee_dans_la_liste_mullvad(h, tmp_path, monkeypatch):
    monkeypatch.setattr(h, "relais", lambda: [
        {"hostname": "fr-par-wg-001", "public_key": RELAIS["wireguard"]["relays"][0]["public_key"],
         "ipv4": "193.32.126.66", "actif": True, "pays": "France", "ville": "Paris"},
        {"hostname": "fr-par-wg-002", "public_key": "x", "ipv4": "1.2.3.4", "actif": False,
         "pays": "France", "ville": "Paris"},
        {"hostname": "zz-bad-wg-001", "public_key": "pas une clé", "ipv4": "1.2.3.4", "actif": True,
         "pays": "", "ville": ""}])
    assert h.trouve("fr-par-wg-001")["ipv4"] == "193.32.126.66"
    for hote in ("fr-par-wg-002", "zz-bad-wg-001", "xx-inconnu-001"):
        with pytest.raises(h.Refus):
            h.trouve(hote)


def test_cle_importee_depuis_un_fichier_mullvad(h, tmp_path, monkeypatch):
    monkeypatch.setattr(h, "CONFIG", str(tmp_path / "etc"))
    monkeypatch.setattr(h, "CLE", str(tmp_path / "etc" / "mullvad.key"))
    monkeypatch.setattr(h, "ADRESSES", str(tmp_path / "etc" / "mullvad.adresses"))
    monkeypatch.setattr(h, "ETAT_DIR", str(tmp_path / "etat"))
    monkeypatch.setattr(h, "ETAT", str(tmp_path / "etat" / "etat.json"))
    monkeypatch.setattr(h.shutil, "which", lambda x: "/usr/bin/wg")
    conf = tmp_path / "fr-par-wg-001.conf"
    conf.write_text("[Interface]\n# Device: Happy Cat\n"
                    "PrivateKey = kHcuKPzZ4XVNLJ3GvUq3O1dC0ZfA2y2Z1Vt6y1fQ1Xo=\n"
                    "Address = 10.66.12.34/32,fc00:bbbb:bbbb:bb01::3:c21/128\nDNS = 10.64.0.1\n\n"
                    "[Peer]\nPublicKey = ov323GyDOEHLT0sNRUUPYiE3BkvFDjpmi1a4fzv49hE=\n"
                    "AllowedIPs = 0.0.0.0/0,::0/0\nEndpoint = 193.32.126.66:51820\n")
    h.importe_cle(str(conf))
    cle = tmp_path / "etc" / "mullvad.key"
    assert oct(cle.stat().st_mode & 0o777) == "0o600"
    assert (tmp_path / "etc" / "mullvad.adresses").read_text().split() == \
        ["10.66.12.34/32", "fc00:bbbb:bbbb:bb01::3:c21/128"]
    assert json.loads((tmp_path / "etat" / "etat.json").read_text())["cle"] is True
    conf.write_text("[Interface]\nPrivateKey = court\nAddress = 10.0.0.1/32\n")
    with pytest.raises(h.Refus):
        h.importe_cle(str(conf))


def test_sortie_par_ville_ou_pays(h, monkeypatch):
    cle = RELAIS["wireguard"]["relays"][0]["public_key"]
    monkeypatch.setattr(h, "relais", lambda: [
        {"hostname": "fr-par-wg-001", "lieu": "fr-par", "public_key": cle, "ipv4": "1.2.3.4",
         "actif": True, "pays": "France", "ville": "Paris"},
        {"hostname": "fr-mrs-wg-001", "lieu": "fr-mrs", "public_key": cle, "ipv4": "1.2.3.5",
         "actif": False, "pays": "France", "ville": "Marseille"},
        {"hostname": "se-got-wg-001", "lieu": "se-got", "public_key": cle, "ipv4": "1.2.3.6",
         "actif": True, "pays": "Sweden", "ville": "Gothenburg"}])
    assert h.trouve("fr")["hostname"] == "fr-par-wg-001"      # the one in service
    assert h.trouve("fr-par")["hostname"] == "fr-par-wg-001"
    assert h.trouve("se")["hostname"] == "se-got-wg-001"
    for x in ("fr-mrs", "f", "de"):
        with pytest.raises(h.Refus):
            h.trouve(x)


# ------------------------------------------- VPN added from the admin page

FICHIER = ("[Interface]\nPrivateKey = kHcuKPzZ4XVNLJ3GvUq3O1dC0ZfA2y2Z1Vt6y1fQ1Xo=\n"
           "Address = 10.66.12.34/32,fc00:bbbb:bbbb:bb01::3:c21/128\nDNS = 10.64.0.1\n")


def sans_cle(client, tmp_path):
    (tmp_path / "etat.json").write_text(json.dumps({"helper": True, "cle": False}))


def test_formulaire_d_envoi_sans_cle_avec_les_pays(client, tmp_path):
    sans_cle(client, tmp_path)
    page = client.get("/admin").get_data(as_text=True)
    assert 'enctype="multipart/form-data"' in page
    assert '<option value="fr" selected>France</option>' in page and ">Sweden<" in page


def test_fichier_envoye_puis_cle_et_sortie_demandees(client, tmp_path, monkeypatch):
    import io
    sans_cle(client, tmp_path)
    monkeypatch.setattr(vpn, "attend", lambda jeton, delai_s=20: {"jeton": jeton, "actif": True,
                                                                  "sortie": "fr-par-wg-001"})
    client.get("/admin")
    j = csrf(client, "/admin")
    client.post("/admin/vpn/cle", data={"csrf": j, "pays": "fr",
                                        "fichier": (io.BytesIO(FICHIER.encode()), "fr-par-wg-001.conf")},
                content_type="multipart/form-data")
    d = client.store.dossier / "vpn"
    assert (d / "import.conf").read_text() == FICHIER
    assert oct((d / "import.conf").stat().st_mode & 0o777) == "0o600"
    assert (d / "demande").read_text().split()[:2] == ["cle", "fr"]
    assert "Mullvad fr-par-wg-001" in client.get("/admin").get_data(as_text=True)  # tested at once


def test_contenu_colle_accepte_et_mauvais_fichier_refuse(client, tmp_path, monkeypatch):
    sans_cle(client, tmp_path)
    monkeypatch.setattr(vpn, "attend", lambda jeton, delai_s=20: {"jeton": jeton})
    j = csrf(client, "/admin")
    d = client.store.dossier / "vpn"
    for mauvais, pays in (("[Interface]\nAddress = 10.0.0.1/32\n", "fr"), ("<html>", "fr"),
                          (FICHIER, "zz"), (FICHIER + "#" * 5000, "fr")):
        client.post("/admin/vpn/cle", data={"csrf": j, "pays": pays, "texte": mauvais})
        assert not (d / "import.conf").exists()
    client.post("/admin/vpn/cle", data={"csrf": j, "pays": "se", "texte": FICHIER})
    assert (d / "demande").read_text().split()[:2] == ["cle", "se"]


def test_retirer_le_vpn(client, monkeypatch):
    monkeypatch.setattr(vpn, "attend", lambda jeton, delai_s=20: {"jeton": jeton})
    j = csrf(client, "/admin")
    client.post("/admin/vpn/oublie", data={"csrf": j})
    assert (client.store.dossier / "vpn" / "demande").read_text().split()[0] == "oublie"


def test_programme_root_lit_cle_et_oublie(h, tmp_path, monkeypatch):
    d = tmp_path / "demande"
    d.write_text("cle fr-par j1\n")
    assert h.lit_demande() == ("cle", "fr-par", "j1")
    d.write_text("oublie j2\n")
    assert h.lit_demande() == ("oublie", "", "j2")
    d.write_text("cle ../x j\n")
    with pytest.raises(h.Refus):
        h.lit_demande()


def test_fichier_importe_lu_puis_efface_lien_refuse(h, tmp_path, monkeypatch):
    monkeypatch.setattr(h, "IMPORT", str(tmp_path / "import.conf"))
    (tmp_path / "import.conf").write_text(FICHIER)
    assert "PrivateKey" in h.lit_import()
    assert not (tmp_path / "import.conf").exists()
    secret = tmp_path / "secret"
    secret.write_text(FICHIER)
    os.symlink(secret, tmp_path / "import.conf")
    with pytest.raises(h.Refus):
        h.lit_import()
    assert secret.exists()  # the link is removed, never its target
