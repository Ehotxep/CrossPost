"""Методы, которые вызывает интерфейс. Каждый публичный метод доступен как POST /api/<имя>."""
import json
import os
import shutil
import threading

from app import __version__, settings
from app.jobs import Jobs
from app import stats
from app.telegram.service import TgService
from app.trends import aggregate, dumps, metrics


class Api:
    def __init__(self):
        self.jobs = Jobs()
        self.window = None  # окно pywebview, если приложение запущено в окне
        self.tg = TgService()

    def call(self, name: str, args: dict):
        if name.startswith("_") or name == "call" or not callable(getattr(self, name, None)):
            raise ValueError(f"Неизвестный метод: {name}")
        return getattr(self, name)(**(args or {}))

    # --- общее ---
    def info(self):
        s = settings.load()
        return {"version": __version__, "settings": s, "can_pick_files": self.window is not None,
                "trends": {src: self._trends_state(src) for src in dumps.SOURCES},
                "tg_job": self.jobs.get("tg:sync"), "pub_job": self.jobs.get("publish"),
                "ins_job": self.jobs.get("insights:tag"),
                "vk_job": self.jobs.get("vk:auth"), "vk_stats_job": self.jobs.get("vk:stats"), "modules": self.modules(), "tagger_paused": __import__("app.tagger.remote", fromlist=["x"]).paused, "levels": self._levels(),
                "update": self._update_info()}

    def _update_info(self):
        try:
            from app import updates
            from app import selfupdate
            updates.check_async()
            ok, why = selfupdate.can_update()
            return dict(updates.status(), job=self.jobs.get("app:update"), ready=selfupdate.ready(), can=ok, why=why, result=selfupdate.result())
        except Exception:  # noqa: BLE001
            return None

    def _levels(self) -> dict:
        try:
            from app.insights import norms
            return norms.levels_cached()
        except Exception:  # noqa: BLE001
            return {"good": 1.3, "hit": 2.0, "super": 3.0}

    def create_shortcuts(self):
        """Ярлыки Crosspost со значком на рабочем столе и в «Пуске» (запуск без .bat и без предупреждения Windows)."""
        from app import icon
        return icon.create_shortcuts()

    def focus(self):
        """Второй запуск Crosspost просит уже открытое окно выйти на передний план."""
        from app import icon
        w = getattr(self, "window", None)
        if w is not None:
            for m in ("restore", "show"):
                try:
                    getattr(w, m)()
                except Exception:  # noqa: BLE001
                    pass
            icon.focus_window(w.title if hasattr(w, "title") else "")
        return {"window": w is not None, "starting": w is None and bool(getattr(self, "window_expected", False))}

    def ui_set(self, changes: dict):
        """Запомнить состояние окна (экран, черновик, фильтры) — в файл, чтобы пережило перезапуск."""
        from app import ui_state
        ui_state.update(changes)
        return True

    def modules(self) -> dict:
        from app.publish import settings as PS
        return PS.load(raw=True)["modules"]

    def modules_set(self, key: str, on: bool):
        """Включить или выключить модуль: площадку, экран или дополнительную функцию (теггер, сбор цифр, новости, прогноз)."""
        return self.modules_set_many({key: on})

    def modules_set_many(self, changes: dict):
        """Несколько модулей разом — например, «Только кросспостинг»."""
        from app.publish import settings as PS
        known = PS.PLATFORM_MODULES + PS.SCREEN_MODULES + PS.EXTRA_MODULES
        bad = [k for k in changes if k not in known]
        if bad:
            raise ValueError("Неизвестный модуль: " + ", ".join(bad))
        PS.update("modules", {k: bool(v) for k, v in changes.items()})
        return self.modules()

    def _mod(self, key: str) -> bool:
        from app.publish import settings as PS
        return PS.module_on(key)

    # --- панель процессов: всё, что сейчас идёт в фоне ---
    JOB_LABELS = {"tg:pool": ("Запас прокси Telegram", "settings:net"), "ext:sync": ("Посты ВК и Bluesky для календаря", "history"), "trends:e6ai": ("Тренды e6ai", "trends"), "trends:e621": ("Тренды e621", "trends"),
                  "e6db": ("База тегов e6ai", "settings:tags"), "tagger:install": ("Установка теггера", "settings:tags"),
                  "tagger:run": ("Подбор тегов", "post"), "publish": ("Публикация", "post"),
                  "tg:sync": ("Загрузка из Telegram", "settings:import"), "insights:tag": ("Разметка истории теггером", "stats"),
                  "media:install": ("Установка ffmpeg", "settings:media"), "media:edit": ("Правка видео", "post"), "media:editprev": ("Просмотр петли", "post"), "vk:auth": ("ВКонтакте", "settings:vk"),
                  "vk:stats": ("Статистика ВК", "settings:collect"), "app:update": ("Загрузка обновления", "settings:app")}

    def news(self):
        """Новости по свежим артам; если давно не смотрели — посмотрим в фоне (ответ — сразу, из сохранённого)."""
        from app.insights import news
        import time as _t
        if not (self._mod("news") and self._mod("collect")):
            return {"items": [], "unread": 0, "checked": 0, "off": True}
        try:
            from app import worker
            busy = worker.status()["running"]   # фоновая отправка сама смотрит раз в 5 минут — не дублируем
        except Exception:
            busy = False
        if not busy and _t.time() - news._last["t"] > news.CHECK_GAP:
            news.collect_async()
        return news.items()

    def news_read(self):
        from app.insights import news
        news.mark_read()
        return news.items()

    def activity(self):
        import time as _t
        now = _t.time()
        out = []
        for key, j in self.jobs.all().items():
            if key == "media:editprev":   # быстрый просмотр внутри окна правки — отдельной плашкой не показываем
                continue
            fin = j.get("finished") or 0
            if j["state"] != "running" and now - fin > (60 if j["state"] == "error" else 6):
                continue
            label, go = self.JOB_LABELS.get(key, (None, ""))
            if key.startswith(("poststats:", "pubstats:")):
                label, go = "Обновление цифр поста", "history"
            out.append({"key": key, "label": label or key, "go": go, "state": j["state"], "cancelling": bool(j.get("cancel")) and j["state"] == "running", "progress": j.get("progress") or 0,
                        "message": j.get("error") if j["state"] == "error" else j.get("message"), "started": j.get("started")})
            if key == "publish":   # у публикации — своя полоска у каждого места
                try:
                    from app.publish import runner
                    st = runner.state() or {}
                    out[-1]["subs"] = [{"platform": d.get("platform"), "name": d.get("name", "").split(" · ")[-1], "state": d.get("state"),
                                        "progress": 1.0 if d.get("state") in ("done", "error") else (d.get("progress") or 0)}
                                       for d in st.get("dests") or []]
                except Exception:
                    pass
        try:   # лёгкие версии картинок
            from app.publish import prep
            with prep._lock:
                n = sum(1 for t in prep._threads.values() if t.is_alive())
            if n:
                out.append({"key": "prep", "label": "JPEG-превью", "go": "post", "state": "running", "progress": None,
                            "message": f"готовлю: {n}"})
        except Exception:
            pass
        try:   # PNG-оригиналы, залитые заранее
            from app.publish import tg_bot
            for bot in list(tg_bot._mt_cache.values()):
                st = bot.pre_state()
                busy = [v for v in st.values() if isinstance(v, (int, float))]
                if busy:
                    out.append({"key": "preupload", "label": "Предварительная загрузка PNG в Telegram", "go": "post", "state": "running",
                                "progress": sum(busy) / len(busy), "message": f"{len(busy)} из {len(st)} файлов"})
        except Exception:
            pass
        try:
            from app.tagger import remote
            if remote.starting:
                out.append({"key": "tagger-load", "label": "Теггер", "go": "post", "state": "running", "progress": None,
                            "message": "загружаю модель в видеокарту — до минуты"})
        except Exception:
            pass
        return out

    # --- обновления ---
    def update_check(self):
        from app import updates
        updates.check(force=True)
        return self._update_info()   # с признаками «можно ли обновлять отсюда» — иначе окно теряет причину отказа

    def update_set(self, check: bool = True):
        from app import updates
        return updates.set_check(check)

    def update_skip(self, version: str = ""):
        from app import updates
        return updates.skip(version)

    def update_prepare(self):
        """Скачать и проверить новую версию (ничего не меняя). Дальше — update_apply."""
        from app import selfupdate
        if not self.jobs.start("app:update", selfupdate.prepare):
            raise ValueError("Обновление уже скачивается")
        return True

    def update_apply(self):
        """Установить скачанную версию: остановить фоновую отправку, заменить файлы отдельным процессом, перезапуститься."""
        from app import selfupdate
        busy = [self.JOB_LABELS.get(k, (k,))[0] for k, j in self.jobs.all().items() if j["state"] == "running" and k != "app:update"]
        if busy:
            raise ValueError("Сейчас идёт: " + ", ".join(busy) + ". Дождитесь окончания — программа закроется на время обновления.")
        res = selfupdate.apply()

        def close():
            import os as _os
            import time as _t
            _t.sleep(1.0)
            try:
                if self.window is not None:
                    self.window.destroy()
            except Exception:  # noqa: BLE001
                pass
            _t.sleep(4.0)
            _os._exit(0)   # браузерный режим или окно не закрылось: замена ждёт завершения процесса
        threading.Thread(target=close, daemon=True, name="update-close").start()
        return res

    def update_ack(self):
        from app import selfupdate
        return selfupdate.result(ack=True)

    def update_open(self):
        from app import updates
        lt = updates.status()["latest"]
        self.open_url((lt or {}).get("url") or f"https://github.com/{updates.REPO}/releases")
        return True

    def open_url(self, url: str):
        """Открыть ссылку в обычном браузере (из окна приложения ссылки сами не открываются)."""
        import webbrowser
        if not str(url).startswith(("https://", "http://")):
            raise ValueError("Можно открывать только веб-ссылки")
        webbrowser.open(url)
        return True

    def open_debug_folder(self):
        """Открыть папку со снимками экрана браузерной публикации."""
        import os
        import subprocess
        import sys
        from app.paths import sub
        folder = str(sub("browser_debug"))
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", folder])
        return folder

    def _open_folder(self, folder: str):
        import os
        import subprocess
        import sys
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", folder])

    def settings_save(self, changes: dict):
        return settings.save(changes)

    def hide_tag(self, tag: str, hide: bool = True):
        """Скрыть тег из всех списков трендов (или вернуть)."""
        cur = list(settings.load().get("hidden_tags", []))
        if hide and tag not in cur:
            cur.append(tag)
        if not hide:
            cur = [t for t in cur if t != tag]
        return settings.save({"hidden_tags": cur})["hidden_tags"]

    def job(self, key: str):
        return self.jobs.get(key)

    def job_cancel(self, key: str):
        """Кнопка «Отмена» на прогресс-баре: фоновая задача остановится при ближайшем шаге."""
        return self.jobs.cancel(key)

    def pick_files(self, title: str = "Выберите файлы", kind: str = "dumps"):
        if self.window is None:
            return []
        import webview
        types = ("Выгрузка Telegram (*.json)", "Все файлы (*.*)") if kind == "tgjson" else ("Выгрузки (*.csv.gz)", "Все файлы (*.*)")
        res = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=kind != "tgjson", file_types=types)
        return list(res or [])

    # --- тренды ---
    def _trends_state(self, source: str):
        meta_p = aggregate.out_dir(source) / "meta.json"
        meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else None
        return {"ready": meta is not None, "meta": {k: meta[k] for k in ("dump_date", "built_at", "posts", "tags")} if meta else None,
                "job": self.jobs.get(f"trends:{source}")}

    def trends_update(self, source: str):
        """Скачать свежую выгрузку и пересчитать (в фоне)."""
        if source not in dumps.SOURCES:
            raise ValueError("Неизвестный источник")
        ua = dumps.user_agent(self._nick())

        from app import subproc

        def run(report):   # в отдельном процессе: разбор выгрузки e621 съедает гигабайты, процесс их потом отдаёт
            subproc.run(report, "app.trends.jobs:update", source, ua)
            from app.trends import metrics
            metrics.forget(source)
        return self.jobs.start(f"trends:{source}", run)

    def _nick(self) -> str:
        """Ник для запросов к e621/e6ai: из «Настройки» → e6ai; если пусто — имя пользователя e6ai."""
        nick = settings.load().get("nickname", "")
        if not nick:
            try:
                from app.publish import settings as PS
                nick = PS.load()["e6ai"].get("username", "")
            except Exception:
                pass
        return nick

    def _auto_refresh(self):
        """При запуске: обновить выгрузки, которые старше срока из настроек (только уже подключённые)."""
        from datetime import datetime, timezone
        if not self.modules().get("trends", True):
            return
        s = settings.load()
        for src in dumps.SOURCES:
            cfg = s["trends"].get(src, {})
            st = self._trends_state(src)
            if not cfg.get("enabled", True) or not st["ready"]:
                continue
            built = datetime.fromisoformat(st["meta"]["built_at"])
            age_days = (datetime.now(timezone.utc) - built).total_seconds() / 86400
            if age_days >= cfg.get("refresh_days", 1):
                self.trends_update(src)

    def trends_import(self, source: str, posts_path: str, tags_path: str):
        """Подключить вручную скачанные файлы выгрузки (в фоне)."""
        from app import subproc

        def run(report):
            subproc.run(report, "app.trends.jobs:import_files", source, posts_path, tags_path)
            from app.trends import metrics
            metrics.forget(source)
        return self.jobs.start(f"trends:{source}", run)

    def trends_overview(self, source: str, rating: str = "all", category: str = "all", mine_only: bool = False):
        model = metrics.get_model(source, rating)
        if model is None:
            return None
        cfg = settings.load()
        style, hidden = cfg.get("style_tags", []), cfg.get("hidden_tags", [])
        data = metrics.overview(model, category, style, mine_only, hidden)
        other = "e6ai" if source == "e621" else "e621"
        m_other = metrics.get_model(other, rating)
        if m_other is not None:
            m621, m6 = (model, m_other) if source == "e621" else (m_other, model)
            data["leads"] = metrics.leads(m621, m6, style, hidden=hidden)
        else:
            data["leads"] = None
        return data

    def trends_forecast(self, source: str, tag: str, rating: str = "all"):
        model = metrics.get_model(source, rating)
        if model is None:
            return None
        fc = metrics.forecast(model, tag)
        if fc:
            fc["mine"] = tag in set(settings.load().get("style_tags", []))
            fc["backtest"] = metrics.backtest(model)
        return fc

    # --- Telegram ---
    def tg_status(self):
        st = self.tg.status()
        try:   # запоминаем — в следующий раз экран откроется сразу с ним
            from app import cache
            cache.write("tg_status", {k: v for k, v in st.items() if k != "net"}, 0)
        except Exception:
            pass
        return st

    def tg_status_quick(self):
        """Последний известный статус входа в Telegram — без обращения к серверам (проверка — отдельно, в фоне)."""
        from app import cache
        o = cache.read("tg_status")
        st = dict(o["data"]) if o else {"configured": self.tg.keys() is not None, "authorized": False, "unknown": True}
        st["net"] = self.tg.status_net() if hasattr(self.tg, "status_net") else {}
        st["quick"] = True
        return st

    def tg_set_keys(self, api_id, api_hash: str):
        return self.tg.set_keys(api_id, api_hash)

    def tg_set_net(self, net: dict):
        return self.tg.set_net(net)

    def tg_check_net(self, net: dict | None = None):
        return self.tg.check_network(net)

    def tg_proxy_links(self, text: str):
        """Ссылки на прокси из Telegram → запасные прокси + проверка (сохраняется первый рабочий)."""
        return self.tg.add_proxy_links(text)

    def tg_forget_proxy(self, host: str, port: int):
        return self.tg.forget_proxy(host, port)

    def tg_pool_set(self, changes: dict):
        """Запас прокси из открытых списков: включить/выключить, сколько держать, откуда брать."""
        from app.telegram import proxy_pool
        proxy_pool.set_cfg(self.tg, changes)
        return self.tg.status()

    def tg_pool_refresh(self):
        """Пополнить запас прокси сейчас (в фоне: скачать списки, проверить, оставить рабочие)."""
        from app.telegram import proxy_pool
        return self.jobs.start("tg:pool", lambda report: proxy_pool.refresh(self.tg, report))

    def tg_send_code(self, phone: str):
        return self.tg.send_code(phone)

    def tg_sign_in(self, code: str):
        return self.tg.sign_in(code)

    def tg_password(self, password: str):
        return self.tg.sign_in_password(password)

    def tg_log_out(self):
        return self.tg.log_out()

    def tg_channels(self, refresh: bool = False):
        return self.tg.channels(refresh)

    def tg_select(self, chat_id: str, selected: bool, rating: str = "all"):
        return self.tg.select(chat_id, selected, rating)

    def tg_sync(self):
        """Импорт истории выбранных каналов и обновление цифр (в фоне)."""
        def run(report):
            self.tg.sync(report)
            # если историю уже размечали теггером — доразмечаем новые посты
            from app.insights import history_tags
            from app.tagger import jtp
            st = history_tags.status()
            if st["tagged"] and st["todo"] and jtp.status()["ready"]:
                from app import subproc
                subproc.run(report, "app.trends.jobs:history_tags")   # модель теггера — в отдельном процессе
            from app.insights import warmup
            warmup.warm_async()
        return self.jobs.start("tg:sync", run)

    def tg_import_export(self, path: str):
        """Импорт канала из выгрузки Telegram Desktop (result.json) — без api_id и входа."""
        from app.telegram.export_import import find_result, import_export
        find_result(path)  # сразу сообщить, если путь неверный
        def run(report):
            import_export(path, report)
            from app.insights import warmup
            warmup.warm_async()
        return self.jobs.start("tg:sync", run)

    # --- публикация: настройки ---
    def pub_settings(self):
        from app.publish import settings as PS
        import core.common as cc
        import app.publish.runner  # noqa: F401  (путь к токенам)
        out = PS.public(PS.load())
        vk_tok = cc.load_token("vk") or {}
        out["vk_connected"] = bool(vk_tok) or out["vk"]["has_manual_token"]
        out["vk_scope"] = vk_tok.get("scope", "")
        bs = cc.load_token("bluesky") or {}
        out["bluesky_session"] = {"handle": bs.get("handle", ""), "ok": bool(bs.get("accessJwt"))}
        da = cc.load_token("deviantart") or {}
        granted = set((da.get("scope") or "").replace(",", " ").split())
        out["deviantart_session"] = {"username": da.get("username", ""), "ok": bool(da.get("access_token")),
                                     "refreshed": da.get("refreshed"),
                                     # вход до появления права browse — папки и цифры работ недоступны, нужно войти заново
                                     "need_relogin": bool(da.get("access_token")) and "browse" not in granted}
        return out

    # --- Bluesky и DeviantArt: вход ---
    def pub_bluesky_login(self, handle: str, app_password: str):
        from app.publish import settings as PS
        import app.publish.runner  # noqa: F401  (путь к токенам)
        from platforms import bluesky
        me = bluesky.login(PS.load()["bluesky"], handle, app_password)   # пароль не сохраняем — только сессию
        PS.update("bluesky", {"enabled": True, "handle": me["handle"]})
        return self.pub_settings()

    def pub_bluesky_logout(self):
        import app.publish.runner  # noqa: F401
        from platforms import bluesky
        bluesky.logout()
        return self.pub_settings()

    def pub_da_login(self, client_id: str = "", client_secret: str = ""):
        from app.publish import settings as PS
        import app.publish.runner  # noqa: F401
        from platforms import deviantart
        ch = {}
        if (client_id or "").strip():
            ch["client_id"] = client_id.strip()
        if (client_secret or "").strip():
            ch["client_secret"] = client_secret.strip()
        if ch:
            PS.update("deviantart", ch)
        url = deviantart.auth_start(PS.load()["deviantart"])
        return {"url": url}

    def pub_da_state(self):
        from platforms import deviantart
        st = deviantart.auth_state()
        if st["done"]:
            from app.publish import settings as PS
            PS.update("deviantart", {"enabled": True})
        return dict(st, settings=self.pub_settings())

    def pub_da_finish(self, url: str):
        """Адрес из браузера, если локальный адрес возврата не поймал ответ."""
        from app.publish import settings as PS
        import app.publish.runner  # noqa: F401
        from platforms import deviantart
        deviantart.auth_finish(url)
        PS.update("deviantart", {"enabled": True})
        return self.pub_settings()

    def pub_da_logout(self):
        import app.publish.runner  # noqa: F401
        from platforms import deviantart
        deviantart.logout()
        return self.pub_settings()

    def pub_da_galleries(self):
        from app.publish import settings as PS
        import app.publish.runner  # noqa: F401
        from platforms import deviantart
        return deviantart.galleries(PS.load()["deviantart"])

    def pub_save(self, section: str, changes: dict):
        from app.publish import settings as PS
        if section == "telegram" and not changes.get("bot_token"):
            changes.pop("bot_token", None)   # пустое поле = оставить сохранённый токен
        if section == "e6ai" and not changes.get("api_key"):
            changes.pop("api_key", None)
        if section == "e6ai":
            changes = PS.site_changes(changes)   # аккаунт и постоянные теги — выбранного сайта
        if section == "vk" and not changes.get("access_token"):
            changes.pop("access_token", None)
        if section == "deviantart" and not changes.get("client_secret"):
            changes.pop("client_secret", None)
        if section == "translate":
            clear = bool(changes.pop("clear_key", False))
            key = str(changes.get("api_key") or "").strip()
            if key:
                changes["api_key"] = key
            elif clear:
                changes["api_key"] = ""        # «Удалить ключ»
            else:
                changes.pop("api_key", None)   # пустое поле = оставить сохранённый ключ
        if section == "platform_order":   # список площадок целиком; чистим от лишнего
            lst = changes if isinstance(changes, list) else (changes or {}).get("order")
            changes = PS.platform_order({"platform_order": lst})
        if section == "versions":   # только известные метки
            changes = {p: [v for v in (vs or []) if v in ("ru", "en", "clean")] for p, vs in changes.items()}
        PS.update(section, changes)
        return self.pub_settings()

    def _tg_ctx(self):
        from app.publish import tg_bot
        tg_bot.TG_SERVICE = self.tg
        return tg_bot

    def pub_translate(self, title: str = "", text: str = "", src: str = "ru", dst: str = "en"):
        """Перевод описания через DeepSeek. Ключ берём из настроек, наружу он не уходит."""
        from app import translate
        from app.publish import settings as PS
        from app.jobs import mask_secrets
        if not PS.module_on("translation"):
            return {"ok": False, "error": "Перевод выключен: «Настройки» → «Функции» → «Языки и перевод»"}
        cfg = PS.load()["translate"]
        try:
            return dict(translate.translate(cfg, title, text, src if src in ("ru", "en") else "ru", dst if dst in ("ru", "en") else "en"), ok=True)
        except Exception as e:  # noqa: BLE001 — в интерфейс уходит понятный текст, без ключа
            msg = mask_secrets(str(e))
            if cfg.get("api_key"):
                msg = msg.replace(cfg["api_key"], "***")
            return {"ok": False, "error": msg}

    def pub_translate_check(self):
        """Проверка ключа: короткий пробный перевод."""
        return self.pub_translate(title="", text="Привет, это проверка.")

    def pub_tg_bot(self, token: str, transport: str = "auto"):
        """Проверяет токен и сохраняет. auto: сначала Bot API, если api.telegram.org недоступен — MTProto."""
        from app.publish import settings as PS
        tg_bot = self._tg_ctx()
        token = (token or "").strip() or PS.load()["telegram"]["bot_token"]
        if not token:
            raise ValueError("Вставьте токен бота")
        note = ""
        if transport in ("auto", "botapi"):
            try:
                me, used = tg_bot.bot_me(token, "botapi"), "botapi"
            except tg_bot.NetworkError:
                if transport == "botapi":
                    raise
                if not self.tg.keys():
                    raise ValueError("api.telegram.org недоступен из вашей сети. Бот может работать как Telegram Desktop (MTProto), "
                                     "но для этого нужны api_id и api_hash: «Настройки» → «Статистика и тренды» → «Telegram: статистика» → «Подключение через API».")
                me, used = tg_bot.bot_me(token, "mtproto"), "mtproto"
                note = "api.telegram.org недоступен — бот подключён через MTProto, как Telegram Desktop."
        else:
            me, used = tg_bot.bot_me(token, "mtproto"), "mtproto"
        PS.update("telegram", {"bot_token": token, "bot_name": me["username"] or me["name"], "enabled": True, "transport": used})
        out = self.pub_settings()
        out["note"] = note
        return out

    def pub_tg_chat(self, chat: str, group: bool = False):
        from app.publish import settings as PS
        tg_bot = self._tg_ctx()
        cfg = PS.load()["telegram"]
        if not cfg["bot_token"]:
            raise ValueError("Сначала укажите токен бота")
        return tg_bot.chat_info(cfg, chat, need_post=not group)

    def pub_tg_known(self):
        """Каналы и группы, которые знает импорт (если выполнен вход через API — с пометкой канал/группа)."""
        try:
            authorized = self.tg.keys() is not None and self.tg.status().get("authorized")
        except Exception:
            authorized = False
        rows = self.tg.channels(refresh=bool(authorized))
        return {"authorized": bool(authorized),
                "chats": [{"chat_id": r["chat_id"], "title": r["title"], "username": r["username"],
                           "broadcast": r.get("broadcast")} for r in rows]}

    def pub_tg_topics(self, chat_id: str):
        from app.publish import tg_bot
        return self.tg.forum_topics(tg_bot.norm_chat(chat_id))

    def pub_discord_check(self, url: str):
        import requests
        url = (url or "").strip()
        if not url.startswith("https://discord.com/api/webhooks/") and not url.startswith("https://discordapp.com/api/webhooks/"):
            raise ValueError("Ссылка вебхука начинается с https://discord.com/api/webhooks/…")
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            raise ValueError(f"Discord не узнал этот вебхук (HTTP {r.status_code}) — скопируйте ссылку заново")
        j = r.json()
        return {"name": j.get("name"), "channel_id": j.get("channel_id"), "guild_id": j.get("guild_id")}

    def pub_vk_login(self, app_id: str):
        from app.publish import settings as PS, vk_auth
        import app.publish.runner  # noqa: F401
        vk = PS.update("vk", {"app_id": str(app_id).strip()})["vk"]
        if vk["redirect_uri"].startswith("http://"):   # старый адрес — VK ID требует HTTPS
            vk = PS.update("vk", {"redirect_uri": "https://localhost/callback"})["vk"]
        vk_auth.start(vk["app_id"], vk["redirect_uri"])
        return True

    def pub_vk_finish(self, url: str):
        from app.publish import vk_auth
        import app.publish.runner  # noqa: F401
        vk_auth.finish(url)
        return self.pub_settings()

    def pub_vk_group(self, group_id: str):
        from app.publish import settings as PS, vk_auth
        import app.publish.runner  # noqa: F401
        vk = PS.load()["vk"]
        g = vk_auth.group_info(group_id, vk["app_id"], vk.get("access_token", ""))
        PS.update("vk", {"group_id": g["id"], "group_name": g["name"], "enabled": True})
        out = self.pub_settings()
        out["vk_group_check"] = g
        return out

    def pub_vk_method(self, method: str):
        from app.publish import settings as PS
        PS.update("vk", {"method": "browser" if method == "browser" else "api"})
        return self.pub_settings()

    def pub_vk_browser_login(self):
        """Окно браузера: войти во ВК один раз (вход сохраняется в отдельном профиле приложения)."""
        from app.publish import settings as PS, vk_web

        def run(report):
            vk_web.login(report)
            PS.update("vk", {"browser_ok": True, "method": "browser"})
        return self.jobs.start("vk:auth", run)

    def pub_vk_browser_group(self, group: str):
        from app.publish import settings as PS
        g = (group or "").strip()
        if not g:
            raise ValueError("Укажите ссылку на группу или её номер")
        import re as _re
        m = _re.search(r"(?:club|public)(\d+)", g)
        gid = m.group(1) if m else g.rstrip("/").split("/")[-1].lstrip("-")
        PS.update("vk", {"group_id": gid, "group_name": "", "enabled": True})
        return self.pub_settings()

    def pub_vk_recon(self, url: str):
        """Разведка статистики ВК: открыть страницу статистики, записать запросы (без ключей), попробовать «Скачать»."""
        from datetime import datetime as _dt
        from app.publish import settings as PS, vk_recon
        url = vk_recon.dashboard_url(url)
        PS.update("vk", {"stats_url": url})
        vk = PS.load(raw=True)["vk"]
        if not vk.get("browser_ok"):
            raise ValueError("Сначала войдите во ВК через браузер — кнопка «Войти во ВКонтакте» выше")

        def run(report):
            res = vk_recon.run(report, url, headless=not vk.get("show_window", True))
            PS.update("vk", {"recon_file": res["file"], "recon_msg": res["message"], "recon_at": _dt.now().isoformat(timespec="minutes")})
        return self.jobs.start("vk:auth", run)

    def pub_vk_stats(self, url: str = ""):
        """Собрать статистику постов ВК сейчас (окно браузера — как в настройке «Показывать окно браузера»)."""
        from app.publish import settings as PS, vk_recon, vk_stats
        if url.strip():
            PS.update("vk", {"stats_url": vk_recon.dashboard_url(url)})
        vk = PS.load(raw=True)["vk"]
        if not vk.get("browser_ok"):
            raise ValueError("Сначала войдите во ВК через браузер — кнопка «Войти во ВКонтакте» выше")
        return self.jobs.start("vk:stats", lambda report: vk_stats.run_and_record(report, headless=not vk.get("show_window", True)))

    def pub_vk_dry(self, kind: str = "images"):
        """Пробный прогон: всё, кроме нажатия «Опубликовать», + снимок экрана. kind: images / gif / video."""
        from app.publish import settings as PS, vk_web
        from app.paths import sub
        vk = PS.load()["vk"]
        if not vk.get("group_id"):
            raise ValueError("Сначала укажите группу")
        if kind == "video":
            from app.publish import media
            if not media.ready():
                raise ValueError("Для пробы с видео нужен ffmpeg — «Настройки» → «Видео и GIF»")

        def run(report):
            from PIL import Image, ImageDraw
            if kind == "gif":
                fp = sub("tmp") / "vk_dry.gif"
                frames = []
                for i in range(6):
                    im = Image.new("RGB", (320, 240), (235, 238, 245))
                    ImageDraw.Draw(im).ellipse((40 + i * 40, 90, 100 + i * 40, 150), fill=(91, 103, 240))
                    frames.append(im)
                frames[0].save(fp, save_all=True, append_images=frames[1:], duration=150, loop=0)
            elif kind == "video":
                from app.publish import media
                import subprocess as _sp
                fp = sub("tmp") / "vk_dry.mp4"
                _sp.run([media.tool("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-t", "3",
                         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(fp)], check=True, creationflags=media.NOWIN)
            else:
                fp = sub("tmp") / "vk_dry.jpg"
                im = Image.new("RGB", (800, 600), (235, 238, 245))
                ImageDraw.Draw(im).text((40, 280), "Crosspost: probnyi progon", fill=(40, 60, 120))
                im.save(fp, "JPEG")
            report(progress=0.1, message="Пробный прогон: открываю группу…")
            res = vk_web.post(vk["group_id"], [str(fp)], "Проверка Crosspost — эта запись не публикуется",
                              dry_run=True, headless=not vk.get("show_window", True), kind=kind,
                              progress=lambda f: report(progress=0.1 + 0.85 * f), log=lambda m: report(message=m + "…"))
            ch = {"dry_shot": res.get("shot", ""), "dry_shot2": res.get("shot2", "")}
            if res.get("title"):
                ch["group_name"] = res["title"]
            PS.update("vk", ch)
            what = {"gif": "GIF", "video": "видео"}.get(kind, "фото")
            report(progress=1.0, message=f"Пробный прогон прошёл: текст и {what} вставились" +
                   (", второй шаг открылся" if res.get("shot2") else "") + ". «Опубликовать» не нажимала, окно закрыла")
        return self.jobs.start("vk:auth", run)

    def pub_vk_token(self, token: str):
        """Готовый ключ доступа пользователя ВК (вместо входа через VK ID)."""
        import re as _re
        from app.publish import settings as PS
        token = (token or "").strip()
        m = _re.search(r"access_token=([^&\s]+)", token)   # можно вставить адрес целиком
        if m:
            token = m.group(1)
        if len(token) < 30:
            raise ValueError("Это не похоже на ключ доступа ВК — он длинный, обычно начинается с vk1.a.")
        PS.update("vk", {"access_token": token})
        return self.pub_settings()

    def pub_vk_logout(self):
        from app.publish import settings as PS
        PS.update("vk", {"access_token": ""})
        import json as _json
        import core.common as cc
        import app.publish.runner  # noqa: F401
        p = cc.TOKENS_PATH
        if p.exists():
            data = _json.loads(p.read_text(encoding="utf-8"))
            data.pop("vk", None)
            p.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.pub_settings()

    def post_prep(self, ids: list):
        """Готовы ли лёгкие версии файлов (готовятся заранее, пока пишется текст)."""
        from app.publish import prep, settings as PS
        from app.publish import uploads
        light = PS.load()["light"]
        out = {}
        for i in ids or []:
            st = prep.status(i, light)
            if st == "none":   # рецепт поменяли после добавления файла — готовим заново
                try:
                    if uploads.path_of(i).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"):
                        prep.ensure_async(i, light)
                        st = prep.status(i, light)
                except ValueError:
                    pass
            out[i] = st
        return out

    def pick_folder(self, start: str = ""):
        """Окно выбора папки (Windows). Возвращает путь или ""."""
        if self.window is None:
            return ""
        import webview
        res = self.window.create_file_dialog(webview.FOLDER_DIALOG, directory=start or "")
        if not res:
            return ""
        return res[0] if isinstance(res, (list, tuple)) else str(res)

    # --- готовые файлы: JPEG-превью, GIF, видео ---
    def outputs_get(self):
        from app.publish import outputs
        return outputs.info()

    def outputs_set(self, folder: str = "", by_date: bool = False, jpeg: bool = True, gif: bool = True, video: bool = True, move: bool = False):
        """Папка готовых файлов (пусто — по умолчанию), по датам, какие виды сохранять; move — перенести уже сохранённое."""
        from app.publish import outputs
        return outputs.apply(folder, by_date, {"jpeg": jpeg, "gif": gif, "video": video}, move)

    def outputs_open(self):
        from app.publish import outputs
        return outputs.open_folder()

    # --- e6ai и теггер ---
    def pub_e6ai(self, username: str = "", api_key: str = ""):
        """Проверить и сохранить вход на e6ai (пустой ключ — оставить сохранённый)."""
        from app.publish import e6ai as E6, settings as PS
        cur = PS.load()["e6ai"]
        cfg = dict(cur, username=(username or cur["username"]).strip(), api_key=(api_key or cur["api_key"]).strip())
        info = E6.check(cfg)
        PS.update("e6ai", PS.site_changes({"username": cfg["username"], "api_key": cfg["api_key"], "enabled": True}))
        out = self.pub_settings()
        out["e6ai_check"] = info
        return out

    def pub_e6_site(self, site: str):
        """Куда загружать: e6ai или e621. Аккаунт, ключ и постоянные теги у каждого сайта свои."""
        from app.publish import settings as PS
        if site not in PS.BOORU:
            raise ValueError("Неизвестный сайт")
        PS.update("e6ai", {"site": site})
        from app import e6tags
        e6tags.reset_site()
        return self.pub_settings()

    def tagger_status(self):
        from app.tagger import jtp
        st = jtp.status()
        st["job"] = self.jobs.get("tagger:install")
        st["run_job"] = self.jobs.get("tagger:run")
        from app.tagger import remote
        st["paused"] = remote.paused
        return st

    def tagger_pause(self, on: bool = True):
        """Кнопка «Теггер» справа вверху: выключить/включить работу теггера сейчас (модуль «Подбор тегов» не трогает)."""
        from app.tagger import remote
        remote.set_paused(bool(on))
        return self.tagger_status()

    # --- ffmpeg для GIF и видео ---
    def media_status(self):
        from app.publish import media
        st = media.status()
        st["job"] = self.jobs.get("media:install")
        return st

    def media_install(self):
        from app.publish import media
        return self.jobs.start("media:install", media.install)

    # --- редактор видео и GIF (один за раз; готовый файл становится обычной загрузкой нового поста) ---
    _edit: dict = {}

    def media_editor_open(self, file_id: str):
        """Подготовить окно редактора: лента кадров и файл для проигрывания. Исходник не меняется."""
        from app.publish import media, uploads
        if not media.ready():
            raise ValueError("Для правки видео нужен ffmpeg — «Настройки» → «Видео и GIF» → «Установить ffmpeg»")
        src = uploads.path_of(file_id)
        d = uploads.describe(src)
        if d["kind"] not in ("video", "gif"):
            raise ValueError("Правится только видео или GIF")
        info = media.probe(src)
        if not info.get("duration"):
            raise ValueError("Не удалось прочитать длительность файла")
        media.strip(file_id, src)
        media.preview(file_id, src)
        return {"kind": d["kind"], "name": d["name"], "size": d["size"], "duration": info["duration"], "width": info.get("width"),
                "height": info.get("height"), "fps": info.get("fps"), "has_audio": bool(info.get("acodec")) and d["kind"] == "video"}

    def media_edit_start(self, file_id: str, start: float = 0, end: float = 0, rotate: int = 0, flip: bool = False,
                         crop: dict | None = None, short: int = 0, gif: bool = False, fps: int = 15, max_mb: int = 0, loop: dict | None = None):
        from app.publish import media, uploads
        if not media.ready():
            raise ValueError("Нужен ffmpeg — «Настройки» → «Видео и GIF» → «Установить ffmpeg»")
        src = uploads.path_of(file_id)
        if uploads.describe(src)["kind"] not in ("video", "gif"):
            raise ValueError("Правится только видео или GIF")
        if int(rotate) % 360 not in (0, 90, 180, 270) or (short and int(short) not in media.SHORT_SIDES) or int(fps) not in media.GIF_FPS:
            raise ValueError("Недопустимые параметры правки")
        o = {"start": float(start or 0), "end": float(end or 0), "rotate": int(rotate), "flip": bool(flip), "crop": crop or None,
             "short": int(short or 0), "gif": bool(gif), "fps": int(fps), "max_bytes": int(max_mb) * 1024 * 1024 if max_mb else 0, "loop": loop or None}
        try:
            media.loop_params(loop, max(0.5, (float(end) or 3600) - float(start or 0)) if end else 3600)   # название режима и перехода — до запуска
        except RuntimeError as e:
            raise ValueError(str(e))
        new_id, dst = uploads.new_slot(src.stem + (".gif" if gif else ".mp4"))

        def run(report):
            try:
                res = media.edit(src, dst, o, report)
                self._edit.update(res=res)
                self._keep_edit(src, dst, new_id, gif, loop)
                report(progress=1.0, message=f"Готово: {res['size'] / 1048576:.1f} МБ".replace(".", ","))
            except Exception:
                shutil.rmtree(dst.parent, ignore_errors=True)
                raise

        if not self.jobs.start("media:edit", run):
            shutil.rmtree(dst.parent, ignore_errors=True)
            raise ValueError("Правка уже идёт — дождитесь конца")
        self._edit.clear()
        self._edit.update(src=file_id, id=new_id)
        return {"id": new_id}

    @staticmethod
    def _keep_edit(src, dst, new_id, gif, loop):
        """Результат правки — копией в папку готовых файлов: «имя.gif» из видео, «имя_edit.*» при той же форме, «имя_loop.*» с петлёй."""
        from pathlib import Path
        from app.publish import outputs
        was_gif = Path(src).suffix.lower() == ".gif"
        looped = bool(loop and loop.get("mode") not in (None, "none"))
        suffix = "_loop" if looped else ("" if gif and not was_gif else "_edit")
        outputs.save("gif" if gif else "video", dst, f"{Path(src).stem}{suffix}{Path(dst).suffix}", f"{new_id}|edit")

    def media_edit_preview(self, file_id: str, start: float = 0, end: float = 0, rotate: int = 0, flip: bool = False,
                           crop: dict | None = None, short: int = 0, gif: bool = False, fps: int = 15, loop: dict | None = None):
        """Быстрый беззвучный просмотр петли (WebM до 360p) с теми же правками — чтобы увидеть шов до «Готово»."""
        from app.publish import media, uploads
        from app.paths import sub
        if not media.ready():
            raise ValueError("Нужен ffmpeg — «Настройки» → «Видео и GIF» → «Установить ffmpeg»")
        src = uploads.path_of(file_id)
        if int(rotate) % 360 not in (0, 90, 180, 270):
            raise ValueError("Недопустимые параметры правки")
        o = {"start": float(start or 0), "end": float(end or 0), "rotate": int(rotate), "flip": bool(flip), "crop": crop or None,
             "short": 360, "gif": bool(gif), "fps": int(fps), "loop": loop or None, "preview": True}
        dst = sub("uploads") / f"{file_id}.looprev.webm"

        def run(report):
            media.edit(src, dst, o, report)

        if not self.jobs.start("media:editprev", run):
            raise ValueError("Просмотр уже готовится — подождите секунду")
        return {"ok": True}

    def media_edit_preview_status(self):
        return {"job": self.jobs.get("media:editprev")}

    def media_edit_status(self):
        """Идёт ли правка; когда готова — описание нового файла (как после загрузки)."""
        from app.publish import uploads
        job = self.jobs.get("media:edit")
        out = {"job": job, "src": self._edit.get("src")}
        if job and job["state"] == "done" and self._edit.get("id"):
            p = uploads.path_of(self._edit["id"])
            out["file"] = dict(uploads.describe(p), id=self._edit["id"])
            out["result"] = self._edit.get("res")
        return out

    def tagger_install(self, reinstall: bool = False, allow_cpu: bool = False):
        """Установить теггер. reinstall — заново поставить PyTorch под видеокарту и драйвер; allow_cpu — согласие на процессор."""
        from app.tagger import jtp
        if allow_cpu:
            self.settings_save({"tagger_cpu_ok": True})
        return self.jobs.start("tagger:install", lambda report: jtp.install(report, bool(reinstall), bool(allow_cpu)))

    def tagger_check(self):
        """Проверить заново: видеокарта, драйвер, видит ли её PyTorch (в отдельном процессе)."""
        from app.tagger import jtp
        jtp._choice["v"] = None
        if jtp.libs_ok()["torch"]:
            jtp.probe()
        return self.tagger_status()

    def tagger_unload(self):
        """Закрыть процесс теггера сейчас — освободить память и видеопамять."""
        from app.tagger import remote
        remote.stop()
        return self.tagger_status()

    def tagger_cpu_ok(self, on: bool = True):
        """Согласие подбирать теги на процессоре (медленно), когда видеокарта не используется."""
        self.settings_save({"tagger_cpu_ok": bool(on)})
        return self.tagger_status()

    # --- производительность ---
    def perf_status(self):
        from app import perf
        return {"now": perf.snapshot(), "summary": perf.summary(24)}

    def perf_report(self):
        """Сохранить отчёт о производительности (без ключей и текстов постов) и открыть папку."""
        from app import perf
        f = perf.report()
        try:
            self._open_folder(str(__import__("pathlib").Path(f).parent))
        except Exception:  # noqa: BLE001 — нет проводника (тесты) — не страшно
            pass
        return {"file": f}

    def perf_kill_browsers(self):
        from app import perf
        return {"killed": perf.kill_stray_browsers()}

    def tagger_run(self, ids: list):
        """Подобрать теги для файлов (в фоне; результаты кешируются рядом с файлами)."""
        from app.tagger import jtp
        from app.publish import uploads
        from app.paths import sub
        import json as _json
        if not self._mod("tagger"):
            return None   # теггер выключен в «Модулях»
        from app.tagger import remote as _rm
        if _rm.paused:
            return None   # выключен кнопкой «Теггер» справа вверху
        todo = [i for i in ids or [] if not (sub("uploads") / f"{i}.tags.json").exists()]
        if not todo:
            return True

        def run(report):
            for n, i in enumerate(todo):
                report(progress=n / len(todo), message=f"Подбираю теги: {n + 1} из {len(todo)}"
                       + (" (первый раз модель загружается в видеокарту — до минуты)" if n == 0 and not __import__("app.tagger.remote", fromlist=["x"]).alive() else ""))
                try:
                    path = uploads.still(i)   # у видео — кадр из него
                except ValueError:
                    continue
                if path is None:   # видео без ffmpeg — кадр не достать, теги заполнят вручную
                    (sub("uploads") / f"{i}.tags.json").write_text("[]", encoding="utf-8")
                    continue
                from app.tagger import remote
                res = remote.predict(path, 0.1)   # модель — в отдельном процессе, закроется сама после простоя
                (sub("uploads") / f"{i}.tags.json").write_text(_json.dumps(res), encoding="utf-8")
            report(progress=1.0, message="Теги подобраны")
        if not jtp.status()["ready"]:
            raise ValueError("Теггер не установлен — «Настройки» → «Теги» → «Установить теггер»")
        if jtp.on_cpu() and not settings.load().get("tagger_cpu_ok"):
            return {"cpu": True}   # видеокарта не используется — без согласия не грузим процессор
        started = self.jobs.start("tagger:run", run)
        if not started:   # уже идёт — допишем позже (интерфейс спросит ещё раз)
            return False
        return True

    def tag_cats(self, tags: list):
        """Категории тегов e6ai (директор, персонаж, вид…) — для цвета и группировки в интерфейсе."""
        from app.publish import e6check
        return e6check.cats(tags or [])

    def post_tags(self, ids: list):
        """Подсказки теггера для картинок, уже переведённые в теги e6ai (переименования, без несуществующих)."""
        from app.paths import sub
        from app import e6tags
        from app.publish import settings as PS
        import json as _json
        never = PS.load()["e6ai"].get("never_suggest") or []
        out = {}
        for i in ids or []:
            f = sub("uploads") / f"{i}.tags.json"
            out[i] = e6tags.normalize_preds(_json.loads(f.read_text(encoding="utf-8")), never) if f.exists() else None
        return out

    # --- чему научились по правкам модераторов e6ai ---
    def e6_learned(self):
        from app.insights import e6learn
        from app.publish import settings as PS
        L = e6learn.learned()
        return {"removed": sorted([t for t, v in L.items() if v["removed"] > v["added"]], key=lambda t: -L[t]["removed"]),
                "added": sorted([t for t, v in L.items() if v["added"] >= v["removed"] and v["added"]], key=lambda t: -L[t]["added"]),
                "counts": L, "never": PS.load()["e6ai"].get("never_suggest") or []}

    def e6_forget(self, tag: str = ""):
        from app.insights import e6learn
        e6learn.forget(tag or None)
        return self.e6_learned()

    def e6_never(self, tags: list):
        from app.publish import settings as PS
        clean = sorted({str(t).strip().lower().replace(" ", "_") for t in tags or [] if str(t).strip()})
        PS.update("e6ai", {"never_suggest": clean})
        return self.e6_learned()

    def e6_check_now(self):
        """Сверить теги с e6ai сейчас (для подходящих по сроку постов)."""
        from app.insights import e6learn
        return {"checked": e6learn.check_due()}

    # --- база тегов e6ai и форма тегов ---
    def e6db_status(self):
        from app import e6tags
        st = e6tags.status()
        st["job"] = self.jobs.get("e6db")
        return st

    def e6db_update(self):
        from app import e6tags
        ua = dumps.user_agent(self._nick())
        from app import subproc
        site = e6tags.site()
        tf = self._e6_tags_dump(site)
        return self.jobs.start("e6db", lambda report: subproc.run(report, "app.trends.jobs:e6db", ua, str(tf) if tf else None, site))

    def _e6_tags_dump(self, site: str = "e6ai"):
        """Файл тегов из выгрузки для трендов, если он сегодняшний — второй раз не качаем."""
        from datetime import date
        loc = dumps.local_latest(site)
        if loc and loc[0] == date.today().isoformat():
            return loc[2]
        return None

    def tag_search(self, q: str, cat: str = "", limit: int = 12):
        from app import e6tags
        return e6tags.search(q, cat or None, int(limit))

    def e6_initial(self, ids: list):
        """Начальный выбор тегов для картинок по подсказкам теггера (только уверенное, без мелких деталей)."""
        from app.publish import e6check, settings as PS
        pre = PS.load()["e6ai"].get("preselect", 0.35)
        preds = self.post_tags(ids)
        return {i: (e6check.initial(p, pre) if p is not None else None) for i, p in preds.items()}

    def e6_form(self, items: list, rating: str = ""):
        """Строки компактной формы и проверка для каждой картинки: items = [{id, tags, meta}]."""
        from app.publish import e6check, settings as PS
        e6 = PS.load()["e6ai"]
        from app.publish import uploads
        preds = self.post_tags([x["id"] for x in items or []])

        def consts(fid):   # у анимации e6ai ждёт тег animated — добавим сами
            c = list(e6.get("constant_tags") or [])
            try:
                if uploads.describe(uploads.path_of(fid))["kind"] in ("gif", "video"):
                    c.append("animated")
            except (ValueError, OSError):
                pass
            return c
        return {x["id"]: e6check.form(preds.get(x["id"]), x.get("tags") or [], consts(x["id"]), x.get("meta") or {},
                                      rating, e6.get("show_min", 0.2), e6.get("preselect", 0.35)) for x in items or []}

    # --- публикация: новый пост ---
    def post_preupload(self, ids: list):
        """Залить PNG-оригиналы в Telegram заранее, пока пишется пост (только при связи MTProto)."""
        from app.publish import settings as PS, uploads
        tg_bot = self._tg_ctx()
        cfg = PS.load()["telegram"]
        if not tg_bot.can_preupload(cfg):
            return {}
        files = []
        for i in ids or []:
            try:
                p = uploads.path_of(i)
            except ValueError:
                continue
            if uploads.describe(p)["kind"] == "image":
                files.append((i, p))
        return tg_bot.preupload(cfg, files)

    def post_plan(self, draft: dict):
        from app.publish import runner
        return runner.plan(draft)

    def post_publish(self, draft: dict):
        from app.publish import runner
        st = self.jobs.get("publish")
        if st and st["state"] == "running":
            raise ValueError("Предыдущая публикация ещё идёт")
        p = runner.plan(draft)
        if p["errors"]:
            raise ValueError(p["errors"][0])
        self._tg_ctx()
        return self.jobs.start("publish", lambda report: runner.start(draft, report))

    def post_schedule(self, draft: dict, run_at: str):
        """Запланировать пост на дату (ISO, с часовым поясом или по времени этого компьютера)."""
        from app.publish import schedule
        self._tg_ctx()
        return schedule.add(draft, run_at)

    def post_scheduled(self):
        from app.publish import schedule
        return schedule.items()

    def post_schedule_cancel(self, sid: int):
        from app.publish import schedule
        schedule.cancel(sid)
        return schedule.items()

    def post_schedule_move(self, sid: int, run_at: str):
        from app.publish import schedule
        schedule.reschedule(sid, run_at)
        return schedule.items()

    def post_schedule_now(self, sid: int):
        from app import worker
        from app.publish import schedule
        schedule.publish_now(sid)
        if not worker.status()["running"]:   # фоновой отправки нет — публикуем из окна
            threading.Thread(target=schedule.tick, args=(self,), daemon=True).start()
        return schedule.items()

    # --- конвейер: очередь постов, время выбирает программа ---
    def pipeline_state(self):
        from app.publish import pipeline
        pipeline.maintain()
        return pipeline.state()

    def pipeline_add(self, draft: dict):
        """Положить пост в конвейер (время выберет программа по статистике)."""
        from app.publish import pipeline
        self._tg_ctx()
        return pipeline.add(draft)

    def pipeline_remove(self, sid: int):
        from app.publish import pipeline
        pipeline.remove(sid)
        return pipeline.state()

    def pipeline_move(self, sid: int, direction: int):
        from app.publish import pipeline
        pipeline.move(sid, direction)
        return pipeline.state()

    def pipeline_pin(self, sid: int, run_at: str = ""):
        """Закрепить время поста (ISO) или снять закрепление (пустая строка)."""
        from app.publish import pipeline
        pipeline.pin(sid, run_at or None)
        return pipeline.state()

    def pipeline_set(self, changes: dict):
        from app.publish import pipeline
        pipeline.set_cfg(changes)
        return pipeline.state()

    # --- фоновая отправка ---
    def worker_status(self):
        from app import worker
        return worker.status()

    def worker_start(self):
        from app import worker
        worker.start_detached()
        import time as _t
        for _ in range(20):   # подождём первый «пульс» (до 10 с)
            _t.sleep(0.5)
            st = worker.status()
            if st["running"]:
                return st
        return worker.status()

    def worker_stop(self):
        from app import worker
        worker.request_stop()
        return worker.status()

    def worker_autostart(self, on: bool):
        from app import worker
        worker.set_autostart(bool(on))
        return worker.status()

    def post_best_times(self):
        if not self._mod("forecast"):
            return []
        from app.publish import schedule
        return schedule.best_times()

    def post_extend_draft(self, pub_id: int):
        """Черновик опубликованного поста для досылки на другие площадки."""
        from app.publish import runner
        return runner.extend_draft(int(pub_id))

    def post_retry(self):
        from app.publish import runner
        self._tg_ctx()
        return self.jobs.start("publish", runner.retry)

    def post_resend_originals(self, pub_id: int):
        """Дослать PNG-оригиналы к уже вышедшему посту Telegram (нового поста не создаёт)."""
        from app.publish import runner
        self._tg_ctx()
        return self.jobs.start("publish", lambda report: runner.resend_originals(int(pub_id), report))

    def post_state(self):
        from app.publish import runner
        return {"job": self.jobs.get("publish"), "state": runner.state()}

    def post_history(self):
        from app.publish import runner
        return runner.history()

    def post_remove_file(self, file_id: str):
        from app.publish import storage, uploads
        if file_id in storage.used_ids():   # файл опубликованного или запланированного поста — убираем только из черновика
            return True
        uploads.remove(file_id)
        return True

    # --- статистика ---
    def stats_exclude(self, post_id=None, excluded: bool = True):
        stats.set_excluded(post_id, excluded)
        return True

    def stats_overview(self, period: str = "90", chat_id: str | None = None):
        """Сразу — последний сохранённый результат; если данные поменялись, пересчёт идёт в фоне."""
        from app import cache
        from app.insights import warmup as W
        r = cache.get(W.stats_name(period, chat_id or None), W.stats_compute(period, chat_id or None), W.stats_key())
        out = r["data"]
        if isinstance(out, dict):
            out = dict(out, _cache={"at": r["at"], "stale": r["stale"], "refreshing": r["refreshing"]})
        return out

    # --- аналитика: прогноз, теги истории, статистика поста ---
    def insights_overview(self):
        from app import cache
        from app.insights import history_tags, warmup as W
        r = cache.get("insights", W.insights_compute, W.insights_key())
        out = dict(r["data"] or {}, _cache={"at": r["at"], "stale": r["stale"], "refreshing": r["refreshing"]})
        out["tagging"] = history_tags.status()
        out["tag_job"] = self.jobs.get("insights:tag")
        return out

    def insights_tag_history(self):
        """Разметить теггером превью старых постов (в фоне, один раз; потом — только новые)."""
        if not self._mod("tagger"):
            raise ValueError("Подбор тегов выключен в «Настройки» → «Функции»")
        if __import__("app.tagger.remote", fromlist=["x"]).paused:
            raise ValueError("Теггер выключен кнопкой «Теггер» справа вверху — включите её")
        from app import subproc
        def run(report):   # модель теггера грузится в отдельный процесс и уходит из памяти вместе с ним
            subproc.run(report, "app.trends.jobs:history_tags")
            from app.insights import warmup
            warmup.warm_async()
        return self.jobs.start("insights:tag", run)

    def post_forecast(self, draft: dict, run_at: str = ""):
        if not self._mod("forecast"):
            return {"off": True}
        from datetime import datetime as _dt
        from app.insights import forecast as FC
        from app.tagger import jtp
        when = None
        if run_at:
            when = _dt.fromisoformat(run_at.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.astimezone()
        out = FC.for_draft(draft, when)
        # теги картинок для прогноза: если теггер стоит, а этот пост он ещё не смотрел — запускаем
        if (not out.get("tags_known") and out.get("items") and jtp.status()["ready"]
                and draft.get("tagger", True)):   # переключатель «Теггер» справа вверху
            ids = [f for f in draft.get("files") or []]
            out["tagging"] = bool(ids) and self.tagger_run(ids) is not None
        return out

    def pub_stats(self, pub_id: int):
        from app.insights import post_stats
        return post_stats.for_publication(int(pub_id))

    def pub_stats_summary(self, ids: list | None = None):
        """Главная цифра и индекс каждой публикации — из сохранённого, пересчёт в фоне."""
        from app import cache
        from app.insights import warmup as W
        r = cache.get("hist_summary", W.summary_compute, W.calendar_key(), max_age=1800)
        return r["data"]

    def storage_info(self):
        from app.publish import storage
        return storage.usage()

    def storage_free(self):
        """«Освободить сейчас»: убрать тяжёлые копии всех уже опубликованных постов (запланированные не трогаем)."""
        from app.publish import storage
        r = storage.cleanup(everything_published=True)
        return dict(r, usage=storage.usage())

    def calendar_month(self, ym: str):
        """Месяц для календаря «Истории»: работы по дням, запланированное, лидеры месяца."""
        import re as _re
        from app import cache
        from app.insights import warmup as W
        if not _re.fullmatch(r"\d{4}-\d{2}", ym or ""):
            raise ValueError("Месяц в виде ГГГГ-ММ")
        from app.insights import ext_posts
        try:
            ext_posts.mirror_vk()   # свежие замеры ВК — сразу, без ожидания 3-часового круга (локально, без сети)
        except Exception:  # noqa: BLE001
            from app.applog import log
            log("календарь: не удалось подтянуть замеры ВК")
        ext_posts.sync_async()   # посты ВК и Bluesky, вышедшие мимо Crosspost, — раз в 3 часа, в фоне
        r = cache.get(W.calendar_name(ym), W.calendar_compute(ym), W.calendar_key(), max_age=1800)
        return dict(r["data"], _cache={"at": r["at"], "stale": r["stale"], "refreshing": r["refreshing"]},
                    ext=ext_posts.state())

    def cal_merge(self, atoms: list):
        """Объединить выбранные работы календаря в одну (атомы — исходные посты)."""
        from app.insights import calendar
        return calendar.merge(atoms)

    def cal_split(self, atoms: list):
        """Разъединить работу календаря на отдельные посты."""
        from app.insights import calendar
        return calendar.split(atoms)

    def cal_decide(self, a: list, b: list, yes: bool):
        """Ответ на подсказку «Похоже на тот же арт»."""
        from app.insights import calendar
        return calendar.decide(a, b, bool(yes))

    def post_detail(self, post_id: int):
        """Статистика импортированного поста (без публикации из Crosspost) — для шторки календаря."""
        from app.insights import post_stats
        return post_stats.for_post(int(post_id))

    def post_stats_refresh(self, post_id: int):
        from app.insights import post_stats
        return self.jobs.start(f"poststats:{int(post_id)}", lambda report: post_stats.refresh_post(self.tg, int(post_id), report))

    def pub_stats_refresh(self, pub_id: int):
        """Снять цифры поста прямо сейчас (Telegram, через вход по API)."""
        from app.insights import post_stats
        from app.publish import settings as PS, vk_stats

        def run(report):
            post_stats.refresh_publication(self.tg, int(pub_id), report)
            import json as _json
            rows = post_stats._pubs(pub_id=int(pub_id))
            vk = PS.load(raw=True)["vk"]
            dests = _json.loads(rows[0]["dests"] or "[]") if rows else []
            if any(d.get("key") == "vk" and d.get("state") == "done" for d in dests) and vk.get("browser_ok"):
                report(message="Обновляю цифры ВК (сбор статистики через браузер)…")
                try:
                    vk_stats.run_and_record(headless=not vk.get("show_window", True), wait=60)
                    report(progress=1.0, message="Цифры обновлены (Telegram и ВК)")
                except Exception as e:  # noqa: BLE001 — Telegram уже обновлён, ВК — по возможности
                    report(progress=1.0, message=f"Telegram обновлён, ВК не вышло: {str(e)[:160]}")
        return self.jobs.start(f"pubstats:{int(pub_id)}", run)
