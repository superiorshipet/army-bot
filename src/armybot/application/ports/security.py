from typing import Protocol


class SecretBox(Protocol):
    def encrypt_json(self, payload: dict) -> str: ...

    def decrypt_json(self, encrypted_payload: str) -> dict: ...
