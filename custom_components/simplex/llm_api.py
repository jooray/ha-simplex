from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .const import DOMAIN
from .websocket_client import SimplexWsError


class SimplexSendMessageTool(llm.Tool):
    """LLM Tool to send a SimpleX message to a configured alias."""

    def __init__(self, entry_id: str, aliases: list[str]) -> None:
        self._entry_id = entry_id
        self.name = "send_simplex_message"
        self.description = (
            "Send a SimpleX message to a configured recipient alias (group or 1:1)."
        )
        # Use enum if aliases exist, otherwise allow any string to avoid empty-enum issues
        recipient_field: Any = vol.In(aliases) if aliases else str
        self.parameters = vol.Schema(
            {
                vol.Required(
                    "recipient", description="Alias configured in the SimpleX integration"
                ): recipient_field,
                vol.Required("text", description="Message text"): str,
            }
        )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> dict[str, Any]:
        data = self.parameters(tool_input.tool_args)
        recipient = data["recipient"].strip()
        text = data["text"].strip()
        if not recipient or not text:
            return {"success": False, "error": "recipient and text are required"}

        domain_data = hass.data[DOMAIN][self._entry_id]
        targets = domain_data["targets"]
        t = targets.get(recipient)
        if not t:
            return {"success": False, "error": f"unknown recipient alias '{recipient}'"}

        chat_ref = t.get("chat_ref") or t.get("chat_id")
        if not chat_ref:
            return {"success": False, "error": f"recipient '{recipient}' is misconfigured (no chat reference)"}

        client = domain_data["client"]
        try:
            await client.send_text(str(chat_ref), text)
            return {"success": True, "result": f"Sent to {recipient}"}
        except SimplexWsError as exc:
            reason = exc.reason or exc.message or "send failed"
            return {"success": False, "error": f"failed to send: {reason}"}


class SimplexAPI(llm.API):
    """SimpleX API exposing one tool to send messages."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass=hass, id=f"{DOMAIN}_{entry.entry_id}", name="SimpleX")
        self._entry_id = entry.entry_id

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        # Build tool with current aliases
        aliases = sorted(self.hass.data[DOMAIN][self._entry_id]["targets"].keys())
        tool = SimplexSendMessageTool(self._entry_id, aliases)
        prompt = (
            "Use send_simplex_message to send a message to a configured alias. "
            "Provide recipient and text."
        )
        return llm.APIInstance(
            api=self,
            api_prompt=prompt,
            llm_context=llm_context,
            tools=[tool],
        )


def register_llm_api(hass: HomeAssistant, entry):
    """Register the SimpleX LLM API and return an unregister callback."""
    api = SimplexAPI(hass, entry)
    return llm.async_register_api(hass, api)
