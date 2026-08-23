@echo off
REM ===========================================================================
REM  Petcare MMM Studio - ONE-TIME INSTALL
REM
REM  Verifies Python, installs dependencies, generates the sample datasets and
REM  leaves the app ready to start with util\redeploy.bat.
REM
REM  Usage:  double-click, or from cmd:  util\install.bat
REM          util\install.bat --with-meridian   (adds the ~2 GB Bayesian engine)
REM ===========================================================================
setlocal EnableDelayedExpansion
title Petcare MMM Studio - Install

REM --- run from the project root, wherever this script was invoked from ------
pushd "%~dp0.."
set "ROOT=%CD%"

echo.
echo ==========================================================
echo   Petcare MMM Studio - one-time install
echo   %ROOT%
echo ==========================================================
echo.

REM --- 1. locate a usable Python ---------------------------------------------
REM Prefer the py launcher; fall back to python on PATH.
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python was not found on this machine.
  echo         Install Python 3.10-3.12 from https://www.python.org/downloads/
  echo         and tick "Add python.exe to PATH" during setup.
  goto :fail
)

for /f "tokens=2" %%v in ('%PY% --version 2^>^&1') do set "PYVER=%%v"
echo [1/5] Python found: %PYVER%  (using "%PY%")

REM --- reject unsupported versions (Meridian needs 3.10-3.12) ----------------
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
  set "MAJOR=%%a"
  set "MINOR=%%b"
)
if not "%MAJOR%"=="3" (
  echo [ERROR] Python 3.10-3.12 is required. Found %PYVER%.
  goto :fail
)
if %MINOR% LSS 10 (
  echo [ERROR] Python 3.10-3.12 is required. Found %PYVER%.
  goto :fail
)
if %MINOR% GTR 12 (
  echo [WARN ] Python %PYVER% is newer than 3.12 - Google Meridian may not install.
  echo         The app still runs on the classic engine.
)

REM --- 2. upgrade pip --------------------------------------------------------
echo [2/5] Upgrading pip...
%PY% -m pip install --upgrade pip --quiet
if errorlevel 1 (
  echo [WARN ] Could not upgrade pip - continuing with the existing version.
)

REM --- 3. core dependencies --------------------------------------------------
REM Installed WITHOUT google-meridian so the ~2 GB TensorFlow download is opt-in.
echo [3/5] Installing core dependencies (fastapi, uvicorn, pandas, sklearn...)
%PY% -m pip install "fastapi>=0.110" "uvicorn[standard]>=0.29" "pandas>=2.0" ^
    "openpyxl>=3.1" "scikit-learn>=1.3" "scipy>=1.11" "python-multipart>=0.0.9" ^
    "pydantic>=2.5" "mcp>=1.0" numpy
if errorlevel 1 (
  echo [ERROR] Dependency install failed - see the pip output above.
  goto :fail
)

REM --- 4. optional Meridian --------------------------------------------------
if /i "%~1"=="--with-meridian" (
  echo [4/5] Installing Google Meridian - this downloads ~2 GB, please wait...
  %PY% -m pip install "google-meridian>=1.8"
  if errorlevel 1 (
    echo [WARN ] Meridian install failed. The app still runs on the classic engine.
  ) else (
    echo        Meridian installed - full Bayesian MCMC available.
  )
) else (
  echo [4/5] Skipping Google Meridian ^(~2 GB^).
  echo        Re-run as:  util\install.bat --with-meridian   to add it later.
)

REM --- 5. sample data --------------------------------------------------------
REM A running instance holds the .xlsx files open, which makes the rewrite fail
REM with PermissionError. Stop anything on the default port first.
echo [5/5] Generating sample datasets and the blank template...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"TCP.*:8050 .*LISTENING"') do (
  if not "%%p"=="0" (
    echo        Stopping running app ^(PID %%p^) so the data files can be rewritten...
    taskkill /F /PID %%p >nul 2>&1
    ping -n 3 127.0.0.1 >nul 2>&1
  )
)
REM Pre-flight: fail with a clear message if a data file is locked (Excel open).
%PY% -c "import os,sys;d='data';[open(os.path.join(d,f),'r+b').close() for f in os.listdir(d) if f.lower().endswith(('.xlsx','.xlsm','.xls')) and not f.startswith('~$')] if os.path.isdir(d) else None" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] A file in data\ is open in another program ^(usually Excel^).
  echo         Close it and re-run util\install.bat.
  goto :fail
)
%PY% scripts\generate_sample_data.py
if errorlevel 1 (
  echo [ERROR] Sample data generation failed.
  echo         If this was a "Permission denied" error, close Excel or any app
  echo         holding data\*.xlsx open, then re-run util\install.bat.
  goto :fail
)

REM --- warm the parse cache so the first load in the UI is instant ----------
echo        Warming the data cache (first parse takes ~40s, then it is instant)...
%PY% -c "from app import data_loader as dl; dl.load_excel('petcare_campaign_long.xlsx')" >nul 2>&1
if errorlevel 1 echo        [WARN ] Could not warm the cache - the first load will be slower.

echo.
echo ==========================================================
echo   INSTALL COMPLETE
echo.
echo   Start the app:   util\redeploy.bat
echo   Then browse to:  http://127.0.0.1:8050
echo ==========================================================
echo.
popd
endlocal
pause
exit /b 0

:fail
echo.
echo ==========================================================
echo   INSTALL FAILED - see the message above.
echo ==========================================================
echo.
popd
endlocal
pause
exit /b 1
