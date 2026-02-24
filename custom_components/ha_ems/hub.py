"""Hub for the Sunpura EMS integration — wraps the cloud API and local TCP clients."""

import asyncio
import logging

from .api import ApiClient
from .const import STORAGE_DEVICE_TYPES
from .tcp_client import SunpuraDeviceClient

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# controlTime slot helpers
# ---------------------------------------------------------------------------

# Empty/disabled slot string — verified via Fase 0 (24 Feb 2026).
# Full decoded format (11 comma-separated fields):
#   enabled, startTime, endTime, powerW, f5, f6, f7, f8, f9, maxSOC, minSOC
#
# Field 4 (powerW): power in Watts, signed:
#   negative → charge from grid  (e.g. -2200 = charge at 2200 W)
#   positive → discharge/feed    (e.g. +800  = feed at 800 W)
#   zero     → slot effectively idle (but use enabled=0 for disabled slots)
#
# Field 5: always 0 (purpose unknown)
# Field 6: always 6 (fixed constant written by the Sunpura app; purpose unknown)
# Fields 7-9: always 0
# Field 10 (maxSOC): upper SOC limit for this slot (0-100 %)
# Field 11 (minSOC): lower SOC limit for this slot  (0-100 %)
_EMPTY_SLOT = "0,00:00,00:00,0,0,0,0,0,0,100,10"

# Server-side read-only fields that must NOT be included in the SET payload.
# Including them causes the API to silently reject or misprocess the request.
_READONLY_FIELDS: frozenset[str] = frozenset({
    "id", "sn", "createTime", "updateTime",
    "currentPower", "currentWorkMode",
    "modeStr", "aiActiveTime", "priceType",
    "smartModeLimitFlag",
})


def _slot_to_str(slot: dict) -> str:
    """Convert a slot dict to the Sunpura controlTime CSV string.

    Decoded format (Fase 0, verified 24 Feb 2026):
        enabled, startTime, endTime, powerW, 0, 6, 0, 0, 0, maxSOC, minSOC

    Expected slot keys:
        enabled  (bool,  default True)
        start    (str HH:MM, default "00:00")
        end      (str HH:MM, default "00:00")
        power_w  (int Watts, negative=charge / positive=discharge, default 0)
        max_soc  (int 0-100, default 100)
        min_soc  (int 0-100, default 10)
    """
    enabled = 1 if slot.get("enabled", True) else 0
    start = slot.get("start", "00:00")
    end = slot.get("end", "00:00")
    power_w = int(slot.get("power_w", 0))
    max_soc = int(slot.get("max_soc", 100))
    min_soc = int(slot.get("min_soc", 10))
    return f"{enabled},{start},{end},{power_w},0,6,0,0,0,{max_soc},{min_soc}"


