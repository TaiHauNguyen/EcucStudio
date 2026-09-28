@echo off
rem EcucStudio launcher. Usage: run.bat [project.dpa]
cd /d "%~dp0"
if "%~1"=="" (
  start "" pythonw -m ecucstudio gui
) else (
  start "" pythonw -m ecucstudio gui "%~1"
)
