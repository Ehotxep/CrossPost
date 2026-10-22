"""Теггер JTP PILOT² (RedRocket, Apache-2.0): предсказывает теги e621 по картинке. Работает на видеокарте.

Устанавливается из приложения по кнопке: PyTorch под вашу видеокарту и драйвер (app/tagger/gpu.py: CUDA 12.8 —
RTX 50xx и свежие драйверы, 12.6 / 11.8 — драйверы постарше), timm, safetensors и файлы модели с HuggingFace (~1,7 ГБ).
Всё ставится в тот же Python, что и приложение. Сама модель работает в отдельном процессе (remote.py / serve.py) —
окно её в памяти не держит.

Обработка картинки — как в официальном inference_gradio.py: вписать в 384×384, прозрачность на серый,
нормализация 0.5/0.5, дополнить до квадрата. Голова модели «с затвором»: sigmoid(x) · sigmoid(gate).
"""
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path

from app.paths import sub

REPO = "https://huggingface.co/RedRocket/JointTaggerProject/resolve/main/JTP_PILOT2/"
FILES = {"weights": "JTP_PILOT2-e3-vit_so400m_patch14_siglip_384.safetensors", "tags": "tags.json"}
NUM_CLASSES = 9083
SIZE = 384
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"   # по умолчанию; на деле выбирается gpu.choose()
PACKAGES = ["timm>=1.0.9", "safetensors>=0.4"]

_lock = threading.Lock()
_model = None   # (model, tags, device, dtype)


def model_dir() -> Path:
    return sub("models") / "jtp_pilot2"


def libs_ok() -> dict:
    return {m: importlib.util.find_spec(m) is not None for m in ("torch", "torchvision", "timm", "safetensors")}


_choice = {"t": 0.0, "v": None}


def gpu_choice() -> dict:
    """Видеокарта, драйвер и подходящая сборка PyTorch (nvidia-smi — раз в 10 минут)."""
    import time
    if _choice["v"] is None or time.time() - _choice["t"] > 600:
        from app.tagger import gpu
        try:
            _choice["v"] = gpu.choose()
        except Exception as e:  # noqa: BLE001
            _choice["v"] = {"build": None, "ok": False, "why": f"не удалось узнать видеокарту: {e}", "advice": "", "gpu": None}
        _choice["t"] = time.time()
    return _choice["v"]


def _info_path():
    return sub("models") / "torch_info.json"


