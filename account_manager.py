"""
Matriks hesap yöneticisi.
Çoklu hesap desteği ve otomatik rotasyon.
Hesaplar accounts.json'da şifreli tutulur.
"""

import json
import os
import time
import base64
from pathlib import Path
from cryptography.fernet import Fernet


ACCOUNTS_FILE = Path("accounts.json")
KEY_FILE = Path(".accounts_key")


def _get_key() -> bytes:
    """Şifreleme anahtarını alır, yoksa oluşturur."""
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    KEY_FILE.chmod(0o600)
    return key


def _encrypt(text: str) -> str:
    f = Fernet(_get_key())
    return f.encrypt(text.encode()).decode()


def _decrypt(text: str) -> str:
    f = Fernet(_get_key())
    return f.decrypt(text.encode()).decode()


def load_accounts() -> list[dict]:
    """Hesapları yükler."""
    if not ACCOUNTS_FILE.exists():
        return []
    data = json.loads(ACCOUNTS_FILE.read_text())
    accounts = []
    for acc in data:
        accounts.append({
            "username": _decrypt(acc["username"]),
            "password": _decrypt(acc["password"]),
            "added_by": acc.get("added_by"),
            "added_at": acc.get("added_at"),
            "active": acc.get("active", True),
        })
    return accounts


def save_account(username: str, password: str, added_by: int) -> bool:
    """Yeni hesap ekler."""
    accounts = load_accounts()
    
    # Aynı kullanıcı adı varsa güncelle
    for acc in accounts:
        if acc["username"] == username:
            acc["password"] = password
            acc["added_by"] = added_by
            _save_all(accounts)
            return False  # Güncellendi
    
    accounts.append({
        "username": username,
        "password": password,
        "added_by": added_by,
        "added_at": int(time.time()),
        "active": True,
    })
    _save_all(accounts)
    return True  # Yeni eklendi


def _save_all(accounts: list[dict]):
    """Tüm hesapları şifreleyerek kaydeder."""
    encrypted = []
    for acc in accounts:
        encrypted.append({
            "username": _encrypt(acc["username"]),
            "password": _encrypt(acc["password"]),
            "added_by": acc.get("added_by"),
            "added_at": acc.get("added_at"),
            "active": acc.get("active", True),
        })
    ACCOUNTS_FILE.write_text(json.dumps(encrypted, indent=2))


def delete_account(username: str) -> bool:
    """Hesabı siler."""
    accounts = load_accounts()
    before = len(accounts)
    accounts = [a for a in accounts if a["username"] != username]
    if len(accounts) < before:
        _save_all(accounts)
        return True
    return False


def list_accounts() -> list[str]:
    """Hesap listesi (şifresiz)."""
    return [a["username"] for a in load_accounts() if a["active"]]


class AccountRotator:
    """
    Hesaplar arasında otomatik döner.
    Session her 25 dakikada bir yenilenir.
    """
    
    SESSION_DURATION = 25 * 60  # 25 dakika
    
    def __init__(self):
        self._current_index = 0
        self._session_start = 0
        self._session_key = None
        self._ws_url = None
    
    def get_current(self) -> dict | None:
        """Mevcut aktif hesabı döner."""
        accounts = [a for a in load_accounts() if a["active"]]
        if not accounts:
            return None
        self._current_index = self._current_index % len(accounts)
        return accounts[self._current_index]
    
    def rotate(self):
        """Bir sonraki hesaba geç."""
        accounts = [a for a in load_accounts() if a["active"]]
        if accounts:
            self._current_index = (self._current_index + 1) % len(accounts)
            self._session_key = None
            self._ws_url = None
    
    def is_session_expired(self) -> bool:
        """Session süresi doldu mu?"""
        return time.time() - self._session_start > self.SESSION_DURATION
    
    def set_session(self, session_key: str, ws_url: str):
        """Yeni session bilgilerini kaydet."""
        self._session_key = session_key
        self._ws_url = ws_url
        self._session_start = time.time()
    
    @property
    def session_key(self) -> str | None:
        return self._session_key
    
    @property
    def ws_url(self) -> str | None:
        return self._ws_url


# Global rotator instance
rotator = AccountRotator()
