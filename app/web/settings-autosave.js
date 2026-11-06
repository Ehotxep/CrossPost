/* Последовательное сохранение обычных настроек. Ключи входа и операции с файлами
   намеренно не входят в список полей; для них остаются явные действия. */
(function () {
  const copy = value => JSON.parse(JSON.stringify(value));
  function merge(left = {}, right = {}) {
    const result = {...left};
    for (const [key,value] of Object.entries(right)) {
      if (['__proto__','constructor','prototype'].includes(key)) continue;
      result[key] = value && typeof value === 'object' && !Array.isArray(value) ? merge(result[key],value) : value;
    }
    return result;
  }

  class SaveQueue {
    constructor(save, changed = () => {}) {
      this.save=save;this.changed=changed;this.pending=new Map();this.errors=new Map();this.active=null;this.running=null;
    }
    edit(key, patch, values = {}) {
      const previous=this.pending.get(key)||{patch:{},values:{}};
      this.pending.set(key,{patch:merge(previous.patch,copy(patch)),values:{...previous.values,...copy(values)}});
      this.errors.delete(key);this.changed();
    }
    snapshot() {
      const jobs=new Map();
      if(this.active)jobs.set(this.active.key,this.active.job);
      for(const [key,job] of this.pending){const old=jobs.get(key)||{patch:{},values:{}};jobs.set(key,{patch:merge(old.patch,job.patch),values:{...old.values,...job.values}});}
      return [...jobs].map(([key,job])=>({key,...copy(job)}));
    }
    flush() {
      if(this.running)return this.running;
      if(!this.pending.size)return Promise.resolve(true);
      this.running=this.run().finally(()=>{this.running=null;this.changed();});
      return this.running;
    }
    async run() {
      const failed=new Set();
      while(true){
        const key=[...this.pending.keys()].find(key=>!failed.has(key));
        if(key===undefined)break;
        const job=this.pending.get(key);this.pending.delete(key);this.active={key,job};this.changed();
        try{await this.save(key,job.patch);this.errors.delete(key);}
        catch(error){
          const newer=this.pending.get(key);
          this.pending.set(key,{patch:merge(job.patch,newer?.patch),values:{...job.values,...newer?.values}});
          this.errors.set(key,error.message||String(error));
          if(!newer)failed.add(key);
        }
        this.active=null;this.changed();
      }
      return this.pending.size===0;
    }
  }

  const C=()=>window.CP, platforms=['telegram','discord','vk','bluesky','deviantart','e6ai','reddit','joyreactor'];
  const allowed={
    common:['nickname','style_tags','hidden_tags','trends'],
    light:['scale','quality','background','subsampling','sharpen'],
    hashtags:['enabled','max_species','rating_tags','constant','use'],
    signatures:['platforms','use'],versions:platforms,translate:['base','model','style'],
    e6ai:['constant_tags','preselect','with_sources','with_description'],
    bluesky:['enabled','include_title','alt_from_title','rating','langs'],
    deviantart:['enabled','is_ai_generated','noai','originals','tags_from','allow_free_download','rating','gallery_id','gallery_name'],
    reddit:['enabled','show_window','communities'],joyreactor:['enabled','show_window','ai_tags','constant_tags'],
    outputs:['by_date','jpeg','gif','video'],never:['tags'],
  };
  const words=value=>value.split(/[\s,]+/).filter(Boolean);
  function fieldKey(el){return el.id;}
  function describe(el) {
    if(!el)return null;
    const id=el.id,v=el.value?.trim()||'',on=!!el.checked;
    if(!id)return null;
    const social=/^bt-(reddit|joyreactor)-(enabled|window|FurAI|aiyiff|ai|constant)$/.exec(id);
    if(social){const [,key,field]=social;
      if(field==='enabled'||field==='window')return {key,patch:{[field==='enabled'?'enabled':'show_window']:on}};
      if(key==='reddit'&&['FurAI','aiyiff'].includes(field))return {key,patch:{communities:{[field]:on}}};
      if(key==='joyreactor'&&['ai','constant'].includes(field))return {key,patch:{[field==='ai'?'ai_tags':'constant_tags']:el.value.split(',').map(t=>t.trim()).filter(Boolean)}};
    }
    const common={nick:{nickname:v},style:{style_tags:words(v)},hidden:{hidden_tags:words(v)}};
    if(common[id])return {key:'common',patch:common[id]};
    if(id==='r6'||id==='r621')return {key:'common',patch:{trends:{[id==='r6'?'e6ai':'e621']:{refresh_days:Number(v)}}}};
    const light={lscale:{scale:Number(v)/100},lq:{quality:Number(v)},lbg:{background:v},lsub:{subsampling:on?'4:4:4':'4:2:0'},lsharp:{sharpen:on}};
    if(light[id])return {key:'light',patch:light[id]};
    const ht={htOn:{enabled:on},htN:{max_species:Number(v)},htS:{rating_tags:{sfw:v.replace(/^#/,'')}},htQ:{rating_tags:{erotic:v.replace(/^#/,'')}},htE:{rating_tags:{explicit:v.replace(/^#/,'')}},htC:{constant:words(v).map(t=>t.replace(/^#/,''))}};
    if(ht[id])return {key:'hashtags',patch:ht[id]};
    if(id.startsWith('htU-')&&platforms.includes(id.slice(4)))return {key:'hashtags',patch:{use:{[id.slice(4)]:on}}};
    const sig=/^sig-(telegram|discord|vk|bluesky|deviantart|reddit|joyreactor)-(en-)?(sfw|nsfw)$/.exec(id);
    if(sig)return {key:'signatures',patch:{platforms:{[sig[1]]:sig[2]?{en:{[sig[3]]:el.value}}:{[sig[3]]:el.value}}}};
    if(id.startsWith('siguse-')&&platforms.includes(id.slice(7)))return {key:'signatures',patch:{use:{[id.slice(7)]:on}}};
    const ver=/^ver-(telegram|discord|vk|bluesky|deviantart|reddit|joyreactor|e6ai)-(ru|en|clean)$/.exec(id);
    if(ver)return {key:'versions',patch:{[ver[1]]:['ru','en','clean'].filter(k=>document.getElementById(`ver-${ver[1]}-${k}`)?.checked)}};
    const tr={trbase:{base:v},trmodel:{model:v},trstyle:{style:el.value}};
    if(tr[id])return {key:'translate',patch:tr[id]};
    const e6={e6const:{constant_tags:words(v).map(t=>t.toLowerCase())},e6pre:{preselect:Number(v)/100},e6src:{with_sources:on},e6desc:{with_description:on}};
    if(e6[id])return {key:'e6ai',patch:e6[id]};
    const bs={bson:{enabled:on},bstitle:{include_title:on},bsalt:{alt_from_title:on},bsrating:{rating:v},bslang:{langs:words(v).slice(0,3)}};
    if(bs[id])return {key:'bluesky',patch:bs[id]};
    const da={daon:{enabled:on},daai:{is_ai_generated:on},danoai:{noai:on},daorig:{originals:on},datags:{tags_from:on?'hashtags':'none'},dadl:{allow_free_download:on},darating:{rating:v}};
    if(da[id])return {key:'deviantart',patch:da[id]};
    if(id==='dagal')return {key:'deviantart',patch:{gallery_id:v,gallery_name:v?(C().S.daGal||[]).find(g=>g.id===v)?.name||'':''}};
    const output={fdate:'by_date',fjpeg:'jpeg',fgif:'gif',fvideo:'video'};
    if(output[id])return {key:'outputs',patch:{[output[id]]:on}};
    if(id==='e6never')return {key:'never',patch:{tags:words(v)}};
    return null;
  }

  let timer=null,initialized=false,lastSaved=false;
  const invalid=new Map(),seen=new Map();
  function persist(){if(C())C().store.set('settingsPending',{jobs:queue.snapshot(),invalid:[...invalid]});}
  async function save(key,patch){
    const s=C().S,api=(name,args)=>C().api(name,args,{silent:true});
    if(key==='common')s.info.settings=await api('settings_save',{changes:patch});
    else if(key==='never')s.e6learn=await api('e6_never',{tags:patch.tags});
    else if(key==='outputs'){
      const o=s.outputs||await api('outputs_get',{});
      s.outputs=await api('outputs_set',{folder:o.custom?o.dir:'',by_date:o.by_date,jpeg:o.jpeg,gif:o.gif,video:o.video,move:false,...patch});
    }else{s.pub=await api('pub_save',{section:key,changes:patch});s.reviewKey=null;s.composition=null;s.readiness=null;}
    lastSaved=true;
  }
  const queue=new SaveQueue(save,()=>{persist();paint();});
  function pendingValues(){return Object.assign({},...queue.snapshot().map(job=>job.values),Object.fromEntries([...invalid].map(([id,entry])=>[id,entry.value])));}
  function dirty(){return queue.pending.size>0||!!queue.active||invalid.size>0;}
  function paint(){
    const error=queue.errors.size>0||invalid.size>0;
    const state=error?'error':dirty()?'saving':lastSaved?'saved':'idle';
    const message=error?'Настройки не сохранены':state==='saving'?'Сохраняю настройки…':state==='saved'?'Настройки сохранены':'Настройки сохраняются автоматически';
    document.querySelectorAll('[data-settings-save-state]').forEach(el=>{
      el.textContent=window.I18N.t(message);el.className=error?'err small':state==='saved'?'ok small':'muted small';
    });
    let box=document.getElementById('settings-save-error');
    if(error&&!box){box=document.createElement('div');box.id='settings-save-error';box.className='settings-save-error';box.setAttribute('role','alert');document.getElementById('main')?.prepend(box);}
    if(box){
      if(!error){box.remove();return;}
      const details=invalid.size?'Проверьте заполнение выделенных полей.':[...queue.errors.values()].join('; ');
      const sections={light:'light',hashtags:'hashtags',signatures:'signatures',versions:'versions',translate:'translate',e6ai:'e6ai',bluesky:'bluesky',deviantart:'deviantart',outputs:'files',never:'tags',common:'trends',reddit:'reddit',joyreactor:'joyreactor'};
      const links=[...new Set([...queue.errors.keys(),...[...invalid.values()].map(v=>v.scope)])].filter(key=>sections[key]).map(key=>`<button class="btn small" data-view-link="settings" data-tab="${sections[key]}">${window.I18N.t('Открыть настройки с ошибкой')}</button>`).join(' ');
      box.innerHTML=`<b>${window.I18N.t('Настройки не сохранены')}</b><p>${C().esc(window.I18N.t(details))}</p><button class="btn small" data-settings-save-retry>${window.I18N.t('Повторить сохранение')}</button> <button class="btn small ghost" data-settings-save-discard ${queue.active?'disabled':''}>${window.I18N.t('Убрать несохранённые правки')}</button> ${links}`;
    }
  }
  function capture(el){
    const spec=describe(el);if(!spec)return false;
    const id=fieldKey(el),value={value:el.value,checked:!!el.checked};
    if(seen.get(id)===JSON.stringify(value))return true;
    seen.set(id,JSON.stringify(value));lastSaved=false;
    if((el.type==='number'&&(!el.value||!el.validity.valid))||(['trbase','trmodel'].includes(id)&&!el.value.trim())){
      invalid.set(id,{value,scope:spec.key});el.setAttribute('aria-invalid','true');persist();paint();return true;
    }
    invalid.delete(id);el.removeAttribute('aria-invalid');
    queue.edit(spec.key,spec.patch,{[id]:value});
    clearTimeout(timer);timer=setTimeout(()=>queue.flush(),el.type==='checkbox'||el.tagName==='SELECT'?0:750);
    return true;
  }
  async function flush(){clearTimeout(timer);await queue.flush();return !dirty();}
  function afterRender(){
    if(!C()?.S.info)return;
    if(!initialized){
      initialized=true;
      const saved=C().store.get('settingsPending',null);
      if(saved){
        for(const job of saved.jobs||[]){
          if(!allowed[job.key]||!job.patch||typeof job.patch!=='object')continue;
          const patch=Object.fromEntries(Object.entries(job.patch).filter(([key])=>allowed[job.key].includes(key)));
          const values=Object.fromEntries(Object.entries(job.values||{}).filter(([id])=>describe({id,value:'',checked:false})));
          if(Object.keys(patch).length)queue.edit(job.key,patch,values);
        }
        for(const [id,entry] of saved.invalid||[])if(describe({id,value:'',checked:false})&&entry?.value)invalid.set(id,entry);
        if(queue.pending.size)timer=setTimeout(()=>queue.flush(),0);
      }
    }
    const values=pendingValues();
    document.querySelectorAll('#main input, #main textarea, #main select').forEach(el=>{
      if(!describe(el))return;
      const v=values[el.id];if(v){el.value=v.value;el.checked=v.checked;}
      if(invalid.has(el.id))el.setAttribute('aria-invalid','true');
      seen.set(el.id,JSON.stringify({value:el.value,checked:!!el.checked}));
      const card=el.closest('section.card');
      if(card&&!card.querySelector('[data-settings-save-state]')){
        const state=document.createElement('p');state.dataset.settingsSaveState='';state.setAttribute('role','status');card.append(state);
      }
    });
    document.querySelectorAll('[data-pa="sig-save"],[data-pa="ht-save"],[data-pa="light-save"],[data-pa="ver-save"],[data-pa="e6-opts"],[data-pa="bs-save"],[data-pa="da-save"],[data-pa="e6-never-save"],[data-pa="nick-save"],[data-action="save-settings"]').forEach(el=>{el.classList.add('autosave-legacy');el.hidden=true;});
    document.querySelectorAll('.cardfoot').forEach(foot=>{if(foot.querySelector('.autosave-legacy')){foot.classList.add('autosave-footer');foot.hidden=![...foot.children].some(el=>!el.hidden&&el.textContent.trim());}});
    paint();
  }
  async function beforeAction(event){
    const target=event.target;
    if(target.closest('[data-settings-save-retry]')){event.preventDefault();await flush();paint();return true;}
    if(target.closest('[data-settings-save-discard]')){
      event.preventDefault();if(queue.active)return true;
      queue.pending.clear();queue.errors.clear();invalid.clear();seen.clear();persist();C().render();return true;
    }
    if(!target.closest('button,a,[role="button"]')||!dirty())return false;
    const ok=await flush();
    const navigation=target.closest('#nav,[data-view-link],[data-psec],[data-guide-action],[data-guide-step]');
    if(!ok&&!navigation){event.preventDefault();paint();return true;}
    return false;
  }
  document.addEventListener('input',event=>{if(C()?.S.info)capture(event.target);});
  window.addEventListener('beforeunload',event=>{if(C()&&dirty()){persist();event.preventDefault();event.returnValue='';}});
  window.SettingsAutosave={SaveQueue,describe,capture,flush,afterRender,beforeAction};
})();
