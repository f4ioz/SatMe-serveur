#!/usr/bin/env bash
# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Run ON THE PROXMOX VE HOST, as root: creates an unprivileged Debian 12
# container (first free number), copies the server into it and installs it.
# Every choice is asked, with a default; a summary is shown before anything
# is created. Works from a clone, or alone:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/f4ioz/SatMe-serveur/main/deploy/proxmox-lxc.sh)"
#   ./deploy/proxmox-lxc.sh
#   ./deploy/proxmox-lxc.sh --essai                 show what would be done, change nothing
#   ./deploy/proxmox-lxc.sh --mise-a-jour --ctid 120  new code into an existing container
#
# Answers may also be given as options (then not asked): --ctid N, --nom,
#   --stockage, --stockage-modeles, --pont, --ip dhcp|ADRESSE/MASQUE,
#   --passerelle, --memoire, --disque, --coeurs, --cle-ssh FICHIER.pub,
#   --mode caddy|proxy, --domaine, --email, --port, --https oui|non,
#   --admin NOM, --sources amsat,satnogs,numero, --vpn-mullvad FICHIER.conf,
#   --vpn-sortie fr, --oui (no confirmation)

set -euo pipefail

DEPOT=https://github.com/f4ioz/SatMe-serveur
CTID=""; NOM=""; STOCKAGE=""; MODELES=""; PONT=""; IP=""; GW=""
MEMOIRE=""; DISQUE=""; COEURS=""; CLE_SSH="-"
MODE=""; DOMAINE="-"; EMAIL="-"; PORT=""; HTTPS=""; ADMIN=""; VPN_CONF="-"; VPN_SORTIE=""; SOURCES=""
MISE_A_JOUR=0; ESSAI=0; OUI=0

aide() { sed -n '6,22p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null | sed 's/^# \{0,1\}//'
         [ -n "${BASH_SOURCE[0]:-}" ] || echo "Voir $DEPOT"; exit "${1:-0}"; }
dit()  { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
nie()  { printf '\033[1;31mErreur :\033[0m %s\n' "$*" >&2; exit 1; }
# Every action on Proxmox goes through here: printed only with --essai.
fait() { if [ "$ESSAI" -eq 1 ]; then printf '   [essai] %s\n' "$*"; else "$@"; fi; }

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
    --https) HTTPS="$2"; shift 2;;
    --admin) ADMIN="$2"; shift 2;;
    --vpn-mullvad) VPN_CONF="$2"; shift 2;;
    --sources) SOURCES="$2"; shift 2;;
    --vpn-sortie) VPN_SORTIE="$2"; shift 2;;
    --mise-a-jour) MISE_A_JOUR=1; shift;;
    --essai) ESSAI=1; shift;;
    --oui) OUI=1; shift;;
    -h|--help) aide 0;;
    *) echo "Option inconnue : $1" >&2; aide 1;;
  esac
done

if [ "$ESSAI" -eq 0 ]; then
  [ "$(id -u)" -eq 0 ] || nie "à lancer en root sur l'hôte Proxmox."
  command -v pct >/dev/null && command -v pveam >/dev/null || nie "pct et pveam introuvables : ce n'est pas un hôte Proxmox VE."
fi
[ -r /dev/tty ] || nie "pas de terminal pour les questions."

# ------------------------------------------------------------------ code
# Beside the script in a clone; otherwise (curl | bash) downloaded from GitHub.
SOURCE=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/../satme_gp/app.py" ]; then
  SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
TEMP=$(mktemp -d /tmp/satme-gp-XXXXXX)
trap 'rm -rf "$TEMP"' EXIT
if [ -z "$SOURCE" ]; then
  dit "Téléchargement du serveur depuis $DEPOT"
  curl -fsSL "$DEPOT/archive/refs/heads/main.tar.gz" | tar xz -C "$TEMP"
  SOURCE="$TEMP/SatMe-serveur-main"
  [ -f "$SOURCE/satme_gp/app.py" ] || nie "archive téléchargée incomplète."
fi

pousse_code() {
  local archive="$TEMP/code.tgz"
  tar czf "$archive" -C "$SOURCE" --exclude .git --exclude .venv --exclude data \
      --exclude '__pycache__' --exclude .pytest_cache .
  fait pct push "$CTID" "$archive" /root/satme-gp.tgz
  fait pct exec "$CTID" -- bash -c 'rm -rf /root/satme-gp && mkdir -p /root/satme-gp && tar xzf /root/satme-gp.tgz -C /root/satme-gp && rm /root/satme-gp.tgz'
}

