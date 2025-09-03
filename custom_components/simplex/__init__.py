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
    try:
        ws_url: str = entry.data.get(CONF_WS_URL, DEFAULT_WS_URL)
        targets = entry.options.get(CONF_TARGETS, {})
        _LOGGER.info("Setting up SimpleX entry %s with ws_url=%s, %d targets: %s",
                     entry.entry_id, ws_url, len(targets), list(targets.keys()))

        client = SimplexWsClient(ws_url, hass.loop)
        await client.connect()

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            "client": client,
            "targets": dict(targets),
        }

        _LOGGER.debug("About to forward entry setups to platforms: %s", PLATFORMS)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("Finished forwarding entry setups to platforms")

        # Register LLM API - only use entry.async_on_unload to avoid double-registration
        unregister_llm = register_llm_api(hass, entry)
        entry.async_on_unload(unregister_llm)
        _LOGGER.info("SimpleX entry %s setup completed successfully", entry.entry_id)
        return True
    except Exception as exc:
        _LOGGER.exception("Failed to setup SimpleX entry %s: %s", entry.entry_id, exc)
        # Clean up on failure
        if entry.entry_id in hass.data.get(DOMAIN, {}):
            data = hass.data[DOMAIN].pop(entry.entry_id, None)
            if data and "client" in data:
                try:
                    await data["client"].close()
                except Exception:
                    pass
        return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.info("Unloading SimpleX entry %s", entry.entry_id)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    _LOGGER.debug("Platform unload result: %s", unloaded)

    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data and "client" in data:
        client: SimplexWsClient = data["client"]
        await client.close()
        _LOGGER.debug("Closed websocket client")

    _LOGGER.info("SimpleX entry %s unloaded successfully", entry.entry_id)
    return unloaded

