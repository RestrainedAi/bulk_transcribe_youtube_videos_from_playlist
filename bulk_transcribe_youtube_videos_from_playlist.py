import os
import argparse
import asyncio
import re
import shutil
import subprocess
import sys
import psutil
import glob
import json
import traceback
from pathlib import Path
from pytubefix import YouTube, Playlist
import pandas as pd
import ctranslate2
from faster_whisper import WhisperModel, BatchedInferencePipeline
from numba import cuda
from tqdm import tqdm

import asr_engines
import transcript_search
import coverage_check
from output_paths import ensure_output_dirs
from watcher_settings import load_settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Constants for pricing
WHISPER_COST_PER_MINUTE = 0.006

convert_single_video = 1
use_spacy_for_sentence_splitting = 1
use_openai_api_for_transcription = 0
openai_api_key = 'REPLACE_WITH_YOUR_API_KEY'
max_simultaneous_youtube_downloads = 4
disable_cuda_override = 0
single_video_url = 'https://www.youtube.com/watch?v=sWAaJF9Wk0w'
playlist_url = 'https://www.youtube.com/playlist?list=PLjpPMe3LP1XKgqqzqz4j6M8-_M_soYxiV'

def add_to_system_path(new_path):
    if new_path not in os.environ["PATH"].split(os.pathsep):
        os.environ["PATH"] = new_path + os.pathsep + os.environ["PATH"]
    if sys.platform == "win32" and ' ' in new_path and not new_path.startswith('"') and not new_path.endswith('"'):
        os.environ["PATH"] = f'"{new_path}"' + os.pathsep + os.environ["PATH"].replace(new_path, "")

def ensure_ffmpeg_on_path():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    candidates = []
    env_dir = os.environ.get("FFMPEG_DIR")
    if env_dir:
        candidates.append(env_dir)
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        candidates.extend(
            [
                os.path.join(local, "Microsoft", "WinGet", "Links"),
                os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "ffmpeg", "bin"),
                os.path.join(
                    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                    "ffmpeg",
                    "bin",
                ),
            ]
        )
    else:
        candidates.extend(["/opt/homebrew/bin", "/usr/local/bin"])
    ffmpeg_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    ffprobe_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    for d in candidates:
        if not d:
            continue
        if os.path.isfile(os.path.join(d, ffmpeg_name)) or os.path.isfile(os.path.join(d, ffprobe_name)):
            add_to_system_path(d)
            return

def get_cuda_toolkit_path():
    home_dir = os.path.expanduser('~')
    if sys.platform in ["win32", "linux", "linux2", "darwin"]:
        anaconda_base_path = os.path.join(home_dir, "anaconda3", "pkgs")
    cuda_glob_pattern = os.path.join(anaconda_base_path, "cudatoolkit-*", "Library", "bin")
    cuda_paths = glob.glob(cuda_glob_pattern)
    if cuda_paths:
        return cuda_paths[0]
    return None


def gpu_available_for_whisper() -> bool:
    """
    Detect GPU availability for faster-whisper.
    Numba can report False on some Windows setups even when CTranslate2 sees CUDA.
    """
    try:
        if cuda.is_available():
            return True
    except Exception:
        pass
    try:
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def ensure_cuda_runtime_dlls_on_path() -> None:
    """
    Make sure CUDA runtime DLL folders from pip wheels are on PATH.
    This avoids runtime errors like missing cublas64_12.dll on Windows.
    """
    if sys.platform != "win32":
        return
    candidates = [
        os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "cublas", "bin"),
        os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "cudnn", "bin"),
        os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "cuda_nvrtc", "bin"),
        os.path.join(sys.prefix, "Lib", "site-packages", "ctranslate2"),
    ]
    for p in candidates:
        if os.path.isdir(p):
            add_to_system_path(p)

cuda_toolkit_path = get_cuda_toolkit_path()
print("CUDA Toolkit Path:", cuda_toolkit_path)
if cuda_toolkit_path:
    add_to_system_path(cuda_toolkit_path)

ensure_ffmpeg_on_path()
ensure_cuda_runtime_dlls_on_path()

