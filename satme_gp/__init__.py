# SatMe GP server — orbital elements (OMM/GP) relay for SatMe.
# Copyright (C) 2025-2026  Olivier Gouyen (F4IOZ)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Relays orbital elements (OMM JSON, "GP data") to SatMe apps.

SatMe used to ask AMSAT and CelesTrak directly, from every phone. One server
fetching each group at most every two hours, then serving everyone from its
cache, spares the upstream sites and survives their outages.
"""

__version__ = "1.0.0"
