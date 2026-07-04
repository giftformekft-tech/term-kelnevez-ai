@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo    AI Kepleiro + Atnevezo - EXE keszites
echo ============================================
echo.

REM --- Python meglet ellenorzese ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [HIBA] Nem talalhato a Python.
    echo Toltsd le: https://www.python.org/downloads/
    echo Telepiteskor PIPALD BE: "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

REM --- Forras .py fajl megkeresese (ekezetes nev miatt wildcard) ---
set "SRC="
for %%f in ("ai_rename_gui3*.py") do set "SRC=%%f"
if not defined SRC (
    echo [HIBA] Nem talalhato a forras .py fajl a mappaban.
    echo.
    pause
    exit /b 1
)
echo Forras fajl: !SRC!
echo.

REM --- 1/3: fuggosegek ---
echo [1/3] Fuggosegek telepitese...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [HIBA] A fuggosegek telepitese nem sikerult.
    echo.
    pause
    exit /b 1
)
echo.

REM --- 2/3: build ---
echo [2/3] EXE keszitese PyInstaller-rel...
python -m PyInstaller --onefile --windowed --collect-all certifi --name "AI_Kepleiro" "!SRC!"
if errorlevel 1 (
    echo [HIBA] A build nem sikerult.
    echo.
    pause
    exit /b 1
)
echo.

REM --- 3/3: kesz ---
echo [3/3] KESZ!
echo Az exe itt talalhato:  dist\AI_Kepleiro.exe
echo.
pause
