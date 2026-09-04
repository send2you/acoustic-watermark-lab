# -*- coding: utf-8 -*-
"""Hide an encrypted message inside a file the user brings - a song, a voice
clip - at full fidelity, and read it back.

Nothing is appended and no marker is added to the container: the file stays a
normal audio file to anything that inspects it. The message lives *in the sound*,
as a tiny change to the energy balance between two mid-range bands (1.2-2.4 vs
2.4-3.6 kHz) - the region a lossy codec must keep, so it survives being shared.

The rest of the spectrum (bass, highs) and the stereo image are left untouched,
and the file keeps its full length. The payload is tiled across the *entire*
file: every frame carries a bit, so there is no clean unmodified stretch left
as an internal reference, and each bit accumulates votes from many copies.

Built from three small pieces: the crypto (room.py), Reed-Solomon (rs.py) and
the framing helpers (codec.py).
"""

import hashlib
import os
import struct
import subprocess
import sys
import tempfile
import wave
import zlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codec
import room as roomlib
from rs import rs_encode, rs_decode

RATE = 44100
FN = 2048
FH = 2048
REPEAT = 4                       # per-copy repeat; the whole-file tiling adds more
DELTA = 0.22                     # small step: the whole-file redundancy pays for it
MIN_VOTES = 8                    # required repetitions of each bit across the file
BAND = (1200, 2400, 2400, 3600)
MARK = b"\x9a\x53"
HDR_PARITY = 10
HDR_LEN = 4 + HDR_PARITY


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ---- framing (identical to wm_full, so the two can interoperate) ----
def _bins():
    b1lo, b1hi, b2lo, b2hi = BAND
    return ((round(b1lo * FN / RATE), round(b1hi * FN / RATE)),
            (round(b2lo * FN / RATE), round(b2hi * FN / RATE)))


def _dither(room, pw, n):
    """A secret per-frame offset of the QIM grid, in [0, DELTA).

    This is what makes the mark undetectable. Plain QIM snaps d = 0.5 ln(E1/E2)
    to fixed multiples of DELTA, so frac(d/DELTA) collapses to 0 on every
    watermarked frame - a spike any steganalyst sees at once. Here the grid is
    shifted by delta_i on frame i, and delta_i is derived from the room+password.
    To anyone without the key, frac(d/DELTA) = frac(delta_i/DELTA) is spread
    uniformly across [0,1): no spike, and no way even to tell a mark is present.
    We subtract delta_i back before reading, so decoding is unaffected.
    """
    seed = int.from_bytes(
        hashlib.sha256(("awl-dither|" + room + "|" + pw).encode()).digest()[:8],
        "big")
    return np.random.default_rng(seed).random(n) * DELTA


def _pack(text):
    raw = text.encode("utf-8")
    comp = zlib.compress(raw, 9)
    return bytes([1]) + comp if len(comp) < len(raw) else bytes([0]) + raw


def _unpack(data):
    return zlib.decompress(data[1:]).decode("utf-8") if data[0] == 1 else data[1:].decode("utf-8")


def _payload_bits(text, room, pw):
    body = roomlib.sealb(_pack(text), room, pw)
    coded = codec.interleave(roomlib.encode_body(body))
    payload = rs_encode(MARK + struct.pack(">H", len(body)), HDR_PARITY) + coded
    return np.unpackbits(np.frombuffer(payload, np.uint8))


def samples_needed(text, room, pw):
    return len(_payload_bits(text, room, pw)) * MIN_VOTES * FH + FN