# ------------------------------------------------------------ update only
if [ "$MISE_A_JOUR" -eq 1 ]; then
  [ -n "$CTID" ] || nie "--mise-a-jour demande --ctid."
  [ "$ESSAI" -eq 1 ] || pct status "$CTID" >/dev/null 2>&1 || nie "conteneur $CTID introuvable."
  dit "Nouveau code dans le conteneur $CTID"
  pousse_code
  fait pct exec "$CTID" -- /root/satme-gp/deploy/install.sh --mise-a-jour
  exit 0
fi

# -------------------------------------------------------------- questions
# pose VARIABLE "question" "défaut": asked unless given as an option.
pose() {
  local var=$1 q=$2 def=${3:-} r
  [ -n "${!var}" ] && return
  read -r -p "  $q${def:+ [$def]} : " r </dev/tty
  printf -v "$var" '%s' "${r:-$def}"
}
# Optional answers start as "-" (not asked yet); "" means "none".
pose_opt() {
  local var=$1 q=$2 def=${3:-} r
  [ "${!var}" != "-" ] && return
  read -r -p "  $q${def:+ [$def]} : " r </dev/tty
  printf -v "$var" '%s' "${r:-$def}"
}
oui_non() { # oui_non "question" o|n → 0 for yes
  local r; read -r -p "  $1 [$( [ "$2" = o ] && echo O/n || echo o/N )] : " r </dev/tty
  case "${r:-$2}" in o|O|oui|y|Y) return 0;; *) return 1;; esac
}
liste() { command -v "$1" >/dev/null && "${@}" 2>/dev/null || true; }

echo
echo "Serveur GP SatMe — installation dans un conteneur Proxmox"
echo "(Entrée = valeur proposée entre crochets)"

dit "Conteneur"
LIBRE=$(liste pvesh get /cluster/nextid); LIBRE=${LIBRE:-100}
pose CTID "Numéro du conteneur (premier libre)" "$LIBRE"
[[ "$CTID" =~ ^[0-9]+$ ]] || nie "numéro invalide : $CTID"
[ "$ESSAI" -eq 1 ] || ! pct status "$CTID" >/dev/null 2>&1 || nie "le conteneur $CTID existe déjà."
pose NOM "Nom d'hôte" "satme-gp"

STOCKAGES=$(liste pvesm status -content rootdir | awk 'NR>1 && $3=="active" {print $1}' | xargs)
echo "  Stockages pour le disque : ${STOCKAGES:-?}"
DEF=local-lvm; [ -z "$STOCKAGES" ] || [[ " $STOCKAGES " == *" local-lvm "* ]] || DEF=${STOCKAGES%% *}
pose STOCKAGE "Stockage du disque" "$DEF"
MODS=$(liste pvesm status -content vztmpl | awk 'NR>1 && $3=="active" {print $1}' | xargs)
DEF=local; [ -z "$MODS" ] || [[ " $MODS " == *" local "* ]] || DEF=${MODS%% *}
pose MODELES "Stockage des modèles (${MODS:-?})" "$DEF"
pose DISQUE "Disque (Go)" "4"
pose MEMOIRE "Mémoire (Mo)" "512"
pose COEURS "Cœurs" "1"

