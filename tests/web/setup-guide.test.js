const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs');
function load(saved,cfg={}) {
 const store={value:saved,get(){return this.value},set(key,value){this.value=JSON.parse(JSON.stringify(value));}};
 const s={pub:cfg,guideOpen:true};
 const ctx={window:{CP:{S:s,store,esc:s=>String(s),render(){},toast(){}}},document:{querySelector(){return null}}};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync('app/web/setup-guide.js','utf8'),ctx);
 return {guide:ctx.window.SetupGuide,s,store,click:async(action)=>ctx.window.SetupGuide.onClick({target:{closest(){return {dataset:{guideAction:action}};}},preventDefault(){}})};
}
test('configured means enabled, destination present and local credentials filled',()=>{
 const {guide}=load();
 const rows=guide.status({modules:{discord:false},discord:{enabled:true,webhooks:[{}]},telegram:{enabled:true,has_token:true,channels:[]},bluesky:{enabled:true},bluesky_session:{ok:false}});
 assert.equal(rows.find(p=>p.key==='discord').ready,false);
 assert.equal(rows.find(p=>p.key==='telegram').ready,false);
 assert.equal(rows.find(p=>p.key==='bluesky').ready,false);
});
test('an existing connected platform is selected, and guide navigation persists',async()=>{
 const {s,store,click}=load(undefined,{discord:{enabled:true,webhooks:[{}]}});
 await click('next');
 assert.equal(s.setupGuide.selected[0],'discord');assert.equal(store.value.step,1);
 await click('skip');assert.equal(store.value.step,2);assert.equal(store.value.skipped[0],1);
});
test('restarting the guide keeps platform choices and does not edit settings',async()=>{
 const cfg={discord:{enabled:true,webhooks:[{}]}};
 const before=JSON.stringify(cfg);
 const {s,store,click}=load({step:3,selected:['discord'],skipped:[2],done:true},cfg);
 await click('restart');
 assert.equal(store.value.step,0);assert.equal(store.value.done,false);assert.equal(store.value.selected[0],'discord');
 assert.equal(JSON.stringify(s.pub),before);
});
test('saved progress is sanitized',()=>{
 const {guide,s}=load({step:99,selected:['discord','untrusted'],skipped:['0',99],done:true});
 guide.render();assert.equal(s.setupGuide.step,3);assert.equal(s.setupGuide.selected.length,1);assert.equal(s.setupGuide.skipped.length,0);
});
