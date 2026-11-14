// Explicit local-model smoke; browser/CLI side effects are simulated, never dispatched.
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire, registerHooks } from 'node:module';
import { performance } from 'node:perf_hooks';
import { activeAgentToolNames, JARVIS_SERVICE_INSTRUCTIONS } from '../apps/desktop/workbench/src/modules/ai/lib/serviceRouting.ts';
import { selectSystemPrompt } from '../apps/desktop/workbench/src/modules/ai/config.ts';

const offlineFixture = process.argv.includes('--offline-fixture');
if (offlineFixture === process.argv.includes('--local-model')) throw new Error('Choose --offline-fixture (no inference) or explicit --local-model (real local inference calls).');
const require = createRequire(new URL('../apps/desktop/package.json', import.meta.url));
registerHooks({resolve(specifier, context, next) {
  if (specifier === '@/host/core') return {url:'data:text/javascript,export const invoke=(command,args)=>globalThis.__routingSmokeInvoke(command,args);', shortCircuit:true};
  if (specifier.startsWith('.') && context.parentURL?.endsWith('.ts')) {
    const url = new URL(specifier + '.ts', context.parentURL);
    if (existsSync(url)) return {url:url.href, shortCircuit:true};
  }
  return next(specifier, context);
}});
const { generateText, isStepCount, tool } = require('ai');
const { createOpenAICompatible } = require('@ai-sdk/openai-compatible');
const { z } = require('zod');
const { buildJarvisTools, revokePendingActions } = await import('../apps/desktop/workbench/src/modules/ai/tools/jarvis.ts');
const { buildBrowserTools } = await import('../apps/desktop/workbench/src/modules/ai/tools/browser.ts');
const declarations = JSON.parse(readFileSync(new URL('../local_mode/tool_declarations.json', import.meta.url), 'utf8'));
const items = declarations.map(item => ({...item, category:'tools', executor:'python', approval:'dynamic', available:true}));
const calls = [], receipts = new Map();
let selectedVideo = false, observedVideo = false, playbackVerified = false, requests = 0;
globalThis.__routingSmokeInvoke = async (command, request) => {
  assert.equal(command, 'backend');
  if (request.method === 'services.catalog') return {items, next_cursor:null};
  if (request.method === 'agent.prepare') {
    assert.equal(request.params.name, 'browser_control', 'Browser task diverted to another executor');
    const token = crypto.randomUUID(); receipts.set(token, request.params.args);
    return {token, requires_approval:false};
  }
  if (request.method !== 'agent.execute') throw new Error(`Unexpected IPC: ${request.method}`);
  const args = receipts.get(request.params.token); receipts.delete(request.params.token);
  assert.ok(args, 'Missing single-use fixture receipt'); calls.push(args);
  assert.notEqual(args.action, 'list_profiles', 'Routine browsing must use the saved profile without discovery');
  if (args.action === 'music_play') {
    const query = args.query === undefined ? 'music' : args.query;
    assert.ok(typeof query === 'string' && query.trim() && query.length <= 200, 'Invalid music query');
    selectedVideo = playbackVerified = true;
    return {ok:true, verified:true, simulated:true, query:query.trim(), media:{url:'https://www.youtube.com/watch?v=fixtureSong', paused:false, muted:false, current_time:5}};
  }
  if (args.action === 'media_play' || args.action === 'media_state') {
    assert.ok(selectedVideo, 'Playback requested without selecting an observed video');
    playbackVerified = true;
    return {ok:true, verified:true, simulated:true, media:{paused:false, muted:false, current_time:5}};
  }
  if (args.action === 'go_to' && args.url === 'https://www.youtube.com/watch?v=fixtureSong') {
    assert.ok(observedVideo, 'Video URL must come from an observed result'); selectedVideo = true;
  }
  if (args.action === 'click') { assert.ok(observedVideo, 'Video ref must come from an observed result'); assert.equal(args.ref, 'song1'); selectedVideo = true; }
  assert.ok(['go_to','search','snapshot','get_text','click'].includes(args.action), `Unexpected browser action: ${args.action}`);
  observedVideo = true;
  return {ok:true, simulated:true, url:selectedVideo ? 'https://www.youtube.com/watch?v=fixtureSong' : 'https://www.youtube.com/results?search_query=music', elements:[{ref:'song1', role:'link', name:'Fixture music video', url:'https://www.youtube.com/watch?v=fixtureSong'}], untrusted_content:true};
};
const generic = Object.fromEntries(['utility_catalog','utility_run','run_code'].map(name => [name, tool({description:'Generic CLI execution', inputSchema:z.object({}), execute:() => {throw new Error('CLI execution must not be used');}})]));
const shared = buildJarvisTools({}, false);
const tools = {...generic, ...shared, ...buildBrowserTools(shared.jarvis_action)};
const model = offlineFixture ? undefined : createOpenAICompatible({name:'lmstudio', baseURL:'http://127.0.0.1:1234/v1', includeUsage:true, fetch:async (url, init) => {
  const body = JSON.parse(init.body);
  assert.ok(body.tools.every(item => !Object.hasOwn(generic, item.function.name)), 'Generic CLI schemas leaked into model request');
  requests++; return fetch(url, init);
}})('gpt-oss-20b');
const started = performance.now();
try {
  if (offlineFixture) {
    const dispatch = async args => {
      const receipt = await globalThis.__routingSmokeInvoke('backend', {method:'agent.prepare',params:{name:'browser_control',args}});
      return globalThis.__routingSmokeInvoke('backend', {method:'agent.execute',params:{token:receipt.token}});
    };
    await assert.rejects(dispatch({action:'media_play'}), /observed video/);
    await assert.rejects(dispatch({action:'go_to',url:'https://www.youtube.com/watch?v=fixtureSong'}), /observed result/);
    for (const query of [null, '', 'x'.repeat(201)]) await assert.rejects(dispatch({action:'music_play',query}), /Invalid music query/);
    for (const args of [{action:'music_play'},{action:'music_play',query:'instrumental music'}]) {
      const result = await dispatch(args);
      assert.equal(result.query, args.query ?? 'music');
      assert.equal(result.verified, true);
    }
    selectedVideo = observedVideo = playbackVerified = false;
    await dispatch({action:'go_to',url:'https://www.youtube.com/results?search_query=music'});
    await dispatch({action:'click',ref:'song1'});
    assert.equal((await dispatch({action:'media_play'})).verified, true);
    assert.equal(requests, 0);
    console.log(JSON.stringify({scope:'offline synthetic browser routing fixture; no inference/browser/audio', explicit_music_play:true, manual_observed_video:true, verified:playbackVerified, requests}));
  } else {
  const result = await generateText({model, instructions:selectSystemPrompt('lmstudio-local') + JARVIS_SERVICE_INSTRUCTIONS,
    prompt:'<env>workspace_root: D:/QA; active_terminal_cwd: D:/QA</env>\nPlay music on YouTube.',
    tools, activeTools:activeAgentToolNames(tools), stopWhen:isStepCount(4), maxOutputTokens:1024,
    abortSignal:AbortSignal.timeout(90000)});
  assert.ok(requests <= 4, 'Routine playback used unnecessary discovery/model requests');
  assert.ok(calls.some(call => ['music_play','media_play','media_state'].includes(call.action)) && playbackVerified, 'Model did not complete verified browser playback routing');
  console.log(JSON.stringify({scope:'real LM Studio inference; simulated browser/CLI IPC; no personal Chrome or audio', model:'gpt-oss-20b', requests, browser_actions:calls.map(call => call.action), elapsed_ms:Math.round(performance.now()-started), usage:result.totalUsage}));
  }
} finally { revokePendingActions(); delete globalThis.__routingSmokeInvoke; }
