# PyInstaller build: one folder with DownloadDeck.exe (GUI) and native_host.exe (browser helper).
# Build:  .venv\Scripts\pyinstaller DownloadDeck.spec --noconfirm   (then run build_share.py)
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

EXCLUDES = ["tkinter", "matplotlib", "numpy", "PIL", "pytest", "IPython", "imageio_ffmpeg",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
            "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf"]

app = Analysis(
    ["app.py"],
    pathex=["."],
    datas=collect_data_files("yt_dlp_ejs") + collect_data_files("certifi"),
    hiddenimports=collect_submodules("yt_dlp_ejs") + ["native_reg"],
    excludes=EXCLUDES,
    noarchive=False,
)
host = Analysis(["native_host.py"], pathex=["."], excludes=EXCLUDES + ["PySide6", "yt_dlp", "requests"])

app_pyz = PYZ(app.pure)
host_pyz = PYZ(host.pure)

app_exe = EXE(app_pyz, app.scripts, [], exclude_binaries=True, name="DownloadDeck",
              console=False, icon="DownloadDeck.ico", upx=False)
host_exe = EXE(host_pyz, host.scripts, [], exclude_binaries=True, name="native_host",
               console=True, icon="DownloadDeck.ico", upx=False)

COLLECT(app_exe, app.binaries, app.datas,
        host_exe, host.binaries, host.datas,
        name="DownloadDeck", upx=False)
