from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO, Iterable


class NSEParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    symbol: str
    series: str
    quantity: int | None
    percentage: Decimal | None


@dataclass(frozen=True, slots=True)
class PriceRecord:
    symbol: str
    series: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    previous_close: Decimal | None
    last_price: Decimal | None
    volume: int
    turnover: Decimal | None
    trade_count: int | None
    isin: str | None
    delivery_quantity: int | None = None
    delivery_percentage: Decimal | None = None


ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("SYMBOL", "TCKRSYMB", "TCKR_SYMB"),
    "series": ("SERIES", "SCTYSRS", "SCTY_SRS"),
    "trade_date": ("TIMESTAMP", "TRADDT", "TRAD_DT", "DATE1"),
    "open": ("OPEN", "OPNPRIC", "OPN_PRIC"),
    "high": ("HIGH", "HGHPRIC", "HGH_PRIC"),
    "low": ("LOW", "LWPRIC", "LW_PRIC"),
    "close": ("CLOSE", "CLSPRIC", "CLS_PRIC"),
    "previous_close": ("PREVCLOSE", "PRVSCLSGPRIC", "PRVS_CLSG_PRIC"),
    "last_price": ("LAST", "LASTPRIC", "LAST_PRIC"),
    "volume": ("TOTTRDQTY", "TTLTradgVol", "TTLTRADGVOL", "TTL_TRADG_VOL"),
    "turnover": ("TOTTRDVAL", "TTLTRADGVAL", "TTL_TRADG_VAL"),
    "trade_count": ("TOTALTRADES", "TTLNBOFTRAD", "TTL_NB_OF_TRAD"),
    "isin": ("ISIN",),
}

DELIVERY_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("SYMBOL", "TCKRSYMB"),
    "series": ("SERIES", "SCTYSRS"),
    "quantity": ("DELIV_QTY", "DELIVERYQUANTITY", "DLVRYQTY"),
    "percentage": ("DELIV_PER", "DELIVERYPERCENTAGE", "DLVRYPER"),
}


def _normalise_header(value: str) -> str:
    return "".join(ch for ch in value.strip().upper() if ch.isalnum() or ch == "_")


def _value(row: dict[str, str], aliases: Iterable[str], *, required: bool = False) -> str | None:
    for alias in aliases:
        value = row.get(_normalise_header(alias))
        if value is not None and value.strip() not in {"", "-", "NA", "N.A."}:
            return value.strip()
    if required:
        raise NSEParseError(f"Required column missing or empty: {next(iter(aliases))}")
    return None


def _decimal(value: str | None, field: str, *, required: bool = False) -> Decimal | None:
    if value is None:
        if required:
            raise NSEParseError(f"Missing numeric value: {field}")
        return None
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise NSEParseError(f"Invalid {field}: {value!r}") from exc


def _integer(value: str | None, field: str, *, required: bool = False) -> int | None:
    number = _decimal(value, field, required=required)
    return int(number) if number is not None else None


def _date(value: str) -> date:
    formats = ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y")
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise NSEParseError(f"Unsupported trade date: {value!r}")


def _read_csv_bytes(payload: bytes, file_name: str) -> str:
    if file_name.lower().endswith(".zip") or payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                raise NSEParseError("ZIP file does not contain a CSV file")
            payload = archive.read(csv_names[0])
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise NSEParseError("Unable to decode CSV file")


def _rows(payload: bytes, file_name: str) -> list[dict[str, str]]:
    text = _read_csv_bytes(payload, file_name)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise NSEParseError("CSV has no header")
    result: list[dict[str, str]] = []
    for raw in reader:
        result.append({_normalise_header(key): (value or "") for key, value in raw.items() if key})
    return result


def parse_delivery(payload: bytes, file_name: str = "delivery.csv") -> dict[tuple[str, str], DeliveryRecord]:
    records: dict[tuple[str, str], DeliveryRecord] = {}
    for row in _rows(payload, file_name):
        symbol = _value(row, DELIVERY_ALIASES["symbol"])
        if not symbol:
            continue
        series = (_value(row, DELIVERY_ALIASES["series"]) or "EQ").upper()
        record = DeliveryRecord(
            symbol=symbol.upper(),
            series=series,
            quantity=_integer(_value(row, DELIVERY_ALIASES["quantity"]), "delivery quantity"),
            percentage=_decimal(
                _value(row, DELIVERY_ALIASES["percentage"]), "delivery percentage"
            ),
        )
        records[(record.symbol, record.series)] = record
    return records


def parse_bhavcopy(
    payload: bytes,
    file_name: str = "bhavcopy.csv",
    delivery: dict[tuple[str, str], DeliveryRecord] | None = None,
    allowed_series: set[str] | None = None,
) -> tuple[list[PriceRecord], list[str]]:
    delivery = delivery or {}
    allowed_series = allowed_series or {"EQ", "BE", "BZ", "SM", "ST"}
    records: list[PriceRecord] = []
    errors: list[str] = []

    for row_number, row in enumerate(_rows(payload, file_name), start=2):
        try:
            symbol = _value(row, ALIASES["symbol"], required=True).upper()  # type: ignore[union-attr]
            series = (_value(row, ALIASES["series"]) or "EQ").upper()
            if series not in allowed_series:
                continue
            trade_date = _date(_value(row, ALIASES["trade_date"], required=True))  # type: ignore[arg-type]
            open_price = _decimal(_value(row, ALIASES["open"], required=True), "open", required=True)
            high = _decimal(_value(row, ALIASES["high"], required=True), "high", required=True)
            low = _decimal(_value(row, ALIASES["low"], required=True), "low", required=True)
            close = _decimal(_value(row, ALIASES["close"], required=True), "close", required=True)
            volume = _integer(_value(row, ALIASES["volume"], required=True), "volume", required=True)
            assert open_price is not None and high is not None and low is not None
            assert close is not None and volume is not None
            if min(open_price, high, low, close) < 0 or high < low or volume < 0:
                raise NSEParseError("Invalid OHLC or volume values")
            delivery_record = delivery.get((symbol, series))
            records.append(
                PriceRecord(
                    symbol=symbol,
                    series=series,
                    trade_date=trade_date,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    previous_close=_decimal(
                        _value(row, ALIASES["previous_close"]), "previous close"
                    ),
                    last_price=_decimal(_value(row, ALIASES["last_price"]), "last price"),
                    volume=volume,
                    turnover=_decimal(_value(row, ALIASES["turnover"]), "turnover"),
                    trade_count=_integer(_value(row, ALIASES["trade_count"]), "trade count"),
                    isin=(_value(row, ALIASES["isin"]) or None),
                    delivery_quantity=delivery_record.quantity if delivery_record else None,
                    delivery_percentage=delivery_record.percentage if delivery_record else None,
                )
            )
        except (NSEParseError, AssertionError) as exc:
            errors.append(f"Row {row_number}: {exc}")
    return records, errors


def load_bytes(path: str | Path | BinaryIO) -> tuple[bytes, str]:
    if hasattr(path, "read"):
        stream = path
        return stream.read(), getattr(stream, "name", "upload.csv")
    file_path = Path(path)
    return file_path.read_bytes(), file_path.name