max_workers_transcribe = psutil.cpu_count(logical=False)  # Number of physical cores

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_SETTINGS = load_settings(Path(REPO_ROOT))
OUTPUT_DIRS = ensure_output_dirs(Path(REPO_ROOT), _SETTINGS)
COMPLETED_AUDIO_DIR = str(OUTPUT_DIRS["completed_audio"])
COMPLETED_TRANSCRIPTS_DIR = str(OUTPUT_DIRS["completed_transcripts"])
COMPLETED_CSV_DIR = str(OUTPUT_DIRS["completed_csv"])
COMPLETED_JSON_DIR = str(OUTPUT_DIRS["completed_json"])

ALLOWED_WHISPER_MODELS = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v2",
    "large-v3",
    "distil-large-v3",
    "large-v3-turbo",
)
# 'speech' is the UI name of 'robust'. 'music' keeps singing (no speech filter). 'auto' runs
# speech first and redoes the file once with music when the coverage check flags it.
ALLOWED_DECODE_PROFILES = ("auto", "robust", "speech", "music", "legacy")
DEFAULT_DECODE_PROFILE = "auto"
AUTO_MUSIC_NO_SPEECH = 0.8


def resolve_decode_profile(settings: dict | None = None) -> str:
    data = settings if isinstance(settings, dict) else _SETTINGS
    raw = str(os.environ.get("WHISPER_DECODE_PROFILE") or data.get("whisper_decode_profile") or "").strip().lower()
    if raw == "speech":
        return "robust"
    return raw if raw in ALLOWED_DECODE_PROFILES else DEFAULT_DECODE_PROFILE


def resolve_coverage_limits(settings: dict | None = None) -> tuple[float, float]:
    data = settings if isinstance(settings, dict) else _SETTINGS
    try:
        block = float(data.get("coverage_max_block_s") or 30)
        gap = float(data.get("coverage_max_gap_s") or 60)
    except (TypeError, ValueError):
        block, gap = 30.0, 60.0
    return block, gap


def resolve_whisper_language(settings: dict | None = None) -> str | None:
    """'auto' (default) lets Whisper detect the language; otherwise an ISO code such as 'en'."""
    data = settings if isinstance(settings, dict) else _SETTINGS
    raw = str(os.environ.get("WHISPER_LANGUAGE") or data.get("whisper_language") or "auto").strip().lower()
    return None if raw in ("", "auto") else raw


def transcribe_kwargs_for_profile(profile: str, vad_filter: bool, language: str | None = "en") -> dict:
    """Decoding options. 'legacy' with language='en' reproduces the original behaviour exactly."""
    kwargs = {"beam_size": 5, "vad_filter": vad_filter, "language": language, "task": "transcribe"}
    if profile == "legacy":
        return kwargs
    if profile in ("speech", "auto"):
        profile = "robust"  # 'auto' starts with the speech settings
    kwargs.update(
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300},
        condition_on_previous_text=False,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        # Primes normal casing/punctuation, which is otherwise lost without previous-text conditioning.
        initial_prompt="Hello, and welcome. Here's what we'll cover today.",
        hallucination_silence_threshold=2.0,
        word_timestamps=True,
    )
    if profile == "music":
        # Singing is not 'speech' to the VAD, so the filter must stay off; no prompt, and
        # silence-skipping off, or whole verses get dropped and the next line is stretched.
        kwargs.update(
            vad_filter=False,
            hallucination_silence_threshold=None,
            initial_prompt=None,
            no_speech_threshold=0.9,
        )
        kwargs.pop("vad_parameters", None)
    return kwargs
DEFAULT_WHISPER_GPU_MODEL = "large-v3"
DEFAULT_WHISPER_CPU_MODEL = "medium"


def normalize_whisper_model(raw: str | None, fallback: str) -> str:
    name = str(raw or "").strip().lower()
    return name if name in ALLOWED_WHISPER_MODELS else fallback


def resolve_whisper_models(settings: dict | None = None) -> tuple[str, str]:
    data = settings if isinstance(settings, dict) else _SETTINGS
    gpu = normalize_whisper_model(
        os.environ.get("WHISPER_GPU_MODEL") or data.get("whisper_gpu_model"),
        DEFAULT_WHISPER_GPU_MODEL,
    )
    cpu = normalize_whisper_model(
        os.environ.get("WHISPER_CPU_MODEL") or data.get("whisper_cpu_model"),
        DEFAULT_WHISPER_CPU_MODEL,
    )
    return gpu, cpu


