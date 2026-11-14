import test from 'node:test';
import assert from 'node:assert/strict';
import {compileServer, serverDraft, replaceServer, canStopMcpServer} from '../../apps/desktop/workbench/src/modules/mcp/settings.ts';

test('MCP Stop remains usable while its own handshake is pending', () => {
  const status = {id:'fixture', enabled:true, connected:false, connecting:true, tool_count:0};
  assert.equal(canStopMcpServer('fixture', 'fixture', status), true);
  assert.equal(canStopMcpServer('fixture', 'other-server', status), false);
  assert.equal(canStopMcpServer('fixture', '', {...status, connecting:false}), false);
  assert.equal(canStopMcpServer('fixture', '', {...status, connecting:false, connected:true}), true);
});

test('MCP configuration stores references separately from credential values', () => {
  const draft = serverDraft();
  draft.command = 'python';
  draft.secrets = [{name: 'API_KEY', value: 'private-value', reference: {service: 'app.jarvis.workbench', account: 'google'}}];
  const {server, writes} = compileServer(draft);
  assert.ok(!JSON.stringify(server).includes('private-value'));
  assert.equal(writes[0].value, 'private-value');
  assert.notEqual(writes[0].account, 'google');
  assert.equal(server.transport.secret_env.API_KEY.account, writes[0].account);
});

test('MCP editing retains saved references without reading credentials', () => {
  const draft = serverDraft();
  const reference = {service: 'app.jarvis.workbench', account: 'saved'};
  draft.secrets = [{name: 'API_KEY', value: '', reference}];
  const compiled = compileServer(draft);
  assert.deepEqual(compiled.writes, []);
  assert.deepEqual(compiled.server.transport.secret_env.API_KEY, reference);
});

test('MCP headers are case-insensitive and credentials require unique names', () => {
  const draft = serverDraft(); draft.type = 'http';
  draft.secrets = [{name: 'Authorization', value: 'a'}, {name: ' authorization ', value: 'b'}];
  assert.throws(() => compileServer(draft), /unique/);
  draft.secrets = [{name: 'API_KEY', value: ''}];
  assert.throws(() => compileServer(draft), /Enter a credential/);
});

test('MCP rejects malformed args and policies without modifying other integrations', () => {
  const draft = serverDraft(); draft.args = '[1]';
  assert.throws(() => compileServer(draft), /strings/);
  draft.args = '[]'; draft.policies = '{"delete":"yes"}';
  assert.throws(() => compileServer(draft), /ask, allow or deny/);
  draft.policies = '{}';
  const server = compileServer(draft).server;
  const other = {...server, id: 'other'};
  const config = {version: 1, servers: [other]};
  assert.deepEqual(replaceServer(config, server).servers, [other, server]);
  assert.deepEqual(config.servers, [other]);
});
