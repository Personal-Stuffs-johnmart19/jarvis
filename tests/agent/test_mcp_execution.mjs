import test from 'node:test';
import assert from 'node:assert/strict';
import { createMcpExecutor, mcpCatalogue, ensureMcpConnected, mcpModelOutput, revokeMcpReceipts, createMcpOperation } from '../../apps/desktop/workbench/src/modules/mcp/client.ts';
import { runCancellableProcess } from '../../apps/desktop/workbench/src/modules/ai/lib/processExecution.ts';

const input = () => ({server_id: 'files', tool_name: 'echo', arguments: {text: 'hello'}});
function fixture(policy = 'ask') {
  const calls = [];
  const info = {server_id: 'files', name: 'echo', description: 'fixture', input_schema: {type: 'object'}, policy, config_revision: 'original-revision', connection_generation: 'original-connection'};
  const invoke = async (command, args) => {
    calls.push({command, args});
    if (command === 'mcp_tool_info') return {...info};
    if (command === 'mcp_config_get') return {version: 1, servers: [{id: 'files', name: 'My Files'}, {id: 'disabled', name: 'Hidden'}]};
    if (command === 'mcp_status') return [{id: 'files', enabled: true, connected: false}, {id: 'disabled', enabled: false}];
    if (command === 'mcp_call_tool') return {content: [{type: 'text', text: args.arguments.text}]};
    if (command === 'mcp_tools') return {items: [info], total: 1, next_cursor: null};
    return undefined;
  };
  const receipts = new Map();
  return {calls, info, invoke, receipts, executor: createMcpExecutor(invoke, () => 'chat-a', runCancellableProcess, receipts)};
}

test('catalogue lists enabled integrations without starting a process, then discovers one explicit server', async () => {
  const f = fixture();
  const list = await mcpCatalogue(f.invoke, {});
  assert.deepEqual(list.servers.map(s => s.id), ['files']);
  assert.equal(list.servers[0].name, 'My Files');
  assert.ok(!f.calls.some(c => c.command === 'mcp_connect'));
  const page = await mcpCatalogue(f.invoke, {server_id: 'files', include_parameters: true});
  assert.equal(page.items[0].name, 'echo');
  assert.equal(f.calls.find(c => c.command === 'mcp_tools').args.includeParameters, true);
});

test('concurrent discovery coalesces an in-flight server connection', async () => {
  let resolve;
  let connections = 0;
  const invoke = async () => { connections++; return new Promise(done => {resolve = done;}); };
  const a = ensureMcpConnected(invoke, 'same');
  const b = ensureMcpConnected(invoke, 'same');
  assert.equal(connections, 1);
  resolve([]);
  await Promise.all([a, b]);
});

test('ask receipt survives a toolset rebuild and executes once with captured revision', async () => {
  const f = fixture(); const value = input();
  assert.equal(await f.executor.prepare(value, 'call-1'), true);
  const rebuilt = createMcpExecutor(f.invoke, () => 'chat-a', runCancellableProcess, f.receipts);
  const result = await rebuilt.execute(value, 'call-1');
  assert.equal(result.content[0].text, 'hello');
  const native = f.calls.find(c => c.command === 'mcp_call_tool').args;
  assert.equal(native.approved, true);
  assert.equal(native.configRevision, 'original-revision');
  assert.match(native.requestId, /^[a-f0-9]{32}$/);
  assert.equal((await rebuilt.execute(value, 'call-1')).ok, false);
  assert.equal(f.calls.filter(c => c.command === 'mcp_call_tool').length, 1);
});

test('allow and deny policies do not create unnecessary confirmation or bypass denial', async () => {
  const allowed = fixture('allow');
  assert.equal(await allowed.executor.prepare(input(), 'allow'), false);
  await allowed.executor.execute(input(), 'allow');
  assert.equal(allowed.calls.find(c => c.command === 'mcp_call_tool').args.approved, false);
  const denied = fixture('deny');
  assert.equal(await denied.executor.prepare(input(), 'deny'), false);
  assert.match((await denied.executor.execute(input(), 'deny')).error, /denied/);
  assert.ok(!denied.calls.some(c => c.command === 'mcp_call_tool'));
});

