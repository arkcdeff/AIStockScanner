# Glossary

## Terms

**ADR (Architecture Decision Record)**
Document that captures an important architectural decision made along with its context and consequences.

**Alembic**
Database migration tool for SQLAlchemy. Manages version-controlled schema changes.

**API (Application Programming Interface)**
Set of endpoints that allow clients to interact with the application.

**Async Session**
SQLAlchemy session that supports async/await operations using asyncpg driver.

**Bhavcopy**
Daily equity market data file published by NSE containing OHLC prices, volume, and delivery data.

**CLI (Command Line Interface)**
Text-based interface for running commands. This project includes CLI for NSE data import.

**CRUD (Create, Read, Update, Delete)**
Basic operations for managing data in a database.

**EOD (End of Day)**
Daily market data captured at the end of trading day.

**EMA (Exponential Moving Average)**
Technical indicator that places greater weight on recent data points.

**Event Loop**
Core component of async frameworks that manages and schedules async tasks.

**FastAPI**
Modern, high-performance web framework for building APIs with Python 3.7+.

**Indicator Engine**
Module that calculates technical indicators from price data using pandas-ta.

**Mermaid**
Markdown-based diagramming tool for creating flowcharts, sequence diagrams, etc.

**Migration**
Version-controlled database schema change managed by Alembic.

**NSE (National Stock Exchange of India)**
India's leading stock exchange. Primary data source for this project.

**OBV (On-Balance Volume)**
Technical indicator that uses volume flow to predict price changes.

**PostgreSQL**
Open-source relational database management system.

**psycopg**
PostgreSQL adapter for Python. Used for synchronous database operations.

**Pydantic**
Data validation library using Python type annotations.

**pandas-ta**
Python library for technical analysis indicators built on pandas.

**Repository Pattern**
Design pattern that abstracts data access logic behind an interface.

**RSI (Relative Strength Index)**
Momentum oscillator that measures speed and magnitude of price changes.

**Scanner Engine**
Module that executes trading scanners against indicator data to identify patterns.

**SQLAlchemy**
Python SQL toolkit and ORM. Provides async support via asyncpg.

**Stochastic Oscillator**
Momentum indicator comparing closing price to price range over time.

**SuperTrend**
Trend-following indicator that combines ATR with moving averages.

**Uvicorn**
ASGI server implementation for running FastAPI applications.

**VWAP (Volume Weighted Average Price)**
Trading benchmark that shows average price weighted by volume.

**ZIP File**
Compressed archive format used for NSE bhavcopy distribution.
