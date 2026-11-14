import { useEffect, useState } from "react";
import type { ToolUIPart } from "ai";
import { listen } from "@/host/event";
import { invoke } from "@/host/core";
import { t } from "../../../../../src/localization";
import { useLocale } from "../../../../../src/i18n";
import { revokePendingAction } from "../tools/jarvis";
import { QueuedTaskApprovalController, QueuedUiReadController, QueuedNativeDispatchController, QueuedTaskUiError, type QueuedTaskCard, type QueuedNativeCard } from "../lib/queuedTaskApprovals";
import { getChatTools } from "../store/chatRuntime";
import { useChatStore } from "../store/chatStore";
import { isQueuedReadOnly, QUEUED_READ_CONTEXT } from "../tools/queuedReadOnly";
import { isQueuedUtility, isQueuedMcp, queuedMcpPreparation, queuedGatewayAction, QUEUED_UTILITY_REQUEST } from "../tools/queuedDispatch";
import { createMcpOperation } from "../../mcp/client";
import { subscribeExplicitAgentStop } from "../lib/explicitAgentStop";
import { currentWorkspaceScopeKey } from "@/modules/workspace";
import { queuedWorkspaceFingerprint } from "../lib/queuedTaskApprovals";
import { displayError, errorText } from "../../../../../src/displayError";
import { AiToolApproval } from "./AiToolApproval";

