# Energy Device Bridge

[![HACS](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![Tests](https://img.shields.io/github/actions/workflow/status/Hellsing/energy-device-bridge/validate.yml?label=Tests&style=for-the-badge)](https://github.com/Hellsing/energy-device-bridge/actions/workflows/validate.yml)

Energy Device Bridge is a Home Assistant helper integration that keeps an Energy Dashboard total continuous when the underlying source meter resets, rolls over, or gets replaced.

Each config entry creates one virtual device with:
- an optional virtual power sensor (live passthrough when a source power sensor is configured)
- a virtual cumulative energy sensor (non-decreasing total in kWh)

## Features

- UI-based setup through Home Assistant config entries
- Stable entity unique IDs per configured consumer
- Configuration flow for changing sources/name from the integration gear menu
- Persistent energy accumulation state across reloads and restarts
- Source energy normalization to kWh (`Wh` and `kWh` supported)
- Maintenance actions to adopt baseline, reset tracker, or set virtual total
- Configurable lower-source handling policies for zero drops and lower non-zero anomalies
- Built-in Repairs issues for missing/invalid source sensor conditions
- Diagnostics download with redacted user/source details

## Installation

### HACS

1. Open HACS and go to **Integrations**.
2. Search for **Energy Device Bridge**.
3. Select **Download**.
4. Restart Home Assistant.
5. Go to **Settings > Devices & Services > Add Integration**.
6. Add **Energy Device Bridge**.

### Manual (advanced)

1. Copy `custom_components/energy_device_bridge` into your Home Assistant `config/custom_components` directory.
2. Restart Home Assistant.
3. Add **Energy Device Bridge** from **Settings > Devices & Services**.

## Configuration

During setup, provide:
- **Consumer name**
- **Source power sensor** (optional, must be a sensor with `W` or `kW` when configured)
- **Source energy sensor** (must be a sensor with `Wh` or `kWh`)

If setup inputs need to change later, use the integration **Configure** gear icon.

## Entity behavior

- **Power sensor**: mirrors current source power, normalized to `kW` (only created when a source power sensor is configured).
- **Energy sensor**: stores a virtual cumulative total in `kWh` and only adds positive deltas from the source.

Accumulation logic:
1. Ignore invalid states (`unknown`, `unavailable`, `none`, non-numeric).
2. Convert source energy to `kWh`.
3. Treat the first valid value as baseline.
4. Add only positive deltas to the virtual total.
5. Never decrease the virtual total when the source drops lower.

Lower-source drop behavior is configurable in the integration options:
- **Zero-drop policy** (`zero_drop_policy`)
  - `accept_zero_as_new_cycle` (default, backward-compatible): when source drops to `0`, baseline is immediately set to `0` and future positive deltas accumulate from `0`.
  - `ignore_zero_until_non_zero`: when source drops to `0`, baseline is not changed; the first later non-zero reading becomes the new baseline without adding a transition delta.
- **Notify on lower non-zero** (`notify_on_lower_non_zero`)
  - If enabled, lower non-zero drops create/update one deterministic persistent notification per config entry.
  - If disabled (default), no persistent notification is shown.

Maintenance actions:
- **Adopt current source as baseline**: stores the current valid source value as baseline without changing virtual total.
- **Reset tracker**: clears baseline/metadata and sets virtual total to `0 kWh`.
- **Set virtual total**: sets the virtual total explicitly (kWh), then refreshes baseline from current source when possible.

You can run these actions from Home Assistant services (`energy_device_bridge.*`) or from the integration's button entities.

Examples:
- **Transient zero from flaky source**: use `ignore_zero_until_non_zero` to avoid anchoring baseline to temporary zeros.
- **Daily-reset meter**: use `accept_zero_as_new_cycle` so accumulation naturally continues from each reset cycle.
- **Unexpected lower non-zero after replacement/reboot**: baseline is adopted to the new lower value, virtual total is preserved, and optional notification can alert you.

## Data updates

- Event-driven updates based on source sensor state changes
- No polling loop is used by bridge entities
- Storage writes are coalesced/debounced during rapid source updates
- Persisted tracker state is restored on startup/reload

## Reauthentication and reconfigure

- **Reauthentication**: not applicable (the integration does not use account credentials or tokens).
- **Reconfigure**: supported and intended for updating required setup values.

## Diagnostics and debug logging

- Download diagnostics from the integration menu in Home Assistant.
- Sensitive fields are redacted before export.

To enable debug logging:

```yaml
logger:
  logs:
    custom_components.energy_device_bridge: debug
```

## Troubleshooting

- Ensure selected source entities are `sensor` entities.
- Confirm source units are supported (`W`/`kW` for power, `Wh`/`kWh` for energy).
- If no values appear, verify source entities are available and numeric.
- Check **Repairs** for actionable runtime issues when source entities disappear or become invalid.

## Known limitations

- Exactly one power source and one energy source are mapped per config entry.
- Integrations that expose energy in unsupported units must be normalized upstream before selection.

## Contributing

Issues and pull requests are welcome at:
[https://github.com/Hellsing/energy-device-bridge](https://github.com/Hellsing/energy-device-bridge)
