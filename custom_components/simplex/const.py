from homeassistant.const import Platform

DOMAIN = "simplex"

CONF_WS_URL = "ws_url"
CONF_TARGETS = "targets"  # dict: alias -> {"chat_ref": "@<id>|#<id>", (legacy) "chat_id": "..."}
DEFAULT_WS_URL = "ws://127.0.0.1:5225"

PLATFORMS: list[Platform] = [Platform.NOTIFY]

ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_INVITE_FAILED = "invite_failed"
ERROR_INVITE_INVALID = "invite_invalid"
ERROR_INVITE_EXPIRED = "invite_expired"
ERROR_INVITE_TIMEOUT = "invite_timeout"
ERROR_WS_NOT_CONNECTED = "ws_not_connected"
ERROR_ALIAS_EXISTS = "alias_exists"
