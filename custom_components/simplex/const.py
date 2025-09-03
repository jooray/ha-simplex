from homeassistant.const import Platform

DOMAIN = "simplex"

CONF_WS_URL = "ws_url"
CONF_TARGETS = "targets"  # dict: alias -> {"chat_ref": "@<id>|#<id>", "name": "friendly_name"}
DEFAULT_WS_URL = "ws://127.0.0.1:5225"

PLATFORMS: list[Platform] = [Platform.NOTIFY]

ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_INVITE_FAILED = "invite_failed"
ERROR_ALIAS_EXISTS = "alias_exists"
