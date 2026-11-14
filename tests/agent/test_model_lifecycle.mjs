import test from 'node:test';
import assert from 'node:assert/strict';
import { unloadLocalModel } from '../../apps/desktop/workbench/src/modules/ai/lib/modelLifecycle.ts';

test('LM Studio unload addresses only unique loaded instance IDs, never model keys or other models', async () => {
  const calls=[];
  const fetcher=async (url,options)=>{calls.push({url,body:JSON.parse(options.body)});return new Response('{}');};
  await unloadLocalModel('lmstudio','http://localhost:1234/v1/','downloaded/model',['instance-1','instance-1','instance-2'],fetcher);
  assert.deepEqual(calls,[{url:'http://localhost:1234/api/v1/models/unload',body:{instance_id:'instance-1'}},{url:'http://localhost:1234/api/v1/models/unload',body:{instance_id:'instance-2'}}]);
  calls.length=0;
  await unloadLocalModel('lmstudio','http://localhost:1234/v1','downloaded/model',[],fetcher);
  assert.equal(calls.length,0);
});

test('Ollama unload sends empty non-streaming prompt and keep_alive zero, preserves configured endpoint prefix', async () => {
  let call;
  await unloadLocalModel('ollama','http://localhost:11434/server/v1','qwen:latest',[],async (url,options)=>{call={url,body:JSON.parse(options.body)};return new Response('{}');});
  assert.deepEqual(call,{url:'http://localhost:11434/server/api/generate',body:{model:'qwen:latest',prompt:'',keep_alive:0,stream:false}});
});

test('unload rejects malformed targets and stops on errors or aborted requests', async () => {
  let calls=0;
  const fetcher=async()=>{calls++;return new Response('denied',{status:403});};
  await assert.rejects(unloadLocalModel('lmstudio','file:///tmp/model','m',['i'],fetcher),/Invalid/);
  await assert.rejects(unloadLocalModel('lmstudio','http://u:p@localhost','m',['i'],fetcher),/Invalid/);
  assert.equal(calls,0);
  await assert.rejects(unloadLocalModel('lmstudio','http://localhost','m',['first','second'],fetcher),/HTTP 403/);
  assert.equal(calls,1);
  const controller=new AbortController();controller.abort();
  await assert.rejects(unloadLocalModel('ollama','http://localhost','m',[],async(_url,options)=>{options.signal.throwIfAborted();return new Response('{}');},controller.signal),/abort/i);
});

test('pending unload confirmation cannot outlive the provider endpoint or owning view', async () => {
  let accept;
  const confirmation=new Promise(resolve=>{accept=resolve;});
  const controller=new AbortController();
  let calls=0;
  const operation=unloadLocalModel('lmstudio','http://localhost:1234','m',['instance'],async()=>{calls++;return new Response('{}');},controller.signal,()=>confirmation);
  controller.abort();
  accept(true);
  await assert.rejects(operation,/abort/i);
  assert.equal(calls,0);
  await unloadLocalModel('ollama','http://localhost:11434','m',[],async()=>{calls++;return new Response('{}');},undefined,async()=>false);
  assert.equal(calls,0);
});
