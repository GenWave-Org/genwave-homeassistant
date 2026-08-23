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

# The now-playing sensor's poll interval (STORY-362 AC3, SPEC F147.3, gh-#558's volume lesson).
# 30s is the floor, not a tunable default: the server's own per-IP "announcements-door" limiter
# (RateLimiterPolicies.Announcements) admits 60 requests/min per IP — the same door
# `genwave.announce` and the config flow's validation read share. That's a distinct concept from
# AnnouncementAcceptedRateLimiter's separate, station-wide 6/min *accepted* cap (SPEC F143.4),
# which this GET-only poll never touches at all. A 30s interval spends 2 of those 60 requests/min
# on the door alone, leaving 58/min of headroom for setup-time reads, service calls, and manual
# reloads from the same IP — comfortably inside the door's budget even on a busy automation day.
NOW_PLAYING_POLL_INTERVAL_SECONDS: Final = 30
