import {compactModelMessagesDetailed} from '../apps/desktop/workbench/src/modules/ai/lib/compact.ts';
const messages=[{role:'user',content:'Keep the requested task and never repeat approved side effects.'}];
for(let i=0;i<18;i++) messages.push({role:'assistant',content:[{type:'tool-call',toolCallId:`c${i}`,toolName:'utility_run',input:{tool:'git',args:['status']}}]},{role:'tool',content:[{type:'tool-result',toolCallId:`c${i}`,toolName:'utility_run',output:{type:'json',value:{exit_code:i===17?1:0,stdout:'Repeated build log line.\n'.repeat(400),stderr:i===17?'LATEST_FAILURE_DETAIL':''}}}]});
messages.push({role:'user',content:'Fix the last failure; retain my instructions.'});
const result=compactModelMessagesDetailed(messages,8192,{reservedTokens:2500});
console.log(JSON.stringify({before_bytes:Buffer.byteLength(JSON.stringify(messages)),after_bytes:Buffer.byteLength(JSON.stringify(result.messages)),estimated_history_tokens:result.estimatedTokensAfter,history_budget:result.historyBudgetTokens,over_budget:result.overBudget}));
