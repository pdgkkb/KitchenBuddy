# Fully-local voice — setup & the on-device wake-word upgrade

## What runs where

| Model | Job | Runs on |
|---|---|---|
| faster-whisper | speech → text ("hears us") | your machine |
| Qwen 2.5-7B-Instruct | understands, replies, calls tools | your machine (Ollama) |
| Piper | text → speech ("speaks to us") | your machine |
| wake-word spotter | detects "Hey Chef" | **browser** (see caveat) |

## Install

```bash
# in the backend venv
pip install faster-whisper piper-tts
```

Piper voice files (one `.onnx` + its `.onnx.json`) from
<https://huggingface.co/rhasspy/piper-voices>. Good picks:
`fr_FR-siwis-medium`, `en_US-amy-medium`. Put them anywhere and point
`PIPER_VOICE` at the `.onnx`.

Whisper models download automatically on first use. `base` is fine on CPU;
`small` or `distil-large-v3` are more accurate; a CUDA GPU makes any of them
instant (set `WHISPER_DEVICE=cuda`).

## `.env`

```ini
# local LLM
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b-instruct
OPENAI_API_KEY=ollama            # any non-empty value; Ollama ignores it

# local voice
SPEECH_PROVIDER=local
WHISPER_MODEL=base               # base | small | distil-large-v3 | /path
WHISPER_DEVICE=auto              # auto | cpu | cuda
STT_LANGUAGE=                    # "" auto, or fr / en
PIPER_VOICE=/models/fr_FR-siwis-medium.onnx
WAKE_WORD=hey chef
```

Start Ollama with the model pulled (`ollama pull qwen2.5:7b-instruct`), then the
backend. `/api/status` will show `"local": true` and `"voice": true`.

## The wake-word caveat, and how to go fully on-device

As shipped, `wake.js` uses the browser's `SpeechRecognition` to spot the phrase.
It only recognises one phrase and needs no model files — but in Chrome the audio
is sent to Google *while hands-free is armed*. The UI makes arming a deliberate,
revocable toggle and says so. The actual command, once the phrase fires, is
transcribed by **local** Whisper.

To remove that last cloud hop, swap in **openWakeWord** running in the browser
with `onnxruntime-web` (no server round-trip, nothing leaves the device):

1. `npm i onnxruntime-web`.
2. Put three ONNX files in `frontend/public/wake/`: the openWakeWord
   `melspectrogram.onnx`, `embedding_model.onnx`, and a wake model
   (e.g. `hey_jarvis_v0.1.onnx`, or a custom "hey chef" model trained with the
   openWakeWord training notebook).
3. In a Web Audio `AudioWorklet`, capture 16 kHz mono frames → melspectrogram →
   embedding → wake model; fire when the score crosses ~0.5 for a few frames.
4. Replace the `SpeechRecognition` body of `startWakeWord` with that pipeline,
   keeping the same `{ phrase, onWake, onError, onStatus } → { stop }` shape so
   `ChatScreen` doesn't change.

This is specced rather than built because it needs the model files and real
device testing to tune the threshold — worth doing as its own focused pass.
