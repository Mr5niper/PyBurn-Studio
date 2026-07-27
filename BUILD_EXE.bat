@echo off
setlocal enabledelayedexpansion

:: ==========================================================================
:: Configuration
:: ==========================================================================
set "REQUIRED_PYTHON_VERSION=3.13.12"
set "PYTHON_DOWNLOAD_URL=https://www.python.org/downloads/release/python-31312/"
set "PY=py -3.13"

:: ==========================================================================
:: Pre-flight Check: Verify Python Version (via py launcher, not PATH)
:: ==========================================================================
echo [INFO] Checking Python version...

:: The py launcher lives in C:\Windows and is reachable even when the
:: 'python' command on PATH is a different version.
%PY% --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python 3.13 was not found via the py launcher.
    echo This build script requires Python %REQUIRED_PYTHON_VERSION%.
    echo Tried: %PY%
    echo.
    echo Please install the correct version from:
    echo %PYTHON_DOWNLOAD_URL%
    echo.
    echo [NOTE] During installation, enable the py launcher option.
    goto :error
)

:: Capture the resolved version (e.g. "Python 3.13.12")
for /f "tokens=2 delims= " %%v in ('%PY% --version 2^>^&1') do set "CURRENT_PYTHON_VERSION=%%v"

echo [INFO] Current Python version: !CURRENT_PYTHON_VERSION!
echo [INFO] Required Python version: %REQUIRED_PYTHON_VERSION%

if not "!CURRENT_PYTHON_VERSION!"=="%REQUIRED_PYTHON_VERSION%" (
    echo.
    echo [ERROR] Incorrect Python version detected.
    echo This build script requires Python %REQUIRED_PYTHON_VERSION%.
    echo The py launcher resolved version !CURRENT_PYTHON_VERSION! instead.
    echo.
    echo Please install the correct version from:
    echo %PYTHON_DOWNLOAD_URL%
    echo.
    goto :error
)

:: ==========================================================================
:: Build Script for PyBurn Studio
:: ==========================================================================
:: Creates a virtual environment, installs the pinned dependencies, and builds
:: a single-file windowed executable using the checked-in PyBurn spec.
:: Requirements are fully pinned in requirements.txt so every build on every
:: machine produces the same onefile.
:: ==========================================================================
echo [INFO] Python version matches. Starting build process...

:: The spec embeds pyburn.ico and reads Windows metadata from version.txt.
:: Fail early with a clear message if either is missing.
if not exist pyburn.ico (
    echo [ERROR] pyburn.ico not found in the project root.
    echo         The build needs pyburn.ico next to pyburn_studio.py.
    goto :error
)
if not exist version.txt (
    echo [ERROR] version.txt not found in the project root.
    echo         The build needs version.txt next to pyburn_studio.py.
    goto :error
)

:: 1. Create a CLEAN Virtual Environment
echo [STEP 1/5] Creating a clean virtual environment in '.\venv'...

:: Always start from a fresh venv. Reusing an old venv is how a wrong dependency
:: version (for example a comtypes that breaks the IMAPI2 audio-burn IStream
:: import) can silently persist across builds. A reproducible build must not
:: depend on whatever happened to be installed before.
if exist .\venv (
    echo [INFO] Removing existing '.\venv' for a clean, reproducible build...
    rmdir /s /q .\venv
)

:: Build the venv with the verified py-launcher 3.13, not bare 'python'.
%PY% -m venv .\venv
if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    goto :error
)

:: 2. Activate Virtual Environment
echo [STEP 2/5] Activating virtual environment...
call .\venv\Scripts\activate.bat

if not defined VIRTUAL_ENV (
    echo [ERROR] Failed to activate the virtual environment. Make sure '.\venv\Scripts\activate.bat' exists.
    goto :error
)

:: 3. Install Dependencies
echo [STEP 3/5] Upgrading pip and installing pinned dependencies from requirements.txt...
python -m pip install --upgrade pip > nul
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip.
    goto :error
)

pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies from requirements.txt.
    goto :error
)

