@echo off
REM ============================================================
REM  Compila Audiomatico en DOS variantes y crea sus instaladores.
REM    win7   -> dist\win7\Audiomatico.exe    32 bits, SIN winsdk. Sirve en Windows 7/8/10/11.
REM              instalador\Instalar_Audiomatico_x.y.z.exe
REM    win10  -> dist\win10\Audiomatico.exe   32 bits, CON winsdk (artista y duracion de otras apps).
REM              instalador\Audiomatico_Win10-11_x.y.z.exe   (solo Windows 10/11)
REM  Requisitos: Python 3.8 de 32 bits (python.org, 3.8.10) e Inno Setup 6/7.
REM  Uso:  compilar.bat            (al final espera una tecla)
REM        compilar.bat nopause    (sin pausa)
REM ============================================================
setlocal
cd /d "%~dp0"

call :compilar win7 requirements.txt
call :compilar win10 requirements.txt requirements-win10.txt
call :instalador win7
call :instalador win10

echo.
echo Listo. Revisa las carpetas dist\ e instalador\
if /i not "%~1"=="nopause" pause
exit /b 0

:compilar
set VAR=%~1
set REQ=-r %~2
if not "%~3"=="" set REQ=%REQ% -r %~3
set EXCL=
if "%VAR%"=="win7" set EXCL=--exclude-module winsdk
echo.
echo ===== Compilando variante %VAR% (32 bits) =====
py -3.8-32 --version >nul 2>&1
if errorlevel 1 (
    echo No se encontro Python 3.8 de 32 bits. Se omite.
    exit /b 0
)
REM un entorno aparte por variante: el de win7 NUNCA debe tener winsdk instalado
if not exist ".venv_%VAR%\Scripts\python.exe" py -3.8-32 -m venv ".venv_%VAR%"
".venv_%VAR%\Scripts\python.exe" -m pip install --upgrade pip
".venv_%VAR%\Scripts\python.exe" -m pip install %REQ% -r requirements-build.txt
".venv_%VAR%\Scripts\python.exe" icono.py
".venv_%VAR%\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --noconsole %EXCL% ^
    --name Audiomatico --icon "%~dp0icono.ico" --add-data "%~dp0icono.ico;." ^
    --distpath "dist\%VAR%" --workpath "build\%VAR%" --specpath "build" ^
    --hidden-import comtypes.stream --hidden-import _cffi_backend --hidden-import _miniaudio ^
    programador_audios.py
exit /b 0

:instalador
echo.
echo ===== Creando el instalador %~1 =====
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
if not exist "dist\%~1\Audiomatico.exe" (
    echo Falta dist\%~1\Audiomatico.exe. Se omite este instalador.
    exit /b 0
)
echo Usando: %ISCC%
"%ISCC%" /DVariante=%~1 instalador.iss
exit /b 0
