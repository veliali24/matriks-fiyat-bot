"""
Ana başlatma dosyası.
Bot + FastAPI aynı anda çalışır.

Çalıştırma:
  python main.py
"""

import asyncio
import logging
import threading
import uvicorn
from pathlib import Path

Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.WARNING,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/main.log", encoding="utf-8"),
    ],
)

from price_stream import price_stream_loop
from yahoo_feed import yahoo_price_loop
from api import app as fastapi_app
import notifier


async def run_all():
    """Bot stream + FastAPI birlikte çalışır."""

    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=8000,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    await asyncio.gather(
        price_stream_loop(),
        yahoo_price_loop(),
        server.serve(),
    )


if __name__ == "__main__":
    print("🚀 Matriks Fiyat API başlatılıyor...")
    print("   API: http://localhost:8000")
    print("   Docs: http://localhost:8000/docs")
    asyncio.run(run_all())
