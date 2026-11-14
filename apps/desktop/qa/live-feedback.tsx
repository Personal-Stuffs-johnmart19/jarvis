import { createRoot } from "react-dom/client";
import { ModelLibrary } from "../workbench/src/settings/components/ModelLibrary";
import { SharedAttachments } from "../workbench/src/modules/ai/components/SharedAttachments";
import { AiToolApproval } from "../workbench/src/modules/ai/components/AiToolApproval";
import { setLocale } from "../src/localization";
import "../workbench/src/styles/globals.css";
const qa = { locale:setLocale, calls:[] as string[], mode:"owned", resolve:null as null|((value:unknown)=>void), responses:[] as boolean[], rawPath:"C:/OWNED/{0}/Read file.gguf", rawRepo:"RAW/Read file{0}", rawFile:"Read file{0}.txt", rawModel:"Read file{0}",
  upload: [{id:"fixture",name:"Read file{0}.txt",path:"C:/OWNED/Read file{0}.txt",documentId:"owned-doc",size:100,kind:"text"}],
};
Object.assign(window,{__liveFeedbackQA:qa,__TAURI_INTERNALS__:{invoke:async(command:string,args:any)=>{
 const key=command==="backend"?args.method:command; qa.calls.push(key);
 if(key==="model_list_installed")return [{owner:"jarvis",complete:true,repo_id:qa.rawRepo,revision:"a".repeat(40),kind:"embedding",bundle_id:"fixture",files:[{path:qa.rawPath,size:10,sha256:"a".repeat(64)}]}];
 if(key==="models.activate"||key==="documents.embed"){
  if(qa.mode==="owned")throw {source:"jarvis",message:"Model file missing: {0}",values:[qa.rawPath]};
  if(qa.mode==="raw")throw "Model file missing: {0}";
  if(qa.mode==="pending")return new Promise(resolve=>{qa.resolve=resolve;});
  if(key==="models.activate")return {running:true};
  return {model:qa.rawModel,dimensions:384,input_tokens:12};
 }
 if(key==="embedding.settings")return {backend:"auto"};
 if(key==="documents.search")return {items:[{id:"owned-doc",name:qa.rawFile,characters:12,indexed:true,citation:{source_kind:"workspace"}}]};
 if(key==="documents.refresh")return {changed:true};
 throw new Error(`Unexpected fixture call: ${key}`);
}}});
const inherited=["constructor","toString","__proto__"];
createRoot(document.getElementById("root")!).render(<main><section aria-label="models"><ModelLibrary /></section><section aria-label="attachments"><SharedAttachments /></section>{inherited.map(name=><section key={name} aria-label={name}><AiToolApproval toolName="jarvis_action" part={{type:"tool-jarvis_action",toolCallId:name,state:"approval-requested",input:{name,args:{path:qa.rawPath}},approval:{id:name}}} onRespond={approved=>qa.responses.push(approved)} /></section>)}</main>);
