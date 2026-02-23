# discovery.py — Zeroconf/mDNS local device discovery for ha_ems

import logging

from zeroconf import ServiceStateChange
from zeroconf._services.info import AsyncServiceInfo

from .const import DOMAIN, STORAGE_DEVICE_TYPES
from .tcp_client import SunpuraDeviceClient

_LOGGER = logging.getLogger(__name__)


def handle_zeroconf_callback(hass, entry, zeroconf, service_type, name, state_change):
    """Synchronous wrapper — schedules async discovery on the HA event loop."""
    hass.loop.call_soon_threadsafe(
        hass.async_create_task,
        async_handle_zeroconf_device(hass, entry, zeroconf, service_type, name, state_change),
    )


async def async_handle_zeroconf_device(
    hass, entry, zeroconf, service_type, name, state_change
):
    """Handle a discovered or removed local device."""
    info = AsyncServiceInfo(service_type, name)
    hub = hass.data.get(DOMAIN, {}).get("hub")
    if hub is None:
        return

    if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
        success = await info.async_request(zeroconf, timeout=3.0)
        if not success:
            return
        properties = dict(info.properties)
        if properties.get(b"s_sn") is None:
            return

        device_sn = properties.get(b"s_sn", b"").decode("utf-8")
        device_ip = properties.get(b"s_ip", b"").decode("utf-8")
        device_type = int(properties.get(b"s_type", b"0").decode("utf-8"))
        device_port = int(properties.get(b"s_port", b"0").decode("utf-8"))

        local_device_list: list = hub.data.get("local_device_list", [])
        # Update or add the device entry
        existing = next((d for d in local_device_list if d["sn"] == device_sn), None)
        if existing is None:
            local_device_list.append(
                {"sn": device_sn, "ip": device_ip, "port": device_port, "type": device_type}
            )
        else:
            existing.update({"ip": device_ip, "port": device_port, "type": device_type})

        _LOGGER.info("mDNS device: SN=%s, IP=%s, port=%s, type=%s",
                     device_sn, device_ip, device_port, device_type)

        # Auto-connect storage devices
        if device_type in STORAGE_DEVICE_TYPES:
            try:
                client = SunpuraDeviceClient(device_ip, device_port)
                await client.connect()
                hub.local_client[device_sn] = client
                if device_sn not in hub.data["local_client_list"]:
                    hub.data["local_client_list"].append(device_sn)
                _LOGGER.info("Local device connected: %s @ %s", device_sn, device_ip)
            except Exception as exc:
                _LOGGER.error("Local device connection failed: %s, %s", device_sn, exc)
                hub.local_client.pop(device_sn, None)

    elif state_change == ServiceStateChange.Removed:
        success = await info.async_request(zeroconf, timeout=3.0)
        if not success:
            return
        properties = dict(info.properties)
        if properties.get(b"s_sn") is None:
            return
        device_sn = properties.get(b"s_sn", b"").decode("utf-8")
        local_device_list = hub.data.get("local_device_list", [])
        hub.data["local_device_list"] = [
            d for d in local_device_list if d["sn"] != device_sn
        ]
        hub.data["local_client_list"] = [
            s for s in hub.data.get("local_client_list", []) if s != device_sn
        ]
        hub.local_client.pop(device_sn, None)
        _LOGGER.info("Local device removed: %s", device_sn)
