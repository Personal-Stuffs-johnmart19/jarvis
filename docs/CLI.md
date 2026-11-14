# CLI, environment and diagnostics

Use **START.cmd** for the desktop. The optional Python CLI has its own supported model/options boundary; it must not be mistaken for the complete desktop provider catalog. See `local_mode/cli.py --help` through the launch wrapper for the current options.

For optional Bluetooth input, run `START.cmd --remote` or set `JARVIS_BLUETOOTH=1` before launching. Telegram continues to use its configured environment/credential settings. These options do not require a separate remote launcher. Use the console route below for interactive input and diagnostic output.

## Commands

```powershell
./start_local.ps1 --text
./start_local.ps1 --voice
./start_local.ps1 --doctor
./start_local.ps1 --capabilities
./start_local.ps1 --voices
./start_local.ps1 --devices
./start_local.ps1 --microphone 1
./start_local.ps1 --set-key
./start_local.ps1 --transcribe 'D:/audio/command.wav'
```

`--voice` records after Enter; the desktop microphone mode listens automatically when enabled. `--transcribe` only recognizes a file. `--wav-command` also executes the recognized command and therefore is not a passive test. `--command` performs one action; `--window-title` selects an explicit window target. `--hold` keeps the one-command process/session alive until interrupted. `/exit` ends the interactive chat.

`--doctor`, device/voice listing and import checks do not prove an actual provider response. `--check-gemini` makes a real quota-consuming request. Do not run live media/desktop commands as incidental development tests.

`--model`, `--auto-fallback` / `--no-auto-fallback` and `--save-model` control the CLI Google adapter. The current allowed IDs come from `local_mode/models.py`, not a historical documentation list. Desktop shared model selection supports other configured providers separately.

## Environment

| Variable | Purpose |
| --- | --- |
| `JARVIS_MODEL`, `JARVIS_AUTO_FALLBACK` | Override the legacy CLI model/fallback preference |
| `JARVIS_ASR` | Select the supported recognition adapter (`whisper` or lightweight `vosk`) |
| `VOSK_MODEL_PATH` | Alternate compatible Vosk model path |
| `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `JARVIS_GEMINI_API_KEY` | Provider credentials; never paste values into commands, logs or Git |
| `JARVIS_BROWSER` | Legacy browser adapter option; Chrome is the supported desktop integration target |
| `JARVIS_TELEGRAM_TOKEN`, `JARVIS_TELEGRAM_CHAT_ID` | Optional explicitly configured Telegram channel |
| `JARVIS_BLUETOOTH=1` / CLI `--remote` | Optional Bluetooth transport |
| `JARVIS_TOOLCHAIN_DIR` | Explicit tool/runtime discovery root |

Secrets use the owning adapter's credential storage; do not move protected settings into chat-specific JSON. User preferences/data remain under `data/` and are not source files.

## Troubleshooting

[Setup](SETUP.md) lists repair/startup/native log locations. [Integrations](INTEGRATIONS.md) distinguishes connectivity, credentials, model access and quota. [Development](DEVELOPMENT.md) defines offline tests and review gates. Never disable TLS validation, alter global networking or delete user data as an automatic repair step.
