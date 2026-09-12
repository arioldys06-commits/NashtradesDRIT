@echo off
cd /d %~dp0
call venv\Scripts\activate
python bot_engine.py
pause
