const {test}=require('node:test');
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function load(observe=false){
  let clock=0,timer;
  const ctx={window:{CP:{S:{view:'stats'}}},document:{addEventListener(){}},performance:{now:()=>clock},
    setTimeout:fn=>{timer=fn;return 1;},clearTimeout:()=>{timer=null;}};
  const callbacks={};
  if(observe){
    class Observer{constructor(callback){this.callback=callback;}observe({type}){callbacks[type]=this.callback;}}
    Observer.supportedEntryTypes=['resource','longtask'];ctx.PerformanceObserver=ctx.window.PerformanceObserver=Observer;
    ctx.URL=URL;ctx.location={href:'http://localhost/',origin:'http://localhost'};
  }
  vm.createContext(ctx);vm.runInContext(fs.readFileSync('app/web/diagnostics.js','utf8'),ctx);
  return {api:ctx.window.PerfUI,time:n=>clock=n,settle:()=>timer?.(),observe:(kind,entries)=>callbacks[kind]({getEntries:()=>entries})};
}
test('waiting requests remain visible and finished requests record no payloads',()=>{
  const {api,time}=load();const request=api.request('stats_overview');time(5000);
  assert.equal(api.snapshot()[0].pending,true);assert.equal(api.snapshot()[0].ms,5000);
  request.finish();request.finish();assert.equal(api.snapshot().length,1);
  assert.equal(api.snapshot()[0].view,'stats');assert.equal(api.snapshot()[0].pending,undefined);
  assert.match(api.snapshot()[0].id,/^[a-f0-9]{8}-\d+$/);
});
test('navigation waits for related requests and rendering, without counting quiet timeout',()=>{
  const {api,time,settle}=load();api.begin('stats');time(10);const request=api.request('stats_overview');
  time(300);settle();assert.equal(api.snapshot().filter(r=>r.kind==='navigation').length,0);
  request.finish();time(310);const rendered=api.rendering('stats');time(320);rendered();time(1000);settle();
  const nav=api.snapshot().find(r=>r.kind==='navigation');assert.equal(nav.ms,320);assert.equal(nav.ok,true);
});
test('switching away records unfinished navigation and buffer stays bounded',()=>{
  const {api,time}=load();api.begin('stats');time(20);api.begin('post');
  assert.equal(api.snapshot()[0].ok,false);
  for(let i=0;i<1200;i++)api.request('info').finish();
  assert.equal(api.snapshot().length,1000);
});

test('thumbnail timing strips URLs and tokens; long tasks store only the view',()=>{
  const {api,observe}=load(true);
  observe('resource',[{name:'http://localhost/pthumb/42?t=SECRET',duration:250},
    {name:'https://external.invalid/PRIVATE',duration:900}]);
  observe('longtask',[{duration:80,attribution:[{name:'PRIVATE TITLE'}]}]);
  const rows=api.snapshot();assert.equal(rows.length,2);assert.equal(rows[0].name,'thumbnail');
  assert.equal(rows[1].kind,'longtask');assert.equal(rows[1].name,'stats');
  assert.ok(!JSON.stringify(rows).includes('SECRET'));assert.ok(!JSON.stringify(rows).includes('PRIVATE'));
});
