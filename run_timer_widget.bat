@echo off
setlocal
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe "%~dp0timer_widget_launcher.pyw"
    exit /b 0
)

where py.exe >nul 2>nul
if %errorlevel%==0 (
    start "" py.exe -3 "%~dp0timer_widget_launcher.pyw"
    exit /b 0
)

start "" python "%~dp0timer_widget_launcher.pyw"
