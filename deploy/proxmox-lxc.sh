#!/usr/bin/env bash
# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Run ON THE PROXMOX VE HOST, as root: creates an unprivileged Debian 12
# container, copies the server into it and runs deploy/install.sh inside.
#
#   ./deploy/proxmox-lxc.sh                           questions asked one by one
#   ./deploy/proxmox-lxc.sh --ip 192.168.1.50/24 --passerelle 192.168.1.1 \
#        --mode proxy --admin f4ioz
#   ./deploy/proxmox-lxc.sh --mode caddy --domaine gp.exemple.org --email moi@exemple.org
#   ./deploy/proxmox-lxc.sh --mise-a-jour --ctid 120  new code into an existing container
#
# Container options: --ctid N (next free), --nom satme-gp, --stockage local-lvm,
#   --stockage-modeles local, --pont vmbr0, --ip dhcp|ADRESSE/MASQUE,
#   --passerelle IP, --memoire 512, --disque 4, --coeurs 1, --cle-ssh FICHIER.pub
# Server options, passed to install.sh: --mode caddy|proxy, --domaine, --email,
#   --port 8080, --admin NOM, --vpn-mullvad FICHIER.conf.

set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTID=""; NOM=satme-gp; STOCKAGE=local-lvm; MODELES=local; PONT=vmbr0; IP=dhcp; GW=""
MEMOIRE=512; DISQUE=4; COEURS=1; CLE_SSH=""
MODE=""; DOMAINE=""; EMAIL=""; PORT=8080; ADMIN=""; MISE_A_JOUR=0; VPN_CONF=""

aide() { sed -n '6,20p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
dit()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
nie()  { printf '\033[1;31mErreur :\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --ctid) CTID="$2"; shift 2;;
    --nom) NOM="$2"; shift 2;;
    --stockage) STOCKAGE="$2"; shift 2;;
    --stockage-modeles) MODELES="$2"; shift 2;;
    --pont) PONT="$2"; shift 2;;
    --ip) IP="$2"; shift 2;;
    --passerelle) GW="$2"; shift 2;;
    --memoire) MEMOIRE="$2"; shift 2;;
    --disque) DISQUE="$2"; shift 2;;
    --coeurs) COEURS="$2"; shift 2;;
    --cle-ssh) CLE_SSH="$2"; shift 2;;
    --mode) MODE="$2"; shift 2;;
    --domaine) DOMAINE="$2"; shift 2;;
    --email) EMAIL="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --admin) ADMIN="$2"; shift 2;;
    --vpn-mullvad) VPN_CONF="$2"; shift 2;;
    --mise-a-jour) MISE_A_JOUR=1; shift;;
    -h|--help) aide 0;;
    *) echo "Option inconnue : $1" >&2; aide 1;;
  esac
done

[ "$(id -u)" -eq 0 ] || nie "à lancer en root sur l'hôte Proxmox."
command -v pct >/dev/null && command -v pveam >/dev/null || nie "pct et pveam introuvables : ce n'est pas un hôte Proxmox VE."
[ -z "$VPN_CONF" ] || [ -f "$VPN_CONF" ] || nie "fichier WireGuard introuvable : $VPN_CONF"

pousse_code() {
  local archive
  archive=$(mktemp /tmp/satme-gp-XXXXXX.tgz)
  tar czf "$archive" -C "$SOURCE" --exclude .git --exclude .venv --exclude data \
      --exclude '__pycache__' --exclude .pytest_cache .
  pct push "$CTID" "$archive" /root/satme-gp.tgz
  rm -f "$archive"
  pct exec "$CTID" -- bash -c 'rm -rf /root/satme-gp && mkdir -p /root/satme-gp && tar xzf /root/satme-gp.tgz -C /root/satme-gp && rm /root/satme-gp.tgz'
}

# ------------------------------------------------------------ update only
if [ "$MISE_A_JOUR" -eq 1 ]; then
  [ -n "$CTID" ] || nie "--mise-a-jour demande --ctid."
  pct status "$CTID" >/dev/null 2>&1 || nie "conteneur $CTID introuvable."
  dit "Nouveau code dans le conteneur $CTID"
  pousse_code
  pct exec "$CTID" -- /root/satme-gp/deploy/install.sh --mise-a-jour
  exit 0
fi

# ----------------------------------------------------------------- choices
lit() { local var=$1 q=$2 def=${3:-} r; [ -n "${!var}" ] && return
        read -r -p "$q${def:+ [$def]} : " r; printf -v "$var" '%s' "${r:-$def}"; }
if [ -z "$MODE" ]; then
  echo "Comment le serveur sera-t-il joint depuis Internet ?"
  echo "  1) Caddy dans le conteneur, HTTPS automatique (domaine pointant vers le conteneur,"
  echo "     ports 80 et 443 redirigés vers lui)"
  echo "  2) Derrière un proxy existant : HTTP sur un port du conteneur"
  read -r -p "Choix [2] : " c
  case "${c:-2}" in 1) MODE=caddy;; 2) MODE=proxy;; *) nie "choix inconnu : $c";; esac
