@echo off
rem ---------------------------------------------------------------------------
rem EcucStudio launcher.   Usage:
rem   run.bat [project.dpa]               EcucStudio
rem   run.bat gateway [gateway.json]      CAN gateway generator (CAN-Ethernet, CAN-CAN)
rem   run.bat editor [network.arxml]      CAN-Ethernet gateway editor
rem   run.bat topology [topology.json]    CAN gateway topology (several ECUs)
rem   run.bat debug [gateway^|editor^|topology] ... the same, but in this window: every message is shown here
rem Checks Python and the required packages first and shows every problem in this window, then
rem starts the window and waits until it is really on the screen. Startup messages are also written
rem to %LOCALAPPDATA%\EcucStudio\ecucstudio.log
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

rem --- arguments
set "DEBUG="
if /i "%~1"=="debug" set "DEBUG=1"
if defined DEBUG shift
set "MODE=gui"
if /i "%~1"=="gateway" set "MODE=gateway"
if /i "%~1"=="editor" set "MODE=editor"
if /i "%~1"=="topology" set "MODE=topology"
if not "%MODE%"=="gui" shift
set "ARG=%~1"
set "QARG="
if defined ARG set QARG="%ARG%"
if "%MODE%"=="gui" set "CMDARGS=-m ecucstudio gui"
if "%MODE%"=="gateway" set "CMDARGS=-m ecucstudio gateway gui"
if "%MODE%"=="editor" set "CMDARGS=-m ecucstudio gateway editor"
if "%MODE%"=="topology" set "CMDARGS=-m ecucstudio gateway topology gui"

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

rem --- 3. cantools is only needed by the CAN gateway generator: try to install, never block
%PY% -c "import cantools" >nul 2>nul
if errorlevel 1 (
  echo Installing cantools for the CAN gateway generator ...
  %PY% -m pip install "cantools>=39"
)

rem --- 4. the interpreter that was checked above, and its windowless twin pythonw.exe
set "PYEXE="
for /f "usebackq delims=" %%i in (`%PY% -c "import sys; print(sys.executable)"`) do set "PYEXE=%%i"
if not defined PYEXE goto :nopyexe
set "PYWEXE=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYWEXE%" set "PYWEXE=%PYEXE%"
echo Interpreter: %PYEXE%
if defined DEBUG goto :debug

rem --- 5. start the window and wait until it reports that it is on the screen
set "ECUCSTUDIO_READY=%TEMP%\ecucstudio_ready_%RANDOM%%RANDOM%.txt"
if exist "%ECUCSTUDIO_READY%" del "%ECUCSTUDIO_READY%"
start "EcucStudio" "%PYWEXE%" %CMDARGS% %QARG%
echo Starting EcucStudio, please wait ...
for /l %%i in (1,1,90) do (
  if exist "%ECUCSTUDIO_READY%" goto :started
  ping -n 2 127.0.0.1 >nul
)
echo.
echo [WARNING] No EcucStudio window appeared within 90 seconds.
echo Last lines of %LOCALAPPDATA%\EcucStudio\ecucstudio.log:
echo ---------------------------------------------------------------------------
"%PYEXE%" -c "import os; p = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'EcucStudio', 'ecucstudio.log'); print(''.join(open(p, encoding='utf-8', errors='replace').readlines()[-20:]) if os.path.exists(p) else '(no log file)')"
echo ---------------------------------------------------------------------------
echo If EcucStudio is still starting, wait a little longer. Otherwise close it in the
echo Task Manager (pythonw.exe) and run:   run.bat debug
echo That starts EcucStudio in this window and shows every message.
pause
exit /b 1

:started
echo [OK]
type "%ECUCSTUDIO_READY%"
del "%ECUCSTUDIO_READY%" >nul 2>nul
ping -n 3 127.0.0.1 >nul
exit /b 0

:debug
echo Debug mode: EcucStudio runs in this window, every message is shown here.
echo.
"%PYEXE%" -X faulthandler %CMDARGS% %QARG%
echo.
echo EcucStudio ended with exit code %errorlevel%.
pause
exit /b 0

:nopyexe
echo [ERROR] Could not ask %PY% for its interpreter path.
goto :fail

:fail
echo.
echo EcucStudio could not be started. Fix the problem above and run run.bat again.
pause
exit /b 1
