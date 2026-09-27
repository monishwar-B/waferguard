# PyInstaller spec for the WaferGuard desktop app (one-folder build; the installer wraps it).
#   pyinstaller packaging/waferguard.spec --noconfirm
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [
    (os.path.join(ROOT, "waferguard", "api", "static"), "waferguard/api/static"),
    (os.path.join(ROOT, "models", "wafer-ensemble"), "models/wafer-ensemble"),
    (os.path.join(ROOT, "deploy", "configs"), "deploy/configs"),
    (os.path.join(ROOT, "sample_data"), "sample_data"),
]
binaries, hidden = [], []
for pkg in ("onnxruntime", "sklearn", "reportlab", "uvicorn", "webview", "cv2"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception:
        pass
hidden += collect_submodules("waferguard") + ["sqlalchemy.dialects.sqlite", "multipart"]

a = Analysis([os.path.join(ROOT, "waferguard", "desktop", "launcher.py")], pathex=[ROOT], binaries=binaries,
             datas=datas, hiddenimports=hidden,
             excludes=["tensorflow", "keras", "torch", "tf2onnx", "matplotlib", "IPython", "tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="WaferGuard", console=False,
          icon=os.path.join(SPECPATH, "waferguard.ico") if os.path.exists(os.path.join(SPECPATH, "waferguard.ico")) else None)
coll = COLLECT(exe, a.binaries, a.datas, name="WaferGuard")
