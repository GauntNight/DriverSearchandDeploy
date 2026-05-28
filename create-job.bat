@echo off
call .venv\Scripts\activate.bat
python cli.py create-driver-job %*
