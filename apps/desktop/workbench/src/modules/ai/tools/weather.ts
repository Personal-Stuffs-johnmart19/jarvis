import { tool, type JSONValue } from "ai";
import { z } from "zod";
import type { buildJarvisTools } from "./jarvis";

const schema = z.object({ city: z.string().trim().min(1).max(200), time: z.enum(["today", "current"]).optional() });

/** A conversational lookup reuses the canonical policy and handler. */
export function buildWeatherTools(shared: ReturnType<typeof buildJarvisTools>["jarvis_action"]) {
  const request = (args: z.infer<typeof schema>) => ({ name: "weather_report", args });
  return {
    weather_report: tool({
      description: "Get current weather and today's forecast for a city. Use directly; no catalogue, MCP or browser needed. Answer only from returned data and include source/time/units; never infer current weather from memory. Ask for a city only if unknown. Explain unavailable/ambiguous results honestly.",
      inputSchema: schema,
      needsApproval: async (input, options) => typeof shared.needsApproval === "function"
        ? await shared.needsApproval(request(input), options) : Boolean(shared.needsApproval),
      execute: async (input, options) => {
        if (!shared.execute) throw new Error("Weather capability has no executor");
        return shared.execute(request(input), options);
      },
      toModelOutput: async ({ input, output, toolCallId }) => shared.toModelOutput
        ? shared.toModelOutput({ input: request(input), output, toolCallId })
        : { type: "json", value: (output ?? null) as JSONValue },
    }),
  };
}
