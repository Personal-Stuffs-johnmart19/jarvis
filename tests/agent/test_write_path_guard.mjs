import test from "node:test";
import assert from "node:assert/strict";
import { checkWritableCanonical } from "./security_fixture.mjs";

test("recursive new targets cannot hide protected ancestors behind an alias", async () => {
  const calls = [];
  const result = await checkWritableCanonical("/owned/alias/new/more", async path => {
    calls.push(path);
    return "/owned/.ssh/new/more";
  });
  assert.equal(result.ok, false);
  assert.match(result.reason, /protected directory/);
  assert.deepEqual(calls, ["/owned/alias/new/more"]);
});

test("legitimate recursive creates use the canonical nearest ancestor target", async () => {
  const result = await checkWritableCanonical("/owned/alias/new/more", async () => "/owned/project/new/more");
  assert.deepEqual(result, { ok: true, canonical: "/owned/project/new/more" });
});

test("missing permission and unknown resolution failures never become literal-path approval", async () => {
  for (const error of ["not found", "permission denied", new Error("unknown canonicalization failure")]) {
    let calls = 0;
    const result = await checkWritableCanonical("/owned/alias/new/more", async () => {
      calls += 1;
      throw error;
    });
    assert.equal(result.ok, false);
    assert.match(result.reason, /Cannot resolve file safely/);
    assert.equal(calls, 1);
  }
});

test("literal protected targets are rejected before native lookup", async () => {
  const result = await checkWritableCanonical("/owned/.ssh/new/more", async () => {
    throw new Error("must not lookup a protected literal");
  });
  assert.equal(result.ok, false);
  assert.match(result.reason, /protected directory/);
});
