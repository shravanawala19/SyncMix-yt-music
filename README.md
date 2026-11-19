<p align="center">
  <img src="assets/icon.png" alt="SyncMix Crossfader Logo" width="112" />
</p>

# SyncMix Live Crossfader — Single-Deck Spotify-Style YouTube Music App

A standalone Windows desktop app built with **PySide6 + QtWebEngine** that presents **one single YouTube Music screen** and automatically crossfades seamlessly between songs in your queue—just like Spotify Crossfade / AutoMix.

## Download for Windows (Recommended)

No Python setup required — download and run the Windows installer:

> **[Download SyncMix_Crossfader_Setup.exe (Windows 10/11 64-bit)](https://github.com/shravanawala19/SyncMix-yt-music/releases/latest/download/SyncMix_Crossfader_Setup.exe)**

- Browse all versions on the **[Releases page](https://github.com/shravanawala19/SyncMix-yt-music/releases)**.
- Installs to `%LOCALAPPDATA%\Programs\SyncMix Crossfader` (no admin rights required), adds optional Desktop & Start Menu shortcuts, and registers cleanly under **Windows Settings -> Apps**.

## How It Works
- **1 Visible Deck**: Search, play playlists, shuffle, or use **Play next / Add to queue** natively in YouTube Music.
- **Hidden Outgoing-Tail Buffer**: While a track plays, a hidden background buffer preloads the end of the current track.
- **Automatic Queue Crossfade**: As the current song enters its outro window, the hidden buffer takes over the outgoing tail and fades it out while the main deck advances cleanly to the next queued song and fades it in.
- **Built-in Ad-Blocker**: Blocks YouTube/Google ad endpoints (`AdBlockInterceptor`) and strips player ad metadata at `DocumentCreation` (`ADBLOCK_SCRIPT_JS`).
- **Real-Time Silence Mix & Outro Drop**: Uses a live pre-fader Web Audio `AnalyserNode` (`Silence Mix` toggle, enabled by default) to detect silent parts / quiet outros (`< -42 dBFS` for `1.6s`) and outro energy drops, automatically starting the mix from that point.
- **Controls & Desktop Features**: `Smart AutoMix` toggle, `Silence Mix` toggle, **`Mix Now`** button (`Ctrl+M` hotkey for instant smooth transitions), live 10-band mini spectrum visualizer, live detected `BPM • Key` readout, persistent `settings.json`, Smart Track Memory (`track_cache.json`), clean mid-mix Pause/Resume, and Minimize to Windows System Tray.
- **Mix Modes**:
  - **Apple AutoMix**: Dedicated Apple-Music-style engine with real-time `50 Hz` autocorrelation BPM detection, `4096-FFT` harmonic mid-band (`220–2200 Hz`) musical key detection (Camelot harmonic overlap up to `24s`), Beat-Phase Snap kick alignment, Smart Incoming Intro-Skip (with instant cached cues), 3-Band Vocal-Safe EQ + Clean Bass Swap, Apple Sound-Check-style Auto Loudness Matching (`0.45×–2.20×` + soft limiter), and automatic **Echo Fade** fallback for large BPM gaps (`> 14%`).
  - **Standard Crossfade (Spotify)**: Smart outro window with user-selected `Crossfade` duration (`1s`–`15s`, default `6s`) and `Equal Power`, `Smooth S-Curve`, or `Linear` curves.
  - **GitHub AutoMix (`VertexSelection/automix-engine`)**: Independent from Standard Crossfade controls — adapted from `stem_transition.py` & `cue_analysis.py` for live YouTube Music streams using a 32-beat (`TRANSITION_PULSES = 32`, `CUE_PULSE = 16`) phrase-aligned transition window, two-stage outgoing energy fill (`-6 dB`, `0.28–0.65` gain) + vocal-safe handoff (`t = 0.45`), and pitch-preserving tempo sync (`0.82x–1.18x`) with a smooth `6.48s` (`TEMPO_RELEASE_SECONDS`) tempo release ramp across `16` control points.
  - **Pro DJ Mix (`kckDeepak/AI-DJ-Mixing-System`)**: Independent from Standard Crossfade controls — adapted from `generate_mixing_plan.py` & `mixing_engine.py` using Camelot Wheel key compatibility, genre-aware dynamic overlap (`4s`–`16s`), 32 micro-step CDJ tempo ramping (`±5%`), real-time Web Audio progressive EQ sweeps (outgoing low-pass `12kHz → 2.2–4kHz` + incoming bass-swap high-pass `260Hz → 20Hz`), and a decaying echo tail at handoff.

## Run from Source
Double-click `Launch_Crossfader.bat` or run:
```powershell
pip install -r requirements.txt
python main.py
```
