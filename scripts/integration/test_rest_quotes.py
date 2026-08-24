# SAFETY: REAL_NETWORK_REQUIRED | READ_ONLY | NO_ORDER_EXECUTION (uses UPSTOX_ANALYTICS_TOKEN from environment; never places orders)
"""Test REST quotes for the 4 resolved instruments."""
import asyncio

import httpx

from app.core.config import settings


async def main():
    keys = [
        "NSE_INDEX|Nifty 50",
        "NSE_FO|58072",   # NIFTY26AUGFUT
        "NSE_FO|61671",   # NIFTY26AUG24250CE
        "NSE_FO|61684",   # NIFTY26AUG24250PE
    ]
    key_str = ",".join(keys)
    url = f"{settings.upstox_base_url}/market-quote/quotes?instrument_key={key_str}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.upstox_analytics_token}",
    }

    print(f"Fetching REST quotes for {len(keys)} instruments:")
    print(f"URL: {url}")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=headers)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            for k, v in data.items():
                print(f"\n--- {k} ---")
                print(f"  LTP: {v.get('last_price')}")
                print(f"  Volume: {v.get('volume')}")
                print(f"  OI: {v.get('oi')}")
                print(f"  Close: {v.get('ohlc', {}).get('close')}")
                depth = v.get("depth", {})
                bids = depth.get("buy", [])
                asks = depth.get("sell", [])
                if bids:
                    print(f"  Best Bid: Price={bids[0].get('price')}, Qty={bids[0].get('quantity')}")
                if asks:
                    print(f"  Best Ask: Price={asks[0].get('price')}, Qty={asks[0].get('quantity')}")

if __name__ == "__main__":
    asyncio.run(main())
