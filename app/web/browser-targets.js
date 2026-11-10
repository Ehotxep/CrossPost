/* Experimental browser connections; publication itself always uses the backend queue. */
(function(){
  const C=()=>window.CP, esc=v=>C().esc(v==null?'':v);
  const names={reddit:'Reddit',joyreactor:'JoyReactor'};
  function settingsHtml(platform){
    const s=C().S,p=s.pub[platform],job=s.info?.browser_jobs?.[platform],running=job?.state==='running';
    const toggle=(id,on,label)=>`<label class="tgl"><input id="${id}" type="checkbox" ${on?'checked':''}><span>${label}</span></label>`;
    let h=`<section class="card setc"><div class="head"><h2>${C().pbadge(platform)} ${names[platform]}</h2><span class="badge warn">В разработке</span></div>
      <p class="muted small">Публикация через отдельный профиль браузера этой площадки в Crosspost. При необходимости войдите заново. Пока только изображения. Работа с реальным сайтом ещё не проверена.</p>
      <p class="${p.browser_ok?'ok':'muted'}">${p.browser_ok?'Вход сохранён; доступность формы проверяется при отправке':'Нужно войти через браузер'}</p>
      ${toggle(`bt-${platform}-enabled`,p.enabled,'Включить публикацию')}${toggle(`bt-${platform}-window`,p.show_window,'Показывать окно браузера')}
      <div class="row"><button class="btn" data-social-action="login" data-platform="${platform}" ${running?'disabled':''}>Войти в браузере</button>
      ${(platform==='reddit'?['FurAI','aiyiff']:['']).map(community=>`<button class="btn" data-social-action="dry" data-platform="${platform}" data-community="${community}" ${!p.browser_ok||running?'disabled':''}>Пробный прогон${community?' r/'+community:''}</button>`).join('')}</div>
      <p class="muted small">Пробный прогон использует тестовое изображение и рейтинг SFW. ${platform==='joyreactor'?'Используются настроенные теги ИИ или тестовый тег; правильность тегов не проверяется. ':''}Кнопка публикации не нажимается. Очистите старые вложения в черновике сайта перед запуском.</p>
      ${job?`<p class="${job.state==='error'?'err':'muted'} small" translate="no">${esc(job.message||job.error||job.state)}</p>`:''}
      ${s.socialError?.[platform]?`<p class="err" translate="no">${esc(s.socialError[platform])}</p>`:''}`;
    if(platform==='reddit'){
      h+=`<div class="tgls">${['FurAI','aiyiff'].map(n=>toggle(`bt-reddit-${n}`,p.communities[n],`r/${n}`)).join('')}</div>
        <p class="muted small">r/FurAI: SFW/NSFW по рейтингу. r/aiyiff: flair по итоговым тегам, с возможностью ручной замены в редакторе. При одинаковом приоритете разных flair потребуется ручной выбор.</p>
        <details class="more"><summary>Соответствия тегов и flair</summary><p class="muted small">JSON: all — обязательные теги; none — исключающие; flair — название; priority — приоритет (большее выше).</p>
        <textarea id="social-rules-reddit" rows="12" class="code" spellcheck="false">${esc(JSON.stringify(p.flair_rules,null,2))}</textarea>
        <button class="btn" data-social-action="rules" data-platform="reddit">Применить правила</button></details>`;
    }else{
      h+=`<div class="field"><label for="bt-joyreactor-ai">Обязательные теги ИИ (через запятую)</label><input id="bt-joyreactor-ai" value="${esc(p.ai_tags.join(', '))}"></div>
        <div class="field"><label for="bt-joyreactor-constant">Постоянные теги (через запятую)</label><input id="bt-joyreactor-constant" value="${esc(p.constant_tags.join(', '))}"></div>
        <p class="muted small">Точные обязательные теги задайте по правилам JoyReactor. Неизвестные теги теггера на сайт не отправляются.</p>
        <details class="more"><summary>Соответствия тегов и рейтингов</summary><p class="muted small">tag_aliases — словарь «тег теггера»: «тег JoyReactor»; rating_tags — списки тегов для sfw, erotic и explicit. Пробелы внутри тегов сохраняются.</p>
        <textarea id="social-rules-joyreactor" rows="10" class="code" spellcheck="false">${esc(JSON.stringify({tag_aliases:p.tag_aliases,rating_tags:p.rating_tags},null,2))}</textarea>
        <button class="btn" data-social-action="rules" data-platform="joyreactor">Применить правила</button></details>`;
    }
    return h+'</section>';
  }
  function postHtml(draft,plan){
    const p=C().S.pub;if(!p?.reddit?.enabled||p.modules.reddit===false)return '';
    const choices=p.reddit.flairs||{}, overrides=draft.reddit_flairs||{};
    return `<details class="more"><summary>Reddit · flair</summary>${['FurAI','aiyiff'].filter(n=>p.reddit.communities[n]).map(n=>{
      const found=plan?.dests.find(d=>d.key===`reddit:${n}`),manual=Object.hasOwn(overrides,n);
      return `<label class="field">r/${n}<select data-reddit-flair="${n}"><option value="__auto" ${!manual?'selected':''}>Автоматически${found?' · '+esc(found.flair||'без flair'):''}</option>
        ${n==='aiyiff'?`<option value="" ${manual&&overrides[n]===''?'selected':''}>Без flair</option>`:''}
        ${(choices[n]||[]).map(f=>`<option value="${esc(f)}" ${manual&&overrides[n]===f?'selected':''}>${esc(f)}</option>`).join('')}</select></label>`;
    }).join('')}</details>`;
  }
  async function onClick(event){
    const button=event.target.closest('[data-social-action]');if(!button)return false;
    event.preventDefault();const c=C(),s=c.S,platform=button.dataset.platform,action=button.dataset.socialAction;
    const rules=action==='rules'?document.getElementById('social-rules-'+platform).value:null;
    s.socialError={...s.socialError,[platform]:''};button.disabled=true;
    try{
      if(action==='rules'){
        const parsed=JSON.parse(rules),changes=platform==='reddit'?{flair_rules:parsed}:parsed;
        s.pub=await c.api('pub_save',{section:platform,changes});
        s.reviewKey=null;
      }else{
        const args=action==='login'?{platform}:{platform,community:button.dataset.community||'FurAI'};
        await c.api(action==='login'?'pub_browser_login':'pub_browser_dry',args);
        s.socialWaiting={...s.socialWaiting,[platform]:true};await c.loadInfo();c.startPoll();
      }
      c.render();
    }catch(error){
      // Keep malformed JSON in the field so the user can fix it.
      s.socialError[platform]=error.message;button.disabled=false;
      let box=button.parentElement.querySelector('[data-social-error]');
      if(!box){box=document.createElement('p');box.className='err';box.dataset.socialError='';button.after(box);}
      box.textContent=error.message;
    }
    return true;
  }
  async function tick(){
    const s=C().S;
    for(const platform of Object.keys(names))if(s.socialWaiting?.[platform]&&s.info?.browser_jobs?.[platform]?.state!=='running'){
      s.socialWaiting[platform]=false;s.pub=await C().api('pub_settings');
    }
  }
  window.BrowserTargets={settingsHtml,postHtml,onClick,tick};
})();
