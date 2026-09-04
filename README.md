# acoustic-watermark-lab

**A covert communication channel inside ordinary audio files.**

Send an encrypted message hidden in a song. The recipient plays the file, hears
nothing unusual, and reads the message with the right password. No attachment,
no metadata, no visible change — the message is baked into the sound itself.

Verified end-to-end through **WhatsApp, Telegram, and Gmail**: the message
survives the lossy re-encoding these platforms apply and arrives intact.

> Unlike forensic watermarking tools (which embed a fixed 128-bit token to track
> ownership), this embeds **arbitrary encrypted text** — AES-GCM, key-dithered,
> zero-knowledge. Without the password you cannot read the message, forge it, or
> even confirm one exists.

---

## What it does

- **Arbitrary text payload** — not a fixed token. Type any message; it is
  AES-GCM encrypted before embedding, so the ciphertext is all anyone can
  observe (and only if they know to look).
- **Survives real-world transport** — verified through WhatsApp (Opus 128k),
  Telegram, Gmail, and stereo→mono downmix. Reed-Solomon coding absorbs the
  codec noise.
- **Zero-knowledge** — the QIM quantisation grid is dithered by a key derived
  from room + password. Without the key you cannot read, forge, or blind-detect
  the channel.
- **Invisible in the file** — only the 1.2–3.6 kHz energy balance shifts,
  inaudibly. The file keeps its full length, stereo image, and plays normally
  everywhere.

## Quickstart

```bash
git clone https://github.com/send2you/acoustic-watermark-lab
cd acoustic-watermark-lab
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
.venv\Scripts\activate         # Windows
pip install -r requirements.txt
python src/server.py
```

Open **http://127.0.0.1:5000/**. No separate `ffmpeg` install — it is bundled.

Prefer zero setup? Open `notebooks/try_it.ipynb` in **Google Colab** and run it
on your own song.

In the browser:
- **Hide** → type a message → pick a song or voice clip → download the result.
- **Reveal** → upload the file + room + password → read the message.

### Command line

```bash
python src/cover.py path/to/song.mp3 "meet at 8 by the kiosk"
```

## How it works

`d = 0.5·ln(E1/E2)` — the log energy ratio between the 1.2–2.4 kHz and
2.4–3.6 kHz bands. A lossy codec must preserve this mid-band ratio to keep
audio sounding natural, so QIM-quantised changes to `d` survive re-encoding.

The payload is **tiled across the entire file**: every frame carries a bit, so
no clean unmodified stretch remains as an internal reference. Each bit
accumulates votes from many copies; that redundancy lets the per-frame step
stay small, which is what keeps the change below standard steganalysis tests.

```
src/       cover.py (engine) · codec.py · room.py · rs.py · server.py
web/       gui.html (Hide / Reveal UI)
notebooks/ try_it.ipynb (Colab)
```

## Findings

- **Survives real platforms** — tested through WhatsApp (Opus 128k re-encode),
  Telegram, and Gmail. Audio files embedded in Word documents, ZIP archives, and
  similar containers also pass through intact.
- **Hard to detect without a reference** — against a purpose-built ML classifier
  (logistic regression, leave-one-track-out, 11 tracks): AUC 0.63. Not
  separable by first- or second-order signal tests alone.
- **Capacity** grows with audio length — a 4–5 minute song holds a full
  sentence at the default settings.

## License

MIT — see [LICENSE](LICENSE).
