# SimpleX integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

Custom [Home Assistant](https://www.home-assistant.io/) integration for sending messages through [SimpleX Chat](https://simplex.chat/).

This integration lets you:

- Connect Home Assistant to a running `simplex-chat` CLI instance in **WebSocket mode** (`simplex-chat -p 5225`).
- Accept **invite links** (1:1 or group) and bind them to friendly aliases (e.g., `wife`, `family`, `reader`).
- Send messages via Home Assistant’s `notify` service:
  ```yaml
  service: notify.simplex
  data:
    message: "Dinner is ready"
    data:
      target: "family"  # alias you configured in options
  ```

* Use [Assist](https://www.home-assistant.io/voice_control/assist/) with the LLM tool `send_simplex_message`:

  > "Send my **wife** 'I'll be home in 10 minutes'"

## Features

* ✅ Paste invite link (1:1 or group) to create a target alias
* ✅ Persistent storage of aliases → chat IDs in HA config
* ✅ `notify.simplex` service for automations and scripts
* ✅ Assist LLM tool (`send_simplex_message`)
* ✅ Works with both personal and group chats
* ⚠️ Currently **does not** generate invites from HA (only accepts links)

---

## Installation

### 1. Run SimpleX CLI in WebSocket mode

You need a `simplex-chat` instance running with WS enabled.
See [this docker example](https://github.com/jooray/simplex-ws-docker/)

### 2. Install the integration

Copy this repo into your Home Assistant `custom_components/` directory:

```
custom_components/simplex/
```

Restart Home Assistant.

### 3. Add the integration in HA UI

* Go to **Settings → Devices & Services → Add Integration**
* Search for **SimpleX**
* Enter the WebSocket URL (default: `ws://127.0.0.1:5225`)

### 4. Add aliases

* Open the SimpleX integration card → **Configure**
* For each target:

  * **Alias**: e.g. `wife`, `family`
  * **Invite link**: paste from your SimpleX app (can be group or 1:1 link)

---

## Usage

### Notify service

```yaml
service: notify.simplex
data:
  message: "Coffee is ready ☕"
  data:
    # recipient alias; you can also use recipient:/alias:/aliases:/recipients:
    target: "reader"

# multiple recipients
service: notify.simplex
data:
  message: "Hello group"
  data:
    aliases: ["family", "friends"]
```

### Voice (Assist LLM)

Say:

> “Send my **family** ‘I’m leaving now.’”

Assist will call the tool `send_simplex_message` with `{ recipient: "family", text: "I’m leaving now" }`.

If Assist says the tool is not available, make sure:

- You're on HA 2024.8+ (LLM API tools support).
- The integration is loaded without errors (check Settings → System → Logs).
- You have at least one alias configured in the integration options.
- After adding or renaming aliases, reload the integration so the tool list refreshes.

---

## Development status

⚠️ **Experimental** — this is an early version.
Expect breaking changes while the SimpleX CLI bot API stabilizes.


---

## Credits

* [SimpleX Chat](https://github.com/simplex-chat/simplex-chat) for the CLI & WS API
* Inspired by other HA notify integrations (`telegram`, `matrix`, `ntfy`…)

---

## License

MIT License