/** Out-of-band queue decisions never enter the model's chat/transcript stream. */
export function QueuedTaskApprovals({ owner, session, route }: {owner:string; session:string;route:string}) {
  useLocale();
  const [cards, setCards] = useState<QueuedTaskCard[]>([]);
  const [nativeCards,setNativeCards]=useState<QueuedNativeCard[]>([]);
  const [nativeController,setNativeController]=useState<QueuedNativeDispatchController>();
  const [cleanupPending,setCleanupPending]=useState(false);
  const [controller, setController] = useState<QueuedTaskApprovalController>();
  const [refresh, setRefresh] = useState<(() => void)>();
  useEffect(() => {
    let active = true;
    let off: (() => void) | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let drain: ReturnType<typeof setTimeout> | undefined;
    const tools = getChatTools(session), action = tools.jarvis_action;
    const options = (toolCallId: string, abortSignal?:AbortSignal) => ({toolCallId, messages:[], context:{},abortSignal});
    const prepare = async (input: {name:string;args:Record<string,unknown>}, id: string) =>
      typeof action.needsApproval === "function" ? await action.needsApproval(input, options(id)) : Boolean(action.needsApproval);
    const execute = async (input: {name:string;args:Record<string,unknown>}, id: string, signal?:AbortSignal,nativeRequestId?:string) => {
      if (!action.execute) throw new QueuedTaskUiError("Queued task action has no executor");
      const local=queuedGatewayAction(action,input.name);
      if(nativeRequestId&&local&&isQueuedMcp(local)){
        const request=input as typeof input&{cleanup_nonce?:string};
        if(!request.cleanup_nonce)throw new QueuedTaskUiError("Queued task action failed");
        const operation=createMcpOperation(invoke,{requestId:nativeRequestId,cleanupNonce:request.cleanup_nonce});
        const result=await action.execute(input,{...options(id,signal),context:operation.context});
        const settled=await operation.settle();
        return {result,transport_settled:settled,output_complete:true};
      }
      const context=signal&&local&&isQueuedReadOnly(local)?{[QUEUED_READ_CONTEXT]:true}
        :nativeRequestId&&local&&isQueuedUtility(local)?{[QUEUED_UTILITY_REQUEST]:nativeRequestId}:{};
      return await action.execute(input, {...options(id,signal),context});
    };
    const queue = new QueuedTaskApprovalController(owner, session,
      {prepare, execute, revoke:revokePendingAction}, rows => { if (active) setCards(rows); });
    const reads = new QueuedUiReadController(owner,session,route,{prepare,execute,revoke:revokePendingAction,
      readable:name=>{const local=queuedGatewayAction(action,name);return local!==undefined&&isQueuedReadOnly(local);},
      current:()=>{const live=useChatStore.getState().live;return getChatTools(session)===tools?queuedWorkspaceFingerprint(currentWorkspaceScopeKey(),live.getCwd(),live.getWorkspaceRoot()):null;},
      claim:request=>invoke("backend",{method:"tasks.ui.claim",params:request}),
      complete:request=>invoke("backend",{method:"tasks.ui.complete",params:request}),
      drained:()=>{if(active&&!drain)drain=setTimeout(()=>{drain=undefined;void backfill();},0);},
    });
    const dispatch=new QueuedNativeDispatchController(owner,session,route,{prepare,execute,revoke:revokePendingAction,
      current:()=>{const live=useChatStore.getState().live;return getChatTools(session)===tools?queuedWorkspaceFingerprint(currentWorkspaceScopeKey(),live.getCwd(),live.getWorkspaceRoot()):null;},
      dispatchable:(name,kind)=>{const local=queuedGatewayAction(action,name);return local!==undefined&&(kind==="mcp"?name==="mcp_call"&&isQueuedMcp(local):isQueuedUtility(local));},
      preparation:(input,id)=>{const local=queuedGatewayAction(action,input.name);return local?queuedMcpPreparation(local,id):"unavailable";},
      claim:request=>invoke("backend",{method:"tasks.ui.claim",params:request}),
      alive:request=>invoke("backend",{method:"tasks.ui.check",params:request}),
      start:request=>invoke<{ok:boolean;waiting?:boolean}>("backend",{method:"tasks.ui.start",params:request}),
      settled:request=>invoke("backend",{method:"tasks.ui.settled",params:request}),
      probeNative:(nonce,kind,cleanupNonce)=>kind==="mcp"
        ?invoke<{settled:boolean}>("mcp_cancel",{requestId:nonce,cleanupNonce})
        :invoke<{settled:boolean}>("shell_run_cancel",{requestId:nonce,seal:true}),
      complete:request=>invoke("backend",{method:"tasks.ui.complete",params:request}),
      changed:rows=>{if(active)setNativeCards(rows);},
      drained:()=>{if(active&&!drain)drain=setTimeout(()=>{drain=undefined;void backfill();},0);},
    });
    setNativeCards([]);setNativeController(dispatch);
    const offStop=subscribeExplicitAgentStop(()=>{queue.clear();reads.clear();dispatch.clear();});
    setCards([]); setController(queue);
    const backfill = async (attempt = 0) => {
      try {
        if (!active) return;
        const live=useChatStore.getState().live,workspace=queuedWorkspaceFingerprint(currentWorkspaceScopeKey(),live.getCwd(),live.getWorkspaceRoot());
        const pending=await invoke<unknown[]>("backend",{method:"tasks.ui.pending",params:{owner,session,route,workspace}}).catch(error=>{console.error(error);return [];});
        if(!active)return;
        const orphaned=await invoke<{items:unknown[];watch:boolean}>("backend",{method:"tasks.ui.orphaned",params:{}}).catch(()=>({items:[],watch:false}));
        const recovered=await Promise.all(orphaned.items.map(request=>dispatch.recover(request)));
        const pendingAcknowledgements=!await dispatch.recoverPending();
        if(!active)return;
        const pendingCleanup=recovered.some(value=>!value)||pendingAcknowledgements;setCleanupPending(pendingCleanup);
        if((pendingCleanup||orphaned.watch)&&!drain)drain=setTimeout(()=>{drain=undefined;void backfill();},500);
        for(const item of pending){void reads.receive(item);void dispatch.receive(item);}
        const output = await invoke<{ready?:boolean;items?:unknown[]}>("backend", {
          method:"services.read", params:{name:"tasks.pending",args:{owner,session}},
        });
        if (!active) return;
        if (output.ready === false && attempt < 3) {
          retry = setTimeout(() => { void backfill(attempt + 1); }, 250);
          return;
        }
        for (const item of output.items ?? []) await queue.receive(item);
      } catch (error) { if (active) console.error(error); }
    };
    setRefresh(() => () => { void backfill(); });
    void listen<{event:string;data:unknown}>("backend-event", ({payload}) => {
      if (active && payload.event === "tasks.approval") void queue.receive(payload.data);
      if(active&&payload.event==="tasks.ui.read"){void reads.receive(payload.data);void dispatch.receive(payload.data);}
      if(active&&payload.event==="tasks.ui.cancel"){reads.cancel(payload.data);dispatch.cancel(payload.data);}
      if (active && (payload.event === "history.clear" || payload.event === "tasks.cleared" || payload.event==="agent.stop")) {queue.clear();reads.clear();dispatch.clear();}
    }).then(unsubscribe => {
      if (!active) { unsubscribe(); return; }
      off = unsubscribe;
      void backfill();
    }).catch(console.error);
    return () => {
      active = false; off?.();offStop(); if (retry) clearTimeout(retry);if(drain)clearTimeout(drain);
      queue.dispose();reads.dispose();dispatch.dispose();setNativeController(undefined);setNativeCards([]); setController(undefined); setRefresh(undefined);
    };
  }, [owner, session, route]);
  if (!cards.length&&!nativeCards.length&&!cleanupPending) return null;
  return <aside aria-label={t("Queued task approvals")} className="fixed bottom-20 right-4 z-50 max-h-[55vh] w-[min(420px,calc(100vw-2rem))] space-y-3 overflow-y-auto rounded-lg border border-border bg-background p-3 shadow-lg">
    <div className="flex items-center justify-between gap-2 text-xs font-medium">
      <span>{t("Queued task approvals")}</span>
      <button type="button" onClick={refresh} className="text-muted-foreground underline">{t("Refresh pending approvals")}</button>
    </div>
    {cleanupPending&&<p role="status" className="text-xs">{t("Waiting for native task cleanup. Restart Jarvis if cleanup cannot finish.")}</p>}
    {cards.map(card => <section key={card.key} className="space-y-1">
      <p className="text-xs font-medium">{t("Queued task {0}", card.request.task_id)}</p>
      {card.goal && <p className="text-xs text-muted-foreground">{card.goal}</p>}
      {card.phase === "preparing" ? <p role="status" className="text-xs">{t("Preparing queued task approval…")}</p>
        : card.phase === "running" ? <p role="status" className="text-xs">{t(card.decision === false ? "Cancelling queued task…" : "Resuming queued task…")}</p>
        : card.phase === "error" ? <div role="alert" className="space-y-1 text-xs text-destructive">
          <p>{t("Queued task approval failed")}: {errorText(displayError(card.error)).slice(0, 400)}</p>
          <button type="button" onClick={() => { void controller?.respond(card.key, false); }} className="underline">{t("Deny")}</button>
        </div> : <AiToolApproval toolName="jarvis_action" previewAllArguments
          part={{type:"tool-jarvis_action",state:"approval-requested",toolCallId:card.callId,
            approval:{id:card.key},input:{name:card.request.name,args:card.request.args}} as Extract<ToolUIPart,{state:"approval-requested"}>}
          onRespond={approved => { void controller?.respond(card.key, approved); }} />}
    </section>)}
    {nativeCards.map(card=><section key={card.key} className="space-y-1">
      <p className="text-xs font-medium">{t("Queued task {0}",card.request.task_id)}</p>
      {card.phase==="preparing"?<p role="status" className="text-xs">{t("Preparing queued task approval…")}</p>
        :card.phase==="running"?<div className="flex items-center justify-between gap-2 text-xs">
          <p role="status">{t(card.decision===false?"Cancelling queued task…":"Resuming queued task…")}</p>
          <button type="button" disabled={card.decision===false} onClick={()=>nativeController?.cancel(card.request)} className="underline">{t("Stop")}</button>
        </div>
        :<AiToolApproval toolName="jarvis_action" previewAllArguments
          part={{type:"tool-jarvis_action",state:"approval-requested",toolCallId:card.callId,
            approval:{id:card.key},input:{name:card.request.name,args:card.request.args}} as Extract<ToolUIPart,{state:"approval-requested"}>}
          onRespond={approved=>{void nativeController?.respond(card.key,approved);}}/>}
    </section>)}
  </aside>;
}
