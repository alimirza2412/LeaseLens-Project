@echo off
REM ============================================================
REM  LeaseLens — start the app (Windows, double-click or run)
REM ============================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [!] Virtual environment not found. Creating it and installing deps...
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  python -m pip install --prefer-binary -r requirements.txt
)

echo.
echo ============================================================
echo   LeaseLens is starting at http://localhost:8000
echo   Press Ctrl+C to stop.
echo ============================================================
echo.
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
