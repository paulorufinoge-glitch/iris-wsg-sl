"""Zone / status classification by EXPLICIT lookup table. Stdlib only.

Deliberately no substring, regex or prefix rules: a value such as "Zone III (aufgehoben)" or
"IIIB-Entwurf" must NOT be silently classified as III / IIIB. Anything not listed in the
configured value map becomes UNKNOWN (raw value is preserved for review).
"""
from __future__ import annotations

from .config import UNKNOWN, normalize_token

# Restrictiveness ranking for screening (1 = most restrictive). Ordering of IIIA/IIIB and the
# placement of unqualified III are my understanding of German practice -- confirm with the LUA.
ZONE_RANK = {"ZONE_I": 1, "ZONE_II": 2, "ZONE_IIIA": 3, "ZONE_III": 3, "ZONE_IIIB": 4}


def map_value(raw, table: dict) -> tuple[str, bool]:
    """Return (canonical, matched). raw=None -> (UNKNOWN, False). Exact lookup only."""
    if raw is None:
        return UNKNOWN, False
    hit = table.get(normalize_token(raw))
    if hit is None:
        return UNKNOWN, False
    return hit, True
