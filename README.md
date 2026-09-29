# Serveur GP SatMe

Relais d'**éléments orbitaux** (format GP / OMM JSON, la « nouvelle génération »
des TLE) pour l'application [SatMe](https://github.com/f4ioz/SatMe).

Au lieu que chaque téléphone interroge AMSAT et CelesTrak, ce serveur récupère
chaque groupe **au plus une fois toutes les deux heures**, vérifie les fichiers,
et sert tout le monde depuis sa copie. Les sites d'origine sont ménagés (CelesTrak
bloque les adresses trop insistantes), et une panne chez eux ne vide plus les
téléphones.

## Ce qu'il sert

Mêmes adresses que CelesTrak, format JSON seulement :

| Adresse | Contenu |
|---|---|
| `/gp.php?GROUP=amateur&FORMAT=json` | un groupe (aussi `/NORAD/elements/gp.php?…`) |
| `/gp.php?CATNR=25544&FORMAT=json` | un satellite par numéro NORAD |
| `/gp/amateur.json` | un groupe, adresse simple |
| `/gp/catnr/25544.json` | un satellite, adresse simple |
| `/gp/index.json` | liste des groupes, nombre de satellites, date |
| `/sante` | 200 si tous les groupes actifs ont moins d'un jour, 503 sinon |
| `/admin` | administration |

Groupes par défaut, ceux de SatMe : `amsat` (bulletin AMSAT), `amateur`,
`stations`, `visual`, `weather`, `cubesat` (CelesTrak).

- **Fichiers vérifiés** : un fichier qui n'est pas une liste OMM valable (page
  d'erreur HTML, liste vide, plus de 10 % d'éléments invalides) ne remplace pas
  la dernière bonne copie.
- **Récupération polie** : `User-Agent` explicite, `If-None-Match` /
  `If-Modified-Since` (un groupe inchangé coûte une réponse 304), jamais deux fois
  un groupe CelesTrak en deux heures, même après un échec.
- **Satellite hors groupes** : cherché chez CelesTrak par numéro, gardé en cache
  (6 h par défaut), plafonné à 60 recherches par heure pour tous les clients.
- **Côté clients** : `ETag`, `Last-Modified`, compression gzip, `Cache-Control`,
  et une limite de requêtes par adresse (120 par 10 min par défaut, réponse 429).

## Installation

Debian 12, Ubuntu ou Raspberry Pi OS (Bookworm). Python 3 seulement, sans base
de données à installer (SQLite).

```sh
git clone https://github.com/f4ioz/SatMe-serveur.git
cd SatMe-serveur
sudo ./deploy/install.sh
```

Le script demande comment le serveur sera joint :

1. **Caddy** installé sur la machine, certificat HTTPS automatique (Let's
   Encrypt). Il faut un nom de domaine qui pointe vers la machine, et les ports
   80 et 443 ouverts vers elle.
2. **Derrière votre proxy** (Nginx, Traefik, Nginx Proxy Manager…) : le serveur
   écoute en HTTP sur un port, vous le déclarez dans le proxy.

Sans questions :

```sh
sudo ./deploy/install.sh --mode caddy --domaine gp.exemple.org --email moi@exemple.org --admin f4ioz
sudo ./deploy/install.sh --mode proxy --ecoute 0.0.0.0 --port 8080 --admin f4ioz
```

Ce que fait le script : utilisateur système `satme-gp`, code dans
`/opt/satme-gp`, données dans `/var/lib/satme-gp`, réglages dans
`/etc/satme-gp/env`, service systemd `satme-gp` (durci : système de fichiers en
lecture seule sauf ses données), compte d'administration.

### Proxmox VE

À lancer **sur l'hôte Proxmox**, en root : crée un conteneur Debian 12 non
privilégié, y copie le serveur et l'installe.

```sh
git clone https://github.com/f4ioz/SatMe-serveur.git
cd SatMe-serveur
./deploy/proxmox-lxc.sh
# ou sans questions :
./deploy/proxmox-lxc.sh --ip 192.168.1.50/24 --passerelle 192.168.1.1 --mode proxy --admin f4ioz
```

Options : `--ctid`, `--nom`, `--stockage` (local-lvm), `--stockage-modeles`
(local), `--pont` (vmbr0), `--ip dhcp|ADRESSE/MASQUE`, `--passerelle`,
`--memoire` (512 Mo), `--disque` (4 Go), `--coeurs` (1), `--cle-ssh FICHIER.pub`.
Le mot de passe d'administration est demandé sur l'hôte ; celui de root du
conteneur est tiré au hasard et affiché à la fin.

## Administration

`https://votre-domaine/admin` :

- **Sources** : état (ok, erreur, en attente), nombre de satellites, dernier
  succès, intervalle ; ajouter une source (identifiant, adresse OMM JSON en
  https), l'activer ou la désactiver, la supprimer ; tout récupérer maintenant.
- **Réglages** : nom public, intervalle général, limite de requêtes par client,
  recherche par numéro chez la source (oui/non, durée de cache, plafond horaire,
  adresse), `User-Agent` envoyé aux sources.
- **Requêtes** des sept derniers jours.
- **Mot de passe** (10 caractères au moins).

Connexion protégée : 5 essais par quart d'heure et par adresse, jeton sur chaque
formulaire, cookie de session `HttpOnly`, `SameSite=Strict`, `Secure` en HTTPS.

Nouveau mot de passe si l'ancien est perdu :

```sh
sudo runuser -u satme-gp -- env PYTHONPATH=/opt/satme-gp/app SATME_GP_DATA=/var/lib/satme-gp \
     /opt/satme-gp/venv/bin/python -m satme_gp admin NOM
```

## Mise à jour, sauvegarde

```sh
cd SatMe-serveur && sudo ./deploy/update.sh               # machine ou Raspberry Pi
./deploy/proxmox-lxc.sh --mise-a-jour --ctid 120           # conteneur Proxmox, depuis l'hôte
```

Réglages, compte et données sont gardés. Pour sauvegarder : `/var/lib/satme-gp`
(base SQLite et fichiers des groupes) et `/etc/satme-gp`.

Journal : `journalctl -u satme-gp -f`.

## Développement

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
SATME_GP_DATA=data .venv/bin/python -m satme_gp admin moi
SATME_GP_DATA=data .venv/bin/python -m satme_gp serve      # http://127.0.0.1:8080
```

## Licence

GPL version 2 ou ultérieure, comme SatMe. Voir `LICENSE`.
Données : bulletin AMSAT et [CelesTrak](https://celestrak.org) (T.S. Kelso).
