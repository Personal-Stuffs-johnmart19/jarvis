import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire,registerHooks } from 'node:module';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const require=createRequire(new URL('../../apps/desktop/package.json',import.meta.url));
const {transformWithOxc}=await import(pathToFileURL(require.resolve('vite')).href);
const componentUrl=new URL('../../apps/desktop/workbench/src/modules/ai/components/PlanDiffReview.tsx',import.meta.url);
const transformed=await transformWithOxc(readFileSync(componentUrl,'utf8'),componentUrl.pathname,{jsx:{runtime:'automatic'}});
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const reactPaths=Object.fromEntries(['react','react/jsx-runtime'].map(name=>[name,pathToFileURL(require.resolve(name)).href]));
const mocks={
  '@/components/ui/button':'import React from "react";export const Button=({children,size,variant,...props})=>React.createElement("button",props,children);',
  '@/lib/utils':'export const cn=(...values)=>values.filter(Boolean).join(" ");',
  '@hugeicons/core-free-icons':'export const ArrowDown01Icon={},Cancel01Icon={},FileEditIcon={},FilePlusIcon={},FolderAddIcon={},Tick02Icon={};',
  '@hugeicons/react':'export const HugeiconsIcon=()=>null;',
  '../store/planStore':'export const usePlanStore=selector=>selector(globalThis.__planReviewState);',
  '../../../../../src/i18n':'export const useLocale=()=>{};',
};
const hook=registerHooks({resolve(specifier,context,nextResolve){
  if(context.parentURL?.includes("/node_modules/"))return nextResolve(specifier,context);
  if(specifier in reactPaths)return nextResolve(reactPaths[specifier],context);
  if(specifier in mocks)return {url:`data:text/javascript,${encodeURIComponent(mocks[specifier])}`,shortCircuit:true};
  if(['../../../../../src/displayError','../../../../../src/localization','./localization'].includes(specifier))return nextResolve(specifier+'.ts',context);
  return nextResolve(specifier,context);
},load(url,context,nextLoad){
  if(url.endsWith('/PlanDiffReview.tsx'))return {format:'module',source:transformed.code,shortCircuit:true};
  return nextLoad(url,context);
}});
const {PlanDiffReview}=await import('../../apps/desktop/workbench/src/modules/ai/components/PlanDiffReview.tsx');
hook.deregister();
function render(locale,failure,applying=false){
  globalThis.localStorage={getItem:()=>locale};
  globalThis.__planReviewState={queue:[{id:'owned',kind:'edit',path:'/owned/raw-name.txt',originalContent:'old',proposedContent:'new',isNewFile:false,workspace:{kind:'local'},failure}],removeOne:()=>{},clear:()=>{},applyAll:async()=>[],applying};
  return renderToStaticMarkup(React.createElement(PlanDiffReview));
}
test('failed retained proposal has a visible live-localized alert with bounded raw detail',()=>{
  const failure={source:'jarvis',message:'Could not apply this change: {0}',values:['RAW owned native error']};
  const english=render('en',failure),russian=render('ru',failure);
  assert.match(english,/role="alert"/);assert.match(english,/Could not apply this change: RAW owned native error/);
  assert.match(russian,/role="alert"/);assert.doesNotMatch(russian,/Could not apply this change:/);assert.match(russian,/RAW owned native error/);
  for(const html of [english,russian])assert.match(html,/raw-name\.txt/);
});
test('conflict remains visible while preview collapsed and Apply reflects global busy state',()=>{
  const html=render('en',{source:'jarvis',message:'File changed since this proposal was prepared. Review it again before applying.',values:[]},true);
  assert.match(html,/role="alert"/);assert.match(html,/File changed since this proposal was prepared/);
  assert.match(html,/<button[^>]*disabled=""[^>]*>Apply /);
  assert.match(html,/<button(?![^>]*disabled)[^>]*>Discard all/);
});
test('clearing a running plan keeps the honest settlement notice visible with no proposals',()=>{
  render('en',undefined,true);
  globalThis.__planReviewState.queue=[];
  globalThis.__planReviewState.applyNotice={source:'jarvis',message:'Plan application stopped. Changes already being written may still finish.',values:[]};
  const html=renderToStaticMarkup(React.createElement(PlanDiffReview));
  assert.match(html,/role="status"/);assert.match(html,/Changes already being written may still finish/);
  assert.match(html,/<button[^>]*disabled=""[^>]*>Apply /);
});
