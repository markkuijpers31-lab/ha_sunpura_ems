"""Number platform for the Sunpura EMS integration — AI setting numeric controls."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EmsSlowCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmsAiNumberDescription(NumberEntityDescription):
    """Description for an AI settings number entity."""

    field_key: str = ""


AI_NUMBER_DESCRIPTIONS: tuple[EmsAiNumberDescription, ...] = (
    EmsAiNumberDescription(
        key="maxChargePower",       field_key="maxChargePower",
        translation_key="max_charge_power", name="Max Charge Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=10, native_max_value=2400, native_step=10,
        icon="mdi:battery-arrow-up",
    ),
    EmsAiNumberDescription(
        key="maxFeedPower",         field_key="maxFeedPower",
        translation_key="max_feed_power",   name="Max Feed-in Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=10, native_max_value=2400, native_step=10,
        icon="mdi:transmission-tower-export",
    ),
    EmsAiNumberDescription(
        key="batBasicDisChargePower", field_key="batBasicDisChargePower",
        translation_key="discharge_power",  name="Discharge Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0, native_max_value=800, native_step=10,
        icon="mdi:battery-arrow-down",
    ),
    EmsAiNumberDescription(
        key="minDischargeSOC",      field_key="minDischargeSOC",
        translation_key="min_discharge_soc", name="Min Discharge SOC",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0, native_max_value=100, native_step=1,
        icon="mdi:battery-low",
    ),
    EmsAiNumberDescription(
        key="maxChargeSOC",         field_key="maxChargeSOC",
        translation_key="max_charge_soc",   name="Max Charge SOC",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0, native_max_value=100, native_step=1,
        icon="mdi:battery-high",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ha_ems number entities."""
    hub = hass.data[DOMAIN]["hub"]
    slow_coordinator: EmsSlowCoordinator = hass.data[DOMAIN]["slow_coordinator"]

    if hub.main_control_device_id:
        async_add_entities([
            EmsAiNumberEntity(slow_coordinator, hub, description)
            for description in AI_NUMBER_DESCRIPTIONS
        ])


class EmsAiNumberEntity(CoordinatorEntity, NumberEntity):
    """Number entity for an AI settings parameter (read-modify-write)."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: EmsSlowCoordinator,
        hub,
        description: EmsAiNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self.hub = hub
        self.entity_description = description
        self._attr_unique_id = f"ha_ems_ai_{description.field_key}"
        self._optimistic_value: float | None = None

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, "ha_ems_main")},
            "name": "Sunpura S2400",
            "manufacturer": "Sunpura",
        }

    def _get_ai_obj(self) -> dict | None:
        if self.coordinator.data is None:
            return None
        ai = self.coordinator.data.get("ai_settings") or {}
        obj = ai.get("obj") if isinstance(ai, dict) else None
        return obj if isinstance(obj, dict) else None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        obj = self._get_ai_obj()
        return obj is not None and self.entity_description.field_key in obj

    @property
    def native_value(self) -> float | None:
        if self._optimistic_value is not None:
            return self._optimistic_value
        obj = self._get_ai_obj()
        if obj is None:
            return None
        val = obj.get(self.entity_description.field_key)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        self._optimistic_value = value
        self.async_write_ha_state()
        obj = self._get_ai_obj() or {}
        new_obj = dict(obj)
        new_obj[self.entity_description.field_key] = int(value)
        if "datalogSn" not in new_obj:
            new_obj["datalogSn"] = self.hub.main_control_device_id
        await self.hub.set_ai_system_times_with_energy_mode(new_obj)

    def _handle_coordinator_update(self) -> None:
        if self._optimistic_value is not None:
            obj = self._get_ai_obj()
            if obj is not None:
                api_val = obj.get(self.entity_description.field_key)
                if api_val is not None:
                    try:
                        if float(api_val) == self._optimistic_value:
                            self._optimistic_value = None
                        else:
                            # API still returns old value — hold optimistic state
                            self.async_write_ha_state()
                            return
                    except (TypeError, ValueError):
                        self._optimistic_value = None
        super()._handle_coordinator_update()
