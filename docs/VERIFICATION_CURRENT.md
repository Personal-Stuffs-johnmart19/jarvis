# Verification and release boundaries

Updated 2026-11-15. Evidence applies to the named source, artifact and exercised paths. See [Development](DEVELOPMENT.md) for checks and [the roadmap](ECOSYSTEM_ROADMAP.md) for remaining work.

## Selected Windows portable

- Build source: `811b98d12082b95845225dceb14b2d63615f9a6e`; `apps/desktop/` tree: `d9269b6eb82fccf73e62ef3da328ac696295a964`.
- Portable: `jarvis-desktop-8005c6af568e92af1bb98d2ab5d308a737fd44e89c6d8666ac7d0782bff7e28e.exe` (27,311,616 bytes).
- SHA-256: `8005c6af568e92af1bb98d2ab5d308a737fd44e89c6d8666ac7d0782bff7e28e`.
- One optimized build completed in 256 seconds. Subsequent release documentation and test-fixture changes do not change embedded application inputs.
- The previous root portable is retired. The required terminal companion remains `desktop-bin/companions/terax/2e2471fb833e097d/terax-cli.exe`, SHA-256 `2e2471fb833e097d0edd8f0dc525f1dcd02ed0dc3254110acbbb5a871bd8ed51`, with its upstream notices. Python packages, engines and model weights remain separate from the EXE.

## Exercised paths

The retired phone transport, sole client abstraction, activation settings, model-facing startup option, BLE protocol documentation and three unused Windows projections are removed. Configured Telegram authorization, queue dispatch and shutdown remain. Old login shortcuts containing `--remote` open the normal desktop; new startup entries omit it. Existing credential-denial rules still protect old private token files.

Focused offline checks covered Telegram polling and backlog handling, owner authorization, launcher arguments and repair, autostart registration, English/Russian status text, and legacy Windows PowerShell parsing. Agent contracts (462) and TypeScript passed with the current frozen dependencies. The remaining 145 full-install pins resolve identically from the dependency source; no model frameworks were installed for this check. The managed security scan covered 15 changed source files with zero findings. Independent standards and spec reviews approved the cleanup.

[Source quality](https://github.com/Personal-Stuffs-johnmart19/jarvis/actions/runs/43665853178), [Python services](https://github.com/Personal-Stuffs-johnmart19/jarvis/actions/runs/43665853185) and [public-tree/history checks](https://github.com/Personal-Stuffs-johnmart19/jarvis/actions/runs/43665853171) passed at the cleanup source.

The selected bundled host completed a fresh 4K chat and an approved owned Python command returning 42. Its Activity group and nested action details started collapsed, expanded to show the result, and collapsed again. The accepted run made three synthetic loopback provider requests and had zero page errors; no real model API was called. The fixture uses the canonical displayed action label and preserves the request-count and disclosure assertions. This does not repeat the earlier full language-picker or zoom matrices.

## Limits

This checkpoint does not certify a complete fresh network installation, live Telegram delivery, OCR activation, authenticated providers, personal Chrome/media, microphone, or model quality and performance. Configured integrations and downloaded models need their own bounded execution evidence. [Speech](SPEECH.md) owns CPU/Vulkan and model-asset limits.

Synchronous network cancellation remains cooperative; remote MCP cancellation does not promise rollback. URL validation does not pin transport DNS/proxy destinations, and filesystem mutations are not a complete capability sandbox. Other operating systems require separate native GUI/lifecycle verification. Preserve private credentials, chats, models and user data outside the public tree; follow [Security](../SECURITY.md).
