"""Room crypto: turn a room name + password into a key, and seal / unseal the
payload with AES-GCM. Plus the Reed-Solomon body coding used by the watermark.

The room name is the salt, so the same password in two different rooms yields
two unrelated keys and nothing is precomputable across rooms.
"""

import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import codec
from rs import rs_decode, rs_encode

MAGIC = 0xC7
VERSION = 1
KDF_ROUNDS = 600_000


def room_key(room, password):
    salt = hashlib.sha256(
        b"awl-v1:room:" + room.strip().lower().encode("utf-8")
    ).digest()
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, KDF_ROUNDS, 32
    )


def encode_body(body):
    """Reed-Solomon code the body, block by block."""
    out = bytearray()
    pos = 0
    for n in codec.chunk_sizes(len(body)):
        out += rs_encode(body[pos:pos + n], codec.parity_for(n))
        pos += n
    return bytes(out)


def decode_body(coded, total):
    out = bytearray()
    pos = 0
    for n in codec.chunk_sizes(total):
        width = n + codec.parity_for(n)
        out += rs_decode(coded[pos:pos + width], codec.parity_for(n))
        pos += width
    return bytes(out)


def sealb(data, room, password):
    """Encrypt raw bytes (a compressed payload) for the given room + password."""
    key = room_key(room, password)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, bytes(data), bytes([MAGIC, VERSION]))
    return nonce + ct


def unsealb(blob, room, password):
    if len(blob) < 28:
        raise ValueError("too small")
    key = room_key(room, password)
    try:
        return AESGCM(key).decrypt(blob[:12], blob[12:], bytes([MAGIC, VERSION]))
    except Exception:
        raise ValueError("Wrong room or wrong password")
