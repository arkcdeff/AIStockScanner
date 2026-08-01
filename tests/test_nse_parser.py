from decimal import Decimal

from app.data.nse_parser import parse_bhavcopy, parse_delivery


BHAVCOPY = b"""SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\nRELIANCE,EQ,1500,1530,1490,1520,1519,1498,100000,152000000,30-JUL-2026,5000,INE002A01018\nTCS,EQ,3000,3050,2990,3040,3038,2980,50000,152000000,30-JUL-2026,3000,INE467B01029\nBAD,EQ,10,5,8,9,9,9,100,900,30-JUL-2026,4,INE000000001\n"""

DELIVERY = b"""SYMBOL,SERIES,DELIV_QTY,DELIV_PER\nRELIANCE,EQ,60000,60.00\nTCS,EQ,25000,50.00\n"""


def test_parse_bhavcopy_and_delivery() -> None:
    delivery = parse_delivery(DELIVERY)
    records, errors = parse_bhavcopy(BHAVCOPY, delivery=delivery)

    assert len(records) == 2
    assert len(errors) == 1
    assert records[0].symbol == "RELIANCE"
    assert records[0].close == Decimal("1520")
    assert records[0].delivery_quantity == 60000
    assert records[0].delivery_percentage == Decimal("60.00")


def test_parse_new_udiff_headers() -> None:
    payload = b"""TckrSymb,SctySrs,TradDt,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol,ISIN\nINFY,EQ,2026-07-30,1400,1425,1395,1420,10000,INE009A01021\n"""
    records, errors = parse_bhavcopy(payload)
    assert not errors
    assert records[0].symbol == "INFY"
    assert records[0].volume == 10000