:: 3b. Verify the burn-critical dependencies actually resolved to the versions
::     the code depends on. The IMAPI2 audio burn imports IStream from a module
::     whose location is comtypes-version-specific, so a wrong comtypes silently
::     breaks burning at runtime. Fail the BUILD here instead of shipping an exe
::     that cannot burn. Add any other version-sensitive checks to this block.
echo [STEP 4/5] Verifying burn-critical dependencies...
python -c "import comtypes; assert comtypes.__version__ == '1.4.13', 'comtypes ' + comtypes.__version__ + ' installed, need 1.4.13'; import comtypes.client; assert hasattr(comtypes.client, 'GetModule'), 'comtypes.client.GetModule missing (audio burn IStream generation would fail)'; import comtypes._post_coinit.unknwn; import PyQt6; print('[INFO] comtypes', comtypes.__version__, 'OK; GetModule present; _post_coinit present; PyQt6 OK')"
if errorlevel 1 (
    echo.
    echo [ERROR] Dependency verification failed. The installed packages do not
    echo         match what the code requires, and a build would produce an exe
    echo         that cannot burn. Check requirements.txt pins and re-run.
    goto :error
)

:: 5. Build with PyInstaller on the command line (this is what the build uses;
:: the .spec is not needed). --collect-all comtypes plus the _post_coinit
:: hidden imports bundle comtypes in full so the frozen IMAPI2 audio burn can
:: generate the COM IStream interface at runtime. --name pyburn_studio keeps the
:: exe and any generated spec on the original pyburn_studio name.
echo [STEP 5/5] Building the onefile executable with PyInstaller...
pyinstaller -F --noupx --clean --noconfirm --windowed --name PyBurnStudio ^
 --collect-all comtypes ^
 --hidden-import comtypes.automation ^
 --hidden-import comtypes._post_coinit ^
 --hidden-import comtypes._post_coinit.unknwn ^
 --hidden-import comtypes._post_coinit.misc ^
 --collect-submodules pyburn ^
 --hidden-import PyQt6.sip ^
 --exclude-module PyQt6.Qt3DCore --exclude-module PyQt6.Qt3DRender ^
 --exclude-module PyQt6.Qt3DInput --exclude-module PyQt6.Qt3DLogic ^
 --exclude-module PyQt6.Qt3DAnimation --exclude-module PyQt6.Qt3DExtras ^
 --exclude-module PyQt6.QtQml --exclude-module PyQt6.QtQuick ^
 --exclude-module PyQt6.QtQuick3D --exclude-module PyQt6.QtQuickWidgets ^
 --exclude-module PyQt6.QtWebEngineCore --exclude-module PyQt6.QtWebEngineWidgets ^
 --exclude-module PyQt6.QtWebEngineQuick --exclude-module PyQt6.QtWebChannel ^
 --exclude-module PyQt6.QtWebSockets --exclude-module PyQt6.QtWebView ^
 --exclude-module PyQt6.QtSql --exclude-module PyQt6.QtTest ^
 --exclude-module PyQt6.QtTextToSpeech --exclude-module PyQt6.QtBluetooth ^
 --exclude-module PyQt6.QtNfc --exclude-module PyQt6.QtPositioning ^
 --exclude-module PyQt6.QtMultimedia --exclude-module PyQt6.QtMultimediaWidgets ^
 --exclude-module PyQt6.QtCharts --exclude-module PyQt6.QtDataVisualization ^
 --exclude-module PyQt6.QtSensors --exclude-module PyQt6.QtSerialPort ^
 --exclude-module PyQt6.QtDesigner --exclude-module PyQt6.QtHelp ^
 --exclude-module PyQt6.QtPdf --exclude-module PyQt6.QtPdfWidgets ^
 --exclude-module PyQt6.QtSvgWidgets ^
 --exclude-module tkinter --exclude-module numpy --exclude-module PIL ^
 --icon pyburn.ico --add-data "pyburn.ico;." --version-file version.txt ^
 .\pyburn_studio.py
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    goto :error
)

echo.
echo [SUCCESS] Build completed successfully.
echo The single-file executable is in the '.\dist' directory (PyBurnStudio.exe).
echo.
echo [NOTE] PyBurn Studio drives external command-line tools (cdrecord, growisofs,
echo        ffmpeg, cdrdao, and so on) at run time. Those tools are NOT bundled.
echo        On Windows the recommended way to provide them is WSL2. Without them
echo        the app runs in simulation mode so the interface can still be used.
goto :end

:error
echo.
echo [FAILURE] The build process failed. Please check the errors above.
echo.
pause
exit /b 1

:end
echo.
pause
endlocal
