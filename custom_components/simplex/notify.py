from __future__ import annotations

from typing import Any

from homeassistant.components.notify import NotifyEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the SimpleX notify platform via config entry."""
    async_add_entities([SimplexNotifyService(hass, entry)])


class SimplexNotifyService(NotifyEntity):
    """Notify service for SimpleX."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_name = entry.title or "SimpleX"
        self._attr_unique_id = f"{entry.entry_id}_notify"

    async def async_send_message(self, message: str = "", **kwargs: Any) -> None:
        data: dict[str, Any] = kwargs.get("data") or {}
        # NOTE:
        # - The notify entity service is notify.send_message and uses HA's standard
        #   entity targeting (entity_id) via the service call "target" object.
        #   That selector looks like {"entity_id": "notify.simplex"} and MUST NOT
        #   be confused with our recipient alias(es).
        # - For recipient aliases, accept any of these keys (string or list[str]):
        #   recipient | recipients | alias | aliases | target (only if str/list)
        def pick_aliases() -> list[str] | None:
            # Helper to normalize a candidate into list[str]
            def norm(v: Any) -> list[str] | None:
                if v is None:
                    return None
                # Ignore HA service target selector objects like {"entity_id": ...}
                if isinstance(v, dict):
                    return None
                if isinstance(v, (list, tuple)):
                    items = [str(x).strip() for x in v if str(x).strip()]
                    return items or None
                s = str(v).strip()
                return [s] if s else None

            # Look in kwargs first, then in data
            for key in ("recipient", "recipients", "alias", "aliases", "target"):
                val = kwargs.get(key)
                ali = norm(val)
                if ali:
                    return ali
            for key in ("recipient", "recipients", "alias", "aliases", "target"):
                val = data.get(key)
                ali = norm(val)
                if ali:
                    return ali
            return None

        targets_input = pick_aliases()
        if not targets_input:
            raise ValueError(
                "Missing recipient alias. Provide 'recipient'/'alias' (string or list) in the service data."
            )

        domain_data = self.hass.data[DOMAIN][self.entry.entry_id]
        client = domain_data["client"]
        targets = domain_data["targets"]

        for alias in targets_input:
            t = targets.get(alias)
            if not t:
                raise ValueError(f"Unknown target alias: {alias}")
            chat_ref = t.get("chat_ref") or t.get("chat_id")
            if not chat_ref:
                raise ValueError(f"Target alias missing chat reference: {alias}")
            await client.send_text(str(chat_ref), message)
            await client.send_text(str(chat_ref), message)
