@echo off
cd /d %~dp0

start "NashtradesDRIT - Signal Engine" cmd /k "venv\Scripts\activate && python signal_engine.py"
timeout /t 3 /nobreak >nul

start "NashtradesDRIT - Bot Engine" cmd /k "venv\Scripts\activate && python bot_engine.py"
timeout /t 3 /nobreak >nul

start "NashtradesDRIT - News Engine" cmd /k "venv\Scripts\activate && python news_engine.py"

echo Se abrieron 3 consolas: Signal Engine, Bot Engine y News Engine.
pause
