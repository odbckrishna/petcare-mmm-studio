@echo off
REM ===========================================================================
REM  Petcare MMM Studio - REDEPLOY
REM
REM  Stops any running instance, then starts a fresh one so code changes take
REM  effect. Safe to run when nothing is running - it simply starts the app.
REM
REM  Usage:  double-click, or from cmd:  util\redeploy.bat
REM          util\redeploy.bat --port 9000    (use a different port)
REM          util\redeploy.bat --console      (run in THIS window, Ctrl+C to stop)
REM          util\redeploy.bat --stop         (stop the app and exit)
REM          util\redeploy.bat --no-browser   (do not open a browser window)
REM ===========================================================================
setlocal EnableDelayedExpansion
title Petcare MMM Studio - Redeploy

pushd "%~dp0.."
set "ROOT=%CD%"

REM --- defaults / argument parsing -------------------------------------------
set "PORT=8050"
set "CONSOLE="
set "STOPONLY="
set "NOBROWSER="

:parseargs
if "%~1"=="" goto :parsed
if /i "%~1"=="--port"       ( set "PORT=%~2" & shift & shift & goto :parseargs )
if /i "%~1"=="--console"    ( set "CONSOLE=1"   & shift & goto :parseargs )
if /i "%~1"=="--stop"       ( set "STOPONLY=1"  & shift & goto :parseargs )
if /i "%~1"=="--no-browser" ( set "NOBROWSER=1" & shift & goto :parseargs )
echo [WARN ] Ignoring unknown option: %~1
shift
goto :parseargs
:parsed

echo.
echo ==========================================================
echo   Petcare MMM Studio - redeploy   (port %PORT%)
echo ==========================================================
echo.

REM --- 1. locate Python ------------------------------------------------------
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python not found. Run util\install.bat first.
  goto :fail
)

REM Resolve the real interpreter path - Start-Process needs an executable,
REM not the "py -3" launcher form.
set "PY_EXE="
for /f "delims=" %%e in ('%PY% -c "import sys; print(sys.executable)" 2^>nul') do set "PY_EXE=%%e"
if not defined PY_EXE (
  echo [ERROR] Could not resolve the Python executable path.
  goto :fail
)

REM --- 2. stop whatever is listening on the port -----------------------------
REM Only kills the PID actually bound to this port, so other Python work is safe.
echo [1/3] Stopping any instance on port %PORT%...
set "FOUND="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"TCP.*:%PORT% .*LISTENING"') do (
  if not "%%p"=="0" (
    taskkill /F /PID %%p >nul 2>&1
    if errorlevel 1 (
      echo        [WARN ] Could not stop PID %%p - it may need admin rights.
    ) else (
      echo        Stopped PID %%p.
      set "FOUND=1"
    )
  )
)
if not defined FOUND echo        Nothing was running on port %PORT%.

REM give the OS a moment to release the socket
ping -n 3 127.0.0.1 >nul 2>&1

if defined STOPONLY (
  echo.
  echo   Stopped. Run util\redeploy.bat to start again.
  echo.
  popd
  endlocal
  pause
  exit /b 0
)

REM --- 3. sanity check: dependencies present ---------------------------------
echo [2/3] Checking dependencies...
%PY% -c "import fastapi, uvicorn, pandas, sklearn" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Dependencies are missing. Run util\install.bat first.
  goto :fail
)

REM --- 4. start ---------------------------------------------------------------
if defined CONSOLE (
  echo [3/3] Starting in this window - press Ctrl+C to stop.
  echo.
  %PY% run.py --port %PORT%
  popd
  endlocal
  exit /b 0
)

echo [3/3] Starting the app...
if not exist "logs" mkdir "logs"
REM Detach fully via PowerShell: a plain "start cmd /c" child inherits this
REM console's stdout, so a piped or non-interactive run would block until the
REM server exits. Start-Process gives the server its own handles.
powershell -NoProfile -Command ^
  "Start-Process -FilePath '%PY_EXE%' -ArgumentList @('run.py','--port','%PORT%') -WorkingDirectory '%ROOT%' -RedirectStandardOutput '%ROOT%\logs\server.log' -RedirectStandardError '%ROOT%\logs\server.err.log' -WindowStyle Hidden" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Could not launch the server process.
  goto :fail
)

REM --- wait for the port to accept connections -------------------------------
set "UP="
for /l %%i in (1,1,30) do (
  if not defined UP (
    ping -n 2 127.0.0.1 >nul 2>&1
    netstat -ano | findstr /r /c:"TCP.*:%PORT% .*LISTENING" >nul 2>&1
    if not errorlevel 1 set "UP=1"
  )
)

echo.
if defined UP (
  echo ==========================================================
  echo   RUNNING at  http://127.0.0.1:%PORT%
  echo.
  echo   Logs:  logs\server.log
  echo   Stop:  util\redeploy.bat --stop
  echo ==========================================================
  REM /b keeps the browser launch from holding this console open.
  if not defined NOBROWSER start "" /b "http://127.0.0.1:%PORT%"
) else (
  echo ==========================================================
  echo   [ERROR] The app did not start within 60 seconds.
  echo   Check logs\server.log for the reason.
  echo ==========================================================
  if exist "logs\server.log" (
    echo.
    echo --- last lines of logs\server.log ---
    powershell -NoProfile -Command "Get-Content 'logs\server.log' -Tail 15" 2>nul
  )
  goto :fail
)
echo.
popd
endlocal
exit /b 0

:fail
echo.
popd
endlocal
pause
exit /b 1
