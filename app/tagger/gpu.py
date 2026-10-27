"""Видеокарта и драйвер NVIDIA → какую сборку PyTorch ставить для теггера.

Сборка под CUDA 12.8 (cu128) нужна RTX 50xx и работает на RTX 20xx/30xx/40xx — но только со свежим драйвером.
Со старым драйвером PyTorch не видит видеокарту, и теггер молча уходит на процессор (медленно и тяжело для ПК).
Поэтому под драйвер подбираем сборку, а если её нет — просим обновить драйвер.

Пороги — минимальные драйверы NVIDIA для Windows под каждую версию CUDA (с запасом).
Видеокарту узнаём через nvidia-smi: он ставится вместе с драйвером, PyTorch для этого не нужен.
"""
import json
import re
import shutil
import subprocess
import sys

INDEX = "https://download.pytorch.org/whl/"
# (сборка, минимальный драйвер, минимальная архитектура, максимальная архитектура)
BUILDS = [("cu128", 570.0, 7.5, 12.9), ("cu126", 560.0, 5.0, 9.0), ("cu118", 522.0, 5.0, 9.0)]
NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _smi() -> str | None:
    exe = shutil.which("nvidia-smi")
    if not exe and sys.platform == "win32":
        for p in (r"C:\Windows\System32\nvidia-smi.exe", r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
            if __import__("os").path.exists(p):
                exe = p
                break
    return exe


def _arch_from_name(name: str) -> float | None:
    """Если nvidia-smi старый и не говорит архитектуру — по названию карты."""
    n = name.upper()
    for pat, cc in ((r"RTX\s*50\d\d", 12.0), (r"RTX\s*40\d\d", 8.9), (r"RTX\s*30\d\d", 8.6), (r"RTX\s*20\d\d", 7.5),
                    (r"GTX\s*16\d\d", 7.5), (r"GTX\s*10\d\d", 6.1), (r"GTX\s*9\d\d", 5.2), (r"RTX\s*A\d{4}", 8.6)):
        if re.search(pat, n):
            return cc
    return None


def detect() -> dict:
    """{"nvidia": bool, "gpus": [{"name", "driver", "arch", "vram_gb"}], "driver": float|None, "error"}."""
    exe = _smi()
    if not exe:
        return {"nvidia": False, "gpus": [], "driver": None}
    rows = []
    for q in ("name,driver_version,compute_cap,memory.total", "name,driver_version,memory.total"):
        try:
            r = subprocess.run([exe, f"--query-gpu={q}", "--format=csv,noheader,nounits"], capture_output=True,
                               text=True, timeout=15, creationflags=NOWIN)
        except Exception as e:  # noqa: BLE001
            return {"nvidia": False, "gpus": [], "driver": None, "error": str(e)[:200]}
        if r.returncode == 0 and r.stdout.strip():
            for line in r.stdout.strip().splitlines():
                parts = [x.strip() for x in line.split(",")]
                if "compute_cap" in q:
                    name, drv, cc, mem = (parts + ["", "", "", ""])[:4]
                else:
                    name, drv, mem = (parts + ["", "", ""])[:3]
                    cc = ""
                try:
                    arch = float(cc)
                except ValueError:
                    arch = _arch_from_name(name)
                try:
                    vram = round(float(mem) / 1024, 1)
                except ValueError:
                    vram = None
                rows.append({"name": name, "driver": drv, "arch": arch, "vram_gb": vram})
            break
    drv = None
    if rows:
        m = re.match(r"(\d+)\.(\d+)", rows[0]["driver"] or "")
        drv = float(f"{m.group(1)}.{m.group(2)}") if m else None
    return {"nvidia": bool(rows), "gpus": rows, "driver": drv}


def choose(info: dict | None = None) -> dict:
    """Какую сборку PyTorch ставить: {"build", "index", "gpu", "ok", "why", "advice"}."""
    info = info or detect()
    if not info.get("nvidia"):
        return {"build": "cpu", "index": INDEX + "cpu", "gpu": None, "ok": False,
                "why": "видеокарта NVIDIA не найдена (или не установлен её драйвер)",
                "advice": "Теггер будет работать на процессоре — это медленно (десятки секунд на картинку) и нагружает компьютер."}
    g = max(info["gpus"], key=lambda x: x.get("arch") or 0)
    arch, drv = g.get("arch"), info.get("driver")
    if arch is not None and arch < 5.0:
        return {"build": "cpu", "index": INDEX + "cpu", "gpu": g, "ok": False,
                "why": f"{g['name']} слишком старая для современного PyTorch",
                "advice": "Теггер будет работать на процессоре — медленно."}
    for build, min_drv, lo, hi in BUILDS:
        if arch is not None and not (lo <= arch <= hi):
            continue
        if drv is not None and drv < min_drv:
            continue
        return {"build": build, "index": INDEX + build, "gpu": g, "ok": True, "why": "", "advice": ""}
    # подходящей сборки под этот драйвер нет
    need = min((d for b, d, lo, hi in BUILDS if arch is None or lo <= arch <= hi), default=570.0)   # самый ранний подходящий
    return {"build": None, "index": None, "gpu": g, "ok": False,
            "why": f"драйвер NVIDIA {drv or '?'} слишком старый для {g['name']}",
            "advice": f"Обновите драйвер NVIDIA до версии {int(need)} или новее (лучше — до последней) (GeForce Experience / NVIDIA App или nvidia.com), "
                      "затем нажмите здесь «Проверить видеокарту»."}


if __name__ == "__main__":   # python -m app.tagger.gpu — для отладки
    d = detect()
    print(json.dumps({"detect": d, "choose": choose(d)}, ensure_ascii=False, indent=1))
