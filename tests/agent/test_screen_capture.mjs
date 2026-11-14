import test from 'node:test';
import assert from 'node:assert/strict';
import { ScreenCaptureCache, localCaptureBase, restrictScreenHistory, SCREEN_DESTINATION } from '../../apps/desktop/workbench/src/modules/ai/lib/screenCapture.ts';
const image={image:'aW1hZ2U=',monitor:1,image_width:640,image_height:360,screen_left:-1280,screen_top:0,screen_width:1280,screen_height:720};
test('capture destination is local LM Studio only, with canonical pinned IP',()=>{
 assert.equal(localCaptureBase('lmstudio'),'http://127.0.0.1:1234/v1');
 for(const [provider,url] of [['google',SCREEN_DESTINATION],['lmstudio','https://127.0.0.1:1234/v1'],['lmstudio','http://example.com:1234/v1'],['lmstudio','http://127.0.0.1:1235/v1'],['lmstudio','http://user:secret@127.0.0.1:1234/v1'],['lmstudio','http://127.0.0.1:1234/proxy'],['lmstudio','http://127.0.0.1:1234/v1?redirect=cloud']]) assert.equal(localCaptureBase(provider,url),null);
});
test('saved results contain no image; destination, session, expiry and cache bounds are enforced',()=>{
 const expirations=[];
 const cache=new ScreenCaptureCache(expire=>{expirations.push(expire);return()=>{};});
 const ref=cache.put('session',image);
 assert.ok(!JSON.stringify(ref).includes(image.image));
 assert.equal(ref.destination,SCREEN_DESTINATION);
 assert.equal(cache.output(ref,'session',true).value[1].type,'image-data');
 assert.equal(cache.output(ref,'other',true).type,'text');
 assert.equal(cache.output(ref,'session',false).type,'text');
 expirations[0]();
 assert.equal(cache.output(ref,'session',true).type,'text');
 const old=cache.put('session',image);
 for(let n=0;n<4;n++)cache.put('session',image);
 assert.equal(cache.output(old,'session',true).type,'text');
});
test('cloud projection strips old capture results without touching manual images or other tools',()=>{
 const messages=[{role:'tool',content:[{type:'tool-result',toolName:'screen_capture',toolCallId:'capture',output:{type:'content',value:[{type:'image-data',data:image.image,mediaType:'image/jpeg'}]}}]},{role:'user',content:[{type:'image',image:'manual attachment'}]}];
 assert.ok(!JSON.stringify(restrictScreenHistory(messages,false)).includes(image.image));
 assert.equal(restrictScreenHistory(messages,false)[1],messages[1]);
 assert.equal(restrictScreenHistory(messages,true),messages);
});

// Exercise the installed SDK converter: saved references hydrate only for the local run.
const { createRequire } = await import('node:module');
const { convertToModelMessages } = createRequire(new URL('../../apps/desktop/package.json', import.meta.url))('ai');
test('SDK replay hydrates local references and withholds images for cloud or expired sessions',async()=>{
 const cache=new ScreenCaptureCache(()=>()=>{});
 const ref=cache.put('local-chat',image);
 const messages=[{role:'assistant',parts:[{type:'tool-screen_capture',toolCallId:'capture',state:'output-available',input:{destination:SCREEN_DESTINATION,monitor:1},output:ref}]}];
 const saved=JSON.stringify(messages);
 for(const allowed of [true,false]) {
  const tools={screen_capture:{toModelOutput:({output})=>cache.output(output,'local-chat',allowed)}};
  const converted=await convertToModelMessages(messages,{tools});
  assert.equal(JSON.stringify(converted).includes(image.image),allowed);
 }
 cache.remove(ref.capture_id);
 const expired=await convertToModelMessages(messages,{tools:{screen_capture:{toModelOutput:({output})=>cache.output(output,'local-chat',true)}}});
 assert.ok(!JSON.stringify(expired).includes(image.image));
 assert.equal(JSON.stringify(messages),saved);
 assert.ok(!saved.includes(image.image));
});
