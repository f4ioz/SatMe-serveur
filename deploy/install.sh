#!/usr/bin/env bash
# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Installs (or updates) the server on Debian, Ubuntu or Raspberry Pi OS:
# system user, Python environment, systemd service, and either Caddy with an
# automatic certificate or a plain local port behind the operator's proxy.
#
#   sudo ./deploy/install.sh                  questions asked one by one
#   sudo ./deploy/install.sh --mode caddy --domaine gp.exemple.org \
#        --email moi@exemple.org --admin f4ioz
#   sudo ./deploy/install.sh --mode proxy --ecoute 0.0.0.0 --port 8080 --admin f4ioz
#   sudo ./deploy/install.sh --mise-a-jour    new code, same settings and data
#   add --vpn-mullvad FICHIER.conf to send the server's own requests through
#   Mullvad (WireGuard file generated on mullvad.net; exit chosen in /admin)
#
# Run again at will: data (/var/lib/satme-gp) and settings (/etc/satme-gp)
# are kept; only the code is replaced.

set -euo pipefail

APP=satme-gp
UTILISATEUR=satme-gp
CODE=/opt/satme-gp
DONNEES=/var/lib/satme-gp
CONFIG=/etc/satme-gp
ENVFILE=$CONFIG/env
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODE=""; DOMAINE=""; EMAIL=""; ECOUTE=""; PORT=""; ADMIN=""; MDP_STDIN=0
MISE_A_JOUR=0; NON_INTERACTIF=0; VPN_CONF=""

aide() { sed -n '6,21p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
dit()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
nie()  { printf '\033[1;31mErreur :\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="$2"; shift 2;;
    --domaine) DOMAINE="$2"; shift 2;;
    --email) EMAIL="$2"; shift 2;;
    --ecoute) ECOUTE="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --admin) ADMIN="$2"; shift 2;;
    --admin-mdp-stdin) MDP_STDIN=1; shift;;
    --mise-a-jour) MISE_A_JOUR=1; shift;;
    --non-interactif) NON_INTERACTIF=1; shift;;
    --vpn-mullvad) VPN_CONF="$2"; shift 2;;
    -h|--help) aide 0;;
    *) echo "Option inconnue : $1" >&2; aide 1;;
  esac
done

[ "$(id -u)" -eq 0 ] || nie "à lancer en root (sudo)."
command -v apt-get >/dev/null || nie "seuls Debian, Ubuntu et Raspberry Pi OS sont pris en charge (apt)."
[ -f "$SOURCE/satme_gp/app.py" ] || nie "code introuvable à côté du script ($SOURCE)."
[ -z "$VPN_CONF" ] || [ -f "$VPN_CONF" ] || nie "fichier WireGuard introuvable : $VPN_CONF"

demande() { # demande VARIABLE "question" "défaut"
  local var=$1 q=$2 def=${3:-} r
  if [ -n "${!var}" ]; then return; fi
  if [ "$NON_INTERACTIF" -eq 1 ]; then printf -v "$var" '%s' "$def"; return; fi
  read -r -p "$q${def:+ [$def]} : " r </dev/tty
  printf -v "$var" '%s' "${r:-$def}"
}

# ---------------------------------------------------------------- choices
if [ "$MISE_A_JOUR" -eq 1 ]; then
  [ -f "$ENVFILE" ] || nie "rien à mettre à jour : $ENVFILE absent (installez d'abord)."
else
  if [ -z "$MODE" ] && [ "$NON_INTERACTIF" -eq 0 ]; then
    echo "Comment le serveur sera-t-il joint depuis Internet ?"
    echo "  1) Caddy installé ici, certificat HTTPS automatique (il faut un nom de domaine"
    echo "     qui pointe vers cette machine, ports 80 et 443 ouverts)"
    echo "  2) Derrière un proxy existant (Nginx, Traefik, Nginx Proxy Manager…) : HTTP sur un port"
    read -r -p "Choix [1] : " c </dev/tty
    case "${c:-1}" in 1) MODE=caddy;; 2) MODE=proxy;; *) nie "choix inconnu : $c";; esac
  fi
  MODE=${MODE:-proxy}
  case "$MODE" in
    caddy)
      demande DOMAINE "Nom de domaine (ex. gp.exemple.org)"
      [ -n "$DOMAINE" ] || nie "le mode caddy demande un nom de domaine."
      demande EMAIL "Adresse e-mail pour Let's Encrypt (avis d'expiration)" ""
      ECOUTE=127.0.0.1; PORT=${PORT:-8080};;
    proxy)
      demande ECOUTE "Adresse d'écoute (127.0.0.1 si le proxy est sur cette machine, 0.0.0.0 sinon)" "127.0.0.1"
      demande PORT "Port" "8080";;
    *) nie "mode inconnu : $MODE (caddy ou proxy)";;
  esac
  demande ADMIN "Nom du compte d'administration" "admin"
