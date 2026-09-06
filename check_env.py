# -*- coding: utf-8 -*-
from pathlib import Path
import importlib
import shutil
import sys

ROOT = Path(__file__).resolve().parent

print("=" * 64)
print("社区食堂 DID 环境检查")
print("=" * 64)
print("Python:", sys.version.split()[0])
print("Python 路径:", sys.executable)
print("项目目录:", ROOT)
print()

checks = [
    ("numpy", "numpy"),
    ("pandas", "pandas"),
    ("scipy", "scipy"),
    ("matplotlib", "matplotlib"),
    ("scikit-learn", "sklearn"),
    ("statsmodels", "statsmodels"),
    ("duckdb", "duckdb"),
    ("linearmodels", "linearmodels"),
    ("pyarrow", "pyarrow"),
    ("openpyxl", "openpyxl"),
    ("jupyterlab", "jupyterlab"),
    ("ipykernel", "ipykernel"),
]

failed = []
for label, module in checks:
    try:
        m = importlib.import_module(module)
        version = getattr(m, "__version__", "OK")
        print(f"[OK]   {label:<16} {version}")
    except Exception as e:
        print(f"[FAIL] {label:<16} {e}")
        failed.append(label)

print()
rars = sorted(ROOT.glob("*.rar"))
if rars:
    print(f"[OK]   根目录发现 {len(rars)} 个 RAR：")
    for p in rars:
        print("       -", p.name)
else:
    print("[WARN] 根目录暂时没有 .rar 文件。运行分析前请把所有原始 RAR 放到根目录。")

extractors = [
    shutil.which("7z"),
    shutil.which("7z.exe"),
    shutil.which("WinRAR"),
    shutil.which("WinRAR.exe"),
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
    r"C:\Program Files\WinRAR\WinRAR.exe",
]
extractor = next((Path(x) for x in extractors if x and Path(x).exists()), None)
if extractor:
    print("[OK]   RAR 解压程序:", extractor)
elif shutil.which("tar") or shutil.which("tar.exe"):
    print("[WARN] 未发现 7-Zip/WinRAR；将尝试 Windows 自带 tar.exe。建议运行安装脚本安装 7-Zip。")
else:
    print("[WARN] 未发现 RAR 解压程序。请重新运行 01_一键安装环境.bat。")

try:
    total, used, free = shutil.disk_usage(ROOT)
    free_gb = free / 1024**3
    print(f"[INFO] 项目盘剩余空间: {free_gb:.1f} GB")
    if free_gb < 30:
        print("[WARN] 原始数据和 DuckDB 临时文件较大，建议至少预留 30-50 GB 空间。")
except Exception:
    pass

print()
if failed:
    print("环境检查失败，缺少:", ", ".join(failed))
    print("请重新运行 01_一键安装环境.bat。")
    sys.exit(1)

print("环境检查通过。")
print("下一步：")
print("  - Jupyter 版：双击 03_启动Jupyter.bat")
print("  - Python 版： 双击 04_运行Python版.bat")
