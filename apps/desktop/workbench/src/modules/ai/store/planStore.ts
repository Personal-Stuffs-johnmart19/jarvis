import { create } from "zustand";
import { native } from "../lib/native";
import type { WorkspaceEnv } from "@/modules/workspace";
import { isOwnedError, type OwnedError } from "../../../../../src/displayError";
import { subscribeExplicitAgentStop } from "../lib/explicitAgentStop";

export type QueuedEdit = {
  id: string;
  kind: "write_file" | "edit" | "multi_edit" | "create_directory";
  path: string;
  /** Workspace approved with this proposal, independent of the active workspace. */
  workspace: WorkspaceEnv;
  /** Complete original text, or empty only for a proven missing file/directory. */
  originalContent: string;
  proposedContent: string;
  isNewFile: boolean;
  description?: string;
  failure?: OwnedError;
};
export type PlanApplyResult = { id: string; ok: boolean; error?: OwnedError };
type PlanState = {
  active: boolean;
  applying: boolean;
  applyNotice?: OwnedError;
  queue: QueuedEdit[];
  toggle: () => void;
  enable: () => void;
  disable: () => void;
  enqueue: (q: QueuedEdit) => void;
  removeOne: (id: string) => void;
  clear: () => void;
  applyAll: () => Promise<PlanApplyResult[]>;
};
const owned = (message: string, ...values: OwnedError["values"]): OwnedError => ({source:"jarvis",message,values});
const stopped = () => owned("Plan application stopped. Changes already being written may still finish.");
let applyEpoch = 0;
let nextId = 1;
export function newQueuedEditId(): string {
  return `q-${Date.now().toString(36)}-${(nextId++).toString(36)}`;
}
export const usePlanStore = create<PlanState>((set, get) => ({
  active: false,
  applying: false,
  queue: [],
  toggle: () => {
    if(get().active)get().disable();
    else get().enable();
  },
  enable: () => set({ active: true }),
  disable: () => {
    applyEpoch++;
    set({ active: false, queue: [], applyNotice: get().applying ? stopped() : undefined });
  },
  enqueue: q => set(s => ({queue:[...s.queue,{...q,workspace:{...q.workspace}}]})),
  removeOne: id => set(s => ({queue:s.queue.filter(q => q.id !== id)})),
  clear: () => {
    applyEpoch++;
    set({queue:[],applyNotice:get().applying ? stopped() : undefined});
  },
  async applyAll() {
    if(get().applying)return [];
    const epoch=applyEpoch;
    const items=get().queue.map(entry=>({entry,snapshot:{...entry,workspace:{...entry.workspace}}}));
    const results:PlanApplyResult[]=[];
    set({applying:true,applyNotice:undefined});
    try {
      for(const {entry,snapshot:q} of items){
        if(epoch!==applyEpoch)break;
        if(!get().queue.includes(entry))continue;
        try {
          if(q.kind!=="create_directory"){
            const current=await native.readSnapshot(q.path,q.workspace);
            if(epoch!==applyEpoch)break;
            if(!get().queue.includes(entry))continue;
            if(q.isNewFile ? current.kind!=="missing" : current.kind==="missing" || (current.kind==="text"&&current.content!==q.originalContent))
              throw owned("File changed since this proposal was prepared. Review it again before applying.");
            if(current.kind!=="missing"&&current.kind!=="text")
              throw owned("File is no longer readable as text. Review it again before applying.");
            // This preflight catches edits made since review; it is not an atomic compare-and-write.
            await native.writeFile(q.path,q.proposedContent,q.workspace);
          }else{
            await native.createDir(q.path,q.workspace);
          }
          results.push({id:q.id,ok:true});
          set(s=>({queue:s.queue.filter(item=>item!==entry)}));
        }catch(error){
          const failure=isOwnedError(error)
            ? error : owned("Could not apply this change: {0}",String(error).slice(0,400));
          results.push({id:q.id,ok:false,error:failure});
          set(s=>({queue:s.queue.map(item=>item===entry?{...item,failure}:item)}));
        }
      }
      return results;
    }finally{set({applying:false});}
  },
}));
subscribeExplicitAgentStop(()=>{
  applyEpoch++;
  if(usePlanStore.getState().applying)usePlanStore.setState({applyNotice:stopped()});
});
