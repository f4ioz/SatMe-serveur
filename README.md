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

Groupes par défaut :

- ceux de SatMe : `amsat` (bulletin AMSAT), `amateur`, `stations`, `visual`,
  `weather`, `cubesat` (CelesTrak) ;
- `satnogs` : tous les satellites suivis par [SatNOGS DB](https://db.satnogs.org)
  (environ 1 700), avec la source de TLE que SatNOGS retient pour chacun ;
- `supgp_iss` : éléments supplémentaires (SupGP) de CelesTrak pour l'ISS,
  calculés à partir des éphémérides de l'opérateur, souvent plus justes que le
  GP public.

La recherche par numéro prend, pour chaque satellite, l'élément le plus récent
parmi toutes les sources actives.

- **Trois formats de source**, reconnus seuls : OMM JSON (CelesTrak, SupGP,
  AMSAT), JSON TLE de SatNOGS DB, et TLE texte (avec ou sans ligne de nom). Les
  TLE sont convertis en OMM (somme de contrôle vérifiée, numéros Alpha-5
  compris) : SatMe reçoit toujours le même format.

- **Fichiers vérifiés** : un fichier qui n'est pas une liste OMM valable (page
  d'erreur HTML, liste vide, plus de 10 % d'éléments invalides) ne remplace pas
  la dernière bonne copie.
- **Récupération polie** : `User-Agent` explicite, `If-None-Match` /
  `If-Modified-Since` (un groupe inchangé coûte une réponse 304), jamais deux fois
  un groupe CelesTrak en deux heures, même après un échec.
- **Pause après un refus** : si CelesTrak répond 403 ou 429, plus aucune requête
  chez lui pendant 6 h (ou la durée de son `Retry-After`), groupes et numéros
  compris ; l'administration l'affiche. Les requêtes d'un même tour sont espacées
  de 3 s. Le bouton « Récupérer maintenant » ne force jamais CelesTrak.
- **Satellite hors groupes** : cherché chez CelesTrak par numéro, gardé en cache
  (6 h par défaut, jamais moins de 2 h), plafonné à 20 recherches par heure pour
  tous les clients. Un numéro hors des numéros possibles (au-delà du plus grand
  connu + 5 000, ou d'Alpha-5) n'est pas demandé.
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

### VPN Mullvad (optionnel)

Les requêtes du serveur vers les sources peuvent passer, le temps qu'il faut,
par un tunnel WireGuard Mullvad. Seules ces requêtes y passent : les visiteurs
arrivent toujours par le nom de domaine (Caddy, proxy ou port redirigé), leurs
réponses repartent par la connexion normale, et SSH comme le reste de la
machine ne sont pas touchés.

1. Sur mullvad.net : Compte → Configuration WireGuard → Linux, générer une clé,
   télécharger un fichier (n'importe quelle sortie : seules la clé et l'adresse
   servent). Chaque clé compte comme un appareil Mullvad.
2. Sur la machine : `sudo satme-gp-vpn cle mullvad.conf` (ou `--vpn-mullvad
   mullvad.conf` à l'installation, avec `install.sh` ou `proxmox-lxc.sh`).
3. Dans `/admin`, carte « VPN Mullvad » : les sorties en service, par pays et
   ville ; « Utiliser cette sortie », « Couper le tunnel », et « Tester », qui
   demande à Mullvad l'adresse vue d'Internet.

Tant qu'un tunnel est choisi, rien ne sort en direct : si le tunnel tombe, les
requêtes échouent au lieu de partir par la connexion normale. Le choix est
remis au démarrage. En ligne de commande : `satme-gp-vpn sortie HOTE`,
`arret`, `etat`, `oublie` (retire le tunnel et efface la clé).

Changer de sortie ne lève pas la pause CelesTrak. Quand le tunnel n'est plus
utile : « Couper le tunnel », puis `sudo satme-gp-vpn oublie` pour effacer la
clé (et la retirer des appareils sur mullvad.net). Sous Proxmox, le
module `wireguard` doit être chargé sur l'hôte (`proxmox-lxc.sh` s'en charge).

## Administration

`https://votre-domaine/admin` :

- **Sources** : état (ok, erreur, en attente), nombre de satellites, dernier
  succès, intervalle ; ajouter une source (identifiant, adresse https en OMM
  JSON, JSON SatNOGS ou TLE texte), l'activer ou la désactiver, la supprimer ; tout récupérer maintenant.
- **Réglages** : nom public, intervalle général, limite de requêtes par client,
  recherche par numéro chez la source (oui/non, durée de cache, plafond horaire,
  adresse), `User-Agent` envoyé aux sources.
- **VPN Mullvad**, s'il est configuré (voir plus haut).
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
Données : bulletin AMSAT, [CelesTrak](https://celestrak.org) (T.S. Kelso) et
[SatNOGS DB](https://db.satnogs.org) (Libre Space Foundation, licence CC BY-SA 4.0).
