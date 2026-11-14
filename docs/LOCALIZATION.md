# Localization

English is the source language for app-owned interface text. Russian and future languages belong in separate catalogs. Do not translate model/provider IDs, command arguments, filesystem paths, source code, OS device names or user-authored chat/file content.

The shell translator is `apps/desktop/src/localization.ts`, with `src/locales/en.json` and `ru.json`. `t(message, ...values)` uses the English source as key, numbered placeholders and an English fallback. Language selection is persisted under `jarvis.ui.language`. Components must rerender after a locale change; translating only at module load or storing already translated status text makes stale labels.

The embedded workspace must use the same lookup and language-change subscription as the shell. Its former standalone catalog has been merged into the shell catalogs. Backend messages should return English templates/data or stable IDs and translate only at the display boundary. Voice preview samples belong in language-specific resources, independent of interface selection.

## Audit requirements

- Extract static visible JSX text, accessible labels, placeholders and component-owned labels to translation calls/catalogs.
- Review conditional/dynamic labels, notifications, confirmation dialogs, errors, menus, context meters, tool states and empty/loading states; extraction alone does not cover them.
- Keep English/Russian key and numbered-placeholder parity. Use English fallback when a translation is missing; never relabel English text as a completed Russian translation.
- Scan source literals for accidental Russian UI prose. Language recognition patterns and genuine language samples need explicit classification, not blind deletion.
- Verify language changes in both surfaces without resetting chat/editor state, and verify persistence after restart.

The translation audit report records remaining findings and its limitations. A zero static-literal finding does not prove all runtime messages are localized.

## Adding a language

Add a message catalog beside `en.json`, import/register it in `localeDefinitions` in `localization.ts`, and keep its keys/placeholders aligned with English. Count-dependent messages use per-language `*.plurals.json` forms and `countMessage`, selected through `Intl.PluralRules`; do not concatenate a translated noun with an English plural suffix. The picker and locale validation derive from that registry; no additional language union or menu copy is needed. Backend messages and language-specific speech samples are canonical resources in `local_mode/locales/`, imported by the frontend; no separately maintained frontend copies are needed.

Run `node tools/audit_localization.mjs .cache/localization.json` and `python tools/audit_localization.py .cache/localization-backend.json` after installing locked app/development dependencies. See [current audit boundaries](LOCALIZATION_AUDIT.md).
