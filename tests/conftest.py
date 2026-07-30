import os

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("DATABASE_URL_ASYNC", "postgresql+asyncpg://postgres:postgres@localhost/test")
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql+psycopg://postgres:postgres@localhost/test")
