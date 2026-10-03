"""Generate private, persistent signing keys without displaying their contents."""
import os
from pathlib import Path
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def initialize_keys():
    directory = Path("/run/keys")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    targets = {
        "signing.pem": lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()),
        "session.secret": lambda: secrets.token_urlsafe(48).encode(),
    }
    for name, generate in targets.items():
        path = directory / name
        if path.exists():
            continue
        # Exclusive creation prevents silently replacing existing credentials.
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(generate())
    print("Persistent signing and session keys ready")


if __name__ == "__main__":
    initialize_keys()
