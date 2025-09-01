import asyncio
import json
import logging
import secrets
from typing import Any, Dict, Optional

from aiohttp import ClientSession, ClientWebSocketResponse, WSMsgType

_LOGGER = logging.getLogger(__name__)


class SimplexWsError(Exception):
    def __init__(self, message: str, reason: Optional[str] = None, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.payload = payload or {}


class SimplexWsTimeoutError(SimplexWsError):
    pass


class SimplexWsClient:
    """Async client for SimpleX CLI WebSocket API.

    Protocol (per bots/README.md):
      - Send: { "corrId": "...", "cmd": "<command string>" }
      - Response: { "corrId": "...", "resp": { "type": "...", ... } }
      - Events: { "resp": { "type": "...", ... } }
    """

    def __init__(self, ws_url: str, loop: asyncio.AbstractEventLoop) -> None:
        self._url = ws_url
        self._loop = loop
        self._session: Optional[ClientSession] = None
        self._ws: Optional[ClientWebSocketResponse] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._events: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        if self._session is None:
            self._session = ClientSession()
        self._ws = await self._session.ws_connect(self._url, heartbeat=20)
        self._reader_task = self._loop.create_task(self._reader())

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()

    async def _reader(self) -> None:
        assert self._ws is not None
        async for msg in self._ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    _LOGGER.warning("WS reader received non-JSON payload: %s", msg.data)
                    continue
                corr = data.get("corrId")
                if corr and corr in self._pending:
                    fut = self._pending.pop(corr)
                    if not fut.done():
                        fut.set_result(data)
                else:
                    # Uncorrelated message: push to events (errors or async events)
                    await self._events.put(data)
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                _LOGGER.debug("WS reader terminating: %s", msg.type)
                break

    async def _rpc_cmd(self, cmd: str, timeout: float = 30.0) -> Dict[str, Any]:
        assert self._ws is not None
        corr = secrets.token_hex(8)
        payload = {"corrId": corr, "cmd": cmd}
        fut = self._loop.create_future()
        self._pending[corr] = fut
        # Redact sensitive values for logging
        safe_cmd = cmd
        if "connect" in cmd and "http" in cmd:
            # Hide the middle of the link
            parts = cmd.split(" ", 1)
            if len(parts) == 2 and len(parts[1]) > 20:
                link = parts[1]
                safe_cmd = parts[0] + " " + f"{link[:8]}…<redacted>…{link[-6:]}"
        _LOGGER.debug("WS -> %s", {"corrId": corr, "cmd": safe_cmd})
        await self._ws.send_str(json.dumps(payload))

        try:
            # Wait either for direct response or for immediate error event without corrId
            resp_task = asyncio.create_task(asyncio.wait_for(fut, timeout))
            # Only briefly watch for an early error event to avoid starving the main wait
            event_task = asyncio.create_task(asyncio.wait_for(self._events.get(), timeout=timeout))
            done, pending = await asyncio.wait({resp_task, event_task}, return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
            result: Dict[str, Any]
            if resp_task in done:
                result = resp_task.result()
            else:
                # Got an event first
                evt = event_task.result()
                if evt is not None and not isinstance(evt, dict):
                    evt = {}
                # If it's an error event, raise it; otherwise re-queue it for other consumers
                resp = evt.get("resp") if isinstance(evt, dict) else None
                if isinstance(resp, dict) and resp.get("type") == "chatCmdError":
                    raise self._convert_chat_cmd_error(resp)
                # Not an error – put it back and continue waiting for corrId response
                if isinstance(evt, dict) and evt:
                    await self._events.put(evt)
                result = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            raise SimplexWsTimeoutError("WebSocket RPC timed out", payload={"cmd": cmd}) from exc

        _LOGGER.debug("WS <- %s", result)
        resp = result.get("resp") if isinstance(result, dict) else None
        if not isinstance(resp, dict):
            return result
        if resp.get("type") == "chatCmdError":
            raise self._convert_chat_cmd_error(resp)
        return resp

    def _convert_chat_cmd_error(self, resp: Dict[str, Any]) -> SimplexWsError:
        chat_error = resp.get("chatError") or {}
        # Flatten common error message sources
        message = None
        reason = None
        if isinstance(chat_error, dict):
            et = chat_error.get("errorType") or chat_error.get("type")
            if isinstance(et, dict):
                reason = et.get("type") or et.get("message")
                message = et.get("message") or et.get("type")
            elif isinstance(et, str):
                reason = et
                message = chat_error.get("message") or et
        message = message or "SimpleX command error"
        return SimplexWsError(message, reason=reason, payload=resp)

    # ---- Helpers and API surface ----

    async def get_active_user(self) -> Optional[Dict[str, Any]]:
        try:
            resp = await self._rpc_cmd("/user", timeout=10.0)
            if resp.get("type") == "activeUser":
                return resp.get("user")
        except SimplexWsError:
            pass
        return None

    async def accept_invite_link(self, link: str, timeout: float = 60.0) -> Dict[str, Any]:
        """Connect via a SimpleX link string and return a ChatRef when ready.

        Returns: { "chat_ref": "@<contactId>"|"#<groupId>", "initial": {...}, "event": {...}? }
        Raises SimplexWsError on immediate command errors, and SimplexWsTimeoutError on timeout.
        """
        # Kick off connection via active user profile
        initial = await self._rpc_cmd(f"/connect {link}", timeout=timeout)

        # If contact already exists, response should include contact details
        chat_ref: Optional[str] = None
        if initial.get("type") == "contactAlreadyExists":
            contact = initial.get("contact") or {}
            cid = contact.get("contactId")
            if cid is not None:
                chat_ref = f"@{cid}"
                return {"chat_ref": chat_ref, "initial": initial}

        # Otherwise, wait for relevant event indicating chat is usable
        event: Optional[Dict[str, Any]] = None
        deadline = self._loop.time() + timeout
        wanted = {
            "contactConnected",  # address auto-accept
            "contactSndReady",   # invitation confirmed, can send
            "userJoinedGroup",   # joined group via link
        }
        while self._loop.time() < deadline:
            remaining = max(0.1, deadline - self._loop.time())
            try:
                msg = await asyncio.wait_for(self._events.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            resp = msg.get("resp") if isinstance(msg, dict) else None
            if not isinstance(resp, dict):
                continue
            etype = resp.get("type")
            if etype == "chatCmdError":
                raise self._convert_chat_cmd_error(resp)
            if etype not in wanted:
                continue
            event = resp
            if etype in ("contactConnected", "contactSndReady"):
                contact = resp.get("contact") or {}
                cid = contact.get("contactId")
                if cid is not None:
                    chat_ref = f"@{cid}"
                    break
            if etype == "userJoinedGroup":
                group = resp.get("groupInfo") or {}
                gid = group.get("groupId")
                if gid is not None:
                    chat_ref = f"#{gid}"
                    break

        if not chat_ref:
            raise SimplexWsTimeoutError("Invite was not ready in time", payload={"initial": initial, "event": event})
        return {"chat_ref": chat_ref, "initial": initial, "event": event}

    async def send_text(self, chat_ref: str, text: str) -> Dict[str, Any]:
        """Send a text message to a chat identified by ChatRef string (@<id> or #<id>)."""
        # Build ComposedMessage list with a single text message
        composed = [
            {
                "msgContent": {"type": "text", "text": text},
                "mentions": {},
            }
        ]
        cmd = f"/_send {chat_ref} json " + json.dumps(composed, separators=(",", ":"))
        return await self._rpc_cmd(cmd, timeout=30.0)
