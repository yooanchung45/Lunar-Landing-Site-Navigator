@echo off
cd /d "%~dp0"
.venv\Scripts\python -m uvicorn backend.app:app --reload
