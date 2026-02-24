"""Battery schedule optimizer for the Sunpura S2400.

Price sources (priority order):
  1. ENTSO-E (hass-entso-e) — 15-minute resolution, preferred
  2. Tibber                  — hourly, expanded to 15-min
  3. Nordpool                — hourly, expanded to 15-min
  4. Flat 0.25 €/kWh fallback

The optimizer works at 15-minute granularity throughout and produces slot
dicts with HH:MM start/end times compatible with hub.push_schedule().

controlTime format (decoded Fase 0, 24 Feb 2026):
    enabled, startTime, endTime, powerW, 0, 6, 0, 0, 0, maxSOC, minSOC
    powerW: signed Watts (negative = charge from grid, positive = discharge)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.dt import now as dt_now

_LOGGER = logging.getLogger(__name__)

_DEFAULT_MAX_CHARGE_W = 2400
_DEFAULT_MAX_DISCHARGE_W = 2400


# ---------------------------------------------------------------------------
# Quarter-hour helpers
# ---------------------------------------------------------------------------

def _next_quarter(h: int, m: int) -> tuple[int, int]:
    """Return the quarter-hour that is 15 minutes after (h, m)."""
    m += 15
    if m >= 60:
        m -= 60
        h = (h + 1) % 24
    return h, m


def _quarter_to_str(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


def _expand_hourly_to_quarters(
    prices: list[tuple[int, float]],
) -> list[tuple[int, int, float]]:
    """Expand (hour, price) list to (hour, minute, price) at 15-min resolution."""
    result: list[tuple[int, int, float]] = []
    for hour, price in prices:
        for minute in (0, 15, 30, 45):
            result.append((hour, minute, price))
    return result


def _group_consecutive_quarters(
    quarters: list[tuple[int, int]],
) -> list[list[tuple[int, int]]]:
    """Group a sorted list of (h, m) quarter-hour tuples into consecutive runs."""
    if not quarters:
        return []
    groups: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = [quarters[0]]
    for q in quarters[1:]:
        expected = _next_quarter(*current[-1])
        if q == expected:
            current.append(q)
        else:
            groups.append(current)
            current = [q]
    groups.append(current)
    return groups


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------

class BatteryOptimizer:
    """Generates optimal charge/discharge schedule for the Sunpura S2400.

    Mode (input_select.battery_optimizer_mode):
        Prijs-arbitrage  — charge at cheapest hours, discharge at most expensive
        Zelfverbruik     — discharge only at high price peaks; solar first
        Gebalanceerd     — balanced: cheap grid + discharge at peaks
        Uit              — disabled, returns empty list
    """

    def __init__(self, hass: HomeAssistant, hub=None) -> None:
        self.hass = hass
        self.hub = hub

    # ------------------------------------------------------------------
    # HA state helpers
    # ------------------------------------------------------------------

    def _state_float(self, entity_id: str, default: float = 0.0) -> float:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return default
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return default

    def _state_str(self, entity_id: str, default: str = "") -> str:
        state = self.hass.states.get(entity_id)
        return state.state if state else default

    def _attr(self, entity_id: str, attr: str, default: Any = None) -> Any:
        state = self.hass.states.get(entity_id)
        return state.attributes.get(attr, default) if state else default

    # ------------------------------------------------------------------
    # Hub power limits
    # ------------------------------------------------------------------

    def _max_charge_w(self) -> int:
        if self.hub is not None:
            obj = (self.hub.data.get("ai_system_times_with_energy_mode") or {}).get("obj") or {}
            try:
                return int(obj["maxChargePower"])
            except (KeyError, TypeError, ValueError):
                pass
        return _DEFAULT_MAX_CHARGE_W

    def _max_discharge_w(self) -> int:
        if self.hub is not None:
            obj = (self.hub.data.get("ai_system_times_with_energy_mode") or {}).get("obj") or {}
            try:
                return int(obj["maxFeedPower"])
            except (KeyError, TypeError, ValueError):
                pass
        return _DEFAULT_MAX_DISCHARGE_W

    # ------------------------------------------------------------------
    # Price sources — all return list[tuple[int, int, float]]
    #   = [(hour, minute, price_eur_kwh), ...] for next ~24 h
    # ------------------------------------------------------------------

    def _get_entsoe_prices(self) -> list[tuple[int, int, float]] | None:
        """Read 15-minute prices from hass-entso-e integration.

        Detects the sensor by looking for the 'prices_today' attribute with
        ENTSO-E attribution.  Returns None if not found / no data.
        """
        now = dt_now()

        for eid in self.hass.states.async_entity_ids("sensor"):
            state = self.hass.states.get(eid)
            if state is None:
                continue
            attrs = state.attributes
            # Detect ENTSO-E sensor by attribution or entity_id pattern
            attribution = attrs.get("attribution", "")
            if "ENTSO" not in attribution and "entsoe" not in eid.lower():
                continue
            today: list[dict] = attrs.get("prices_today") or []
            tomorrow: list[dict] = attrs.get("prices_tomorrow") or []
            if not today:
                continue

            result: list[tuple[int, int, float]] = []
            for entry in today + tomorrow:
                try:
                    t = datetime.fromisoformat(str(entry["time"]))
                    if t < now - timedelta(minutes=15):
                        continue
                    result.append((t.hour, t.minute, float(entry["price"])))
                except (KeyError, ValueError, TypeError):
                    continue

            if result:
                _LOGGER.debug(
                    "ENTSO-E prices from %s: %d quarter-slots (%.3f–%.3f €/kWh)",
                    eid, len(result),
                    min(p for _, _, p in result),
                    max(p for _, _, p in result),
                )
                return result[:96]  # max 24 h

        return None

    def _get_tibber_prices(self) -> list[tuple[int, int, float]] | None:
        """Read hourly Tibber prices, expanded to 15-min resolution."""
        now_hour = dt_now().hour
        for eid in self.hass.states.async_entity_ids("sensor"):
            if "tibber" not in eid or "price" not in eid:
                continue
            state = self.hass.states.get(eid)
            if state is None:
                continue
            today: list = state.attributes.get("prices_today") or []
            tomorrow: list = state.attributes.get("prices_tomorrow") or []
            all_prices = today + tomorrow
            if not all_prices:
                continue
            hourly: list[tuple[int, float]] = []
            for i in range(24):
                idx = now_hour + i
                if idx >= len(all_prices):
                    break
                entry = all_prices[idx]
                price = float(entry.get("total") or entry.get("price") or 0.25)
                hourly.append(((now_hour + i) % 24, price))
            if hourly:
                _LOGGER.debug("Tibber prices from %s: %d hours", eid, len(hourly))
                return _expand_hourly_to_quarters(hourly)
        return None

    def _get_nordpool_prices(self) -> list[tuple[int, int, float]] | None:
        """Read hourly Nordpool prices, expanded to 15-min resolution."""
        now_hour = dt_now().hour
        for eid in self.hass.states.async_entity_ids("sensor"):
            if "nordpool" not in eid:
                continue
            state = self.hass.states.get(eid)
            if state is None:
                continue
            raw_today = state.attributes.get("raw_today") or []
            raw_tomorrow = state.attributes.get("raw_tomorrow") or []
            all_raw = raw_today + raw_tomorrow
            if not all_raw:
                continue
            hourly: list[tuple[int, float]] = []
            for i in range(24):
                idx = now_hour + i
                if idx >= len(all_raw):
                    break
                entry = all_raw[idx]
                price = float(entry.get("value") or 0.25) if isinstance(entry, dict) else float(entry)
                hourly.append(((now_hour + i) % 24, price))
            if hourly:
                _LOGGER.debug("Nordpool prices from %s: %d hours", eid, len(hourly))
                return _expand_hourly_to_quarters(hourly)
        return None

    def _get_prices(self) -> list[tuple[int, int, float]]:
        """Return prices as (hour, minute, eur_kwh) for next ~24 h.

        Priority: ENTSO-E → Tibber → Nordpool → flat 0.25 €/kWh fallback.
        """
        prices = self._get_entsoe_prices()
        if prices:
            return prices
        prices = self._get_tibber_prices()
        if prices:
            return prices
        prices = self._get_nordpool_prices()
        if prices:
            return prices

        _LOGGER.warning("No price data found — using flat 0.25 €/kWh fallback")
        now = dt_now()
        result: list[tuple[int, int, float]] = []
        h, m = now.hour, (now.minute // 15) * 15
        for _ in range(96):
            result.append((h, m, 0.25))
            h, m = _next_quarter(h, m)
        return result

    # ------------------------------------------------------------------
    # Solar forecast
    # ------------------------------------------------------------------

    def _get_solar_forecast(self) -> dict[int, float]:
        """Return {hour: expected_kwh} from forecast.solar."""
        forecast: dict[int, float] = {}
        for eid in self.hass.states.async_entity_ids("sensor"):
            if "energy_production" not in eid and "forecast_solar" not in eid:
                continue
            state = self.hass.states.get(eid)
            if state is None:
                continue
            wh_period = (
                state.attributes.get("wh_period")
                or state.attributes.get("watts_hours_period")
                or {}
            )
            if not wh_period:
                continue
            for dt_str, wh in wh_period.items():
                try:
                    dt = datetime.fromisoformat(str(dt_str))
                    forecast[dt.hour] = forecast.get(dt.hour, 0.0) + float(wh) / 1000.0
                except (ValueError, TypeError):
                    pass
            if forecast:
                _LOGGER.debug("Solar forecast from %s", eid)
                break
        return forecast

    # ------------------------------------------------------------------
    # Historical consumption from MariaDB
    # ------------------------------------------------------------------

    async def _get_hourly_consumption(self) -> list[float]:
        """Return [h0..h23] average kWh/h from last 30 days (MariaDB)."""
        default = [0.5] * 24
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistics_during_period

            end_time = dt_now()
            start_time = end_time - timedelta(days=30)
            candidate_ids = {
                "sensor.sunpura_s2400_home_power",
                "sensor.sunpura_s2400_load_power",
            }
            recorder = get_instance(self.hass)
            stats = await recorder.async_add_executor_job(
                statistics_during_period,
                self.hass, start_time, end_time,
                candidate_ids, "hour", None, {"mean"},
            )
            data_points: list[Any] = []
            for eid in candidate_ids:
                if stats.get(eid):
                    data_points = stats[eid]
                    _LOGGER.debug("Consumption history: %d points from %s", len(data_points), eid)
                    break
            if not data_points:
                _LOGGER.debug("No consumption history — using 0.5 kWh/h default")
                return default
            sums = [0.0] * 24
            counts = [0] * 24
            for pt in data_points:
                h = pt["start"].hour
                sums[h] += float(pt.get("mean") or 0.0) / 1000.0
                counts[h] += 1
            return [(sums[h] / counts[h]) if counts[h] else 0.5 for h in range(24)]
        except Exception as exc:
            _LOGGER.warning("Could not load consumption history: %s", exc)
            return default

    # ------------------------------------------------------------------
    # Main optimizer
    # ------------------------------------------------------------------

    async def optimize(self) -> list[dict]:
        """Return up to 16 slot dicts for hub.push_schedule().

        Each slot: {enabled, start (HH:MM), end (HH:MM), power_w, max_soc, min_soc}
        Returns [] when scheduling is disabled.
        """
        mode = self._state_str("input_select.battery_optimizer_mode", "Gebalanceerd")
        active = self._state_str("input_boolean.battery_schedule_active", "on")

        if active != "on" or mode == "Uit":
            _LOGGER.info("Optimizer disabled (mode=%s, active=%s)", mode, active)
            return []

        reserve_soc = self._state_float("input_number.battery_reserve_soc", 15.0)
        ev_reserve_soc = self._state_float("input_number.battery_ev_reserve_soc", 20.0)
        high_price = self._state_float("input_number.battery_high_price_threshold", 0.25)
        low_price = self._state_float("input_number.battery_low_price_threshold", 0.08)

        # Battery SOC — find entity dynamically (may have _2 suffix)
        soc_entity = next(
            (eid for eid in self.hass.states.async_entity_ids("sensor")
             if "sunpura" in eid and "battery_soc" in eid),
            None,
        )
        current_soc = self._state_float(soc_entity, 50.0) if soc_entity else 50.0

        # Cupra EV
        ev_charging = False
        cupra_eid = next(
            (eid for eid in self.hass.states.async_entity_ids("sensor")
             if "cupra" in eid and "charging_power" in eid),
            None,
        )
        if cupra_eid:
            ev_charging = self._state_float(cupra_eid, 0.0) > 0.0

        effective_reserve = reserve_soc + (ev_reserve_soc if ev_charging else 0.0)
        max_charge_w = self._max_charge_w()
        max_discharge_w = self._max_discharge_w()

        # Price + forecast data
        prices = self._get_prices()  # [(h, m, eur/kWh), ...]
        solar = self._get_solar_forecast()  # {hour: kWh}
        consumption = await self._get_hourly_consumption()  # [kWh/h] × 24

        _LOGGER.info(
            "Optimizer: mode=%s, reserve=%.0f%%, ev=%s, "
            "thresholds=%.3f/%.3f, SOC=%.0f%%, "
            "charge=%dW, discharge=%dW, price_slots=%d",
            mode, effective_reserve, ev_charging,
            low_price, high_price, current_soc,
            max_charge_w, max_discharge_w, len(prices),
        )

        # Classify each 15-min quarter
        charge_quarters: list[tuple[int, int]] = []
        discharge_quarters: list[tuple[int, int]] = []

        for h, m, price in prices:
            net_solar = max(0.0, solar.get(h, 0.0) / 4 - consumption[h] / 4)

            if mode == "Prijs-arbitrage":
                if price <= low_price and net_solar < 0.025:
                    charge_quarters.append((h, m))
                elif price >= high_price:
                    discharge_quarters.append((h, m))

            elif mode == "Zelfverbruik":
                if price >= high_price and current_soc > effective_reserve + 10:
                    discharge_quarters.append((h, m))

            else:  # Gebalanceerd
                if price <= low_price and net_solar < 0.025:
                    charge_quarters.append((h, m))
                elif price >= high_price and net_solar < 0.125:
                    discharge_quarters.append((h, m))

        # Deduplicate and sort
        charge_quarters = sorted(set(charge_quarters))
        discharge_set = set(discharge_quarters) - set(charge_quarters)
        discharge_quarters = sorted(discharge_set)

        # Build slots
        slots: list[dict] = []

        for group in _group_consecutive_quarters(charge_quarters):
            if len(slots) >= 14:
                break
            end_h, end_m = _next_quarter(*group[-1])
            slots.append({
                "enabled": True,
                "start": _quarter_to_str(*group[0]),
                "end": _quarter_to_str(end_h, end_m),
                "power_w": -max_charge_w,
                "max_soc": 95,
                "min_soc": int(effective_reserve),
            })

        for group in _group_consecutive_quarters(discharge_quarters):
            if len(slots) >= 14:
                break
            end_h, end_m = _next_quarter(*group[-1])
            slots.append({
                "enabled": True,
                "start": _quarter_to_str(*group[0]),
                "end": _quarter_to_str(end_h, end_m),
                "power_w": max_discharge_w,
                "max_soc": 100,
                "min_soc": int(effective_reserve),
            })

        # EV overnight cheap charge
        if ev_charging and len(slots) < 15:
            night_quarters = sorted(
                [(h, m, p) for h, m, p in prices if h >= 22 or h < 6],
                key=lambda x: x[2],
            )
            ev_qs = sorted({(h, m) for h, m, _ in night_quarters[:12]})
            for group in _group_consecutive_quarters(ev_qs):
                if len(slots) >= 15:
                    break
                if any(s["power_w"] < 0 and _quarter_in_slot(group[0], s) for s in slots):
                    continue
                end_h, end_m = _next_quarter(*group[-1])
                slots.append({
                    "enabled": True,
                    "start": _quarter_to_str(*group[0]),
                    "end": _quarter_to_str(end_h, end_m),
                    "power_w": -(max_charge_w // 2),
                    "max_soc": int(min(95, reserve_soc + ev_reserve_soc + 15)),
                    "min_soc": int(reserve_soc),
                })

        _LOGGER.info("Optimizer produced %d slot(s):", len(slots))
        for i, s in enumerate(slots, 1):
            _LOGGER.info(
                "  [%d] %s-%s %s %dW  SOC %d-%d%%",
                i, s["start"], s["end"],
                "charge" if s["power_w"] < 0 else "discharge",
                abs(s["power_w"]), s["min_soc"], s["max_soc"],
            )

        return slots


def _quarter_in_slot(quarter: tuple[int, int], slot: dict) -> bool:
    """True if the quarter (h, m) falls within slot's start-end range."""
    try:
        sh, sm = int(slot["start"][:2]), int(slot["start"][3:])
        eh, em = int(slot["end"][:2]), int(slot["end"][3:])
        qmins = quarter[0] * 60 + quarter[1]
        smins = sh * 60 + sm
        emins = eh * 60 + em
        if smins <= emins:
            return smins <= qmins < emins
        return qmins >= smins or qmins < emins
    except (KeyError, ValueError, IndexError):
        return False
