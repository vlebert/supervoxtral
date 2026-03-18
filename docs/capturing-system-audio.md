# Capturing System Audio

To capture both microphone and system audio (e.g., remote participants in a video call), you have two options:

## Option 1 — Screen recorder (simpler)

Use a screen recorder that captures mic + system audio into a single file, then process it with `svx process`. No loopback driver needed.

**CleanShot X** (macOS) is a good option: it records screen/audio and exports to a standard video file. Then:
```
svx process recording.mp4 --prompt "Summarize the meeting"
```

This is the recommended approach for occasional meeting transcription.

## Option 2 — Loopback device (real-time, dual capture)

For real-time dual capture during recording (mic + system audio mixed live), you need a loopback audio device. SuperVoxtral will open both inputs simultaneously and mix them into a single mono WAV.

### macOS — BlackHole (free, open source)

1. Install BlackHole:
   ```
   brew install --cask blackhole-2ch
   ```
   (Restart may be required after install.)

2. Open **Audio MIDI Setup** (Spotlight → "Audio MIDI Setup").

3. Click `+` at the bottom left → **Create Multi-Output Device**.

4. Check your output device (e.g., "Headphones" or "External Speakers") — it must be **first** (clock source).

5. Check **BlackHole 2ch**.

6. Optionally rename it (e.g., "Headphones + BlackHole").

7. Set this Multi-Output Device as your system sound output (System Settings → Sound → Output).

8. In your `config.toml`:
   ```toml
   loopback_device = "BlackHole 2ch"
   ```

> **Note:** System volume control does not work with Multi-Output Devices (macOS limitation). Adjust volume in individual applications or in Audio MIDI Setup.

### Linux — PulseAudio monitor (built-in)

PulseAudio exposes a `.monitor` source for every output device. No additional software needed.

1. Find your monitor source name:
   ```
   pactl list sources short | grep monitor
   ```
   Example output: `alsa_output.pci-0000_00_1f.3.analog-stereo.monitor`

2. In your `config.toml`:
   ```toml
   loopback_device = "Monitor of Built-in Audio"
   ```

### Windows — WASAPI loopback (built-in)

Windows supports loopback capture natively via WASAPI since Vista.

1. The loopback device typically appears as "Stereo Mix" or similar. You may need to enable it:
   - Right-click the speaker icon → Sound Settings → More sound settings
   - Recording tab → right-click → Show Disabled Devices → Enable "Stereo Mix"

2. In your `config.toml`:
   ```toml
   loopback_device = "Stereo Mix"
   ```

### Gain adjustment

When `loopback_device` is configured, you can adjust the relative volume of each source via `mic_gain` and `loopback_gain` in `config.toml` (default: 1.0 each).
