import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: str
    nonce: str
    format_version: int = 1


class CredentialCipher:
    _AAD = b"vida-provider-profile:v1"

    def __init__(self, master_key: bytes):
        if len(master_key) != 32:
            raise ValueError("master key must contain exactly 32 bytes")
        self._aes = AESGCM(master_key)

    def encrypt(self, plaintext: str) -> EncryptedCredential:
        if not plaintext:
            raise ValueError("apiKey must not be empty")
        nonce = os.urandom(12)
        ciphertext = self._aes.encrypt(nonce, plaintext.encode("utf-8"), self._AAD)
        return EncryptedCredential(
            base64.b64encode(ciphertext).decode("ascii"),
            base64.b64encode(nonce).decode("ascii"),
        )

    def decrypt(self, envelope: EncryptedCredential) -> str:
        if envelope.format_version != 1:
            raise ValueError("unsupported credential format")
        plaintext = self._aes.decrypt(
            base64.b64decode(envelope.nonce),
            base64.b64decode(envelope.ciphertext),
            self._AAD,
        )
        return plaintext.decode("utf-8")
