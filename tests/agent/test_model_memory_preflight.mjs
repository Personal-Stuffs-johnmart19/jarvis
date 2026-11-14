import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {inspectTargetDisk,memoryGuidance} from '../../apps/desktop/workbench/src/settings/lib/modelLibrary.ts';

const component=readFileSync(new URL('../../apps/desktop/workbench/src/settings/components/ModelLibrary.tsx',import.meta.url),'utf8');

test('same pinned bundle preflight carries actual bytes and per-adapter unknown free VRAM',async()=>{
  const hardware={selected_file_bytes:6*1024**3,planning_bytes:7.5*1024**3,ram:{name:'Physical RAM',total_bytes:16*1024**3,available_bytes:7*1024**3,status:'limited_headroom'},gpus:[{name:'AMD Vulkan adapter',total_bytes:4*1024**3,available_bytes:null,status:'files_exceed_capacity'}],gpu_telemetry:'dedicated_capacity_only'};
  const info={repo_id:'org/model-999B-name-is-not-memory',revision:'a'.repeat(40),files:[{path:'actual.gguf',size:6*1024**3}],total_size:6*1024**3,gated:false};
  const variant={kind:'llm',descriptorId:'llm-gguf',files:['actual.gguf']};
  const value=await inspectTargetDisk(info,variant,{invoke:async(command,args)=>{
    assert.equal(command,'model_disk_preflight');
    assert.deepEqual(args,{info,bundle:{kind:'llm',descriptor_id:'llm-gguf',files:['actual.gguf']}});
    return {required_bytes:6*1024**3+1024**2,available_bytes:20*1024**3,resumable_bytes:0,published_sizes_known:true,sufficient:true,hardware};
  }});
  assert.equal(value.hardware,hardware);
  assert.equal(value.hardware.gpus[0].available_bytes,null);
  assert.equal(value.sufficient,true,'GPU capacity warning must not change disk/download admission');
});

test('memory guidance distinguishes capacity-only, unknown and advisory headroom without guaranteeing fit',()=>{
  assert.match(memoryGuidance('planning_headroom'),/not guaranteed/);
  assert.match(memoryGuidance('capacity_only'),/available memory.*unknown/);
  assert.equal(memoryGuidance('unsupported_future_probe'),'Unknown');
  for(const locale of ['en','ru']) {
    const catalog=JSON.parse(readFileSync(new URL(`../../apps/desktop/src/locales/${locale}.json`,import.meta.url),'utf8'));
    for(const status of ['unknown','files_exceed_capacity','files_exceed_available','limited_headroom','planning_headroom','capacity_only'])assert.ok(catalog[memoryGuidance(status)]);
  }
});

test('UI source contract keeps RAM/VRAM advisory and states missing telemetry and KV/cache limitations',()=>{
  assert.match(component,/disabled=\{downloading \|\| !selectedVariant \|\| !disk\?\.sufficient\}/);
  assert.doesNotMatch(component,/disabled=\{[^}\n]*hardware/);
  assert.match(component,/This does not mean no GPU is installed/);
  assert.match(component,/Context length, KV cache, compute buffers/);
  assert.match(component,/CPU execution or partial offload may work/);
  assert.match(component,/\{gpu\.name\}/,'Hardware adapter names are literal data');
});