test('changed arguments and different chat sessions cannot consume an approval', async () => {
  const f = fixture(); const original = input();
  await f.executor.prepare(original, 'same');
  original.arguments.text = 'changed';
  assert.equal((await f.executor.execute(original, 'same')).ok, false);
  await f.executor.prepare(input(), 'session');
  const other = createMcpExecutor(f.invoke, () => 'chat-b', runCancellableProcess, f.receipts);
  assert.equal((await other.execute(input(), 'session')).ok, false);
  assert.ok(!f.calls.some(c => c.command === 'mcp_call_tool'));
});

test('configuration changes never replace a captured approval revision', async () => {
  const f = fixture(); await f.executor.prepare(input(), 'revision');
  const originalInvoke = f.invoke;
  const changedInvoke = async (command, args) => {
    if (command === 'mcp_call_tool') { assert.equal(args.configRevision, 'original-revision'); throw new Error('settings changed; request approval again'); }
    return originalInvoke(command, args);
  };
  const executor = createMcpExecutor(changedInvoke, () => 'chat-a', runCancellableProcess, f.receipts);
  const result = await executor.execute(input(), 'revision');
  assert.equal(result.ok, false);
  assert.match(result.error, /settings changed/);
});

test('Stop forwards the request ID to native cancellation and rejects a late response', async () => {
  const f = fixture(); let finish;
  const invoke = async (command, args) => {
    if (command === 'mcp_call_tool') { f.calls.push({command, args}); return new Promise(resolve => {finish = resolve;}); }
    return f.invoke(command, args);
  };
  const executor = createMcpExecutor(invoke, () => 'chat-a', runCancellableProcess, new Map());
  await executor.prepare(input(), 'stop');
  const controller = new AbortController();
  const pending = executor.execute(input(), 'stop', controller.signal);
  await Promise.resolve(); controller.abort(); finish({content: [{type: 'text', text: 'too late'}]});
  await assert.rejects(pending, /abort/i);
  const run = f.calls.find(c => c.command === 'mcp_call_tool');
  const cancel = f.calls.find(c => c.command === 'mcp_cancel');
  assert.equal(cancel.args.requestId, run.args.requestId);
});

test('already aborted work does not issue a tool call', async () => {
  const f = fixture(); await f.executor.prepare(input(), 'pre-abort');
  const controller = new AbortController(); controller.abort();
  await assert.rejects(f.executor.execute(input(), 'pre-abort', controller.signal), /abort/i);
  assert.ok(!f.calls.some(c => c.command === 'mcp_call_tool'));
});

