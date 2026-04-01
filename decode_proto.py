"""
Matriks WebSocket mesaj decoder — v2 (daha sağlam)

Yaklaşım:
1. Topic string'i bul (mx/symbol/THYAO@lvl2 gibi)
2. Binary kısmından tüm potansiyel 0x29 anchor'larını dene
3. En iyi sonucu ver — field'ların tutarlılığını doğrula
4. Cross-check: high >= last >= low, prev mantıklı mı?

Format: [tag 1 byte][double 8 byte] — 9 byte per field
Tags: 0x29=last, 0x31=ask, 0x39=bid, 0x41=high, 0x49=prev, 0x51=open, 0x61=low, 0x71=vol
"""

import struct
import re
import time

FIELD_MAP = {
    0x29: "last",
    0x31: "ask",
    0x39: "bid",
    0x41: "high",
    0x49: "prev",
    0x51: "open",
    0x61: "low",
    0x69: "high2",
    0x71: "vol",
}

# Makul fiyat aralıkları (sembol tipine göre)
PRICE_RANGES = {
    "stock":      (0.01,  100_000),   # Hisseler
    "derivative": (0.001, 200_000),   # Vadeli
}


def _try_decode_from(binary: bytes, start: int) -> dict | None:
    """Verilen offset'ten 9-byte adımlarla decode et."""
    vals = {}
    pos = start
    while pos + 9 <= len(binary):
        tag = binary[pos]
        field = FIELD_MAP.get(tag)
        if field:
            try:
                v = struct.unpack_from('<d', binary, pos + 1)[0]
                # NaN/Inf kontrolü
                if v != v or v == float('inf') or v == float('-inf'):
                    pos += 9
                    continue
                if 0.0001 < abs(v) < 100_000_000:
                    vals[field] = round(v, 6)
            except:
                pass
        pos += 9
    return vals if vals.get("last") else None


def _validate(vals: dict, sym_type: str) -> bool:
    """
    Decode sonucunun mantıklı olup olmadığını kontrol et.
    Birden fazla field varsa cross-check yap.
    """
    last = vals.get("last")
    if not last or last <= 0:
        return False

    mn, mx = PRICE_RANGES.get(sym_type, (0.001, 100_000))
    if not (mn <= last <= mx):
        return False

    high = vals.get("high")
    low  = vals.get("low")
    prev = vals.get("prev")
    bid  = vals.get("bid")
    ask  = vals.get("ask")

    # High/Low kontrolü
    if high and low:
        if high < low:
            return False
        if last > high * 1.01 or last < low * 0.99:
            return False

    # Bid/Ask spread kontrolü (çok geniş spread = yanlış decode)
    if bid and ask and bid > 0 and ask > 0:
        spread_pct = abs(ask - bid) / last
        if spread_pct > 0.10:  # %10'dan geniş spread şüpheli
            return False
        if ask < bid:  # Ask < Bid olamaz
            return False

    # Prev kontrolü: önceki kapanış çok farklı olmamalı
    if prev and prev > 0:
        diff = abs(last - prev) / prev
        if diff > 0.20:  # %20'den fazla günlük değişim şüpheli
            return False

    return True


def decode_mx_message(raw: bytes) -> dict | None:
    """
    Matriks binary mesajını decode eder.
    Birden fazla anchor dener, en tutarlı sonucu döner.
    """
    try:
        if not raw or len(raw) < 40:
            return None

        text = raw.decode('latin-1')

        # Sembol ve topic bul
        m = re.search(r'mx/symbol/([A-Z0-9]+)@(lvl\w+)', text)
        is_deriv = False
        lvl_suffix = None
        if m:
            lvl_suffix = m.group(2)
        else:
            m = re.search(r'mx/derivative/([A-Z0-9]+)', text)
            is_deriv = True
        if not m:
            return None

        sym = m.group(1)
        sym_type = "derivative" if is_deriv else "stock"

        # Topic sonu bul
        if lvl_suffix:
            topic_end = text.find(f'@{lvl_suffix}') + len(lvl_suffix) + 1
        else:
            topic_end = text.find(sym) + len(sym)
        binary = raw[topic_end:]

        # Tüm potansiyel 0x29 anchor'larını bul
        candidates = []
        for i in range(min(80, len(binary) - 9)):
            if binary[i] == 0x29:
                try:
                    v = struct.unpack_from('<d', binary, i + 1)[0]
                    mn, mx = PRICE_RANGES[sym_type]
                    if mn <= abs(v) <= mx and v == v:
                        candidates.append((i, v))
                except:
                    pass

        if not candidates:
            return None

        # Her candidate'i dene, validation'dan geçeni al
        best = None
        best_score = -1

        for start, last_val in candidates:
            vals = _try_decode_from(binary, start)
            if not vals:
                continue
            if not _validate(vals, sym_type):
                continue

            # Puanlama: ne kadar çok field varsa o kadar iyi
            score = len(vals)
            # High/Low ve bid/ask uyumlu ise bonus
            if vals.get("high") and vals.get("low"):
                score += 2
            if vals.get("bid") and vals.get("ask"):
                score += 2

            if score > best_score:
                best_score = score
                best = vals

        if not best:
            return None

        # PnL için değişim yüzdesi
        if best.get("prev") and best["prev"] > 0:
            best["chg_pct"] = round((best["last"] - best["prev"]) / best["prev"] * 100, 2)

        return {
            "symbol":  sym,
            "type":    sym_type,
            "ts":      int(time.time()),
            **best,
        }

    except Exception:
        return None
