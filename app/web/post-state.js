/* Полный снимок документа. Отключённые функции не удаляют сохранённые поля;
   применение языков и версий файлов определяется настройками отправителя. */
(function () {
  function capture(draft, tagger = true) {
    const document = {
      files: (draft.files || []).map(file => typeof file === 'string' ? file : file.id),
      title: draft.title || '',
      text: draft.text || '',
      rating: draft.rating,
      signature: draft.signature,
      exclude: draft.exclude || [],
      variants: draft.variants || {},
      multi: !!draft.multi,
      marks: draft.marks || {},
      hashtags: draft.hashtags == null ? null : draft.hashtags,
      e6ai_tags: draft.e6tags || draft.e6ai_tags || {},
      e6ai_meta: draft.e6meta || draft.e6ai_meta || {},
      ...(draft.booru_posts ? {booru_posts:draft.booru_posts} : {}),
      tagger,
      platform_texts: draft.platform_texts || {},
      alts: draft.alts || {},
      reddit_flairs: draft.reddit_flairs || {},
      profile: draft.profile || null,
      extend: draft.extend?.pub_id,
    };
    // Отложенное сохранение не должно увидеть последующие изменения вложенных полей.
    return JSON.parse(JSON.stringify(document));
  }

  function copyBooruTags(draft, source, target, {mode = 'merge', file = 'all'} = {}) {
    const sites = ['e6ai', 'e621', 'floofgens'];
    if (!sites.includes(source) || !sites.includes(target) || source === target || !['merge','replace'].includes(mode)) {
      throw Error('Некорректные параметры копирования тегов');
    }
    const posts = JSON.parse(JSON.stringify(draft.booru_posts || {}));
    const origin = posts[source]?.tags || {};
    const destination = posts[target] ||= {tags:{},meta:{}};
    destination.tags ||= {}; destination.meta ||= {};
    const ids = [...new Set((draft.files || []).map(item => typeof item === 'string' ? item : item.id))]
      .filter(id => file === 'all' || id === file);
    const clean = tags => [...new Set((Array.isArray(tags) ? tags : [])
      .filter(tag => typeof tag === 'string').map(tag => tag.trim().toLowerCase().replace(/ /g,'_')).filter(Boolean))];
    let copied = 0, skipped = 0;
    for (const id of ids) {
      const tags = clean(origin[id]);
      if (!tags.length) { skipped++; continue; } // Missing source data must never erase a target file.
      destination.tags[id] = mode === 'merge' ? [...new Set([...clean(destination.tags[id]), ...tags])] : tags;
      destination.meta[id] = {nochar:true, nocont:true, ...destination.meta[id], ok:''};
      copied++;
    }
    return {posts, copied, skipped};
  }

  class LatestRequests {
    constructor() {
      this.versions = new Map();
    }

    begin(channel, key) {
      const version = (this.versions.get(channel) || 0) + 1;
      this.versions.set(channel, version);
      return {channel, key, version};
    }

    current(ticket, key = ticket.key) {
      return this.versions.get(ticket.channel) === ticket.version && ticket.key === key;
    }
  }

  window.PostState = {capture, copyBooruTags, LatestRequests};
})();
