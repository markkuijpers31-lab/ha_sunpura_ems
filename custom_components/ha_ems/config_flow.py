"""Config flow for the Sunpura EMS integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import ClientError
import voluptuous as vol

from homeassistant import config_entries

from .api import ApiClient, md5_hash
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Sunpura EMS."""

    def __init__(self) -> None:
        """Initialize."""
        self.username = None
        self.password = None
        self.data = {}
        self._ApiClient = None
        self.familys = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> dict:
        """Handle the initial step with login credentials."""
        errors = {}
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            username = user_input["username"]
            password = md5_hash(user_input["password"])
            self._ApiClient = ApiClient(self.hass)
            try:
                await self._ApiClient.login(username, password)
                self.data.update(
                    {
                        "username": username,
                        "password": password,
                    }
                )
                resp = await self._ApiClient.getPlantVos()
                self.familys = {
                    str(item["id"]): item["plantName"] for item in resp["obj"]
                }
                return await self.async_step_select_family()
            except ClientError as e:
                _LOGGER.error("Network error during login: %s", e)
                errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.info("Login failed: {e}")
                errors["base"] = f"invalid_auth: {e}"

        # Show login form
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("username"): str,
                    vol.Required("password"): str,
                }
            ),
            description_placeholders={"title": "Sunpura EMS"},
            errors=errors,
        )

    async def async_step_select_family(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle the power station step."""
        # Abort if already configured
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            family_id = user_input["family"]
            family_name = self.familys[family_id]
            self.data.update(
                {
                    "family": family_id,
                    "family_name": family_name,
                }
            )
            return self.async_create_entry(
                title=f"Integration - {family_name}", data=self.data
            )
        return self.async_show_form(
            step_id="select_family",
            data_schema=vol.Schema(
                {
                    vol.Required("family"): vol.In(self.familys),
                }
            ),
        )

