from __future__ import annotations

from typing import Any
import asyncio
import logging
from urllib.parse import urlparse

from aiohttp import ClientError

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlowWithReload
from homeassistant.core import HomeAssistant, callback

from .const import (
    DOMAIN,
    CONF_WS_URL,
    CONF_TARGETS,
    DEFAULT_WS_URL,
    ERROR_CANNOT_CONNECT,
    ERROR_INVITE_FAILED,
    ERROR_ALIAS_EXISTS,
)
from .websocket_client import (
    SimplexWsClient,
    SimplexWsError,
    SimplexWsTimeoutError,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER = vol.Schema({vol.Optional(CONF_WS_URL, default=DEFAULT_WS_URL): str})


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
        return OptionsFlowHandler()


class OptionsFlowHandler(OptionsFlowWithReload):
    """Handle an options flow."""

    async def async_step_init(self, user_input=None):
        """Add a new alias by accepting an invite link."""
        _LOGGER.debug("async_step_init called with user_input: %s", user_input)
        errors: dict[str, str] = {}
        description_placeholders = {}

        if user_input is not None:
            _LOGGER.debug("Processing user input for new alias")
            alias = user_input.get("alias", "").strip()
            link = user_input.get("invite_link", "").strip()

            def _safe_link_for_log(l: str) -> str:
                try:
                    p = urlparse(l)
                    host = p.netloc or ""
                    if len(l) > 20:
                        tail = l[-6:]
                        return f"{p.scheme}://{host}/…<redacted>…{tail}"
                    return f"{p.scheme}://{host}/…<redacted>"
                except Exception:
                    return "…<redacted>"

            _LOGGER.debug("Alias: '%s', Link: '%s'", alias, _safe_link_for_log(link) if link else "")

            if not alias:
                errors["alias"] = "required"
            elif not link:
                errors["invite_link"] = "required"
            else:
                # Basic link sanity check to fail fast before contacting server
                try:
                    normalized = link.strip()
                    is_probably_simplex = (
                        normalized.startswith("http") and "simplex" in normalized
                    ) or normalized.startswith("simplex:")
                    if not is_probably_simplex or "#" not in normalized:
                        errors["base"] = "invite_invalid"
                        raise ValueError("Invite link format invalid")
                except Exception:
                    # If invalid, fall through to show the form with errors
                    pass

                if errors:
                    return self.async_show_form(
                        step_id="init",
                        data_schema=vol.Schema(
                            {
                                vol.Required("alias"): str,
                                vol.Required("invite_link"): str,
                            }
                        ),
                        errors=errors,
                        description_placeholders=description_placeholders,
                    )

                current_targets = self.config_entry.options.get(CONF_TARGETS, {})
                _LOGGER.debug("Current targets: %s", current_targets)

                # Check if alias already exists
                if alias in current_targets:
                    errors["alias"] = ERROR_ALIAS_EXISTS
                else:
                    try:
                        _LOGGER.debug("About to accept invite link...")
                        # Prefer existing client if integration is loaded; otherwise, create a temporary one
                        client: SimplexWsClient | None = None
                        temporary_client: SimplexWsClient | None = None
                        domain_data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
                        if domain_data and (c := domain_data.get("client")):
                            client = c
                        else:
                            ws_url = self.config_entry.data.get(CONF_WS_URL, DEFAULT_WS_URL)
                            _LOGGER.debug("No active client found; creating temporary WS client for %s", ws_url)
                            temporary_client = SimplexWsClient(ws_url, self.hass.loop)
                            try:
                                await temporary_client.connect()
                            except (ClientError, asyncio.TimeoutError) as conn_err:
                                _LOGGER.warning("Failed to connect WS for invite accept: %s", conn_err)
                                errors["base"] = "ws_not_connected"
                                return self.async_show_form(
                                    step_id="init",
                                    data_schema=vol.Schema(
                                        {
                                            vol.Required("alias"): str,
                                            vol.Required("invite_link"): str,
                                        }
                                    ),
                                    errors=errors,
                                    description_placeholders=description_placeholders,
                                )
                            client = temporary_client

                        assert client is not None
                        # Allow more time for the invite acceptance handshake
                        resp = await client.accept_invite_link(link, timeout=60.0)
                        _LOGGER.debug("Got response from accept_invite_link: %s", resp)

                        chat_ref = resp.get("chat_ref")
                        if not chat_ref:
                            _LOGGER.error("Invite flow completed but no chat_ref in response: %s", resp)
                            errors["base"] = ERROR_INVITE_FAILED
                            return self.async_show_form(
                                step_id="init",
                                data_schema=vol.Schema(
                                    {
                                        vol.Required("alias"): str,
                                        vol.Required("invite_link"): str,
                                    }
                                ),
                                errors=errors,
                                description_placeholders=description_placeholders,
                            )

                        _LOGGER.debug("Chat ref obtained: %s", chat_ref)
                        # Add the new alias
                        new_targets = dict(current_targets)
                        new_targets[alias] = {"chat_ref": chat_ref, "name": alias}

                        new_options = {CONF_TARGETS: new_targets}
                        _LOGGER.debug("Creating options entry: %s", new_options)
                        return self.async_create_entry(title="", data=new_options)

                    except SimplexWsTimeoutError:
                        _LOGGER.warning("Timeout trying to accept invite link")
                        # Map to a more specific, translated error when possible
                        errors["base"] = "invite_timeout"
                    except SimplexWsError as e:
                        _LOGGER.error("SimplexWsError while accepting invite: %s", e)
                        # Try to map backend reason to a more specific error
                        reason = (getattr(e, "reason", None) or getattr(e, "message", "") or "").lower()
                        mapped = None
                        if any(k in reason for k in ("invalid", "bad link", "bad_link")):
                            mapped = "invite_invalid"
                        elif any(k in reason for k in ("expired", "revoked")):
                            mapped = "invite_expired"
                        elif "timeout" in reason:
                            mapped = "invite_timeout"
                        errors["base"] = mapped or ERROR_INVITE_FAILED
                    except (ClientError, asyncio.TimeoutError) as conn_err:
                        _LOGGER.warning("WebSocket connection issue during invite accept: %s", conn_err)
                        errors["base"] = "ws_not_connected"
                    except Exception as e:
                        _LOGGER.exception("Unexpected error while accepting invite: %s", e)
                        errors["base"] = "unknown"
                    finally:
                        # Clean up temporary client if created
                        if 'temporary_client' in locals() and temporary_client is not None:
                            try:
                                await temporary_client.close()
                            except Exception:
                                pass

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("alias"): str,
                    vol.Required("invite_link"): str,
                }
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )
