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
- **Real-Time Silence Mix**: Uses a live Web Audio `AnalyserNode` RMS meter (`Silence Mix` toggle, enabled by default) to detect silent parts / quiet outros (`< -42 dBFS` sustained for `1.6s` in the second half or final `45s` of a track) and automatically start mixing into the next queued song from that point.
- **Controls**: `Smart AutoMix` toggle, `Silence Mix` toggle, and `Mix Mode` selector (`Standard Crossfade (Spotify)`, `GitHub AutoMix (VertexSelection)`, or `Pro DJ Mix (AI-DJ-Mixing-System)`). When `Standard Crossfade (Spotify)` is active, `Crossfade` duration (`1s`–`15s`, default `6s`) and fade curve (`Equal Power / Smooth S-Curve / Linear`) are shown; when `GitHub AutoMix` or `Pro DJ Mix` is active, Standard Crossfade controls are hidden and each mode runs independently.
- **Mix Modes**:
  - **Standard Crossfade (Spotify)**: Smart outro window with user-selected `Crossfade` duration and `Equal Power`, `Smooth S-Curve`, or `Linear` curves.
  - **GitHub AutoMix (`VertexSelection/automix-engine`)**: Independent from the Standard Crossfade duration and curve controls — adapted from `stem_transition.py` & `cue_analysis.py` for live YouTube Music streams using a 32-beat (`TRANSITION_PULSES = 32`, `CUE_PULSE = 16`) phrase-aligned transition window, two-stage outgoing energy fill (`-6 dB`, `0.28–0.65` gain) + vocal-safe handoff (`t = 0.45`), and pitch-preserving tempo sync (`0.82x–1.18x`) with a smooth `6.48s` (`TEMPO_RELEASE_SECONDS`) tempo release ramp across `16` control points.
  - **Pro DJ Mix (`kckDeepak/AI-DJ-Mixing-System`)**: Independent from Standard Crossfade controls — adapted from `generate_mixing_plan.py` & `mixing_engine.py` using Camelot Wheel key compatibility, genre-aware dynamic overlap (`4s`–`16s`), 32 micro-step CDJ tempo ramping (`±5%`), real-time Web Audio progressive EQ sweeps (outgoing low-pass `12kHz → 2.2–4kHz` + incoming bass-swap high-pass `260Hz → 20Hz`), and a decaying echo tail at handoff.

## Run from Source
Double-click `Launch_Crossfader.bat` or run:
```powershell
pip install -r requirements.txt
python main.py
```
