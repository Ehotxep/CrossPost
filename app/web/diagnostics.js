/* Local, bounded timing buffer. No payloads, URLs, titles, tokens or error text. */
(() => {
  'use strict';
  const LIMIT=1000, views=new Set(['post','history','stats','trends','settings','unknown']);
  const rows=[], active=new Map(), prefix=Math.floor(Math.random()*0x100000000).toString(16).padStart(8,'0');
  let seq=0, navigation=null, timer=null;
  const now=()=>performance.now();
  const view=()=>views.has(window.CP?.S.view)?window.CP.S.view:'unknown';
  function record(kind,name,ms,ok=true,extra={}) {
    if(!Number.isFinite(ms)||ms<0||ms>3600000)return;
    if(['render','navigation','longtask'].includes(kind)&&!views.has(name))return;
    if(kind==='api'&&!/^[a-z][a-z0-9_]{0,79}$/.test(name))return;
    rows.push({kind,name,ms:Math.round(ms*100)/100,ok:!!ok,...extra});
    if(rows.length>LIMIT)rows.splice(0,rows.length-LIMIT);
  }
  function settle() {
    clearTimeout(timer);
    const nav=navigation;
    if(!nav||nav.pending)return;
    timer=setTimeout(()=>{
      if(navigation!==nav||nav.pending)return;
      record('navigation',nav.view,nav.last-nav.start);
      navigation=null;
    },150);
  }
  function begin(name) {
    if(!views.has(name))return;
    if(navigation)record('navigation',navigation.view,now()-navigation.start,false);
    navigation={view:name,start:now(),last:now(),pending:0};settle();
  }
  function request(name) {
    const start=now(), current=view(), nav=navigation, id=`${prefix}-${++seq}`;
    if(nav){nav.pending++;clearTimeout(timer);}
    active.set(id,{kind:'api',name,view:current,id,start});
    if(active.size>LIMIT)active.delete(active.keys().next().value);
    let done=false;
    return {id, finish(ok=true) {
      if(done)return;done=true;active.delete(id);
      record('api',name,now()-start,ok,{view:current,id});
      if(nav===navigation&&nav){nav.pending--;nav.last=now();settle();}
    }};
  }
  function rendering(name) {
    const start=now();
    return ()=>{
      record('render',name,now()-start);
      if(navigation){navigation.last=now();settle();}
    };
  }
  document.addEventListener('click',event=>{
    const el=event.target.closest?.('[data-view],[data-view-link],[data-go],[data-psec]');
    if(!el)return;
    let target=el.dataset.view||el.dataset.viewLink||el.dataset.go?.split(':')[0];
    if(el.dataset.psec||target==='platforms')target='settings';
    if(target&&target!==view())begin(target);
  },true);
  try {
    if(window.PerformanceObserver?.supportedEntryTypes?.includes('longtask')) {
      new PerformanceObserver(list=>{
        for(const entry of list.getEntries())record('longtask',view(),entry.duration);
      }).observe({type:'longtask',buffered:false});
    }
  } catch (_) { /* optional browser capability */ }
  function resource(entry) {
    let path;
    try { const url=new URL(entry.name,location.href);if(url.origin!==location.origin)return;path=url.pathname; }
    catch(_){return;}
    const name=/^\/(pthumb|mthumb|uthumb)\//.test(path)?'thumbnail':
      /^\/(media|pbig|ubig|uloop|uvideo|ustrip)\//.test(path)?'preview':
      path.startsWith('/static/')?'static':path==='/upload'?'upload':null;
    if(name)record('resource',name,entry.duration,true,{view:view()});
  }
  try {
    if(window.PerformanceObserver?.supportedEntryTypes?.includes('resource')) {
      new PerformanceObserver(list=>{for(const entry of list.getEntries())resource(entry);})
        .observe({type:'resource',buffered:true});
    }
  } catch (_) { /* ResourceTiming is optional too. URLs never enter the buffer. */ }
  window.PerfUI={request,rendering,begin,snapshot:()=>rows.map(row=>({...row})).concat([...active.values()].map(({start,...row})=>({...row,ms:now()-start,ok:false,pending:true}))).slice(-LIMIT)};
})();
