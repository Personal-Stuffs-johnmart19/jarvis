import { invoke } from "@/host/core";
type Bootstrap = { base: string; token: string; model: string; test: boolean };
let pending: Promise<Bootstrap> | undefined;
export function getJarvis(): Promise<Bootstrap> {
  if (!pending) pending = invoke<Bootstrap>("backend", {method:"workbench.bootstrap",params:{}});
  return pending;
}
