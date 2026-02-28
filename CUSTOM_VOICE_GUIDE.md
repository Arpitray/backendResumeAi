# 🎙️ Custom Voice Models & TTS Integration Guide

This guide explains how to integrate custom voice models and third-party Text-to-Speech (TTS) providers into the AI Resume platform.

## 🌟 Recommended Providers

If `edge-tts` (which uses Microsoft Edge's free voices) isn't enough, we recommend the following professional custom voice providers:

### 1. ElevenLabs (Best for Realism)
ElevenLabs offers the most realistic "Voice Cloning" technology.
- **Site**: [elevenlabs.io](https://elevenlabs.io)
- **Use Case**: High-end interview simulation with emotional depth.
- **Integration**:
  ```python
  # Example code for speech.py integration
  async def elevenlabs_tts(text: str, voice_id: str):
      url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
      headers = {"xi-api-key": os.getenv("ELEVENLABS_API_KEY")}
      # ... implementation details
  ```

### 2. Play.ht (Great for Diversity)
Play.ht provides a massive library of AI voices and their own custom "Play3.0" models.
- **Site**: [play.ht](https://play.ht)
- **Use Case**: Multiple accents and clear, loud speech.

### 3. OpenVoice (Self-Hosted / Open Source)
If you want to host your own voice models to save costs.
- **Repo**: [MyShell-AI/OpenVoice](https://github.com/myshell-ai/OpenVoice)
- **Use Case**: Cloning a specific user's voice for training.

---

## 🛠️ How to Add a Custom Voice Model

To swap the current `edge-tts` with your own model, follow these steps:

### Step 1: Add Environment Variable
Add your API key to the [.env](.env) file:
```env
CUSTOM_TTS_API_KEY=your_key_here
```

### Step 2: Modify `speech.py`
Open [speech.py](speech.py) and update the `tts` endpoint:

```python
# Create a new function for your custom provider
async def custom_voice_tts(text: str, voice: str):
    # Call ElevenLabs/Play.ht/OpenVoice API here
    # Return a byte stream (io.BytesIO)
    pass
```

### Step 3: Update Frontend
Ensure your frontend UI sends the correct `voice_id` string to the `/speech/tts` endpoint.

---

## 📡 Local AI Voice Model Alternative
If you prefer to run a model locally (like **Coqui TTS** or **Bark**), be warned that these require significant **VRAM/GPU** and may be slow on standard CPU-based deployment servers like Railway.

For Railway, sticking to API-based providers (ElevenLabs/Play.ht) is highly recommended for speed and reliability.