dit "Réseau"
PONTS=$(liste ip -o link show type bridge | awk -F': ' '{print $2}' | cut -d@ -f1 | xargs)
echo "  Ponts : ${PONTS:-?}"
pose PONT "Pont" "vmbr0"
echo "  Une adresse fixe est conseillée : le proxy (ou la box) doit toujours la trouver."
pose IP "Adresse IP (dhcp, ou ADRESSE/MASQUE comme 192.168.1.50/24)" "dhcp"
if [ "$IP" != dhcp ]; then
  [[ "$IP" == */* ]] || nie "adresse sans masque : $IP (ex. 192.168.1.50/24)."
  GW_HOTE=$(liste ip -4 route show default | awk '{print $3; exit}')
  pose GW "Passerelle" "$GW_HOTE"
fi
DEF=""; [ -f /root/.ssh/authorized_keys ] && DEF=/root/.ssh/authorized_keys
pose_opt CLE_SSH "Clés SSH publiques pour root du conteneur (fichier, vide = aucune)" "$DEF"
[ -z "$CLE_SSH" ] || [ -f "$CLE_SSH" ] || nie "fichier introuvable : $CLE_SSH"

dit "Accès depuis Internet"
if [ -z "$MODE" ]; then
  echo "  1) Caddy dans le conteneur, HTTPS automatique : les ports 80 et 443 de la box"
  echo "     doivent aller au conteneur"
  echo "  2) Derrière votre proxy existant (Nginx Proxy Manager, Traefik, Nginx…) : le"
  echo "     proxy garde les ports 80 et 443 et envoie le domaine vers le conteneur"
  read -r -p "  Choix [2] : " c </dev/tty
  case "${c:-2}" in 1) MODE=caddy;; 2) MODE=proxy;; *) nie "choix inconnu : $c";; esac
fi
case "$MODE" in
  caddy)
    pose_opt DOMAINE "Nom de domaine (ex. gp.exemple.org)" ""
    [ -n "$DOMAINE" ] || nie "le mode caddy demande un nom de domaine."
    pose_opt EMAIL "E-mail pour Let's Encrypt (vide = aucun)" ""
    PORT=${PORT:-8080}; HTTPS=oui;;
  proxy)
    pose_opt DOMAINE "Nom de domaine déclaré dans le proxy (ex. gp.exemple.org)" ""
    EMAIL=""
    pose PORT "Port du serveur dans le conteneur" "8080"
    if [ -z "$HTTPS" ]; then
      if oui_non "Le proxy sert-il ce domaine en HTTPS ?" o; then HTTPS=oui; else HTTPS=non; fi
    fi;;
  *) nie "mode inconnu : $MODE (caddy ou proxy)";;
esac

dit "Administration (https://domaine/admin)"
pose ADMIN "Nom du compte" "admin"
while :; do
  read -r -s -p "  Mot de passe (10 caractères au moins) : " MDP </dev/tty; echo
  [ "${#MDP}" -ge 10 ] || { echo "  Trop court."; continue; }
  read -r -s -p "  Encore une fois : " MDP2 </dev/tty; echo
  [ "$MDP" = "$MDP2" ] && break
  echo "  Les deux saisies diffèrent."
done

dit "Sources de données"
. "$SOURCE/deploy/sources.sh"
[ -n "$SOURCES" ] || choisis_sources "$SOURCE" SOURCES

dit "VPN Mullvad (optionnel, pour les requêtes du serveur vers les sources)"
echo "  Il peut aussi être ajouté plus tard dans l'administration, en y envoyant le fichier."
if [ "$VPN_CONF" = "-" ]; then
  VPN_CONF=""
  if oui_non "Faire passer les requêtes du serveur par Mullvad dès maintenant ?" n; then
    echo "  Fichier WireGuard généré sur mullvad.net (Compte → Configuration WireGuard → Linux)."
    pose VPN_CONF "Chemin du fichier .conf sur cet hôte (vide = plus tard)" ""
  fi
fi
if [ -n "$VPN_CONF" ]; then
  [ -f "$VPN_CONF" ] || nie "fichier introuvable : $VPN_CONF"
  grep -q '^ *PrivateKey' "$VPN_CONF" || nie "pas de PrivateKey dans $VPN_CONF."
  pose VPN_SORTIE "Sortie (pays fr, ville fr-par ou relais fr-par-wg-001)" "fr"
fi

# ---------------------------------------------------------------- summary
dit "Récapitulatif"
if [ "$MODE" = caddy ]; then ACCES="Caddy, https://$DOMAINE/ (certificat automatique)"
else ACCES="derrière le proxy : ${DOMAINE:-(domaine)} → http://ADRESSE:$PORT, HTTPS $HTTPS"; fi
if [ -n "$VPN_CONF" ]; then VPN="Mullvad, sortie $VPN_SORTIE, tunnel monté avant la 1re récupération"
else VPN="non"; fi
cat <<EOF
  Conteneur  $CTID « $NOM », Debian 12 non privilégié, démarre avec l'hôte
             disque $DISQUE Go sur $STOCKAGE, $MEMOIRE Mo, $COEURS cœur(s)
  Réseau     $PONT, $IP${GW:+ via $GW}${CLE_SSH:+, clés SSH de $CLE_SSH}
  Accès      $ACCES
  Admin      $ADMIN
  Sources    ${SOURCES//,/, }
  VPN        $VPN
EOF
if [ "$OUI" -eq 0 ] && ! oui_non "Créer le conteneur ?" o; then echo "Rien n'a été fait."; exit 0; fi

# --------------------------------------------------------------- template
dit "Modèle Debian 12"
if [ "$ESSAI" -eq 1 ]; then
  MODELE=debian-12-standard_12.x_amd64.tar.zst
else
  pveam update >/dev/null
  MODELE=$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -1)
  [ -n "$MODELE" ] || nie "modèle debian-12-standard introuvable dans pveam."
fi
if [ "$ESSAI" -eq 1 ] || ! pveam list "$MODELES" | grep -q "$MODELE"; then
  fait pveam download "$MODELES" "$MODELE"
fi

# -------------------------------------------------------------- container
dit "Conteneur $CTID"
RESEAU="name=eth0,bridge=$PONT,ip=$IP"
[ "$IP" != dhcp ] && [ -n "$GW" ] && RESEAU="$RESEAU,gw=$GW"
MDP_ROOT=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20 || true)
options=(--hostname "$NOM" --memory "$MEMOIRE" --swap 256 --cores "$COEURS"
         --rootfs "$STOCKAGE:$DISQUE" --net0 "$RESEAU" --unprivileged 1
         --features nesting=1 --onboot 1 --password "$MDP_ROOT" --timezone host)
[ -n "$CLE_SSH" ] && options+=(--ssh-public-keys "$CLE_SSH")
fait pct create "$CTID" "$MODELES:vztmpl/$MODELE" "${options[@]}"
fait pct start "$CTID"

dit "Attente du réseau"
if [ "$ESSAI" -eq 0 ]; then
  for _ in $(seq 1 60); do
    pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
    sleep 2
  done
  pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 || nie "le conteneur n'a pas de réseau (pont $PONT, ip $IP)."
fi

# ----------------------------------------------------------------- server
dit "Installation du serveur dans le conteneur"
pousse_code
# The password goes through a file readable by root only, never the command line.
fmdp="$TEMP/mdp"; ( umask 077; printf '%s\n' "$MDP" > "$fmdp" )
fait pct push "$CTID" "$fmdp" /root/.satme-gp-mdp --perms 600
args=(--mode "$MODE" --admin "$ADMIN" --admin-mdp-stdin --non-interactif --port "$PORT" --sources "$SOURCES")
[ -n "$DOMAINE" ] && args+=(--domaine "$DOMAINE")
if [ "$MODE" = caddy ]; then
  [ -n "$EMAIL" ] && args+=(--email "$EMAIL")
else
  # The proxy sits outside the container: the server listens on every interface.
  args+=(--ecoute 0.0.0.0)
  [ "$HTTPS" = oui ] && args+=(--https)
fi
# A WireGuard interface in an unprivileged container needs the host's module:
# loaded in any case, so a tunnel added later from the admin page works too.
if fait modprobe wireguard; then
  grep -qx wireguard /etc/modules 2>/dev/null || fait sh -c 'echo wireguard >> /etc/modules'
elif [ -n "$VPN_CONF" ]; then
  nie "module wireguard absent sur l'hôte Proxmox."
else
  echo "  (module wireguard absent sur l'hôte : le VPN ne pourra pas être ajouté plus tard)"
fi
if [ -n "$VPN_CONF" ]; then
  fait pct push "$CTID" "$VPN_CONF" /root/.satme-gp-mullvad.conf --perms 600
  args+=(--vpn-mullvad /root/.satme-gp-mullvad.conf --vpn-sortie "$VPN_SORTIE")
fi
fait pct exec "$CTID" -- bash -c '/root/satme-gp/deploy/install.sh "$@" < /root/.satme-gp-mdp; s=$?; rm -f /root/.satme-gp-mdp /root/.satme-gp-mullvad.conf; exit $s' _ "${args[@]}"

ADRESSE="ADRESSE"
[ "$ESSAI" -eq 1 ] || ADRESSE=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
dit "Conteneur $CTID prêt, adresse $ADRESSE"
if [ "$MODE" = caddy ]; then
  echo "  Redirigez les ports 80 et 443 de la box vers $ADRESSE, puis :"
  echo "  https://$DOMAINE/  ·  https://$DOMAINE/admin"
else
  echo "  Dans votre proxy : ${DOMAINE:-le domaine} → http://$ADRESSE:$PORT"
  [ "$HTTPS" = oui ] && echo "  (certificat pour ${DOMAINE:-ce domaine} ; transmettre X-Forwarded-For et X-Forwarded-Proto)"
  echo "  Ensuite : https://${DOMAINE:-domaine}/  ·  https://${DOMAINE:-domaine}/admin"
  echo "  Sur le réseau local, directement : http://$ADRESSE:$PORT/"
fi
if [ -n "$VPN_CONF" ]; then echo "  VPN : sortie choisie, modifiable dans l'administration."
else echo "  VPN : à ajouter quand vous voulez dans l'administration (fichier Mullvad)."; fi
echo "  Mot de passe root du conteneur : $MDP_ROOT  (ou : pct enter $CTID)"
echo "  Mise à jour plus tard, depuis l'hôte :"
echo "      bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/f4ioz/SatMe-serveur/main/deploy/proxmox-lxc.sh)\" _ --mise-a-jour --ctid $CTID"
