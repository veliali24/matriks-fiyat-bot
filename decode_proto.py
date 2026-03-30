"""
Matriks WebSocket mesajlarını decode eder.
Format: mx/symbol/SEMBOL@lvl2 + binary (Protobuf benzeri)
Her field: [tag 1 byte] + [IEEE 754 double 8 byte] = 9 byte

Field haritası (0x29'dan itibaren 9 byte adımlarla):
  0x29: last      (son fiyat)
  0x31: ask       (satış)
  0x39: bid       (alış)
  0x41: high      (günlük yüksek)
  0x49: prev      (önceki kapanış)
  0x51: open      (açılış)
  0x61: low       (günlük düşük)
  0x69: high2     (?)
  0x71: vol       (hacim)
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


def decode_mx_message(raw: bytes) -> dict | None:
    """
    Matriks binary mesajını decode eder.
    0x29 tag'ını anchor olarak kullanır, 9 byte adımlarla okur.
    """
    try:
        if not raw or len(raw) < 50:
            return None

        text = raw.decode('latin-1')

        # Sembol bul
        m = re.search(r'mx/symbol/([A-Z0-9]+)@lvl2', text)
        is_deriv = False
        if not m:
            m = re.search(r'mx/derivative/([A-Z0-9]+)', text)
            is_deriv = True
        if not m:
            return None

        sym = m.group(1)

        # Topic sonu: '@lvl2' + 5 byte
        topic_end = text.find('@lvl2') + 5
        binary = raw[topic_end:]

        # 0x29 (last price tag) anchor'ı bul
        start = -1
        for i in range(min(60, len(binary) - 9)):
            if binary[i] == 0x29:
                try:
                    v = struct.unpack_from('<d', binary, i + 1)[0]
                    if 0.001 < abs(v) < 1_000_000 and v == v:
                        start = i
                        break
                except:
                    pass

        if start < 0:
            return None

        # Sabit 9-byte adımlarla field'ları oku
        vals = {}
        pos = start
        while pos + 9 <= len(binary):
            tag = binary[pos]
            field = FIELD_MAP.get(tag)
            if field:
                try:
                    v = struct.unpack_from('<d', binary, pos + 1)[0]
                    if abs(v) > 0.0001 and v == v and abs(v) < 100_000_000:
                        vals[field] = round(v, 4)
                except:
                    pass
            pos += 9

        if not vals.get("last"):
            return None

        return {
            "symbol": sym,
            "type": "derivative" if is_deriv else "stock",
            "ts": int(time.time()),
            **vals,
        }

    except Exception:
        return None
