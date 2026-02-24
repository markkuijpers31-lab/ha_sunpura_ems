"""API client for the API.

This module provides an API client for the ha-ems api.
"""

from datetime import datetime
import hashlib
import json
import logging

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import BASE_URL

_LOGGER = logging.getLogger(__name__)

langs = {
    "zh-hans": "zh-CN",
    "zh-hant": "zh-HK",
    # ar_QA
    "ar": "ar-QA",
    # de_DE
    "de": "de-DE",
    # en_US
    "en": "en-US",
    # es_ES
    "es": "es-ES",
    # fr_FR
    "fr": "fr-FR",
    # it_IT
    "it": "it-IT",
    # nl_NL
    "nl": "nl-NL",
    # ru_RU
    "ru": "ru-RU",
    # th_TH
    "th": "th-TH",
    # vi_VN
    "vi": "vi-VN",
}


def md5_hash(password: str):
    """Return an MD5 hash for the given password."""
    # Create MD5 hash object
    hasher = hashlib.md5()
    # Encode password to bytes and feed to hasher
    hasher.update(password.encode("utf-8"))

    # Return hex digest
    return hasher.hexdigest()


class ApiClient:
    """API client for the API."""

    def __init__(self, hass=None):
        """Initialize the API client.

        Args:
            hass: Home Assistant instance
            username: API username
            password: API password
            current_language: Current language setting (default: zh-CN)
            api_base_url: Base URL for the API (default: production URL)
        """
        self.hass = hass
        self.username: str = ""
        self.password: str = ""
        self.token: str = ""
        self.language: str = "en-US"
        self._session = async_get_clientsession(hass)
        

    async def setLanguage(self, language):
        self.language = langs.get(language, "en-US")
    async def getLanguage(self):
        return self.language
    async def post(self, headers, url, params=None, data=None) -> dict | None:
        """Make a POST request."""
        header = {
            "Content-Type": "application/json",
            "Accept-Language": await self.getLanguage(),
            "token": self.token,
            "projectType": "1",
        }
        if headers:
            header.update(headers)
        json_data = json.dumps(data)
        try:
            async with self._session.post(
                url, headers=header, params=params, data=json_data
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning(
                        "Post failed: %s, %s", resp.status, await resp.text()
                    )
                    return {}
                return await resp.json()
        except Exception as e:
            _LOGGER.error(
                "Post failed: e=%s, parms=%s, data=%s, url=%s", e, params, data, url
            )
            return {}
    async def form_post(self, headers, url, params=None) -> dict | None:
        """Make a POST request with form data (multipart/form-data)."""
        header = {
            "Content-Type": "application/json",
            "Accept-Language": await self.getLanguage(),
            "token": self.token,
            "projectType": "1",
        }
        if headers:
            header.update(headers)
        try:
            async with self._session.post(url, headers=header, params=params) as resp:
                if resp.status >= 200 and resp.status < 300:
                    return await resp.json()
                _LOGGER.error(
                    "FORM POST_form failed with status %d: %s",
                    resp.status,
                    await resp.text(),
                )
                return {}
        except Exception as e:
            _LOGGER.error("Post failed_form: e=%s, parms=%s, url=%s", e, params, url)
            return {}

    async def get(self, headers, url, params=None, data=None) -> dict | None:
        """Make a GET request."""
        header = {
            "Content-Type": "application/json",
            "token": self.token,
            "Accept-Language": await self.getLanguage(),
            "projectType": "1",
        }
        if headers:
            header.update(headers)
        try:
            json_data = json.dumps(data)
            async with self._session.get(
                url, headers=header, params=params, data=json_data
            ) as resp:
                try:
                    if resp.status != 200:
                        _LOGGER.error(
                            "Get failed: %s, %s", resp.status, await resp.text()
                        )
                        return {}
                    data = await resp.json()    
                    if (data.get('result') == 10000):
                        await self.login(self.username,self.password)
                    return data
                except Exception as e:
                    _LOGGER.error("Get failed: %s, %s", e, await resp.text())
                    return {}
        except Exception as e:
            _LOGGER.error(
                "Get failed: e=%s, parms=%s, data=%s, url=%s", e, params, data, url
            )
            return {}

    async def login(self, username, password):
        """Login to the API."""
        _LOGGER.debug("Login to the ha-ems")
        url = BASE_URL + "/user/login"
        data = {
            "email": username,
            "password": password,
            "phoneOs": 1,
            "phoneModel": "1.1",
            "appVersion": "V1.1",
        }
        resp = await self.post(
            None,
            url,
            data=data,
        )
        if resp.get("result") != 0:
            _LOGGER.error("Login failed: %s", resp.get("msg"))
            raise Exception("check username and password.")
        await self.setLanguage(self.hass.config.language.lower())
        res = resp.get("obj")
        _LOGGER.debug("Login success: %s", res)
        self.username = username
        self.password = password
        self.token = res.get("token")
        return resp

    async def getPlantVos(self):
        """Get plant vos."""
        url = BASE_URL + "/plant/getPlantVos"
        try:
            resp = await self.get({}, url, {})
            return resp
        except Exception as e:
            _LOGGER.error("Get plant vos failed: %s", e)
            return {}

    async def get_home_control_devices(self, senceId):
        url = BASE_URL + "/energy/getHomeControlSn/" + str(senceId)
        try:
            resp = await self.get({}, url)
            _LOGGER.debug(f"get_home_control_devices: {resp}")
            return resp
        except Exception as e:
            _LOGGER.error("Get home control devices failed: %s", e)
            return {}

    async def getHomeCountData(self, scenceId: int, sn: str):
        """Fetch home energy flow data."""
        try:
            url = BASE_URL + "/energy/getHomeCountData"
            return await self.post(
                {}, url, params={"plantId": scenceId, "deviceSn": sn}
            )
        except Exception as e:
            _LOGGER.error("Get home count data failed: %s", e)

    async def get_energy_data_day(self, plant_id, sn=""):
        """Fetch daily plant energy data."""
        url = BASE_URL + "/energy/getEnergyDataDay"
        a = datetime.now().strftime("%Y-%m-%d")
        try:
            resp = await self.post(
                {}, url, params={"plantId": plant_id, "time": a, "deviceSn": sn}
            )
            return resp
        except Exception as e:
            _LOGGER.error("Get energy data day failed: %s", e)
    async def get_energy_data_hour(self, plant_id, sn=""):
        """Fetch hourly plant energy data."""
        url = BASE_URL + "/energy/getEnergyDataHour"
        a = datetime.now().strftime("%Y-%m-%d")
        try:
            resp = await self.post(
                {}, url, params={"plantId": plant_id, "time": a, "deviceSn": sn}
            )
            return resp
        except Exception as e:
            _LOGGER.error("Get energy data hour failed: %s", e)

    async def get_energy_data_month(self, plant_id, sn=""):
        """Fetch monthly plant energy data."""
        url = BASE_URL + "/energy/getEnergyDataMonth"
        a = datetime.now().strftime("%Y-%m")
        try:
            resp = await self.post(
                {}, url, params={"plantId": plant_id, "time": a, "deviceSn": sn}
            )
            return resp
        except Exception as e:
            _LOGGER.error("Get energy data day failed: %s", e)

    async def get_energy_data_year(self, plant_id, sn=""):
        """Fetch yearly plant energy data."""
        url = BASE_URL + "/energy/getEnergyDataYear"
        a = datetime.now().strftime("%Y")
        try:
            resp = await self.post(
                {}, url, params={"plantId": plant_id, "time": a, "deviceSn": sn}
            )
            return resp
        except Exception as e:
            _LOGGER.error("Get energy data day failed: %s", e)

    async def get_energy_data_total(self, plant_id, sn=""):
        """Fetch total (lifetime) plant energy data."""
        url = BASE_URL + "/energy/getEnergyDataTotal"
        a = datetime.now().strftime("%Y")
        try:
            resp = await self.post(
                {}, url, params={"plantId": plant_id, "time": a, "deviceSn": sn}
            )
            return resp
        except Exception as e:
            _LOGGER.error("Get energy data total failed: %s", e)

    async def get_device_page(self, plant_id, pageNow=1, pageSize=50, type=1):
        """Fetch paginated device list."""
        url = BASE_URL + "/device/getDevicePage"
        data = {
            "pageNow": pageNow,
            "pageSize": pageSize,
            "map": {"plantId": plant_id, "type": type},
        }
        res = await self.post(None, url, data=data)
        return res

    async def fetch_device_info(self, device_type, device_sn):
        """Fetch device settings by serial number."""
        url = BASE_URL + "/device/getDeviceBySn"
        # Current date as yyyy-MM-dd
        a = datetime.now().strftime("%Y-%m-%d")
        return await self.post(
            {},
            url,
            params={"deviceType": device_type, "sn": device_sn, "time": a},
        )

    async def getSettingInfo(self, dtc, deviceSn, datalogSn):
        url = BASE_URL + "/deviceSetConfig/getSettingInfo"
        resp = await self.get(
            {},
            url,
            params={
                "dtc": dtc,
                "displayType": 2,
                "deviceSn": deviceSn,
                "datalogSn": datalogSn,
            },
        )
        return resp
    async def setSettingInfo(self, data):
        url = BASE_URL + "/deviceSetConfig/setCustomParams"
        resp = await self.post(
            {},
            url,
            data=data
        )
        return resp

    async def getGreenPowerPlan(self, datalog_sn, plant_id):
        """Fetch green power plan."""
        try:
            url = BASE_URL + "/aiSystem/getGreenPowerPlan"
            resp = await self.get(
                {},
                url,
                params={"datalogSn": datalog_sn, "plantId": plant_id},
            )
            return resp
        except Exception as e:
            _LOGGER.error("Get green power plan failed: %s", e)

    async def get_weather(self, plant_id):
        """Fetch weather data for plant location."""
        url = BASE_URL + "/weather/getWeatherInfo"
        a = datetime.now().strftime("%Y")
        resp = await self.form_post({}, url, params={"plantId": plant_id})
        return resp

    async def setDeviceName(self, device_sn, device_name, typeNo=2):
        """Set device name."""
        url = BASE_URL + "/device/updateDeviceName"
        resp = await self.post(
            {"Content-Type": "application/json"},
            url,
            params={"deviceSn": device_sn, "deviceName": device_name, "type": typeNo},
        )
        return resp

    async def setDeviceParam(self, device_sn, start_addr, data):
        url = BASE_URL + "/device/setDeviceParam"
        resp = await self.post(
            {"Content-Type": "application/x-www-form-urlencoded"},
            url,
            params={
                "deviceSn": device_sn,
                "startAddr": start_addr,
                "data": data,
            },
        )
        return resp

    async def getAiSystemTimesWithEnergyMode(self, datalog_sn, energy_mode):
        try:
            url = BASE_URL + "/aiSystem/getAiSystemBySnWithEnergyMode"
            resp = await self.post(
                {},
                url,
                params={
                    "datalogSn": datalog_sn,
                    "energyMode": energy_mode,
                },
            )
            return resp
        except Exception as e:
            _LOGGER.error(f"{url},{e}")
    async def setAiSystemTimesWithEnergyMode(self, data):
        url = BASE_URL + "/aiSystem/setAiSystemTimesWithEnergyMode"
        resp = await self.post(
            {"Content-Type": "application/json"},
            url,
            data=data,
        )
        return resp

    # Socket
    async def switch_socket(self, sn, v):
        """Toggle socket on/off."""
        resp = await self.setDeviceParam(sn, 0x0000, v)
        _LOGGER.info(f"Switch command response: {resp}")

    # EV charger
    async def switch_charger(self, sn, v):
        """Toggle EV charger on/off."""
        resp = await self.setDeviceParam(sn, 0x00AF, v)
        _LOGGER.info(f"Switch command response: {resp}")

    async def setDeviceMsType(self, master_sn, plant_id):
        url = BASE_URL + "/device/setDeviceMsType"
        resp = await self.post(
            {},
            url,
            params={
                "masterSn": master_sn,
                "plantId": plant_id,
            },
        )
        return resp

    async def setPhaseDetection(self, device_sn, datalog_sn, plant_ld):
        url = BASE_URL + "/device/setPhaseDetection"
        resp = await self.post(
            {},
            url,
            params={
                "deviceSn": device_sn,
                "datalogSn": datalog_sn,
                "plantId": plant_ld,
            },
        )
        return resp

    async def checkFirmwareVersion(self, device_sn, upgrade_type):
        url = BASE_URL + "/upgrade/checkFirmwareVersion"
        resp = await self.form_post(
            {}, url, params={"deviceSn": device_sn, "upgradeType": upgrade_type}
        )
        return resp

    async def updateGreenPowerPlan(self, data):
        url = BASE_URL + "/aiSystem/updateGreenPowerPlan"
        json_data = json.dumps(data)
        _LOGGER.info(f"Green power plan updated: {json_data}")
        resp = await self.post({}, url, data=data)

        return resp

    async def getSmartDeviceList(self, plant_id, main_control_device_id):
        url = BASE_URL + "/aiSystem/getSmartDeviceList"
        resp = await self.get(
            {"Content-Type": "application/json"},
            url,
            params={
                "plantId": plant_id,
                "datalogSn": main_control_device_id,
            },
        )
        return resp
    async def getMasterDeviceList(self, plant_id):
        """Fetch master/slave device list for a plant."""
        url = BASE_URL + "/device/getMasterDeviceList"
        resp = await self.get(
            {},
            url,
            params={
                "plantId": plant_id
            }
        )
        return resp
    async def setMasterDefMeter(self, plant_id, meter_sn):
        """Update the default meter for the main controller."""
        url = BASE_URL + "/device/setMasterDefMeter"
        resp = await self.post(
            {},
            url,
            params={
                "plantId": plant_id,
                "meterSn": meter_sn
            }
        )
        return resp
    async def getPhaseDetection(self, device_sn, datalog_sn, dtype):
        """Fetch phase detection info for master/slave devices."""
        url = BASE_URL + "/device/getPhaseDetection"
        resp = await self.get(
            {},
            url,
            params={
                "deviceSn": device_sn,
                "datalogSn": datalog_sn,
                "type": dtype
            }
        )
        return resp

    async def setSmartSocketMode(
        self,
        datalog_sn,
        smart_socket_mode,
        bat_basic_dis_charge_power,
        basic_dis_charge_enable,
    ):
        url = BASE_URL + "/aiSystem/setSmartSocketMode"
        resp = await self.post(
            {},
            url,
            params={
                "datalogSn": datalog_sn,
                "smartSocketMode": int(smart_socket_mode),
                "batBasicDisChargePower": bat_basic_dis_charge_power,
                "basicDisChargeEnable": int(basic_dis_charge_enable),
            },
        )
        return resp

    async def getDeviceHistoryInfo(self, sn, device_type, time, id, client_id):
        url = BASE_URL + "/device/getDeviceHistoryInfoById"
        resp = await self.get(
            {},
            url,
            params={
                "sn": sn,
                "deviceType": device_type,
                "time": time,
                "id": id,
                "clientId": client_id,
            },
        )
        return resp

    async def getPriceCompanyByPlantId(self, plant_id, datalog_sn=None):
        url = BASE_URL + "/aiSystem/getPriceCompanyByPlantId/v2/" + str(plant_id)
        resp = await self.get({}, url, params={"datalogSn": datalog_sn})
        return resp

    async def addProvider(self, data):
        url = BASE_URL + "/aiSystem/addProvider"
        resp = await self.post({}, url, data=data)
        return resp

    async def getDictDataByTypeId(self):
        url = BASE_URL + "/dict/getDictDataByTypeId/3"
        resp = await self.get({}, url)
        return resp
    async def getDictDataByTypeIdNumber(self,type_id):
        url = BASE_URL + "/dict/getDictDataByTypeId/" + str(type_id) 
        resp = await self.get({}, url)
        return resp
    async def saveAiPrice(self, data):
        url = BASE_URL + "/aiPrice/saveAiPrice"
        resp = await self.post({}, url, data=data)
        return resp

    async def getTibberToken(self, plant_id, system_sn):
        """Fetch the user's Tibber token."""
        url = BASE_URL + "/aiSystem/getTibberToken"
        resp = await self.form_post(
            {},
            url,
            params={"plantId": plant_id, "systemSn": system_sn},
        )
        return resp

    async def addTibberToken(self, plant_id, system_sn, token):
        """Bind a Tibber token to a plant."""
        url = BASE_URL + "/aiSystem/addTibberToken"
        resp = await self.form_post(
            {},
            url,
            params={
                "plantId": plant_id,
                "systemSn": system_sn,
                "token": token,
            },
        )
        return resp

    async def setPriceCompany(self, data):
        url = BASE_URL + "/aiSystem/setPriceCompany"
        resp = await self.post({}, url, data=data)
        return resp

    async def getAiPrice(self, plant_id, price_type):
        url = BASE_URL + "/aiPrice/getAiPrice"
        data = {"priceType": price_type, "plantId": plant_id}
        resp = await self.post({"Content-Type": "application/json"}, url, data=data)
        return resp

    async def getPriceChart(
        self,
        time,
        price_company,
        plant_id,
        datalog_sn,
        tax_flag,
        price_type,
        interval_type,
        price_area,
    ):
        url = BASE_URL + "/aiSystem/getPriceChart"
        resp = await self.post(
            {"Content-Type": "application/json"},
            url,
            params={
                "time": time,
                "priceCompany": price_company,
                "plantId": plant_id,
                "datalogSn": datalog_sn,
                "taxFlag": tax_flag,
                "priceType": price_type,
                "intervalType": interval_type,
                "priceArea": price_area,
            },
        )
        return resp

    async def generateAiPrice(self, plant_id, datalog_sn, ec_version, interval_type):
        url = BASE_URL + "/aiSystem/getAiPrice"
        parms = {
            "datalogSn": datalog_sn,
            "plantId": plant_id,
            "ecVersion": ec_version,
            "intervalType": interval_type,
        }
        resp = await self.get({}, url, params=parms)
        return resp

    async def checkMeterByPlantId(self, plant_id):
        url = BASE_URL + "/device/checkMeterByPlantId"
        resp = await self.get({}, url, params={"plantId": plant_id})
        return resp

    async def setCustomParamsWithThird(self, data):
        url = BASE_URL + "/device/setCustomParams"
        resp = await self.post(
            {},
            url,
            data=data,
        )
        return resp
    async def setCustomParams(self, data):
        url = BASE_URL + "/device/setCustomParams"
        resp = await self.post(
            {},
            url,
            data=data,
        )
        return resp
    async def getAiUseElectricity(self, device_sn, device_type):
        _LOGGER.info(f"Fetching AI electricity usage: {device_sn}, {device_type}")
        url = BASE_URL + "/aiSystem/getAiUseElectricity"
        resp = await self.get({},url,params={
            "deviceSn": device_sn,
            "deviceType": device_type
        })
        return resp
    async def getAiUseStrategy(self, data):
        _LOGGER.info(f"Fetching AI usage strategy: {data}")
        url = BASE_URL + "/aiSystem/getAiUseStrategy"
        resp = await self.post({},url,data=data)
        return resp
    async def setExternalDevice(self, data):
        url = BASE_URL + "/device/setExternalDevice"
        resp = await self.post(
            {},
            url,
            data=data
        )
        return resp
    
    

    async def setAiMode(self, datalogSn, flag):
        url = BASE_URL + "/datalog/setDataLogCmd"
        resp = await self.post(
            {},
            url,
            params={"datalogSn": datalogSn, "params": 98, "values": flag},
        )
        return resp

    async def setAiModeWithThird(self, device_type, device_sn, flag):
        resp = await self.setCustomParamsWithThird(
            {
                "deviceType": device_type,
                "deviceSn": device_sn,
                "object": {"linkage": flag},
            }
        )
        return resp

    async def setAiPreMode(self, data):
        url = BASE_URL + "/aiSystem/updateAiUseElectricity"
        resp = await self.post(
            {},
            url,
            data=data,
        )
        return resp
    async def checkZeroFeed(self,datalogSn,plantId,powerMode,aiMode):
        url = BASE_URL + "/aiSystem/checkZeroFeed"
        resp = await self.get(
            {},
            url,
            params={
                "datalogSn": datalogSn,
                "plantId": plantId,
                "powerMode": powerMode,
                "aiMode": aiMode,
            },
        )
        return resp