def torch_info() -> dict | None:
    """Что показала последняя проверка PyTorch: версия, CUDA, видна ли видеокарта (без загрузки torch в окно)."""
    try:
        return json.loads(_info_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


PROBE = r"""
import json
d = {}
try:
    import torch
    d = {"torch": torch.__version__, "cuda": torch.version.cuda, "available": bool(torch.cuda.is_available())}
    if d["available"]:
        d["device"] = torch.cuda.get_device_name(0)
        d["cap"] = list(torch.cuda.get_device_capability(0))
        d["archs"] = torch.cuda.get_arch_list()
        try:   # «no kernel image» вылезает только при настоящем расчёте
            x = torch.ones(64, 64, device="cuda", dtype=torch.float16)
            float((x @ x).sum())
            d["works"] = True
        except Exception as e:
            d["works"] = False
            d["error"] = str(e)[:300]
except Exception as e:
    d = {"error": str(e)[:300]}
print("PROBE" + json.dumps(d))
"""


def probe() -> dict:
    """Проверить PyTorch в отдельном процессе и запомнить результат."""
    from app import subproc
    try:
        r = subprocess.run([subproc.python_exe(), "-c", PROBE], capture_output=True, text=True, timeout=180,
                           creationflags=subproc.NOWIN, encoding="utf-8", errors="replace")
        line = next((x for x in (r.stdout or "").splitlines() if x.startswith("PROBE")), None)
        d = json.loads(line[5:]) if line else {"error": (r.stderr or "PyTorch не запустился")[-300:]}
    except Exception as e:  # noqa: BLE001
        d = {"error": str(e)[:300]}
    import time
    d["at"] = time.time()
    try:
        sub("models").mkdir(parents=True, exist_ok=True)
        _info_path().write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return d


def on_cpu() -> bool:
    """Теггер будет считать на процессоре (видеокарту PyTorch не видит или на ней не работает)."""
    t = torch_info()
    return bool(t) and not (t.get("available") and t.get("works", True))


def status() -> dict:
    libs = libs_ok()
    files = {k: (model_dir() / f).exists() for k, f in FILES.items()}
    ready = all(libs.values()) and all(files.values())
    t = torch_info() if libs["torch"] else None
    ch = gpu_choice()
    out = {"libs": libs, "files": files, "ready": ready,
           "loaded": _model is not None or _remote_alive(), "folder": str(model_dir()),
           "device": _model[2] if _model else _remote_device(), "torch": t, "choice": ch, "on_cpu": on_cpu() if ready else False}
    # PyTorch стоит, но видеокарту не видит, хотя она есть — скорее всего, сборка не под драйвер
    if ready and out["on_cpu"] and ch.get("gpu"):
        out["advice"] = ch.get("advice") or (f"Видеокарта {ch['gpu']['name']} есть, но PyTorch её не использует. "
                                             "Нажмите «Переустановить PyTorch» — поставится сборка под ваш драйвер.")
    return out


def _remote_alive():
    try:
        from app.tagger import remote
        return remote.alive()
    except Exception:
        return False


def _remote_device():
    try:
        from app.tagger import remote
        return remote.device()
    except Exception:
        return None


# --- установка ------------------------------------------------------------------

def _pip(args: list[str], report, label: str, lo: float, hi: float):
    cmd = [sys.executable.replace("pythonw.exe", "python.exe"), "-m", "pip", "install", "--disable-pip-version-check"] + args
    flags = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW: без чёрного окна консоли
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", creationflags=flags)
    tail = []
    n = 0
    for line in p.stdout:
        line = line.strip()
        if not line:
            continue
        tail = (tail + [line])[-15:]
        n += 1
        if line.startswith(("Collecting", "Downloading", "Installing", "Successfully")):
            report(progress=min(hi, lo + (hi - lo) * min(n / 60, 0.95)), message=f"{label}: {line[:120]}")
    if p.wait() != 0:
        raise RuntimeError(f"{label}: установка не удалась.\n" + "\n".join(tail[-6:]))


def _download(name: str, report, lo: float, hi: float):
    import requests
    dest = model_dir() / name
    tmp = dest.with_suffix(dest.suffix + ".part")
    have = tmp.stat().st_size if tmp.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with requests.get(REPO + name, headers=headers, stream=True, timeout=60) as r:
        if r.status_code == 416:   # уже скачано целиком
            tmp.replace(dest)
            return
        if r.status_code not in (200, 206):
            raise RuntimeError(f"HuggingFace ответил {r.status_code} на {name}")
        if r.status_code == 200:
            have = 0
        total = have + int(r.headers.get("Content-Length") or 0)
        with open(tmp, "ab" if have else "wb") as f:
            done = have
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    report(progress=lo + (hi - lo) * done / total,
                           message=f"Скачиваю модель: {done / 1e9:.2f} из {total / 1e9:.2f} ГБ")
    tmp.replace(dest)


def install(report, reinstall_torch: bool = False, allow_cpu: bool = False):
    """Ставит библиотеки и скачивает модель. Можно запускать повторно — уже готовое пропускается.
    reinstall_torch — заново поставить PyTorch под текущие видеокарту и драйвер (после обновления драйвера)."""
    model_dir().mkdir(parents=True, exist_ok=True)
    libs = libs_ok()
    _choice["v"] = None
    ch = gpu_choice()
    if not (libs["torch"] and libs["torchvision"]) or reinstall_torch:
        if ch.get("build") is None:
            raise RuntimeError(f"Не ставлю PyTorch: {ch.get('why')}. {ch.get('advice')}")
        if ch["build"] == "cpu" and not allow_cpu:
            raise RuntimeError(f"{ch.get('why').capitalize()}. {ch.get('advice')} "
                               "Если всё равно нужно — нажмите «Установить для процессора».")
        what = "для процессора (около 300 МБ)" if ch["build"] == "cpu" else f"под {ch['gpu']['name']} ({ch['build']}, около 3 ГБ, это самый долгий шаг)"
        report(progress=0.02, message=f"Ставлю PyTorch {what}…")
        extra = ["--upgrade", "--force-reinstall"] if reinstall_torch else []
        _pip(["torch", "torchvision", *extra, "--index-url", ch["index"]], report, "PyTorch", 0.02, 0.55)
    if not (libs["timm"] and libs["safetensors"]):
        report(progress=0.56, message="Ставлю timm и safetensors…")
        _pip(PACKAGES, report, "timm", 0.56, 0.6)
    importlib.invalidate_caches()
    if not (model_dir() / FILES["tags"]).exists():
        _download(FILES["tags"], report, 0.6, 0.61)
    if not (model_dir() / FILES["weights"]).exists():
        _download(FILES["weights"], report, 0.61, 0.97)
    report(progress=0.97, message="Проверяю PyTorch и видеокарту…")
    t = probe()
    report(progress=0.98, message="Пробую модель (в отдельном процессе — окно её в памяти не держит)…")
    from app.tagger import remote
    try:
        remote.stop()
        remote._start()   # загрузит модель тем же путём, что и при работе
        info = remote.device() or "cpu"
    finally:
        remote.stop()
    if info == "cpu" and ch.get("gpu"):
        report(progress=1.0, message=f"Теггер готов, но работает на процессоре: {ch.get('why') or t.get('error') or 'PyTorch не видит видеокарту'}")
    else:
        report(progress=1.0, message=f"Теггер готов ({'видеокарта ' + info if info != 'cpu' else 'процессор — будет медленно'})")


# --- модель -----------------------------------------------------------------------

def load() -> str:
    """Загружает модель один раз. Возвращает имя устройства."""
    global _model
    with _lock:
        if _model is not None:
            return _model[2]
        import safetensors.torch
        import timm
        import torch
        from torch import nn

        class GatedHead(nn.Module):
            def __init__(self, num_features: int, num_classes: int):
                super().__init__()
                self.num_classes = num_classes
                self.linear = nn.Linear(num_features, num_classes * 2)
                self.act = nn.Sigmoid()
                self.gate = nn.Sigmoid()

            def forward(self, x):
                x = self.linear(x)
                return self.act(x[:, :self.num_classes]) * self.gate(x[:, self.num_classes:])

        model = timm.create_model("vit_so400m_patch14_siglip_384.webli", pretrained=False, num_classes=NUM_CLASSES)
        model.head = GatedHead(min(model.head.weight.shape), NUM_CLASSES)
        safetensors.torch.load_model(model, str(model_dir() / FILES["weights"]))
        model.eval()
        device, dtype = "cpu", torch.float32
        if torch.cuda.is_available():
            device = "cuda"
            model = model.to(device)
            if torch.cuda.get_device_capability()[0] >= 7:
                dtype = torch.float16
                model = model.to(dtype=dtype, memory_format=torch.channels_last)
        raw = json.loads((model_dir() / FILES["tags"]).read_text(encoding="utf-8"))
        tags = list(raw.keys()) if isinstance(raw, dict) else list(raw)   # порядок ключей = номер выхода модели
        tags = [str(t).replace(" ", "_") for t in tags]
        name = torch.cuda.get_device_name(0) if device == "cuda" else "cpu"
        _model = (model, tags, name, (device, dtype))
        return name


def _prepare(path, torch):
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im.load()
        im = im.convert("RGBA")
    w, h = im.size
    s = SIZE / max(w, h)
    im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    import numpy as np
    a = torch.from_numpy(np.asarray(im, dtype=np.float32) / 255.0).permute(2, 0, 1)   # 4×H×W
    rgb, alpha = a[:3], a[3:4]
    rgb = rgb * alpha + 0.5 * (1 - alpha)          # прозрачность → серый 0.5
    rgb = (rgb - 0.5) / 0.5
    out = torch.zeros(3, SIZE, SIZE)               # дополнение до квадрата нулём (= серый после нормализации)
    y, x = (SIZE - rgb.shape[1]) // 2, (SIZE - rgb.shape[2]) // 2
    out[:, y:y + rgb.shape[1], x:x + rgb.shape[2]] = rgb
    return out


def predict(path, min_prob: float = 0.2, top: int = 250) -> list[tuple[str, float]]:
    import torch
    load()
    model, tags, _, (device, dtype) = _model
    x = _prepare(path, torch).unsqueeze(0).to(device=device, dtype=dtype)
    if device == "cuda":
        x = x.to(memory_format=torch.channels_last)
    with torch.no_grad():
        probs = model(x)[0].float().cpu()
    vals, idx = probs.topk(min(top, probs.shape[0]))
    return [(tags[i], round(float(v), 3)) for v, i in zip(vals.tolist(), idx.tolist()) if v >= min_prob and tags[i]]


def predict_batch(paths: list, min_prob: float = 0.3, top: int = 80, batch: int = 16) -> list[list[tuple[str, float]]]:
    """То же, что predict, но пачками — для разметки сотен старых постов (на видеокарте в разы быстрее)."""
    import torch
    load()
    model, tags, _, (device, dtype) = _model
    out = []
    for i in range(0, len(paths), batch):
        xs, ok = [], []
        for p in paths[i:i + batch]:
            try:
                xs.append(_prepare(p, torch))
                ok.append(True)
            except Exception:   # битая картинка — пропускаем, остальное считаем
                ok.append(False)
        res = iter([])
        if xs:
            x = torch.stack(xs).to(device=device, dtype=dtype)
            if device == "cuda":
                x = x.to(memory_format=torch.channels_last)
            with torch.no_grad():
                probs = model(x).float().cpu()
            vals, idx = probs.topk(min(top, probs.shape[1]), dim=1)
            res = iter([[(tags[j], round(float(v), 3)) for v, j in zip(vr.tolist(), ir.tolist()) if v >= min_prob and tags[j]]
                        for vr, ir in zip(vals, idx)])
        out += [next(res) if good else [] for good in ok]
    return out