# ---- QIM over the WHOLE file, key-dithered grid ----
def _embed(sig, bits, room, pw):
    """Watermark every frame of the signal.

    The payload is tiled across the whole file: frame j carries
    bit[(j // REPEAT) % len(bits)]. Two things follow. There is no unmarked
    stretch an attacker could hold up as an internal reference, and every bit is
    repeated many times over the file, so the per-frame nudge (DELTA) can stay
    small - which is what keeps the mark below a steganalyst's second-order tests.
    """
    g1, g2 = _bins()
    F = (len(sig) - FN) // FH + 1
    dith = _dither(room, pw, F)
    nb = len(bits)
    out = sig.copy()
    for j in range(F):
        bit = int(bits[(j // REPEAT) % nb])
        pos = j * FH
        S = np.fft.rfft(out[pos:pos + FN])
        E1 = np.sum(np.abs(S[g1[0]:g1[1]])**2) + 1e-12
        E2 = np.sum(np.abs(S[g2[0]:g2[1]])**2) + 1e-12
        d = 0.5 * np.log(E1 / E2)
        delta = dith[j]                            # secret shift of the grid
        q = int(round((d - delta) / DELTA))
        if q % 2 != bit:
            q += 1 if (d - delta) >= q * DELTA else -1
        adj = (q * DELTA + delta) - d              # snap to the shifted grid
        S[g1[0]:g1[1]] *= np.exp(adj / 2)
        S[g2[0]:g2[1]] *= np.exp(-adj / 2)
        out[pos:pos + FN] = np.fft.irfft(S, FN)
    return out


def _frame_bits(x, off, dith, nframes):
    """Raw per-frame bit read for `nframes` frames starting at `off`."""
    g1, g2 = _bins()
    out = np.empty(nframes, np.int8)
    for j in range(nframes):
        pos = off + j * FH
        if pos + FN > len(x):
            return out[:j]
        S = np.fft.rfft(x[pos:pos + FN])
        E1 = np.sum(np.abs(S[g1[0]:g1[1]])**2) + 1e-12
        E2 = np.sum(np.abs(S[g2[0]:g2[1]])**2) + 1e-12
        out[j] = int(round((0.5 * np.log(E1 / E2) - dith[j]) / DELTA)) % 2
    return out


# ---- audio I/O via ffmpeg (any input -> 44.1k stereo float) ----
def _load_stereo(path):
    raw = subprocess.run(
        [ffmpeg(), "-v", "error", "-i", path, "-ac", "2", "-ar", str(RATE),
         "-f", "s16le", "-"], capture_output=True).stdout
    if not raw:
        raise ValueError("ffmpeg could not read this file as audio")
    return np.frombuffer(raw, np.int16).astype(np.float64).reshape(-1, 2) / 32768.0


def _load_mono(path):
    raw = subprocess.run(
        [ffmpeg(), "-v", "error", "-i", path, "-ac", "1", "-ar", str(RATE),
         "-f", "s16le", "-"], capture_output=True).stdout
    if not raw:
        raise ValueError("ffmpeg could not read this file as audio")
    return np.frombuffer(raw, np.int16).astype(np.float64) / 32768.0


def _write_stereo_to(out_path, st, fmt):
    """Encode full-length stereo to the chosen output format."""
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "full.wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((np.clip(st, -1, 1) * 32767).astype(np.int16).reshape(-1).tobytes())
        if fmt == "wav":
            os.replace(wav, out_path)
            return
        args = {"mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
                "flac": ["-c:a", "flac"]}.get(fmt, ["-c:a", "libmp3lame", "-b:a", "320k"])
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", wav] + args + [out_path],
                       check=True)


# ---- public API ----
def encode_cover(in_path, text, room, pw, out_path, out_fmt="mp3"):
    """Watermark `in_path` with `text`; write the full-length result to out_path.

    The message is embedded into the mid channel (L+R) across the whole file, so
    only the 1.2-3.6 kHz band moves and the file keeps its length and stereo
    image. Returns (seconds_marked, total_seconds); marked == total here.
    """
    bits = _payload_bits(text, room, pw)
    nb = len(bits)

    st = _load_stereo(in_path)
    F = (len(st) - FN) // FH + 1
    if F < nb * MIN_VOTES:
        need_s = nb * MIN_VOTES * FH / RATE
        raise ValueError(
            f"message too long for this file: it needs at least {need_s:.0f}s of "
            f"audio but the file is {len(st)/RATE:.0f}s. Use a longer file or "
            f"shorter text.")

    L = st[:, 0]
    R = st[:, 1]
    mid = (L + R) / 2.0
    side = (L - R) / 2.0
    mid2 = _embed(mid, bits, room, pw)

    out = st.copy()
    out[:, 0] = mid2 + side
    out[:, 1] = mid2 - side
    _write_stereo_to(out_path, out, out_fmt)
    total = len(st) / RATE
    return total, total


def decode_cover(path, room, pw, max_off=9000):
    """Read the hidden text from a watermarked file, or raise ValueError.

    The grid is dithered by the key, so the header itself only lines up for the
    right room+password: a wrong key matches nothing, reads no bodies, and fails
    fast - and it cannot even tell whether a watermark was present. That is why
    the error is deliberately the same whether the file is clean or the key is
    wrong; distinguishing them would leak that a message exists.
    """
    x = _load_mono(path)                                # mono downmix == mid channel
    hdr_frames = HDR_LEN * 8 * REPEAT
    for off in range(0, max_off, 64):
        F = (len(x) - off - FN) // FH + 1
        if F < hdr_frames:
            break
        dith = _dither(room, pw, F)
        # find the frame alignment from the header (first copy), cheaply
        hfb = _frame_bits(x, off, dith, hdr_frames)
        if len(hfb) < hdr_frames:
            break
        hbits = np.array(
            [1 if hfb[i*REPEAT:(i+1)*REPEAT].sum() * 2 >= REPEAT else 0
             for i in range(HDR_LEN * 8)], np.uint8)
        try:
            header = rs_decode(np.packbits(hbits).tobytes(), HDR_PARITY)
        except Exception:
            continue
        if header[:2] != MARK:
            continue
        blen = struct.unpack(">H", header[2:4])[0]
        if not (28 <= blen <= 4000):
            continue
        # a real frame: now vote each bit across every copy in the whole file
        nb = (HDR_LEN + codec.coded_size(blen)) * 8
        fb = _frame_bits(x, off, dith, F)
        idx = (np.arange(len(fb)) // REPEAT) % nb
        votes = np.zeros(nb); cnt = np.zeros(nb)
        np.add.at(votes, idx, fb)
        np.add.at(cnt, idx, 1)
        bits = (votes * 2 >= cnt).astype(np.uint8)
        try:
            body = roomlib.decode_body(
                codec.deinterleave(np.packbits(bits).tobytes()[HDR_LEN:]), blen)
            return _unpack(roomlib.unsealb(body, room, pw))
        except Exception:
            continue
    raise ValueError("no hidden message here, or wrong room/password")


def capacity_chars(seconds):
    """Rough guide: how many UTF-8 bytes of message an audio of this length can hold."""
    frames = (int(seconds * RATE) - FN) // FH + 1
    max_payload = frames // MIN_VOTES // 8          # bytes available for payload
    max_coded = max_payload - HDR_LEN               # after header
    max_body = max_coded * 4 // 5                   # RS parity ~1/4 of data
    return max(0, max_body - 29)                    # AES-GCM: nonce+tag+flag = 29 bytes


if __name__ == "__main__":
    # offline self-test: pass a path to any audio file (a song, a voice clip)
    #   python cover.py path/to/audio.mp3
    if len(sys.argv) < 2:
        print("usage: python cover.py <audio-file>")
        sys.exit(1)
    song = sys.argv[1]
    msg = sys.argv[2] if len(sys.argv) > 2 else "meet at 8 by the kiosk"
    room, pw = "room1", "password1"
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "wm.mp3")
        marked, total = encode_cover(song, msg, room, pw, out)
        print(f"encoded: marked {marked:.0f}s of a {total:.0f}s file -> {os.path.getsize(out)//1024} KB mp3")
        got = decode_cover(out, room, pw)
        print(f"decoded: {got!r}  {'OK' if got == msg else 'MISMATCH'}")
        try:
            decode_cover(out, room, "wrong")
            print("wrong password: LEAK (bad)")
        except ValueError:
            print("wrong password: rejected OK")
