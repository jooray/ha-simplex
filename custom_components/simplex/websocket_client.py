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
        _LOGGER.debug("_rpc_cmd: start cmd=%s corr=%s timeout=%.1fs", safe_cmd, corr, timeout)
        _LOGGER.debug("WS -> %s", {"corrId": corr, "cmd": safe_cmd})
        await self._ws.send_str(json.dumps(payload))

        try:
            # Wait either for direct response or for immediate error event without corrId
            _LOGGER.debug("_rpc_cmd: waiting for response or early error event, timeout=%.1fs", timeout)

            # Simple approach: just wait for the correlated response, handle async events separately
            while True:
                try:
                    result = await asyncio.wait_for(fut, timeout)
                    _LOGGER.debug("_rpc_cmd: got response for corr=%s", corr)
                    break
                except asyncio.TimeoutError:
                    # Check if we got any async events that might be errors
                    try:
                        evt = self._events.get_nowait()
                        resp = evt.get("resp") if isinstance(evt, dict) else None
                        if isinstance(resp, dict) and resp.get("type") == "chatCmdError":
                            _LOGGER.debug("_rpc_cmd: found async chatCmdError during timeout, raising")
                            raise self._convert_chat_cmd_error(resp)
                        # Not an error event, put it back
                        await self._events.put(evt)
                        _LOGGER.debug("_rpc_cmd: re-queued unrelated async event during wait")
                    except asyncio.QueueEmpty:
                        pass
                    # Re-raise the timeout
                    raise
        except asyncio.TimeoutError as exc:
            _LOGGER.debug("_rpc_cmd: timeout after %.1fs for corr=%s", timeout, corr)
            raise SimplexWsTimeoutError("WebSocket RPC timed out", payload={"cmd": cmd}) from exc
        except asyncio.CancelledError:
            _LOGGER.debug("_rpc_cmd: cancelled for corr=%s", corr)
            # Clean up pending future
            self._pending.pop(corr, None)
            raise
        except ConnectionError as exc:
            _LOGGER.debug("_rpc_cmd: connection error for corr=%s: %s", corr, exc)
            self._pending.pop(corr, None)
            raise SimplexWsError(f"WebSocket connection error: {exc}", reason="connection_error", payload={"cmd": cmd}) from exc
        except json.JSONDecodeError as exc:
            _LOGGER.debug("_rpc_cmd: JSON decode error for corr=%s: %s", corr, exc)
            self._pending.pop(corr, None)
            raise SimplexWsError(f"Invalid JSON response: {exc}", reason="json_error", payload={"cmd": cmd}) from exc
        except Exception as exc:
            _LOGGER.exception("_rpc_cmd: unexpected error for corr=%s", corr)
            self._pending.pop(corr, None)
            raise SimplexWsError(f"Unexpected RPC error: {exc}", reason="unknown_error", payload={"cmd": cmd}) from exc

        _LOGGER.debug("WS <- %s", result)
        resp = result.get("resp") if isinstance(result, dict) else None
        if not isinstance(resp, dict):
            _LOGGER.debug("_rpc_cmd: returning raw result for corr=%s", corr)
            return result
        if resp.get("type") == "chatCmdError":
            _LOGGER.debug("_rpc_cmd: response is chatCmdError for corr=%s, raising", corr)
            raise self._convert_chat_cmd_error(resp)
        _LOGGER.debug("_rpc_cmd: success for corr=%s, response_type=%s", corr, resp.get("type"))
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
        # Redact sensitive parts of the link for logs
        def _safe_link_for_log(l: str) -> str:
            try:
                if len(l) > 20:
                    return f"{l[:8]}…<redacted>…{l[-6:]}"
                return "…<redacted>"
            except Exception:
                return "…<redacted>"

        _LOGGER.debug("accept_invite_link: start link=%s timeout=%.1fs", _safe_link_for_log(link), timeout)
        # Kick off connection via active user profile
        initial = await self._rpc_cmd(f"/connect {link}", timeout=timeout)
        _LOGGER.debug("accept_invite_link: initial response type=%s payload=%s", initial.get("type"), initial)

        # If contact already exists, response should include contact details
        chat_ref: Optional[str] = None
        if initial.get("type") == "contactAlreadyExists":
            contact = initial.get("contact") or {}
            cid = contact.get("contactId")
            if cid is not None:
                chat_ref = f"@{cid}"
                _LOGGER.debug("accept_invite_link: contact already exists, chat_ref=%s", chat_ref)
                return {"chat_ref": chat_ref, "initial": initial}

        # Otherwise, wait for relevant event indicating chat is usable
        event: Optional[Dict[str, Any]] = None
        deadline = self._loop.time() + timeout
        wanted = {
            "contactConnected",  # address auto-accept
            "contactSndReady",   # invitation confirmed, can send
            "userJoinedGroup",   # joined group via link
        }
        events_seen = 0
        last_type: Optional[str] = None
        while self._loop.time() < deadline:
            remaining = max(0.1, deadline - self._loop.time())
            try:
                msg = await asyncio.wait_for(self._events.get(), timeout=remaining)
            except asyncio.TimeoutError:
                _LOGGER.debug("accept_invite_link: wait_for event timed out after %.2fs, events_seen=%d", remaining, events_seen)
                break
            resp = msg.get("resp") if isinstance(msg, dict) else None
            if not isinstance(resp, dict):
                _LOGGER.debug("accept_invite_link: ignoring non-dict event: %s", msg)
                continue
            etype = resp.get("type")
            last_type = etype
            events_seen += 1
            _LOGGER.debug("accept_invite_link: event #%d type=%s payload=%s", events_seen, etype, resp)
            if etype == "chatCmdError":
                _LOGGER.debug("accept_invite_link: received chatCmdError, raising")
                raise self._convert_chat_cmd_error(resp)
            if etype not in wanted:
                _LOGGER.debug("accept_invite_link: event not in wanted set, continue")
                continue
            event = resp
            if etype in ("contactConnected", "contactSndReady"):
                contact = resp.get("contact") or {}
                cid = contact.get("contactId")
                if cid is not None:
                    chat_ref = f"@{cid}"
                    _LOGGER.debug("accept_invite_link: ready via %s, chat_ref=%s", etype, chat_ref)
                    break
            if etype == "userJoinedGroup":
                group = resp.get("groupInfo") or {}
                gid = group.get("groupId")
                if gid is not None:
                    chat_ref = f"#{gid}"
                    _LOGGER.debug("accept_invite_link: joined group, chat_ref=%s", chat_ref)
                    break

        if not chat_ref:
            _LOGGER.debug(
                "accept_invite_link: no chat_ref before timeout; initial_type=%s last_event_type=%s events_seen=%d initial=%s last_event=%s",
                initial.get("type"), last_type, events_seen, initial, event,
            )
            raise SimplexWsTimeoutError("Invite was not ready in time", payload={"initial": initial, "event": event})
        _LOGGER.debug("accept_invite_link: success chat_ref=%s", chat_ref)
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
