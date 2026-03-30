"""
Matriks WebSocket mesajlarını Protobuf ile decode eder.
mx/symbol/THYAO@lvl2 formatındaki binary mesajları çözer.
"""

import struct
import re


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Protobuf varint okur, (değer, yeni_pos) döner."""
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos


def decode_matriks_message(raw: bytes) -> dict | None:
    """
    Matriks binary mesajını decode eder.
    Format başı: 0x30 + length + topic + data
    """
    try:
        if not raw or raw[0] != 0x30:
            return None

        # Topic string'i bul
        text_part = raw.decode('latin-1')
        
        # Sembol adını çıkar
        symbol_match = re.search(r'mx/symbol/([A-Z0-9]+)@lvl2', text_part)
        deriv_match = re.search(r'mx/derivative/([A-Z0-9]+)@lvl2', text_part)
        
        symbol = None
        is_derivative = False
        
        if symbol_match:
            symbol = symbol_match.group(1)
        elif deriv_match:
            symbol = deriv_match.group(1)
            is_derivative = True
        else:
            return None

        # Topic'in bittiği yeri bul
        topic_end = text_part.find('@lvl2')
        if topic_end == -1:
            return None
        topic_end += 5  # '@lvl2' uzunluğu

        # Binary kısmı
        binary = raw[topic_end:]
        
        # Double float'ları çıkar (field tag + value format)
        # Protobuf field 9 (double) = tag 0x41, field 10 = 0x49 vs.
        prices = {}
        field_names = {
            0x29: "last",      # field 5
            0x31: "bid",       # field 6  
            0x39: "ask",       # field 7
            0x41: "high",      # field 8
            0x49: "low",       # field 9
            0x51: "open",      # field 10
            0x59: "prev_close", # field 11
            0x61: "price",     # field 12
            0x69: "change",    # field 13
        }
        
        i = 0
        while i < len(binary) - 8:
            tag = binary[i]
            if tag in field_names and i + 9 <= len(binary):
                try:
                    val = struct.unpack_from('<d', binary, i + 1)[0]
                    if 0.001 < abs(val) < 1000000 and val == val:  # NaN check
                        prices[field_names[tag]] = round(val, 4)
                except:
                    pass
            i += 1

        if prices:
            return {
                "symbol": symbol,
                "type": "derivative" if is_derivative else "stock",
                **prices
            }

    except Exception as e:
        pass
    return None


def parse_raw_string(raw_str: str) -> dict | None:
    """
    String formatındaki raw binary veriyi parse eder.
    Örn: "b'0u\\x00\\x14mx/symbol/THYAO@lvl2...'"
    """
    try:
        # b'...' formatındaki string'den bytes'a çevir
        raw_bytes = eval(raw_str) if raw_str.startswith("b'") else raw_str.encode()
        return decode_matriks_message(raw_bytes)
    except:
        return None


# Test
if __name__ == "__main__":
    import json
    from pathlib import Path
    
    data = json.loads(Path("network_analysis.json").read_text())
    
    print("=== Decode Edilen Fiyatlar ===\n")
    results = {}
    
    for msg in data["websocket_messages"]:
        if msg.get("type") != "message_raw":
            continue
        
        raw_str = msg.get("data", "")
        if not raw_str:
            continue
        
        # String'den bytes'a çevir
        try:
            raw_bytes = eval(raw_str)
            if not isinstance(raw_bytes, bytes):
                continue
        except:
            continue
        
        result = decode_matriks_message(raw_bytes)
        if result and result.get("last"):
            sym = result["symbol"]
            if sym not in results:
                results[sym] = result
    
    # Sonuçları göster
    for sym, data in sorted(results.items()):
        last = data.get("last", "?")
        bid = data.get("bid", "?")
        ask = data.get("ask", "?")
        print(f"  {sym:10} Last: {last:12} Bid: {bid:12} Ask: {ask}")
    
    print(f"\nToplam: {len(results)} sembol")
