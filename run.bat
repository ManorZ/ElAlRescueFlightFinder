@echo off
echo Starting El Al Rescue Flight Finder...

REM Check for virtual environment
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo No virtual environment found. Running with system Python.
)

python app.py
pause
