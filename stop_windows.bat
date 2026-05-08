@echo off
setlocal
set ROOT_DIR=%~dp0
cd /d "%ROOT_DIR%"

set PID_FILE=%ROOT_DIR%\tmp\server.pid
set NGROK_PID_FILE=%ROOT_DIR%\tmp\ngrok.pid

if not exist "%PID_FILE%" (
  echo Aucun serveur enregistré.
) else (
  set /p SERVER_PID=<"%PID_FILE%"
  if not "%SERVER_PID%"=="" (
    taskkill /PID %SERVER_PID% /F >nul 2>&1
  )
  if exist "%PID_FILE%" del "%PID_FILE%"
  echo Serveur arrêté.
)

if exist "%NGROK_PID_FILE%" (
  set /p NGROK_PID=<"%NGROK_PID_FILE%"
  if not "%NGROK_PID%"=="" (
    taskkill /PID %NGROK_PID% /F >nul 2>&1
  )
  del "%NGROK_PID_FILE%" >nul 2>&1
  echo Ngrok arrêté.
)
