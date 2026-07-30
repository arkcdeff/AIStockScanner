@echo off
setlocal
if not exist .venv\Scripts\python.exe (
  echo Creating Python 3.13 virtual environment...
  py -3.13 -m venv .venv || exit /b 1
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip uv || exit /b 1
uv sync --extra dev --link-mode=copy || exit /b 1
if not exist .env copy .env.example .env >nul

echo.
echo Setup complete. Edit .env and replace YOUR_PASSWORD before migrations.
echo Then run:
echo   uv run alembic upgrade head
echo   uv run uvicorn app.main:app --reload
endlocal
