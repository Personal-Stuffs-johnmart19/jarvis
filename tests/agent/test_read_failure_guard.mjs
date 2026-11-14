import test from "node:test";
import assert from "node:assert/strict";
import {createReadFailureGuard} from "../../apps/desktop/workbench/src/modules/ai/lib/readFailureGuard.ts";
for(const [toolName,output] of [["jarvis_catalog",{items:[]}],["mcp_catalog",{servers:[]}],["utility_catalog",{error:"unavailable"}]])
test(`${toolName} repeated empty discovery produces a final-answer boundary`,()=>{
 const guard=createReadFailureGuard(),part={type:"tool-result",toolName,input:{query:"weather"},output};
 guard.observe([part]);assert.equal(guard.limited,false);
 guard.observe([part]);assert.equal(guard.limited,true);
});
const failure=(path)=>({type:"tool-result",toolName:"read_file",input:{path},output:{error:"not found"}});
test("permits a corrected path, then successful reading resets lookup budget",()=>{
 const g=createReadFailureGuard();g.observe([failure("wrong")]);assert.equal(g.limited,false);
 g.observe([{...failure("right"),output:{content:"actual file"}}]);
 g.observe([failure("wrong")]);assert.equal(g.limited,false);
});
test("same failing lookup ends tools after one retry despite reordered arguments",()=>{
 const g=createReadFailureGuard();g.observe([{...failure("x"),input:{path:"x",limit:10}}]);
 g.observe([{...failure("x"),input:{limit:10,path:"x"}}]);assert.equal(g.limited,true);
});
test("different failed paths and empty search churn share a four-result budget",()=>{
 const g=createReadFailureGuard();g.observe([failure("a"),failure("b"),{type:"tool-result",toolName:"glob",input:{pattern:"**/x"},output:{paths:[]}}]);assert.equal(g.limited,false);
 g.observe([{type:"tool-error",toolName:"list_directory",input:{path:"c"}}]);assert.equal(g.limited,true);
});
test("does not reinterpret denied mutations or unrelated tool data, and resets each turn",()=>{
 const g=createReadFailureGuard();g.observe(Array.from({length:8},()=>({type:"tool-result",toolName:"jarvis_action",output:{error:"denied"}})));assert.equal(g.limited,false);
 g.observe([failure("a"),failure("a")]);assert.equal(g.limited,true);assert.equal(createReadFailureGuard().limited,false);
});

const shared=(part)=>({...part,toolName:"jarvis_action",input:{name:part.toolName,args:part.input}});
for(const [name,input,output] of [
 ["read_file",{path:"missing.xml"},{error:"not found",path:"missing.xml"}],
 ["list_directory",{path:"missing"},{error:"not found",path:"missing"}],
 ["grep",{pattern:"missing",root:"owned"},{hits:[],root:"owned"}],
 ["glob",{pattern:"**/missing",root:"owned"},{hits:[],root:"owned"}],
])test(`shared ${name} failures use the same bounded lookup budget`,()=>{
 const g=createReadFailureGuard();
 const part={type:"tool-result",toolName:name,input,output};
 g.observe([shared(part)]);assert.equal(g.limited,false);
 g.observe([shared(part)]);assert.equal(g.limited,true);
});

test("alternating shared and direct reads share canonical arguments and ignore stripped keys",()=>{
 for(const reverse of [false,true]){
  const g=createReadFailureGuard();
  const direct={...failure("x"),input:{path:"x",limit:10}};
  const alias=shared({...direct,input:{limit:10,path:"x",ignored:"stripped by Zod"}});
  g.observe([reverse?alias:direct]);assert.equal(g.limited,false);
  g.observe([reverse?direct:alias]);assert.equal(g.limited,true);
 }
});

test("successful shared or direct lookup resets the other route's failure budget",()=>{
 for(const reverse of [false,true]){
  const g=createReadFailureGuard();
  const fail=failure("x");
  const success={...failure("right"),output:{path:"right",content:"actual file"}};
  g.observe([reverse?shared(fail):fail]);
  g.observe([reverse?success:shared(success)]);
  g.observe([reverse?fail:shared(fail)]);assert.equal(g.limited,false);
 }
});

test("shared tool-error retains SDK input and counts as the same direct failure",()=>{
 const g=createReadFailureGuard();
 g.observe([shared({type:"tool-error",toolName:"read_file",input:{path:"x"},error:new Error("native read failed")})]);
 g.observe([failure("x")]);assert.equal(g.limited,true);
});

test("malformed aliases, chains, unrelated actions and denied calls neither consume nor reset read budget",()=>{
 const ignored=[
  {name:"read_file",args:null},{name:"read_file",args:[]},{name:"read_file",args:"x"},
  {name:"read_file",args:{}},{name:"read_file",args:{path:1}},{name:"read_file",args:{path:"x",offset:-1}},
  {name:"read_file",args:{path:"x",limit:"ten"}}, {name:"grep",args:{pattern:"x",glob:"*.txt"}},
  {name:"glob",args:{pattern:"x",root:[]}}, {name:"glob",args:{pattern:"x",max_results:2001}},
  {name:"grep",args:{pattern:"x",case_insensitive:"yes"}},
  {name:"jarvis_action",args:{name:"read_file",args:{path:"x"}}},
  {name:"write_file",args:{path:"x",content:"owned"}}, {name:"file_controller",args:{action:"write",path:"x"}},
  {name:"unknown",args:{}}, null, [],
 ];
 const g=createReadFailureGuard();g.observe([failure("x")]);
 for(const input of ignored)for(let i=0;i<4;i++)g.observe([{type:"tool-result",toolName:"jarvis_action",input,output:{error:"denied"}}]);
 const denied={...shared(failure("x")),type:"tool-output-denied"};
 g.observe([denied,denied]);assert.equal(g.limited,false);
 g.observe([failure("x")]);assert.equal(g.limited,true);
});

test("unchanged cached reads stop after two markers across shared and direct routes without altering output",()=>{
 for(const routes of [[false,false],[false,true],[true,false],[true,true]]){
  const g=createReadFailureGuard();
  const output=Object.freeze({path:"x",unchanged:true,size:42});
  const part={type:"tool-result",toolName:"read_file",input:{path:"x"},output};
  g.observe([routes[0]?shared(part):part]);assert.equal(g.limited,false);
  g.observe([routes[1]?shared(part):part]);assert.equal(g.limited,true);
  assert.deepEqual(output,{path:"x",unchanged:true,size:42});
 }
});

test("unchanged marker then actual read content resets the lookup budget",()=>{
 for(const reverse of [false,true]){
  const g=createReadFailureGuard();
  const marker={...failure("x"),output:{path:"x",unchanged:true,size:42}};
  const content={...failure("x"),output:{path:"x",content:"actual saved file",size:42}};
  g.observe([reverse?shared(marker):marker]);
  g.observe([reverse?content:shared(content)]);
  g.observe([reverse?marker:shared(marker)]);assert.equal(g.limited,false);
  g.observe([reverse?shared(marker):marker]);assert.equal(g.limited,true);
 }
});
