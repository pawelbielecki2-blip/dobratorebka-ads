@echo off
cd /d "%~dp0"
title DobraTorebka V45.1 - SIMPLE NEWBLACK - PORT 5040
echo ==========================================
echo DOBRATOREBKA V45.1 - SIMPLE NEWBLACK
echo PORT: 5040
echo ==========================================

if not exist ".env" (
  if exist ".env.example" copy ".env.example" ".env" >nul
)

findstr /B /C:"NEWBLACK_API_KEY=" ".env" >nul 2>&1
if errorlevel 1 (
  echo.>>".env"
  echo NEWBLACK_API_KEY=>>".env"
)

py -m pip install -r requirements.txt
if errorlevel 1 goto error
py -m py_compile app.py
if errorlevel 1 goto error

start "DobraTorebka V45.1 SERVER" cmd /k "cd /d "%~dp0" && py app.py"
timeout /t 4 /nobreak >nul
start "" http://127.0.0.1:5040
pause
exit /b 0

:error
echo Wystapil blad.
pause
