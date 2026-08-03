# Project Overview

## AI Stock Scanner

AI Stock Scanner is a production-ready FastAPI application designed for analyzing NSE (National Stock Exchange of India) equity market data. It provides automated EOD (End-of-Day) data ingestion, technical indicator calculation, and a flexible scanner engine for identifying stock patterns.

## Key Capabilities

- **NSE EOD Data Ingestion**: Automated download and parsing of NSE bhavcopy files (CSV/ZIP)
- **Technical Indicators**: 15 indicators calculated using pandas-ta (EMA, SMA, RSI, MACD, ATR, ADX, SuperTrend, VWAP, OBV, Bollinger Bands, Stochastic)
- **Scanner Engine**: 36 production-ready scanners across multiple categories (trend, momentum, volatility, volume, delivery, combined)
- **Volume & Delivery Analysis**: StockEdge-style scanners for volume spikes and delivery analysis
- **REST API**: FastAPI-based async API with Swagger documentation
- **CLI Tool**: Command-line interface for data import
- **PostgreSQL Storage**: Async SQLAlchemy ORM with Alembic migrations

## Target Users

- Retail traders analyzing NSE stocks
- Quantitative researchers
- Financial analysts
- Algorithmic trading system developers

## Business Value

- Automated data ingestion reduces manual work by hours daily
- 36 pre-built scanners cover common trading strategies
- Extensible scanner registry allows custom scanner development
- Production-ready architecture with proper logging, error handling, and testing
