"""Switch platform for the Sunpura EMS integration."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ICON_TYPE_CHARGER, ICON_TYPE_SOCKET
from .coordinator import EmsSlowCoordinator

_LOGGER = logging.getLogger(__name__)

# AI setting switches: (field_key, name, icon, translation_key)
_AI_SWITCHES = [
    ("basicDisChargeEnable", "Basic Discharge",            "mdi:battery-arrow-down-outline", "basic_discharge"),
    ("antiRefluxSet",        "Anti-Reflux (Zero Feed-in)", "mdi:transmission-tower-off",     "anti_reflux"),
    ("ctEnable",             "CT Clamp Enable",            "mdi:current-ac",                 "ct_enable"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ha_ems switch entities."""
    hub = hass.data[DOMAIN]["hub"]
    slow_coordinator: EmsSlowCoordinator = hass.data[DOMAIN]["slow_coordinator"]

    entities: list[SwitchEntity] = []

    if hub.main_control_device_id:
        # Smart link mode switch
        entities.append(EmsSmartModeSwitch(slow_coordinator, hub))
        # AI setting switches
        for field_key, name, icon, translation_key in _AI_SWITCHES:
            entities.append(EmsAiSettingSwitch(slow_coordinator, hub, field_key, name, icon, translation_key))

    # Device switches (one per socket/charger in device_list)
    device_list = slow_coordinator.data.get("device_list", []) if slow_coordinator.data else []
    for device in device_list:
        if device.get("iconType") in (ICON_TYPE_SOCKET, ICON_TYPE_CHARGER):
            entities.append(EmsDeviceSwitch(slow_coordinator, hub, device))

    async_add_entities(entities)


class _EmsBaseSwitch(CoordinatorEntity, SwitchEntity):
    """Shared base for all ha_ems switches."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, "ha_ems_main")},
            "name": "Sunpura S2400",
            "manufacturer": "Sunpura",
        }


class EmsSmartModeSwitch(_EmsBaseSwitch):
    """Switch to enable/disable AI smart link mode."""

    def __init__(self, coordinator: EmsSlowCoordinator, hub) -> None:
        super().__init__(coordinator)
        self.hub = hub
        self._attr_translation_key = "smart_link_mode"
        self._attr_name = "Smart Link Mode"
        self._attr_unique_id = "ha_ems_smart_link_mode"
        self._attr_icon = "mdi:brain"
        self._optimistic_state: bool | None = None

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_state is not None:
            return self._optimistic_state
        if self.coordinator.data is None:
            return None
        ai = self.coordinator.data.get("ai_settings") or {}
        obj = ai.get("obj") if isinstance(ai, dict) else None
        if isinstance(obj, dict):
            for key in ("linkage", "aiMode", "flag", "smartMode"):
                val = obj.get(key)
                if val is not None:
                    return bool(val)
        return None

    async def async_turn_on(self, **kwargs) -> None:
        await self.hub.set_ai_link_mode(self.hub.main_control_device_id, 1)
        self._optimistic_state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.hub.set_ai_link_mode(self.hub.main_control_device_id, 0)
        self._optimistic_state = False
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        self._optimistic_state = None  # reset after real update
        super()._handle_coordinator_update()


class EmsAiSettingSwitch(_EmsBaseSwitch):
    """Switch for a single boolean AI settings field (read-modify-write)."""

    def __init__(
        self, coordinator: EmsSlowCoordinator, hub,
        field_key: str, name: str, icon: str, translation_key: str,
    ) -> None:
        super().__init__(coordinator)
        self.hub = hub
        self._field_key = field_key
        self._attr_translation_key = translation_key
        self._attr_name = name
        self._attr_unique_id = f"ha_ems_ai_{field_key}"
        self._attr_icon = icon
        self._optimistic_state: bool | None = None

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
        return obj is not None and self._field_key in obj

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_state is not None:
            return self._optimistic_state
        obj = self._get_ai_obj()
        if obj is None:
            return None
        val = obj.get(self._field_key)
        return bool(val) if val is not None else None

    async def _write(self, value: int) -> None:
        obj = self._get_ai_obj() or {}
        new_obj = dict(obj)
        new_obj[self._field_key] = value
        if "datalogSn" not in new_obj:
            new_obj["datalogSn"] = self.hub.main_control_device_id
        resp = await self.hub.set_ai_system_times_with_energy_mode(new_obj)

    async def async_turn_on(self, **kwargs) -> None:
        self._optimistic_state = True
        self.async_write_ha_state()
        await self._write(1)

    async def async_turn_off(self, **kwargs) -> None:
        self._optimistic_state = False
        self.async_write_ha_state()
        await self._write(0)

    def _handle_coordinator_update(self) -> None:
        if self._optimistic_state is not None:
            obj = self._get_ai_obj()
            if obj is not None:
                api_val = obj.get(self._field_key)
                if api_val is not None and bool(api_val) == self._optimistic_state:
                    # API confirmed our written value — clear optimistic override
                    self._optimistic_state = None
                else:
                    # API still returns old value — hold optimistic state
                    _LOGGER.debug(
                        "API returned %s=%s, holding optimistic state=%s",
                        self._field_key, api_val, self._optimistic_state,
                    )
                    self.async_write_ha_state()
                    return
        super()._handle_coordinator_update()


class EmsDeviceSwitch(_EmsBaseSwitch):
    """Switch for a single smart socket or EV charger."""

    def __init__(
        self, coordinator: EmsSlowCoordinator, hub, device: dict
    ) -> None:
        super().__init__(coordinator)
        self.hub = hub
        self._sn = device.get("deviceSn", "")
        self._icon_type = device.get("iconType")
        device_name = device.get("deviceName") or self._sn
        self._attr_name = device_name
        self._attr_unique_id = f"ha_ems_device_{self._sn}"
        self._attr_icon = (
            "mdi:ev-station" if self._icon_type == ICON_TYPE_CHARGER else "mdi:power-socket"
        )
        self._optimistic_state: bool | None = None

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_state is not None:
            return self._optimistic_state
        if self.coordinator.data is None:
            return None
        for device in self.coordinator.data.get("device_list", []):
            if device.get("deviceSn") == self._sn:
                info = device.get("deviceInfo") or {}
                for key in ("switch", "status", "onOff", "state"):
                    val = info.get(key)
                    if val is not None:
                        return bool(val)
        return None

    async def async_turn_on(self, **kwargs) -> None:
        await self.hub.switch(self._sn, 1)
        self._optimistic_state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.hub.switch(self._sn, 0)
        self._optimistic_state = False
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        self._optimistic_state = None
        super()._handle_coordinator_update()
