# Energy Device Bridge

[![HACS](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![Tests](https://img.shields.io/github/actions/workflow/status/Hellsing/energy-device-bridge/validate.yml?label=Tests&style=for-the-badge)](https://github.com/Hellsing/energy-device-bridge/actions/workflows/validate.yml)

Energy Device Bridge is a Home Assistant helper integration that keeps an Energy Dashboard total continuous when the underlying source meter resets, rolls over, or gets replaced.

Each config entry creates one virtual device with:
- a virtual power sensor (live passthrough of the selected source power sensor)
- a virtual cumulative energy sensor (non-decreasing total in kWh)

## Features

- UI-based setup through Home Assistant config entries
- Stable entity unique IDs per configured consumer
- Reconfigure flow for changing the source entities and consumer name
- Persistent energy accumulation state across reloads and restarts
- Source energy normalization to kWh (`Wh` and `kWh` supported)
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
- **Source power sensor** (must be a sensor with `W` or `kW`)
- **Source energy sensor** (must be a sensor with `Wh` or `kWh`)

If setup inputs need to change later, use **Reconfigure** from the integration entry.

## Entity behavior

- **Power sensor**: mirrors current source power, normalized to `kW`.
- **Energy sensor**: stores a virtual cumulative total in `kWh` and only adds positive deltas from the source.

Accumulation logic:
1. Ignore invalid states (`unknown`, `unavailable`, `none`, non-numeric).
2. Convert source energy to `kWh`.
3. Treat the first valid value as baseline.
4. Add only positive deltas to the virtual total.
5. Ignore negative deltas (source reset/rollover) so the virtual total never decreases.

## Data updates

- Event-driven updates based on source sensor state changes
- No polling loop is used by bridge entities
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

## Known limitations

- Exactly one power source and one energy source are mapped per config entry.
- Integrations that expose energy in unsupported units must be normalized upstream before selection.

## Contributing

Issues and pull requests are welcome at:
[https://github.com/Hellsing/energy-device-bridge](https://github.com/Hellsing/energy-device-bridge)
