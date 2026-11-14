import test from "node:test";
import assert from "node:assert/strict";
import { z } from "../../apps/desktop/node_modules/zod/index.js";
import {
  preferenceInputSchema,
  parsePreferenceChange,
} from "../../apps/desktop/workbench/src/modules/ai/tools/preferenceSchema.ts";
test("preference tool exposes an object root and enforces key/value policies", () => {
  const schema = z.toJSONSchema(preferenceInputSchema);
  assert.equal(schema.type, "object");
  assert.equal(schema.oneOf, undefined);
  for (const value of [
    { key: "theme", value: "light" },
    { key: "editorFontSize", value: 16 },
    { key: "editorWordWrap", value: true },
    { key: "themeId", value: "default" },
    { key: "lmstudioReasoning", value: "off" },
  ])
    assert.deepEqual(
      parsePreferenceChange(preferenceInputSchema.parse(value)),
      value,
    );
  for (const value of [
    { key: "theme", value: "oops" },
    { key: "theme", value: 16 },
    { key: "editorFontSize", value: 100 },
    { key: "editorWordWrap", value: "true" },
    { key: "apiKey", value: "secret" },
    { key: "customInstructions", value: "Voice activation enabled" },
  ])
    assert.equal(preferenceInputSchema.safeParse(value).success, false);
});
