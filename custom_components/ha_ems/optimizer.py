"""Battery schedule optimizer for the Sunpura S2400.

Reads Tibber/Nordpool prices, forecast.solar prognoses, historical consumption
from MariaDB, and Cupra EV state from HA — then generates an optimal list of
up to 16 controlTime slot dicts for push_schedule().

controlTime format (decoded Fase 0, 24 Feb 2026):
    enabled, startTime, endTime, powerW, 0, 6, 0, 0, 0, maxSOC, minSOC

    powerW: signed integer in Watts
        negative → charge from grid  (e.g. -2400)
        positive → discharge/feed    (e.g. +2400)

Usage (from a service handler):
    from .optimizer import BatteryOptimizer
    optimizer = BatteryOptimizer(hass, hub)
    slots = await optimizer.optimize()
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.dt import now as dt_now

_LOGGER = logging.getLogger(__name__)

# Sunpura S2400 defaults (used when API values are unavailable)
_DEFAULT_MAX_CHARGE_W = 2400
_DEFAULT_MAX_DISCHARGE_W = 2400


# ---------------------------------------------------------------------------
# Helper: group consecutive hours into runs
# ---------------------------------------------------------------------------

def _group_consecutive_hours(hours: list[int]) -> list[list[int]]:
    """Group a sorted list of hours into consecutive runs.

    Example: [0, 1, 2, 5, 6] → [[0, 1, 2], [5, 6]]
    """
    if not hours:
        return []
    groups: list[list[int]] = []
    current: list[int] = [hours[0]]
    for h in hours[1:]:
        if h == current[-1] + 1:
            current.append(h)
        else:
            groups.append(current)
            current = [h]
    groups.append(current)
    return groups


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------

class BatteryOptimizer:
    """Generates an optimal charge/discharge schedule for the Sunpura S2400.

    The optimizer follows a three-mode strategy controlled by
    input_select.battery_optimizer_mode:

        Prijs-arbitrage  — charge at cheapest grid hours, discharge at
                           most expensive hours, maximising financial return.
        Zelfverbruik     — only discharge at very high price peaks; rely on
                           solar for self-consumption.
        Gebalanceerd     — combination: cheap grid + solar, discharge at peaks.
        Uit              — return empty list (no schedule pushed).
    """

    def __init__(self, hass: HomeAssistant, hub=None) -> None:
        self.hass = hass
        self.hub = hub

    # ------------------------------------------------------------------
    # HA state helpers
    # ------------------------------------------------------------------

    def _state_float(self, entity_id: str, default: float = 0.0) -> float:
        """Read an entity state as float; return default on error."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return default
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return default

    def _state_str(self, entity_id: str, default: str = "") -> str:
        """Read an entity state as string."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return default
        return state.state

    def _attr_value(self, entity_id: str, attr: str, default: Any = None) -> Any:
        """Read an entity attribute."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return default
        return state.attributes.get(attr, default)

    # ------------------------------------------------------------------
    # Read max charge/discharge power from hub AI settings
    # ------------------------------------------------------------------

    def _max_charge_w(self) -> int:
        """Return maxChargePower in Watts (from hub AI settings or default)."""
        if self.hub is not None:
            obj = (self.hub.data.get("ai_system_times_with_energy_mode") or {}).get("obj") or {}
            val = obj.get("maxChargePower")
            if val is not None:
                try:
                    return int(val)
                except (TypeError, ValueError):
                    pass
        return _DEFAULT_MAX_CHARGE_W

    def _max_discharge_w(self) -> int:
        """Return maxFeedPower in Watts (from hub AI settings or default)."""
        if self.hub is not None:
            obj = (self.hub.data.get("ai_system_times_with_energy_mode") or {}).get("obj") or {}
            val = obj.get("maxFeedPower")
            if val is not None:
                try:
                    return int(val)
                except (TypeError, ValueError):
                    pass
        return _DEFAULT_MAX_DISCHARGE_W

    # ------------------------------------------------------------------
    # Price data
    # ------------------------------------------------------------------

    def _get_tibber_prices(self) -> list[tuple[int, float]]:
        """Return (hour_of_day, price_eur_kwh) for the next 24 h from Tibber.

        Tibber sensor attributes:
          - ``prices_today``   : list of dicts with 'startsAt' and 'total'
          - ``prices_tomorrow``: same format for the next day

        Falls back to Nordpool if no Tibber sensor is found.
        Falls back to a flat 0.25 €/kWh if neither is available.
        """
        now = dt_now()
        now_hour = now.hour

        # --- Try Tibber ---
        tibber_entities = [
            eid for eid in self.hass.states.async_entity_ids("sensor")
            if "tibber" in eid and "price" in eid
        ]
        for eid in tibber_entities:
            state = self.hass.states.get(eid)
            if state is None:
                continue
            attrs = state.attributes
            today = attrs.get("prices_today") or []
            tomorrow = attrs.get("prices_tomorrow") or []
            all_prices: list[dict] = list(today) + list(tomorrow)
            if not all_prices:
                continue
            result: list[tuple[int, float]] = []
            for i in range(24):
                idx = now_hour + i
                if idx >= len(all_prices):
                    break
                entry = all_prices[idx]
                price = float(entry.get("total") or entry.get("price") or 0.25)
                hour = (now_hour + i) % 24
                result.append((hour, price))
            if result:
                _LOGGER.debug("Tibber prices loaded from %s (%d hours)", eid, len(result))
                return result

        # --- Try Nordpool ---
        nordpool_entities = [
            eid for eid in self.hass.states.async_entity_ids("sensor")
            if "nordpool" in eid
        ]
        for eid in nordpool_entities:
            state = self.hass.states.get(eid)
            if state is None:
                continue
            attrs = state.attributes
            raw_today = attrs.get("raw_today") or []
            raw_tomorrow = attrs.get("raw_tomorrow") or []
            all_raw = list(raw_today) + list(raw_tomorrow)
            if not all_raw:
                continue
            result = []
            for i in range(24):
                idx = now_hour + i
                if idx >= len(all_raw):
                    break
                entry = all_raw[idx]
                price = float(entry.get("value") or 0.25) if isinstance(entry, dict) else float(entry)
                hour = (now_hour + i) % 24
                result.append((hour, price))
            if result:
                _LOGGER.debug("Nordpool prices loaded from %s (%d hours)", eid, len(result))
                return result

        _LOGGER.warning("No Tibber/Nordpool price data found — using flat 0.25 €/kWh")
        return [((now_hour + i) % 24, 0.25) for i in range(24)]

    # ------------------------------------------------------------------
    # Solar forecast
    # ------------------------------------------------------------------

    def _get_solar_forecast(self) -> dict[int, float]:
        """Return {hour: expected_kwh} for the next 24 h from forecast.solar.

        forecast.solar stores hourly production in the ``wh_period`` attribute
        as {ISO-datetime-string: watt_hours}.
        """
        forecast: dict[int, float] = {h: 0.0 for h in range(24)}

        forecast_entities = [
            eid for eid in self.hass.states.async_entity_ids("sensor")
            if "energy_production" in eid or "forecast_solar" in eid
        ]
        for eid in forecast_entities:
            state = self.hass.states.get(eid)
            if state is None:
                continue
            wh_period = state.attributes.get("wh_period") or {}
            if not wh_period:
                wh_period = state.attributes.get("watts_hours_period") or {}
            if wh_period:
                for dt_str, wh in wh_period.items():
                    try:
                        dt = datetime.fromisoformat(str(dt_str))
                        forecast[dt.hour] = forecast.get(dt.hour, 0.0) + float(wh) / 1000.0
                    except (ValueError, TypeError):
                        pass
                _LOGGER.debug("Solar forecast loaded from %s", eid)
                break

        return forecast

    # ------------------------------------------------------------------
    # Historical consumption from MariaDB
    # ------------------------------------------------------------------

    async def _get_hourly_consumption(self) -> list[float]:
        """Return [h0..h23] average kWh consumed per hour (last 30 days).

        Uses the HA recorder statistics API.  Falls back to 0.5 kWh/h
        if no data is available.
        """
        default = [0.5] * 24

        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )

            end_time = dt_now()
            start_time = end_time - timedelta(days=30)

            candidate_ids = {
                "sensor.sunpura_s2400_home_power",
                "sensor.sunpura_s2400_load_power",
            }
            recorder = get_instance(self.hass)
            stats = await recorder.async_add_executor_job(
                statistics_during_period,
                self.hass,
                start_time,
                end_time,
                candidate_ids,
                "hour",
                None,
                {"mean"},
            )

            data_points: list[Any] = []
            for eid in candidate_ids:
                if stats.get(eid):
                    data_points = stats[eid]
                    _LOGGER.debug(
                        "Consumption history loaded from %s (%d points)", eid, len(data_points)
                    )
                    break

            if not data_points:
                _LOGGER.warning(
                    "No consumption history found for %s — using defaults", candidate_ids
                )
                return default

            hourly_sums = [0.0] * 24
            hourly_counts = [0] * 24
            for point in data_points:
                h = point["start"].hour
                mean_w = point.get("mean") or 0.0
                hourly_sums[h] += float(mean_w) / 1000.0  # W → kW (= kWh/h)
                hourly_counts[h] += 1

            return [
                (hourly_sums[h] / hourly_counts[h]) if hourly_counts[h] > 0 else 0.5
                for h in range(24)
            ]

        except Exception as exc:
            _LOGGER.warning("Could not fetch consumption history: %s", exc)
            return default

    # ------------------------------------------------------------------
    # Main optimizer
    # ------------------------------------------------------------------

    async def optimize(self) -> list[dict]:
        """Run optimisation and return a list of up to 16 slot dicts.

        Each slot dict is suitable for hub.push_schedule() and contains:
            enabled (bool), start (HH:MM), end (HH:MM),
            power_w (int Watts: negative=charge, positive=discharge),
            max_soc (int %), min_soc (int %).

        Returns an empty list when scheduling is disabled.
        """
        # --- User settings ---
        optimizer_mode = self._state_str(
            "input_select.battery_optimizer_mode", "Gebalanceerd"
        )
        schedule_active = self._state_str(
            "input_boolean.battery_schedule_active", "on"
        )

        if schedule_active != "on" or optimizer_mode == "Uit":
            _LOGGER.info(
                "Battery optimizer disabled (mode=%s, active=%s)", optimizer_mode, schedule_active
            )
            return []

        reserve_soc = self._state_float("input_number.battery_reserve_soc", 15.0)
        ev_reserve_soc = self._state_float("input_number.battery_ev_reserve_soc", 20.0)
        high_price = self._state_float("input_number.battery_high_price_threshold", 0.25)
        low_price = self._state_float("input_number.battery_low_price_threshold", 0.08)

        # Find the battery SOC entity dynamically (entity ID may have _2 suffix)
        soc_entity = next(
            (
                eid for eid in self.hass.states.async_entity_ids("sensor")
                if "sunpura" in eid and "battery_soc" in eid
            ),
            "sensor.sunpura_s2400_battery_soc",
        )
        current_soc = self._state_float(soc_entity, 50.0)
        _LOGGER.debug("Battery SOC: %.0f%% (entity=%s)", current_soc, soc_entity)

        # --- Power limits (from hub API settings) ---
        max_charge_w = self._max_charge_w()
        max_discharge_w = self._max_discharge_w()

        # --- Cupra EV ---
        ev_charging = False
        cupra_power_entities = [
            eid for eid in self.hass.states.async_entity_ids("sensor")
            if "cupra" in eid and "charging_power" in eid
        ]
        if cupra_power_entities:
            ev_power = self._state_float(cupra_power_entities[0], 0.0)
            ev_charging = ev_power > 0.0
            _LOGGER.debug(
                "Cupra charging_power=%.0f W (entity=%s)", ev_power, cupra_power_entities[0]
            )

        effective_reserve = reserve_soc + (ev_reserve_soc if ev_charging else 0.0)

        # --- Data ---
        prices = self._get_tibber_prices()           # [(hour, eur/kWh), ...]
        solar = self._get_solar_forecast()            # {hour: kWh}
        consumption = await self._get_hourly_consumption()   # [kWh] per hour of day

        _LOGGER.info(
            "Optimizer: mode=%s, reserve=%.0f%%, ev=%s, high=%.2f, low=%.2f, "
            "max_charge=%dW, max_discharge=%dW",
            optimizer_mode, effective_reserve, ev_charging,
            high_price, low_price, max_charge_w, max_discharge_w,
        )

        # --- Classify each hour ---
        charge_hours: list[int] = []
        discharge_hours: list[int] = []

        for hour, price in prices:
            net_solar = max(0.0, solar.get(hour, 0.0) - consumption[hour % 24])

            if optimizer_mode == "Prijs-arbitrage":
                if price <= low_price and net_solar < 0.1:
                    charge_hours.append(hour)
                elif price >= high_price:
                    discharge_hours.append(hour)

            elif optimizer_mode == "Zelfverbruik":
                # Discharge only at very high prices and sufficient SOC
                if price >= high_price and current_soc > effective_reserve + 10:
                    discharge_hours.append(hour)

            else:  # Gebalanceerd (default)
                if price <= low_price and net_solar < 0.1:
                    charge_hours.append(hour)
                elif price >= high_price and net_solar < 0.5:
                    discharge_hours.append(hour)

        # Deduplicate and sort
        charge_hours = sorted(set(charge_hours))
        discharge_hours = sorted(set(h for h in set(discharge_hours) if h not in charge_hours))

        # --- Build slot list (max 16 total) ---
        slots: list[dict] = []

        for group in _group_consecutive_hours(charge_hours):
            if len(slots) >= 14:
                break
            end_hour = (group[-1] + 1) % 24
            slots.append({
                "enabled": True,
                "start": f"{group[0]:02d}:00",
                "end": f"{end_hour:02d}:00",
                "power_w": -max_charge_w,   # negative = charge from grid
                "max_soc": 95,
                "min_soc": int(effective_reserve),
            })

        for group in _group_consecutive_hours(discharge_hours):
            if len(slots) >= 14:
                break
            end_hour = (group[-1] + 1) % 24
            slots.append({
                "enabled": True,
                "start": f"{group[0]:02d}:00",
                "end": f"{end_hour:02d}:00",
                "power_w": max_discharge_w,  # positive = discharge/feed
                "max_soc": 100,
                "min_soc": int(effective_reserve),
            })

        # --- EV-specific overnight charge (up to 2 extra slots) ---
        if ev_charging and optimizer_mode != "Uit" and len(slots) < 15:
            night_prices = sorted(
                [(h, p) for h, p in prices if h >= 22 or h < 6],
                key=lambda x: x[1],
            )
            ev_hours = sorted({h for h, _ in night_prices[:3]})
            for group in _group_consecutive_hours(ev_hours):
                if len(slots) >= 15:
                    break
                covered = any(
                    s["power_w"] < 0 and _hour_in_slot(group[0], s)
                    for s in slots
                )
                if not covered:
                    end_hour = (group[-1] + 1) % 24
                    slots.append({
                        "enabled": True,
                        "start": f"{group[0]:02d}:00",
                        "end": f"{end_hour:02d}:00",
                        "power_w": -(max_charge_w // 2),  # half power for EV top-up
                        "max_soc": int(min(95, reserve_soc + ev_reserve_soc + 15)),
                        "min_soc": int(reserve_soc),
                    })

        _LOGGER.info("Optimizer produced %d slot(s)", len(slots))
        for i, s in enumerate(slots):
            direction = "charge" if s["power_w"] < 0 else "discharge"
            _LOGGER.debug(
                "  Slot %d: %s-%s %s %dW soc=%d-%d%%",
                i + 1, s["start"], s["end"], direction,
                abs(s["power_w"]), s["min_soc"], s["max_soc"],
            )

        return slots


def _hour_in_slot(hour: int, slot: dict) -> bool:
    """Return True if the given hour falls within the slot's time range."""
    try:
        start_h = int(slot["start"].split(":")[0])
        end_h = int(slot["end"].split(":")[0])
        if start_h <= end_h:
            return start_h <= hour < end_h
        # Wrap-around (e.g. 23:00 → 01:00)
        return hour >= start_h or hour < end_h
    except (KeyError, ValueError, IndexError):
        return False
