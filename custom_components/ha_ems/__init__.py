"""The Sunpura EMS custom integration."""

from __future__ import annotations

import logging
from functools import partial

from zeroconf import ServiceBrowser, ServiceStateChange
from homeassistant.components import zeroconf
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import EmsRealtimeCoordinator, EmsSlowCoordinator
from .discovery import handle_zeroconf_callback
from .hub import SunpuraHub

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.SELECT, Platform.NUMBER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ha_ems from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    family = int(entry.data.get("family"))
    hub = SunpuraHub(hass, entry, family)

    # Initial login and plant/device discovery
    try:
        await hub.reLogin()
        await hub.getPlantList()
        await hub.getMainControlDeviceId()
    except Exception as err:
        raise ConfigEntryNotReady(f"Cannot connect to cloud: {err}") from err

    # Create coordinators
    realtime_coordinator = EmsRealtimeCoordinator(hass, hub)
    slow_coordinator = EmsSlowCoordinator(hass, hub)

    # First data fetch before setting up entities
    await realtime_coordinator.async_config_entry_first_refresh()
    await slow_coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN]["hub"] = hub
    hass.data[DOMAIN]["realtime_coordinator"] = realtime_coordinator
    hass.data[DOMAIN]["slow_coordinator"] = slow_coordinator

    # Set up sensor / switch / select platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ----------------------------------------------------------------
    # HA services (kept for use in automations)
    # ----------------------------------------------------------------

    async def _service_switch_plant(call):
        plant_id = call.data.get("plantId")
        if not plant_id:
            return
        hub.plant_id = plant_id
        await hub.getMainControlDeviceId()
        await realtime_coordinator.async_refresh()
        await slow_coordinator.async_refresh()

    hass.services.async_register(DOMAIN, "service_switch_plant", _service_switch_plant)

    async def _service_refresh_data(call):
        await realtime_coordinator.async_refresh()
        await slow_coordinator.async_refresh()

    hass.services.async_register(DOMAIN, "service_refresh_data", _service_refresh_data)

    async def _service_get_energy_data_day(call):
        await hub.get_energy_data_day(
            call.data.get("plantId", hub.plant_id),
            call.data.get("sn", ""),
        )

    hass.services.async_register(DOMAIN, "service_get_energy_data_day", _service_get_energy_data_day)

    async def _service_get_energy_data_month(call):
        await hub.get_energy_data_month(
            call.data.get("plantId", hub.plant_id),
            call.data.get("sn", ""),
        )

    hass.services.async_register(DOMAIN, "service_get_energy_data_month", _service_get_energy_data_month)

    async def _service_get_energy_data_year(call):
        await hub.get_energy_data_year(
            call.data.get("plantId", hub.plant_id),
            call.data.get("sn", ""),
        )

    hass.services.async_register(DOMAIN, "service_get_energy_data_year", _service_get_energy_data_year)

    async def _service_get_energy_data_total(call):
        await hub.get_energy_data_total(
            call.data.get("plantId", hub.plant_id),
            call.data.get("sn", ""),
        )

    hass.services.async_register(DOMAIN, "service_get_energy_data_total", _service_get_energy_data_total)

    async def _service_set_device_name(call):
        await hub.set_device_name(call.data.get("deviceSn", ""), call.data.get("deviceName", ""))

    hass.services.async_register(DOMAIN, "service_set_device_name", _service_set_device_name)

    async def _service_green_power_plan(call):
        await hub.green_power_plan(call.data.get("datalogSn", ""), call.data.get("plantId", ""))

    hass.services.async_register(DOMAIN, "service_green_power_plan", _service_green_power_plan)

    async def _service_update_green_power_plan(call):
        await hub.update_green_power_plan(call.data)

    hass.services.async_register(DOMAIN, "service_update_green_power_plan", _service_update_green_power_plan)

    async def _service_switch(call):
        await hub.switch(call.data.get("deviceSn"), call.data.get("value"))

    hass.services.async_register(DOMAIN, "service_switch", _service_switch)

    async def _service_set_smart_link_mode(call):
        await hub.set_ai_link_mode(call.data.get("datalogSn", ""), call.data.get("value", ""))

    hass.services.async_register(DOMAIN, "service_set_smart_link_mode", _service_set_smart_link_mode)

    async def _service_set_ai_pre_mode(call):
        await hub.set_ai_pre_mode(call.data)

    hass.services.async_register(DOMAIN, "service_set_ai_pre_mode", _service_set_ai_pre_mode)

    async def _service_get_ai_system_times(call):
        await hub.get_ai_system_times_with_energy_mode(
            call.data.get("datalogSn", ""), call.data.get("energyMode", 0)
        )

    hass.services.async_register(
        DOMAIN, "service_get_ai_system_times_with_energy_mode", _service_get_ai_system_times
    )

    async def _service_set_ai_system_times(call):
        await hub.set_ai_system_times_with_energy_mode(call.data)

    hass.services.async_register(
        DOMAIN, "service_set_ai_system_times_with_energy_mode", _service_set_ai_system_times
    )

    async def _service_set_master_slave_type(call):
        await hub.set_master_slave_type(call.data.get("deviceSn", ""))

    hass.services.async_register(DOMAIN, "service_set_device_ms_type", _service_set_master_slave_type)

    async def _service_set_phase_detection(call):
        await hub.set_phase_detection(
            call.data.get("deviceSn", ""),
            call.data.get("datalogSn", ""),
            call.data.get("plantId", ""),
        )

    hass.services.async_register(DOMAIN, "service_set_phase_detection", _service_set_phase_detection)

    async def _service_check_firmware(call):
        await hub.check_firmware_version(
            call.data.get("deviceSn", ""), call.data.get("upgradeType", "")
        )

    hass.services.async_register(DOMAIN, "service_check_firmware_version", _service_check_firmware)

    async def _service_get_smart_device_list(call):
        await hub.get_smart_device_list()

    hass.services.async_register(DOMAIN, "service_get_smart_device_list", _service_get_smart_device_list)

    async def _service_set_smart_socket_mode(call):
        await hub.set_smart_socket_mode(
            call.data.get("datalogSn", ""),
            call.data.get("smartSocketMode", ""),
            call.data.get("batBasicDisChargePower", 0),
            call.data.get("basicDisChargeEnable", 0),
        )

    hass.services.async_register(DOMAIN, "service_set_smart_socket_mode", _service_set_smart_socket_mode)

    async def _service_get_device_history(call):
        await hub.get_ai_use_electricity(call.data.get("sn", ""), call.data.get("deviceType", ""))

    hass.services.async_register(DOMAIN, "service_device_history_info", _service_get_device_history)

    async def _service_get_price_company(call):
        await hub.get_price_company_by_plant_id(call.data.get("datalogSn"))

    hass.services.async_register(
        DOMAIN, "service_get_price_company_by_plant_id", _service_get_price_company
    )

    async def _service_add_provider(call):
        await hub.add_provider(call.data)

    hass.services.async_register(DOMAIN, "service_add_provider", _service_add_provider)

    async def _service_get_currency_type(call):
        await hub.get_dict_data_by_type_id()

    hass.services.async_register(DOMAIN, "service_get_currency_type", _service_get_currency_type)

    async def _service_save_ai_price(call):
        await hub.save_ai_price(call.data)

    hass.services.async_register(DOMAIN, "service_save_ai_price", _service_save_ai_price)

    async def _service_get_tibber_token(call):
        await hub.get_tibber_token()

    hass.services.async_register(DOMAIN, "service_get_tibber_token", _service_get_tibber_token)

    async def _service_add_tibber_token(call):
        await hub.add_tibber_token(
            call.data.get("plantId"),
            call.data.get("systemSn"),
            call.data.get("token"),
        )

    hass.services.async_register(DOMAIN, "service_add_tibber_token", _service_add_tibber_token)

    async def _service_set_price_company(call):
        await hub.set_price_company(call.data)

    hass.services.async_register(DOMAIN, "service_set_price_company", _service_set_price_company)

    async def _service_get_ai_price(call):
        await hub.get_ai_price(call.data.get("priceType", 3))

    hass.services.async_register(DOMAIN, "service_get_ai_price", _service_get_ai_price)

    async def _service_generate_ai_price(call):
        await hub.generate_ai_price(
            call.data.get("datalogSn"),
            call.data.get("ecVersion"),
            call.data.get("intervalType"),
        )

    hass.services.async_register(DOMAIN, "service_generate_ai_price", _service_generate_ai_price)

    async def _service_check_meter(call):
        await hub.check_meter_by_plant_id()

    hass.services.async_register(DOMAIN, "service_check_meter_by_plant_id", _service_check_meter)

    async def _service_check_zero_feed(call):
        await hub.async_check_zero_feed(
            call.data.get("datalogSn"),
            call.data.get("plantId"),
            call.data.get("powerMode"),
            call.data.get("aiMode"),
        )

    hass.services.async_register(DOMAIN, "service_check_zero_feed", _service_check_zero_feed)

    async def _service_set_setting_info(call):
        await hub.async_set_setting_info(call.data)

    hass.services.async_register(DOMAIN, "service_set_setting_info", _service_set_setting_info)

    async def _service_set_custom_params(call):
        await hub.async_set_custom_params(call.data)

    hass.services.async_register(DOMAIN, "service_set_custom_params", _service_set_custom_params)

    async def _service_set_external_device(call):
        await hub.set_external_device(call.data)

    hass.services.async_register(DOMAIN, "service_set_external_device", _service_set_external_device)

    async def _service_get_dict_data(call):
        await hub.get_dict_data_by_type_id_number(call.data.get("typeId", ""))

    hass.services.async_register(
        DOMAIN, "service_get_dict_data_by_type_id_number", _service_get_dict_data
    )

    async def _service_local_connection(call):
        await hub.async_local_device_connection(call.data.get("deviceSn"))

    hass.services.async_register(DOMAIN, "service_local_device_connection", _service_local_connection)

    async def _service_local_get_device_info(call):
        await hub.async_local_get_device_parms(call.data.get("deviceSn"))

    hass.services.async_register(DOMAIN, "service_local_get_device_info", _service_local_get_device_info)

    async def _service_local_switch(call):
        await hub.switch(call.data.get("deviceSn"), call.data.get("value"))

    hass.services.async_register(DOMAIN, "service_local_device_switch", _service_local_switch)

    async def _service_local_get_energy_parms(call):
        await hub.async_local_get_energy_control_parms(
            call.data.get("deviceSn"), call.data.get("data")
        )

    hass.services.async_register(
        DOMAIN, "service_local_get_energy_control_parms", _service_local_get_energy_parms
    )

    async def _service_local_energy_enable(call):
        await hub.async_local_energy_enable(call.data.get("deviceSn"), call.data.get("value"))

    hass.services.async_register(DOMAIN, "service_local_energy_enable", _service_local_energy_enable)

    async def _service_local_smart_mode(call):
        await hub.async_local_smart_mode_switch(call.data.get("deviceSn"))

    hass.services.async_register(DOMAIN, "service_local_smart_mode_switch", _service_local_smart_mode)

    async def _service_local_custom_mode(call):
        await hub.async_local_custom_mode_switch(
            call.data.get("deviceSn"), call.data.get("data")
        )

    hass.services.async_register(DOMAIN, "service_local_custom_mode_switch", _service_local_custom_mode)

    async def _service_local_set_energy_parms(call):
        await hub.set_device_control_parms(call.data.get("deviceSn"), call.data.get("data"))

    hass.services.async_register(
        DOMAIN, "service_local_set_energy_control_parms", _service_local_set_energy_parms
    )

    async def _service_local_set_socket(call):
        await hub.set_socket_control(
            call.data.get("deviceSn"),
            call.data.get("devAddr"),
            call.data.get("isThirdParty"),
            call.data.get("data"),
        )

    hass.services.async_register(DOMAIN, "service_local_set_socket_control", _service_local_set_socket)

    async def _service_local_set_hot_tub(call):
        await hub.set_hot_tub_control(
            call.data.get("deviceSn"),
            call.data.get("devAddr"),
            call.data.get("isThirdParty"),
            call.data.get("data"),
        )

    hass.services.async_register(DOMAIN, "service_local_set_hot_tub_control", _service_local_set_hot_tub)

    async def _service_local_set_relay(call):
        await hub.set_relay_control(
            call.data.get("deviceSn"),
            call.data.get("devAddr"),
            call.data.get("isThirdParty"),
            call.data.get("data"),
        )

    hass.services.async_register(DOMAIN, "service_local_set_relay_control", _service_local_set_relay)

    async def _service_local_transmit(call):
        await hub.transmit_data(
            call.data.get("deviceSn"),
            call.data.get("functionCode"),
            call.data.get("transmittedData"),
        )

    hass.services.async_register(DOMAIN, "service_local_transmit_data", _service_local_transmit)

    async def _service_local_get_ems_register(call):
        await hub.get_ems_register(call.data.get("deviceSn"), call.data.get("registerAddr"))

    hass.services.async_register(
        DOMAIN, "service_local_get_ems_register", _service_local_get_ems_register
    )

    async def _service_local_set_ems_register(call):
        await hub.set_ems_register(call.data.get("deviceSn"), call.data.get("data"))

    hass.services.async_register(
        DOMAIN, "service_local_set_ems_register", _service_local_set_ems_register
    )

    async def _service_get_ai_use_electricity(call):
        await hub.get_ai_use_electricity(
            call.data.get("deviceSn", ""), call.data.get("deviceType", "")
        )

    hass.services.async_register(
        DOMAIN, "service_get_ai_use_electricity", _service_get_ai_use_electricity
    )

    async def _service_get_ai_use_strategy(call):
        await hub.get_ai_use_strategy(call.data)

    hass.services.async_register(DOMAIN, "service_get_ai_use_strategy", _service_get_ai_use_strategy)

    # ----------------------------------------------------------------
    # Optimizer services
    # ----------------------------------------------------------------

    async def _service_push_schedule(call):
        """Write a list of slot dicts to the battery in Custom mode."""
        slots = call.data.get("slots", [])
        dry_run = bool(call.data.get("dry_run", False))
        result = await hub.push_schedule(slots, dry_run=dry_run)
        _LOGGER.info("push_schedule result: %s", result)
        if not dry_run:
            await slow_coordinator.async_refresh()

    hass.services.async_register(DOMAIN, "push_schedule", _service_push_schedule)

    # ----------------------------------------------------------------
    # Zeroconf local device discovery (unchanged)
    # ----------------------------------------------------------------
    zeroconf_instance = await zeroconf.async_get_async_instance(hass)
    browser = ServiceBrowser(
        zeroconf_instance.zeroconf,
        "_http._tcp.local.",
        handlers=[partial(handle_zeroconf_callback, hass, entry)],
    )
    hass.data[DOMAIN]["zeroconf_browser"] = browser

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the ha_ems config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.pop(DOMAIN, None)
    return unloaded
