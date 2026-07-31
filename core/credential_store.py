import os
import hashlib
import base64
import json
from typing import Optional, Dict, Any

from core.logger import AppLogger

_CRED_FILE = ".credentials.enc"
_KEY_FILE = ".credential_key"


def _derive_key(master_password: str) -> bytes:
    key = base64.urlsafe_b64encode(
        hashlib.sha256(master_password.encode()).digest()
    )
    return key


def _encrypt_data(data: Dict[str, str], master_password: str) -> str:
    key = _derive_key(master_password)
    try:
        from cryptography.fernet import Fernet
        f = Fernet(key)
        plain = json.dumps(data, sort_keys=True)
        token = f.encrypt(plain.encode())
        return token.decode()
    except ImportError:
        AppLogger.warn("cryptography not installed — using base64 (weak)")
        plain = json.dumps(data, sort_keys=True)
        return base64.urlsafe_b64encode(plain.encode()).decode()


def _decrypt_data(token: str, master_password: str) -> Optional[Dict[str, str]]:
    key = _derive_key(master_password)
    try:
        from cryptography.fernet import Fernet, InvalidToken
        f = Fernet(key)
        plain = f.decrypt(token.encode())
        return json.loads(plain.decode())
    except ImportError:
        try:
            plain = base64.urlsafe_b64decode(token.encode()).decode()
            return json.loads(plain)
        except Exception:
            return None
    except InvalidToken:
        AppLogger.error("Decryption failed — wrong master password?")
        return None
    except Exception as e:
        AppLogger.error(f"Decryption error: {e}")
        return None


def save_credentials(creds: Dict[str, str], master_password: str, path: str = _CRED_FILE) -> bool:
    try:
        token = _encrypt_data(creds, master_password)
        with open(path, "w") as f:
            f.write(token)
        AppLogger.success(f"Credentials saved to {path}")
        return True
    except Exception as e:
        AppLogger.error(f"Failed to save credentials: {e}")
        return False


def load_credentials(master_password: str, path: str = _CRED_FILE) -> Optional[Dict[str, str]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            token = f.read().strip()
        return _decrypt_data(token, master_password)
    except Exception as e:
        AppLogger.error(f"Failed to load credentials: {e}")
        return None


def has_credentials(path: str = _CRED_FILE) -> bool:
    return os.path.exists(path)
