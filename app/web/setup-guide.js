/* Путеводитель использует обычные экраны настроек и не публикует тестовые посты. */
(function () {
  const platforms = [['telegram','Telegram'],['discord','Discord'],['vk','ВКонтакте'],['bluesky','Bluesky'],['deviantart','DeviantArt'],['e6ai','blep.art'],['e621','e621'],['floofgens','floofgens.net'],['reddit','Reddit'],['joyreactor','JoyReactor']];
  const steps = ['Площадки','Оформление','Расписание','Дополнительные функции'];
  const C = () => window.CP;
  const esc = value => C().esc(value == null ? '' : value);
  let checkVersion = 0;

  function status(cfg = {}) {
    const enabled = key => cfg.modules?.[key] !== false && cfg[key]?.enabled === true;
    const tg = cfg.telegram || {}, vk = cfg.vk || {};
    const configured = {
      telegram: !!(tg.has_token && (tg.channels?.length || tg.group?.id)),
      discord: !!cfg.discord?.webhooks?.length,
      vk: !!(vk.group_id && (vk.method === 'browser' ? vk.browser_ok : cfg.vk_connected)),
      reddit: !!(cfg.reddit?.browser_ok && Object.values(cfg.reddit.communities||{}).some(Boolean)),
      joyreactor: !!(cfg.joyreactor?.browser_ok && cfg.joyreactor.ai_tags?.length),
      bluesky: !!cfg.bluesky_session?.ok,
      deviantart: !!cfg.deviantart_session?.ok,
      ...Object.fromEntries(['e6ai','e621','floofgens'].map(k=>[k,!!(cfg[k]?.has_key && cfg[k]?.username)])),
    };
    return platforms.map(([key,name]) => ({key,name,ready:enabled(key) && configured[key],
      message: cfg.modules?.[key]===false ? 'Площадка выключена' : !configured[key] ? 'Нужно настроить подключение' : enabled(key) ? 'Настройки заполнены' : 'Подключение сохранено, отправка выключена'}));
  }

  function state() {
    const s = C().S;
    if (!s.setupGuide) {
      const saved = C().store.get('setupGuide', {}) || {};
      s.setupGuide = {
        step: Number.isInteger(saved.step) ? Math.max(0, Math.min(3, saved.step)) : 0,
        selected: Array.isArray(saved.selected) ? saved.selected.filter(key => platforms.some(p => p[0] === key)) : status(s.pub).filter(p => p.ready).map(p => p.key),
        skipped: Array.isArray(saved.skipped) ? saved.skipped.filter(n => Number.isInteger(n) && n >= 0 && n <= 3) : [],
        done: saved.done === true,
      };
    }
    return s.setupGuide;
  }

  function save() {
    const g = state();
    C().store.set('setupGuide', {step:g.step,selected:g.selected.slice(),skipped:g.skipped.slice(),done:g.done});
  }

  function link(section, label) {
    return `<button class="btn small" data-guide-open="settings" data-view-link="settings" data-tab="${section}">${label}</button>`;
  }

  function card(title, text, actions) {
    return `<div class="guide-item"><b>${title}</b><p class="muted small">${text}</p><div class="row">${actions}</div></div>`;
  }

  function resultHtml() {
    const s = C().S, result = s.guideResult;
    if (!result) return '';
    if (result.signature !== JSON.stringify([s.pub,state().selected])) return '<p class="warnline small">Настройки изменились — повторите проверку.</p>';
    return `<div class="guide-result" role="status"><b>${result.ready?'Выбранные площадки настроены':'Настройки требуют внимания'}</b>
      <ul>${result.checks.map(p => `<li class="${p.ready?'ok':'warnline'}">${p.name}: ${p.message}</li>`).join('')}</ul>
      ${!result.checks.length?'<p>Не выбрано ни одной площадки. Подключение можно настроить позже.</p>':''}
      <p class="muted small">Проверено наличие настроек. Действительность входа и права публикации проверяются в настройках каждой площадки.</p></div>`;
  }

  function stepHtml() {
    const g = state(), p = C().S.pub || {}, modules = p.modules || {};
    if (g.step === 0) return `<p class="muted">Отметьте площадки, которые хотите настроить. Уже заполненные настройки показаны рядом.</p>
      <div class="guide-platforms">${status(p).map(row => `<div class="guide-item"><label class="guide-choice"><input type="checkbox" data-guide-platform="${row.key}" ${g.selected.includes(row.key)?'checked':''}><span>${row.name}</span></label><p class="${row.ready?'ok':'muted'} small">${row.message}</p>${link(row.key,row.ready?'Изменить настройки':'Настроить подключение')}</div>`).join('')}</div>
      <p class="muted small">В настройках площадки проверьте подключение и доступ к выбранному каналу или группе.</p>`;
    if (g.step === 1) return `<p class="muted">Эти настройки необязательны. Начать можно с обычного текста и значений по умолчанию.</p><div class="guide-items">
      ${card('Подписи','Задайте ссылки и подписи для нужных площадок. Для SFW и 18+ можно использовать разные варианты.',link('signatures','Настроить подписи'))}
      ${card('Языки и перевод',modules.translation?'Английский текст можно вводить вручную. Автоперевод подключается отдельно.':'Языки выключены. Включите их, если нужны разные тексты для разных аудиторий.',link(modules.translation?'translate':'features',modules.translation?'Настроить перевод':'Выбрать функции'))}
      ${card('Файлы и оригиналы','Способ отправки оригиналов выбирается у каждой площадки. Общий размер JPEG-превью настраивается отдельно.',link('light','JPEG-превью')+platforms.filter(([key])=>g.selected.includes(key)).map(([key,name])=>link(key,name)).join(''))}</div>`;
    if (g.step === 2) return `<div class="guide-items">
      ${card('Время вручную','В редакторе выберите дату и время рядом с кнопкой публикации. Пост появится в календаре.', '<button class="btn small" data-guide-open="post" data-view-link="post">Открыть редактор</button>')}
      ${card('Автоматическая очередь',p.pipeline?.enabled?'Автоматическая очередь включена. Crosspost назначает время по её правилам; его можно закрепить вручную.':'Автоматическая очередь выключена. Добавленные в неё посты ждут включения, а посты с ручным временем следуют своему расписанию.','<button class="btn small" data-guide-open="queue" data-view-link="history">Открыть очередь</button>')}
      ${card('Фоновая отправка','Чтобы запланированные посты выходили при закрытом окне, нужна фоновая отправка. Перед закрытием дождитесь подтверждения её запуска. Автозапуск Windows включается отдельно.',link('worker','Настроить фоновую отправку'))}</div>`;
    const features = [['tags','Подбор тегов',['tagger'],'Предлагает теги по картинке. Модель устанавливается отдельно.'],['stats','Статистика',['stats','collect','news','forecast'],'Собирает результаты публикаций, показывает новости и прогноз.'],['trends','Тренды',['trends'],'Помогает изучать популярные темы на blep.art, e621 и floofgens.net.']];
    return `<p class="muted">Включите только нужные возможности. Их можно изменить в любой момент.</p><div class="guide-items">${features.map(([key,name,keys,text])=>`<label class="guide-item"><span class="tgl"><input type="checkbox" data-feat="${key}" ${keys.some(k=>modules[k]!==false)?'checked':''}><span>${name}</span></span><p class="muted small">${text}</p></label>`).join('')}</div>
      <div class="guide-check"><h3>Проверка готовности</h3><p class="muted small">Это локальная проверка настроек: она не обращается к площадкам, не запускает очередь и ничего не публикует.</p><button class="btn" data-guide-action="check" ${C().S.guideChecking?'disabled':''}>${C().S.guideChecking?'Проверяю…':'Проверить настройки'}</button>${resultHtml()}${C().S.guideError?C().problemHtml(C().S.guideError):''}</div>`;
  }

  function render() {
    const s = C().S;
    if (!s.guideOpen) return '';
    const g = state();
    const heading = `<div class="head"><h2 tabindex="-1">Быстрая настройка <span class="muted small">${g.step+1} / 4</span></h2><button class="btn small ghost" data-guide-action="close">Закрыть путеводитель</button></div>`;
    if (s.guideCompact) return `<section class="card setup-guide">${heading}<div class="row"><span class="muted small">Продолжайте настройку ниже. Прогресс путеводителя сохранён.</span><button class="btn small" data-guide-action="expand">Вернуться к шагам</button></div></section>`;
    return `<section class="card setup-guide" aria-label="Быстрая настройка">${heading}<nav class="guide-steps" aria-label="Шаги настройки">${steps.map((name,i)=>`<button class="btn small ${i===g.step?'primary':''}" data-guide-step="${i}" aria-current="${i===g.step?'step':'false'}">${i+1}. ${name}${g.skipped.includes(i)?' · пропущен':''}</button>`).join('')}</nav>
      ${stepHtml()}<div class="guide-footer"><button class="btn small" data-guide-action="back" ${g.step===0?'disabled':''}>Назад</button><button class="btn small ghost" data-guide-action="restart">Начать сначала</button><span class="spacer"></span>${g.step<3?'<button class="btn small ghost" data-guide-action="skip">Пропустить шаг</button><button class="btn primary" data-guide-action="next">Далее</button>':`<button class="btn primary" data-guide-action="finish" ${s.guideChecking?'disabled':''}>${s.guideResult?'Завершить путеводитель':'Завершить без проверки'}</button>`}</div></section>`;
  }

  async function onClick(event) {
    const target = event.target.closest('[data-guide-action], [data-guide-step], [data-guide-open]');
    if (!target) return false;
    const s = C().S;
    if (target.dataset.guideOpen) {
      if(target.dataset.guideOpen==='start'){s.guideOpen=true;s.guideCompact=false;}
      else s.guideCompact = true;
      if (target.dataset.guideOpen === 'queue') s.queueOpen = true;
      return false; // Обычный переход к реальным настройкам обрабатывает app.js.
    }
    event.preventDefault();
    const action = target.dataset.guideAction;
    if(action==='open'&&!s.pub)await window.Pub.load('settings');
    const g=state();
    if (action === 'open') {s.guideOpen=true;s.guideCompact=false;}
    else if (action === 'close') {s.guideOpen=false;save();}
    else if (action === 'expand') s.guideCompact=false;
    else if (target.dataset.guideStep !== undefined) {g.step=+target.dataset.guideStep;s.guideCompact=false;save();}
    else if (action === 'back') {g.step=Math.max(0,g.step-1);save();}
    else if (action === 'next' || action === 'skip') {
      g.skipped=g.skipped.filter(i=>i!==g.step);
      if(action==='skip')g.skipped.push(g.step);
      g.step=Math.min(3,g.step+1);save();
    } else if (action === 'restart') {checkVersion++;g.step=0;g.skipped=[];g.done=false;s.guideResult=null;s.guideError='';save();}
    else if (action === 'finish') {g.done=true;s.guideOpen=false;save();C().toast('Путеводитель завершён. Вернуться к нему можно в настройках.','ok');}
    else if (action === 'check') {
      const version=++checkVersion, selected=g.selected.slice();
      s.guideChecking=true;s.guideError='';s.guideResult=null;C().render();
      try {
        const cfg=await C().api('pub_settings');
        if(version===checkVersion && selected.join()===state().selected.join()) {
          s.pub=cfg;
          const checks=status(cfg).filter(row=>selected.includes(row.key));
          s.guideResult={checks,ready:checks.length>0&&checks.every(row=>row.ready),signature:JSON.stringify([cfg,selected])};
        }
      } catch(error) {if(version===checkVersion)s.guideError=error.message;}
      finally {s.guideChecking=false;}
    }
    C().render();
    if(action==='close'||action==='finish')document.querySelector('[data-guide-action="open"]')?.focus();
    else document.querySelector('.setup-guide h2')?.focus({preventScroll:action==='check'});
    return true;
  }

  function onChange(event) {
    const key=event.target.dataset.guidePlatform;
    if(!key||!platforms.some(p=>p[0]===key))return false;
    checkVersion++;
    const g=state();g.selected=g.selected.filter(p=>p!==key);
    if(event.target.checked)g.selected.push(key);
    C().S.guideResult=null;save();return true;
  }

  window.SetupGuide = {render,onClick,onChange,status};
})();
