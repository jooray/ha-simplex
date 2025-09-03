# SimpleX integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

Custom [Home Assistant](https://www.home-assistant.io/) integration for sending messages through [SimpleX Chat](https://simplex.chat/).

This integration lets you:

- Connect Home Assistant to a running `simplex-chat` CLI instance in **WebSocket mode** (`simplex-chat -p 5225`).
- Accept **invite links** (1:1 or group) and create individual notify entities for each contact/group.
- Send messages via modern Home Assistant notify entities:
  ```yaml
  action: notify.send_message
  data:
    message: "Dinner is ready!"
    title: "Home Alert"
  target:
    entity_id: notify.simplex_family
  ```

* Use [Assist](https://www.home-assistant.io/voice_control/assist/) with the LLM tool `send_simplex_message`:

  > "Send my **wife** 'I'll be home in 10 minutes'"

## Features

* ✅ **Individual notify entities** for each contact/group (e.g., `notify.simplex_alice`, `notify.simplex_family`)
* ✅ **Simple invite flow** - paste link to create new entity
* ✅ **Entity-based targeting** - send to specific contacts or multiple at once
* ✅ **Standard HA entity management** - delete contacts via Entities page
* ✅ **Assist LLM tool** (`send_simplex_message`) for voice commands
* ✅ **Works with both personal and group chats**
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

### 4. Add contacts/groups

* Open the SimpleX integration card → **Configure**
* Choose **Add New Chat/Contact**
* Enter a friendly name (e.g., `Alice`, `Family Group`)
* Paste the invite link from your SimpleX app
* A new notify entity will be created (e.g., `notify.simplex_alice`)

---

## Testing Notifications

The integration creates individual notify entities for each configured alias. This allows you to send messages to specific contacts or groups.

### Entity Names

- If you have aliases configured, each will get its own entity: `notify.simplex_alice`, `notify.simplex_bob`, etc.
- If no aliases are configured, a single `notify.simplex` entity is created that broadcasts to all future aliases

### Testing in Developer Tools

1. **Go to Developer Tools → Actions**
2. **Select the `notify.send_message` action**
3. **Target a specific alias entity:**

```yaml
action: notify.send_message
data:
  message: "Hello from Home Assistant!"
  title: "Test Message"
target:
  entity_id: notify.simplex_macbook
```

Or send to multiple aliases at once:

```yaml
action: notify.send_message  
data:
  message: "Broadcast message"
  title: "Alert"
target:
  entity_id:
    - notify.simplex_alice
    - notify.simplex_bob
```

### Using in Automations

```yaml
automation:
  - alias: "Security Alert"
    trigger:
      - platform: state
        entity_id: binary_sensor.door_sensor
        to: "on"
    action:
      - action: notify.send_message
        data:
          message: "Front door opened!"
          title: "Security Alert"
        target:
          entity_id: notify.simplex_security_group
```

### Troubleshooting

If notifications aren't working:

1. **Check entity availability:** Go to **Developer Tools → States** and look for your `notify.simplex_*` entities
2. **Verify aliases:** Check the configuration options to ensure aliases are properly set up
3. **Check logs:** Look for errors in **Settings → System → Logs** or enable debug logging:

```yaml
logger:
  logs:
    custom_components.simplex: debug
```

4. **WebSocket connection:** Ensure your SimpleX chat is running and the WebSocket connection is active
