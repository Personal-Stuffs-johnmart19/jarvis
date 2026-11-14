const utilities = new WeakSet<object>();
export type QueuedMcpPreparation = "ask" | "allow" | "unavailable";
const mcps = new WeakMap<object,{preparation:(callId:string)=>QueuedMcpPreparation;revoke?:(callId:string)=>void}>();
export function queuedMcp<T extends object>(action:T, helpers:{preparation:(callId:string)=>QueuedMcpPreparation;revoke?:(callId:string)=>void}):T {
  mcps.set(action,helpers);return action;
}
export const isQueuedMcp=(action:object)=>mcps.has(action);
export const queuedMcpPreparation=(action:object,callId:string):QueuedMcpPreparation=>mcps.get(action)?.preparation(callId)??"unavailable";
export const revokeQueuedMcp=(action:object,callId:string)=>mcps.get(action)?.revoke?.(callId);
const gateways = new WeakMap<object, ReadonlyMap<string,object>>();

/** Private canonical lookup: catalogue-only tools need no additional model schemas. */
export function registerQueuedGateway<T extends object>(gateway:T,local:Record<string,object>):T {
  gateways.set(gateway,new Map(Object.entries(local)));
  return gateway;
}
export function queuedGatewayAction(gateway:object,name:string):object|undefined {
  return gateways.get(gateway)?.get(name);
}
export const QUEUED_UTILITY_REQUEST = Symbol("queued utility request id");
export function queuedUtilityRequest(context:unknown):string|undefined {
  if(typeof context!=="object"||context===null||!(QUEUED_UTILITY_REQUEST in context))return undefined;
  const value=context[QUEUED_UTILITY_REQUEST];
  if(typeof value!=="string"||!/^[a-f0-9]{32}$/.test(value))throw new Error("Invalid queued utility request id");
  return value;
}

/** Explicit canonical-handler opt-in; native queue execution is approval gated. */
export function queuedUtility<T extends { needsApproval?: unknown }>(action: T): T {
  if (action.needsApproval !== true) throw new Error("Queued utilities require approval");
  utilities.add(action);
  return action;
}

export const isQueuedUtility = (action: object) => utilities.has(action);
