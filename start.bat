@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Please follow README.md to install it.
  pause
  popd
  exit /b 1
)
set "HF_HUB_OFFLINE=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "HF_HUB_DISABLE_TELEMETRY=1"
set "TRANSFORMERS_OFFLINE=1"
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, faster_whisper, opencc"
if errorlevel 1 (
  echo Python dependencies are incomplete. See the Environment repair section in README.md.
  pause
  popd
  exit /b 1
)
".venv\Scripts\python.exe" -m app.main
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" pause
popd
endlocal & exit /b %APP_EXIT%