fi

# --------------------------------------------------------------- packages
dit "Paquets système"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv ca-certificates curl rsync iproute2 >/dev/null

# ------------------------------------------------------------------- user
if ! id "$UTILISATEUR" >/dev/null 2>&1; then
  dit "Utilisateur système $UTILISATEUR"
  useradd --system --home-dir "$DONNEES" --shell /usr/sbin/nologin "$UTILISATEUR"
fi
install -d -o "$UTILISATEUR" -g "$UTILISATEUR" -m 750 "$DONNEES"
install -d -o root -g "$UTILISATEUR" -m 750 "$CONFIG"
install -d -o "$UTILISATEUR" -g "$UTILISATEUR" -m 750 "$DONNEES/vpn"
install -d -o root -g root -m 755 /var/lib/satme-gp-vpn

# ------------------------------------------------------------------- code
dit "Code dans $CODE"
install -d -m 755 "$CODE"
rsync -a --delete --exclude .git --exclude .venv --exclude data --exclude tests \
      --exclude '__pycache__' --exclude .pytest_cache "$SOURCE/" "$CODE/app/"
if [ ! -x "$CODE/venv/bin/python" ]; then
  python3 -m venv "$CODE/venv"
fi
"$CODE/venv/bin/pip" install -q --upgrade pip
"$CODE/venv/bin/pip" install -q -r "$CODE/app/requirements.txt"

# --------------------------------------------------------------- settings
if [ "$MISE_A_JOUR" -eq 0 ]; then
  dit "Réglages dans $ENVFILE"
  PROXY=1; HTTPS=0
  [ "$MODE" = caddy ] && HTTPS=1
  # A plain port reached directly (no proxy in front) must not trust
  # X-Forwarded-For: any client could claim any address.
  if [ "$MODE" = proxy ] && [ "$ECOUTE" = 0.0.0.0 ] && [ "$NON_INTERACTIF" -eq 0 ]; then
    read -r -p "Le proxy placé devant transmet-il l'adresse du client (X-Forwarded-For) ? [O/n] " r </dev/tty
    case "$r" in n|N) PROXY=0;; esac
  fi
  # umask for this file only: left in force, it made the Caddy keyring
  # unreadable by apt ("NO_PUBKEY").
  ( umask 027
  cat > "$ENVFILE" <<EOF
# Serveur GP SatMe — lu par le service systemd $APP
SATME_GP_DATA=$DONNEES
SATME_GP_HOST=$ECOUTE
SATME_GP_PORT=$PORT
SATME_GP_PROXY=$PROXY
SATME_GP_HTTPS=$HTTPS
EOF
  )
  chown root:"$UTILISATEUR" "$ENVFILE"; chmod 640 "$ENVFILE"
  echo "MODE=$MODE" > "$CONFIG/installation"
  [ -n "$DOMAINE" ] && echo "DOMAINE=$DOMAINE" >> "$CONFIG/installation"
fi

# ---------------------------------------------------------------- service
dit "Service systemd"
cat > /etc/systemd/system/$APP.service <<EOF
[Unit]
Description=Serveur GP SatMe (éléments orbitaux pour SatMe)
After=network-online.target satme-gp-vpn.service
Wants=network-online.target

[Service]
User=$UTILISATEUR
Group=$UTILISATEUR
EnvironmentFile=$ENVFILE
WorkingDirectory=$DONNEES
ExecStart=$CODE/venv/bin/python -m satme_gp serve
Environment=PYTHONPATH=$CODE/app
Restart=on-failure
RestartSec=5
# Hardening: the server only needs its data folder and the network.
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$DONNEES
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
EOF
# Optional Mullvad tunnel: a root helper the web admin reaches only through a
# one-line request file, watched by a path unit.
install -m 755 -o root -g root "$SOURCE/deploy/satme-gp-vpn" /usr/local/sbin/satme-gp-vpn
cat > /etc/systemd/system/$APP-vpn.service <<EOF
[Unit]
Description=Serveur GP SatMe : tunnel Mullvad (optionnel)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/satme-gp-vpn restaure

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/$APP-vpn-demande.service <<EOF
[Unit]
Description=Serveur GP SatMe : demande de tunnel faite dans l'administration

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/satme-gp-vpn applique
EOF
cat > /etc/systemd/system/$APP-vpn-demande.path <<EOF
[Unit]
Description=Serveur GP SatMe : attente des demandes de tunnel

