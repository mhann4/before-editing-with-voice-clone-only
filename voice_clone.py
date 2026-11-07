"""VoxCPM2 voice-cloning backend and multi-worker remote client."""

from __future__ import annotations

import os
import subprocess
import tempfile
import concurrent.futures
from functools import lru_cache
from urllib.parse import urljoin


@lru_cache(maxsize=1)
def load_voxcpm2():
    """Load VoxCPM2 once per Streamlit process."""
    from voxcpm import VoxCPM

    return VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)


def voxcpm_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def prepare_reference_audio(input_path: str) -> str:
    """Convert reference audio to a clean 16 kHz mono WAV when ffmpeg exists."""
    fd, output_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    command = [
        "ffmpeg", "-y", "-i", input_path,
        "-ac", "1", "-ar", "16000", "-vn", output_path,
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        try:
            os.remove(output_path)
        except OSError:
            pass
        return input_path
    return output_path


def generate_voxcpm2(text: str, reference_audio: str, reference_text: str, output_path: str) -> str:
    """Generate one cloned-voice WAV file using VoxCPM2."""
    import soundfile as sf

    model = load_voxcpm2()
    clean_reference = prepare_reference_audio(reference_audio)
    try:
        wav = model.generate(
            text=text,
            prompt_wav_path=clean_reference,
            prompt_text=reference_text.strip() or None,
            reference_wav_path=clean_reference,
            cfg_value=2.0,
            inference_timesteps=10,
        )
        sf.write(output_path, wav, model.tts_model.sample_rate)
        return output_path
    finally:
        if clean_reference != reference_audio:
            try:
                os.remove(clean_reference)
            except OSError:
                pass


def generate_remote_voxcpm2(
    endpoint_url: str,
    text: str,
    reference_audio: str,
    reference_text: str,
    output_path: str,
    token: str = "",
    timeout: int = 900,
) -> str:
    """Call the companion Colab/FastAPI backend and save its WAV response."""
    import requests

    base = endpoint_url.strip().rstrip("/") + "/"
    url = urljoin(base, "generate")
    headers = {"X-Voice-Token": token} if token.strip() else {}
    with open(reference_audio, "rb") as ref_file:
        response = requests.post(
            url,
            data={"text": text, "reference_text": reference_text},
            files={"reference_audio": (os.path.basename(reference_audio), ref_file, "audio/wav")},
            headers=headers,
            timeout=timeout,
        )
    response.raise_for_status()
    if not response.content or len(response.content) < 64:
        raise RuntimeError("Remote VoxCPM2 returned an empty audio response.")

    # Re-mux/normalize the response before FFmpeg's video pipeline reads it.
    # This also rejects an HTML/proxy response that happened to return HTTP 200.
    fd, raw_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        with open(raw_path, "wb") as raw_file:
            raw_file.write(response.content)
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", raw_path, "-ac", "1", "-ar", "16000", output_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) < 128:
            raise RuntimeError(f"Remote audio could not be decoded: {result.stderr[-500:]}")
    finally:
        try:
            os.remove(raw_path)
        except OSError:
            pass
    return output_path


def generate_remote_voxcpm2_parallel(
    paragraphs: list[str],
    audio_dir: str,
    reference_audio: str,
    reference_text: str,
    workers: list[dict[str, str]],
    timeout: int = 900,
    retries: int = 1,
) -> list[str]:
    """Generate paragraph WAVs across independent remote VoxCPM2 workers.

    Each worker must point to its own running Colab/API runtime. Results are
    returned in the original paragraph order even though requests finish at
    different times.
    """
    active_workers = [
        {"endpoint": w.get("endpoint", "").strip(), "token": w.get("token", "").strip()}
        for w in workers
        if w.get("endpoint", "").strip()
    ]
    if not active_workers:
        raise ValueError("At least one VoxCPM2 worker endpoint is required.")

    def run_one(index: int, paragraph: str) -> str:
        worker = active_workers[index % len(active_workers)]
        output_path = os.path.join(audio_dir, f"audio_{index}.wav")
        last_error = None
        for attempt in range(retries + 1):
            try:
                return generate_remote_voxcpm2(
                    worker["endpoint"], paragraph, reference_audio, reference_text,
                    output_path, worker["token"], timeout,
                )
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    continue
        raise RuntimeError(f"Worker {index % len(active_workers) + 1} failed paragraph {index + 1}: {last_error}")

    outputs = [None] * len(paragraphs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(active_workers)) as executor:
        futures = {
            executor.submit(run_one, index, paragraph): index
            for index, paragraph in enumerate(paragraphs)
        }
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            outputs[index] = future.result()
    return outputs
