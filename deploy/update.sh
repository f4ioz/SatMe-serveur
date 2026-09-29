#!/usr/bin/env bash
# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Updates an installed server: fetches the latest code (git clone) and
# replaces it, keeping settings, admin account and data.
#
#   sudo ./deploy/update.sh
set -euo pipefail
ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -d "$ICI/.git" ]; then
  echo "==> git pull"
  # The clone may belong to another user than root.
  proprio=$(stat -c %U "$ICI")
  runuser -u "$proprio" -- git -C "$ICI" pull --ff-only
fi
exec "$ICI/deploy/install.sh" --mise-a-jour
