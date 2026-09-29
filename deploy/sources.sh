# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Sourced by install.sh and proxmox-lxc.sh: the question "which sources?".
# The list comes from satme_gp/store.py (SOURCES_DEFAUT, one entry per line),
# so it needs no Python on the machine that asks.

# sources_defaut DIR → "id|nom|url" lines, then the lookup by number.
sources_defaut() {
  sed -n 's/^    ("\([a-z0-9_]*\)", "\([^"]*\)", "\([^"]*\)"),.*/\1|\2|\3/p' "$1/satme_gp/store.py"
  echo "numero|Recherche d'un satellite hors groupes, par numéro|https://celestrak.org/"
}

# choisis_sources DIR VARIABLE: asks, and puts "id,id,…" in VARIABLE.
choisis_sources() {
  local dir=$1 var=$2 i=0 ids=() tout=() sans=() id nom url r n
  echo "  Sources interrogées par le serveur :"
  while IFS='|' read -r id nom url; do
    i=$((i + 1)); ids+=("$id"); tout+=("$id")
    if [[ "$url" == *celestrak.* ]]; then
      printf '    %d  %-10s %s  (CelesTrak)\n' "$i" "$id" "$nom"
    else
      printf '    %d  %-10s %s\n' "$i" "$id" "$nom"; sans+=("$id")
    fi
  done < <(sources_defaut "$dir")
  echo "  Entrée = toutes ; s = toutes sauf CelesTrak ; ou les numéros à garder (ex. 1 7)."
  while :; do
    read -r -p "  Choix : " r </dev/tty
    case "$r" in
      "") printf -v "$var" '%s' "$(IFS=,; echo "${tout[*]}")"; return;;
      s|S) printf -v "$var" '%s' "$(IFS=,; echo "${sans[*]}")"; return;;
    esac
    local choix=() ok=1
    for n in $r; do
      if [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le "$i" ]; then choix+=("${ids[$((n - 1))]}")
      else echo "  « $n » : numéro entre 1 et $i attendu."; ok=0; fi
    done
    [ "$ok" -eq 1 ] && [ "${#choix[@]}" -gt 0 ] && { printf -v "$var" '%s' "$(IFS=,; echo "${choix[*]}")"; return; }
  done
}
