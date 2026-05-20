@echo off
echo =============================================
echo  YouTube Shorts Automation - Windows Setup
echo =============================================
echo.

REM ─── Check Python ──────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install!
    pause
    exit /b 1
)

echo [OK] Python found
python --version

REM ─── Create virtual environment ────────────────────────────────────────────
echo.
echo [1/5] Creating virtual environment...
python -m venv venv
if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment
    pause
    exit /b 1
)
echo [OK] Virtual environment created

REM ─── Activate venv ─────────────────────────────────────────────────────────
call venv\Scripts\activate.bat

REM ─── Upgrade pip ───────────────────────────────────────────────────────────
echo.
echo [2/5] Upgrading pip...
python -m pip install --upgrade pip

REM ─── Install Python packages ───────────────────────────────────────────────
echo.
echo [3/5] Installing Python packages (this may take a few minutes)...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install packages
    pause
    exit /b 1
)
echo [OK] All packages installed

REM ─── Check FFmpeg ──────────────────────────────────────────────────────────
echo.
echo [4/5] Checking FFmpeg...
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ACTION REQUIRED] FFmpeg not found!
    echo.
    echo Please install FFmpeg:
    echo   1. Go to: https://www.gyan.dev/ffmpeg/builds/
    echo   2. Download "ffmpeg-release-essentials.zip"
    echo   3. Extract it to C:\ffmpeg\
    echo   4. Add C:\ffmpeg\bin to your Windows PATH:
    echo      - Search "Edit environment variables" in Start menu
    echo      - Click "Path" under System Variables
    echo      - Click New and add: C:\ffmpeg\bin
    echo      - Click OK, restart this window
    echo.
    echo After installing FFmpeg, run this setup script again.
    pause
    exit /b 1
) else (
    echo [OK] FFmpeg found
)

REM ─── Set up .env ───────────────────────────────────────────────────────────
echo.
echo [5/5] Checking .env file...
if not exist .env (
    echo [ACTION REQUIRED] No .env file found. Creating from template...
    echo ANTHROPIC_API_KEY=sk-ant-PASTE-YOUR-KEY-HERE > .env
    echo.
    echo Please edit .env and add your Anthropic API key:
    echo   Get it at: https://console.anthropic.com/
    notepad .env
) else (
    echo [OK] .env file exists
)

REM ─── Done ──────────────────────────────────────────────────────────────────
echo.
echo =============================================
echo  Setup Complete!
echo =============================================
echo.
echo Next steps:
echo   1. Make sure your ANTHROPIC_API_KEY is in .env
echo   2. For YouTube upload: see SETUP.md for Google credentials
echo   3. Run: venv\Scripts\activate ^&^& python main.py --no-upload
echo      (test without uploading first!)
echo.
pause
