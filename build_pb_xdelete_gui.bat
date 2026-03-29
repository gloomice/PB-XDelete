@echo off
setlocal
cd /d "%~dp0"

REM ============================================================================
REM PB XDelete GUI - Nuitka onefile + optional Authenticode signing
REM Paths with "(x86)" break cmd "if (...)" blocks - use short 8.3 path for signtool.
REM set PB_XDELETE_SIGN_PASSWORD=... before run if signing; do not store in this file.
REM signtool verify may fail for self-signed certs (root not in Windows trust store) - signing still OK.
REM ============================================================================

set "OUTPUT_GUI=pb_xdelete_gui.exe"
set "ENTRY_PY=pb_xdelete_gui.py"
set "ENGINE_EXE=pb_xdelete.exe"
set PB_XDELETE_SIGN_PASSWORD=www.linkleading.com

if not defined PFX_FILE set "PFX_FILE=my_signature.pfx"
if not defined PFX_PWD if defined PB_XDELETE_SIGN_PASSWORD set "PFX_PWD=%PB_XDELETE_SIGN_PASSWORD%"

REM Default signtool: short path (no parentheses) so nested IF blocks work
if not defined SIGNTOOL (
    for %%I in ("%ProgramFiles(x86)%") do set "SIGNTOOL=%%~sI\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"
)

if not exist "%ENTRY_PY%" (
    echo ERROR: missing %ENTRY_PY%
    pause
    exit /b 1
)
if not exist "%ENGINE_EXE%" (
    echo ERROR: missing %ENGINE_EXE%
    pause
    exit /b 1
)
if not exist lflogo.ico (
    echo WARNING: lflogo.ico not found
)

echo ==========================================
echo Nuitka onefile: %OUTPUT_GUI%
echo ==========================================
echo.

echo [1/5] Optional: sign engine %ENGINE_EXE%
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
echo SKIP: engine not pre-signed - no PFX or password
:AfterEngineSign

echo.
echo [2/5] Nuitka compile...
py -m nuitka --standalone --onefile --windows-console-mode=disable --windows-uac-admin --windows-icon-from-ico=lflogo.ico --enable-plugin=pyqt6 --include-data-files=%ENGINE_EXE%=%ENGINE_EXE% --include-data-files=PB_XDelete_Manual.pdf=PB_XDelete_Manual.pdf --include-data-files=lflogo.ico=lflogo.ico --lto=yes --remove-output --output-dir=dist --output-filename=%OUTPUT_GUI% "%ENTRY_PY%"

if errorlevel 1 (
    echo Build failed!
    pause
    exit /b 1
)

echo.
echo [3/5] Optional UPX - set USE_UPX=1 to enable
if /i "%USE_UPX%"=="1" (
    if exist upx.exe (
        upx.exe --best --lzma --force "dist\%OUTPUT_GUI%" 2>nul
        echo UPX: dist\%OUTPUT_GUI%
    ) else (
        echo UPX: upx.exe not found
    )
) else (
    echo UPX: off - default
)

echo.
echo [4/5] Copy to release\
if exist release rmdir /s /q release
mkdir release
copy /Y "dist\%OUTPUT_GUI%" "release\"

echo.
echo [5/5] Sign GUI
if not exist "%PFX_FILE%" goto SkipGuiSign
if not defined PFX_PWD goto SkipGuiSign
if not exist "%SIGNTOOL%" goto SkipGuiSignNoTool
"%SIGNTOOL%" sign /f "%PFX_FILE%" /p "%PFX_PWD%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /d "PB XDelete GUI" /v "release\%OUTPUT_GUI%"
if errorlevel 1 (
    echo ERROR: GUI sign failed.
    pause
    exit /b 1
)
echo Running signtool verify...
"%SIGNTOOL%" verify /pa /v "release\%OUTPUT_GUI%"
if errorlevel 1 (
    echo NOTE: verify failed - common for self-signed certs not in Windows Trusted Root. Signing succeeded above.
) else (
    echo Sign+verify OK.
)
goto AfterGuiSign
:SkipGuiSignNoTool
echo SKIP: signtool not found: %SIGNTOOL%
goto AfterGuiSign
:SkipGuiSign
echo SKIP: GUI not signed
:AfterGuiSign

echo.
echo Done: release\%OUTPUT_GUI%
pause
endlocal
