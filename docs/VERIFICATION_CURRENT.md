# Verification and release boundaries

Updated 2026-11-15. Evidence applies to the named source, artifact and exercised paths. See [Development](DEVELOPMENT.md) for focused checks and [the roadmap](ECOSYSTEM_ROADMAP.md) for remaining work.

## Selected Windows portable

- Build source: `f38b98f64dac213566705099261877fb67d95773`; `apps/desktop/` tree: `252e5d75f67d29c0e311ea254ecb9c93aba17a6f`.
- Portable: `jarvis-desktop-0b7374c7a8dc561ee4c6b9317998f23dd284c0478a75facb315acf0891484b0d.exe` (27,311,616 bytes).
- SHA-256: `0b7374c7a8dc561ee4c6b9317998f23dd284c0478a75facb315acf0891484b0d`.
- One optimized build completed in 262 seconds. Subsequent fixture and release-document changes do not change embedded application inputs.
- The manifest selects the latest portable. The required terminal companion remains `desktop-bin/companions/terax/2e2471fb833e097d/terax-cli.exe`, SHA-256 `2e2471fb833e097d0edd8f0dc525f1dcd02ed0dc3254110acbbb5a871bd8ed51`, with its upstream notices. Python packages, engines and model weights remain separate.

## Language menu verification

Language menus use the theme's paired popup background and foreground. Input overlays such as the default dark theme's translucent white no longer become the menu background. Missing popup overrides fall back to the current mode's defaults; selected, highlighted and indicator text inherit the popup foreground.

Owned WebView checks covered Jarvis Default and Glass in dark/light modes, with shell, settings-header and embedded pickers. Default palettes, translucent input overlays and palettes omitting popup tokens were exercised in checked and highlighted states. The served matrix passed 72 states; the same packaged EXE covered 72 unique states across two bounded runs, with minimum text contrast **6.86:1** against a **4.5:1** requirement.

Keyboard navigation, focus restoration, Escape and English/Russian selection with reload persistence passed through all three owners. Both packaged runs had no page errors and closed their owned hosts. The fixture awaits persisted theme ID and mode before reload; its contrast validators and default two-theme coverage remain intact. The earlier geometry-only mode is preserved; unchanged zoom geometry was not repeated for this color fix.

Offline agent contracts (462), TypeScript and the managed staged source scan passed; the latter covered two changed source files with zero findings. Independent standards and spec reviews approved the source and fixture corrections. [Source quality](https://github.com/Personal-Stuffs-johnmart19/jarvis/actions/runs/43726689985), [Python services](https://github.com/Personal-Stuffs-johnmart19/jarvis/actions/runs/43726690032) and [public-tree/history checks](https://github.com/Personal-Stuffs-johnmart19/jarvis/actions/runs/43726690007) passed at the build source.

## Limits

This is focused menu/theme acceptance. It does not re-certify every custom palette, broad chat/tool execution, live providers, personal Chrome/media, microphone, OCR, a fresh network installation, or all GUI/zoom paths. No real model API calls or personal-profile changes were used. Configured integrations and downloaded models need their own bounded evidence; [Speech](SPEECH.md) owns CPU/Vulkan and model-asset limits.

Synchronous network cancellation remains cooperative; remote MCP cancellation does not promise rollback. URL validation does not pin transport DNS/proxy destinations, and filesystem mutations are not a complete capability sandbox. Other operating systems require separate native GUI/lifecycle verification. Keep private credentials, chats, models and user data outside the public tree; follow [Security](../SECURITY.md).
