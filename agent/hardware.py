"""Detect graphics memory (or RAM) and pick a local model from the config table."""
import ctypes
import os
import platform
import shutil
import subprocess


def detect():
    """Return {'gpu': name or None, 'vram_gb': float or None, 'ram_gb': float or None}."""
    info = {"gpu": None, "vram_gb": None, "ram_gb": ram_gb()}
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10).stdout
            name, mib = out.strip().splitlines()[0].rsplit(",", 1)
            info["gpu"], info["vram_gb"] = name.strip(), round(int(mib) / 1024, 1)
        except Exception:
            pass
    elif platform.system() == "Darwin" and platform.machine() == "arm64":
        # Apple Silicon shares memory between CPU and GPU: about 2/3 usable by the GPU.
        info["gpu"] = "Apple Silicon (shared memory)"
        if info["ram_gb"]:
            info["vram_gb"] = round(info["ram_gb"] * 2 / 3, 1)
    return info


def ram_gb():
    try:
        if platform.system() == "Windows":
            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("pf_total", ctypes.c_ulonglong), ("pf_avail", ctypes.c_ulonglong),
                            ("v_total", ctypes.c_ulonglong), ("v_avail", ctypes.c_ulonglong),
                            ("ext", ctypes.c_ulonglong)]
            s = Status()
            s.length = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
            return round(s.total / 2**30, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except Exception:
        return None


def pick_model(table, info):
    """First row whose min_vram_gb fits. Without a GPU, half the RAM is used as the budget."""
    if info.get("vram_gb"):
        budget, source = info["vram_gb"], f"{info['gpu']} with {info['vram_gb']} GB of graphics memory"
    elif info.get("ram_gb"):
        budget = info["ram_gb"] / 2
        source = f"no graphics card found, {info['ram_gb']} GB of RAM (counting half)"
    else:
        budget, source = 0, "memory could not be detected"
    for row in sorted(table, key=lambda r: r["min_vram_gb"], reverse=True):
        if budget >= row["min_vram_gb"]:
            return row["use"], f"{source} → {row['use']} (rule: {row['min_vram_gb']} GB or more)"
    return None, f"{source}: no model in local_models fits"
