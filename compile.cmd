@echo off
setlocal
if not defined UV_CACHE_DIR set "UV_CACHE_DIR=.uv-cache"
uv sync
if errorlevel 1 exit /b %errorlevel%

rem Hidden imports and binary collection live in powercom.spec.
uv run pyinstaller --clean --noconfirm --upx-dir=C:\UPX powercom.spec
if errorlevel 1 exit /b %errorlevel%

set "DIST_DIR=dist\powercom"

robocopy sounds "%DIST_DIR%\sounds" /E >nul
if %errorlevel% GEQ 8 exit /b %errorlevel%

copy /Y ttcom.conf.sample "%DIST_DIR%\ttcom.conf.sample" >nul
if errorlevel 1 exit /b %errorlevel%

copy /Y powercom_defaults.ini "%DIST_DIR%\powercom_defaults.ini" >nul
if errorlevel 1 exit /b %errorlevel%

endlocal
