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
- `amsat_tle` : le bulletin TLE d'AMSAT, qui accompagne son bulletin JSON et
  se trouve parfois plus frais (l'ISS du jour quand le JSON avait neuf jours) ;
- `supgp_iss` : éléments supplémentaires (SupGP) de CelesTrak pour l'ISS,
  calculés à partir des éphémérides de l'opérateur, souvent plus justes que le
  GP public.

La recherche par numéro prend, pour chaque satellite, l'élément **du moment**
parmi toutes les sources actives : l'époque la plus récente qui n'est pas plus
d'une heure dans le futur (le SupGP de l'ISS publie des segments de prévision
jusqu'à deux semaines en avance), sinon la plus proche.

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

## Garde-fou et connexions

- **Bannissement automatique** (24 h par défaut) d'une adresse qui se fait
  refuser 3 fois en une heure pour excès, qui demande 30 pages inexistantes en
  10 minutes, ou qui cherche une seule fois une page de site connu pour ses
  failles (`/.env`, `/wp-login.php`, `.git`…). Les réponses 404 des fichiers
  d'éléments (groupe désactivé, numéro inconnu) ne comptent pas. Les adresses du
  réseau local ne sont jamais bannies automatiquement : derrière un proxy qui ne
  transmet pas `X-Forwarded-For`, tout le monde aurait l'adresse du proxy.
- **Option « réservé à SatMe »** : les fichiers d'éléments seulement pour
  l'application (reconnue à son `User-Agent`, `SatMe/20.73 (Android 14)`) ; elle
  écarte les robots ordinaires, pas un imitateur.
- **Waitress** : 200 connexions simultanées au plus, 30 s d'inactivité.
- **Page « Connexions »** (`/admin/connexions`) : sur 24 h, 7 ou 30 jours,
  requêtes, adresses, part de SatMe, refus ; par pays (drapeau, nom) ; par
  adresse (pays, SatMe et sa version ou autre client, requêtes, refus, première
  et dernière visite, bouton Bannir) ; adresses bannies (Débannir, bannir à la
  main) ; réglages du garde-fou. Un avertissement s'affiche si toutes les
  requêtes semblent venir du proxy.
- **Page « SatMe connectés »** (`/admin/satme`) : SatMe (20.73 et suivantes)
  n'envoie que des marqueurs publics, son nom et sa version et celle d'Android
  (`SatMe/20.73 (Android 14)`) : ni numéro, ni indicatif. Le serveur distingue
  les téléphones **dans la journée** par une empreinte (adresse et marqueurs)
  salée d'un secret tiré chaque jour et effacé le lendemain : impossible de
  relier deux jours ou de retrouver une adresse. La page montre les SatMe du
  jour, un graphique par jour sur 30 jours, la moyenne sur 7 jours, et la
  répartition par version de SatMe, d'Android et par pays. Seuls ces totaux
  sont gardés (365 jours, réglable).
- **Pays** : base gratuite *IP to Country Lite* de DB-IP (CC BY 4.0), téléchargée
  par le serveur au démarrage puis chaque mois.
- **Données personnelles** : les adresses des clients sont gardées 7 jours par
  défaut (réglable), puis effacées.

## Installation

Debian 12, Ubuntu ou Raspberry Pi OS (Bookworm). Python 3 seulement, sans base
de données à installer (SQLite).

```sh
git clone https://github.com/f4ioz/SatMe-serveur.git
cd SatMe-serveur
sudo ./deploy/install.sh
```

Le script demande quelles sources interroger (toutes, toutes sauf CelesTrak,
ou une liste : `--sources amsat,satnogs,numero` sans question ; `numero` est
la recherche d'un satellite hors groupes chez CelesTrak). Elles se changent
ensuite dans l'administration. Il demande aussi comment le serveur sera joint :

1. **Caddy** installé sur la machine, certificat HTTPS automatique (Let's
   Encrypt). Il faut un nom de domaine qui pointe vers la machine, et les ports
   80 et 443 ouverts vers elle.
2. **Derrière votre proxy** (Nginx, Traefik, Nginx Proxy Manager…) : le serveur
   écoute en HTTP sur un port, vous le déclarez dans le proxy.

Sans questions :

```sh
sudo ./deploy/install.sh --mode caddy --domaine gp.exemple.org --email moi@exemple.org --admin f4ioz
sudo ./deploy/install.sh --mode proxy --ecoute 0.0.0.0 --port 8080 --domaine gp.exemple.org --https --admin f4ioz
```

`--https` : le proxy placé devant sert le domaine en HTTPS (cookie
d'administration réservé à HTTPS).

Ce que fait le script : utilisateur système `satme-gp`, code dans
`/opt/satme-gp`, données dans `/var/lib/satme-gp`, réglages dans
`/etc/satme-gp/env`, service systemd `satme-gp` (durci : système de fichiers en
lecture seule sauf ses données), compte d'administration.

### Proxmox VE

À lancer **sur l'hôte Proxmox**, en root. Le script pose toutes les questions,
avec une valeur proposée à chaque fois, montre un récapitulatif, puis crée un
conteneur Debian 12 non privilégié (premier numéro libre), y installe le
serveur et donne ce qu'il faut déclarer dans votre proxy. Sans rien cloner :

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/f4ioz/SatMe-serveur/main/deploy/proxmox-lxc.sh)"
```

Questions : numéro (premier libre), nom, stockages (ceux de l'hôte sont
listés), disque, mémoire, cœurs, pont, adresse IP (fixe conseillée) et
passerelle, clés SSH ; accès (Caddy dans le conteneur, ou derrière votre proxy
avec le nom de domaine et HTTPS) ; compte d'administration ; sources
interrogées ; VPN Mullvad (dès maintenant avec le fichier `.conf` de l'hôte,
ou plus tard depuis l'administration).

`--essai` montre ce qui serait fait sans rien toucher. Chaque réponse peut aussi
être donnée en option (`--ctid`, `--ip`, `--mode`, `--domaine`, `--vpn-mullvad`…,
voir `--help`). Mise à jour d'un conteneur existant :

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/f4ioz/SatMe-serveur/main/deploy/proxmox-lxc.sh)" _ --mise-a-jour --ctid 120
```

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
2. Dans `/admin`, carte « VPN Mullvad » : envoyer ce fichier (ou coller son
   contenu) et choisir le pays de sortie. Le serveur installe la clé, prend une
   sortie en service dans ce pays, monte le tunnel et le teste. Le fichier
   envoyé est effacé ; la clé n'est lisible que par root.
3. Ensuite, dans la même carte : autre sortie (par pays et ville), « Couper le
   tunnel », « Tester » (adresse vue d'Internet, selon Mullvad), « Retirer le
   VPN » (tunnel retiré, clé effacée).

En ligne de commande : `sudo satme-gp-vpn cle mullvad.conf` puis `sortie fr`
(pays, ville `fr-par` ou relais `fr-par-wg-001` ; `liste` montre pays et
villes), `arret`, `etat`, `oublie`. À l'installation : `--vpn-mullvad
mullvad.conf --vpn-sortie fr`, le tunnel est alors monté avant la première
récupération.

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
Localisation des adresses : [DB-IP](https://db-ip.com) (CC BY 4.0).
Données : bulletin AMSAT, [CelesTrak](https://celestrak.org) (T.S. Kelso) et
[SatNOGS DB](https://db.satnogs.org) (Libre Space Foundation, licence CC BY-SA 4.0).
