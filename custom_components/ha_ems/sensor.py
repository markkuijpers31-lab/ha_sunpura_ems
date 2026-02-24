"""Sensor platform for the Sunpura EMS integration."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfMass,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EmsRealtimeCoordinator, EmsSlowCoordinator


@dataclass(frozen=True)
class EmsSlowSensorDescription(SensorEntityDescription):
    """Description for a statistics sensor from the slow coordinator."""

    period: str = ""      # "day", "month", "year", "total"
    field_key: str = ""

_LOGGER = logging.getLogger(__name__)

# Strips a trailing unit suffix and returns a float.
# Handles: "112W", "21%", "0.00kWh", "1.01kWh", "-131W", plain numbers.
_NUMERIC_RE = re.compile(r"^-?\d+\.?\d*")


def _parse_value(value: Any) -> float | None:
    """Extract a numeric float from a value that may have a unit suffix."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _NUMERIC_RE.match(value.strip())
        if match:
            return float(match.group())
    return None


@dataclass(frozen=True)
class EmsRealtimeSensorDescription(SensorEntityDescription):
    """Description for a real-time energy flow sensor."""
    field_key: str = ""


# ---------------------------------------------------------------------------
# Sensor definitions — field_key maps to homeCountData.obj fields
# ---------------------------------------------------------------------------
REALTIME_SENSOR_DESCRIPTIONS: tuple[EmsRealtimeSensorDescription, ...] = (
    # --- Power (strings like "112W") ---
    EmsRealtimeSensorDescription(
        key="solarPower",       field_key="solarPower",
        translation_key="solar_power",  name="Solar Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,   state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
    ),
    EmsRealtimeSensorDescription(
        key="pv1Power",         field_key="pv1Power",
        translation_key="pv1_power",    name="PV1 Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,   state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
    ),
    EmsRealtimeSensorDescription(
        key="pv2Power",         field_key="pv2Power",
        translation_key="pv2_power",    name="PV2 Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,   state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
    ),
    EmsRealtimeSensorDescription(
        key="gridPower",        field_key="gridPower",
        translation_key="grid_power",   name="Grid Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,   state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
    ),
    EmsRealtimeSensorDescription(
        key="batPower",         field_key="batPower",
        translation_key="battery_power", name="Battery Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,   state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging",
    ),
    EmsRealtimeSensorDescription(
        key="acPower",          field_key="acPower",
        translation_key="ac_charge_power", name="AC Charge Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,   state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-import",
    ),
    EmsRealtimeSensorDescription(
        key="homePower",        field_key="homePower",
        translation_key="home_power",   name="Home Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,   state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    EmsRealtimeSensorDescription(
        key="loadPower",        field_key="loadPower",
        translation_key="load_power",   name="Load Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,   state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt",
    ),
    EmsRealtimeSensorDescription(
        key="totalLoadPower",   field_key="totalLoadPower",
        translation_key="total_load_power", name="Total Load Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,   state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt-outline",
    ),
    # --- Battery SOC and remaining energy ---
    EmsRealtimeSensorDescription(
        key="batSoc",           field_key="batSoc",
        translation_key="battery_soc",  name="Battery SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT,
    ),
    EmsRealtimeSensorDescription(
        key="batRemainingEnergy", field_key="batRemainingEnergy",
        translation_key="battery_remaining_energy", name="Battery Remaining Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging",
    ),
    # --- Daily energy (kWh) ---
    EmsRealtimeSensorDescription(
        key="solarDayElec",     field_key="solarDayElec",
        translation_key="solar_today",  name="Solar Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,  state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power",
    ),
    EmsRealtimeSensorDescription(
        key="gridDayBuyElec",   field_key="gridDayBuyElec",
        translation_key="grid_import_today", name="Grid Import Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,  state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-import",
    ),
    EmsRealtimeSensorDescription(
        key="gridDayElec",      field_key="gridDayElec",
        translation_key="grid_export_today", name="Grid Export Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,  state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-export",
    ),
    EmsRealtimeSensorDescription(
        key="batteryDayElec",   field_key="batteryDayElec",
        translation_key="battery_charge_today", name="Battery Charge Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,  state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-plus",
    ),
    EmsRealtimeSensorDescription(
        key="batteryDayDischargeElec", field_key="batteryDayDischargeElec",
        translation_key="battery_discharge_today", name="Battery Discharge Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,  state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-minus",
    ),
    EmsRealtimeSensorDescription(
        key="loadDayElec",      field_key="loadDayElec",
        translation_key="load_today",   name="Load Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,  state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:home-lightning-bolt-outline",
    ),
    # --- Cumulative energy totals (strings like "0.00kWh") ---
    EmsRealtimeSensorDescription(
        key="todayEnergy",      field_key="todayEnergy",
        translation_key="today_energy", name="Today Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,  state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:calendar-today",
    ),
    EmsRealtimeSensorDescription(
        key="monthEnergy",      field_key="monthEnergy",
        translation_key="month_energy", name="Month Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,  state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:calendar-month",
    ),
    EmsRealtimeSensorDescription(
        key="yearEnergy",       field_key="yearEnergy",
        translation_key="year_energy",  name="Year Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,  state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:calendar",
    ),
    EmsRealtimeSensorDescription(
        key="totalEnergy",      field_key="totalEnergy",
        translation_key="total_energy", name="Total Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,  state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:sigma",
    ),
    # --- CO2 savings ---
    EmsRealtimeSensorDescription(
        key="co2",              field_key="co2",
        translation_key="co2_savings",  name="CO2 Savings",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        device_class=SensorDeviceClass.WEIGHT,  state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:molecule-co2",
    ),
)


