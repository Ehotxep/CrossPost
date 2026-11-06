const {test}=require('node:test');
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function load(){const ctx={window:{addEventListener(){}},document:{addEventListener(){}}};vm.createContext(ctx);vm.runInContext(fs.readFileSync('app/web/settings-autosave.js','utf8'),ctx);return ctx.window.SettingsAutosave;}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('rapid edits merge nested patches without dropping other languages',async()=>{
 const calls=[],{SaveQueue}=load();const queue=new SaveQueue(async(key,patch)=>calls.push({key,patch}));
 queue.edit('signatures',{platforms:{telegram:{sfw:'A'}}});
 queue.edit('signatures',{platforms:{telegram:{en:{sfw:'English'}}}});
 queue.edit('signatures',{platforms:{telegram:{sfw:'B'}}});await queue.flush();
 assert.equal(calls.length,1);assert.equal(calls[0].patch.platforms.telegram.sfw,'B');assert.equal(calls[0].patch.platforms.telegram.en.sfw,'English');
});
test('writes stay sequential and edits arriving during a write are saved afterwards',async()=>{
 const calls=[],waiting=[],{SaveQueue}=load();const queue=new SaveQueue((key,patch)=>new Promise(resolve=>{calls.push(patch.sfw);waiting.push(resolve);}));
 queue.edit('signatures',{sfw:'old'},{text:{value:'old'}});const done=queue.flush();
 queue.edit('signatures',{sfw:'new'},{text:{value:'new'}});assert.equal(calls.length,1);
 waiting.shift()();await tick();assert.equal(calls[1],'new');
 waiting.shift()();await done;assert.equal(queue.snapshot().length,0);
});
test('failed writes remain available for retry with their input values',async()=>{
 let fail=true;const {SaveQueue}=load();const queue=new SaveQueue(async()=>{if(fail)throw Error('offline');});
 queue.edit('light',{quality:80},{lq:{value:'80'}});assert.equal(await queue.flush(),false);
 assert.equal(queue.snapshot()[0].values.lq.value,'80');assert.equal(queue.errors.get('light'),'offline');
 fail=false;assert.equal(await queue.flush(),true);assert.equal(queue.errors.size,0);
});
test('one failed section does not lose changes in another section',async()=>{
 const saved=[],{SaveQueue}=load();const queue=new SaveQueue(async(key)=>{if(key==='light')throw Error('offline');saved.push(key);});
 queue.edit('light',{quality:80});queue.edit('hashtags',{constant:['art']});await queue.flush();
 assert.equal(saved[0],'hashtags');assert.equal(queue.snapshot().length,1);assert.equal(queue.snapshot()[0].key,'light');
});
test('credentials, account names and folder moves are not autosave fields',()=>{
 const {describe}=load();
 for(const id of ['trkey','bspass','bottok','dasecret','e6key','vktok','apiid','apihash','fdir','fmove'])assert.equal(describe({id,value:'secret',checked:true}),null,id);
});
