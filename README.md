# Energy Device Bridge

[![HACS](https://img.shields.io/badge/HACS-Default-41BDF5?style=flat-square&logo=homeassistant&logoColor=white)](https://hacs.xyz/)
[![Home Assistant >= 2025.1.0](https://img.shields.io/badge/Home%20Assistant-2025.1.0%2B-18BCF2?style=flat-square&logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![Version](https://img.shields.io/badge/dynamic/json?style=flat-square&label=version&query=%24.version&url=https%3A%2F%2Fraw.githubusercontent.com%2FHellsing%2Fenergy-device-bridge%2Fmain%2Fcustom_components%2Fenergy_device_bridge%2Fmanifest.json)](./custom_components/energy_device_bridge/manifest.json)
[![License MIT](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](./LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/Hellsing/energy-device-bridge/validate.yml?style=flat-square&label=tests)](https://github.com/Hellsing/energy-device-bridge/actions/workflows/validate.yml)
[![Hassfest](https://img.shields.io/github/actions/workflow/status/Hellsing/energy-device-bridge/hassfest.yml?style=flat-square&label=hassfest)](https://github.com/Hellsing/energy-device-bridge/actions/workflows/hassfest.yml)
[![HACS validation](https://img.shields.io/github/actions/workflow/status/Hellsing/energy-device-bridge/hacs.yml?style=flat-square&label=hacs)](https://github.com/Hellsing/energy-device-bridge/actions/workflows/hacs.yml)

Creates persistent virtual power/energy entities so your Energy Dashboard totals stay continuous when the source meter resets, rolls over, or is replaced.

This custom integration builds a virtual consumer in Home Assistant from your source sensors. The virtual energy sensor is monotonic (`kWh`) and preserves accumulated totals across restarts and source drops, so long-term energy tracking remains stable.

## Key highlights

- Config entry setup in the UI (no YAML setup required).
- Virtual energy sensor that only accumulates positive source deltas.
- Optional virtual power passthrough sensor, normalized to `kW`.
- Built-in maintenance actions to adopt baseline, reset, set total, and import history.
- Optional one-time source history replay into bridge statistics on creation.
- Runtime Repairs issues and diagnostics with redacted sensitive fields.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Hellsing&repository=energy-device-bridge&category=integration)

1. Open HACS, then go to **Integrations**.
2. Search for **Energy Device Bridge**.
3. Select **Download**.
4. Restart Home Assistant.
5. Go to **Settings > Devices & Services > Add Integration**.
6. Select **Energy Device Bridge**.

### Manual install (advanced)

1. Copy `custom_components/energy_device_bridge` into your Home Assistant `config/custom_components` directory.
2. Restart Home Assistant.
3. Add the integration in **Settings > Devices & Services**.

## Configuration

Configure each virtual consumer from the UI:

| Field | Required | Notes |
|---|---|---|
| Consumer name | Yes | Display name for the created device/entities. |
| Source energy sensor | Yes | Must be a `sensor` with unit `Wh` or `kWh`. |
| Source power sensor | No | Optional `sensor` with unit `W` or `kW`. |
| Source zero-drop handling | Yes | `accept_zero_as_new_cycle` (default) or `ignore_zero_until_non_zero`. |
| Notify on lower non-zero | Yes | Creates a persistent notification when a lower non-zero source value is detected. |
| Copy/import source history on create | Yes (default enabled) | Replays available source history and imports bridge statistics once after creation. |

You can update configuration later from the integration gear menu (**Configure**) or options menu.

## What it creates

Each config entry creates one Home Assistant device and these entities:

| Type | Entity | Created when |
|---|---|---|
| Sensor | `Energy` (`kWh`, `state_class: total_increasing`) | Always |
| Sensor | `Power` (`kW`, `state_class: measurement`) | Only if source power sensor is configured |
| Button | `Adopt current source as baseline` | Always |
| Button | `Reset tracker` | Always |
| Button | `Import source history` | Always |

Services exposed by the integration:

- `energy_device_bridge.adopt_current_source_as_baseline` (targets bridge energy sensor entities)
- `energy_device_bridge.reset_tracker` (targets bridge energy sensor entities)
- `energy_device_bridge.set_virtual_total` (targets bridge energy sensor entities, requires `value_kwh`)
- `energy_device_bridge.import_source_history` (requires `config_entry_id`)

## Behavior

- The bridge listens to source sensor state changes (event-driven, no polling).
- Source energy is normalized to `kWh`.
- First valid sample becomes baseline.
- Positive deltas are added to the virtual total.
- Lower source values never decrease the virtual total.

Reset/rollover handling:

- `accept_zero_as_new_cycle` (default): a drop to zero is accepted immediately as the new baseline.
- `ignore_zero_until_non_zero`: zero is ignored until a later non-zero reading appears, then that reading becomes the new baseline.
- Lower non-zero drops are treated as reset/replacement events: baseline is updated, total is preserved, and optional notification can be shown.

History import:

- Uses Home Assistant recorder/history/statistics APIs.
- Replays retained source history through the same bridge logic used for live updates.
- Imports hourly statistics rows for the bridge energy sensor.
- If older raw history is already purged by recorder retention, it cannot be reconstructed.

## Troubleshooting and limitations

- Source entities must be `sensor` entities with supported units (`Wh`/`kWh`, optional `W`/`kW`).
- If the source energy `state_class` is not `total` or `total_increasing`, the integration raises a Repair issue.
- If source sensors are missing or invalid, check **Settings > Repairs** for actionable warnings.
- One bridge entry maps to one source energy sensor and an optional source power sensor.
- Reauthentication is not applicable (no external account or token auth).

## Support

- Report issues: [GitHub Issues](https://github.com/Hellsing/energy-device-bridge/issues)
- Documentation: [README](./README.md)
- Download diagnostics from the integration menu before opening an issue.

Optional debug logging:

```yaml
logger:
  logs:
    custom_components.energy_device_bridge: debug
```