def _slow_energy(key, field_key, name, icon, translation_key):
    return EmsSlowSensorDescription(
        key=key,
        field_key=field_key,
        period=key.split("_")[0],
        name=name,
        translation_key=translation_key,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon=icon,
    )


SLOW_SENSOR_DESCRIPTIONS: tuple[EmsSlowSensorDescription, ...] = (
    # --- This Month ---
    _slow_energy("month_solarTotal",        "solarTotal",        "Solar This Month",              "mdi:solar-power",              "solar_this_month"),
    _slow_energy("month_gridBuyTotal",      "gridBuyTotal",      "Grid Import This Month",        "mdi:transmission-tower-import","grid_import_this_month"),
    _slow_energy("month_gridTotal",         "gridTotal",         "Grid Export This Month",        "mdi:transmission-tower-export","grid_export_this_month"),
    _slow_energy("month_batTotal",          "batTotal",          "Battery Charge This Month",     "mdi:battery-plus",             "battery_charge_this_month"),
    _slow_energy("month_batDischargeTotal", "batDischargeTotal", "Battery Discharge This Month",  "mdi:battery-minus",            "battery_discharge_this_month"),
    # --- This Year ---
    _slow_energy("year_solarTotal",         "solarTotal",        "Solar This Year",               "mdi:solar-power",              "solar_this_year"),
    _slow_energy("year_gridBuyTotal",       "gridBuyTotal",      "Grid Import This Year",         "mdi:transmission-tower-import","grid_import_this_year"),
    _slow_energy("year_gridTotal",          "gridTotal",         "Grid Export This Year",         "mdi:transmission-tower-export","grid_export_this_year"),
    _slow_energy("year_batTotal",           "batTotal",          "Battery Charge This Year",      "mdi:battery-plus",             "battery_charge_this_year"),
    _slow_energy("year_batDischargeTotal",  "batDischargeTotal", "Battery Discharge This Year",   "mdi:battery-minus",            "battery_discharge_this_year"),
    # --- All Time ---
    _slow_energy("total_solarTotal",        "solarTotal",        "Solar All Time",                "mdi:solar-power",              "solar_all_time"),
    _slow_energy("total_gridBuyTotal",      "gridBuyTotal",      "Grid Import All Time",          "mdi:transmission-tower-import","grid_import_all_time"),
    _slow_energy("total_gridTotal",         "gridTotal",         "Grid Export All Time",          "mdi:transmission-tower-export","grid_export_all_time"),
    _slow_energy("total_batTotal",          "batTotal",          "Battery Charge All Time",       "mdi:battery-plus",             "battery_charge_all_time"),
    _slow_energy("total_batDischargeTotal", "batDischargeTotal", "Battery Discharge All Time",    "mdi:battery-minus",            "battery_discharge_all_time"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ha_ems sensors."""
    hub = hass.data[DOMAIN]["hub"]
    realtime_coordinator: EmsRealtimeCoordinator = hass.data[DOMAIN]["realtime_coordinator"]
    slow_coordinator: EmsSlowCoordinator = hass.data[DOMAIN]["slow_coordinator"]

    entities: list[SensorEntity] = []

    # Discovery sensors — show raw API fields as attributes for troubleshooting
    entities.append(EmsRawDiscoverySensor(realtime_coordinator, hub))
    entities.append(EmsSlowDiscoverySensor(slow_coordinator, hub))

    # Individual real-time sensors
    for description in REALTIME_SENSOR_DESCRIPTIONS:
        entities.append(EmsRealtimeSensor(realtime_coordinator, hub, description))

    # Statistics sensors (month / year / all-time breakdowns)
    for description in SLOW_SENSOR_DESCRIPTIONS:
        entities.append(EmsSlowSensor(slow_coordinator, hub, description))

    # Schedule sensor — shows current controlTime slots from the device
    if hub.main_control_device_id:
        entities.append(EmsScheduleSensor(slow_coordinator, hub))

    async_add_entities(entities)


class _EmsBaseSensor(CoordinatorEntity, SensorEntity):
    """Shared base for all ha_ems sensors."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, "ha_ems_main")},
            "name": "Sunpura S2400",
            "manufacturer": "Sunpura",
        }


class EmsRawDiscoverySensor(_EmsBaseSensor):
    """Diagnostic sensor: all raw homeCountData fields as attributes.

    Use Developer Tools → States to find the actual field names
    returned by the cloud API.
    """

    def __init__(self, coordinator: EmsRealtimeCoordinator, hub) -> None:
        super().__init__(coordinator)
        self.hub = hub
        self._attr_translation_key = "api_discovery_realtime"
        self._attr_name = "API Discovery (realtime)"
        self._attr_unique_id = "ha_ems_raw_home_count"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:code-json"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return "available" if self.coordinator.data.get("home_count") else "no data"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None:
            return {}
        # Flatten: skip large nested lists/dicts to keep the attribute readable
        raw = self.coordinator.data.get("home_count", {})
        return {
            k: v for k, v in raw.items()
            if not isinstance(v, (list, dict)) or k in ("pvPowerMap", "batDataMap")
        }


class EmsSlowDiscoverySensor(_EmsBaseSensor):
    """Diagnostic sensor: all raw slow-coordinator fields as attributes.

    Shows energy totals (day/month/year/total) and AI settings.
    Use Developer Tools → States to find actual field names.
    """

    def __init__(self, coordinator: EmsSlowCoordinator, hub) -> None:
        super().__init__(coordinator)
        self.hub = hub
        self._attr_translation_key = "api_discovery_slow"
        self._attr_name = "API Discovery (slow data)"
        self._attr_unique_id = "ha_ems_raw_slow_data"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:database-eye"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return "available" if self.coordinator.data else "no data"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None:
            return {}
        data = self.coordinator.data
        result: dict[str, Any] = {}
        for section in ("day", "month", "year", "total"):
            section_data = data.get(section) or {}
            if isinstance(section_data, dict):
                for k, v in section_data.items():
                    if not isinstance(v, (list, dict)):
                        result[f"{section}.{k}"] = v
        ai = data.get("ai_settings") or {}
        obj = ai.get("obj") if isinstance(ai, dict) else None
        if isinstance(obj, dict):
            for k, v in obj.items():
                if not isinstance(v, (list, dict)):
                    result[f"ai.{k}"] = v
        return result


class EmsRealtimeSensor(_EmsBaseSensor):
    """Sensor for a single field from homeCountData.obj."""

    def __init__(
        self,
        coordinator: EmsRealtimeCoordinator,
        hub,
        description: EmsRealtimeSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.hub = hub
        self.entity_description = description
        self._attr_unique_id = f"ha_ems_{description.field_key}"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.get("home_count", {}).get(
            self.entity_description.field_key
        )
        return _parse_value(raw)

    @property
    def available(self) -> bool:
        if not super().available or self.coordinator.data is None:
            return False
        return self.entity_description.field_key in self.coordinator.data.get(
            "home_count", {}
        )


class EmsSlowSensor(_EmsBaseSensor):
    """Sensor for a single field from the slow coordinator (month/year/total statistics)."""

    def __init__(
        self,
        coordinator: EmsSlowCoordinator,
        hub,
        description: EmsSlowSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.hub = hub
        self.entity_description = description
        self._attr_unique_id = f"ha_ems_{description.period}_{description.field_key}"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        period_data = self.coordinator.data.get(self.entity_description.period) or {}
        val = period_data.get(self.entity_description.field_key)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    @property
    def available(self) -> bool:
        if not super().available or self.coordinator.data is None:
            return False
        period_data = self.coordinator.data.get(self.entity_description.period) or {}
        return self.entity_description.field_key in period_data


class EmsScheduleSensor(_EmsBaseSensor):
    """Sensor showing the current controlTime schedule slots from the device.

    State: number of active slots (int).
    Attributes: slot_1..slot_16 (raw CSV strings), energy_mode.
    """

    def __init__(self, coordinator: EmsSlowCoordinator, hub) -> None:
        super().__init__(coordinator)
        self.hub = hub
        self._attr_translation_key = "battery_schedule"
        self._attr_name = "Battery Schedule"
        self._attr_unique_id = "ha_ems_battery_schedule"
        self._attr_icon = "mdi:calendar-clock"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    def _get_obj(self) -> dict | None:
        if self.coordinator.data is None:
            return None
        ai = self.coordinator.data.get("ai_settings") or {}
        obj = ai.get("obj") if isinstance(ai, dict) else None
        return obj if isinstance(obj, dict) else None

    @property
    def native_value(self) -> int | None:
        obj = self._get_obj()
        if obj is None:
            return None
        active = 0
        for i in range(1, 17):
            slot = obj.get(f"controlTime{i}", "")
            if slot and str(slot).split(",")[0] == "1":
                active += 1
        return active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        obj = self._get_obj()
        if obj is None:
            return {}
        attrs: dict[str, Any] = {}
        for i in range(1, 17):
            attrs[f"slot_{i}"] = obj.get(f"controlTime{i}", "")
        attrs["energy_mode"] = obj.get("energyMode")
        return attrs


