# Setup

Run `INSTALL.cmd` for first-time installation, then `START.cmd` or `Jarvis.cmd`. Startup checks the environment and launches the SHA-256-selected portable from `desktop-bin/manifest.json`. Python services remain separate from the native executable; a lone EXE is not the entire installation.

Configure provider credentials and endpoints in **Settings → Integrations**. Choose the shared chat model in **Models & Providers**. Select microphone, synthesis model and speaker in **Speech & Voice**. The camera is optional and off by default. Do not infer speech processing location from the selected chat model.

Chat and Files & Tools use the same agent capabilities, model configuration, uploaded library and approvals. Attach files from either composer. Select the correct Windows drive or WSL distribution before issuing filesystem/terminal commands.

F4 toggles microphone input; F6 selects text-only operation; F7 pauses/resumes speech; F9 stops speech. Agent Stop cancels the active agent request and its owned command tasks. Stopping speech is separate from stopping an agent or browser music.

Exit the running instance through the tray before updating. Launch again through `START.cmd`; do not overwrite a running executable or delete companion programs.

## Diagnostics

- Setup/repair: `data/logs/repair.log`.
- Startup: `data/logs/startup.log`.
- Native host: `data/logs/desktop-native.log`.
- Runtime/model logs remain beside their managed runtime directories.

Do not include credentials, private chats or uploaded files in diagnostic submissions. Advanced CLI options are documented in [CLI guide](CLI.md). Developers should follow [Development](DEVELOPMENT.md), not reinstall the environment for every test.