def build_whisper_attempts(
    use_gpu: bool,
    gpu_model: str | None = None,
    cpu_model: str | None = None,
    profile: str = "legacy",
) -> list[tuple[str, str, str, bool, str]]:
    gpu = normalize_whisper_model(gpu_model, DEFAULT_WHISPER_GPU_MODEL)
    cpu = normalize_whisper_model(cpu_model, DEFAULT_WHISPER_CPU_MODEL)
    attempts: list[tuple[str, str, str, bool, str]] = []
    if use_gpu and profile in ("robust", "speech", "auto", "music"):
        # RTX 3090 (24 GB) runs large-v3 in full float16 comfortably; int8 is the fallback.
        vad = profile != "music"
        attempts.extend(
            [
                (gpu, "cuda", "float16", vad, "gpu_fp16"),
                (gpu, "cuda", "int8_float16", vad, "gpu_int8"),
            ]
        )
    elif use_gpu:
        attempts.extend(
            [
                (gpu, "cuda", "int8_float16", True, "gpu_vad"),
                (gpu, "cuda", "int8_float16", False, "gpu_no_vad"),
            ]
        )
    attempts.extend(
        [
            (cpu, "cpu", "int8", False, "cpu"),
            (cpu, "cpu", "auto", False, "cpu_auto"),
        ]
    )
    return attempts


def _regex_sentence_splitter(text):
    text = remove_pagination_breaks(text)
    pattern = r'\.(?!\s*(com|net|org|io)\s)(?![0-9])'
    pattern += r'|[.!?]\s+'
    pattern += r'|\.\.\.(?=\s)'
    sentences = re.split(pattern, text)
    refined_sentences = []
    temp_sentence = ""
    for sentence in sentences:
        if sentence is not None:
            temp_sentence += sentence
            if temp_sentence.count('"') % 2 == 0:
                refined_sentences.append(temp_sentence.strip())
                temp_sentence = ""
    if temp_sentence:
        refined_sentences.append(temp_sentence.strip())
    return [s.strip() for s in refined_sentences if s.strip()]


nlp = None
if use_spacy_for_sentence_splitting:
    try:
        import spacy
        nlp = spacy.load("en_core_web_sm")
    except Exception as exc:
        print(f"SpaCy unavailable ({exc}); using regex sentence splitting.")
        nlp = None


def sophisticated_sentence_splitter(text):
    if nlp is not None:
        text = remove_pagination_breaks(text)
        doc = nlp(text)
        return [sent.text.strip() for sent in doc.sents if sent.text.strip()]
    return _regex_sentence_splitter(text)

def clean_filename(title):
    title = re.sub(r'[^\w\s-]', '', title)
    return re.sub(r'[-\s]+', '_', title).strip().lower()

async def download_audio(video):
    filename = clean_filename(video.title)
    base_filename = filename
    counter = 1
    audio_dir = COMPLETED_AUDIO_DIR
    audio_file_path = os.path.join(audio_dir, f"{filename}.mp4")
    while os.path.exists(audio_file_path):
        filename = f"{base_filename}_{counter}"
        audio_file_path = os.path.join(audio_dir, f"{filename}.mp4")
        counter += 1
    if not os.path.exists(audio_file_path):
        stream = video.streams.filter(only_audio=True).first()
        if stream is None:
            raise ValueError(f"No audio stream found for video: {video.title}")
        try:
            os.makedirs(audio_dir, exist_ok=True)
            audio_file_path = stream.download(output_path=audio_dir, filename=f"{filename}.mp4")
        except Exception as e:
            print(f"Error downloading video {video.title}: {e}")
            return None, None
    return audio_file_path, filename

def estimate_whisper_transcription_cost(audio_duration_seconds):
    audio_duration_minutes = audio_duration_seconds / 60
    estimated_cost = audio_duration_minutes * WHISPER_COST_PER_MINUTE
    print("\n=== Estimated Whisper Transcription Cost ===")
    print(f"Audio Duration: {audio_duration_minutes:.2f} minutes")
    print(f"Estimated Cost: ${estimated_cost:.4f}\n")
    return estimated_cost

async def get_audio_duration(audio_file_path):
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", audio_file_path],
        capture_output=True, text=True, check=True
    )
    return float(result.stdout.strip())

