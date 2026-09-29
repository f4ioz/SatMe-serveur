# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web side: public read-only API, and the admin pages.

**Same URLs as CelesTrak** (`gp.php?GROUP=amateur&FORMAT=json`,
`gp.php?CATNR=25544&FORMAT=json`), so SatMe switches by changing the host
only. Plain paths too (`/gp/amateur.json`), easier to cache and to read.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
import secrets
import time
from email.utils import formatdate, parsedate_to_datetime

from flask import (Flask, Response, abort, flash, redirect, render_template, request,
                   session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from . import __version__
from . import vpn
from .fetch import Amont, celestrak_en_pause, est_celestrak, intervalle_min
from .limite import Limiteur
from .store import Store

CACHE_S = 1800
ESSAIS_CONNEXION = 5
FENETRE_CONNEXION_S = 900


def cree_app(store: Store, amont: Amont | None = None, *, secret: str,
             derriere_proxy: bool = False, https: bool = False) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=https,
        PERMANENT_SESSION_LIFETIME=12 * 3600,
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if derriere_proxy:
        # Caddy or the operator's proxy sets X-Forwarded-For: the client's
        # real address, for the rate limit. Only then — trusted blindly, any
        # client could claim any address.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    amont = amont or Amont(store)
    limiteur = Limiteur()
    essais = Limiteur()
    compresses: dict[tuple[str, float], bytes] = {}

    # ------------------------------------------------------------ public

    def trop(route: str):
        ok, attente = limiteur.autorise(request.remote_addr or "?", store.reglage_int("limite_requetes"),
                                        store.reglage_int("fenetre_s"))
        if ok:
            store.compte(route)
            return None
        r = Response(json.dumps({"erreur": "trop de requêtes", "attente_s": attente}),
                     status=429, mimetype="application/json")
        r.headers["Retry-After"] = str(attente)
        return r

    def sert_json(corps: bytes, etag: str, modifie: float) -> Response:
        """JSON with ETag / Last-Modified / gzip: a phone that has it gets a 304."""
        if request.if_none_match and etag in request.if_none_match:
            return Response(status=304, headers={"ETag": f'"{etag}"'})
        ims = request.headers.get("If-Modified-Since")
        if ims:
            try:
                if parsedate_to_datetime(ims).timestamp() >= int(modifie):
                    return Response(status=304, headers={"ETag": f'"{etag}"'})
            except (TypeError, ValueError):
                pass
        r = Response(corps, mimetype="application/json")
        if "gzip" in request.headers.get("Accept-Encoding", "") and len(corps) > 1024:
            cle = (etag, modifie)
            if cle not in compresses:
                if len(compresses) > 64:
                    compresses.clear()
                compresses[cle] = gzip.compress(corps, 6)
            r.set_data(compresses[cle])
            r.headers["Content-Encoding"] = "gzip"
        r.headers["ETag"] = f'"{etag}"'
        r.headers["Last-Modified"] = formatdate(modifie, usegmt=True)
        r.headers["Cache-Control"] = f"public, max-age={CACHE_S}"
        r.headers["Vary"] = "Accept-Encoding"
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    def groupe(sid: str) -> Response:
        s = store.source(sid)
        if s is None or not s.actif:
            abort(404)
        f = store.fichier(sid)
        try:
            corps = f.read_bytes()
            modifie = f.stat().st_mtime
        except OSError:
            # Not fetched yet: say so plainly rather than an empty list,
            # which the app would take for "no satellites".
            return Response(json.dumps({"erreur": "groupe pas encore récupéré"}),
                            status=503, mimetype="application/json", headers={"Retry-After": "300"})
        etag = hashlib.sha256(corps).hexdigest()[:32]
        return sert_json(corps, etag, modifie)

    def par_numero(n: int) -> Response:
        catalogue = store.catalogue()
        r = catalogue.get(n)
        liste = [r] if r else amont.cherche(n, plus_grand_connu=max(catalogue, default=None))
        if not liste:
            return Response("No GP data found", status=404, mimetype="text/plain")
        corps = json.dumps(liste, separators=(",", ":")).encode()
        return sert_json(corps, hashlib.sha256(corps).hexdigest()[:32], time.time())

    @app.get("/gp/<sid>.json")
    def gp_groupe(sid: str):
        return trop("groupe") or groupe(sid)

    @app.get("/gp/catnr/<int:n>.json")
    def gp_numero(n: int):
        return trop("catnr") or par_numero(n)

    @app.get("/gp.php")
    @app.get("/NORAD/elements/gp.php")
    def gp_php():
        """CelesTrak's own query form, JSON only (SatMe reads nothing else)."""
        if request.args.get("FORMAT", "json").lower() != "json":
            return Response("Seul FORMAT=json est servi.", status=400, mimetype="text/plain")
        if request.args.get("GROUP"):
            return trop("groupe") or groupe(request.args["GROUP"].lower())
        if request.args.get("CATNR"):
            try:
                n = int(request.args["CATNR"])
            except ValueError:
                abort(400)
            return trop("catnr") or par_numero(n)
        abort(400)

    @app.get("/gp/index.json")
    def index_json():
        bloque = trop("index")
        if bloque:
            return bloque
        return {
            "serveur": store.reglage("nom_public"), "version": __version__,
            "groupes": [{
                "id": s.id, "nom": s.nom, "nombre": s.nombre,
                "mise_a_jour": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s.dernier_ok)) if s.dernier_ok else None,
                "url": url_for("gp_groupe", sid=s.id, _external=True),
            } for s in store.sources() if s.actif],
        }

    @app.get("/sante")
    def sante():
        """For monitoring: 200 when every active group is there and under a day old."""
        maintenant = time.time()
        vieux = [s.id for s in store.sources() if s.actif and maintenant - s.dernier_ok > 86_400]
        return ({"ok": not vieux, "perimes": vieux}, 200 if not vieux else 503)

    @app.get("/")
    def accueil():
        return render_template("accueil.html", nom=store.reglage("nom_public"),
                               sources=[s for s in store.sources() if s.actif], version=__version__)

    # ------------------------------------------------------------- admin

    def jeton() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        return session["csrf"]

    app.jinja_env.globals["csrf"] = jeton

    def verifie_csrf() -> None:
        if not hmac.compare_digest(request.form.get("csrf", ""), session.get("csrf", "-")):
            abort(400)

    def connecte() -> bool:
        return session.get("admin") is not None and store.admin_hash(session["admin"]) is not None

    @app.route("/admin/connexion", methods=["GET", "POST"])
    def connexion():
        if request.method == "POST":
            verifie_csrf()
            ip = request.remote_addr or "?"
            ok_essai, attente = essais.autorise(ip, ESSAIS_CONNEXION, FENETRE_CONNEXION_S)
            if not ok_essai:
                flash(f"Trop d'essais. Réessayez dans {attente // 60 + 1} min.")
                return render_template("connexion.html"), 429
            nom = request.form.get("nom", "")
            h = store.admin_hash(nom)
            if h and check_password_hash(h, request.form.get("mot_de_passe", "")):
                essais.oublie(ip)
                session.clear()
                session["admin"] = nom
                session.permanent = True
                return redirect(url_for("admin"))
            flash("Nom ou mot de passe incorrect.")
        return render_template("connexion.html")

    @app.post("/admin/deconnexion")
    def deconnexion():
        verifie_csrf()
        session.clear()
        return redirect(url_for("connexion"))

    def exige_admin():
        if not connecte():
            return redirect(url_for("connexion"))
        return None

    @app.get("/admin")
    def admin():
        r = exige_admin()
        if r:
            return r
        sources = store.sources()
        return render_template("admin.html", sources=sources, store=store,
                               intervalles={s.id: intervalle_min(store, s) for s in sources},
                               stats=store.statistiques(), maintenant=time.time(),
                               pause_celestrak=(store.reglage_float("celestrak_pause_jusqua")
                                                if celestrak_en_pause(store, time.time()) else 0),
                               raison_pause=store.reglage("celestrak_pause_raison"),
                               vpn_etat=(e := vpn.etat()),
                               vpn_sorties=(vpn.sorties(store, amont.telecharge) if e.get("cle") else []),
                               vpn_test=session.pop("vpn_test", None),
                               reglages={k: store.reglage(k) for k in (
                                   "nom_public", "intervalle_min", "limite_requetes", "fenetre_s",
                                   "catnr_amont", "catnr_url", "catnr_cache_min", "catnr_max_heure",
                                   "user_agent")},
                               version=__version__)

    @app.post("/admin/source")
    def admin_source():
        r = exige_admin()
        if r:
            return r
        verifie_csrf()
        sid = request.form.get("id", "").strip().lower()
        url = request.form.get("url", "").strip()
        if not sid.replace("-", "").replace("_", "").isalnum() or not url.startswith("https://"):
            flash("Identifiant en lettres et chiffres, adresse en https:// obligatoires.")
            return redirect(url_for("admin"))
        inter = request.form.get("intervalle_min", "").strip()
        store.pose_source(sid, request.form.get("nom", sid).strip() or sid, url,
                          request.form.get("actif") == "1", int(inter) if inter.isdigit() else None)
        flash(f"Source {sid} enregistrée.")
        return redirect(url_for("admin"))

    @app.post("/admin/source/<sid>/bascule")
    def admin_bascule(sid: str):
        """Turns a source on or off without retyping it."""
        r = exige_admin()
        if r:
            return r
        verifie_csrf()
        s = store.source(sid)
        if s:
            store.pose_source(s.id, s.nom, s.url, not s.actif, s.intervalle_min)
            flash(f"Source {sid} {'désactivée' if s.actif else 'activée'}.")
        return redirect(url_for("admin"))

    @app.post("/admin/source/<sid>/supprime")
    def admin_supprime(sid: str):
        r = exige_admin()
        if r:
            return r
        verifie_csrf()
        store.supprime_source(sid)
        flash(f"Source {sid} supprimée.")
        return redirect(url_for("admin"))

    @app.post("/admin/rafraichir")
    def admin_rafraichir():
        r = exige_admin()
        if r:
            return r
        verifie_csrf()
        ids = [s.id for s in store.sources() if not est_celestrak(s.url)]
        store.force(ids)
        flash(f"{len(ids)} source(s) récupérée(s) dans la minute. CelesTrak n'est jamais "
              "demandé plus d'une fois en deux heures : ses groupes suivent leur rythme.")
        return redirect(url_for("admin"))

    @app.post("/admin/vpn")
    def admin_vpn():
        """Asks the root helper for another exit, or for no tunnel."""
        r = exige_admin()
        if r:
            return r
        verifie_csrf()
        if not vpn.etat().get("cle"):
            flash("VPN non configuré sur cette machine (voir le README).")
            return redirect(url_for("admin"))
        if request.form.get("action") == "arret":
            ligne = "arret"
        else:
            hote = request.form.get("sortie", "")
            if hote not in {x["hostname"] for x in vpn.sorties(store, amont.telecharge)}:
                flash("Sortie inconnue.")
                return redirect(url_for("admin"))
            ligne = f"sortie {hote}"
        jeton = vpn.demande(store, ligne)
        e = vpn.attend(jeton)
        if e.get("jeton") != jeton:
            flash("Demande envoyée, pas encore traitée : rechargez la page dans un moment.")
        elif e.get("erreur"):
            flash(f"VPN : {e['erreur']}")
        elif e.get("actif"):
            flash(f"Tunnel par {e.get('sortie')} ({e.get('ville')}, {e.get('pays')}). "
                  "Cliquez sur Tester pour vérifier la sortie.")
        else:
            flash("Plus de tunnel : connexion directe.")
        return redirect(url_for("admin"))

    @app.post("/admin/vpn/test")
    def admin_vpn_test():
        r = exige_admin()
        if r:
            return r
        verifie_csrf()
        session["vpn_test"] = vpn.teste(store, amont.telecharge)
        return redirect(url_for("admin"))

    @app.post("/admin/reglages")
    def admin_reglages():
        r = exige_admin()
        if r:
            return r
        verifie_csrf()
        for cle in ("nom_public", "user_agent", "catnr_url"):
            if cle in request.form:
                store.pose_reglage(cle, request.form[cle].strip())
        for cle in ("intervalle_min", "limite_requetes", "fenetre_s", "catnr_cache_min", "catnr_max_heure"):
            v = request.form.get(cle, "").strip()
            if v.isdigit() and int(v) > 0:
                store.pose_reglage(cle, v)
        store.pose_reglage("catnr_amont", "1" if request.form.get("catnr_amont") == "1" else "0")
        flash("Réglages enregistrés.")
        return redirect(url_for("admin"))

    @app.post("/admin/mot-de-passe")
    def admin_mot_de_passe():
        r = exige_admin()
        if r:
            return r
        verifie_csrf()
        nom = session["admin"]
        if not check_password_hash(store.admin_hash(nom) or "", request.form.get("actuel", "")):
            flash("Mot de passe actuel incorrect.")
        elif len(request.form.get("nouveau", "")) < 10:
            flash("Le nouveau mot de passe doit faire au moins 10 caractères.")
        elif request.form.get("nouveau") != request.form.get("confirmation"):
            flash("Les deux saisies diffèrent.")
        else:
            store.pose_admin(nom, generate_password_hash(request.form["nouveau"]))
            flash("Mot de passe changé.")
        return redirect(url_for("admin"))

    @app.template_filter("date")
    def filtre_date(t: float) -> str:
        return time.strftime("%d/%m %H:%M UTC", time.gmtime(t)) if t else "—"

    return app


def secret_persistant(dossier: str) -> str:
    """The session key: from the environment, else created once in the data folder."""
    if os.environ.get("SATME_GP_SECRET"):
        return os.environ["SATME_GP_SECRET"]
    chemin = os.path.join(dossier, "secret.key")
    try:
        with open(chemin, encoding="ascii") as f:
            return f.read().strip()
    except OSError:
        cle = secrets.token_urlsafe(48)
        fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(cle)
        return cle
