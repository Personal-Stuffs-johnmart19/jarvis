# Terminal CLI packaging

The Jarvis workspace is embedded. The only separately packaged component is its matching terminal control CLI; there is no separate development-window launcher or model/settings system.

`apps/desktop/workbench/SOURCE.json` pins the embedded frontend, native modules and terminal CLI. Preserve the Apache license and upstream attribution in [the adaptation notice](../third_party/terax/NOTICE.md).

`build_companions.ps1` builds the embedded Rust CLI and packages it with its license; `-PackageOnly` reuses an existing build. `-CliOnly` remains a compatibility alias. Node, pnpm and a standalone GUI source clone are not required.

`companions.json` retains the `terax` component/protocol identity and records the current revision and SHA-256 values. Packaging publishes fixed build/license inputs to an immutable CLI-hash directory, then atomically replaces the manifest. Previous manifest paths are never followed. A failed manifest update leaves the previous package selected; a complete unselected directory can be reused on retry. Packaging never removes old packages or user data. The embedded native host independently verifies the CLI path, size and checksum before installing its terminal alias. This is local integrity validation, not publisher authentication.

Packaging is not an in-place repair tool. An existing hash directory with different or unexpected contents is rejected without modification. If native verification fails, restore the companion files and manifest from a trusted Jarvis release; rerunning `-CliOnly` does not repair a corrupted immutable directory. Publishing a newly built CLI with a different hash selects a separate package and leaves previous files untouched.

Current provider/browser setup belongs in [Integrations](../docs/INTEGRATIONS.md) and [Browser](../docs/BROWSER.md). See [Architecture](../docs/ARCHITECTURE.md) for the shared runtime and [Development](../docs/DEVELOPMENT.md) for headless checks and release rules.
