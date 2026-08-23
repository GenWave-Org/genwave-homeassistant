# genwave-homeassistant
Your smart home speaks through your radio station. Home Assistant integration for GenWave - an announce service and notify platform that put your automations on air in your DJ's voice, a now-playing sensor for your dashboards, and starter blueprints to import.

## Status

The `genwave.announce` service, the `notify` platform, and the config flow are here (config
entry setup + validation). The now-playing sensor and the starter blueprints ship in later
tasks of the same epic.

## Requirements

- Home Assistant **2025.3.0** or newer. Older cores are missing APIs this integration is built
  on (`AddConfigEntryEntitiesCallback`, `ConfigFlow._get_reauth_entry`) — `hacs.json` states the
  same floor, so HACS won't offer an install on an older core.
- A running GenWave station with **`Admin:Enabled` set to `true`**. GenWave's `[AdminSurface]`
  posture 404s the *entire* announcements family — including the endpoint this integration
  calls — when the admin control plane is disabled. If `Admin:Enabled` is `false` on the box,
  this integration has nothing to talk to; there is no separate "public" door for it.
- An announce token, generated from the GenWave station's own Announcements page. The token is
  shown **once** at generation time (GenWave never stores or re-displays the plaintext) — copy it
  immediately into this integration's config flow.
- Network reachability from Home Assistant to the GenWave station's URL.

## Installation

### HACS (custom repository)

1. HACS -> Integrations -> the `...` menu -> **Custom repositories**.
2. Add `https://github.com/GenWave-Org/genwave-homeassistant` as an **Integration**.
3. Install "GenWave" from HACS, then restart Home Assistant.

A HACS default-store listing is not a v1 goal — the custom-repository route above is the
supported install path for now.

### Manual

Copy `custom_components/genwave` into your Home Assistant config directory's
`custom_components/` folder, then restart Home Assistant.

## Configuration

Settings -> Devices & Services -> Add Integration -> **GenWave**.

- **GenWave URL** - your station's base URL (e.g. `https://station.example.com`).
- **Announce token** - generated on the station's Announcements page (session-authed; the token
  itself never mints or reads its own status - only an admin session can do that).

The config flow validates both fields live, via the same now-playing read the sensor will later
poll, **before** the entry is created. A bad URL or a bad/revoked token comes back as a form
error on this step, not a broken entry to debug afterward.

If GenWave later revokes or regenerates the token out from under this integration, the entry
raises a **reauthentication** prompt the next time a poll or a service call hits a 401 - never a
silent failure. Generate a fresh token on the station's Announcements page and paste it into the
reauth form.

### Transport note

The announce token is a bearer credential and crosses the network on every call this integration
makes. Point it at an `https://` URL, or keep the connection on a trusted LAN / VPN / tunnel you
already control - GenWave does not add its own transport encryption, and neither does this
integration.

## The `genwave.announce` service

Maps 1:1 onto GenWave's `POST /api/announcements` — this integration adds no semantics of its
own beyond snake_case-to-camelCase field naming. The server owns every cap (message length,
accepted-rate limit, pending-queue depth); when it declines a message, the service call fails
with the server's own reason, verbatim.

| Field | Required | Type | Notes |
|---|---|---|---|
| `message` | yes | string | Up to 280 characters (the station's own hard limit). |
| `verbatim` | no | boolean | Default `false` (flavored - the on-air persona works the message into character, always preserving its own words). `true` speaks the message exactly as written. |
| `ttl_seconds` | no | number | 60-3600. How long the message stays eligible to air before expiring unspoken. Defaults to the station's own 900s when omitted. |
| `voice` | no | string | A specific station voice, if the station has more than one. Defaults to the station's own voice. |

```yaml
service: genwave.announce
data:
  message: "The dryer just finished"
  verbatim: false
  ttl_seconds: 600
```

## The `notify` platform

A `notify.send_message` target is set up alongside the config entry, for automations already
built around `notify.*`. It speaks flavored (`verbatim: false`) through the same client the
`announce` service uses - there is no second write path. `title`, when given, is folded into the
message text (GenWave's wire contract has no separate title field).

## Running the tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest tests/
.venv/bin/ruff check custom_components/
```

Proven on Python 3.13.3 against `homeassistant==2026.2.3` /
`pytest-homeassistant-custom-component==0.13.316` (`requirements_test.txt` pins both).

## Latency, honestly

GenWave announcements air in the **next scheduled break** - a minutes-scale delay, by design
(loudness-matched, crossfaded, never-silent broadcast means nothing interrupts a track mid-play).
This is deliberately **not** a doorbell-class urgent-notification channel; nothing in this
integration's starter blueprint set assumes sub-minute latency, and none should be built that
does until GenWave grows an urgent-class delivery path of its own.
