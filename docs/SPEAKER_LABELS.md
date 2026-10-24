# Speaker labels (v1.5.0)

Optional. Tick **Settings > Transcription > Label speakers**. Each transcript line then gets a label:
`Speaker 1: ...`, `Speaker 2: ...` (numbered in the order people first speak).

- `.txt`: consecutive lines of one speaker are one paragraph starting `Speaker N:`.
- `.srt` / `.vtt`: every cue starts `Speaker N:`.
- `.json` / `.csv`: each segment has a `speaker` field/column (`Speaker 1`); lines where the speaker
  changed mid-sentence are split at the change and marked `split_by_speaker`.
- `<name>.speakers.json` (next to the JSON): type real names, e.g. `"Speaker 1": "Thomas"`, then run
  `python speaker_labels.py rename "<name>"`. It rewrites the json/csv/txt/srt/vtt with the names, keeps the
  original label in `speaker_label`, and saves a one-time `<name>.before-rename.json` backup first.
  Re-running a file never throws away names you typed.

Everything runs locally on the CPU (about 20 s for a 3-minute video). If anything goes wrong the
transcript is still saved, just without labels, and the log says why.

## Setup

    venv\Scripts\python.exe -m pip install sherpa-onnx
    venv\Scripts\python.exe speaker_labels.py download   # ~33 MB into models\diarization
    venv\Scripts\python.exe speaker_labels.py check

Settings: `label_speakers` (off), `diarization_models_dir` (empty = `models\diarization`),
`speaker_count` (0 = detect automatically; set 2 if you know there are two people),
optional `speaker_threshold` (0.5) and `diarization_embedding_model` (file name in the models folder).

## Licenses (all permissive, no GPL, no Hugging Face token, nothing gated)

| Part | What | License |
|---|---|---|
| sherpa-onnx (k2-fsa), PyPI `sherpa-onnx` | diarization engine (segmentation + embeddings + clustering) | Apache-2.0 |
| onnxruntime (pulled in by sherpa-onnx) | model runtime | MIT |
| `sherpa-onnx-pyannote-segmentation-3-0` | pyannote/segmentation-3.0 converted to ONNX, downloaded from the sherpa-onnx GitHub release (not the gated HF repo) | MIT (Copyright 2022 CNRS; LICENSE file ships in the folder) |
| `wespeaker_en_voxceleb_resnet34_LM.onnx` | WeSpeaker ResNet34-LM speaker embedding, from the sherpa-onnx GitHub release | WeSpeaker code Apache-2.0; weights CC-BY-4.0 (inherit the VoxCeleb2 dataset license, per WeSpeaker docs/pretrained.md) |

CC-BY-4.0 attribution: speaker embedding model by the WeSpeaker project (wenet-e2e), trained on
VoxCeleb2 (J. S. Chung, A. Nagrani, A. Zisserman, "VoxCeleb2: Deep Speaker Recognition", INTERSPEECH 2018).
Used unmodified (ONNX export by sherpa-onnx).

Fully Apache-2.0 alternative (tested, same result with `speaker_count` = 2 on the test video, but it
split one voice into extra speakers in automatic mode): `3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx`
(3D-Speaker, Apache-2.0). Set `diarization_embedding_model` to use it. The 3D-Speaker CAM++ VoxCeleb model
(Apache-2.0) was also tried and did not separate the voices on the test video, so it is not the default.

Not used: pyannote.audio pipelines from Hugging Face (gated, need a token), and any GPL tool.
