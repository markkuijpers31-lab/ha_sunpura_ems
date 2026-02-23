"""DataUpdateCoordinators for the Sunpura EMS integration."""

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import SCAN_INTERVAL_REALTIME, SCAN_INTERVAL_STATISTICS

_LOGGER = logging.getLogger(__name__)


class EmsRealtimeCoordinator(DataUpdateCoordinator):
    """Coordinator for real-time energy flow data (30s interval)."""

    def __init__(self, hass: HomeAssistant, hub) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="ha_ems_realtime",
            update_interval=timedelta(seconds=SCAN_INTERVAL_REALTIME),
        )
        self.hub = hub

    async def _async_update_data(self) -> dict:
        try:
            await self.hub.getHomeCountData()
            home_count = dict(self.hub.total_data.get("obj") or {})

            # Flatten batDataMap: top-level batRemainingEnergy is always ""; use nested value
            bat_data = home_count.get("batDataMap") or {}
            if isinstance(bat_data, dict):
                bat_remaining = bat_data.get("batRemainingEnergy")
                if bat_remaining:
                    home_count["batRemainingEnergy"] = bat_remaining

            # Flatten pvPowerMap: individual PV string powers
            pv_map = home_count.get("pvPowerMap") or {}
            if isinstance(pv_map, dict):
                for i, (key, val) in enumerate(pv_map.items(), start=1):
                    home_count[f"pv{i}Power"] = val

            return {
                "home_count": home_count,
                "system_sn": self.hub.system_sn,
            }
        except Exception as err:
            raise UpdateFailed(f"Error fetching realtime data: {err}") from err


class EmsSlowCoordinator(DataUpdateCoordinator):
    """Coordinator for device list, AI settings and energy statistics (5min interval)."""

    def __init__(self, hass: HomeAssistant, hub) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="ha_ems_slow",
            update_interval=timedelta(seconds=SCAN_INTERVAL_STATISTICS),
        )
        self.hub = hub

    async def _async_update_data(self) -> dict:
        try:
            await self.hub.get_device_page()
            await self.hub.get_ai_system_times_with_energy_mode(
                self.hub.main_control_device_id, 0
            )
            await self.hub.get_energy_data_day(
                self.hub.plant_id, self.hub.main_control_device_id
            )
            await self.hub.get_energy_data_month(
                self.hub.plant_id, self.hub.main_control_device_id
            )
            await self.hub.get_energy_data_year(
                self.hub.plant_id, self.hub.main_control_device_id
            )
            await self.hub.get_energy_data_total(
                self.hub.plant_id, self.hub.main_control_device_id
            )
            return {
                "device_list": self.hub.data.get("device_list") or [],
                "ai_settings": self.hub.data.get("ai_system_times_with_energy_mode") or {},
                "day": (self.hub.data.get("energy_data_day") or {}).get("obj") or {},
                "month": (self.hub.data.get("energy_data_month") or {}).get("obj") or {},
                "year": (self.hub.data.get("energy_data_year") or {}).get("obj") or {},
                "total": (self.hub.data.get("energy_data_total") or {}).get("obj") or {},
            }
        except Exception as err:
            raise UpdateFailed(f"Error fetching slow data: {err}") from err
