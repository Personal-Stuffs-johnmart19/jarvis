# Optional Windows BLE protocol

The optional Windows transport in `remote/bluetooth_transport.py` advertises a
BLE GATT peripheral service and exchanges framed JSON with an authenticated
client. Enable it explicitly with `--remote` or `JARVIS_BLUETOOTH=1`; the Windows
adapter must support BLE peripheral mode. No Android client is distributed or
maintained in this repository. This is the remaining transport contract, not
evidence of tested compatibility with a particular phone or client.

## Link layer

- **Transport:** Bluetooth LE (GATT).
- **Service UUID:** `8f5f4a5a-2f1a-4a9e-9b2a-3a6c9e1d2b4c` — scan name _Jarvis
  Remote_.
- **Write characteristic** (client → PC):
  `9f5f4a5a-2f1a-4a9e-9b2a-3a6c9e1d2b4c`
  - Requires `ENCRYPTION_AND_AUTHENTICATION_REQUIRED`; client pairing depends on
    the operating system. The application token check below is also required.
- **Notify characteristic** (PC → client):
  `a5f4a5a0-2f1a-4a9e-9b2a-3a6c9e1d2b4c`

## Framing

Every message is a UTF-8 JSON object, prefixed by a 2-byte big-endian length:

```
[ len_hi ][ len_lo ][ ...len bytes of JSON... ]
```

## Message envelope

```json
{ "version": 1, "type": "auth", "id": "3", "payload": { ... } }
```

- `id` — client-generated request id, echoed back on the matching response.
- `payload` — message-specific object.

## Client → PC

| type         | payload                       | reply                            |
| ------------ | ----------------------------- | -------------------------------- |
| `auth`       | `{"token": "<bridge token>"}` | `auth_ok` / `auth_fail`          |
| `ping`       | —                             | `pong`                           |
| `status`     | —                             | `response` with `state`, `muted` |
| `mute`       | `{"muted": true}` or `{"muted": false}` | `response`             |
| `stop`       | —                             | `response`                       |
| `disconnect` | —                             | `event` (system), then closes    |
| `command`    | `{"text": "play some music"}` | `response` (text reply)          |

The current desktop starts the bridge through `local_mode/remote.py`.
`command` text enters its `submit_remote` callback and the shared Python task
queue with a Bluetooth source identifier. The returned user message becomes a
`response` with the request ID. The transport does not select a separate model
or grant a command additional permissions.

> Note: v1 is text-only over Bluetooth (BLE data rate makes live PCM voice
> impractical). Real-time voice stays on local mic/speakers.

## PC → Client

| type                    | payload                                                                    | notes                       |
| ----------------------- | -------------------------------------------------------------------------- | --------------------------- |
| `auth_ok` / `auth_fail` | `{"error": "..."}` (fail only)                                             | auth result                 |
| `pong`                  | —                                                                          | ping reply                  |
| `response`              | `{"text": "...", "state": "...", "muted": ...}`                            | reply to a command / status |
| `event`                 | `{"kind": "system"\|"phase"\|"tool", "text"/"state"/"name"/"status": ...}` | unsolicited events          |

The envelope supports `phase`, `tool` and `system` events when the host emits
them. A response or event does not by itself prove a tool completed or that
audio stopped; interpret the host's actual result.

## Auth

- The transport first reads `config/bridge_token.txt`, then
  `JARVIS_BRIDGE_TOKEN`. If neither supplies a usable token, startup attempts to
  create a new token file. Keep it private.
- No message is processed before a valid `auth`. A failed auth closes the
  client.
- Comparison uses a constant-time check.

## Reconnect

- A reconnecting client must authenticate again while the service is running.
- Tracked clients are keyed by the notify characteristic instance and dropped
  when they unsubscribe.
