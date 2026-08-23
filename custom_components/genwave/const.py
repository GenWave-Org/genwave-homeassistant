"""Constants for the GenWave integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "genwave"

# genwave.announce (SPEC F147.3) — mirrors POST /api/announcements 1:1.
SERVICE_ANNOUNCE: Final = "announce"

ATTR_MESSAGE: Final = "message"
ATTR_VERBATIM: Final = "verbatim"
ATTR_TTL_SECONDS: Final = "ttl_seconds"
ATTR_VOICE: Final = "voice"

# The AnnouncementRequest.Verbatim wire default (db/40's own column default) — the integration
# repeats it here only so the service field is optional, never as a client-side opinion.
DEFAULT_VERBATIM: Final = False

# SPEC F143.1's fixed per-request ttlSeconds bound — repeated here so the service schema rejects
# an out-of-range value before it ever reaches the wire, matching the server's own fixed law
# (never settings-tunable, so this constant is safe to hardcode).
MIN_TTL_SECONDS: Final = 60
MAX_TTL_SECONDS: Final = 3600

# GenWaveApiClient's per-request timeout. Modest and fixed — no retries in v1 (api.py's own
# remarks): the server already owns its own caps (rate limit, pending depth), and HA's own
# service-call semantics already give the caller a clear failure to react to.
API_TIMEOUT_SECONDS: Final = 10
