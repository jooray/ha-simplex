from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, CONF_WS_URL, CONF_TARGETS, PLATFORMS, DEFAULT_WS_URL
from .websocket_client import SimplexWsClient
from .llm_api import register_llm_api

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ws_url: str = entry.data.get(CONF_WS_URL, DEFAULT_WS_URL)
    _LOGGER.debug("Setting up SimpleX entry %s with ws_url=%s", entry.entry_id, ws_url)
    client = SimplexWsClient(ws_url, hass.loop)
    await client.connect()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "targets": dict(entry.options.get(CONF_TARGETS, {})),
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    unregister_llm = register_llm_api(hass, entry)
    hass.data[DOMAIN][entry.entry_id]["unregister_llm"] = unregister_llm
    # Ensure LLM tool is unregistered on entry unload
    entry.async_on_unload(unregister_llm)
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug("Unloading SimpleX entry %s", entry.entry_id)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data:
        # Unregister LLM tool if registered
        unregister = data.get("unregister_llm")
        if unregister:
            try:
                unregister()
            except Exception:
                pass
        client: SimplexWsClient = data["client"]
        await client.close()
    return unloaded


async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _LOGGER.debug("Options updated for SimpleX entry %s", entry.entry_id)
    data = hass.data[DOMAIN][entry.entry_id]
    data["targets"] = dict(entry.options.get(CONF_TARGETS, {}))
    # Refresh LLM tool so the allowed aliases enum stays current.
    # Unregister old tool (if any) and register a new one.
    unregister = data.get("unregister_llm")
    if unregister:
        try:
            unregister()
        except Exception:  # best-effort
            pass
    unregister_llm = register_llm_api(hass, entry)
    data["unregister_llm"] = unregister_llm
