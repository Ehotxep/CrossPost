const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs');
function context(){
 const pending=[];
 const state={pub:{modules:{translation:false,targeting:false}},draft:{files:[],title:'A',text:'A',rating:'sfw',signature:false,exclude:[],variants:{en:{text:'English'}},multi:true,marks:{file:'en'}}};
 const ctx={window:{CP:{S:state,api:(name,args)=>new Promise((resolve,reject)=>pending.push({name,args,resolve,reject})),store:{set(){}},render(){}}},document:{addEventListener(){},getElementById(){return null},querySelectorAll(){return []}},setTimeout(){return 1},clearTimeout(){}};
 vm.createContext(ctx);
 for(const file of ['post-state.js','publish.js'])vm.runInContext(fs.readFileSync('app/web/'+file,'utf8'),ctx);
 return {ctx,state,pending};
}
test('a saved draft keeps hidden translations and targeting, as an independent snapshot',()=>{
 const {ctx,state}=context();
 const doc=ctx.window.PostState.capture(state.draft);
 assert.equal(doc.variants.en.text,'English');assert.equal(doc.multi,true);assert.equal(doc.marks.file,'en');
 state.draft.variants.en.text='Changed';assert.equal(doc.variants.en.text,'English');
});
test('older plan response cannot replace newer plan',async()=>{
 const {ctx,state,pending}=context();
 const change=checked=>ctx.window.Pub.onChange({target:{dataset:{platformEnable:'discord'},checked}});
 const old=change(true);state.draft.text='B';const current=change(false);
 pending[1].resolve({title:'B'});await current;pending[0].resolve({title:'A'});await old;
 assert.equal(state.plan.title,'B');
});
test('an error for an old draft cannot erase a current plan',async()=>{
 const {ctx,state,pending}=context();
 const change=checked=>ctx.window.Pub.onChange({target:{dataset:{platformEnable:'discord'},checked}});
 const old=change(true);state.draft.text='B';const current=change(false);
 pending[1].resolve({title:'B'});await current;pending[0].reject(Error('old failure'));await old;
 assert.equal(state.plan.title,'B');assert.equal(state.planErr,'');
});

test('copying booru tags merges matching files without mutating source or metadata',()=>{
 const {ctx}=context();
 const d={files:[{id:'a'},{id:'b'}],booru_posts:{
  e6ai:{tags:{a:['female','wolf','wolf'],b:['armor']},meta:{a:{ok:'source-confirmation',parent:'9'}}},
  e621:{tags:{a:['solo','female'],b:['forest'],stale:['keep']},meta:{a:{ok:'old',parent:'42',nochar:false},b:{ok:'old'}}},
  floofgens:{tags:{a:['untouched']},meta:{a:{ok:'third'}}}}};
 const before=JSON.stringify(d);
 const r=ctx.window.PostState.copyBooruTags(d,'e6ai','e621');
 assert.equal(r.copied,2);assert.equal(r.skipped,0);
 assert.deepEqual(Array.from(r.posts.e621.tags.a),['solo','female','wolf']);
 assert.deepEqual(Array.from(r.posts.e621.tags.b),['forest','armor']);
 assert.equal(r.posts.e621.meta.a.ok,'');assert.equal(r.posts.e621.meta.a.parent,'42');
 assert.equal(r.posts.e621.meta.a.nochar,false);assert.equal(r.posts.e6ai.meta.a.ok,'source-confirmation');
 assert.equal(r.posts.floofgens.meta.a.ok,'third');assert.equal(JSON.stringify(d),before);
 r.posts.e621.tags.a.push('new');assert.equal(r.posts.e6ai.tags.a.includes('new'),false);
});
test('replace affects only the selected file and skips absent or empty source tags',()=>{
 const {ctx}=context();
 const d={files:['a','b','c'],booru_posts:{floofgens:{tags:{a:['male'],b:[]},meta:{}},e621:{tags:{a:['female'],b:['keep'],c:['also_keep']},meta:{b:{ok:'still-confirmed'}}}}};
 const r=ctx.window.PostState.copyBooruTags(d,'floofgens','e621',{mode:'replace',file:'a'});
 assert.deepEqual(Array.from(r.posts.e621.tags.a),['male']);assert.equal(r.posts.e621.meta.a.ok,'');
 assert.deepEqual(Array.from(r.posts.e621.tags.b),['keep']);assert.equal(r.posts.e621.meta.b.ok,'still-confirmed');
 const all=ctx.window.PostState.copyBooruTags(d,'floofgens','e621',{mode:'replace'});
 assert.equal(all.copied,1);assert.equal(all.skipped,2);
 assert.deepEqual(Array.from(all.posts.e621.tags.c),['also_keep']);
});
test('all six site directions produce independent tags which survive draft capture',()=>{
 const {ctx}=context();const api=ctx.window.PostState;
 for(const source of ['e6ai','e621','floofgens'])for(const target of ['e6ai','e621','floofgens']){
  if(source===target)continue;
  const d={files:[{id:'a'}],booru_posts:{[source]:{tags:{a:['wolf']},meta:{a:{ok:'source'}}}}};
  const r=api.copyBooruTags(d,source,target);
  d.booru_posts=r.posts;const captured=api.capture(d);
  assert.equal(captured.booru_posts[target].tags.a[0],'wolf');
  assert.equal(captured.booru_posts[target].meta.a.ok,'');
  r.posts[target].tags.a.push('armor');assert.equal(captured.booru_posts[target].tags.a.length,1);
 }
});
test('copy rejects invalid directions and modes without changing the draft',()=>{
 const {ctx}=context(),d={files:[],booru_posts:{}};
 for(const [source,target,mode] of [['e6ai','e6ai','merge'],['unknown','e621','merge'],['e621','floofgens','invalid']]){
  assert.throws(()=>ctx.window.PostState.copyBooruTags(d,source,target,{mode}));
 }
 assert.equal(JSON.stringify(d),'{"files":[],"booru_posts":{}}');
});
test('file reordering uses insertion boundaries in both directions',()=>{
 const {ctx}=context(),reorder=ctx.window.PostState.reorderFiles,files=['a','b','c','d'];
 assert.deepEqual(Array.from(reorder(files,0,2)),['b','a','c','d']); // a after b
 assert.deepEqual(Array.from(reorder(files,0,4)),['b','c','d','a']); // a to the end
 assert.deepEqual(Array.from(reorder(files,3,1)),['a','d','b','c']); // d before b
 assert.deepEqual(Array.from(reorder(files,2,3)),files);            // c after itself
 assert.deepEqual(Array.from(files),['a','b','c','d']);             // source is untouched
});
