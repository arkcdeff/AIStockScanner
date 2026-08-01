import asyncio

from sqlalchemy import select

from app.db.session import get_session_factory
from app.models.stock import Stock


async def main() -> None:
    factory = get_session_factory()

    async with factory() as session:
        result = await session.scalars(select(Stock))
        stocks = result.all()
        print("Stock count:", len(stocks))
        print(stocks)


if __name__ == "__main__":
    asyncio.run(main())