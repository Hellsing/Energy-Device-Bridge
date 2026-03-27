# Changelog

## 1.0.6 - 2026-03-27

- Fixed broken test (37d8120)
- Cancel entry-scoped tasks and add cleanup service (b3bc3fe)
- Clear create-pending option on import outcomes (7075e17)
- Persist and use last source baseline for imports (734b96e)

## 1.0.5 - 2026-03-27

- Tolerate missing state_class; fix import & options (4007826)
- Improved statistics clearing before reinitialization (519685c)

## 1.0.4 - 2026-03-27

- Fixed import issue (420c4ed)
- Import short-term stats and manual reinitialization (4b85739)

## 1.0.3 - 2026-03-26

- Cleanup entity data on delete (ccf2dc9)

## 1.0.2 - 2026-03-26

- Refactor recorder cleanup and remove reconfigure flow (81baee9)
- Merge branch 'main' of https://github.com/Hellsing/Energy-Device-Bridge (36046be)
- Update release-manual.yml (ca075e2)

## 1.0.1 - 2026-03-26

- Add pending copy flag and clear recorder stats (5c92759)
- Merge branch 'main' of https://github.com/Hellsing/Energy-Device-Bridge (46e99e5)
- Fixed history import with 0 kWh entries after creation (c56c324)

## 1.0.0 - 2026-03-26

- Changed to manual release action (809e07f)
- Add manual release workflow and release-please (0b438db)
- Clear recorder stats on first import (99728cb)
- Prevent duplicate create-time history import (4e623f2)
- Added GitHub token usage (15eb738)
- Replay full source history on first import (699b9d6)
- Fixed manifest order (7806607)
- Add HA compatibility fixes and manifest updates (95e403f)
- Refactor services: integration-level and validations (8678bda)
- Fixed failing test (e4bed3f)
- Add source history import & replay into bridge (d58162e)
- Added new options to new device creation as well (5ed16b0)
- Add zero-drop policies and lower-value notifications (04d5ac8)
- Add maintenance actions, repairs, and services (fa2eb0f)
- Added more translations (650a315)
- Updated German translation (2544877)
- Made the power entity during setup and reconfig optional (08eb7ee)
- Fixed test regarding integration type (9934bc9)
- Fixed created devices not showing in the integrations tab (ea33f52)
- Fixed config flow tests (450cc41)
- Initial commit (6a3f709)

