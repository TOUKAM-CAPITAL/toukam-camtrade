@echo off
REM TOUKAM BUSINESS INTELLIGENCE - CAMTRADE dashboard launcher
REM Double-click this file to start the dashboard. It opens automatically
REM in your default browser at http://127.0.0.1:8000

cd /d "%~dp0"

echo Checking dataset...
if not exist "CAMTRADE_DATA\CAMTRADE.db" (
    echo No database found - generating the CAMTRADE demo dataset first...
    python generate_camtrade_data_no_numpy.py
    if errorlevel 1 (
        echo.
        echo ERROR: failed to generate the CAMTRADE dataset. Is Python installed and on PATH?
        pause
        exit /b 1
    )
)

echo.
echo Running a quick sanity check (verify_camtrade.py)...
python verify_camtrade.py
if errorlevel 1 (
    echo.
    echo WARNING: verify_camtrade.py reported a failure - see above.
    echo The dashboard may still work, but review the failure before demoing.
    pause
)

echo.
echo Starting the CAMTRADE dashboard...
echo   Executive dashboard : http://127.0.0.1:8000/
echo   Commercial demo path : http://127.0.0.1:8000/demo
echo   Press Ctrl+C in this window to stop the server.
echo.
python camtrade_dashboard.py

pause
