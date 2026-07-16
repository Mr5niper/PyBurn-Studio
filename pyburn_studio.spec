# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

datas = [('pyburn.ico', '.')]
binaries = []
# The pyburn package is pulled in by submodule collection because the GUI
# imports its modules dynamically through the queue/backend. PyQt6 itself is
# handled by PyInstaller's built-in PyQt6 hook, which bundles exactly the Qt
# libraries and plugins the imported modules need (QtWidgets, QtCore, QtGui),
# including the platform plugin. We do NOT collect_all('PyQt6'): that dragged
# in the entire Qt plugin tree (Qt 3D, QML/Quick, WebEngine, WebView, and the
# Oracle/Postgres/Firebird/Mimer SQL drivers) none of which this app uses, and
# each one printed a "Library not found" warning for the DLLs of the packages
# we never installed. sip still needs to be named explicitly.
# The icon is referenced twice on purpose. The EXE(icon=...) field at the
# bottom embeds it into the exe's Windows resources so Explorer, the shortcut,
# and the file itself show it. The datas entry above ALSO bundles the .ico as a
# runtime data file, so the app can load it with resource_path() and call
# setWindowIcon() to put it on the title bar, the taskbar button while running,
# and the corner of every dialog and message box. Embedding alone does not make
# the running window show the icon; the runtime copy is what does that.
hiddenimports = ['PyQt6.sip'] + collect_submodules('pyburn')

# Exclude the Qt subsystems this app never imports. This keeps the standard
# PyQt6 hook from bundling them, silences the plugin-dependency warnings, and
# makes the onefile smaller. The app only uses QtWidgets / QtCore / QtGui.
excludes = [
    'PyQt6.Qt3DCore', 'PyQt6.Qt3DRender', 'PyQt6.Qt3DInput',
    'PyQt6.Qt3DLogic', 'PyQt6.Qt3DAnimation', 'PyQt6.Qt3DExtras',
    'PyQt6.QtQml', 'PyQt6.QtQuick', 'PyQt6.QtQuick3D', 'PyQt6.QtQuickWidgets',
    'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebEngineQuick',
    'PyQt6.QtWebChannel', 'PyQt6.QtWebSockets', 'PyQt6.QtWebView',
    'PyQt6.QtSql', 'PyQt6.QtTest', 'PyQt6.QtTextToSpeech',
    'PyQt6.QtBluetooth', 'PyQt6.QtNfc', 'PyQt6.QtPositioning',
    'PyQt6.QtMultimedia', 'PyQt6.QtMultimediaWidgets', 'PyQt6.QtCharts',
    'PyQt6.QtDataVisualization', 'PyQt6.QtSensors', 'PyQt6.QtSerialPort',
    'PyQt6.QtDesigner', 'PyQt6.QtHelp', 'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets',
    'PyQt6.QtSvgWidgets',
    # Unrelated heavy libs sometimes pulled transitively; the app does not use them.
    'tkinter', 'numpy', 'PIL',
]


a = Analysis(
    ['pyburn_studio.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PyBurnStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version.txt',
    icon=['pyburn.ico'],
)
