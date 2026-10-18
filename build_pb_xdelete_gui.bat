@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM ============================================================================
REM PB XDelete GUI - Nuitka onefile + zstd + cached unpack + optional UPX + sign
REM Requires: py -m pip install nuitka PyQt6 zstandard
REM Build engine first: build_pb_xdelete.bat
REM Usage: build_pb_xdelete_gui.bat [nopause]
REM ============================================================================

set "OUTPUT_GUI=pb_xdelete_gui.exe"
set "ENTRY_PY=pb_xdelete_gui.py"
set "ENGINE_EXE=pb_xdelete.exe"
set "PRODUCT_VERSION=1.0.8.0"

if not defined PB_XDELETE_SIGN_PASSWORD set "PB_XDELETE_SIGN_PASSWORD=www.linkleading.com"
if not defined PFX_FILE set "PFX_FILE=my_signature.pfx"
if not defined PFX_PWD if defined PB_XDELETE_SIGN_PASSWORD set "PFX_PWD=%PB_XDELETE_SIGN_PASSWORD%"

if not defined UPX_EXE (
  if exist "%~dp0upx.exe" set "UPX_EXE=%~dp0upx.exe"
)
if not defined UPX_EXE (
  if exist "C:\upx-5.0.2-win64\upx.exe" set "UPX_EXE=C:\upx-5.0.2-win64\upx.exe"
)

if not defined SIGNTOOL (
    for %%I in ("%ProgramFiles(x86)%") do set "SIGNTOOL=%%~sI\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"
)

if not exist "%ENTRY_PY%" (
    echo ERROR: missing %ENTRY_PY%
    if /i not "%~1"=="nopause" pause
    exit /b 1
)
if not exist "%ENGINE_EXE%" (
    echo ERROR: missing %ENGINE_EXE% - run build_pb_xdelete.bat first
    if /i not "%~1"=="nopause" pause
    exit /b 1
)
if not exist lflogo.ico (
    echo WARNING: lflogo.ico not found
)

py -c "import zstandard" 1>nul 2>nul
if errorlevel 1 (
  echo [INFO] installing zstandard for onefile compression...
  py -m pip install -q zstandard
)

echo ==========================================
echo Nuitka onefile: %OUTPUT_GUI%  V%PRODUCT_VERSION%
echo Startup: cached onefile unpack dir
echo ==========================================
echo.

echo [1/6] Optional: sign engine %ENGINE_EXE%
if not exist "%PFX_FILE%" goto SkipEngineSign
if not defined PFX_PWD goto SkipEngineSign
if not exist "%SIGNTOOL%" goto SkipEngineSignNoTool
"%SIGNTOOL%" sign /f "%PFX_FILE%" /p "%PFX_PWD%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /d "PB XDelete Engine" /v "%ENGINE_EXE%"
if errorlevel 1 echo WARNING: engine sign failed, continuing.
goto AfterEngineSign
:SkipEngineSignNoTool
echo SKIP: signtool not found: %SIGNTOOL%
goto AfterEngineSign
:SkipEngineSign
echo SKIP: engine not pre-signed
:AfterEngineSign

echo.
echo [2/6] Nuitka compile (no PDF manual; platforms; cached onefile)...
py -m nuitka --standalone --onefile ^
  --windows-console-mode=disable ^
  --windows-uac-admin ^
  --windows-icon-from-ico=lflogo.ico ^
  --enable-plugin=pyqt6 ^
  --include-qt-plugins=platforms ^
  --include-data-files=%ENGINE_EXE%=%ENGINE_EXE% ^
  --include-data-files=lflogo.ico=lflogo.ico ^
  --company-name="Wuhan Linkleading" ^
  --product-name="PB XDelete" ^
  --file-version=%PRODUCT_VERSION% ^
  --product-version=%PRODUCT_VERSION% ^
  --file-description="PB XDelete GUI" ^
  --copyright="Copyright (C) 2025-2026 Wuhan Linkleading" ^
  --onefile-tempdir-spec="{CACHE_DIR}/WuhanLinkleading/PB_XDelete/{VERSION}" ^
  --onefile-cache-mode=cached ^
  --lto=yes ^
  --remove-output ^
  --output-dir=dist ^
  --output-filename=%OUTPUT_GUI% ^
  "%ENTRY_PY%"

if errorlevel 1 (
    echo Build failed!
    if /i not "%~1"=="nopause" pause
    exit /b 1
)

echo.
echo [3/6] Copy to release\
if exist release rmdir /s /q release
mkdir release
copy /Y "dist\%OUTPUT_GUI%" "release\"
copy /Y "%ENGINE_EXE%" "release\"

echo.
echo [4/6] UPX compress (UPX before sign)
if not defined UPX_EXE goto AfterUpx
if not exist "%UPX_EXE%" goto AfterUpx
echo UPX: %UPX_EXE%
"%UPX_EXE%" --best --lzma -q "release\%ENGINE_EXE%"
if errorlevel 1 echo WARNING: UPX engine failed/skipped
"%UPX_EXE%" --best --lzma -q "release\%OUTPUT_GUI%"
if errorlevel 1 (
  echo WARNING: UPX GUI --best --lzma failed, retry --best...
  "%UPX_EXE%" --best -q "release\%OUTPUT_GUI%"
  if errorlevel 1 echo WARNING: UPX GUI skipped
)
:AfterUpx

echo.
echo [5/6] Sign release binaries
if not exist "%PFX_FILE%" goto SkipGuiSign
if not defined PFX_PWD goto SkipGuiSign
if not exist "%SIGNTOOL%" goto SkipGuiSignNoTool
"%SIGNTOOL%" sign /f "%PFX_FILE%" /p "%PFX_PWD%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /d "PB XDelete Engine" /v "release\%ENGINE_EXE%"
if errorlevel 1 echo WARNING: release engine sign failed
"%SIGNTOOL%" sign /f "%PFX_FILE%" /p "%PFX_PWD%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /d "PB XDelete GUI" /v "release\%OUTPUT_GUI%"
if errorlevel 1 (
    echo ERROR: GUI sign failed.
    if /i not "%~1"=="nopause" pause
    exit /b 1
)
"%SIGNTOOL%" verify /pa /v "release\%OUTPUT_GUI%"
if errorlevel 1 (
    echo NOTE: verify failed - normal for self-signed cert. Sign OK above.
) else (
    echo Sign+verify OK.
)
goto AfterGuiSign
:SkipGuiSignNoTool
echo SKIP: signtool not found: %SIGNTOOL%
goto AfterGuiSign
:SkipGuiSign
echo SKIP: not signed
:AfterGuiSign

echo.
echo [6/6] Done
for %%F in ("release\%OUTPUT_GUI%") do echo   GUI: %%~nxF  %%~zF bytes
for %%F in ("release\%ENGINE_EXE%") do echo   ENG: %%~nxF  %%~zF bytes
echo   Tip: first start fills cache; later starts are faster.
if /i not "%~1"=="nopause" pause
endlocal