class SunpuraHub:
    """Central hub: holds API client, plant/device state, local TCP connections."""

    def __init__(self, hass, entry, plant_id) -> None:
        self.hass = hass
        self.entry = entry
        self.plant_id = plant_id
        self.apiClient = ApiClient(hass)
        self.plant_list: list = []
        self.main_control_device_id: str | None = None
        self.total_data: dict = {}
        self.system_sn: str = ""
        self.local_client: dict = {}
        self.data: dict = {
            "local_device_list": [],
            "local_client_list": [],
            "local_device_info_data": {},
            "energy_data_day": {},
            "energy_data_month": {},
            "energy_data_year": {},
            "energy_data_total": {},
            "device_energy_data_day": {},
            "device_energy_data_hour": {},
            "device_energy_data_month": {},
            "device_energy_data_year": {},
            "device_list": [],
            "green_power_plan": {},
            "ai_system_times_with_energy_mode": {},
            "weather": {},
            "tibber_token": {},
            "ai_price": {},
            "price_chart": {},
        }

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def reLogin(self) -> None:
        """Log in (or re-login) to the cloud API."""
        await self.apiClient.login(
            self.entry.data["username"], self.entry.data["password"]
        )

    # ------------------------------------------------------------------
    # Plant / device discovery
    # ------------------------------------------------------------------

    async def getPlantList(self) -> list:
        resp = await self.apiClient.getPlantVos()
        self.plant_list = resp.get("obj") or []
        return self.plant_list

    async def getMainControlDeviceId(self) -> str | None:
        if self.plant_id is None:
            return None
        device = await self.apiClient.get_home_control_devices(self.plant_id)
        obj = device.get("obj") if device else None
        if not obj:
            return None
        self.main_control_device_id = obj[0].get("datalogSn")
        return self.main_control_device_id

    async def getHomeCountData(self) -> dict | None:
        if self.plant_id is None or self.main_control_device_id is None:
            return None
        resp = await self.apiClient.getHomeCountData(
            self.plant_id, self.main_control_device_id
        )
        if resp is None or resp.get("result") != 0:
            return None
        self.total_data = resp
        self.system_sn = (resp.get("obj") or {}).get("systemSn", "")
        return self.total_data

    async def get_device_page(self) -> None:
        data = await self.apiClient.get_device_page(self.plant_id, 1, 100, 1)
        if data.get("result") != 0:
            return
        data_list = (data.get("obj") or {}).get("dataList") or []
        device_list = []
        for item in data_list:
            resp = await self.apiClient.fetch_device_info(
                item.get("deviceType"), item.get("deviceSn")
            )
            device_info = (resp or {}).get("obj")
            if device_info is None:
                continue
            setting_info = await self.apiClient.getSettingInfo(
                device_info.get("dtc"),
                item.get("deviceSn"),
                item.get("datalogSn"),
            )
            if (setting_info or {}).get("result") == 0:
                item.update(
                    {
                        "settingInfo": setting_info.get("obj"),
                        "deviceInfo": device_info,
                    }
                )
                device_list.append(item)
        self.data["device_list"] = device_list

    # ------------------------------------------------------------------
    # Energy statistics
    # ------------------------------------------------------------------

    async def get_energy_data_day(self, plant_id, sn="") -> None:
        if not plant_id:
            return
        self.data["energy_data_day"] = await self.apiClient.get_energy_data_day(
            plant_id, sn
        )

    async def get_energy_data_month(self, plant_id, sn="") -> None:
        if not plant_id:
            return
        self.data["energy_data_month"] = await self.apiClient.get_energy_data_month(
            plant_id, sn
        )

    async def get_energy_data_year(self, plant_id, sn="") -> None:
        if not plant_id:
            return
        self.data["energy_data_year"] = await self.apiClient.get_energy_data_year(
            plant_id, sn
        )

    async def get_energy_data_total(self, plant_id, sn="") -> None:
        if not plant_id:
            return
        self.data["energy_data_total"] = await self.apiClient.get_energy_data_total(
            plant_id, sn
        )

    async def get_energy_data_day_device(self, plant_id, sn="") -> None:
        if not plant_id:
            return
        self.data["device_energy_data_day"] = await self.apiClient.get_energy_data_day(
            plant_id, sn
        )

    async def get_energy_data_hour_device(self, plant_id, sn="") -> None:
        if not plant_id:
            return
        self.data["device_energy_data_hour"] = await self.apiClient.get_energy_data_hour(
            plant_id, sn
        )

    async def get_energy_data_month_device(self, plant_id, sn="") -> None:
        if not plant_id:
            return
        self.data["device_energy_data_month"] = (
            await self.apiClient.get_energy_data_month(plant_id, sn)
        )

    async def get_energy_data_year_device(self, plant_id, sn="") -> None:
        if not plant_id:
            return
        self.data["device_energy_data_year"] = await self.apiClient.get_energy_data_year(
            plant_id, sn
        )

    # ------------------------------------------------------------------
    # AI / energy management settings
    # ------------------------------------------------------------------

    async def get_ai_system_times_with_energy_mode(
        self, datalog_sn, mode
    ) -> dict:
        if not datalog_sn or mode == "":
            return {}
        resp = await self.apiClient.getAiSystemTimesWithEnergyMode(datalog_sn, mode)
        self.data["ai_system_times_with_energy_mode"] = resp
        return resp or {}

    async def set_ai_system_times_with_energy_mode(self, data: dict) -> dict:
        # Strip server-side read-only fields before sending to avoid silent rejection
        clean = {k: v for k, v in data.items()
                 if k not in _READONLY_FIELDS and v is not None}
        resp = await self.apiClient.setAiSystemTimesWithEnergyMode(clean)
        if (resp or {}).get("result") == 0:
            # Update cache immediately so push_schedule reads fresh values
            cached = self.data.get("ai_system_times_with_energy_mode") or {}
            obj = (cached.get("obj") if isinstance(cached, dict) else None) or {}
            for k, v in clean.items():
                obj[k] = v
            if isinstance(cached, dict):
                cached["obj"] = obj
            else:
                self.data["ai_system_times_with_energy_mode"] = {"obj": obj}
        return resp or {}

    async def push_schedule(self, slots: list[dict], dry_run: bool = False) -> dict:
        """Write up to 16 controlTime slots in Custom mode (energyMode=2).

        Args:
            slots:   List of slot dicts (see _slot_to_str for keys).
                     Unused slots (beyond len(slots)) are zeroed automatically.
            dry_run: If True, log the payload but do NOT call the API.

        Returns:
            API response dict (or the payload dict when dry_run=True).
        """
        if not self.main_control_device_id:
            _LOGGER.error("push_schedule: main_control_device_id not set")
            return {"result": -1, "msg": "No main control device"}

        # HA service data may arrive as a dict (keyed by index) instead of a
        # list when the YAML is parsed by the service call handler.
        if isinstance(slots, dict):
            slots = list(slots.values())
        slots = list(slots) if slots else []

        # Build a clean payload from the cached GET response.
        # Strategy: copy all writable, non-null fields from the cached obj, then
        # overlay our schedule changes.  Explicitly skip:
        #   - _READONLY_FIELDS (server-side metadata — cause silent API rejection)
        #   - null/None values (including controlTime17-96 which are always null)
        #   - any controlTime key (we set all 16 ourselves below)
        cached_obj = (
            self.data.get("ai_system_times_with_energy_mode") or {}
        ).get("obj") or {}

        payload: dict = {}
        for k, v in cached_obj.items():
            if k in _READONLY_FIELDS:
                continue
            if v is None:
                continue
            if k.startswith("controlTime"):
                continue  # overwritten below
            payload[k] = v

        # Schedule fields
        payload["energyMode"] = 2  # Custom mode
        payload["datalogSn"] = self.main_control_device_id

        # Write provided slots (max 16)
        active = slots[:16]
        for i, slot in enumerate(active, start=1):
            s = _slot_to_str(slot)
            payload[f"controlTime{i}"] = s
            _LOGGER.debug("push_schedule slot %d: %s", i, s)

        # Always fill all 16 slots — unused ones get the empty string
        for i in range(len(active) + 1, 17):
            payload[f"controlTime{i}"] = _EMPTY_SLOT

        _LOGGER.info(
            "push_schedule: %d active slot(s), payload fields=%s",
            len(active),
            sorted(payload.keys()),
        )

        if dry_run:
            _LOGGER.info("push_schedule DRY RUN payload: %s", payload)
            return {"result": 0, "dry_run": True, "payload": payload}

        resp = await self.set_ai_system_times_with_energy_mode(payload)
        _LOGGER.info("push_schedule API response: %s", resp)
        return resp

    async def set_ai_link_mode(self, datalog_sn, flag) -> dict | None:
        if not datalog_sn or flag is None:
            return None
        device = next(
            (d for d in self.data.get("device_list", []) if d.get("datalogSn") == datalog_sn),
            None,
        )
        if device is None:
            # Fall back to non-third-party mode
            resp = await self.apiClient.setAiMode(datalog_sn, flag)
        elif device.get("isThird") == 1:
            resp = await self.apiClient.setAiModeWithThird(
                device.get("deviceType", ""), datalog_sn, flag
            )
        else:
            resp = await self.apiClient.setAiMode(datalog_sn, flag)
        await asyncio.sleep(0.03)
        return resp

    async def set_ai_pre_mode(self, data) -> dict:
        resp = await self.apiClient.setAiPreMode(data)
        self.data["set_ai_pre_mode"] = resp
        return resp or {}

    # ------------------------------------------------------------------
    # Device control
    # ------------------------------------------------------------------

    async def switch(self, sn, v) -> None:
        if sn is None or v is None:
            return
        for device in self.data.get("device_list", []):
            if device.get("deviceSn") == sn:
                icon_type = device.get("iconType")
                if icon_type == 5:
                    await self.apiClient.switch_socket(sn, v)
                elif icon_type == 6:
                    await self.apiClient.switch_charger(sn, v)
                break

    async def set_device_name(self, device_sn, device_name) -> None:
        if not device_sn or not device_name:
            return
        await self.apiClient.setDeviceName(device_sn, device_name)

    async def set_smart_socket_mode(
        self, datalog_sn, smart_socket_mode, bat_basic_dis_charge_power=0, basic_dis_charge_enable=0
    ) -> dict:
        resp = await self.apiClient.setSmartSocketMode(
            datalog_sn, smart_socket_mode, bat_basic_dis_charge_power, basic_dis_charge_enable
        )
        return resp or {}

    async def set_master_slave_type(self, master_sn) -> dict:
        resp = await self.apiClient.setDeviceMsType(master_sn, self.plant_id)
        await asyncio.sleep(0.03)
        return resp or {}

    async def set_phase_detection(self, device_sn, datalog_sn, plant_id) -> dict:
        resp = await self.apiClient.setPhaseDetection(device_sn, datalog_sn, plant_id)
        await asyncio.sleep(0.03)
        return resp or {}

    async def check_firmware_version(self, device_sn, upgrade_type) -> dict:
        resp = await self.apiClient.checkFirmwareVersion(device_sn, upgrade_type)
        await asyncio.sleep(0.03)
        return resp or {}

    async def set_external_device(self, data) -> dict:
        resp = await self.apiClient.setExternalDevice(data)
        return resp or {}

    async def async_set_setting_info(self, data) -> dict:
        resp = await self.apiClient.setSettingInfo(data)
        await asyncio.sleep(0.03)
        return resp or {}

    async def async_set_custom_params(self, data) -> dict:
        resp = await self.apiClient.setCustomParams(data)
        return resp or {}

    async def async_check_zero_feed(self, datalog_sn, plant_id, power_mode, ai_mode) -> dict:
        resp = await self.apiClient.checkZeroFeed(datalog_sn, plant_id, power_mode, ai_mode)
        await asyncio.sleep(0.03)
        return resp or {}

    # ------------------------------------------------------------------
    # Green power plan
    # ------------------------------------------------------------------

    async def green_power_plan(self, datalog_sn, plant_id) -> None:
        if not datalog_sn or not plant_id:
            return
        self.data["green_power_plan"] = await self.apiClient.getGreenPowerPlan(
            datalog_sn, plant_id
        )

    async def update_green_power_plan(self, data) -> dict:
        resp = await self.apiClient.updateGreenPowerPlan(data)
        await asyncio.sleep(0.03)
        return resp or {}

    # ------------------------------------------------------------------
    # Pricing / Tibber
    # ------------------------------------------------------------------

    async def get_tibber_token(self) -> dict:
        resp = await self.apiClient.getTibberToken(self.plant_id, self.system_sn)
        self.data["tibber_token"] = resp
        return resp or {}

    async def add_tibber_token(self, plant_id, system_sn, token) -> dict:
        resp = await self.apiClient.addTibberToken(plant_id, system_sn, token)
        await asyncio.sleep(0.03)
        return resp or {}

    async def get_price_company_by_plant_id(self, datalog_sn=None) -> dict:
        return await self.apiClient.getPriceCompanyByPlantId(self.plant_id, datalog_sn) or {}

    async def add_provider(self, data) -> dict:
        return await self.apiClient.addProvider(data) or {}

    async def set_price_company(self, data) -> dict:
        resp = await self.apiClient.setPriceCompany(data)
        await asyncio.sleep(0.03)
        return resp or {}

    async def get_ai_price(self, price_type) -> dict:
        resp = await self.apiClient.getAiPrice(self.plant_id, price_type)
        self.data["ai_price"] = resp
        return resp or {}

    async def save_ai_price(self, data) -> dict:
        resp = await self.apiClient.saveAiPrice(data)
        await asyncio.sleep(0.03)
        return resp or {}

    async def generate_ai_price(self, datalog_sn, ec_version, interval_type) -> dict:
        resp = await self.apiClient.generateAiPrice(
            self.plant_id, datalog_sn, ec_version, interval_type
        )
        await asyncio.sleep(0.03)
        return resp or {}

    async def get_price_chart(
        self, time, price_company, plant_id, datalog_sn, tax_flag, price_type, interval_type, price_area
    ) -> dict:
        resp = await self.apiClient.getPriceChart(
            time, price_company, plant_id, datalog_sn, tax_flag, price_type, interval_type, price_area
        )
        self.data["price_chart"] = resp
        return resp or {}

    async def get_dict_data_by_type_id(self) -> dict:
        return await self.apiClient.getDictDataByTypeId() or {}

    async def get_dict_data_by_type_id_number(self, type_id) -> dict:
        if not type_id:
            return {}
        return await self.apiClient.getDictDataByTypeIdNumber(type_id) or {}

    # ------------------------------------------------------------------
    # Scheduling / reservations
    # ------------------------------------------------------------------

    async def get_ai_use_electricity(self, device_sn, device_type) -> dict:
        return await self.apiClient.getAiUseElectricity(device_sn, device_type) or {}

    async def get_ai_use_strategy(self, data) -> dict:
        return await self.apiClient.getAiUseStrategy(data) or {}

    async def get_smart_device_list(self) -> dict:
        return await self.apiClient.getSmartDeviceList(
            self.plant_id, self.main_control_device_id
        ) or {}

    async def check_meter_by_plant_id(self) -> dict:
        return await self.apiClient.checkMeterByPlantId(self.plant_id) or {}

    # ------------------------------------------------------------------
    # Local TCP device communication
    # ------------------------------------------------------------------

    async def async_local_device_connection(self, device_sn) -> str:
        curr_client = self.local_client.get(device_sn)
        if curr_client:
            curr_client.tcp_manager.close()
        for device in self.data.get("local_device_list", []):
            if device["sn"] == device_sn and device["type"] in STORAGE_DEVICE_TYPES:
                try:
                    client = SunpuraDeviceClient(device["ip"], device["port"])
                    await client.connect()
                    self.local_client[device_sn] = client
                    if device_sn not in self.data["local_client_list"]:
                        self.data["local_client_list"].append(device_sn)
                    return "Connection successful"
                except Exception as exc:
                    _LOGGER.error("Local device connection failed: %s, %s", device_sn, exc)
                    return "Connection error"
        return "Device not found"

    async def _get_local_client(self, device_sn):
        return self.local_client.get(device_sn)

    async def async_local_get_device_parms(self, device_sn) -> dict | None:
        client = await self._get_local_client(device_sn)
        if client is None:
            return None
        resp = await client.get_device_parms()
        if resp is not None:
            self.data["local_device_info_data"][device_sn] = resp
        return resp

    async def async_local_energy_enable(self, device_sn, v) -> None:
        if not device_sn or v is None:
            return
        client = self.local_client.get(device_sn)
        if client:
            await client.set_device_control_parms({"3000": v})

    async def async_local_smart_mode_switch(self, device_sn) -> None:
        if not device_sn:
            return
        client = self.local_client.get(device_sn)
        if client:
            await client.set_device_control_parms(
                {"3000": "1", "3021": "1", "3022": "1", "3030": "0"}
            )

    async def async_local_custom_mode_switch(self, device_sn, data) -> None:
        if not device_sn:
            return
        client = self.local_client.get(device_sn)
        if client:
            await client.set_device_control_parms(
                {"3000": "1", "3021": "0", "3022": "0", "3030": "1", **(data or {})}
            )

    async def async_local_get_energy_control_parms(self, device_sn, data) -> dict | None:
        if not device_sn:
            return None
        client = self.local_client.get(device_sn)
        if client is None:
            return {"message": "Device not connected"}
        return await client.get_device_control_parms(data)

    async def set_device_control_parms(self, device_sn, data) -> dict | None:
        if not device_sn:
            return None
        client = self.local_client.get(device_sn)
        if client is None:
            return {"message": "Device not connected"}
        return await client.set_device_control_parms(data)

    async def set_socket_control(self, device_sn, dev_addr, is_third_party, data) -> dict | None:
        if not device_sn or not dev_addr:
            return None
        client = self.local_client.get(device_sn)
        if client is None:
            return {"message": "Device not connected"}
        return await client.set_socket_control(dev_addr, is_third_party, data)

    async def set_hot_tub_control(self, device_sn, dev_addr, is_third_party, data) -> dict | None:
        if not device_sn or not dev_addr:
            return None
        client = self.local_client.get(device_sn)
        if client is None:
            return {"message": "Device not connected"}
        return await client.set_hot_tub_control(dev_addr, is_third_party, data)

    async def set_relay_control(self, device_sn, dev_addr, is_third_party, data) -> dict | None:
        if not device_sn or not dev_addr:
            return None
        client = self.local_client.get(device_sn)
        if client is None:
            return {"message": "Device not connected"}
        return await client.set_relay_control(dev_addr, is_third_party, data)

    async def transmit_data(self, device_sn, function_code, transmitted_data) -> dict | None:
        """Send raw data transmission to local device."""
        if not device_sn or not function_code:
            return None
        client = self.local_client.get(device_sn)
        if client is None:
            return {"message": "Device not connected"}
        return await client.transmit_data(function_code, transmitted_data)

    async def get_ems_register(self, device_sn, register_addr) -> dict | None:
        if not device_sn or register_addr is None:
            return None
        client = self.local_client.get(device_sn)
        if client is None:
            return {"message": "Device not connected"}
        return await client.get_ems_register(register_addr)

    async def set_ems_register(self, device_sn, data) -> dict | None:
        if not device_sn or data is None:
            return None
        client = self.local_client.get(device_sn)
        if client is None:
            return {"message": "Device not connected"}
        return await client.set_ems_register(data)

    async def get_weather(self, plant_id) -> None:
        if not plant_id:
            return
        self.data["weather"] = await self.apiClient.get_weather(plant_id)
