import asyncio
import json
import logging
from typing import Any

from .tcp_manager import TCPClientManager

_LOGGER = logging.getLogger(__name__)


class SunpuraDeviceClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.tcp_manager = TCPClientManager.get_instance(host, port, timeout=5)
        self.serial_number = 1  # Can be made dynamic or read from config
        self.is_connected = False
        self.lock = asyncio.Lock()
        self.response_lock = asyncio.Lock()

    async def connect(self):
        """Ensure the TCP connection is established."""
        _LOGGER.info(f"Connecting to device: {self.host}:{self.port}")
        try:
            await self.tcp_manager.connect()
            self.is_connected = True
        except Exception as e:
            _LOGGER.warning(
                f"Connection to {self.host}:{self.port} timed out — check if device is online"
            )
            raise e

    async def get_device_parms(self) -> dict[str, Any] | None:
        """Send request and retrieve device energy parameters."""
        try:
            _LOGGER.info(f"Fetching device parameters: {self.host}:{self.port}")
            res = await self.fetch_data("EnergyParameter")
            _LOGGER.debug(f"Received response:{self.host} {res}")
            return res
        except Exception as e:
            _LOGGER.error(f"Failed to fetch device parameters: {self.host}:{self.port} {e}")
            return None

    async def get_device_control_parms(self, custome_parms):
        """Fetch device control parameters."""
        _LOGGER.info(f"Fetching device control info: {self.host}:{self.port}")
        # {
        #     "RegControlAddr": [3000,3001]
        # }
        params = {"RegControlAddr": custome_parms}
        res = await self.fetch_data("Energycontrolparameters", params)
        _LOGGER.info(f"Received response: {res}")
        return res

    async def set_device_control_parms(self, custome_parms: dict | None = None):
        """Send request to set device control parameters."""
        _LOGGER.info(f"Setting device control parameters: {self.host}:{self.port}")
        params = {"SetControlInfo": custome_parms}
        res = await self.set_control_parms("Energycontrolparameters", params)
        _LOGGER.info(f"Received response: {res}")
        return res

    # Socket control
    async def set_socket_control(
        self, dev_addr: int, is_third_party: bool, custome_parms: dict
    ) -> dict[str, Any] | None:
        """Set socket control parameters."""
        self.serial_number = self.serial_number + 1
        params = {
            "ControlsParameter": {
                "DevTypeClass": 512,
                "DevAddr": dev_addr,
                "IsThirdParty": is_third_party,
                "CommSerialNum": self.serial_number,
                "DevType": 200,
                "Param": custome_parms,
            }
        }
        res = await self.set_control_parms("SubDeviceControl", params)
        return res

    # Relay control (hot tub)
    async def set_hot_tub_control(
        self, dev_addr: int, is_third_party: bool, custome_parms: dict
    ) -> dict[str, Any] | None:
        """Set hot tub relay control parameters."""
        self.serial_number = self.serial_number + 1
        params = {
            "ControlsParameter": {
                "DevTypeClass": 1280,
                "DevAddr": dev_addr,
                "IsThirdParty": is_third_party,
                "CommSerialNum": self.serial_number,
                "DevType": 80,
                "Param": custome_parms,
            }
        }
        res = await self.set_control_parms("SubDeviceControl", params)
        return res

    # Relay control
    async def set_relay_control(
        self, dev_addr: int, is_third_party: bool, custome_parms: dict
    ) -> dict[str, Any] | None:
        """Set relay control parameters."""
        self.serial_number = self.serial_number + 1
        params = {
            "ControlsParameter": {
                "DevTypeClass": 1536,
                "DevAddr": dev_addr,
                "IsThirdParty": is_third_party,
                "CommSerialNum": self.serial_number,
                "DevType": 230,
                "Param": custome_parms,
            }
        }
        res = await self.set_control_parms("SubDeviceControl", params)
        return res

    # transmit_data
    async def transmit_data(
        self, function_code, transmit_data
    ) -> dict[str, Any] | None:
        """Transmit raw data to the device."""
        self.serial_number = self.serial_number + 1
        params = {
            "SetCommand": {
                "FunctionCode": function_code,
                "TransmittedData": transmit_data,
            }
        }
        res = await self.set_control_parms("DataTransmission", params)
        return res

    # Register read
    async def get_ems_register(self, reg_addr):
        """Read an EMS register from the device."""
        _LOGGER.info(f"Reading EMS register: {self.host}:{self.port}")
        # {
        #     "RegControlAddr": [3000,3001]
        # }
        params = {"RegDeviceManagementAddr": reg_addr}
        res = await self.fetch_data("DeviceManagement", params)
        _LOGGER.info(f"Received response: {res}")
        return res

    # EMS register write command — dict of register addresses to values
    async def set_ems_register(self, data) -> dict[str, Any] | None:
        """Write EMS register values to the device."""
        self.serial_number = self.serial_number + 1
        params = {"DeviceManagementAddr": data}
        res = await self.set_control_parms("DeviceManagement", params)
        return res

    async def disconnect(self):
        """Close the TCP connection."""
        await self.tcp_manager.close()

    async def fetch_data(
        self, command: str, params: dict | None = None
    ) -> dict[str, Any] | None:
        """Generic device data fetch.

        Args:
            command: Command to execute (e.g. "EnergyParameter")
            params: Optional command parameters

        Returns:
            Device response dict or None on failure
        """
        async with self.lock:
            if not self.is_connected:
                return None

            try:
                # Get connection (once, to avoid duplicate connects)
                reader, writer = await self.tcp_manager.get_reader_writer()

                # Build request (merge common fields)
                self.serial_number = self.serial_number + 1
                request = {
                    "Get": command,
                    "SerialNumber": self.serial_number,
                    "CommandSource": "HA",
                    **(params or {}),
                }
                # Send request
                request_str = json.dumps(request) + "\n"
                writer.write(request_str.encode("utf-8"))
                await writer.drain()
                _LOGGER.debug(f"Sent request: {request_str.strip()}")

                # Read response
                return await self._read_response(reader)
            except (ConnectionResetError, OSError, asyncio.IncompleteReadError) as e:
                _LOGGER.warning(
                    f"Connection error during fetch_data: {e}, reconnecting..."
                )
                await self.tcp_manager.reconnect()
                return None
            except Exception as e:
                _LOGGER.error(f"Error fetching data: {e}", exc_info=True)
                return None

    async def set_control_parms(
        self, command: str, params: dict | None = None
    ) -> dict[str, Any] | None:
        """Generic device control parameter setter.

        Args:
            command: Command to execute (e.g. "EnergyParameter")
            params: Optional command parameters

        Returns:
            Device response dict or None on failure
        """
        async with self.lock:
            try:
                # Get connection (once, to avoid duplicate connects)
                reader, writer = await self.tcp_manager.get_reader_writer()

                # Build request (merge common fields)
                self.serial_number = self.serial_number + 1
                request = {
                    "Set": command,
                    "SerialNumber": self.serial_number,
                    "CommandSource": "HA",
                    **(params or {}),
                }
                _LOGGER.debug(f"Built request: {request}")

                # Send request
                request_str = json.dumps(request) + "\n"
                writer.write(request_str.encode("utf-8"))
                await writer.drain()
                _LOGGER.debug(f"Sent request: {request_str.strip()}")

                # Read response
                return await self._read_response(reader)
            except (ConnectionResetError, OSError, asyncio.IncompleteReadError) as e:
                _LOGGER.warning(
                    f"Connection error during fetch_data: {e}, reconnecting..."
                )
                await self.tcp_manager.reconnect()
                return None
            except Exception as e:
                _LOGGER.error(f"Error fetching data: {e}", exc_info=True)
                return None

    async def _read_response(
        self, reader: asyncio.StreamReader
    ) -> dict[str, Any] | None:
        """Read and parse JSON response from device."""
        async with self.response_lock:
            buffer = b""
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    raise ConnectionResetError("Device closed the connection")

                buffer += chunk
                try:
                    # Attempt to parse complete JSON
                    json_data = json.loads(buffer.decode("utf-8"))
                    _LOGGER.debug(f"Received response: {json_data}")
                    return json_data
                except json.JSONDecodeError:
                    # Wait for more data (non-blocking)
                    await asyncio.sleep(0.1)
