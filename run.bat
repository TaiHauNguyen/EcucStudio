@echo off
rem ---------------------------------------------------------------------------
rem EcucStudio launcher.   Usage: run.bat [project.dpa]
rem Checks Python and the required packages first and shows every problem in
rem this window, then starts the GUI. Startup errors are also written to
rem %LOCALAPPDATA%\EcucStudio\ecucstudio.log
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

rem --- 1. find a real Python (the Microsoft Store alias "python" does not count)
set "PY="
where py >nul 2>nul && py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3 was not found.
  echo         Install Python 3.10 or newer from https://www.python.org/downloads/
  echo         and tick "Add python.exe to PATH" and "tcl/tk and IDLE" during setup.
  goto :fail
)

rem --- 2. check version, tkinter and lxml
%PY% -m ecucstudio --check
if errorlevel 2 goto :fail
if errorlevel 1 (
  echo.
  echo Installing the missing Python packages from requirements.txt ...
  %PY% -m pip install -r requirements.txt
  %PY% -m ecucstudio --check
  if errorlevel 1 goto :fail
)

rem --- 3. start the GUI without a console window (pythonw), fall back to python
set "PYW="
where pyw >nul 2>nul && set "PYW=pyw -3"
if not defined PYW (
  where pythonw >nul 2>nul && set "PYW=pythonw"
)
if not defined PYW set "PYW=%PY%"
if "%~1"=="" (
  start "EcucStudio" %PYW% -m ecucstudio gui
) else (
  start "EcucStudio" %PYW% -m ecucstudio gui "%~1"
)
echo EcucStudio is starting. If no window appears, see %LOCALAPPDATA%\EcucStudio\ecucstudio.log
echo or run:  %PY% -m ecucstudio gui
timeout /t 3 >nul
exit /b 0

:fail
echo.
echo EcucStudio could not be started. Fix the problem above and run run.bat again.
pause
exit /b 1
