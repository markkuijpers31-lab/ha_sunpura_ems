# Sunpura EMS — Home Assistant Custom Integration

Custom Home Assistant integration for the Sunpura S2400 home battery system.

**Author:** [@markkuijpers31-lab](https://github.com/markkuijpers31-lab)
**Repository:** [ha_sunpura_ems](https://github.com/markkuijpers31-lab/ha_sunpura_ems)

## Features

- Real-time energy flow monitoring (30-second polling)
- Battery state of charge and remaining energy
- Daily, monthly, yearly and all-time energy statistics
- AI energy mode control (General / Smart / Custom / Time of Use / Manual)
- Battery charge / discharge settings (power limits, SOC limits)
- Smart socket and EV charger switches
- Anti-reflux (zero feed-in) and CT clamp controls
- Local device discovery via Zeroconf
- Dutch and English entity descriptions

## Supported device

Sunpura S2400 (cloud API: `server-nj.ai-ec.cloud`)

## Setup

Add via **Settings → Integrations → Add Integration → Sunpura EMS**.
You will need your Sunpura cloud account username and password.
