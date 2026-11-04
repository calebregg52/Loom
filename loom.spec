# PyInstaller build file: `pyinstaller loom.spec` produces dist/Loom.exe
a = Analysis(
    ["main.py"],
    datas=[("assets/loom.png", "assets")],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtPdf", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="Loom",
    icon="assets/loom.ico",
    console=False,
    upx=False,
)
