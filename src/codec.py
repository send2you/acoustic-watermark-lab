"""Framing helpers: how the encrypted body is split into Reed-Solomon blocks
and interleaved so a burst of codec damage is spread across many blocks.

Pure integer/byte arithmetic, no dependencies.
"""

# Reed-Solomon block size (data bytes per block, before parity).
CHUNK = 150


def parity_for(k):
    """Parity bytes for a block of k data bytes, about a quarter rate.

    Integer arithmetic only, and no rounding function: rounding differs between
    languages (Python rounds 14.5 down, JavaScript up), and both ends must agree
    to the byte or the receiver reads the wrong number of symbols.
    """
    return max(10, min(64, 2 * -(-k // 8)))


def chunk_sizes(total):
    """How a body of `total` bytes is split into Reed-Solomon blocks.

    Both ends derive this from the length in the header, so no block boundaries
    need to be transmitted.
    """
    out = []
    left = total
    while left > 0:
        n = min(CHUNK, left)
        out.append(n)
        left -= n
    return out


def coded_size(total):
    return sum(n + parity_for(n) for n in chunk_sizes(total))


def interleave(data, depth=8):
    """Spread a burst of damage across separate Reed-Solomon positions."""
    if len(data) <= depth:
        return data
    out = bytearray()
    for r in range(depth):
        out += data[r::depth]
    return bytes(out)


def deinterleave(data, depth=8):
    if len(data) <= depth:
        return data
    n = len(data)
    out = [0] * n
    pos = 0
    for r in range(depth):
        idxs = range(r, n, depth)
        for j, idx in enumerate(idxs):
            out[idx] = data[pos + j]
        pos += len(idxs)
    return bytes(out)
