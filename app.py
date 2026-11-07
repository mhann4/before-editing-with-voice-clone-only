import streamlit as st
import os
import subprocess
import math
import concurrent.futures
import asyncio
import time
import shutil
import gc
import threading
import base64
from voice_clone import (
    generate_remote_voxcpm2, generate_remote_voxcpm2_parallel,
    generate_voxcpm2, voxcpm_available,
)
# ─────────────────────────────────────────────
# Timer Utility
# ─────────────────────────────────────────────

def format_time(seconds):
    """Format seconds into mm:ss or mm:ss.ms format."""
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    ms = int((seconds % 1) * 10)
    if mins > 0:
        return f"{mins}:{secs:02d}"
    return f"{secs}.{ms}s"


# ─────────────────────────────────────────────
# VoxCPM2 Voice Clone and Video Settings
# ─────────────────────────────────────────────

@st.cache_resource
def get_recap_styles():
    return [
        {"id": "Normal", "name": "ပုံမှန်အသံ", "speed": 0, "pitch": 0},
        {"id": "NyoGyi_25", "name": "ကျားကြီး ၁", "speed": 0, "pitch": 25},
        {"id": "NyoGyi_35", "name": "ကျားကြီး ၂", "speed": 0, "pitch": 35},
        {"id": "NyoGyi_45", "name": "ကျားကြီး ၃", "speed": 0, "pitch": 45},
        {"id": "Nilar_40", "name": "နီလာ ချွဲသံ", "speed": 0, "pitch": 40},
        {"id": "Combo_15", "name": "ပေါင်းစပ် ၁၅", "speed": 15, "pitch": 15},
        {"id": "Combo_30", "name": "ပေါင်းစပ် ၃၀", "speed": 30, "pitch": 30},
        {"id": "Combo_50", "name": "ပေါင်းစပ် ၅၀", "speed": 50, "pitch": 50},
        {"id": "Pitch_20", "name": "အသံသေး ၂၀", "speed": 0, "pitch": 20},
        {"id": "Pitch_50", "name": "အသံသေး ၅၀", "speed": 0, "pitch": 50}
    ]

@st.cache_resource
def get_emotions():
    return [
        {"id": "EXCITING", "name": "စိတ်လှုပ်ရှား 🤩", "s": 15, "p": 10},
        {"id": "CALM", "name": "တည်ငြိမ် 😌", "s": -10, "p": -5},
        {"id": "PROFESSIONAL", "name": "သတင်း 💼", "s": 0, "p": -2},
        {"id": "NARRATIVE", "name": "ဇာတ်ကြောင်း 📖", "s": -5, "p": 0},
        {"id": "HAPPY", "name": "ပျော်ရွှင် 😊", "s": 10, "p": 15},
        {"id": "SERIOUS", "name": "လေးနက် 😠", "s": -5, "p": -10},
        {"id": "WHISPER", "name": "တီးတိုး 🤫", "s": -15, "p": -20},
        {"id": "SAD", "name": "ဝမ်းနည်း 😢", "s": -15, "p": -15},
        {"id": "SARCASTIC", "name": "ရွဲ့ပြော 🙄", "s": -10, "p": 5},
        {"id": "ANGRY", "name": "ဒေါသထွက် 🤬", "s": 20, "p": -10},
        {"id": "FEAR", "name": "ကြောက်လန့် 😨", "s": 10, "p": 20}
    ]

RECAP_STYLES = get_recap_styles()
EMOTIONS = get_emotions()

st.set_page_config(page_title="Video & Text Processor", layout="wide")


def get_streamlit_secret(name, default=""):
    """Read an optional Streamlit secret without breaking local development."""
    try:
        return str(st.secrets.get(name, default)).strip()
    except Exception:
        return default


# ─────────────────────────────────────────────
# SRT Subtitle Generator
# ─────────────────────────────────────────────

