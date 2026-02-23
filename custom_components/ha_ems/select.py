"""Select platform for the Sunpura EMS integration — energy mode selector."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ENERGY_MODE_OPTIONS
from .coordinator import EmsSlowCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ha_ems select entities."""
    hub = hass.data[DOMAIN]["hub"]
    slow_coordinator: EmsSlowCoordinator = hass.data[DOMAIN]["slow_coordinator"]

    if hub.main_control_device_id:
        async_add_entities([EmsEnergyModeSelect(slow_coordinator, hub)])


class EmsEnergyModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity for the EMS energy mode."""

    _attr_has_entity_name = True
    _attr_translation_key = "energy_mode"
    _attr_name = "Energy Mode"
    _attr_icon = "mdi:lightning-bolt-circle"
    _attr_options = list(ENERGY_MODE_OPTIONS.values())

    def __init__(self, coordinator: EmsSlowCoordinator, hub) -> None:
        super().__init__(coordinator)
        self.hub = hub
        self._attr_unique_id = "ha_ems_energy_mode"
        self._optimistic_option: str | None = None

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, "ha_ems_main")},
            "name": "Sunpura S2400",
            "manufacturer": "Sunpura",
        }

    @property
    def current_option(self) -> str | None:
        if self._optimistic_option is not None:
            return self._optimistic_option
        if self.coordinator.data is None:
            return None
        ai = self.coordinator.data.get("ai_settings") or {}
        obj = ai.get("obj") if isinstance(ai, dict) else None
        if isinstance(obj, dict):
            for key in ("energyMode", "mode", "workMode"):
                val = obj.get(key)
                if val is not None:
                    return ENERGY_MODE_OPTIONS.get(str(val))
        return None

    async def async_select_option(self, option: str) -> None:
        mode_code = next(
            (k for k, v in ENERGY_MODE_OPTIONS.items() if v == option), None
        )
        if mode_code is None:
            _LOGGER.warning("Unknown energy mode option: %s", option)
            return
        # Read-modify-write: preserve all other AI settings
        ai = self.coordinator.data.get("ai_settings") or {} if self.coordinator.data else {}
        obj = ai.get("obj") if isinstance(ai, dict) else {}
        new_obj = dict(obj) if isinstance(obj, dict) else {}
        new_obj["energyMode"] = int(mode_code)
        if "datalogSn" not in new_obj:
            new_obj["datalogSn"] = self.hub.main_control_device_id
        await self.hub.set_ai_system_times_with_energy_mode(new_obj)
        self._optimistic_option = option
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _handle_coordinator_update(self) -> None:
        self._optimistic_option = None
        super()._handle_coordinator_update()
