# SAFETY: REAL_NETWORK_REQUIRED | READ_ONLY | NO_ORDER_EXECUTION (uses UPSTOX_ANALYTICS_TOKEN from environment; never places orders)
"""Inspection script to inspect real Upstox instrument keys and option contracts."""
import asyncio
import json

import httpx

from app.core.config import settings


async def main():
    token = settings.upstox_analytics_token
    if not token:
        print("NO TOKEN CONFIGURED")
        return

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        # 1. Option Chain for Nifty 50
        url = f"{settings.upstox_base_url}/option/chain?instrument_key=NSE_INDEX|Nifty 50"
        print(f"Fetching Option Chain: {url}")
        resp = await client.get(url, headers=headers)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", [])
            print(f"Total strike entries: {len(items)}")
            if items:
                print("\nSample Item Structure:")
                sample = items[0]
                print(json.dumps(sample, indent=2))
        
        # 2. Option Contract Master/List for Nifty 50
        contract_url = f"{settings.upstox_base_url}/option/contract?instrument_key=NSE_INDEX|Nifty 50"
        print(f"\nFetching Option Contracts: {contract_url}")
        resp2 = await client.get(contract_url, headers=headers)
        print(f"Status: {resp2.status_code}")
        if resp2.status_code == 200:
            cdata = resp2.json()
            citems = cdata.get("data", [])
            print(f"Total contracts: {len(citems)}")
            if citems:
                print("First 3 contracts:")
                print(json.dumps(citems[:3], indent=2))

        # 3. Market Quotes for NIFTY spot
        quote_url = f"{settings.upstox_base_url}/market-quote/quotes?instrument_key=NSE_INDEX|Nifty 50"
        print(f"\nFetching Spot Quote: {quote_url}")
        resp3 = await client.get(quote_url, headers=headers)
        print(f"Status: {resp3.status_code}")
        if resp3.status_code == 200:
            print(json.dumps(resp3.json(), indent=2))

if __name__ == "__main__":
    asyncio.run(main())
