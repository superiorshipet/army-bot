from uuid import uuid4

from armybot.application.ports.repositories import CredentialRepository
from armybot.application.ports.security import SecretBox
from armybot.domain.entities import User, UserCredential
from armybot.domain.enums import CredentialProvider


class CredentialService:
    def __init__(self, credentials: CredentialRepository, secret_box: SecretBox) -> None:
        self.credentials = credentials
        self.secret_box = secret_box

    async def save(self, user: User, provider: CredentialProvider, payload: dict) -> None:
        encrypted = self.secret_box.encrypt_json(payload)
        existing = await self.credentials.get(user.id, provider)
        credential = UserCredential(
            id=existing.id if existing else uuid4(),
            user_id=user.id,
            provider=provider,
            encrypted_payload=encrypted,
        )
        await self.credentials.upsert(credential)

    async def load_all(self, user: User) -> dict[str, dict]:
        rows = await self.credentials.list_for_user(user.id)
        return {
            credential.provider.value: self.secret_box.decrypt_json(credential.encrypted_payload)
            for credential in rows
        }
