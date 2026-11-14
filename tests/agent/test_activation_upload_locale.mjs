import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createRequire, registerHooks, stripTypeScriptTypes } from 'node:module';
import { t, setLocale } from '../../apps/desktop/src/localization.ts';
import { activateModel } from '../../apps/desktop/workbench/src/settings/lib/modelLibrary.ts';

// Use the production host adapter; replace only the native transport.
const nativeUrl = 'data:text/javascript,' + encodeURIComponent(`export const invoke=(...args)=>globalThis.__localeTransport(...args); export class Channel {}; export const transformCallback=()=>0,convertFileSrc=x=>x,isTauri=()=>false,addPluginListener=()=>{};export class Resource {}`);
const hook = registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === '@tauri-apps/api/core') return { url: nativeUrl, shortCircuit: true };
  if (specifier === '../../../src/displayError') return nextResolve(new URL('../../apps/desktop/src/displayError.ts', import.meta.url).href, context);
  if (specifier === './localization' && context.parentURL?.endsWith('/displayError.ts')) return nextResolve('./localization.ts', context);
  return nextResolve(specifier, context);
} });
const { invoke, invokeOwned } = await import('../../apps/desktop/workbench/src/host/core.ts');
const { displayError, errorText } = await import('../../apps/desktop/src/displayError.ts');
hook.deregister();
const values = new Map();
globalThis.localStorage = { getItem:key=>values.get(key), setItem:(key,value)=>values.set(key,value) };
globalThis.window = { dispatchEvent() {} };
window.parent = window;
const require = createRequire(new URL('../../tools/dev/package.json', import.meta.url));
const { parse } = require('@babel/parser');
function handler(file, name, context) {
  const source = fs.readFileSync(new URL(file, import.meta.url), 'utf8');
  let found;
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'VariableDeclarator' && node.id.name === name) found ??= node.init;
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object') visit(value);
    }
  }
  visit(parse(source, { sourceType:'module', plugins:['typescript','jsx'] }));
  assert.ok(found, `Production handler ${name}`);
  return vm.runInNewContext(stripTypeScriptTypes(`(${source.slice(found.start,found.end)})`), context);
}

function activationText(activationStatus, locale) {
  const source=fs.readFileSync(new URL('../../apps/desktop/workbench/src/settings/components/ModelLibrary.tsx',import.meta.url),'utf8');
  let found;
  function visit(node) {
    if (!node || typeof node!=='object') return;
    if (node.type==='JSXExpressionContainer' && node.expression?.type==='ConditionalExpression'
      && node.expression.test?.type==='BinaryExpression' && node.expression.test.operator==='in'
      && node.expression.test.right?.name==='activationStatus') found=node.expression;
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value==='object') visit(value);
    }
  }
  visit(parse(source,{sourceType:'module',plugins:['typescript','jsx']}));
  assert.ok(found,'Production activation feedback render');
  setLocale(locale);
  return vm.runInNewContext(stripTypeScriptTypes(`(${source.slice(found.start,found.end)})`),{activationStatus,locale,t,errorText});
}

const changedUpload = {source:'jarvis',message:'Uploaded file changed; upload it again before indexing',values:[]};
const missingPath = {source:'jarvis',message:'Path does not exist or cannot be resolved: {0}',values:['C:\\User\\literal {0} модель.gguf']};

test('production model activation consumer displays localized detail with raw path; raw collision stays raw', async () => {
  for (const locale of ['en','ru','en']) {
    setLocale(locale);
    for (const failure of [missingPath, missingPath.message]) {
      let status, active;
      globalThis.__localeTransport = async (command, args) => {
        assert.equal(command,'backend');
        assert.equal(args.method,'models.activate');
        assert.equal(args.params.repo_id,'fixture/model');
        throw failure;
      };
      const activate = handler('../../apps/desktop/workbench/src/settings/components/ModelLibrary.tsx','handleActivate',{
        t, String, displayError, feedbackDeps: {invoke:invokeOwned}, activationMode:'vulkan',
        activateModel,
        setActivatingKey:value=>{active=value}, setActivationStatus:value=>{status=value},
      });
      await activate({kind:'llm',repo_id:'fixture/model',revision:'r1'});
      assert.equal(active,null);
      status = activationText(status, locale);
      if (typeof failure === 'string') assert.ok(status.includes(failure),status);
      else {
        assert.ok(status.includes(failure.values[0]),status);
        assert.ok(status.includes(locale==='ru'?'Путь не существует':'Path does not exist'),status);
      }
      assert.ok(!status.includes('{{error}}'),status);
    }
  }
});

test('production uploaded-attachment consumer displays owned rejection and preserves raw provider collision', async () => {
  for (const locale of ['en','ru','en']) {
    setLocale(locale);
    for (const failure of [changedUpload, changedUpload.message]) {
      let status, busy;
      const calls=[];
      globalThis.__localeTransport=async (command,args)=>{
        calls.push(args.method);
        if (args.method === 'embedding.settings') return {};
        throw failure;
      };
      const embed=handler('../../apps/desktop/workbench/src/modules/ai/components/SharedAttachments.tsx','embed',{
        files:[{path:'C:\\synthetic.txt',name:'User title {0}',documentId:'synthetic-id'}],mode:'auto',
        setBusy:value=>{busy=value},setResult:value=>{status=value},
        rpc:(method,params)=>invokeOwned('backend',{method,params}),String,displayError,
      });
      await embed();
      assert.deepEqual(calls,['embedding.settings','documents.embed']);
      assert.equal(busy,false);
      assert.equal(errorText(status[0],locale),typeof failure==='string'?failure:locale==='ru'?'Загруженный файл изменился; загрузите его ещё раз перед индексированием':changedUpload.message);
    }
  }
});

