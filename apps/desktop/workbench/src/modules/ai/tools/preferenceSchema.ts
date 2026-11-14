import { z } from "zod";

const preferenceChange = z.discriminatedUnion("key", [
  z.object({
    key: z.literal("theme"),
    value: z.enum(["light", "dark", "system"]),
  }),
  z.object({
    key: z.literal("themeId"),
    value: z.string().min(1).max(128),
  }),
  z.object({
    key: z.literal("editorFontSize"),
    value: z.number().int().min(8).max(40),
  }),
  z.object({ key: z.literal("editorWordWrap"), value: z.boolean() }),
  z.object({
    key: z.literal("lmstudioReasoning"),
    value: z.enum(["default", "on", "off", "low", "medium", "high"]),
  }),
]);

// OpenAI-compatible servers require an object root, while validation still checks key/value pairs.
export const preferenceInputSchema = z
  .object({
    key: z.enum([
      "theme",
      "themeId",
      "editorFontSize",
      "editorWordWrap",
      "lmstudioReasoning",
    ]),
    value: z
      .union([z.boolean(), z.number(), z.string().max(12000)])
      .describe(
        "Value type depends on key: editorWordWrap is a JSON boolean (true/false); editorFontSize is an integer 8–40; theme, themeId and lmstudioReasoning are strings.",
      ),
  })
  .superRefine((value, ctx) => {
    const result = preferenceChange.safeParse(value);
    if (!result.success)
      for (const issue of result.error.issues)
        ctx.addIssue({
          code: "custom",
          path: issue.path,
          message: issue.message,
        });
  });

export const parsePreferenceChange = (input: unknown) =>
  preferenceChange.parse(input);
