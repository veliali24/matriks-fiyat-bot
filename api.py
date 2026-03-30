"""
Matriks Fiyat API
FastAPI ile gerçek zamanlı fiyat servisi.

Endpoints:
  GET /api/fiyat/{symbol}          — tek sembol
  GET /api/fiyatlar                — tüm semboller
  GET /api/fiyatlar?semboller=THYAO,GARAN  — seçili semboller
  GET /health                      — sistem durumu
"""

import time
import os
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from price_stream import get_price, get_all_prices, _last_update, _stream_running

load_dotenv()

app = FastAPI(
    title="Matriks Fiyat API",
    description="Gerçek zamanlı hisse fiyatları",
    version="1.0.0",
)

# CORS — PHP platformundan erişim için
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Production'da platfom domain'ini yaz
    allow_methods=["GET"],
    allow_headers=["*"],
)

# API Key auth
API_KEY = os.getenv("API_KEY", "")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

STALE_THRESHOLD = 60  # 60 saniye güncellenmemişse "stale" sayılır


def verify_api_key(key: str = Security(api_key_header)):
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=401, detail="Geçersiz API key")
    return key


def format_price(data: dict) -> dict:
    """Fiyat verisini API formatına çevirir."""
    now = time.time()
    ts = data.get("ts", 0)
    age = int(now - ts) if ts else None
    stale = age is not None and age > STALE_THRESHOLD

    return {
        "symbol": data.get("symbol"),
        "last": data.get("last"),
        "bid": data.get("bid"),
        "ask": data.get("ask"),
        "high": data.get("high"),
        "low": data.get("low"),
        "open": data.get("open"),
        "prev": data.get("prev"),
        "vol": data.get("vol"),
        "chg_pct": data.get("chg_pct"),
        "updated_at": ts,
        "age_seconds": age,
        "stale": stale,
    }


@app.get("/health")
def health():
    """Sistem durumu."""
    now = time.time()
    last = _last_update
    age = int(now - last) if last else None
    prices = get_all_prices()

    return {
        "status": "ok" if (age is not None and age < STALE_THRESHOLD) else "stale",
        "stream_running": _stream_running,
        "last_update_seconds_ago": age,
        "tracked_symbols": len(prices),
        "symbols": sorted(prices.keys()),
    }


@app.get("/api/fiyat/{symbol}")
def get_single_price(symbol: str, _: str = Depends(verify_api_key)):
    """Tek sembol fiyatı."""
    symbol = symbol.upper().strip()
    data = get_price(symbol)

    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"{symbol} için fiyat verisi yok. Matriks'te bu sembol açık olmalı."
        )

    return format_price(data)


@app.get("/api/fiyatlar")
def get_prices(
    semboller: str = None,
    _: str = Depends(verify_api_key)
):
    """
    Tüm veya seçili semboller.
    ?semboller=THYAO,GARAN,AKBNK
    """
    all_prices = get_all_prices()

    if semboller:
        symbols = [s.strip().upper() for s in semboller.split(",") if s.strip()]
        result = {}
        missing = []
        for sym in symbols:
            if sym in all_prices:
                result[sym] = format_price(all_prices[sym])
            else:
                missing.append(sym)
        return {
            "prices": result,
            "missing": missing,
            "count": len(result),
        }

    return {
        "prices": {sym: format_price(data) for sym, data in all_prices.items()},
        "count": len(all_prices),
    }
