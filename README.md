# AI Stock Scanner

FastAPI + PostgreSQL + SQLAlchemy async + Alembic + pandas-ta + Scanner Engine.

## Quick Start

`powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
uv sync --extra dev --link-mode=copy
uv run alembic upgrade head
uv run pytest
uv run uvicorn app.main:app --reload --port 8002
`

## Features

- NSE EOD bhavcopy import (CSV/ZIP)
- 15 technical indicators (EMA, SMA, RSI, MACD, ATR, ADX, SuperTrend, VWAP, OBV, Bollinger Bands, Stochastic)
- 36 scanners (trend, momentum, volatility, volume, delivery, combined)
- Volume & Delivery StockEdge-style scanners
- REST API with Swagger docs
- CLI for data import
- Async PostgreSQL with connection pooling
- Production-ready logging, middleware, and error handling

## API Docs

Open http://127.0.0.1:8002/docs after starting the server.
