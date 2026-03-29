@echo off
setlocal
cd /d "%~dp0"

set PB_XDELETE_SIGN_PASSWORD=www.linkleading.com
REM Optional signing: set PB_XDELETE_SIGN_PASSWORD before run. Default PFX: my_signature.pfx
if not defined PFX_FILE set "PFX_FILE=my_signature.pfx"
if not defined PFX_PWD if defined PB_XDELETE_SIGN_PASSWORD set "PFX_PWD=%PB_XDELETE_SIGN_PASSWORD%"
if not defined SIGNTOOL (
    for %%I in ("%ProgramFiles(x86)%") do set "SIGNTOOL=%%~sI\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"
)

echo [1/3] rc resource.rc
rc.exe /nologo resource.rc
if errorlevel 1 (
    echo [ERR] rc.exe failed
    pause
    exit /b 1
)

echo [2/3] cl pb_xdelete.cpp
cl.exe /nologo /std:c++17 /EHsc /O2 /Ob2 /Oi /Ot /Oy /GL /Gy /arch:AVX2 /W3 /utf-8 /DNDEBUG /D_WIN32_WINNT=0x0600 /Fe:pb_xdelete.exe ^
  pb_xdelete.cpp resource.res ^
  /link /SUBSYSTEM:CONSOLE /LTCG /OPT:REF /OPT:ICF /STACK:8388608 /ENTRY:wmainCRTStartup ^
  advapi32.lib

if errorlevel 1 (
    echo [ERR] cl.exe failed
    pause
    exit /b 1
)

echo [OK] pb_xdelete.exe

echo [3/3] optional sign pb_xdelete.exe
if not exist "%PFX_FILE%" goto after_sign
if not defined PFX_PWD goto after_sign
if not exist "%SIGNTOOL%" goto sign_no_tool
"%SIGNTOOL%" sign /f "%PFX_FILE%" /p "%PFX_PWD%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /d "PB XDelete" /v pb_xdelete.exe
if errorlevel 1 goto sign_warn
"%SIGNTOOL%" verify /pa /v pb_xdelete.exe
if errorlevel 1 (
    echo NOTE: verify failed - normal for self-signed cert not in Windows trust store.
) else (
    echo [OK] sign+verify OK.
)
goto after_sign
:sign_no_tool
echo [SKIP] signtool missing: %SIGNTOOL%
goto after_sign
:sign_warn
echo [WARN] sign failed - check PFX and password.
goto after_sign
:after_sign

echo Done.
pause
endlocal
