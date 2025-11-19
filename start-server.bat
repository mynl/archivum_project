@echo off
setlocal

REM Non-admin servers
REM Avoid name collisions WindowsTerminaL
set "WTL=%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe"

REM === Paths & commands ===
set "APP1_PORT=9777"
set "APP1_DIR=C:\S\TELOS\Python\archivum_project"
set "APP1_CMD=waitress-serve --listen=192.168.4.43:%APP1_PORT% --call website.app:create_app"
set "APP1_LBL=Archivum"

set "APP2_PORT=19777"
set "APP2_DIR=C:\S\Library"
set "APP2_CMD=python -m http.server %APP2_PORT% --bind 192.168.4.43"
set "APP2_LBL=Library server"


REM === Open one Windows Terminal window with multiple tabs ===
REM -w 0 is the current window
"%WTL%" -w 0 ^
    new-tab --title "%APP1_LBL%" cmd /k "cd /d \"%APP1_DIR%\" && %APP1_CMD%" ^
  ; new-tab --title "%APP2_LBL%" cmd /k "cd /d \"%APP2_DIR%\" && %APP2_CMD%" ^
  ; focus-tab -t 0

echo Servers created
echo ===============================================
echo %APP1_LBL%         http://192.168.4.43:%APP1_PORT%
echo %APP2_LBL%   http://192.168.4.43:%APP2_PORT% (not used directly)


REM Notes:
REM - Use quotes around path variables with spaces.
REM - No trailing spaces after ^
REM - The `exit /b` at the end prevents the parent cmd window from closing.
REM - `cmd /k` keeps the tab open after the command runs.

exit /b
