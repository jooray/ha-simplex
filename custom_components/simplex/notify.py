"""Simplex notification platform."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN, CONF_TARGETS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Simplex notify platform."""
    _LOGGER.debug("Setting up notify entities for config entry %s", config_entry.entry_id)

    try:
        entities = []

        # Get aliases from config
        aliases = config_entry.options.get(CONF_TARGETS, {})
        _LOGGER.debug("Found %d aliases in config: %s", len(aliases), list(aliases.keys()))

        # Create a notify entity for each alias
        for alias_id, alias_data in aliases.items():
            try:
                alias_name = alias_data.get("name", alias_id)
                entity = SimplexNotifyEntity(hass, config_entry, alias_id, alias_name)
                entities.append(entity)
                _LOGGER.debug("Created notify entity: %s (unique_id: %s)", entity.name, entity.unique_id)
            except Exception as exc:
                _LOGGER.exception("Failed to create notify entity for alias %s: %s", alias_id, exc)
                continue

        # If no aliases configured, create a default entity that sends to all
        if not aliases:
            try:
                entity = SimplexNotifyEntity(hass, config_entry, None, "All Aliases")
                entities.append(entity)
                _LOGGER.debug("Created default notify entity: %s (unique_id: %s)", entity.name, entity.unique_id)
            except Exception as exc:
                _LOGGER.exception("Failed to create default notify entity: %s", exc)

        if entities:
            _LOGGER.info("Adding %d notify entities to Home Assistant", len(entities))
            async_add_entities(entities)
        else:
            _LOGGER.warning("No notify entities were successfully created")

    except Exception as exc:
        _LOGGER.exception("Failed to setup notify platform: %s", exc)
        raise


class SimplexNotifyEntity(NotifyEntity):
    """Implementation of a notification entity for Simplex."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, alias_id: str | None, alias_name: str):
        """Initialize the Simplex notify entity."""
        self.hass = hass
        self.config_entry = config_entry
        self.alias_id = alias_id
        self.alias_name = alias_name

        # Create unique identifiers
        if alias_id:
            # Generate a clean entity ID suffix from the alias name
            clean_alias = alias_name.lower().replace(' ', '_').replace('-', '_')
            # Remove any non-alphanumeric characters except underscores
            import re
            clean_alias = re.sub(r'[^a-z0-9_]', '', clean_alias)
            # Ensure it doesn't start or end with underscore
            clean_alias = clean_alias.strip('_')
            if not clean_alias:  # Fallback if cleaning removed everything
                clean_alias = f"alias_{alias_id}"

            self._attr_name = f"Simplex {alias_name}"
            self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{alias_id}"
            # Don't set entity_id directly, let HA generate it
        else:
            self._attr_name = "Simplex"
            self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}"

        self._attr_supported_features = NotifyEntityFeature.TITLE

        # Device info for grouping entities
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name="Simplex Chat",
            manufacturer="Simplex Chat",
            model="Home Assistant Integration",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send a message via Simplex."""
        domain_data = self.hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(self.config_entry.entry_id)

        if not entry_data:
            _LOGGER.error("Simplex integration data not found")
            return

        websocket_client = entry_data.get("client")
        if not websocket_client:
            _LOGGER.error("Simplex WebSocket client not available")
            return

        # Build message with optional title
        full_message = message
        if title:
            full_message = f"{title}: {message}"

        # Send to specific alias or all aliases
        if self.alias_id:
            # Send to specific alias - get the chat_ref from the targets configuration
            aliases = self.config_entry.options.get(CONF_TARGETS, {})
            alias_data = aliases.get(self.alias_id, {})
            chat_ref = alias_data.get("chat_ref")

            if not chat_ref:
                _LOGGER.error("No chat_ref found for alias %s", self.alias_name)
                return

            try:
                await websocket_client.send_text(chat_ref, full_message)
                _LOGGER.debug("Sent message to alias %s (%s) at %s", self.alias_name, self.alias_id, chat_ref)
            except Exception as err:
                _LOGGER.error("Failed to send message to alias %s: %s", self.alias_name, err)
        else:
            # Send to all configured aliases
            aliases = self.config_entry.options.get(CONF_TARGETS, {})
            for alias_id, alias_data in aliases.items():
                chat_ref = alias_data.get("chat_ref")
                if not chat_ref:
                    _LOGGER.warning("No chat_ref found for alias %s, skipping", alias_id)
                    continue

                try:
                    await websocket_client.send_text(chat_ref, full_message)
                    _LOGGER.debug("Sent message to alias %s at %s", alias_id, chat_ref)
                except Exception as err:
                    _LOGGER.error("Failed to send message to alias %s: %s", alias_id, err)
