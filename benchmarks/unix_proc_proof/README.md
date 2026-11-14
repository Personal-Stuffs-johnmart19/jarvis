# Unix owned process-group proof

Small standalone Rust test crate importing the actual production `proc/capture.rs` by path; it does not copy or replace the runner. Requires Linux/WSL, Python 3, Rust and the pinned crates already in Cargo's cache. The lockfile pins the proof dependencies. No models, cloud API, desktop UI or user processes are used.

From the repository root inside Linux/WSL (with the intended Rust toolchain on PATH):

```sh
JARVIS_UNIX_PROOF_OUTPUT="$PWD/.cache/verification/unix-proc-proof" CARGO_TARGET_DIR="$PWD/.cache/verification/unix-proc-target" cargo test --locked --offline --manifest-path benchmarks/unix_proc_proof/Cargo.toml -- --test-threads=1 --nocapture
```

For this workspace the existing dependency cache is `/mnt/d/Jarvis/.cache/desktop-toolchain/cargo`; set process-local `CARGO_HOME` to that directory if needed. The scoped Rust installation used for the recorded run lives under `.cache/verification/wsl-proc-proof/toolchain`, with no user shell/profile edits.

Six existing production tests run alongside three owned Python root/child/grandchild proofs: explicit cancellation, timeout, and successful root exit with background descendants. The cancellation case verifies all three PIDs share the owned root's process group before Stop. Cleanup signals only that owned group. Termination checks treat zombies as non-executing; the recorded run additionally confirmed every proof PID disappeared from `/proc` afterward.

Recorded evidence: `.cache/verification/wsl-proc-proof/result.json` (production-source hash and final PID cleanup), `result.log` (9 passing tests), and `setup.log` (scoped toolchain provenance). The recorded run used the same standalone harness with cache-local source paths before this relocatable copy was added. This proves the exercised production Unix capture/process-group behavior, not a Linux Jarvis GUI, native host bridge, macOS, escaped process groups, or every tool integration.
