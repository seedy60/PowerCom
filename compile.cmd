@echo off
setlocal
if not defined UV_CACHE_DIR set "UV_CACHE_DIR=.uv-cache"
uv sync
if errorlevel 1 exit /b %errorlevel%

uv run pyinstaller --clean --noconfirm --hidden-import=notifypy --hidden-import=ntfpy --hidden-import=prism --hidden-import=prism.core --hidden-import=prism.lib --hidden-import=logging.handlers --hidden-import=pyprowl --hidden-import=sound_lib --hidden-import=sound_lib.output --hidden-import=sound_lib.stream --hidden-import=watchdog --hidden-import=watchdog.observers --hidden-import=wx --hidden-import=wx.adv --hidden-import=powercom_config --hidden-import=powercom_config_model --collect-binaries=prism --collect-binaries=sound_lib --upx-dir=C:\UPX powercom.py
if errorlevel 1 exit /b %errorlevel%

set "DIST_DIR=dist\powercom"

robocopy sounds "%DIST_DIR%\sounds" /E >nul
if %errorlevel% GEQ 8 exit /b %errorlevel%

copy /Y ttcom.conf.sample "%DIST_DIR%\ttcom.conf.sample" >nul
if errorlevel 1 exit /b %errorlevel%

copy /Y powercom_defaults.ini "%DIST_DIR%\powercom_defaults.ini" >nul
if errorlevel 1 exit /b %errorlevel%

endlocal
