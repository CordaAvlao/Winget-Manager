@echo off
REM ============================================================
REM  Build script for "Winget Manager" portable .exe
REM  Requires: pyinstaller (pip install pyinstaller) + sv_ttk
REM ============================================================
setlocal

REM Repertoire du script (la ou se trouve ce .bat).
set "HERE=%~dp0"
cd /d "%HERE%"

echo.
echo === Nettoyage des builds precedents ===
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "Winget Manager.spec" del /q "Winget Manager.spec"

echo.
echo === Construction de Winget Manager.exe (PyInstaller --onefile) ===
py -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "Winget Manager" ^
    --icon icon.ico ^
    --collect-all sv_ttk ^
    --add-data "icon.ico;." ^
    winget_manager.py

if errorlevel 1 (
    echo.
    echo *** ECHEC de la construction ***
    exit /b 1
)

echo.
echo === Construction terminee ===
echo    Fichier genere : "%HERE%dist\Winget Manager.exe"
echo    Ce .exe est autonome (Python et sv_ttk embarques).
echo.
pause
endlocal
