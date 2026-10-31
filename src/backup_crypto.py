"""Streaming authenticated backup encryption; keys never enter the backup itself."""
import base64
import csv
import hashlib
import os
import subprocess
import tempfile
import struct
from pathlib import Path

MAGIC = b"RRBACKUP1\x00"
BLOCK = 1024 * 1024


class BackupError(RuntimeError):
    """An operator-safe error; never include token-bearing URLs or response bodies."""


def protect_file(path):
    path = Path(path)
    if os.name == "nt":
        result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                capture_output=True, text=True, timeout=15, check=True)
        sid = next(csv.reader(result.stdout.strip().splitlines()))[-1]
        result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                                 f"*{sid}:F", "*S-1-5-18:F"],
                                capture_output=True, timeout=15)
        if result.returncode:
            raise BackupError("Could not restrict access to the backup secret file")
    else:
        path.chmod(0o600)


def private_write(path, data, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise BackupError("Secret file already exists; refusing to replace it")
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=".private-")
    temporary = Path(name)
    try:
        os.close(handle)
        protect_file(temporary)
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            # An atomic hard link creates the destination only if it is absent.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def generate_key(path):
    private_write(path, base64.urlsafe_b64encode(os.urandom(32)) + b"\n", exclusive=True)


def load_key(path):
    try:
        key = base64.b64decode(Path(path).read_bytes().strip(), altchars=b"-_", validate=True)
    except (OSError, ValueError):
        raise BackupError("Backup recovery key unavailable; run the explicit keygen command first") from None
    if len(key) != 32:
        raise BackupError("Backup recovery key must contain 32 random bytes, base64 encoded")
    return key


def key_id(key):
    return hashlib.sha256(key).hexdigest()[:16]


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def encrypt_file(source, target, key, *, context):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    cipher = AESGCM(key)
    with Path(source).open("rb") as src, Path(target).open("xb") as dest:
        dest.write(MAGIC)
        index = 0
        while True:
            block = src.read(BLOCK)
            nonce = os.urandom(12)
            aad = context.encode("utf-8") + b"\x00" + struct.pack(">Q", index)
            encrypted = cipher.encrypt(nonce, block, aad)
            dest.write(struct.pack(">I", len(encrypted)) + nonce + encrypted)
            index += 1
            if not block:  # Authenticated terminator detects truncation at a chunk boundary.
                break


def decrypt_file(source, target, key, *, context):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    source, target = Path(source), Path(target)
    created = False
    try:
        with source.open("rb") as src:
            if src.read(len(MAGIC)) != MAGIC:
                raise BackupError("Unsupported encrypted backup format")
            cipher, index = AESGCM(key), 0
            with target.open("xb") as dest:
                created = True
                while True:
                    length = src.read(4)
                    if len(length) != 4:
                        raise BackupError("Encrypted backup is incomplete")
                    length = struct.unpack(">I", length)[0]
                    if not 16 <= length <= BLOCK + 16:
                        raise BackupError("Invalid encrypted chunk size")
                    nonce, encrypted = src.read(12), src.read(length)
                    if len(nonce) != 12 or len(encrypted) != length:
                        raise BackupError("Encrypted backup is incomplete")
                    aad = context.encode("utf-8") + b"\x00" + struct.pack(">Q", index)
                    block = cipher.decrypt(nonce, encrypted, aad)
                    index += 1
                    if not block:
                        if src.read(1):
                            raise BackupError("Unexpected bytes after encrypted backup")
                        break
                    dest.write(block)
    except (InvalidTag, BackupError):
        if created:
            target.unlink(missing_ok=True)
        raise BackupError("Backup authentication failed: wrong key, wrong snapshot, or corrupted data") from None
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise


def encrypt_json(value, key, context):
    import json
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, json.dumps(value).encode("utf-8"), context.encode("utf-8"))


def decrypt_json(data, key, context):
    import json
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    try:
        return json.loads(AESGCM(key).decrypt(data[:12], data[12:], context.encode("utf-8")))
    except (ValueError, InvalidTag):
        raise BackupError("Encrypted credential/session cache could not be authenticated") from None
