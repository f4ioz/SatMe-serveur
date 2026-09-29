# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Command line.

    python -m satme_gp serve            the server (waitress) and the fetch loop
    python -m satme_gp admin NOM        creates or resets the admin account
    python -m satme_gp fetch            one fetch round now, printed
    python -m satme_gp sources [--actives amsat,satnogs,numero]
                                        which sources are asked (numero: lookups
                                        by number upstream); shown without option

Configuration by environment (the systemd unit reads /etc/satme-gp/env):
    SATME_GP_DATA    data folder                  (./data)
    SATME_GP_HOST    listening address            (127.0.0.1)
    SATME_GP_PORT    listening port               (8080)
    SATME_GP_PROXY   1 behind Caddy or a proxy    (0)
    SATME_GP_HTTPS   1 when reached over https    (0)
    SATME_GP_SECRET  session key                  (created in the data folder)
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

from werkzeug.security import generate_password_hash

from .store import Store


def _env(nom: str, defaut: str) -> str:
    return os.environ.get(nom, defaut)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="satme_gp", description="Serveur GP SatMe")
    sous = p.add_subparsers(dest="commande", required=True)
    sous.add_parser("serve", help="lance le serveur")
    a = sous.add_parser("admin", help="crée ou remplace le compte d'administration")
    a.add_argument("nom")
    a.add_argument("--mot-de-passe-stdin", action="store_true",
                   help="lit le mot de passe sur l'entrée standard (scripts)")
    sous.add_parser("fetch", help="récupère maintenant les sources dues")
    so = sous.add_parser("sources", help="sources interrogées")
    so.add_argument("--actives", help="identifiants séparés par des virgules ; les autres sont désactivées")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dossier = _env("SATME_GP_DATA", "data")
    store = Store(dossier)

    if args.commande == "admin":
        if args.mot_de_passe_stdin:
            mdp = sys.stdin.readline().rstrip("\n")
        else:
            mdp = getpass.getpass("Mot de passe (10 caractères au moins) : ")
            if mdp != getpass.getpass("Encore une fois : "):
                print("Les deux saisies diffèrent.", file=sys.stderr)
                return 1
        if len(mdp) < 10:
            print("Mot de passe trop court (10 caractères au moins).", file=sys.stderr)
            return 1
        store.pose_admin(args.nom, generate_password_hash(mdp))
        print(f"Compte d'administration « {args.nom} » enregistré.")
        return 0

    if args.commande == "sources":
        if args.actives is not None:
            voulues = {x.strip() for x in args.actives.split(",") if x.strip()}
            connues = {s.id for s in store.sources()} | {"numero"}
            if voulues - connues:
                print(f"Sources inconnues : {', '.join(sorted(voulues - connues))}", file=sys.stderr)
                return 1
            for s in store.sources():
                store.pose_source(s.id, s.nom, s.url, s.id in voulues, s.intervalle_min)
            store.pose_reglage("catnr_amont", "1" if "numero" in voulues else "0")
        for s in store.sources():
            print(f"{'oui' if s.actif else 'non':3}  {s.id:10}  {s.nom}")
        print(f"{'oui' if store.reglage('catnr_amont') == '1' else 'non':3}  {'numero':10}  "
              "Recherche par numéro chez la source")
        return 0

    if args.commande == "fetch":
        from .fetch import tour
        for ligne in tour(store) or ["rien à récupérer pour l'instant"]:
            print(ligne)
        return 0

    from waitress import serve

    from .app import cree_app, secret_persistant
    from .fetch import Planificateur

    if not store.a_un_admin():
        logging.warning("Aucun compte d'administration : lancez « python -m satme_gp admin NOM ».")
    app = cree_app(store, secret=secret_persistant(dossier),
                   derriere_proxy=_env("SATME_GP_PROXY", "0") == "1",
                   https=_env("SATME_GP_HTTPS", "0") == "1")
    Planificateur(store, app.extensions["garde"].pays).start()
    hote, port = _env("SATME_GP_HOST", "127.0.0.1"), int(_env("SATME_GP_PORT", "8080"))
    logging.info("Serveur GP SatMe sur %s:%d, données dans %s", hote, port, os.path.abspath(dossier))
    # A robot opening hundreds of connections gets queued, not the server exhausted.
    serve(app, host=hote, port=port, threads=8, ident="satme-gp",
          connection_limit=200, channel_timeout=30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
