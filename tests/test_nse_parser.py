# ruff: noqa: E501
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


def test_parse_full_modern_cm_udiff_columns() -> None:
    """Full modern (08-Jul-2024+) CM-UDiFF ticker columns must parse."""
    payload = b"""TCKRSYMB,SCTYSRS,TRADDT,OPNPRIC,HGHPRIC,LWPRIC,CLSPRIC,PRVSCLSGPRIC,LASTPRIC,TTLTRADGVOL,TTLTRADGVAL,TTLNBOFTRAD,ISIN\nRELIANCE,EQ,2024-07-08,1500,1530,1490,1520,1498,1519,100000,152000000,5000,INE002A01018\n"""
    records, errors = parse_bhavcopy(payload)
    assert not errors
    assert records[0].symbol == "RELIANCE"
    assert records[0].close == Decimal("1520")
    assert records[0].volume == 100000
    assert records[0].turnover == Decimal("152000000")
    assert records[0].trade_count == 5000
    assert records[0].previous_close == Decimal("1498")
    assert records[0].last_price == Decimal("1519")
    assert records[0].trade_date.isoformat() == "2024-07-08"


def test_parse_legacy_aliases() -> None:
    """Legacy pre-08-Jul-2024 header aliases must actually parse."""
    payload = b"""SctySrs,TCKRSYMB,TRAD_DT,OPN_PRIC,HGH_PRIC,LW_PRIC,CLS_PRIC,TTL_TRADG_VOL,TTL_TRADG_VAL,TTL_NB_OF_TRAD,ISIN\nEQ,IDEA,2020-01-15,120.0,125.0,118.0,124.0,500000,60000000,9000,INE824A01032\n"""
    records, errors = parse_bhavcopy(payload)
    assert not errors
    assert records[0].symbol == "IDEA"
    assert records[0].open == Decimal("120.0")
    assert records[0].volume == 500000
    assert records[0].trade_date.isoformat() == "2020-01-15"


def test_parse_legacy_alias_date1() -> None:
    """DATE1 trade-date alias from legacy archives."""
    payload = b"""SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TTL_TRADG_VOL,DATE1,PREVCLOSE,LAST\nLIVTEST,EQ,10,11,9,10,1000,2019-03-15,9.5,10\n"""
    records, errors = parse_bhavcopy(payload)
    assert not errors
    assert records[0].symbol == "LIVTEST"
    assert records[0].trade_date.isoformat() == "2019-03-15"
