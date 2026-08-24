# SAFETY: REAL_NETWORK_REQUIRED | READ_ONLY | NO_ORDER_EXECUTION (uses UPSTOX_ANALYTICS_TOKEN from environment; never places orders)
"""Resolve current NIFTY spot, nearest future, nearest ATM CE, nearest ATM PE from Upstox API."""
import asyncio
import json
from datetime import date

import httpx

from app.core.config import settings


async def main():
    token = settings.upstox_analytics_token
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        # 1. Spot Quote
        quote_url = f"{settings.upstox_base_url}/market-quote/quotes?instrument_key=NSE_INDEX|Nifty 50"
        resp = await client.get(quote_url, headers=headers)
        spot_price = 24252.0
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            for _k, v in data.items():
                spot_price = v.get("last_price") or spot_price
        print(f"Current NIFTY Spot Price: {spot_price}")

        # 2. Get Contracts
        contract_url = f"{settings.upstox_base_url}/option/contract?instrument_key=NSE_INDEX|Nifty 50"
        resp2 = await client.get(contract_url, headers=headers)
        if resp2.status_code != 200:
            print(f"Failed to fetch contracts: {resp2.status_code}")
            return
        
        contracts = resp2.json().get("data", [])
        print(f"Total contracts fetched: {len(contracts)}")

        # Group by expiry and type
        expiries = sorted(list(set(c["expiry"] for c in contracts if c.get("expiry"))))
        today_str = date.today().isoformat()
        future_expiries = [e for e in expiries if e >= today_str]
        nearest_expiry = future_expiries[0] if future_expiries else expiries[0]
        print(f"Available Expiries (first 5): {expiries[:5]}")
        print(f"Nearest Active Expiry: {nearest_expiry}")

        # Find ATM strike
        atm_strike = round(spot_price / 50.0) * 50
        print(f"ATM Strike: {atm_strike}")

        # Filter contracts for nearest expiry
        nearest_contracts = [c for c in contracts if c.get("expiry") == nearest_expiry]
        
        # Find ATM CE
        atm_ce = next((c for c in nearest_contracts if c.get("instrument_type") == "CE" and c.get("strike_price") == atm_strike), None)
        if not atm_ce:
            # Pick closest
            ce_candidates = [c for c in nearest_contracts if c.get("instrument_type") == "CE"]
            atm_ce = min(ce_candidates, key=lambda c: abs(c.get("strike_price", 0) - spot_price))

        # Find ATM PE
        atm_pe = next((c for c in nearest_contracts if c.get("instrument_type") == "PE" and c.get("strike_price") == atm_strike), None)
        if not atm_pe:
            pe_candidates = [c for c in nearest_contracts if c.get("instrument_type") == "PE"]
            atm_pe = min(pe_candidates, key=lambda c: abs(c.get("strike_price", 0) - spot_price))

        # Find nearest Future
        futures = [c for c in contracts if c.get("instrument_type") in ("FUT", "FUTIDX", "FUTURE")]
        print(f"Futures found in contract list: {len(futures)}")
        if not futures:
            # Let's search market quotes or instrument master for NIFTY FUT
            # Let's query /market-quote/quotes with potential keys or search
            print("Let's check futures search...")

        print("\nResolved Instruments:")
        print("1. NIFTY Spot:")
        print("   instrument_key: NSE_INDEX|Nifty 50")
        print("   trading_symbol: Nifty 50")
        print("   canonical_symbol: NIFTY")
        
        if atm_ce:
            print("\n2. ATM CE:")
            print(f"   instrument_key: {atm_ce.get('instrument_key')}")
            print(f"   trading_symbol: {atm_ce.get('trading_symbol')}")
            print(f"   canonical_symbol: {atm_ce.get('name')}")
            print(f"   strike: {atm_ce.get('strike_price')}")
            print(f"   expiry: {atm_ce.get('expiry')}")
            print(f"   lot_size: {atm_ce.get('lot_size')}")

        if atm_pe:
            print("\n3. ATM PE:")
            print(f"   instrument_key: {atm_pe.get('instrument_key')}")
            print(f"   trading_symbol: {atm_pe.get('trading_symbol')}")
            print(f"   canonical_symbol: {atm_pe.get('name')}")
            print(f"   strike: {atm_pe.get('strike_price')}")
            print(f"   expiry: {atm_pe.get('expiry')}")
            print(f"   lot_size: {atm_pe.get('lot_size')}")

        # Test Option Chain with nearest expiry
        chain_url = f"{settings.upstox_base_url}/option/chain?instrument_key=NSE_INDEX|Nifty 50&expiry_date={nearest_expiry}"
        print(f"\nFetching Option Chain with expiry {nearest_expiry}: {chain_url}")
        resp_chain = await client.get(chain_url, headers=headers)
        print(f"Chain Status: {resp_chain.status_code}")
        if resp_chain.status_code == 200:
            cdata = resp_chain.json().get("data", [])
            print(f"Chain strikes: {len(cdata)}")
            if cdata:
                print("First strike in chain:")
                print(json.dumps(cdata[0], indent=2))

if __name__ == "__main__":
    asyncio.run(main())
