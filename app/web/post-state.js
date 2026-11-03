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
      tagger,
      platform_texts: draft.platform_texts || {},
      alts: draft.alts || {},
      profile: draft.profile || null,
      extend: draft.extend?.pub_id,
    };
    // Отложенное сохранение не должно увидеть последующие изменения вложенных полей.
    return JSON.parse(JSON.stringify(document));
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

  window.PostState = {capture, LatestRequests};
})();
