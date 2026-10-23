@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" halloumi_ctl.py start %*
pause