def remove_unwanted_segments_from_json(json_file_path, unwanted_text="Subtitles by the Amara.org community"):
    with open(json_file_path, 'r', encoding='utf-8') as json_file:
        data = json.load(json_file)
    original_segment_count = len(data)
    filtered_data = [segment for segment in data if unwanted_text not in segment['text']]
    filtered_segment_count = len(filtered_data)
    if original_segment_count != filtered_segment_count:
        with open(json_file_path, 'w', encoding='utf-8') as json_file:
            json.dump(filtered_data, json_file, indent=4, ensure_ascii=False)
        print(f"Removed {original_segment_count - filtered_segment_count} unwanted segments from {json_file_path}.")
    else:
        print(f"No unwanted segments found in {json_file_path}.")
        
_WHISPER_MODEL_CACHE: dict = {}


def get_whisper_model(model_size: str, device: str, compute_type: str):
    """Load a Whisper model once and reuse it for every later file in this process."""
    key = (model_size, device, compute_type)
    model = _WHISPER_MODEL_CACHE.get(key)
    if model is None:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        _WHISPER_MODEL_CACHE[key] = model
    else:
        print(f"Reusing loaded Whisper model {model_size}/{device}/{compute_type}")
    return model


def release_whisper_models() -> None:
    """Free every cached Whisper model (needed before another GPU engine loads)."""
    _WHISPER_MODEL_CACHE.clear()
    import gc
    gc.collect()


def resolve_batched_mode() -> tuple[bool, int]:
    """Opt-in fast mode. Env (set by the watcher) wins over saved settings."""
    env = os.environ.get("WHISPER_BATCHED", "").strip()
    enabled = env == "1" if env else bool(_SETTINGS.get("whisper_batched", False))
    try:
        size = int(os.environ.get("WHISPER_BATCH_SIZE", "") or _SETTINGS.get("whisper_batch_size", 8) or 8)
    except (TypeError, ValueError):
        size = 8
    return enabled, max(1, min(32, size))


def batched_transcribe_kwargs(kwargs: dict) -> dict:
    """Adapt robust kwargs for BatchedInferencePipeline.

    Benchmarks showed the initial_prompt makes batched mode drop 8-20% of words
    (each 30 s chunk gets the prompt), and hallucination_silence_threshold is not
    supported there, so both are removed. Without them batched matched the
    sequential result (0.991 vs 0.994 agreement with an independent reference).
    """
    import inspect
    allowed = set(inspect.signature(BatchedInferencePipeline.transcribe).parameters)
    out = {k: v for k, v in kwargs.items() if k in allowed}
    out.pop("initial_prompt", None)
    out.pop("hallucination_silence_threshold", None)
    return out


