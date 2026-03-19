@echo off
echo ============================================
echo  El Al Rescue Flight Finder - Tokyo Setup
echo ============================================
echo.

REM Check for virtual environment
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo No virtual environment found. Running with system Python.
)

REM Prompt for email
set /p EMAIL=Enter your email for alerts:
if "%EMAIL%"=="" (
    echo Email is required. Exiting.
    pause
    exit /b 1
)

REM Prompt for date (default: today)
echo.
set /p ALERT_DATE=Enter alert start date (YYYY-MM-DD, press Enter for today):
if "%ALERT_DATE%"=="" (
    for /f "tokens=1-3 delims=/" %%a in ("%date%") do (
        REM Handle different date formats - try to get YYYY-MM-DD
        set ALERT_DATE=%%c-%%a-%%b
    )
    REM Fallback: use Python to get today's date reliably
    for /f %%d in ('python -c "import datetime; print(datetime.date.today())"') do set ALERT_DATE=%%d
)

echo.
echo Setting up alerts for Star Alliance origins from Tokyo...
echo Date: %ALERT_DATE%
echo Email: %EMAIL%
echo.

python setup_alerts.py --date %ALERT_DATE% --email %EMAIL%

if errorlevel 1 (
    echo.
    echo Setup failed. Check the error above.
    pause
    exit /b 1
)

echo.
echo Starting dashboard...
echo.
python app.py
pause
