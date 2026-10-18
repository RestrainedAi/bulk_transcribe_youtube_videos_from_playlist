"""Build handoff payloads and dispatch to NotebookLM vs LLM providers (Phase II)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def build_handoff_bundle(
    completed_video: Path | None,
    txt_path: Path | None,
    title: str,
    *,
    include_video_path: bool = True,
) -> str:
    lines = [f"# {title}", ""]
    if include_video_path and completed_video and completed_video.is_file():
        lines.append(f"Source video: {completed_video}")
        lines.append("")
    if txt_path and txt_path.is_file():
        lines.append("--- Transcript ---")
        lines.append(_read_text(txt_path))
    return "\n".join(lines).strip()


def copy_to_clipboard_windows(text: str) -> bool:
    try:
        import tkinter as tk  # noqa: PLC0415

        r = tk.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()
        r.destroy()
        return True
    except Exception:
        return False


def handoff_notebooklm(bundle: str, repo_root: Path, watch_dir: Path | None = None) -> None:
    """Stage transcript for NotebookLM: clipboard + open notebooklm.google."""
    copy_to_clipboard_windows(bundle)
    base = watch_dir if watch_dir is not None else repo_root / "watch_incoming"
    stage = base / "staged_for_notebooklm"
    stage.mkdir(parents=True, exist_ok=True)
    snippet = stage / "last_handoff.txt"
    try:
        snippet.write_text(bundle, encoding="utf-8")
    except OSError:
        pass
    webbrowser.open("https://notebooklm.google.com/")


def handoff_llm_browser(provider: str, bundle: str) -> None:
    copy_to_clipboard_windows(bundle)
    urls = {
        "chatgpt": "https://chatgpt.com/",
        "gemini": "https://gemini.google.com/app",
        "claude": "https://claude.ai/",
        "grok": "https://grok.com/",
        "lmstudio": "http://localhost:1234/",
    }
    url = urls.get(provider, "https://chatgpt.com/")
    webbrowser.open(url)


def handoff_open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], shell=False)  # noqa: S603
    else:
        subprocess.Popen(["xdg-open", str(path)], shell=False)  # noqa: S603


def _summarize_openai(
    api_key: str,
    model: str,
    user_text: str,
    base_url: str | None,
    system_prompt: str,
) -> str:
    from openai import OpenAI  # noqa: PLC0415

    kwargs: dict[str, str] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url.rstrip("/")
    client = OpenAI(**kwargs)
    r = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text[:120000]},
        ],
    )
    choice = r.choices[0].message
    return (choice.content or "").strip()


def _anthropic_summarize(api_key: str, user_text: str, system_prompt: str) -> str:
    model = "claude-haiku-4-5"
    try:
        import anthropic  # noqa: PLC0415

        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_text[:120000]}],
        )
        texts = [block.text for block in msg.content if getattr(block, "type", "") == "text"]
        return "\n".join(texts).strip()
    except ImportError:
        pass
    body = json.dumps(
        {
            "model": model,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_text[:120000]}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    parts = data.get("content") or []
    texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
    return "\n".join(texts).strip()


def _gemini_summarize(api_key: str, user_text: str, system_prompt: str) -> str:
    q = urllib.parse.urlencode({"key": api_key})
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?{q}"
    )
    body = json.dumps(
        {
            "contents": [
                {
                    "parts": [
                        {
                            "text": f"{system_prompt}\n\n---\n\n{user_text[:120000]}"
                        }
                    ]
                }
            ]
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    cands = data.get("candidates") or []
    if not cands:
        return ""
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "\n".join(p.get("text", "") for p in parts).strip()


def _resolve_key(settings: dict[str, Any], setting_field: str, *env_names: str) -> str:
    v = str(settings.get(setting_field) or "").strip()
    if v:
        return v
    for name in env_names:
        ev = os.environ.get(name, "").strip()
        if ev:
            return ev
    return ""


def handoff_try_llm_api(
    provider: str,
    bundle: str,
    settings: dict[str, Any],
) -> tuple[bool, str]:
    """Tier-1: call an LLM API when `handoff_try_api_first` and keys allow. Returns (ok, text_or_error)."""
    if not settings.get("handoff_try_api_first"):
        return False, ""
    text = bundle.strip()
    if not text:
        return False, "empty bundle"
    system_prompt = str(
        settings.get("handoff_llm_system_prompt")
        or "Summarize the transcript for an operator: key points, action items, and notable quotes."
    ).strip()
    try:
        if provider == "chatgpt":
            key = _resolve_key(settings, "openai_api_key", "OPENAI_API_KEY")
            if not key:
                return False, "OpenAI API key not set (settings or OPENAI_API_KEY)"
            out = _summarize_openai(key, "gpt-4o-mini", text, None, system_prompt)
            return True, out
        if provider == "claude":
            key = _resolve_key(settings, "anthropic_api_key", "ANTHROPIC_API_KEY")
            if not key:
                return False, "Anthropic API key not set (settings or ANTHROPIC_API_KEY)"
            out = _anthropic_summarize(key, text, system_prompt)
            return True, out
        if provider == "gemini":
            key = _resolve_key(settings, "google_api_key", "GOOGLE_API_KEY", "GEMINI_API_KEY")
            if not key:
                return False, "Google API key not set (settings or GOOGLE_API_KEY / GEMINI_API_KEY)"
            out = _gemini_summarize(key, text, system_prompt)
            return True, out
        if provider == "grok":
            key = _resolve_key(settings, "xai_api_key", "XAI_API_KEY")
            if not key:
                return False, "xAI API key not set (settings or XAI_API_KEY)"
            out = _summarize_openai(key, "grok-2-latest", text, "https://api.x.ai/v1", system_prompt)
            return True, out
        if provider == "lmstudio":
            base = str(settings.get("lm_studio_base_url") or "http://localhost:1234/v1").strip()
            model = str(settings.get("lm_studio_model") or "local-model").strip() or "local-model"
            out = _summarize_openai("lm-studio", model, text, base, system_prompt)
            return True, out
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = str(e)
        return False, f"HTTP {e.code}: {err_body[:500]}"
    except Exception as e:
        return False, str(e)
    return False, f"unknown provider {provider!r}"


def write_api_handoff_artifact(watch_dir: Path, title: str, summary: str) -> Path:
    stage = watch_dir / "staged_for_notebooklm"
    stage.mkdir(parents=True, exist_ok=True)
    path = stage / "last_api_summary.txt"
    path.write_text(f"# API summary - {title}\n\n{summary}", encoding="utf-8")
    return path