class SmartSubMaker:
    """Generate SRT subtitles proportional to text length and total video duration."""

    def __init__(self, full_text, duration_seconds):
        self.full_text = full_text.strip().replace('\n', ' ')
        self.duration_seconds = duration_seconds
        self.max_chars_per_line = 60

    def safe_myanmar_split(self, text):
        """Split Myanmar text safely without breaking syllables.
        Checks Unicode range \u102B-\u103E and \u105E-\u105F for medials/vowels/signs."""
        chunks = []
        words = text.split(' ')
        current_chunk = ""
        for word in words:
            if not word:
                continue
            if current_chunk and len(current_chunk) + len(word) + 1 > self.max_chars_per_line:
                chunks.append(current_chunk.strip())
                current_chunk = word + " "
            else:
                current_chunk += word + " "
        while len(current_chunk) > self.max_chars_per_line + 20:
            split_idx = self.max_chars_per_line
            while split_idx < len(current_chunk):
                char = current_chunk[split_idx]
                if '\u102B' <= char <= '\u103E' or '\u105E' <= char <= '\u105F':
                    split_idx += 1
                else:
                    break
            chunks.append(current_chunk[:split_idx].strip())
            current_chunk = current_chunk[split_idx:]
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
        return chunks

    def generate_subs(self):
        """Generate full SRT text with proportional timestamps."""
        if not self.full_text or self.duration_seconds <= 0:
            return "1\n00:00:00,000 --> 00:00:05,000\n[အသံထွက်နေပါသည်...]\n\n"

        # Split by Myanmar period (။) first
        raw_sentences = [s.strip() + "။" for s in self.full_text.split("။") if s.strip()]
        if not raw_sentences:
            raw_sentences = [self.full_text]

        # Further split long sentences by space within char limit
        final_chunks = []
        for rs in raw_sentences:
            split_parts = self.safe_myanmar_split(rs)
            final_chunks.extend(split_parts)

        total_chars = sum(len(c) for c in final_chunks)
        if total_chars == 0:
            total_chars = 1

        srt_text = ""
        current_time = 0.0
        for i, chunk in enumerate(final_chunks, 1):
            char_ratio = len(chunk) / total_chars
            chunk_duration = self.duration_seconds * char_ratio
            start_time = current_time
            end_time = current_time + chunk_duration
            srt_text += f"{i}\n"
            srt_text += f"{self._format_time(start_time)} --> {self._format_time(end_time)}\n"
            srt_text += f"{chunk}\n\n"
            current_time = end_time
        return srt_text

    def _format_time(self, seconds_float):
        """Format seconds to SRT timestamp HH:MM:SS,mmm."""
        hours = int(seconds_float // 3600)
        minutes = int((seconds_float % 3600) // 60)
        secs = int(seconds_float % 60)
        millis = int(round((seconds_float - int(seconds_float)) * 1000))
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def count_paragraphs(text):
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    return paragraphs


def make_output_basename(uploaded_name):
    original_name = os.path.splitext(os.path.basename(uploaded_name))[0]
    return f"BE_{original_name[:20].replace(' ', '_')}"


# ─────────────────────────────────────────────
# TTS Generation
# ─────────────────────────────────────────────

async def generate_all_tts(paragraphs, audio_dir, reference_audio, reference_text, workers):
    """Generate all VoxCPM2 paragraph WAVs using the configured worker pool."""
    if workers:
        generate_remote_voxcpm2_parallel(
            paragraphs, audio_dir, reference_audio, reference_text, workers,
        )
        return
    if not voxcpm_available():
        raise RuntimeError("No VoxCPM2 worker is configured and no local CUDA GPU is available.")
    for i, paragraph in enumerate(paragraphs):
        generate_voxcpm2(
            paragraph, reference_audio, reference_text,
            os.path.join(audio_dir, f"audio_{i}.wav"),
        )


# ─────────────────────────────────────────────
# Probe Helpers
# ─────────────────────────────────────────────

def get_video_duration(video_path):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
           '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return float(result.stdout.strip())


def get_video_resolution(video_path):
    cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
           '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', video_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    res = result.stdout.strip().split('x')
    w, h = int(res[0]), int(res[1])
    return w, h


def get_audio_duration(audio_path):
    if not audio_path or not os.path.exists(audio_path) or os.path.getsize(audio_path) < 128:
        raise ValueError(f"Audio file is missing or empty: {audio_path}")
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
           '-of', 'default=noprint_wrappers=1:nokey=1', audio_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    duration_text = result.stdout.strip()
    if result.returncode != 0 or not duration_text:
        raise ValueError(f"Could not read audio duration for {audio_path}: {result.stderr.strip()}")
    return float(duration_text)


# ─────────────────────────────────────────────
# Step 1: Split video into segments (video only, no audio)
# ─────────────────────────────────────────────

def split_video(video_path, num_segments, output_dir):
    """Split video into segments using fast copy (instant, no re-encoding).
    Audio is stripped to avoid conflicts."""
    duration = get_video_duration(video_path)
    segment_duration = duration / num_segments
    segments = []
    for i in range(num_segments):
        start_time = i * segment_duration
        output_path = os.path.join(output_dir, f"segment_{i}.mp4")
        cmd = ['ffmpeg', '-y', '-ss', str(start_time), '-t', str(segment_duration),
               '-i', video_path, '-c:v', 'copy', '-an',
               '-avoid_negative_ts', 'make_zero', output_path]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        segments.append(output_path)
    return segments, segment_duration


# ─────────────────────────────────────────────
# Step 2: Speed-adjust each segment to match TTS audio
# ─────────────────────────────────────────────

def speed_adjust_segment(index, video_segment, audio_path, adjusted_dir):
    """Speed-adjust VIDEO ONLY to match TTS audio duration.
    TTS audio is kept at original speed (no atempo).
    Returns the path to the adjusted segment (video+audio combined).
    Cleans up the original segment after success."""
    audio_duration = get_audio_duration(audio_path)
    orig_duration = get_video_duration(video_segment)
    output_path = os.path.join(adjusted_dir, f"adjusted_{index}.mp4")

    # Target video duration is rounded up to the nearest 0.5s increment
    # relative to the audio duration (ensuring a buffer of 0 to 0.5s).
    # This prevents audio truncation while keeping the buffer small.
    target_video_duration = math.ceil(audio_duration * 2) / 2
    
    # Ensure the video is at least as long as the audio (handle float precision)
    if target_video_duration < audio_duration:
        target_video_duration = audio_duration
        
    speed_factor = target_video_duration / orig_duration

    # Use a simpler filter complex that only touches video PTS.
    # We map audio directly (1:a) to ensure the TTS audio duration is strictly preserved.
    # We do NOT use -shortest because we want the video to be slightly longer (0-0.5s) than the audio.
    cmd = [
        'ffmpeg', '-y',
        '-i', video_segment,
        '-i', audio_path,
        '-filter_complex', f"[0:v]setpts={speed_factor}*PTS[v]",
        '-map', '[v]',
        '-map', '1:a',
        '-c:v', 'libx264', '-preset', 'ultrafast',
        '-c:a', 'aac',
        output_path
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode == 0 and os.path.exists(output_path):
        if os.path.exists(video_segment):
            os.remove(video_segment)
        return output_path
    else:
        raise Exception(f"Speed adjust failed for segment {index}: {result.stderr}")


# ─────────────────────────────────────────────
# Step 3: Merge all speed-adjusted segments
# ─────────────────────────────────────────────

def merge_speed_adjusted_segments(adjusted_segments, output_path):
    """Merge all speed-adjusted segments into a single video."""
    concat_file = output_path + "_concat.txt"
    with open(concat_file, 'w') as f:
        for seg in adjusted_segments:
            f.write(f"file '{os.path.abspath(seg)}'\n")
    cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0',
           '-i', concat_file, '-c', 'copy', output_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if os.path.exists(concat_file):
        os.remove(concat_file)
    if result.returncode != 0:
        raise Exception(f"Merge speed-adjusted segments failed: {result.stderr}")
    for seg in adjusted_segments:
        if os.path.exists(seg):
            os.remove(seg)



# ─────────────────────────────────────────────
# Step 5: Apply cycle repeat to a single chunk
# ─────────────────────────────────────────────

def build_cycle_filter(video_path, audio_path, chunk_duration,
                       freeze1_dur, freeze2_dur,
                       freeze1_zoom, freeze2_zoom, zoom_dur):
    """Build FFmpeg filter complex for cycle repeat on a chunk."""
    fps = 24
    cycle_duration = freeze1_dur + freeze2_dur
    num_cycles = math.ceil(chunk_duration / cycle_duration)

    # Use original resolution
    width, height = get_video_resolution(video_path)
    res_str = f"{width}x{height}"

    def make_zoom_filter(zoom_dur, total_dur, zoom_type):
        total_frames = max(int(total_dur * fps), 1)
        zoom_frames = max(int(zoom_dur * fps), 1)
        # Cap zoom_frames at total_frames to avoid overflow
        z_frames = min(zoom_frames, total_frames)
        
        if zoom_type == "Zoom In":
            # Zoom from 1.0 to 1.15 over z_frames, then stay at 1.15
            return (f"zoompan=z='if(lte(on,{z_frames}),min(1+0.15*on/{z_frames},1.15),1.15)':"
                    f"d={total_frames}:s={res_str}:fps={fps}")
        elif zoom_type == "Zoom Out":
            # Zoom from 1.15 to 1.0 over z_frames, then stay at 1.0
            return (f"zoompan=z='if(lte(on,{z_frames}),max(1.15-0.15*on/{z_frames},1.0),1.0)':"
                    f"d={total_frames}:s={res_str}:fps={fps}")
        return None

    f1_z = make_zoom_filter(zoom_dur, freeze1_dur, freeze1_zoom) if freeze1_zoom != "None" else None
    f2_z = make_zoom_filter(zoom_dur, freeze2_dur, freeze2_zoom) if freeze2_zoom != "None" else None

    filter_parts = []
    concat_inputs = []

    for i in range(num_cycles):
        curr = i * cycle_duration

        # Play section


        # Freeze 1
        f1_start = curr
        if f1_start < chunk_duration:
            f1_end = min(f1_start + freeze1_dur, chunk_duration)
            f1_actual_dur = f1_end - f1_start
            if f1_actual_dur > 0:
                filter_parts.append(
                    f"[0:v]trim=start={f1_start}:end={f1_end},setpts=PTS-STARTPTS,"
                    f"loop=loop={int(f1_actual_dur * fps)}:size=1:start=0[vf1_{i}];")
                if f1_z:
                    filter_parts.append(f"[vf1_{i}]{f1_z}[vf1z_{i}];")
                    concat_inputs.append(f"[vf1z_{i}]")
                else:
                    concat_inputs.append(f"[vf1_{i}]")

        # Freeze 2
        f2_start = curr + freeze1_dur
        if f2_start < chunk_duration:
            f2_end = min(f2_start + freeze2_dur, chunk_duration)
            f2_actual_dur = f2_end - f2_start
            if f2_actual_dur > 0:
                filter_parts.append(
                    f"[0:v]trim=start={f2_start}:end={f2_end},setpts=PTS-STARTPTS,"
                    f"loop=loop={int(f2_actual_dur * fps)}:size=1:start=0[vf2_{i}];")
                if f2_z:
                    filter_parts.append(f"[vf2_{i}]{f2_z}[vf2z_{i}];")
                    concat_inputs.append(f"[vf2z_{i}]")
                else:
                    concat_inputs.append(f"[vf2_{i}]")

    filter_str = "".join(filter_parts)
    filter_str += "".join(concat_inputs) + f"concat=n={len(concat_inputs)}:v=1:a=0[vout]"
    
    return filter_str


def process_chunk_with_retry(index, chunk_path, chunk_duration, output_dir,
                            play_dur, freeze1_dur, freeze2_dur,
                            freeze1_zoom, freeze2_zoom, zoom_dur, retries=2):
    """Process a single chunk with FFmpeg filter complex. Retries on failure."""
    output_path = os.path.join(output_dir, f"processed_{index}.mp4")
    filter_str = build_cycle_filter(chunk_path, chunk_path, chunk_duration,
                                    play_dur, freeze1_dur, freeze2_dur,
                                    freeze1_zoom, freeze2_zoom, zoom_dur)
    
    cmd = [
        'ffmpeg', '-y', '-i', chunk_path,
        '-filter_complex', filter_str,
        '-map', '[vout]', '-map', '0:a',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
        '-c:a', 'copy', output_path
    ]

    for attempt in range(retries + 1):
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0 and os.path.exists(output_path):
            if os.path.exists(chunk_path):
                os.remove(chunk_path)
            return output_path
        time.sleep(1)
    
    raise Exception(f"Chunk {index} failed after {retries} retries: {result.stderr}")


def merge_videos(video_list, output_path):
    valid_videos = [v for v in video_list if v is not None and os.path.exists(v)]
    if not valid_videos:
        raise Exception("No valid segments to merge.")
    concat_file = "concat_list.txt"
    with open(concat_file, 'w') as f:
        for video in valid_videos:
            f.write(f"file '{os.path.abspath(video)}'\n")
    cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0',
           '-i', concat_file, '-c', 'copy', output_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    if os.path.exists(concat_file):
        os.remove(concat_file)
    if result.returncode != 0:
        raise Exception(f"Merge Error: {result.stderr}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    st.title("🎬 Video & Text Processor — VoxCPM2 Voice Clone")

    # ─────────────────────────────────────────────
    # Language Toggle
    # ─────────────────────────────────────────────
    if "language" not in st.session_state:
        st.session_state.language = "မြန်မာ"

    st.session_state.language = st.segmented_control(
        "Language / ဘာသာစကား",
        options=["မြန်မာ", "English"],
        default=st.session_state.language,
    )

    lang = st.session_state.language
    is_en = lang == "English"

    st.markdown("---")

    # ─────────────────────────────────────────────
    # Settings Section (Main UI)
    # ─────────────────────────────────────────────
    st.header("⚙️ Settings" if not is_en else "⚙️ Settings")

    # UI labels
    label_text = "📝 Enter Text" if is_en else "📝 စာသား ထည့်ပါ"
    label_video = "🎥 Upload Video" if is_en else "🎥 ဗီဒီယို တင်ပါ"
    label_start = "🚀 Start Processing" if is_en else "🚀 Processing စတင်ပါ"
    label_info = "📊 Paragraphs: {para} | Characters: {char}"
    label_error = "❌ Provide text and video." if is_en else "❌ စာသား နှင့် ဗီဒီယို ထည့်ပါ။"

    # --- VoxCPM2-only voice configuration ---
    st.subheader("🎙️ VoxCPM2 Voice Clone" if is_en else "🎙️ VoxCPM2 Voice Clone")
    st.caption(
        "This app uses VoxCPM2 only. Configure up to three independent Colab workers; paragraphs are routed round-robin and returned in order."
        if is_en else
        "ဒီ app က VoxCPM2 ကိုပဲသုံးပါတယ်။ Colab worker သီးခြား ၃ ခုအထိထည့်နိုင်ပြီး paragraph တွေကို worker အလိုက်ခွဲပို့ကာ အစဉ်အတိုင်း ပြန်စုပေးပါတယ်။"
    )
    preset_clone_name = st.selectbox(
        "Reference voice preset" if is_en else "Reference voice preset",
        ["Upload my own reference"] + list(PRESET_CLONE_VOICES.keys()),
        key="preset_clone_name",
    )
    clone_reference_audio = None
    clone_reference_text = ""
    if preset_clone_name == "Upload my own reference":
        clone_reference_audio = st.file_uploader(
            "Reference voice (WAV/MP3/FLAC/M4A)" if is_en else "Reference အသံ (WAV/MP3/FLAC/M4A)",
            type=["wav", "mp3", "flac", "m4a"], key="clone_reference_audio",
        )
        clone_reference_text = st.text_area(
            "Exact words spoken in the reference clip" if is_en else "Reference အသံထဲမှာ ပြောထားတဲ့စာသားအတိအကျ",
            height=90, key="clone_reference_text",
        )
    else:
        preset = PRESET_CLONE_VOICES[preset_clone_name]
        clone_reference_text = preset["transcript"]
        st.success(f"{preset_clone_name} reference ကိုအသုံးပြုမယ်။" if not is_en else f"Using the {preset_clone_name} reference.")

    secret_workers = []
    for worker_index in range(1, 4):
        default_endpoint = get_streamlit_secret(f"VOXCPM_ENDPOINT_{worker_index}")
        default_token = get_streamlit_secret(f"VOXCPM_TOKEN_{worker_index}")
        with st.expander(f"Colab T4 worker {worker_index}", expanded=(worker_index == 1)):
            endpoint = st.text_input(
                "Endpoint URL (no /generate)" if is_en else "Endpoint URL (/generate မထည့်ပါနဲ့)",
                value=default_endpoint, placeholder="https://xxxxx.trycloudflare.com",
                key=f"worker_endpoint_{worker_index}",
            ).strip()
            token = st.text_input(
                "Backend token" if is_en else "Backend token",
                value=default_token, type="password", key=f"worker_token_{worker_index}",
            ).strip()
            if endpoint:
                secret_workers.append({"endpoint": endpoint, "token": token})
    workers = secret_workers
    if workers:
        st.info(f"{len(workers)} VoxCPM2 worker(s) ready. Paragraphs will be distributed round-robin." if is_en else f"{len(workers)} ခု VoxCPM2 worker အဆင်သင့်ဖြစ်ပါပြီ။ Paragraph တွေကို အလှည့်ကျခွဲပို့ပါမယ်။")

    # VoxCPM2 reference selection is complete; continue to text/video inputs.

    # VoxCPM2 is always the selected voice engine.
    preset_clone_selected = preset_clone_name != "Upload my own reference"
    effective_reference_text = clone_reference_text
    effective_reference_upload = clone_reference_audio
    voice_id = "voxcpm2"

    # Row 3: Text Input & Video Upload
    input_col, video_col = st.columns(2)
    with input_col:
        text_input = st.text_area(label_text, height=200)
        if text_input:
            paragraphs = count_paragraphs(text_input)
            st.info(f"📊 Paragraphs: {len(paragraphs)} | Characters: {len(text_input)}")
    with video_col:
        video_file = st.file_uploader(label_video, type=["mp4", "mov", "avi"])

    # Initialize session state for tracking
    if 'processing_active' not in st.session_state:
        st.session_state.processing_active = False

    # Start button logic
    if st.button(label_start) and not st.session_state.processing_active:

        if not text_input or not video_file:
            st.error(label_error)
            return
        if effective_reference_upload is None and not preset_clone_selected:
            st.error("Reference audio နဲ့ transcript လိုပါတယ်။" if not is_en else "Upload a reference audio and its exact transcript.")
            return
        if not effective_reference_text.strip():
            st.error("Reference transcript မရှိပါ။" if not is_en else "Reference transcript is missing.")
            return
        if not workers and not voxcpm_available():
            st.error("Colab T4 worker URL အနည်းဆုံး ၁ ခုထည့်ပါ။" if not is_en else "Configure at least one Colab T4 worker URL.")
            return
        
        st.session_state.processing_active = True
        
        # Save inputs to session state
        st.session_state.inputs = {
            "text": text_input,
            "voice_id": voice_id,
            "reference_text": effective_reference_text,
            "workers": workers,

        }
        st.session_state.output_basename = make_output_basename(video_file.name)

        # Setup temp directories
        temp_dir = "temp_processing"
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

        st.session_state.dirs = {
            "temp": temp_dir,
            "audio": os.path.join(temp_dir, "audio"),
            "video": os.path.join(temp_dir, "video"),
            "adjusted": os.path.join(temp_dir, "adjusted"),
        }
        for d in st.session_state.dirs.values():
            os.makedirs(d, exist_ok=True)

        reference_audio_path = None
        reference_audio_path = os.path.join(st.session_state.dirs["audio"], "voice_reference.wav")
        if preset_clone_selected:
            source_path = PRESET_CLONE_VOICES[preset_clone_name]["path"]
            if not os.path.exists(source_path):
                raise FileNotFoundError(f"Preset voice sample not found: {source_path}")
            shutil.copyfile(source_path, reference_audio_path)
        else:
            with open(reference_audio_path, "wb") as f:
                f.write(effective_reference_upload.getbuffer())
        st.session_state.inputs["reference_audio"] = reference_audio_path

        video_path = os.path.join(st.session_state.dirs["video"], "input_video.mp4")
        st.session_state.video_path = video_path

        with open(video_path, "wb") as f:
            while chunk := video_file.read(8192):
                f.write(chunk)

        # Display status and progress
        status = st.status("Processing video...", expanded=True)
        status.write("Starting video processing...")
        progress_bar = status.progress(0, text="Initializing...")
        step_status_placeholder = status.empty()
        progress_detail = status.empty()
        timer_placeholder = status.empty()

        total_start = time.time()

        try:
            inputs = st.session_state.inputs
            dirs = st.session_state.dirs
            video_path = st.session_state.video_path
            paragraphs = count_paragraphs(inputs["text"])
            num_paragraphs = len(paragraphs)

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # STEP 1+2: Pre-process Video + TTS + Split (PARALLEL)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            step_status_placeholder.markdown("**Step 1/3:** Pre-processing video & generating TTS...")
            progress_detail.markdown("Initializing...")
            step_start = time.time()

            progress_detail.markdown(f"🔊 Generating {num_paragraphs} TTS files in parallel...")
            asyncio.run(generate_all_tts(
                paragraphs,
                dirs["audio"],
                inputs["reference_audio"],
                inputs["reference_text"],
                inputs.get("workers", []),
            ))
            progress_detail.markdown(f"✅ TTS complete ({num_paragraphs}/{num_paragraphs})")

            progress_detail.markdown(f"✂️ Splitting video into {num_paragraphs} segments...")
            video_segments, _ = split_video(video_path, num_paragraphs, dirs["video"])
            progress_detail.markdown(f"✅ Split complete ({num_paragraphs} segments)")

            step12_elapsed = time.time() - step_start
            progress_bar.progress(0.33)
            progress_detail.markdown(f"✅ TTS + Split complete ({step12_elapsed:.1f}s)")

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # STEP 3: Speed-adjust each segment
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            step_status_placeholder.markdown(f"**Step 2/3:** Speed-adjusting {num_paragraphs} segments...")
            step_start = time.time()
            adjusted_segments = [None] * num_paragraphs
            audio_extension = "wav"
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = {
                    executor.submit(
                        speed_adjust_segment,
                        i, video_segments[i],
                        os.path.join(dirs["audio"], f"audio_{i}.{audio_extension}"),
                        dirs["adjusted"]
                    ): i for i in range(num_paragraphs)
                }
                for future in concurrent.futures.as_completed(futures):
                    idx = futures[future]
                    try:
                        adjusted_segments[idx] = future.result()
                    except Exception as e:
                        st.error(f"❌ Speed adjust failed for segment {idx+1}: {e}")
                    done = sum(1 for x in adjusted_segments if x is not None)
                    progress_bar.progress(0.33 + 0.33 * done / num_paragraphs)
                    progress_detail.markdown(f"⚡ Segment {done}/{num_paragraphs} speed adjusted")

            failed_segments = [i + 1 for i, path in enumerate(adjusted_segments) if path is None]
            if failed_segments:
                raise RuntimeError(f"Speed adjustment failed for segment(s): {failed_segments}")

            gc.collect()
            step3_elapsed = time.time() - step_start
            progress_detail.markdown(f"✅ All {num_paragraphs} segments speed-adjusted ({step3_elapsed:.1f}s)")

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # FINAL STEP: Merge all speed-adjusted segments
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            step_status_placeholder.markdown("**Step 3/3:** Merging final video...")
            progress_detail.markdown("🔗 Merging segments...")
            step_start = time.time()
            output_video = os.path.join(dirs["temp"], "final_output.mp4")
            merge_speed_adjusted_segments(adjusted_segments, output_video)
            progress_bar.progress(1.0)
            final_merge_elapsed = time.time() - step_start
            progress_detail.markdown(f"✅ Final video merged ({final_merge_elapsed:.1f}s)")

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # GENERATE SRT SUBTITLE FILE
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            progress_detail.markdown("📝 Generating SRT subtitles...")
            final_duration = get_video_duration(output_video)
            full_text = inputs["text"]
            sub_maker = SmartSubMaker(full_text, final_duration)
            srt_text = sub_maker.generate_subs()
            srt_path = os.path.join(dirs["temp"], "final_output.srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(srt_text)
            progress_detail.markdown("✅ SRT subtitles generated")

            gc.collect()
            
            st.session_state.processing_active = False
            st.session_state.output_video = output_video
            st.session_state.srt_path = srt_path
            st.session_state.dirs_path = dirs
            with open(output_video, "rb") as f:
                st.session_state.video_bytes = f.read()
            with open(srt_path, "rb") as f:
                st.session_state.srt_bytes = f.read()
            status.update(label="✅ Complete!", state="complete")
            st.write("✅ Processing complete. Download your video below.")

        except Exception as e:
            st.error(f"❌ Processing failed: {e}")
            st.session_state.processing_active = False
            return

        total_elapsed = time.time() - total_start
        timer_placeholder.markdown(f"⏱️ **Total: {format_time(total_elapsed)}**")
        step_status_placeholder.markdown("**✅ All steps complete!**")
        st.success(f"🎉 Completed in **{format_time(total_elapsed)}**")

    # Show download buttons even after rerun (outside the processing block)
    if st.session_state.get("output_video") and os.path.exists(st.session_state.get("output_video", "")):
        output_basename = st.session_state.get("output_basename", "BE_output")

        # Read bytes from session_state (stored once after processing)
        video_data = st.session_state.get("video_bytes")
        srt_data = st.session_state.get("srt_bytes")

        col_v, col_s = st.columns(2)
        with col_v:
            if video_data:
                st.download_button(
                    "📥 Download Video" if is_en else "📥 Download Video",
                    data=video_data,
                    file_name=f"{output_basename}.mp4",
                    mime="video/mp4"
                )
        with col_s:
            if srt_data:
                st.download_button(
                    "📝 Download SRT" if is_en else "📝 Download SRT",
                    data=srt_data,
                    file_name=f"{output_basename}.srt",
                    mime="text/plain"
                )

    st.markdown("---")
    label_reset = "🔄 Process Another Video" if is_en else "🔄 နောက်ထပ် ဗီဒီယို ထပ်လုပ်မယ်"
    if st.button(label_reset, use_container_width=True):
        if st.session_state.get("dirs_path"):
            shutil.rmtree(st.session_state.dirs_path["temp"], ignore_errors=True)
        if st.session_state.get("output_video") and os.path.exists(st.session_state.output_video):
            os.remove(st.session_state.output_video)
        st.session_state.processing_active = False
        st.session_state.output_video = None
        st.session_state.srt_path = None
        st.session_state.dirs_path = None
        st.session_state.video_bytes = None
        st.session_state.srt_bytes = None
        st.rerun()
if __name__ == "__main__":
    main()
