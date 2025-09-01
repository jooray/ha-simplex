from __future__ import annotations

from typing import Any
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback

from .const import (
    DOMAIN,
    CONF_WS_URL,
    CONF_TARGETS,
    DEFAULT_WS_URL,
    ERROR_CANNOT_CONNECT,
    ERROR_INVITE_FAILED,
)
from .websocket_client import (
    SimplexWsClient,
    SimplexWsError,
    SimplexWsTimeoutError,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER = vol.Schema({vol.Optional(CONF_WS_URL, default=DEFAULT_WS_URL): str})
STEP_ADD_TARGET = vol.Schema(
    {
        vol.Required("alias"): str,
        vol.Required("invite_link"): str,
    }
)


async def _validate_ws(hass: HomeAssistant, ws_url: str) -> None:
    client = SimplexWsClient(ws_url, hass.loop)
    try:
        await client.connect()
    finally:
        await client.close()


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            ws_url = user_input.get(CONF_WS_URL, DEFAULT_WS_URL)
            try:
                await _validate_ws(self.hass, ws_url)
                return self.async_create_entry(title="SimpleX", data={CONF_WS_URL: ws_url})
            except Exception:
                errors["base"] = ERROR_CANNOT_CONNECT
        return self.async_show_form(step_id="user", data_schema=STEP_USER, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry):
        self.config_entry = entry

    async def async_step_init(self, user_input=None):
        """Show the main options menu."""
        targets = self.config_entry.options.get(CONF_TARGETS, {})
        
        if user_input is not None:
            if user_input.get("action") == "add_target":
                return await self.async_step_add_target()
            elif user_input.get("action") == "manage_targets":
                return await self.async_step_manage_targets()
        
        # Create menu schema based on whether targets exist
        menu_options = [("add_target", "Add new recipient alias")]
        if targets:
            menu_options.append(("manage_targets", "Manage existing aliases"))
        
        menu_schema = vol.Schema({
            vol.Required("action"): vol.In(dict(menu_options))
        })
        
        target_list = "\n".join([f"• {alias}" for alias in targets.keys()]) if targets else "No aliases configured yet."
        
        return self.async_show_form(
            step_id="init",
            data_schema=menu_schema,
            description_placeholders={
                "target_list": target_list,
                "ws_url": self.config_entry.data.get(CONF_WS_URL, DEFAULT_WS_URL),
            },
        )

    async def async_step_manage_targets(self, user_input=None):
        """Show existing targets and allow deletion."""
        targets = self.config_entry.options.get(CONF_TARGETS, {})
        
        if user_input is not None:
            if user_input.get("action") == "back":
                return await self.async_step_init()
            elif user_input.get("delete_target"):
                # Remove the selected target
                alias_to_delete = user_input["delete_target"]
                if alias_to_delete in targets:
                    new_targets = {k: v for k, v in targets.items() if k != alias_to_delete}
                    new_options = {**self.config_entry.options, CONF_TARGETS: new_targets}
                    return self.async_create_entry(title="", data=new_options)
        
        if not targets:
            return await self.async_step_init()
        
        # Create schema for target management
        target_options = list(targets.keys())
        manage_schema = vol.Schema({
            vol.Optional("delete_target"): vol.In(target_options),
            vol.Optional("action"): vol.In({"back": "← Back to main menu"})
        })
        
        target_details = []
        for alias, target_data in targets.items():
            chat_ref = target_data.get("chat_ref", "Unknown")
            target_details.append(f"• {alias}: {chat_ref}")
        
        return self.async_show_form(
            step_id="manage_targets",
            data_schema=manage_schema,
            description_placeholders={
                "target_details": "\n".join(target_details),
            },
        )

    async def async_step_add_target(self, user_input=None):
        """Add a new target alias."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {
            "ws_url": self.config_entry.data.get(CONF_WS_URL, DEFAULT_WS_URL),
        }
        
        if user_input is not None:
            if user_input.get("action") == "back":
                return await self.async_step_init()
                
            alias = user_input["alias"].strip()
            link = user_input["invite_link"].strip()
            
            # Check if alias already exists
            targets = dict(self.config_entry.options.get(CONF_TARGETS, {}))
            if alias in targets:
                errors["alias"] = "alias_exists"
            else:
                try:
                    client: SimplexWsClient = self.hass.data[DOMAIN][self.config_entry.entry_id]["client"]
                    # Allow more time for the invite acceptance handshake.
                    resp = await client.accept_invite_link(link, timeout=60.0)
                    chat_ref = resp.get("chat_ref")
                    if not chat_ref:
                        _LOGGER.error("Invite flow completed but no chat_ref in response: %s", resp)
                        raise ValueError("No chat_ref in response")

                    targets[alias] = {"chat_ref": chat_ref}
                    new_options = {**self.config_entry.options, CONF_TARGETS: targets}
                    return self.async_create_entry(title="", data=new_options)
                except SimplexWsTimeoutError as exc:
                    _LOGGER.warning("Timed out while accepting invite: %s", exc)
                    errors["base"] = "invite_timeout"
                    description_placeholders["error_details"] = str(exc)
                except SimplexWsError as exc:
                    # Try to map common reasons to specific UI errors
                    reason = (exc.reason or exc.message or "").lower()
                    description_placeholders["error_details"] = exc.message or exc.reason or repr(exc)
                    if any(x in reason for x in ("invalid", "malformed", "bad link")):
                        errors["base"] = "invite_invalid"
                    elif any(x in reason for x in ("expired", "revoked")):
                        errors["base"] = "invite_expired"
                    elif any(x in reason for x in ("not connected", "no websocket")):
                        errors["base"] = "ws_not_connected"
                    else:
                        errors["base"] = ERROR_INVITE_FAILED
                except Exception as exc:
                    _LOGGER.exception("Failed to accept invite link: %s", exc)
                    errors["base"] = ERROR_INVITE_FAILED
                    description_placeholders["error_details"] = str(exc)

        # Add back button to schema
        add_target_schema = vol.Schema({
            vol.Required("alias"): str,
            vol.Required("invite_link"): str,
            vol.Optional("action"): vol.In({"back": "← Back to main menu"})
        })

        return self.async_show_form(
            step_id="add_target",
            data_schema=add_target_schema,
            errors=errors,
            description_placeholders=description_placeholders,
        )