[Path]
PathChanged=$DONNEES/vpn/demande
Unit=$APP-vpn-demande.service

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --quiet $APP $APP-vpn.service $APP-vpn-demande.path
systemctl start $APP-vpn-demande.path
[ -f /var/lib/satme-gp-vpn/etat.json ] || /usr/local/sbin/satme-gp-vpn restaure || true
if [ -n "$VPN_CONF" ]; then
  dit "Clé Mullvad"
  /usr/local/sbin/satme-gp-vpn cle "$VPN_CONF"
fi

# ------------------------------------------------------------------ admin
if [ "$MISE_A_JOUR" -eq 0 ]; then
  dit "Compte d'administration « $ADMIN »"
  # runuser rather than sudo: a minimal container (Proxmox template) has no sudo.
  admin_cmd=(runuser -u "$UTILISATEUR" -- env PYTHONPATH="$CODE/app" SATME_GP_DATA="$DONNEES"
             "$CODE/venv/bin/python" -m satme_gp admin "$ADMIN")
  if [ "$MDP_STDIN" -eq 1 ]; then
    "${admin_cmd[@]}" --mot-de-passe-stdin
  elif [ "$NON_INTERACTIF" -eq 1 ]; then
    echo "Compte non créé (mode non interactif) : lancez plus tard"
    echo "  runuser -u $UTILISATEUR -- env PYTHONPATH=$CODE/app SATME_GP_DATA=$DONNEES $CODE/venv/bin/python -m satme_gp admin $ADMIN"
  else
    "${admin_cmd[@]}" </dev/tty
  fi
fi

systemctl restart $APP

# ------------------------------------------------------------------ caddy
. "$CONFIG/installation"
if [ "${MODE:-}" = caddy ]; then
  if ! command -v caddy >/dev/null; then
    dit "Installation de Caddy (dépôt officiel)"
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
      | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    chmod 644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
      > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy >/dev/null
  fi
  if [ "$MISE_A_JOUR" -eq 0 ]; then
    dit "Caddy pour $DOMAINE"
    [ -f /etc/caddy/Caddyfile ] && cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.avant-satme-gp.$(date +%s)"
    {
      [ -n "$EMAIL" ] && printf '{\n\temail %s\n}\n\n' "$EMAIL"
      cat <<EOF
$DOMAINE {
	encode gzip
	reverse_proxy 127.0.0.1:$(grep ^SATME_GP_PORT= "$ENVFILE" | cut -d= -f2)
}
EOF
    } > /etc/caddy/Caddyfile
  fi
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
  if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  fi
fi

# ---------------------------------------------------------------- summary
sleep 2
if systemctl is-active --quiet $APP; then
  dit "Le service tourne."
else
  journalctl -u $APP -n 20 --no-pager || true
  nie "le service ne démarre pas (journal ci-dessus)."
fi
. "$ENVFILE"
echo
if [ "${MODE:-}" = caddy ]; then
  echo "  Adresse publique : https://${DOMAINE}/"
  echo "  Administration   : https://${DOMAINE}/admin"
  echo "  (le certificat est obtenu à la première visite ; le domaine doit déjà pointer ici)"
else
  echo "  Le serveur écoute sur http://${SATME_GP_HOST}:${SATME_GP_PORT}/"
  echo "  À déclarer dans votre proxy, par exemple Nginx :"
  echo "      location / { proxy_pass http://ADRESSE_DE_CETTE_MACHINE:${SATME_GP_PORT};"
  echo "                   proxy_set_header Host \$host;"
  echo "                   proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;"
  echo "                   proxy_set_header X-Forwarded-Proto \$scheme; }"
  echo "  Si le proxy fait du HTTPS, mettez SATME_GP_HTTPS=1 dans $ENVFILE puis :"
  echo "      systemctl restart $APP"
fi
echo "  Journal          : journalctl -u $APP -f"
if [ -f "$CONFIG/mullvad.key" ]; then
  echo "  VPN Mullvad      : sortie à choisir dans l'administration (ou satme-gp-vpn sortie HOTE)"
else
  echo "  VPN Mullvad      : optionnel, sudo satme-gp-vpn cle FICHIER.conf (voir le README)"
fi
echo "  Mise à jour      : sudo $SOURCE/deploy/update.sh"
