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
