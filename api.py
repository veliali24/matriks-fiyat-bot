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
from yahoo_feed import get_yahoo_price_dict, get_all_yahoo_prices

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
    """Tek sembol fiyatı — önce Yahoo'da bak, yoksa Matriks'te."""
    symbol = symbol.upper().strip()
    
    # Yahoo'da ara
    yahoo_data = get_yahoo_price_dict(symbol)
    if yahoo_data:
        return {
            **format_price(yahoo_data),
            "source": "yahoo",
        }
    
    # Matriks'te ara
    data = get_price(symbol)
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"{symbol} için fiyat verisi yok. Matriks'te bu sembol açık olmalı."
        )

    return {
        **format_price(data),
        "source": "matriks",
    }


@app.get("/api/fiyatlar")
def get_prices(
    semboller: str = None,
    _: str = Depends(verify_api_key)
):
    """
    Tüm veya seçili semboller (Yahoo + Matriks merge, Yahoo öncelikli).
    ?semboller=THYAO,GARAN,AKBNK
    """
    all_matriks = get_all_prices()
    all_yahoo = get_all_yahoo_prices()
    
    # Yahoo'yu Matriks'in üstüne yapıştır (merge)
    merged = dict(all_matriks)
    for sym, data in all_yahoo.items():
        merged[sym] = {**data, "source": "yahoo"}
    
    if semboller:
        symbols = [s.strip().upper() for s in semboller.split(",") if s.strip()]
        result = {}
        missing = []
        for sym in symbols:
            if sym in merged:
                data = merged[sym]
                result[sym] = {
                    **format_price(data),
                    "source": data.get("source", "matriks"),
                }
            else:
                missing.append(sym)
        return {
            "prices": result,
            "missing": missing,
            "count": len(result),
        }

    return {
        "prices": {
            sym: {
                **format_price(data),
                "source": data.get("source", "matriks"),
            }
            for sym, data in merged.items()
        },
        "count": len(merged),
    }


@app.get("/api/yahoo/{symbol}")
def get_yahoo_only(symbol: str, _: str = Depends(verify_api_key)):
    """Sadece Yahoo Finance verisi."""
    symbol = symbol.upper().strip()
    data = get_yahoo_price_dict(symbol)
    
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"{symbol} Yahoo Finance'da bulunamadı."
        )
    
    return {
        **format_price(data),
        "source": "yahoo",
    }