fi
if [ "$MODE" = caddy ]; then
  lit DOMAINE "Nom de domaine (ex. gp.exemple.org)"
  [ -n "$DOMAINE" ] || nie "le mode caddy demande un nom de domaine."
  lit EMAIL "E-mail pour Let's Encrypt" ""
fi
lit ADMIN "Nom du compte d'administration" "admin"
read -r -s -p "Mot de passe d'administration (10 caractères au moins) : " MDP; echo
read -r -s -p "Encore une fois : " MDP2; echo
[ "$MDP" = "$MDP2" ] || nie "les deux saisies diffèrent."
[ "${#MDP}" -ge 10 ] || nie "mot de passe trop court."

[ -n "$CTID" ] || CTID=$(pvesh get /cluster/nextid)
pct status "$CTID" >/dev/null 2>&1 && nie "le conteneur $CTID existe déjà (choisissez --ctid)."

# --------------------------------------------------------------- template
dit "Modèle Debian 12"
pveam update >/dev/null
MODELE=$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -1)
[ -n "$MODELE" ] || nie "modèle debian-12-standard introuvable dans pveam."
if ! pveam list "$MODELES" | grep -q "$MODELE"; then
  pveam download "$MODELES" "$MODELE"
fi

# -------------------------------------------------------------- container
dit "Conteneur $CTID ($NOM)"
RESEAU="name=eth0,bridge=$PONT,ip=$IP"
[ "$IP" != dhcp ] && [ -n "$GW" ] && RESEAU="$RESEAU,gw=$GW"
MDP_ROOT=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20)
options=(--hostname "$NOM" --memory "$MEMOIRE" --swap 256 --cores "$COEURS"
         --rootfs "$STOCKAGE:$DISQUE" --net0 "$RESEAU" --unprivileged 1
         --features nesting=1 --onboot 1 --password "$MDP_ROOT" --timezone host)
[ -n "$CLE_SSH" ] && options+=(--ssh-public-keys "$CLE_SSH")
pct create "$CTID" "$MODELES:vztmpl/$MODELE" "${options[@]}"
pct start "$CTID"

dit "Attente du réseau"
for _ in $(seq 1 60); do
  pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
  sleep 2
done
pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 || nie "le conteneur n'a pas de réseau (pont $PONT, ip $IP)."

# ----------------------------------------------------------------- server
dit "Installation du serveur dans le conteneur"
pousse_code
# The password goes through a file readable by root only, never the command line.
fmdp=$(mktemp); chmod 600 "$fmdp"; printf '%s\n' "$MDP" > "$fmdp"
pct push "$CTID" "$fmdp" /root/.satme-gp-mdp --perms 600
rm -f "$fmdp"
if [ -n "$VPN_CONF" ]; then
  # A WireGuard interface in an unprivileged container needs the host's module.
  modprobe wireguard 2>/dev/null || nie "module wireguard absent sur l'hôte Proxmox."
  grep -qx wireguard /etc/modules 2>/dev/null || echo wireguard >> /etc/modules
  pct push "$CTID" "$VPN_CONF" /root/.satme-gp-mullvad.conf --perms 600
fi
# The proxy sits outside the container: the server listens on every interface.
args=(--mode "$MODE" --admin "$ADMIN" --admin-mdp-stdin --non-interactif --port "$PORT")
[ -n "$VPN_CONF" ] && args+=(--vpn-mullvad /root/.satme-gp-mullvad.conf)
if [ "$MODE" = caddy ]; then args+=(--domaine "$DOMAINE" --email "$EMAIL")
else args+=(--ecoute 0.0.0.0); fi
pct exec "$CTID" -- bash -c '/root/satme-gp/deploy/install.sh "$@" < /root/.satme-gp-mdp; s=$?; rm -f /root/.satme-gp-mdp /root/.satme-gp-mullvad.conf; exit $s' _ "${args[@]}"

ADRESSE=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
echo
dit "Conteneur $CTID prêt, adresse $ADRESSE"
if [ "$MODE" = caddy ]; then
  echo "  Redirigez les ports 80 et 443 de votre box vers $ADRESSE, puis :"
  echo "  https://$DOMAINE/  ·  https://$DOMAINE/admin"
else
  echo "  Serveur : http://$ADRESSE:$PORT/  ·  administration : http://$ADRESSE:$PORT/admin"
  echo "  À déclarer dans votre proxy vers http://$ADRESSE:$PORT"
fi
echo "  Mot de passe root du conteneur : $MDP_ROOT  (pct enter $CTID pour une console)"
echo "  Mise à jour plus tard : $0 --mise-a-jour --ctid $CTID"
