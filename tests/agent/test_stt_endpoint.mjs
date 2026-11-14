import test from "node:test";
import assert from "node:assert/strict";
import { assertLoopbackUrl } from "../../apps/desktop/workbench/src/modules/ai/lib/sttEndpoint.ts";

test("Whisper readiness and transcription share the loopback-only endpoint policy", () => {
  for (const url of ["http://localhost:8080", "http://127.0.0.1:8080/prefix", "http://[::1]:8080"])
    assert.doesNotThrow(() => assertLoopbackUrl(url));
  for (const url of ["https://example.com", "http://localhost.evil.test", "http://192.168.1.1", "file://localhost/a", "http://user:pass@localhost:8080", "bad-url"])
    assert.throws(() => assertLoopbackUrl(url));
});