test('production activation success/progress interpolates raw repository identity in both locales', async () => {
  const repo='fixture/literal {0}';
  for (const locale of ['en','ru']) {
    setLocale(locale);
    for (const running of [false,true]) {
      const statuses=[];
      let finish;
      const waiting=new Promise(resolve=>{finish=resolve});
      const activate=handler('../../apps/desktop/workbench/src/settings/components/ModelLibrary.tsx','handleActivate',{
        t, String, displayError, feedbackDeps: {invoke:invokeOwned}, activationMode:'vulkan',activateModel:()=>waiting,
        setActivatingKey:()=>{},setActivationStatus:value=>statuses.push(value),
      });
      const result=activate({kind:'llm',repo_id:repo,revision:'r1'});
      assert.equal(activationText(statuses[0],locale),locale==='ru'?'Активация модели…':'Activating model…');
      finish({running});
      await result;
      assert.equal(statuses.length,2);
      // Render the same captured success state in both locales without another activation.
      assert.ok(activationText(statuses[1],'en').includes(repo));
      assert.ok(activationText(statuses[1],'ru').includes(repo));
      statuses[1] = activationText(statuses[1],locale);
      assert.ok(statuses[1].includes(repo));
      assert.equal(statuses[1], running
        ? locale==='ru'?`Модель «${repo}» успешно активирована.`:`Activated '${repo}' successfully.`
        : locale==='ru'?`Активация настроена: ${repo}`:`Activation configured: ${repo}`);
    }
  }
});

test('production download confirmation interpolates pinned values and refusal starts no download', async () => {
  for (const locale of ['en','ru']) {
    setLocale(locale);
    let prompt;
    const start=handler('../../apps/desktop/workbench/src/settings/components/ModelLibrary.tsx','handleStartDownload',{
      t,repoInfo:{repo_id:'fixture/model',revision:'literal {0} revision'},
      variants:[{files:['raw.gguf'],hasUnknownSize:false,totalBytes:7}],selectedVariantIndex:0,
      formatBytes:()=> '7 B',confirmAction:async text=>{prompt=text;return false},
    });
    await start();
    assert.ok(prompt.includes('literal {0} revision'),prompt);
    assert.ok(prompt.includes('7 B'),prompt);
    assert.ok(prompt.startsWith(locale==='ru'?'Подтвердить загрузку 1':'Confirm download of 1'),prompt);
    assert.ok(!prompt.includes('{{'),prompt);
  }
});


test('opt-in invoke preserves provenance while legacy invoke and raw collisions remain compatible', async () => {
  globalThis.__localeTransport=async()=>{throw missingPath};
  setLocale('ru');
  await assert.rejects(invoke('backend'),error=>typeof error==='string' && error.includes('Путь не существует'));
  await assert.rejects(invokeOwned('backend'),error=>error===missingPath);
  globalThis.__localeTransport=async()=>{throw missingPath.message};
  for (const call of [invoke,invokeOwned]) await assert.rejects(call('backend'),error=>error===missingPath.message);
});

test('already-visible activation failure state renders live locale without another call or raw text translation', async () => {
  let status,calls=0;
  const activate=handler('../../apps/desktop/workbench/src/settings/components/ModelLibrary.tsx','handleActivate',{
    displayError,feedbackDeps:{invoke:invokeOwned},activationMode:'vulkan',
    activateModel:async()=>{calls++;throw missingPath},setActivatingKey:()=>{},setActivationStatus:value=>{status=value},
  });
  await activate({kind:'llm',repo_id:'fixture/model',revision:'r1'});
  for (const locale of ['en','ru','en']) {
    assert.ok(activationText(status,locale).includes(locale==='ru'?'Путь не существует':'Path does not exist'));
    assert.ok(activationText(status,locale).includes(missingPath.values[0]));
  }
  assert.equal(calls,1);
  status={error:missingPath.message};
  assert.ok(activationText(status,'ru').endsWith(missingPath.message));
});

test('stored attachment success keeps raw file/model values and translates units without another RPC', async () => {
  let result;
  const calls=[];
  const name='Write file {0}.txt',model='Run shell command {1}';
  const embed=handler('../../apps/desktop/workbench/src/modules/ai/components/SharedAttachments.tsx','embed',{
    files:[{path:'C:/synthetic.txt',name,documentId:'synthetic-id'}],mode:'auto',displayError,
    setBusy:()=>{},setResult:value=>{result=value},
    rpc:async(method)=>{calls.push(method);return method==='embedding.settings'?{}:{model,dimensions:3,input_tokens:null}},
  });
  await embed();
  assert.equal(result.length,1);
  for (const locale of ['en','ru','en']) {
    const text=result.map(item=>errorText(item,locale)).join('\n');
    assert.ok(text.startsWith(`${name}: ${model}`),text);
    assert.ok(text.endsWith(locale==='ru'?'3 измерений · ? токенов':'3 dimensions · ? tokens'),text);
  }
  assert.deepEqual(calls,['embedding.settings','documents.embed']);
});