test('model projection bounds text, preserves images, and leaves the full saved result intact', () => {
  const raw = {content: [{type: 'text', text: 'x'.repeat(30000)}, {type: 'image', mimeType: 'image/png', data: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1S8AAAAASUVORK5CYII='}]};
  const projection = mcpModelOutput(raw, 'outer-call');
  assert.equal(projection.type, 'content');
  assert.ok(projection.value.filter(p => p.type === 'text').reduce((n, p) => n + p.text.length, 0) < 13000);
  assert.ok(projection.value.some(p => p.type === 'image-data' && p.mediaType === 'image/png'));
  assert.ok(projection.value.some(p => p.type === 'text' && p.text.includes('tool_call_id')));
  assert.equal(raw.content[0].text.length, 30000);
});

// Load the real shared executor with only native IPC stubbed; no Tauri process or model API.
import { registerHooks } from 'node:module';
import { existsSync } from 'node:fs';
registerHooks({ resolve(specifier, context, next) {
  if (specifier === '@/host/core') return {url: 'data:text/javascript,export const invoke=(command,args)=>globalThis.__mcpTestInvoke(command,args);', shortCircuit: true};
  if (specifier.startsWith('.') && context.parentURL?.endsWith('.ts')) {
    const url = new URL(specifier + '.ts', context.parentURL);
    if (existsSync(url)) return {url: url.href, shortCircuit: true};
  }
  return next(specifier, context);
}});

test('real jarvis_action delegates MCP policy, execution and multimodal output through shared receipts', async () => {
  const f = fixture();
  globalThis.__mcpTestInvoke = async (command, args) => command === 'backend' ? {} : f.invoke(command, args);
  const {buildMcpTools} = await import('../../apps/desktop/workbench/src/modules/ai/tools/mcp.ts');
  const {buildJarvisTools, revokePendingActions} = await import('../../apps/desktop/workbench/src/modules/ai/tools/jarvis.ts');
  const mcp = buildMcpTools({getSessionId: () => 'integration'});
  const shared = buildJarvisTools(mcp);
  const options = {toolCallId: 'shared-mcp-call', messages: []};
  const request = {name: 'mcp_call', args: input()};
  assert.equal(await shared.jarvis_action.needsApproval(request, options), true);
  const output = await shared.jarvis_action.execute(request, options);
  assert.equal(output.content[0].text, 'hello');
  const projection = await shared.jarvis_action.toModelOutput({input: request, output, toolCallId: options.toolCallId});
  assert.equal(projection.type, 'content');
  assert.ok(projection.value.some(p => p.type === 'text' && p.text.includes('hello')));
  assert.equal(f.calls.filter(c => c.command === 'mcp_call_tool').length, 1);
  revokePendingActions(); revokeMcpReceipts(); delete globalThis.__mcpTestInvoke;
});

test('model projection rejects malformed base64 and bounds combined images', () => {
  const image = {type: 'image', mimeType: 'image/png', data: 'A'.repeat(3 * 1024 * 1024)};
  const output = mcpModelOutput({content: [image, image, {...image, data: 'invalid-base64'}]}, 'bounded');
  assert.equal(output.value.filter(part => part.type === 'image-data').length, 1);
});

test('child MCP handoff executes only through the existing parent approval receipt', async () => {
 const f=fixture('ask');
 globalThis.__mcpTestInvoke=async(command,args)=>command==='backend'?{}:f.invoke(command,args);
 const {childCapabilityTools}=await import('../../apps/desktop/workbench/src/modules/ai/agents/childCapabilities.ts');
 const {buildMcpTools}=await import('../../apps/desktop/workbench/src/modules/ai/tools/mcp.ts');
 const {buildJarvisTools,revokePendingActions}=await import('../../apps/desktop/workbench/src/modules/ai/tools/jarvis.ts');
 const parent=buildJarvisTools(buildMcpTools({getSessionId:()=> 'child-owner'}),false);
 const handoffs=[];const child=childCapabilityTools(parent,handoffs);
 const deferred=await child.jarvis_action.execute({name:'mcp_call',args:input()},{toolCallId:'child-write',messages:[]});
 assert.equal(deferred.child_tool_call_id,'child-write');assert.equal(deferred.status,'requires_parent_approval');
 assert.equal(f.calls.filter(c=>c.command==='mcp_call_tool').length,0);
 assert.equal(f.calls.filter(c=>c.command==='mcp_tool_info').length,0);
 const request={name:deferred.name,args:deferred.args};const options={toolCallId:'parent-approved-child-write',messages:[]};
 assert.equal(await parent.jarvis_action.needsApproval(request,options),true);
 assert.equal(f.calls.filter(c=>c.command==='mcp_call_tool').length,0);
 const result=await parent.jarvis_action.execute(request,options);
 assert.equal(result.content[0].text,'hello');
 await assert.rejects(parent.jarvis_action.execute(request,options),/Expired action/);
 assert.equal(f.calls.filter(c=>c.command==='mcp_call_tool').length,1);
 revokePendingActions();revokeMcpReceipts();delete globalThis.__mcpTestInvoke;
});

test('denied child handoff cannot bypass configured MCP policy through the parent',async()=>{
 const f=fixture('deny');globalThis.__mcpTestInvoke=async(command,args)=>command==='backend'?{}:f.invoke(command,args);
 const {childCapabilityTools}=await import('../../apps/desktop/workbench/src/modules/ai/agents/childCapabilities.ts');
 const {buildMcpTools}=await import('../../apps/desktop/workbench/src/modules/ai/tools/mcp.ts');
 const {buildJarvisTools,revokePendingActions}=await import('../../apps/desktop/workbench/src/modules/ai/tools/jarvis.ts');
 const parent=buildJarvisTools(buildMcpTools({getSessionId:()=> 'denied-child-owner'}),false);
 const deferred=await childCapabilityTools(parent,[]).jarvis_action.execute({name:'mcp_call',args:input()},{toolCallId:'denied-child',messages:[]});
 const request={name:deferred.name,args:deferred.args},options={toolCallId:'parent-denied-child',messages:[]};
 assert.equal(await parent.jarvis_action.needsApproval(request,options),false);
 assert.match((await parent.jarvis_action.execute(request,options)).error,/denied/);
 assert.ok(!f.calls.some(c=>c.command==='mcp_call_tool'));
 revokePendingActions();revokeMcpReceipts();delete globalThis.__mcpTestInvoke;
});

test('MCP preparation cannot transfer receipts across an awaited session switch', async () => {
  for (const stage of ['mcp_connect', 'mcp_tool_info']) {
    const f = fixture(); let session = 'original'; let release;
    const invoke = async (command, args) => {
      if (command === stage) await new Promise(resolve => { release = resolve; });
      return f.invoke(command, args);
    };
    const executor = createMcpExecutor(invoke, () => session, runCancellableProcess, f.receipts);
    const pending = executor.prepare(input(), 'switch');
    while (!release) await new Promise(resolve => setImmediate(resolve));
    session = 'replacement'; release();
    assert.equal(await pending, false);
    assert.equal(f.receipts.size, 0);
    assert.equal((await executor.execute(input(), 'switch')).ok, false);
    session = 'original';
    assert.equal((await executor.execute(input(), 'switch')).ok, false);
    assert.ok(!f.calls.some(call => call.command === 'mcp_call_tool'));
  }
});

test('MCP receipt revocation during preparation cannot recreate an approval', async () => {
  const f = fixture(); let release;
  const invoke = async (command, args) => {
    if (command === 'mcp_tool_info') await new Promise(resolve => { release = resolve; });
    return f.invoke(command, args);
  };
  const executor = createMcpExecutor(invoke, () => 'original', runCancellableProcess, f.receipts);
  const pending = executor.prepare(input(), 'revoked');
  while (!release) await new Promise(resolve => setImmediate(resolve));
  revokeMcpReceipts(); release();
  assert.equal(await pending, false);
  assert.equal(f.receipts.size, 0);
  assert.equal((await executor.execute(input(), 'revoked')).ok, false);
});

test('MCP execution pins connection generation and consumes stale approval without reconnect', async () => {
  const f = fixture(); let generation = 'original-connection';
  const invoke = async (command, args) => {
    if (command === 'mcp_call_tool') {
      assert.equal(args.connectionGeneration, 'original-connection');
      if (args.connectionGeneration !== generation) throw new Error('MCP connection changed; request approval again');
    }
    return f.invoke(command, args);
  };
  const executor = createMcpExecutor(invoke, () => 'original', runCancellableProcess, f.receipts);
  await executor.prepare(input(), 'old-connection');
  const connects = f.calls.filter(call => call.command === 'mcp_connect').length;
  generation = 'replacement-connection';
  assert.match((await executor.execute(input(), 'old-connection')).error, /connection changed/);
  assert.equal((await executor.execute(input(), 'old-connection')).ok, false);
  assert.equal(f.calls.filter(call => call.command === 'mcp_connect').length, connects);
  assert.ok(!f.calls.some(call => call.command === 'mcp_call_tool'));
});


test('private MCP operation binds execution and cleanup to an opaque context', async () => {
  const f = fixture(); const operation = createMcpOperation(f.invoke);
  assert.deepEqual(Reflect.ownKeys(operation.context), []);
  await f.executor.prepare(input(), 'private');
  await f.executor.execute(input(), 'private', undefined, operation.context);
  const call = f.calls.find(call => call.command === 'mcp_call_tool');
  assert.match(call.args.cleanupNonce, /^[a-f0-9]{32}$/);
  assert.notEqual(call.args.requestId, call.args.cleanupNonce);
  assert.equal(await operation.settle(), false, 'undefined legacy cancellation is never cleanup proof');
  const probe = f.calls.find(call => call.command === 'mcp_cancel');
  assert.equal(probe.args.requestId, call.args.requestId);
  assert.equal(probe.args.cleanupNonce, call.args.cleanupNonce);
  await f.executor.prepare(input(), 'reused');
  assert.match((await f.executor.execute(input(), 'reused', undefined, operation.context)).error, /reused/);
  assert.equal(f.calls.filter(call => call.command === 'mcp_call_tool').length, 1);
});

test('cleanup probe propagates failure and accepts only explicit settlement', async () => {
  let response = {settled: false};
  const operation = createMcpOperation(async () => {
    if (response instanceof Error) throw response;
    return response;
  });
  assert.equal(await operation.settle(), false);
  response = {settled: 'true'};
  assert.equal(await operation.settle(), false);
  response = new Error('seal capacity reached');
  await assert.rejects(operation.settle(), /capacity/);
  response = {settled: true};
  assert.equal(await operation.settle(), true);
});

test('an opaque operation cannot cross native clients or be forged through context fields', async () => {
  const f = fixture();
  const operation = createMcpOperation(async () => undefined);
  await f.executor.prepare(input(), 'wrong-client');
  assert.match((await f.executor.execute(input(), 'wrong-client', undefined, operation.context)).error, /another client/);
  assert.ok(!f.calls.some(call => call.command === 'mcp_call_tool'));
  await f.executor.prepare(input(), 'forged-context');
  await f.executor.execute(input(), 'forged-context', undefined,
    {requestId: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', cleanupNonce: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'});
  const call = f.calls.find(call => call.command === 'mcp_call_tool');
  assert.notEqual(call.args.requestId, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
  assert.equal(call.args.cleanupNonce, undefined);
});

test('MCP preparation lookup reads policy once and cannot recreate consumed receipts', async () => {
  for (const policy of ['ask', 'allow', 'deny']) {
    const f = fixture(policy);
    assert.equal(f.executor.preparation('peek'), 'unavailable');
    await f.executor.prepare(input(), 'peek');
    const calls = f.calls.length;
    assert.equal(f.executor.preparation('peek'), policy === 'deny' ? 'unavailable' : policy);
    assert.equal(f.executor.preparation('peek'), policy === 'deny' ? 'unavailable' : policy);
    assert.equal(f.calls.length, calls);
    await f.executor.execute(input(), 'peek');
    assert.equal(f.executor.preparation('peek'), 'unavailable');
  }
  const expired = fixture();
  await expired.executor.prepare(input(), 'old');
  for (const receipt of expired.receipts.values()) receipt.expires = Date.now() - 1;
  assert.equal(expired.executor.preparation('old'), 'unavailable');
});

test('backend-bound MCP cleanup identity is validated and captured before mutation', async () => {
  const identity = {requestId: 'a'.repeat(32), cleanupNonce: 'b'.repeat(32)};
  const calls = [];
  const operation = createMcpOperation(async (command, args) => {
    calls.push({command, args}); return {settled: true};
  }, identity);
  identity.requestId = 'c'.repeat(32); identity.cleanupNonce = 'd'.repeat(32);
  assert.equal(await operation.settle(), true);
  assert.deepEqual(calls[0], {command: 'mcp_cancel', args: {
    requestId: 'a'.repeat(32), cleanupNonce: 'b'.repeat(32),
  }});
  for (const value of ['', 'x'.repeat(32), 'A'.repeat(32), 'a'.repeat(33)]) {
    assert.throws(() => createMcpOperation(async () => undefined,
      {requestId: value, cleanupNonce: 'b'.repeat(32)}), /Invalid/);
    assert.throws(() => createMcpOperation(async () => undefined,
      {requestId: 'a'.repeat(32), cleanupNonce: value}), /Invalid/);
  }
});

test('MCP receipt revocation releases capacity without executing or affecting another call', async () => {
  const f = fixture();
  await f.executor.prepare(input(), 'denied');
  await f.executor.prepare(input(), 'retained');
  const calls = f.calls.length;
  f.executor.revoke('denied');
  f.executor.revoke('denied');
  assert.equal(f.receipts.size, 1);
  assert.equal(f.executor.preparation('denied'), 'unavailable');
  assert.equal(f.executor.preparation('retained'), 'ask');
  assert.equal(f.calls.length, calls);
  assert.equal((await f.executor.execute(input(), 'denied')).ok, false);
  assert.ok(!f.calls.some(call => call.command === 'mcp_call_tool'));
});
