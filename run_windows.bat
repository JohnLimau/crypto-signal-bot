@echo off
cd /d "%~dp0"
if not exist .env (
  echo Please copy .env.example to .env and set DISCORD_WEBHOOK_URL first.
  pause
  exit /b 1
)
if not exist .venv ( py -3 -m venv .venv )
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python bot.py
pause
