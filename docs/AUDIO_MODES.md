# Audio modes and the needs-review folder (v1.4.0)

Each job has an audio mode. Default is **Speech**. You set another mode when you
re-run a file from needs-review (Queue page > "Re-run from needs-review...").

| Mode | What runs | Why |
|---|---|---|
| Speech | Your saved decode profile (default `auto`: speech settings, retries with the music profile if coverage is poor) | Unchanged from 1.3.x |
| Speech over music | faster-whisper `speech_music` profile: VAD relaxed (threshold 0.25, min silence 1.5 s, pad 0.6 s; VAD off on CPU), `no_speech_threshold` 0.8, `condition_on_previous_text` off, silence-skipping off | Talk under a music bed is often scored "not speech" by the default VAD (0.5) and dropped. |
| Music/lyrics | Optional vocal separation, then Whisper large-v3 with the existing `music` profile (VAD off, no prompt, `no_speech_threshold` 0.9) | Separating the voice from the backing track is the biggest single win for lyrics. |

Note on `no_speech_threshold`: in faster-whisper a segment is dropped as silence
when `no_speech_prob > no_speech_threshold` (and log-prob is low). So *raising* it
keeps more quiet or music-covered speech; lowering it would drop more. We raise it.

## Music/lyrics: the choice

* **Separator:** [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator), MIT license.
* **Model:** `vocals_mel_band_roformer.ckpt` (Mel-Band RoFormer vocals, Kimberley Jensen), MIT license on Hugging Face. Downloaded on first use to the separator's model folder; never bundled in this repo.
* **Transcriber:** the existing faster-whisper / CTranslate2 large-v3 on the GPU. No new ASR model.

It is optional. It is **not installed** on VR_ZONE yet (it pulls in PyTorch,
about 2-3 GB). Without it, Music/lyrics still runs on the full mix with the music
profile and the log says "audio-separator is not installed". To turn it on:

    venv\Scripts\python.exe -m pip install "audio-separator[gpu]"

Setting `music_vocal_separation` (default on) switches it off if you prefer.

### Rejected (license)

* Demucs / HTDemucs: code is MIT, but the pretrained weights are "provided only for scientific purposes" (maintainer, facebookresearch/demucs#327). Not used.
* Spleeter: MIT code, weights have no stated license.
* Open-Unmix `umxl`: CC-BY-NC-SA (non-commercial). (`umxhq` is MIT but much weaker.)
* Any GPL tool or model (for example some UVR dereverb checkpoints): excluded.
* Caveat: many separation models, including permissively tagged ones, were trained partly on MUSDB18, an academic dataset. Fine for personal transcription; check again before shipping it commercially.

## needs-review

A failure that is about the *content* (no speech found, no audio track, unreadable
audio) is not a crash. The watcher copies the file into
`<library>\transcription\needs-review`, checks the SHA-256, removes the original,
and writes `<file>.needs-review.txt` with a plain-language reason. It is not counted
as FAILED. Crashes (worker exits, stalls, CUDA errors, all attempts failed) stay
in the Failed list as before.

The status badge on the Dashboard (where "LOAD: FAULT" used to be) says
"All good", "Working", "2 files need review: no speech found" or
"Engine crashed on 1 file: see log". Hover for an explanation; click it to open
the needs-review folder or the log. It clears itself when the files are moved out
or the failure is re-run.
