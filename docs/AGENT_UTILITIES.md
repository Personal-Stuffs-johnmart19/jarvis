# Shared agent utilities

Jarvis exposes one local toolchain to chat, Files & Tools and every supported model provider. Utilities execute on the user's computer even when the model is hosted. This does not embed Codex's proprietary services or promise that every optional compiler is installed.

## Tools and discovery

A compact set of read/search/data tools plus utility_catalog, utility_run and run_code is sent directly to models. All other handlers remain discoverable through jarvis_catalog and executable through jarvis_action with the same policy. The catalogue deduplicates published native snapshots against current handlers, returns short summaries for broad searches, and sends a full schema on an exact-name lookup. utility_catalog itself supports filtering and pagination (default 12 entries). This keeps local-model context available for actual work.

| Tool | Purpose | Approval |
| --- | --- | --- |
| `utility_catalog` | Installed executables, runtime modules, paths and availability | No |
| `utility_run` | Known CLI with literal `args`, explicit workspace `cwd`, bounded output/timeout | Yes |
| `run_code` | Python or JavaScript with optional argv and workspace cwd | Yes |
| `archive_list` | Inspect ZIP entries and expanded size | No |
| `archive_create` | ZIP selected files/folders into a new file | Yes |
| `archive_extract` | Extract a ZIP into a new directory | Yes |

Existing grep/glob, file read/write/edit, Git, terminal and browser tools remain shared. Search uses the ripgrep Rust engine without requiring an external `rg.exe`. ZIP uses Rust without requiring `zip.exe` or Python. General CLI execution is not an OS sandbox: it has the user's permissions and can access the network. Approving executable code approves its behavior, not just its working directory. No packages are installed automatically.

The executable catalogue covers: `rg`, `fd`, `jq`, `yq`, `git`, `gh`, `python`, `node`, `npm`, `pnpm`, `uv`, `pip`, `cargo`, `rustc`, `go`, `dotnet`, `java`, `javac`, `gcc`, `clang`, `cmake`, `ninja`, `make`, `curl`, `tar`, `zip`, `unzip`, `7z`, `ffmpeg`, `ffprobe`, `magick`, `tesseract`, `pdftotext`, `pdfinfo`, `pdftoppm`, `sqlite3`, `bash`, `pwsh`, `powershell`, `wsl`, `pandoc`, `soffice`.

Settings → Advanced Settings → Utilities shows availability and exact paths, with search and refresh. Missing CLI executables with a curated Windows package offer Install…; built-in capabilities and tools without a managed installer remain usable through their alternatives. Built-in means native functionality exists, not that a same-name CLI executable exists. Shell-only wrappers use `bash_run`; missing tools need a user-installed executable or an available alternative.

## Runtime selection

Project `.venv` Python takes priority. Other executables come from PATH, an explicitly configured `JARVIS_TOOLCHAIN_DIR`, or an existing shared Codex runtime under the current user's cache. Jarvis neither copies that runtime into releases nor depends on it being present. Node package entrypoints are executed with Node directly; batch files are not interpolated through cmd.exe. Python's pip is offered directly only when its module is present in the selected Windows runtime.

Discovered directories are added only to Jarvis's process PATH at startup, so new terminals and subprocesses can find them. System/user PATH is unchanged. `JARVIS_TOOLCHAIN_DIR` points to the dependencies directory, for example the parent of `node`, `python`, and `native`. Existing terminal sessions retain their inherited environment. Refresh rereads the Windows user/machine PATH and known application bin directories without changing system settings. utility_run passes the updated PATH to its children; existing terminal sessions still need restarting.

`utility_run`/`run_code` execute on the host. In a WSL workspace they return an explicit instruction to use `bash_run` inside the selected distribution, rather than silently running on Windows. Native archive tools use the workspace's filesystem mapping.

## Limits and lifecycle

CLI: at most 128 arguments / 64000 bytes total, 1–300 seconds, default stdout 8000 bytes (maximum 32000), stderr 4000 bytes. Results include stdout, stderr, exit code, timeout and truncation. Foreground command descendants are stopped on completion/timeout via an owned Windows Job or Unix process group; intentionally detached daemons belong in the terminal workflow. This is lifecycle control, not security isolation.

ZIP: 1–100 sources, at most 10000 entries and 512 MiB uncompressed. Input archive at most 520 MiB. Listing returns at most 200 entries; model results default to 30. Output parents must exist and destinations must be new. Creation uses a temporary file and no-clobber publication; extraction stages its output before publishing. Archives reject traversal, Windows aliases, duplicate case-insensitive names, encrypted/special entries and symbolic links. No executable modes are restored. Creation skips links and protected credential/metadata paths; protected archives cannot be extracted or inspected with this tool. Filesystem guards are not a sandbox against a hostile concurrent local process.

## Examples

```json
{"name":"run_code","args":{"language":"python","code":"print(6 * 7)","cwd":"D:/Projects/demo"}}
```

```json
{"name":"utility_run","args":{"tool":"git","args":["status","--short"],"cwd":"D:/Projects/demo"}}
```

```json
{"name":"archive_create","args":{"sources":["D:/Projects/demo/report.txt"],"destination":"D:/Projects/demo/report.zip"}}
```

## On-demand installation (Windows)

Install… shows the fixed package ID from the WinGet community source. The user chooses current-user (default) or machine scope, accepts package/source agreements and clicks Install package. Machine scope can trigger Windows elevation; an unsupported user scope is reported as an error, never automatically retried with higher privileges. The native command validates the exact package ID, scope and agreement flag. The installer is deliberately absent from the AI catalogue: models can discover/run tools but do not initiate Settings installations automatically.

Only one installer runs at a time. WinGet receives literal argv, exact ID, fixed `winget` source, no-upgrade, no hash/security bypass and a 15-minute timeout. Bounded output and exit status are shown. Packages may install dependencies or modify their normal installation/PATH settings. A successful package install and a discovered executable are reported separately; a restart can be needed. Active installation supports owned-process cancellation. No bulk downloads, uninstall, force reinstall, or automatic privilege escalation. GCC/make, OS components, and active-venv pip repair have no generic managed installer here; use manual setup or alternatives. ZIP operations remain native.

Ordinary UI QA blocks utility_install at the native boundary. A deliberate `JARVIS_QA_INSTALL=1` test is required for an actual package download. Package availability is not proof that every installer supports both scopes. The live installer/elevation matrix remains incomplete.

Reference: [Microsoft WinGet install](https://learn.microsoft.com/en-us/windows/package-manager/winget/install).
