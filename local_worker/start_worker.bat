@echo off
REM Krypto_Trader - Lokaler Worker (Windows-Starthilfe)
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [FEHLER] Python wurde nicht gefunden.
  echo Bitte Python 3.11/3.12 installieren und "Add python.exe to PATH" anhaken:
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Virtuelle Umgebung wird angelegt...
  python -m venv .venv || goto :fail
)

echo [2/3] Abhaengigkeiten werden geprueft...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail

echo [3/3] Worker startet...
".venv\Scripts\python.exe" worker.py %*
goto :eof

:fail
echo.
echo [FEHLER] Einrichtung fehlgeschlagen - siehe Meldungen oben.
pause
exit /b 1
