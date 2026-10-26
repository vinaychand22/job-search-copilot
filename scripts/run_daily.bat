@echo off
REM Daily run of the Job Search Copilot. Windows Task Scheduler calls this file
REM (see docs/DEPLOY.md). It reads new job alerts and company replies, then updates
REM the shortlist. Output is appended to data\daily.log.
cd /d "%~dp0.."
if not exist data mkdir data
set PYTHONUTF8=1
echo ===== %date% %time% ===== >> data\daily.log
".venv\Scripts\python.exe" -m copilot.daily >> data\daily.log 2>&1
