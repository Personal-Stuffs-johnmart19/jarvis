export function useComposer() {
 const qa=(window as any).__liveFeedbackQA;
 return {files:qa.upload,isBusy:false,removeFile:()=>{},addFiles:async()=>{},setValue:()=>{},textareaRef:{current:null}};
}
