@echo off
REM ============================================================
REM  Compila Audiomatico para Windows 7 / 8 / 10 / 11 y crea el instalador.
REM  Genera:
REM     dist\32bits\Audiomatico.exe   (sirve en Windows de 32 y 64 bits)
REM     dist\64bits\Audiomatico.exe   (solo Windows de 64 bits)
REM     instalador\Instalar_Audiomatico_x.y.z.exe   (con Inno Setup 6)
REM  Requisitos: Python 3.8 de 32 y de 64 bits (python.org, 3.8.10) e
REM  Inno Setup 6 (jrsoftware.org). Usa el lanzador "py".
REM  Uso:  compilar.bat            (al final espera una tecla)
REM        compilar.bat nopause    (sin pausa)
REM ============================================================
setlocal
cd /d "%~dp0"

call :compilar 32 "py -3.8-32"
call :compilar 64 "py -3.8-64"
call :instalador

echo.
echo Listo. Revisa las carpetas dist\ e instalador\
if /i not "%~1"=="nopause" pause
exit /b 0

:compilar
set ARQ=%~1
set PY=%~2
echo.
echo ===== Compilando %ARQ% bits =====
%PY% --version >nul 2>&1
if errorlevel 1 (
    echo No se encontro Python 3.8 de %ARQ% bits. Se omite.
    exit /b 0
)
if not exist ".venv%ARQ%\Scripts\python.exe" %PY% -m venv ".venv%ARQ%"
".venv%ARQ%\Scripts\python.exe" -m pip install --upgrade pip
".venv%ARQ%\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-build.txt
".venv%ARQ%\Scripts\python.exe" icono.py
".venv%ARQ%\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --noconsole ^
    --name Audiomatico --icon "%~dp0icono.ico" --add-data "%~dp0icono.ico;." ^
    --distpath "dist\%ARQ%bits" --workpath "build\%ARQ%" --specpath "build" ^
    --hidden-import comtypes.stream ^
    programador_audios.py
exit /b 0

:instalador
echo.
echo ===== Creando el instalador =====
set ISCC=
REM 1) donde lo registro el instalador de Inno Setup (versiones 7 y 6, vistas de 32 y 64 bits)
for %%V in (7 6) do for %%R in (32 64) do (
    for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup %%V_is1" /v InstallLocation /reg:%%R 2^>nul ^| find "InstallLocation"') do (
        if exist "%%B\ISCC.exe" set ISCC=%%B\ISCC.exe
    )
)
REM 2) carpetas habituales
if "%ISCC%"=="" if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if "%ISCC%"=="" if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if "%ISCC%"=="" if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if "%ISCC%"=="" (
    echo No se encontro Inno Setup. Se omite el instalador.
    exit /b 0
)
echo Usando: %ISCC%
"%ISCC%" instalador.iss
exit /b 0
