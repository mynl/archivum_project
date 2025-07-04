@echo off
setlocal

:: audit call: with python version and optional mode: new (default) or refresh
if "%1"=="" (
    echo Usage: build.bat PYTHON_VERSION new^|refresh
    exit /b 1
)

if /i not "%2"=="new" if /i not "%2"=="refresh" (
    echo Usage: build.bat PYTHON_VERSION new^|refresh
    echo Invalid second argument mode: "%2"
    echo Must be 'new' or 'refresh'.
    exit /b 1
)

:: --- Configuration CUSTOMIZE HERE! ---
set "PYTHON_VERSION=%1"
set "MODE=%2"
set "PROJECT_NAME=archivum_project"
:: set "PROJECT_REPO=https://github.com/mynl/%PROJECT_NAME%.git"
set "PROJECT_REPO=c:\s\telos\python\%PROJECT_NAME%"
set "BUILD_DIR=C:\tmp\%PROJECT_NAME%_rtd_build_%1"
set "VENV_DIR=%BUILD_DIR%\venv"
set "HTML_OUTPUT_DIR=%BUILD_DIR%\html"
set "PORT=9800"

:: --- Prepare Environment and Clone Repository ---
if /i "%MODE%"=="new" (
    echo Cleaning previous build directory...
    pushd C:\tmp
    rmdir /s /q "%BUILD_DIR%" >nul 2>&1
    mkdir "%BUILD_DIR%"

    echo Cloning repository...
    git clone --depth 1 "%PROJECT_REPO%" "%BUILD_DIR%"
    if %ERRORLEVEL% NEQ 0 (
        echo Git clone failed. Exiting.
        exit /b %ERRORLEVEL%
    )
) else (
    echo Reusing existing build directory at "%BUILD_DIR%"
)


pushd "%BUILD_DIR%"

if /i "%MODE%"=="refresh" (
    echo Updating local clone from "%PROJECT_REPO%"...
    git remote add source "%PROJECT_REPO%" 2>nul
    git fetch source
    git reset --hard source/master
    if %ERRORLEVEL% NEQ 0 (
        echo Git update failed. Exiting.
        exit /b %ERRORLEVEL%
    )
)

:: --- Setup Virtual Environment ---
if /i "%MODE%"=="new" (
    echo Creating virtual environment for Python %PYTHON_VERSION%...
    uv venv "%VENV_DIR%" --python %PYTHON_VERSION%
    if %ERRORLEVEL% NEQ 0 (
        echo Failed to create virtual environment. Exiting.
        exit /b %ERRORLEVEL%
    )
)

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo Virtual environment not found at "%VENV_DIR%".
    echo Please run with 'new' mode first to create it.
    exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat"
if %ERRORLEVEL% NEQ 0 (
    echo Failed to activate virtual environment. Exiting.
    exit /b %ERRORLEVEL%
)

if /i "%MODE%"=="new" (
    :: --- Install Dependencies ---
    echo Upgrading setuptools...
    uv pip install --upgrade setuptools
    if %ERRORLEVEL% NEQ 0 (
        echo Failed to upgrade setuptools. Exiting.
        exit /b %ERRORLEVEL%
    )

    echo Installing Sphinx...
    uv pip install --upgrade sphinx
    if %ERRORLEVEL% NEQ 0 (
        echo Failed to install Sphinx. Exiting.
        exit /b %ERRORLEVEL%
    )

    echo Installing project dependencies from pyproject.toml...
    uv pip install --upgrade --no-cache-dir .[dev]
    if %ERRORLEVEL% NEQ 0 (
        echo Failed to install project dependencies. Exiting.
        exit /b %ERRORLEVEL%
    )
)

:: --- Build HTML Documentation ---
echo Building HTML documentation...
python -m sphinx -T -b html -d _build\doctrees -D language=en docs "%HTML_OUTPUT_DIR%"
if %ERRORLEVEL% NEQ 0 (
    echo HTML build failed. Exiting.
    exit /b %ERRORLEVEL%
)

echo.
echo HTML documentation built successfully in "%HTML_OUTPUT_DIR%"
echo run cd "%HTML_OUTPUT_DIR%" ^&^& python -m http.server %PORT%
echo to serve the documentation.


endlocal
