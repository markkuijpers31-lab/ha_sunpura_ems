# Sunpura EMS — Home Assistant Custom Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2023.9%2B-blue.svg)](https://www.home-assistant.io/)
[![Version](https://img.shields.io/badge/version-2.1.0-green.svg)](https://github.com/markkuijpers31-lab/ha_sunpura_ems/releases)

Custom Home Assistant integration for the **Sunpura S2400** home battery system. Connects to the Sunpura cloud API to provide real-time energy monitoring and full control over battery behaviour directly from Home Assistant.

> **Written with the assistance of [Claude Code](https://claude.ai/code) by Anthropic.**

---

## Features

- **Real-time monitoring** — solar power, grid power, battery power, home consumption (30-second polling)
- **Per-string PV data** — individual PV1 / PV2 string power
- **Battery status** — state of charge (SOC), remaining energy, charge/discharge power
- **Daily statistics** — solar, grid import/export, battery charge/discharge, load (today)
- **Period statistics** — monthly, yearly and all-time breakdowns per energy source
- **Energy mode control** — switch between General, Smart (AI), Custom, Time of Use, Manual
- **Battery charge/discharge settings** — max charge power, max feed-in power, discharge power, min/max SOC
- **AI switches** — Smart Link mode, Basic Discharge, Anti-Reflux (zero feed-in), CT Clamp
- **Smart devices** — switches for connected smart sockets and EV chargers
- **Local device discovery** — automatic detection of local devices via Zeroconf/mDNS
- **Multi-language** — full support for 🇳🇱 Dutch, 🇬🇧 English, 🇩🇪 German, 🇫🇷 French

---

## Supported Device

| Device | Cloud API |
|--------|-----------|
| Sunpura S2400 | `server-nj.ai-ec.cloud:8443` |

---

## Prerequisites

- Home Assistant **2023.9.0** or newer
- A **Sunpura cloud account** (the same credentials used in the Sunpura app)
- The Sunpura S2400 must be connected to the internet and registered in the cloud

---

## Installation

### Option A — Via HACS (recommended)

[HACS](https://hacs.xyz/) must be installed in your Home Assistant instance.

1. Open HACS in the Home Assistant sidebar.
2. Click the **three dots** (⋮) in the top-right corner and choose **Custom repositories**.
3. Enter the repository URL:
   ```
   https://github.com/markkuijpers31-lab/ha_sunpura_ems
   ```
4. Set the category to **Integration** and click **Add**.
5. Search for **Sunpura EMS** in HACS and click **Download**.
6. Restart Home Assistant.

### Option B — Manual installation

1. Download the latest release from the [Releases page](https://github.com/markkuijpers31-lab/ha_sunpura_ems/releases) (or clone the repository).
2. Copy the `custom_components/ha_ems` folder to your Home Assistant configuration directory:
   ```
   /config/custom_components/ha_ems/
   ```
   The result should look like:
   ```
   config/
   └── custom_components/
       └── ha_ems/
           ├── __init__.py
           ├── manifest.json
           ├── sensor.py
           └── ...
   ```
3. Restart Home Assistant.

---

## Configuration

1. Go to **Settings → Integrations** and click **+ Add Integration**.
2. Search for **Sunpura EMS** and click it.
3. Enter your Sunpura cloud account **username** and **password**.
4. Select your **installation** from the list (if you have multiple).
5. Click **Submit** — the integration will connect and create all entities automatically.

> **Note:** Your password is hashed (MD5) before being stored, consistent with the Sunpura app protocol.

---

## Entities

### Sensors — Real-time (updated every 30 seconds)

| Entity | Description | Unit |
|--------|-------------|------|
| Solar Power | Total output of all solar panels | W |
| PV1 Power | Power from PV string 1 | W |
| PV2 Power | Power from PV string 2 | W |
| Grid Power | Grid exchange (+ import / − export) | W |
| Battery Power | Battery charge (+) or discharge (−) | W |
| AC Charge Power | Power charging battery from AC grid | W |
| Home Power | Total home consumption | W |
| Load Power | Connected load power | W |
| Total Load Power | All loads combined | W |
| Battery SOC | State of charge | % |
| Battery Remaining Energy | Usable energy remaining in battery | kWh |
| Solar Today | Solar energy generated today | kWh |
| Grid Import Today | Energy imported from grid today | kWh |
| Grid Export Today | Energy exported to grid today | kWh |
| Battery Charge Today | Energy charged into battery today | kWh |
| Battery Discharge Today | Energy discharged from battery today | kWh |
| Load Today | Load energy consumed today | kWh |
| Today Energy | Total system production today | kWh |
| Month Energy | Total system production this month | kWh |
| Year Energy | Total system production this year | kWh |
| Total Energy | Total system production all-time | kWh |
| CO2 Savings | Estimated CO₂ avoided | kg |

### Sensors — Statistics (updated every 5 minutes)

Monthly, yearly and all-time breakdowns for:
- Solar generation
- Grid import / export
- Battery charge / discharge

### Switches

| Entity | Description |
|--------|-------------|
| Smart Link Mode | Enable/disable AI smart optimisation |
| Basic Discharge | Enable/disable fixed-rate battery discharge |
| Anti-Reflux (Zero Feed-in) | Block energy export to the grid |
| CT Clamp Enable | Enable/disable CT clamp current sensor |
| *(device switches)* | One switch per connected smart socket or EV charger |

### Select

| Entity | Options |
|--------|---------|
| Energy Mode | General / Smart (AI) / Custom / Time of Use / Manual |

### Number controls

| Entity | Range | Description |
|--------|-------|-------------|
| Max Charge Power | 10 – 2400 W | Maximum battery charging power |
| Max Feed-in Power | 10 – 2400 W | Maximum power exported to grid |
| Discharge Power | 0 – 800 W | Battery discharge power (basic mode) |
| Min Discharge SOC | 0 – 100 % | Stop discharging below this SOC |
| Max Charge SOC | 0 – 100 % | Stop charging above this SOC |

---

## Diagnostic Sensors

Two hidden diagnostic sensors are included (visible in Developer Tools → States and in entity settings under *Diagnostic*):

| Sensor | Description |
|--------|-------------|
| API Discovery (realtime) | All raw fields from the homeCountData endpoint as attributes |
| API Discovery (slow data) | All raw fields from energy/AI endpoints as attributes |

These are useful for troubleshooting or identifying new field names returned by the API.

---

## Troubleshooting

**Entities show "unavailable"**
- The cloud API may not return a specific field for your device configuration. Check the **API Discovery (realtime)** diagnostic sensor to see which fields are actually present.
- Old entities from a previous version may appear as unavailable. Remove them via **Settings → Entities**, filter on *Unavailable*, and delete.

**"Cannot connect" during setup**
- Verify your username and password in the Sunpura app.
- Check that your Sunpura device has an active internet connection.
- Home Assistant will automatically retry — the integration uses `ConfigEntryNotReady` to handle transient cloud outages.

**Energy Mode shows "Unknown"**
- Your device may report an energy mode not yet mapped. Check `ai.energyMode` in the **API Discovery (slow data)** sensor and open an issue.

---

## Polling intervals

| Data type | Interval |
|-----------|----------|
| Real-time energy flow | 30 seconds |
| Statistics, AI settings, device list | 5 minutes |

---

## Contributing

Issues and pull requests are welcome at [github.com/markkuijpers31-lab/ha_sunpura_ems](https://github.com/markkuijpers31-lab/ha_sunpura_ems).

If your device returns different field names than expected, please share the output of the **API Discovery** diagnostic sensors in a new issue — this helps improve coverage.

---

## Credits

This integration was developed with the assistance of **[Claude Code](https://claude.ai/code)**, an AI-powered coding tool by [Anthropic](https://www.anthropic.com/).

**Author:** [@markkuijpers31-lab](https://github.com/markkuijpers31-lab)

---

## Disclaimer

This is an unofficial, community-developed integration. It is not affiliated with, endorsed by, or supported by Sunpura or its parent company. Use at your own risk.
