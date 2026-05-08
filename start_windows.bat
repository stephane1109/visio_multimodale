@echo off
setlocal
set ROOT_DIR=%~dp0
cd /d "%ROOT_DIR%"
if not exist "%ROOT_DIR%\tmp" mkdir "%ROOT_DIR%\tmp"

set PID_FILE=%ROOT_DIR%\tmp\server.pid
set LOG_FILE=%ROOT_DIR%\tmp\server.log

if exist "%PID_FILE%" (
  set /p EXISTING_PID=<"%PID_FILE%"
  if not "%EXISTING_PID%"=="" (
    taskkill /PID %EXISTING_PID% /F >nul 2>&1
    timeout /t 1 >nul
  )
  del "%PID_FILE%" >nul 2>&1
)

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  echo Le port 8000 est deja utilise par un autre processus (PID %%a).
  echo Fermez ce processus puis relancez start_windows.bat.
  exit /b 1
)

if exist "%ROOT_DIR%\.venv\Scripts\python.exe" (
  set "PYTHON_BIN=%ROOT_DIR%\.venv\Scripts\python.exe"
) else (
  set "PYTHON_BIN=python"
)

for /f %%i in ('powershell -NoProfile -Command "$p = Start-Process -FilePath ''%PYTHON_BIN%'' -ArgumentList ''app/server.py'',''serve'',''--host'',''127.0.0.1'',''--port'',''8000'' -WorkingDirectory ''%ROOT_DIR%'' -RedirectStandardOutput ''%LOG_FILE%'' -RedirectStandardError ''%LOG_FILE%'' -PassThru; $p.Id"') do set SERVER_PID=%%i
echo %SERVER_PID% > "%PID_FILE%"
timeout /t 2 >nul
start "" "http://127.0.0.1:8000/admin.html"
