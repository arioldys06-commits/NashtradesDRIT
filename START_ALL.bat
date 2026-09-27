@echo off
REM ============================================================
REM START_ALL.bat -- NashtradesDRIT
REM
REM IMPORTANTE (2026-09-22): en esta PC, lanzar MT5 automaticamente
REM desde Python falla con "Process create failed" (problema de
REM Windows/permisos con esta instalacion especifica, no del codigo).
REM Por eso signal_engine.py y bot_engine.py ahora usan
REM launch_terminal=False -- ambos se CONECTAN a una MT5 que
REM YA debe estar abierta y logueada manualmente por ti.
REM
REM Lanza los 3 procesos, cada uno en su propia consola:
REM   1. signal_engine.py  -> se conecta a MT5 ya abierta (no la lanza)
REM   2. bot_engine.py     -> se conecta a la misma terminal
REM   3. news_engine.py    -> no usa MT5, corre en loop propio cada 20 min
REM
REM Este .bat asume que está guardado en la RAIZ del proyecto,
REM junto a signal_engine.py, bot_engine.py, news_engine.py y .env.
REM ============================================================

setlocal

REM --- Cambiar al directorio donde está este .bat ---
cd /d "%~dp0"

REM --- Python a usar: si existe .venv en el proyecto, se usa ese; si no, el lanzador "py" de Windows ---
set "PYTHON_EXE=py"
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
)

echo ============================================================
echo  NashtradesDRIT -- Iniciando procesos
echo  Python: %PYTHON_EXE%
echo  Carpeta: %cd%
echo ============================================================
echo.
echo  *** ANTES DE CONTINUAR ***
echo  Abre MT5 manualmente (C:\XM Global MT5\terminal64.exe) y
echo  confirma que ya estas logueado en la cuenta 411221079.
echo  Este script YA NO abre MT5 por ti -- solo se conecta a ella.
echo.
pause

REM --- 1) signal_engine.py: se conecta a MT5 (no la lanza) y evalua señales ---
echo [1/3] Iniciando signal_engine.py...
start "NashtradesDRIT - signal_engine" cmd /k "%PYTHON_EXE%" signal_engine.py

timeout /t 5 /nobreak >nul

REM --- 2) bot_engine.py: se conecta a la misma terminal, ejecuta ordenes reales ---
echo [2/3] Iniciando bot_engine.py (ejecuta ordenes reales)...
start "NashtradesDRIT - bot_engine" cmd /k "%PYTHON_EXE%" bot_engine.py

timeout /t 3 /nobreak >nul

REM --- 3) news_engine.py: calendario + noticias, no depende de MT5 ---
echo [3/3] Iniciando news_engine.py (calendario y noticias)...
start "NashtradesDRIT - news_engine" cmd /k "%PYTHON_EXE%" news_engine.py

echo.
echo ============================================================
echo  Los 3 procesos quedaron abiertos en ventanas separadas:
echo    - NashtradesDRIT - signal_engine
echo    - NashtradesDRIT - bot_engine
echo    - NashtradesDRIT - news_engine
echo  Cierra esta ventana cuando confirmes que las 3 arrancaron bien.
echo ============================================================
echo.
pause
endlocal