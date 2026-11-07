# Before Editing with Voice Clone Only

A Streamlit video processor that uses **VoxCPM2 voice cloning only**. Microsoft Edge-TTS and its preset voice catalog have been removed.

## Architecture

The lightweight Streamlit app sends paragraph audio requests to one or more independent VoxCPM2 API workers. Each worker normally runs in its own authorized Colab T4 runtime:

```text
Streamlit UI
  ├── paragraph 1, 4, 7 ... → Worker 1
  ├── paragraph 2, 5, 8 ... → Worker 2
  └── paragraph 3, 6, 9 ... → Worker 3

Results are restored to the original paragraph order before FFmpeg merges the video.
```

The app supports one, two, or three workers. With one worker, requests are sequential. With three separate healthy workers, up to three VoxCPM2 requests run concurrently. FFmpeg speed adjustment remains bounded to three threads.

## Streamlit Secrets

In Streamlit Cloud, open **App settings → Secrets** and add one block per running Colab worker:

```toml
VOXCPM_ENDPOINT_1 = "https://worker-1.trycloudflare.com"
VOXCPM_TOKEN_1 = "worker-1-token"
VOXCPM_ENDPOINT_2 = "https://worker-2.trycloudflare.com"
VOXCPM_TOKEN_2 = "worker-2-token"
VOXCPM_ENDPOINT_3 = "https://worker-3.trycloudflare.com"
VOXCPM_TOKEN_3 = "worker-3-token"
```

Only configure workers that are actually running. Endpoint values must not include `/generate`; the app adds that path. Tokens are sensitive and must never be committed to GitHub.

The UI also exposes the three worker fields for temporary/manual configuration. Secrets are used as the defaults.

## Colab worker setup

Run `colab_voxcpm_backend.ipynb` separately in each authorized Colab runtime:

1. Select a T4 GPU and confirm it with `!nvidia-smi`.
2. Upload the repository ZIP and install `requirements-gpu.txt`.
3. Generate a unique `VOXCPM_TOKEN` for that runtime.
4. Start `voice_clone_server.py` on port 7860.
5. Run the warm-up request until it returns `{"status":"ready"}`.
6. Start a Cloudflare tunnel and copy the resulting `https://...trycloudflare.com` URL.
7. Put that URL and token into the matching Streamlit Secrets worker fields.

Each runtime needs its own URL and token. Keep the Colab runtimes active while processing. Temporary Cloudflare URLs stop working when their runtime stops.

## Voice references

The UI supports the bundled authorized presets:

- `RecappianM`
- `KgSatNg` (uses the selected cleaned `KgSatNgcleaned.wav` reference)
- `MinMinHtun`

It also supports an uploaded reference audio file and its exact transcript. Use only voices and recordings for which you have permission.

## Local validation

```bash
python3 -m py_compile app.py voice_clone.py voice_clone_server.py
python3 -m pip install -r requirements.txt
```

For local VoxCPM2 generation, install `requirements-gpu.txt` and use a CUDA-capable machine. For the recommended low-memory deployment, keep Streamlit lightweight and use the Colab workers for inference.

## Limitations

- This is a personal-use workflow, not an always-on production API.
- Free Colab GPU availability and runtime duration are not guaranteed.
- Three UI entries do not create GPUs; each endpoint must point to a separate live worker.
- If a worker stops, its assigned paragraphs fail after retry. Remove the failed worker or restart it before retrying.
