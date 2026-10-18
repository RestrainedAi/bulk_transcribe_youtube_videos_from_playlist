"""Runs one optional ASR engine (Voxtral or Granite) on a 16 kHz mono WAV.

Executed by asr_engines.run_worker with venv_asr_eval's Python (torch lives only there).
Writes JSON: {"engine", "text", "chunks": [{"start", "end", "text"}], "seconds", "peak_vram_gb"}.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import soundfile as sf
import torch

SR = 16000


def chunks_of(wav, seconds: int):
    step = seconds * SR
    for i in range(0, len(wav), step):
        yield i / SR, min(len(wav), i + step) / SR, wav[i:i + step]


def run_voxtral(model_dir: str, wav):
    from transformers import AutoProcessor, VoxtralForConditionalGeneration
    proc = AutoProcessor.from_pretrained(model_dir)
    model = VoxtralForConditionalGeneration.from_pretrained(model_dir, torch_dtype=torch.bfloat16, device_map="cuda")
    import os
    import tempfile
    out = []
    tmpdir = tempfile.mkdtemp(prefix="voxtral_chunks_")
    for n, (start, end, piece) in enumerate(chunks_of(wav, 600)):  # 10-minute chunks keep memory steady
        chunk_path = os.path.join(tmpdir, f"chunk{n:04d}.wav")
        sf.write(chunk_path, piece, SR)
        inp = proc.apply_transcription_request(language="en", audio=chunk_path, model_id=model_dir)
        inp = inp.to("cuda", dtype=torch.bfloat16)
        ids = model.generate(**inp, max_new_tokens=4000, do_sample=False)
        out.append(dict(start=start, end=end, text=proc.batch_decode(ids[:, inp.input_ids.shape[1]:], skip_special_tokens=True)[0].strip()))
        os.remove(chunk_path)
    os.rmdir(tmpdir)
    return out


def run_granite(model_dir: str, wav):
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
    proc = AutoProcessor.from_pretrained(model_dir)
    model = AutoModelForSpeechSeq2Seq.from_pretrained(model_dir, torch_dtype=torch.bfloat16, device_map="cuda")
    tok = proc.tokenizer
    chat = [{"role": "system", "content": "You are Granite, developed by IBM. You are a helpful AI assistant."},
            {"role": "user", "content": "<|audio|>can you transcribe the speech into a written format?"}]
    prompt = tok.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
    out = []
    for start, end, piece in chunks_of(wav, 30):
        inp = proc(prompt, torch.tensor(piece).unsqueeze(0), device="cuda", return_tensors="pt").to("cuda")
        ids = model.generate(**inp, max_new_tokens=300, do_sample=False, num_beams=4)
        out.append(dict(start=start, end=end, text=tok.decode(ids[0, inp["input_ids"].shape[-1]:], skip_special_tokens=True).strip()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if not torch.cuda.is_available():
        print("CUDA is not available in the ASR venv; refusing to run on CPU.", file=sys.stderr)
        return 3
    wav, sr = sf.read(a.audio, dtype="float32")
    if sr != SR:
        print(f"expected 16 kHz audio, got {sr}", file=sys.stderr)
        return 2
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    t0 = time.time()
    if a.engine == "voxtral-mini-3b":
        chunks = run_voxtral(a.model_dir, wav)
    elif a.engine == "granite-2b":
        chunks = run_granite(a.model_dir, wav)
    else:
        print(f"unknown engine {a.engine}", file=sys.stderr)
        return 2
    res = dict(engine=a.engine, text=" ".join(c["text"] for c in chunks if c["text"]), chunks=chunks,
               seconds=round(time.time() - t0, 1), peak_vram_gb=round(torch.cuda.max_memory_allocated() / 1e9, 2))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(f"{a.engine}: {len(res['text'].split())} words in {res['seconds']} s, peak {res['peak_vram_gb']} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
