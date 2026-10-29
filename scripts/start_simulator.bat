@echo off
rem Start the sensor simulator. The web server must already be running. Ctrl+C to stop.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run:  py -m venv .venv  then  .venv\Scripts\pip install -r requirements.txt
  exit /b 1
)
".venv\Scripts\python.exe" manage.py simulate_sensors %*
