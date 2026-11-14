# Integrations and API accounting

Use **Settings → Integrations** for credentials, local endpoints and provider setup. Chat/default/autocomplete selectors should show configured usable models, including installed local models that can load on demand. Discovery must not load weights simply to populate a menu.

LM Studio and Ollama discovery distinguish installed models from running models. A reachable server with no compatible models is not a ready chat provider. Capability/context information should use returned metadata, not guesses based on model names. Custom OpenAI-compatible endpoints retain their own URL, credentials and model IDs.

OpenClaw uses a configured OpenAI-compatible gateway endpoint; Jarvis does not import arbitrary consumer OAuth credentials. The default gateway URL is `http://127.0.0.1:18789/v1`. Discovered model/agent IDs are retained. Endpoint URLs reject embedded credentials, query-string keys and fragments; tokens use the native keyring. Live gateway/sign-in coverage remains limited, as recorded in the backlog.

## Speech connections

Recognition and synthesis credentials are independent of chat selection. Hide unavailable providers from runtime selectors and direct setup to Integrations. Local runtime health and model files are required even when no API key is needed.

## Google routing

Advanced **Network & Proxy** settings affect the Google adapter, not the entire PC. Direct, automatic/fallback and explicit proxy modes have distinct behavior. Connection tests are separate from an actual model response: TLS connectivity does not prove model access or quota. A model-response test consumes quota. Do not disable certificate verification or change global networking to resolve an application error.

## Usage

Chat input/output/cache counters show metadata returned by the selected provider, when present. The context meter is an estimate, not an invoice. Session totals are cumulative and are not the current request size.

Google SDK attempt counters count this application's requests/retries; they are not the project's remaining quota or other applications' usage. Missing token metadata remains unknown. Provider dashboards are authoritative for account billing and limits. No free-tier promise or hard-coded model price belongs in this guide.
