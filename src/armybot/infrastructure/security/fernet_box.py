import json
from typing import Any

from cryptography.fernet import Fernet


class FernetSecretBox:
    def __init__(self, key: str) -> None:
        self.fernet = Fernet(key.encode())

    def encrypt_json(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return self.fernet.encrypt(raw).decode()

    def decrypt_json(self, encrypted_payload: str) -> dict[str, Any]:
        raw = self.fernet.decrypt(encrypted_payload.encode())
        return json.loads(raw.decode())