async def compute_transcript_with_whisper_from_audio_func(
    audio_file_path,
    audio_file_name,
    audio_file_size_mb,
    progress_callback=None,
):
    cuda_toolkit_path = get_cuda_toolkit_path()
    if cuda_toolkit_path:
        add_to_system_path(cuda_toolkit_path)
    combined_transcript_text = ""
    combined_transcript_text_list_of_metadata_dicts = []
    list_of_transcript_sentences = []
    if use_openai_api_for_transcription:
        print(f"Using OpenAI API for transcription of {audio_file_name}...")
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_api_key)
        audio_duration_seconds = await get_audio_duration(audio_file_path)
        estimate_whisper_transcription_cost(audio_duration_seconds)
        with open(audio_file_path, "rb") as audio_file:
            response = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json"
            )
        # Access the response content
        response_data = json.loads(response.model_dump_json())
        segments = response_data.get('segments', [])
        combined_transcript_text = response_data.get('text', "")
        combined_transcript_text_list_of_metadata_dicts = [
            {
                "start": segment.get('start', 0),
                "end": segment.get('end', 0),
                "text": segment.get('text', ""),
                "avg_logprob": segment.get('avg_logprob', 0)
            }
            for segment in segments
        ]
    else:
        print(f"Using local Whisper model for transcription of {audio_file_name}...")
        force_cpu = os.environ.get("WHISPER_FORCE_CPU", "").strip() == "1"
        use_gpu = gpu_available_for_whisper() and not disable_cuda_override and not force_cpu
        gpu_model, cpu_model = resolve_whisper_models()
        audio_duration_seconds = await get_audio_duration(audio_file_path)
        print(f"Computing transcript for {audio_file_name} which has a {audio_file_size_mb:.2f}MB file size...")

        profile = resolve_decode_profile()
        language = resolve_whisper_language()
        print(f"Whisper decode profile: {profile}; language: {language or 'auto'}")
        run_profile = "robust" if profile == "auto" else profile
        max_block, max_gap = resolve_coverage_limits()
        whisper_pass = 0
        while True:
            whisper_pass += 1
            attempts = build_whisper_attempts(use_gpu, gpu_model, cpu_model, run_profile)
            if use_gpu:
                print(f"CUDA is available. Starting with GPU transcription ({gpu_model}).")
            else:
                print(f"CUDA unavailable or disabled. Starting with CPU transcription ({cpu_model}).")

            last_exception = None
            for model_size, device, compute_type, vad_filter, label in attempts:
                if device == "cpu" and use_gpu:
                    print(
                        f"WARNING: GPU transcription failed for {audio_file_name}; falling back to the weaker CPU model "
                        f"'{model_size}'. Segments will be tagged with model={model_size}/cpu so this is visible in the output."
                    )
                print(
                    f"Whisper attempt {label} for {audio_file_name}: "
                    f"model={model_size} device={device} compute_type={compute_type} vad_filter={vad_filter}"
                )
                combined_transcript_text = ""
                combined_transcript_text_list_of_metadata_dicts = []
                list_of_transcript_sentences = []
                try:
                    model = get_whisper_model(model_size, device, compute_type)
                    run_kwargs = transcribe_kwargs_for_profile(run_profile, vad_filter, language)
                    batched, batch_size = resolve_batched_mode()
                    if batched and device == "cuda":
                        print(f"Fast batched mode on (batch_size={batch_size}).")
                        transcribe_fn = BatchedInferencePipeline(model).transcribe
                        run_kwargs = dict(batched_transcribe_kwargs(run_kwargs), batch_size=batch_size)
                    else:
                        transcribe_fn = model.transcribe
                    with tqdm(total=audio_duration_seconds, desc=f"Transcribing {audio_file_name}", unit="s") as pbar:
                        segments, info = await asyncio.to_thread(transcribe_fn, audio_file_path, **run_kwargs)
                        detected_language = getattr(info, "language", None)
                        detected_language_prob = round(float(getattr(info, "language_probability", 0) or 0), 3)
                        print(f"Language: {detected_language} (p={detected_language_prob}, forced={language is not None})")
                        for segment in segments:
                            pbar.update(max(0.0, segment.end - segment.start))
                            preview = " ".join(segment.text.split())[:120]
                            print(
                                f"Segment [{segment.start:.2f}s->{segment.end:.2f}s] "
                                f"{audio_file_name}: {preview}"
                            )
                            if progress_callback is not None:
                                try:
                                    progress_callback(segment.start, segment.end, segment.text)
                                except Exception as cb_exc:  # a progress-file hiccup must never abort a transcription
                                    print(f"Progress update skipped: {type(cb_exc).__name__}: {cb_exc}")
                            combined_transcript_text += segment.text + " "
                            sentences = sophisticated_sentence_splitter(segment.text)
                            list_of_transcript_sentences.extend(sentences)
                            metadata = {
                                "start": round(segment.start, 2),
                                "end": round(segment.end, 2),
                                "text": segment.text,
                                "avg_logprob": round(segment.avg_logprob, 2),
                                "no_speech_prob": round(segment.no_speech_prob, 3),
                                "compression_ratio": round(segment.compression_ratio, 2),
                                "language": detected_language,
                                "language_probability": detected_language_prob,
                                "model": f"{model_size}/{device}/{compute_type}",
                                "profile": run_profile,
                                "source": "whisper",
                            }
                            combined_transcript_text_list_of_metadata_dicts.append(metadata)
                    if combined_transcript_text_list_of_metadata_dicts:
                        print(f"Whisper attempt {label} succeeded with {len(combined_transcript_text_list_of_metadata_dicts)} segments.")
                        break
                    print(f"Whisper attempt {label} returned no segments for {audio_file_name}.")
                except Exception as exc:
                    last_exception = exc
                    _WHISPER_MODEL_CACHE.pop((model_size, device, compute_type), None)
                    print(
                        f"Whisper attempt {label} failed for {audio_file_name}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
            if not combined_transcript_text_list_of_metadata_dicts and last_exception is not None:
                raise RuntimeError(
                    f"all_whisper_attempts_failed:{type(last_exception).__name__}:{last_exception}"
                ) from last_exception
            if profile == "auto" and whisper_pass == 1 and combined_transcript_text_list_of_metadata_dicts:
                first_warn = coverage_check.coverage_warnings(
                    combined_transcript_text_list_of_metadata_dicts, audio_duration_seconds, max_block, max_gap)
                mean_ns = coverage_check.mean_no_speech(combined_transcript_text_list_of_metadata_dicts)
                if first_warn or mean_ns > AUTO_MUSIC_NO_SPEECH:
                    why = "; ".join(coverage_check.summary_lines(first_warn)[:3]) or f"mean no_speech_prob {mean_ns:.2f}"
                    print(f"Whisper music profile retry for {audio_file_name}: {why}")
                    run_profile = "music"
                    continue
            break
    if not use_openai_api_for_transcription:
        try:
            del model
        except NameError:
            pass
        engine = asr_engines.resolve_engine(_SETTINGS) if hasattr(asr_engines, "resolve_engine") else "whisper"
        crosscheck = asr_engines.resolve_crosscheck(_SETTINGS) if hasattr(asr_engines, "resolve_crosscheck") else False
        if engine != "whisper" or crosscheck:
            release_whisper_models()  # free Whisper's GPU memory before a second engine loads
        import gc
        gc.collect()
        try:
            processed = asr_engines.apply_engine_pipeline(
                audio_file_path, audio_file_name, combined_transcript_text_list_of_metadata_dicts,
                _SETTINGS, COMPLETED_JSON_DIR,
            )
        except Exception as exc:  # optional stage: never lose the Whisper result
            print(f"ASR engine stage skipped: {type(exc).__name__}: {exc}")
            processed = combined_transcript_text_list_of_metadata_dicts
        if processed is not combined_transcript_text_list_of_metadata_dicts:
            combined_transcript_text_list_of_metadata_dicts = processed
            combined_transcript_text = " ".join(str(s.get("text", "")).strip() for s in processed) + " "
            list_of_transcript_sentences = []
            for s in processed:
                list_of_transcript_sentences.extend(sophisticated_sentence_splitter(str(s.get("text", ""))))
    if not combined_transcript_text_list_of_metadata_dicts:
        print(f"No segments were returned for file {audio_file_name}.")
        return "", [], []
    txt_path = os.path.join(COMPLETED_TRANSCRIPTS_DIR, f"{audio_file_name}.txt")
    with open(txt_path, 'w', encoding='utf-8') as file:
        file.write(render_readable_transcript(combined_transcript_text_list_of_metadata_dicts))
    df = pd.DataFrame(combined_transcript_text_list_of_metadata_dicts)
    csv_path = os.path.join(COMPLETED_CSV_DIR, f"{audio_file_name}.csv")
    df.to_csv(csv_path, index=False, encoding='utf-8')
    json_file_path = os.path.join(COMPLETED_JSON_DIR, f"{audio_file_name}.json")
    df.to_json(json_file_path, orient='records', indent=4, force_ascii=False)
    remove_unwanted_segments_from_json(json_file_path)
    try:
        record_coverage(json_file_path, audio_file_name, combined_transcript_text_list_of_metadata_dicts,
                        audio_duration_seconds if not use_openai_api_for_transcription else None)
    except Exception as exc:  # a warning pass must never lose the transcript
        print(f"Coverage check skipped: {type(exc).__name__}: {exc}")
    transcript_search.after_transcript(audio_file_name, COMPLETED_JSON_DIR, COMPLETED_TRANSCRIPTS_DIR)
    return combined_transcript_text, combined_transcript_text_list_of_metadata_dicts, list_of_transcript_sentences

def record_coverage(json_file_path: str, stem: str, segments: list[dict], duration: float | None) -> list[dict]:
    """Add 'coverage_warnings' to every JSON segment and a section to <stem>.flags.txt."""
    max_block, max_gap = resolve_coverage_limits()
    warnings = coverage_check.coverage_warnings(segments, duration, max_block, max_gap)
    with open(json_file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for s in data:
        if isinstance(s, dict):
            s["coverage_warnings"] = warnings
    with open(json_file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    if warnings:
        flags = os.path.join(os.path.dirname(json_file_path), f"{stem}.flags.txt")
        with open(flags, "a", encoding="utf-8") as f:
            f.write("\nPossibly missed audio (coverage check):\n")
            for line in coverage_check.summary_lines(warnings):
                f.write(f"  {line}\n")
        print(f"Coverage check: {len(warnings)} warning(s) for {stem}: "
              + "; ".join(coverage_check.describe(w) for w in warnings[:5]))
    return warnings


async def process_video_or_playlist(
    url, max_simultaneous_downloads, max_workers_transcribe, *, single_video=None
):
    is_single = convert_single_video if single_video is None else single_video
    if is_single:
        yt = YouTube(url)
        videos = [yt]
    else:
        playlist = Playlist(url)
        videos = playlist.videos
    download_semaphore = asyncio.Semaphore(max_simultaneous_downloads)
    async def download_and_transcribe(video):
        try:
            async with download_semaphore:
                audio_path, audio_filename = await download_audio(video)
                if audio_path and audio_filename:
                    audio_file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
                    await compute_transcript_with_whisper_from_audio_func(audio_path, audio_filename, audio_file_size_mb)
        except Exception as e:
            traceback.print_exc()
            print(f"Error processing video {video.title}: {e}")
    tasks = [download_and_transcribe(video) for video in videos]
    await asyncio.gather(*tasks)

def normalize_logprobs(avg_logprob, min_logprob, max_logprob):
    range_logprob = max_logprob - min_logprob
    return (avg_logprob - min_logprob) / range_logprob if range_logprob != 0 else 0.5

def remove_pagination_breaks(text: str) -> str:
    text = re.sub(r'-(\n)(?=[a-z])', '', text)
    text = re.sub(r'(?<=\w)(?<![.?!-]|\d)\n(?![\nA-Z])', ' ', text)
    return text


def render_readable_transcript(segments):
    blocks = []
    for segment in segments:
        text = " ".join(str(segment.get("text", "")).split()).strip()
        if text:
            blocks.append(text)
    return "\n\n".join(blocks)

def merge_transcript_segments_into_combined_text(segments):
    if not segments:
        return "", [], []
    min_logprob = min(segment['avg_logprob'] for segment in segments)
    max_logprob = max(segment['avg_logprob'] for segment in segments)
    combined_text = ""
    sentence_buffer = ""
    list_of_metadata_dicts = []
    list_of_sentences = []
    char_count = 0
    time_start = None
    time_end = None
    total_logprob = 0.0
    segment_count = 0
    for segment in segments:
        if time_start is None:
            time_start = segment['start']
        time_end = segment['end']
        total_logprob += segment['avg_logprob']
        segment_count += 1
        sentence_buffer += segment['text'] + " "
        sentences = sophisticated_sentence_splitter(sentence_buffer)
        for sentence in sentences:
            combined_text += sentence.strip() + " "
            list_of_sentences.append(sentence.strip())
            char_count += len(sentence.strip()) + 1
            avg_logprob = total_logprob / segment_count
            model_confidence_score = normalize_logprobs(avg_logprob, min_logprob, max_logprob)
            metadata = {
                'start_char_count': char_count - len(sentence.strip()) - 1,
                'end_char_count': char_count - 2,
                'time_start': time_start,
                'time_end': time_end,
                'model_confidence_score': model_confidence_score
            }
            list_of_metadata_dicts.append(metadata)
        if sentences:
            sentence_buffer = sentences.pop() if len(sentences) % 2 != 0 else ""
    return combined_text, list_of_metadata_dicts, list_of_sentences

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Download YouTube audio and transcribe with Whisper (local or OpenAI API)."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--single",
        action="store_true",
        help="Process one video URL (overrides convert_single_video in the script).",
    )
    mode.add_argument(
        "--playlist",
        action="store_true",
        help="Process a playlist URL (overrides convert_single_video in the script).",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Video or playlist URL. If omitted, uses single_video_url or playlist_url from the script.",
    )
    parser.add_argument(
        "--max-downloads",
        type=int,
        default=None,
        metavar="N",
        help=f"Max parallel YouTube downloads (default: {max_simultaneous_youtube_downloads}).",
    )
    args = parser.parse_args()
    if args.single:
        is_single = True
    elif args.playlist:
        is_single = False
    else:
        is_single = convert_single_video
    url_to_process = args.url or (single_video_url if is_single else playlist_url)
    max_dl = (
        max_simultaneous_youtube_downloads
        if args.max_downloads is None
        else args.max_downloads
    )
    if is_single:
        print(f"Processing a single video: {url_to_process}")
    else:
        print(f"Processing a playlist: {url_to_process}")
    asyncio.run(
        process_video_or_playlist(
            url_to_process,
            max_dl,
            max_workers_transcribe,
            single_video=is_single,
        )
    )
