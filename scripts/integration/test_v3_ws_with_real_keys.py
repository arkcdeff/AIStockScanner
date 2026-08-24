# SAFETY: REAL_NETWORK_REQUIRED | READ_ONLY | NO_ORDER_EXECUTION (uses UPSTOX_ANALYTICS_TOKEN from environment; never places orders)
"""Test real V3 WebSocket subscription with resolved instrument keys."""
import asyncio
import json

import httpx
import websockets

from app.core.config import settings
from app.providers.upstox_proto import MarketDataFeedV3_pb2


async def main():
    keys = [
        "NSE_INDEX|Nifty 50",
        "NSE_FO|58072",   # NIFTY26AUGFUT
        "NSE_FO|61671",   # NIFTY26AUG24250CE
        "NSE_FO|61684",   # NIFTY26AUG24250PE
    ]

    # 1. Authorize
    auth_url = settings.upstox_v3_feed_authorize_url
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.upstox_analytics_token}",
    }
    print(f"1. Calling V3 authorize endpoint: {auth_url}")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(auth_url, headers=headers)
        print(f"   HTTP Status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"   Authorize Failed: {resp.text}")
            return
        data = resp.json().get("data", {})
        ws_uri = data.get("authorized_redirect_uri") or data.get("authorizedRedirectUri")
        print(f"   Authorized URI Obtained: {'YES' if ws_uri else 'NO'}")

    # 2. Connect and Subscribe
    print("\n2. Connecting to WebSocket...")
    async with websockets.connect(ws_uri) as ws:
        print("   WebSocket Connected!")
        sub_payload = {
            "guid": "r1b_test_sub",
            "method": "sub",
            "data": {
                "mode": "full",
                "instrumentKeys": keys,
            },
        }
        print(f"3. Sending subscription request for {len(keys)} keys:")
        print(f"   Payload: {json.dumps(sub_payload, indent=2)}")
        await ws.send(json.dumps(sub_payload))

        # 4. Receive and decode frames
        print("\n4. Listening for binary Protobuf frames (up to 10 seconds)...")
        frame_count = 0
        decoded_by_key = {}
        start_t = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_t < 10.0:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                if isinstance(msg, bytes):
                    frame_count += 1
                    feed_resp = MarketDataFeedV3_pb2.FeedResponse()
                    feed_resp.ParseFromString(msg)
                    print(f"\n   [Frame {frame_count}] Type={feed_resp.type}, current_ts={feed_resp.current_ts}, feeds_count={len(feed_resp.feeds)}")
                    for ik, feed in feed_resp.feeds.items():
                        print(f"   -> Feed Key: {ik}")
                        info = {"ik": ik, "raw_ltp": None, "raw_close": None, "raw_volume": None, "raw_oi": None, "raw_bid": None, "raw_ask": None, "raw_greeks": None}
                        if feed.HasField("full_feed"):
                            ff = feed.full_feed
                            if ff.HasField("market_ff"):
                                mff = ff.market_ff
                                info["raw_ltp"] = mff.ltpc.ltp if mff.ltpc.ltp else None
                                info["raw_close"] = mff.ltpc.close if mff.ltpc.close else None
                                info["raw_oi"] = mff.eoi if mff.eoi else None
                                if mff.HasField("market_ohlc") and mff.market_ohlc.ohlc:
                                    for ohlc in mff.market_ohlc.ohlc:
                                        if ohlc.interval == "1d":
                                            info["raw_volume"] = ohlc.volume
                                if mff.HasField("market_level") and mff.market_level.bid_ask:
                                    quotes = mff.market_level.bid_ask
                                    if len(quotes) > 0 and quotes[0].price:
                                        info["raw_bid"] = (quotes[0].price, quotes[0].qtn)
                                    if len(quotes) > 1 and quotes[1].price:
                                        info["raw_ask"] = (quotes[1].price, quotes[1].qtn)
                                if ff.market_ff.HasField("option_greeks"):
                                    og = ff.market_ff.option_greeks
                                    info["raw_greeks"] = {
                                        "iv": og.iv,
                                        "delta": og.delta,
                                        "gamma": og.gamma,
                                        "theta": og.theta,
                                        "vega": og.vega,
                                    }
                            elif ff.HasField("index_ff"):
                                iff = ff.index_ff
                                info["raw_ltp"] = iff.ltpc.ltp if iff.ltpc.ltp else None
                                info["raw_close"] = iff.ltpc.close if iff.ltpc.close else None
                        elif feed.HasField("ltpc"):
                            info["raw_ltp"] = feed.ltpc.ltp if feed.ltpc.ltp else None
                            info["raw_close"] = feed.ltpc.close if feed.ltpc.close else None
                        
                        decoded_by_key[ik] = info
                        print(f"      Decoded: {info}")
                else:
                    print(f"   Non-binary message received: {msg}")
            except TimeoutError:
                print("   (Socket wait timeout reached)")
                break

        print(f"\nTotal Frames Received: {frame_count}")
        print(f"Total Unique Instrument Keys Decoded: {len(decoded_by_key)}")

if __name__ == "__main__":
    asyncio.run(main())
