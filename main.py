"""
SyncMix Live Crossfader — Single-Deck Spotify-Style YouTube Music App
=====================================================================
Displays a single YouTube Music screen where you search, queue, and play
music natively. A hidden outgoing-tail deck buffers the end of the current
song so that as it finishes, it smoothly transitions into the next queued track
on the main screen — supporting Standard Spotify-style Crossfade, GitHub AutoMix
(VertexSelection/automix-engine), and Pro DJ Mix (kckDeepak/AI-DJ-Mixing-System).
"""

import ctypes
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)


def _resource_path(rel_path: str) -> str:
    """Resolve asset paths for both source execution and PyInstaller bundles."""
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, rel_path)


def _get_data_dir() -> str:
    """Store user data/cache in LocalAppData when running frozen, or project folder from source."""
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(local_app_data, "SyncMix Crossfader")
        os.makedirs(path, exist_ok=True)
        return path
    return os.path.dirname(os.path.abspath(__file__))


def _get_profile_dir() -> str:
    """Store QtWebEngine browser profile in LocalAppData when running frozen."""
    return os.path.join(_get_data_dir(), "profile_data")


YTM_URL = "https://music.youtube.com"
DATA_DIR = _get_data_dir()
PROFILE_DIR = _get_profile_dir()
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
TRACK_CACHE_PATH = os.path.join(DATA_DIR, "track_cache.json")

MIX_MODE_STANDARD = "Crossfade"
MIX_MODE_AUTOMIX = "32-Beat AutoMix"
MIX_MODE_PRO_DJ = "Pro DJ"
MIX_MODE_APPLE = "Apple AutoMix"
LEGACY_MIX_MODE_MAP = {
    "Standard Crossfade (Spotify)": MIX_MODE_STANDARD,
    "GitHub AutoMix (VertexSelection)": MIX_MODE_AUTOMIX,
    "Pro DJ Mix (AI-DJ-Mixing-System)": MIX_MODE_PRO_DJ,
    "Apple AutoMix": MIX_MODE_APPLE,
}
WARMUP_LEAD_SEC = 6.0

# Real-time Web Audio silence detection (detect silent outro / quiet part and mix early)
SILENCE_RMS_THRESHOLD = 0.008      # ~ -42 dBFS: audio below this counts as silent
SILENCE_RESUME_THRESHOLD = 0.014   # ~ -37 dBFS: hysteresis to reset silence timer
AUDIBLE_CONFIRM_RMS = 0.015        # ~ -36.5 dBFS: require real audio before allowing silence trigger
SILENCE_HOLD_SEC = 1.6             # Continuous silence duration required before triggering mix
SILENCE_MIN_ELAPSED_SEC = 20.0     # Never trigger during track intros / first 20s
SILENCE_MIN_PROGRESS = 0.55        # Allow silence mix once past 55% of track OR within last 45s
SILENCE_OUTRO_WINDOW_SEC = 45.0

# Apple-AutoMix-style real-time energy-drop, intro-skip, loudness & BPM/key thresholds
OUTRO_DROP_RATIO = 0.32            # Energy < 32% of song's own average energy = quiet outro phrase
OUTRO_DROP_MIN_AVG_RMS = 0.04      # Only detect energy drop on tracks with clear body energy
OUTRO_DROP_HOLD_SEC = 2.8          # Sustained low-energy outro duration before triggering mix
OUTRO_DROP_MIN_PROGRESS = 0.65     # Allow energy-drop mix in final 35% OR last 35s
OUTRO_DROP_WINDOW_SEC = 35.0
INTRO_SKIP_STEP_SEC = 2.0          # Fast-cue step over silent intros on incoming deck
INTRO_SKIP_MAX_SEC = 8.0           # Cap intro-skip so we never skip past a real opening verse
TARGET_LOUDNESS_RMS = 0.16         # Apple Sound-Check style target RMS loudness
LOUDNESS_TRIM_MIN = 0.45           # Allow full -7 dB attenuation on very loud masters
LOUDNESS_TRIM_MAX = 2.20           # Allow full +7 dB lift on quiet acoustic tracks (protected by soft limiter)
CACHE_SCHEMA_VERSION = 2           # v2: pre-fader analyser, 50Hz autocorr BPM, mid-band 4096-FFT key

# kckDeepak/AI-DJ-Mixing-System constants (generate_mixing_plan.py & mixing_engine.py)
PRO_DJ_RAMP_STEPS = 32
PRO_DJ_MAX_STRETCH = 0.05
PRO_DJ_ECHO_DELAY_SEC = 0.375
PRO_DJ_ECHO_DECAY = 0.50

CAMELOT_KEYS = (
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
    "Am", "A#m", "Bm", "Cm", "C#m", "Dm", "D#m", "Em", "Fm", "F#m", "Gm", "G#m",
)

CAMELOT_WHEEL = {
    "C": {"compatible": ["G", "F", "Am"]},
    "C#": {"compatible": ["G#", "F#", "A#m"]},
    "D": {"compatible": ["A", "G", "Bm"]},
    "D#": {"compatible": ["A#", "G#", "Cm"]},
    "E": {"compatible": ["B", "A", "C#m"]},
    "F": {"compatible": ["C", "A#", "Dm"]},
    "F#": {"compatible": ["C#", "B", "D#m"]},
    "G": {"compatible": ["D", "C", "Em"]},
    "G#": {"compatible": ["D#", "C#", "Fm"]},
    "A": {"compatible": ["E", "D", "F#m"]},
    "A#": {"compatible": ["F", "D#", "Gm"]},
    "B": {"compatible": ["F#", "E", "G#m"]},
    "Am": {"compatible": ["Em", "Dm", "C"]},
    "A#m": {"compatible": ["Fm", "D#m", "C#"]},
    "Bm": {"compatible": ["F#m", "Em", "D"]},
    "Cm": {"compatible": ["Gm", "Fm", "D#"]},
    "C#m": {"compatible": ["G#m", "F#m", "E"]},
    "Dm": {"compatible": ["Am", "Gm", "F"]},
    "D#m": {"compatible": ["A#m", "G#m", "F#"]},
    "Em": {"compatible": ["Bm", "Am", "G"]},
    "Fm": {"compatible": ["Cm", "A#m", "G#"]},
    "F#m": {"compatible": ["C#m", "Bm", "A"]},
    "Gm": {"compatible": ["Dm", "Cm", "A#"]},
    "G#m": {"compatible": ["D#m", "C#m", "B"]},
}

PRO_DJ_GENRE_RULES = {
    "edm": {"overlap_delta": 4.0, "eq_filter_strength": 1.5},
    "house": {"overlap_delta": 4.0, "eq_filter_strength": 1.2},
    "hip": {"overlap_delta": -2.0, "eq_filter_strength": 0.8},
    "rap": {"overlap_delta": -2.0, "eq_filter_strength": 0.8},
    "rock": {"overlap_delta": -1.0, "eq_filter_strength": 0.9},
    "pop": {"overlap_delta": 0.0, "eq_filter_strength": 1.0},
}

# VertexSelection/automix-engine transition constants (stem_transition.py)
TRANSITION_PULSES = 32
CUE_PULSE = 16
PULSES_PER_BAR = 4
TEMPO_RELEASE_SECONDS = 6.48
MAX_RELEASE_PULSES = 16
RELEASE_CONTROL_POINTS = 16
OUTGOING_FILL_TARGET_DB_BELOW_INCOMING = 6.0
OUTGOING_FILL_MIN_GAIN = 0.28
OUTGOING_FILL_MAX_GAIN = 0.65
CUE_VOCAL_MIN_TRANSITION_FRACTION = 0.45
CUE_VOCAL_MIN_PLAYBACK_RATE = 0.82
CUE_VOCAL_MAX_PLAYBACK_RATE = 1.18
VOCAL_REVEAL_LINE_GAIN_MIN = 0.32
VOCAL_REVEAL_LINE_GAIN_MAX = 0.52

AD_BLOCK_PATTERNS = (
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "adservice.google.",
    "/pagead/",
    "/api/stats/ads",
    "/ptracking",
    "youtube.com/get_midroll_info",
    "youtube.com/pcs/activeview",
    "youtubei/v1/log_event",
    "play.google.com/log",
)

# Injected at DocumentCreation (MainWorld) to strip ad metadata before YT Music's player reads it
ADBLOCK_SCRIPT_JS = r"""
(() => {
    if (window.__syncmixAdBlockInstalled) return;
    window.__syncmixAdBlockInstalled = true;

    const stripAds = (obj) => {
        if (!obj || typeof obj !== 'object') return obj;
        try {
            if ('adPlacements' in obj) delete obj.adPlacements;
            if ('playerAds' in obj) delete obj.playerAds;
            if ('adSlots' in obj) delete obj.adSlots;
            if ('adBreakHeartbeatParams' in obj) delete obj.adBreakHeartbeatParams;
            if (obj.playerResponse && typeof obj.playerResponse === 'object') {
                stripAds(obj.playerResponse);
            }
        } catch (e) {}
        return obj;
    };

    const origParse = JSON.parse;
    JSON.parse = function (...args) {
        return stripAds(origParse.apply(this, args));
    };

    const origJson = Response.prototype.json;
    Response.prototype.json = function () {
        return origJson.apply(this, arguments).then((data) => stripAds(data));
    };

    let _ytInitial = undefined;
    try {
        Object.defineProperty(window, 'ytInitialPlayerResponse', {
            configurable: true,
            get() { return _ytInitial; },
            set(val) { _ytInitial = stripAds(val); }
        });
    } catch (e) {}

    const installObserver = () => {
        if (!document.head || !document.body) {
            setTimeout(installObserver, 50);
            return;
        }
        const style = document.createElement('style');
        style.textContent = `
            .ytp-ad-module,
            .ytp-ad-overlay-container,
            .ytp-ad-player-overlay,
            ytmusic-mealbar-promo-renderer,
            ytd-ad-slot-renderer {
                display: none !important;
            }
        `;
        document.head.appendChild(style);

        setInterval(() => {
            const adEl = document.querySelector('.ad-showing, .ad-interrupting');
            const v = document.querySelector('video');
            if (adEl) {
                if (v) {
                    v.muted = true;
                    v.__syncmixAdMuted = true;
                    if (v.paused) v.play().catch(()=>{});
                    if (isFinite(v.duration) && v.duration > 0) {
                        v.currentTime = Math.max(0, v.duration - 0.05);
                    }
                }
                const skipBtn = document.querySelector(
                    '.ytp-ad-skip-button, .ytp-skip-ad-button, .ytp-ad-skip-button-modern'
                );
                if (skipBtn) skipBtn.click();
            } else if (v && v.__syncmixAdMuted) {
                v.muted = false;
                v.__syncmixAdMuted = false;
            }
        }, 150);
    };
    installObserver();
})();
"""

INSPECT_JS = r"""
(() => {
    const video = document.querySelector('video');
    const mp = document.getElementById('movie_player');
    let title = '';
    let artist = '';
    if (navigator.mediaSession && navigator.mediaSession.metadata) {
        title = navigator.mediaSession.metadata.title || '';
        artist = navigator.mediaSession.metadata.artist || '';
    }
    if (!title) {
        const tEl = document.querySelector('ytmusic-player-bar .title');
        const aEl = document.querySelector('ytmusic-player-bar .byline');
        if (tEl) title = tEl.textContent.trim();
        if (aEl) artist = aEl.textContent.trim();
    }

    let videoId = '';
    let nextVideoId = '';
    let nextTitle = '';
    const params = new URLSearchParams(window.location.search);

    try {
        if (mp && typeof mp.getVideoData === 'function') {
            const vd = mp.getVideoData();
            if (vd && vd.video_id) videoId = vd.video_id;
        }
        if (mp && typeof mp.getPlaylist === 'function' && typeof mp.getPlaylistIndex === 'function') {
            const pl = mp.getPlaylist();
            const idx = mp.getPlaylistIndex();
            if (Array.isArray(pl) && idx >= 0 && idx + 1 < pl.length) {
                nextVideoId = pl[idx + 1] || '';
            }
        }
    } catch (e) {}

    const extractFromObj = (obj) => {
        if (!obj || typeof obj !== 'object') return '';
        const p = obj.playlistPanelVideoRenderer || (obj.playlistPanelVideoWrapperRenderer && obj.playlistPanelVideoWrapperRenderer.primaryRenderer && obj.playlistPanelVideoWrapperRenderer.primaryRenderer.playlistPanelVideoRenderer) || obj;
        const nav = p.navigationEndpoint || obj.navigationEndpoint || {};
        const we = nav.watchEndpoint || {};
        return p.videoId || we.videoId || '';
    };

    if (!nextVideoId) {
        try {
            const app = document.querySelector('ytmusic-app');
            const state = app && app.store && typeof app.store.getState === 'function' ? app.store.getState() : null;
            const qItems = state && state.queue && Array.isArray(state.queue.items) ? state.queue.items : [];
            if (qItems.length > 0) {
                const selIdx = qItems.findIndex((it) => {
                    const p = it && (it.playlistPanelVideoRenderer || (it.playlistPanelVideoWrapperRenderer && it.playlistPanelVideoWrapperRenderer.primaryRenderer && it.playlistPanelVideoWrapperRenderer.primaryRenderer.playlistPanelVideoRenderer) || it);
                    return !!(p && p.selected);
                });
                if (selIdx >= 0 && selIdx + 1 < qItems.length) {
                    nextVideoId = extractFromObj(qItems[selIdx + 1]);
                }
            }
        } catch (e) {}
    }

    if (!nextVideoId) {
        try {
            const items = Array.from(document.querySelectorAll('ytmusic-player-queue-item'));
            let selIdx = items.findIndex(el => el.hasAttribute('selected') || el.hasAttribute('playing'));
            if (selIdx < 0 && items.length > 1) selIdx = 0;
            if (selIdx >= 0 && selIdx + 1 < items.length) {
                const nxt = items[selIdx + 1];
                const candidates = [nxt.data, nxt.__data && nxt.__data.data, nxt.polymerController && nxt.polymerController.data];
                for (const c of candidates) {
                    const vid = extractFromObj(c);
                    if (vid) {
                        nextVideoId = vid;
                        break;
                    }
                }
                const ntEl = nxt.querySelector('.song-title');
                if (ntEl) nextTitle = ntEl.textContent.trim();
            }
        } catch (e) {}
    }

    if (!videoId) {
        videoId = params.get('v') || '';
    }

    let audioRms = -1;
    let audioReady = false;
    let loudnessRms = 0;
    let midEnergy = 0;
    let liveBpm = 0;
    let bpmConfidence = 0;
    let liveKey = '';
    let keyConfidence = 0;
    let beatPhase = -1;
    let vizBands = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0];

    if (video && !video.paused && video.readyState >= 2) {
        try {
            let meter = window.__syncmixMeter;
            if (!meter || meter.video !== video) {
                if (meter && meter.timer) {
                    clearInterval(meter.timer);
                }
                const Ctx = window.AudioContext || window.webkitAudioContext;
                const fx = window.__syncmixProFx;
                const ctx = (fx && fx.ctx) || window.__syncmixAudioCtx || new Ctx();
                window.__syncmixAudioCtx = ctx;
                const src = video.__syncmixMediaSrc || ctx.createMediaElementSource(video);
                video.__syncmixMediaSrc = src;
                const analyser = ctx.createAnalyser();
                analyser.fftSize = 4096;
                analyser.smoothingTimeConstant = 0.2;
                src.connect(analyser);

                let mixGain = window.__syncmixMixGain;
                let limiter = window.__syncmixLimiter;
                if (!mixGain || mixGain.context !== ctx) {
                    mixGain = ctx.createGain();
                    const initVol = (typeof video.__syncmixTargetVol === 'number') ? video.__syncmixTargetVol : (video.muted ? 0.0 : video.volume);
                    mixGain.gain.value = Math.max(0.0, Math.min(1.0, initVol));
                    limiter = ctx.createDynamicsCompressor();
                    limiter.threshold.value = -2.0;
                    limiter.knee.value = 6.0;
                    limiter.ratio.value = 12.0;
                    limiter.attack.value = 0.003;
                    limiter.release.value = 0.12;
                    mixGain.connect(limiter);
                    limiter.connect(ctx.destination);
                    window.__syncmixMixGain = mixGain;
                    window.__syncmixLimiter = limiter;
                }
                // Keep native video.volume = 1.0 so pre-fader AnalyserNode ALWAYS reads true unmuted audio
                if (!video.__syncmixAdMuted) {
                    video.muted = false;
                    video.volume = 1.0;
                }
                if (!fx || fx.video !== video) {
                    if (!video.__syncmixDirectConnected) {
                        src.connect(mixGain);
                        video.__syncmixDirectConnected = true;
                    }
                }
                const MAJ_PROF = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88];
                const MIN_PROF = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17];
                const KEYS_MAJ = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
                const KEYS_MIN = ['Cm', 'C#m', 'Dm', 'D#m', 'Em', 'Fm', 'F#m', 'Gm', 'G#m', 'Am', 'A#m', 'Bm'];
                meter = {
                    video,
                    ctx,
                    src,
                    analyser,
                    trackId: '',
                    timeBuf: new Float32Array(analyser.fftSize),
                    freqBuf: new Uint8Array(analyser.frequencyBinCount),
                    prevLowSpec: null,
                    fluxRing: new Float32Array(500),
                    fluxIdx: 0,
                    fluxCount: 0,
                    audioRms: -1,
                    loudnessRms: 0,
                    midEnergy: 0,
                    lowHist: [],
                    lastOnsetTs: 0,
                    lastBeatTrackTime: -1,
                    liveBpm: 0,
                    bpmConfidence: 0,
                    chroma: new Float64Array(12),
                    chromaFrames: 0,
                    liveKey: '',
                    keyConfidence: 0,
                    vizBands: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                    tickCount: 0,
                    resetTrack(id) {
                        this.trackId = id || '';
                        this.loudnessRms = 0;
                        this.prevLowSpec = null;
                        this.fluxRing.fill(0);
                        this.fluxIdx = 0;
                        this.fluxCount = 0;
                        this.lowHist = [];
                        this.lastOnsetTs = 0;
                        this.lastBeatTrackTime = -1;
                        this.liveBpm = 0;
                        this.bpmConfidence = 0;
                        this.chroma.fill(0);
                        this.chromaFrames = 0;
                        this.liveKey = '';
                        this.keyConfidence = 0;
                    }
                };
                meter.timer = setInterval(() => {
                    try {
                        if (!meter.video || meter.video.paused || meter.ctx.state !== 'running') return;
                        meter.analyser.getFloatTimeDomainData(meter.timeBuf);
                        let sumSq = 0;
                        for (let i = 0; i < meter.timeBuf.length; i += 2) {
                            const s = meter.timeBuf[i];
                            sumSq += s * s;
                        }
                        const rms = Math.sqrt(sumSq / (meter.timeBuf.length / 2));
                        meter.audioRms = rms;
                        if (rms >= 0.012) {
                            meter.loudnessRms = meter.loudnessRms > 0
                                ? (meter.loudnessRms * 0.96 + rms * 0.04)
                                : rms;
                        }

                        meter.analyser.getByteFrequencyData(meter.freqBuf);
                        const sr = meter.ctx.sampleRate || 48000;
                        const binHz = sr / meter.analyser.fftSize;
                        const lowStart = Math.max(1, Math.floor(40 / binHz));
                        const lowEnd = Math.min(meter.freqBuf.length - 1, Math.ceil(250 / binHz));

                        // Positive low-band spectral flux (50 Hz) for autocorrelation BPM + kick phase
                        let flux = 0;
                        let lowSum = 0;
                        const lowLen = Math.max(1, lowEnd - lowStart + 1);
                        if (!meter.prevLowSpec || meter.prevLowSpec.length !== lowLen) {
                            meter.prevLowSpec = new Uint8Array(lowLen);
                        }
                        for (let i = 0; i < lowLen; i++) {
                            const val = meter.freqBuf[lowStart + i];
                            lowSum += val;
                            const diff = val - meter.prevLowSpec[i];
                            if (diff > 0) flux += diff;
                            meter.prevLowSpec[i] = val;
                        }
                        const lowAvg = lowSum / lowLen;
                        const normFlux = flux / (lowLen * 25.0);
                        meter.fluxRing[meter.fluxIdx] = normFlux;
                        meter.fluxIdx = (meter.fluxIdx + 1) % 500;
                        if (meter.fluxCount < 500) meter.fluxCount++;

                        // Kick onset timestamp for beat-phase snap (20ms resolution)
                        meter.lowHist.push(lowAvg);
                        if (meter.lowHist.length > 45) meter.lowHist.shift();
                        let histMean = 0;
                        for (let i = 0; i < meter.lowHist.length; i++) histMean += meter.lowHist[i];
                        histMean /= meter.lowHist.length;
                        const nowMs = performance.now();
                        if (
                            meter.lowHist.length >= 15 &&
                            lowAvg > histMean * 1.30 &&
                            lowAvg > 42 &&
                            (nowMs - meter.lastOnsetTs) >= 260
                        ) {
                            meter.lastOnsetTs = nowMs;
                            meter.lastBeatTrackTime = meter.video.currentTime || 0;
                        }

                        meter.tickCount = (meter.tickCount + 1) % 50;

                        // Vocal mid-band energy + 10-band visualizer at 25 Hz (every 2nd tick)
                        if (meter.tickCount % 2 === 0) {
                            const midStart = Math.max(1, Math.floor(300 / binHz));
                            const midEnd = Math.min(meter.freqBuf.length - 1, Math.ceil(3000 / binHz));
                            let midSum = 0;
                            for (let b = midStart; b <= midEnd; b++) midSum += meter.freqBuf[b];
                            meter.midEnergy = (midSum / Math.max(1, midEnd - midStart + 1)) / 255.0;

                            const bandEdges = [45, 90, 160, 280, 500, 900, 1600, 2800, 5200, 9000, 14000];
                            for (let bi = 0; bi < 10; bi++) {
                                const bs = Math.max(1, Math.floor(bandEdges[bi] / binHz));
                                const be = Math.min(meter.freqBuf.length - 1, Math.max(bs, Math.ceil(bandEdges[bi + 1] / binHz)));
                                let bSum = 0;
                                for (let k = bs; k <= be; k++) bSum += meter.freqBuf[k];
                                meter.vizBands[bi] = Math.min(1.0, (bSum / Math.max(1, be - bs + 1)) / 220.0);
                            }
                        }

                        // Autocorrelation BPM comb-filter (once per second at 50 Hz, lags 19..40 -> 75..158 BPM)
                        if (meter.tickCount === 0 && meter.fluxCount >= 150) {
                            const N = meter.fluxCount;
                            const env = new Float32Array(N);
                            const start = (meter.fluxIdx - N + 500) % 500;
                            let meanF = 0;
                            for (let i = 0; i < N; i++) {
                                const v = meter.fluxRing[(start + i) % 500];
                                env[i] = v;
                                meanF += v;
                            }
                            meanF /= N;
                            for (let i = 0; i < N; i++) env[i] -= meanF;

                            const minLag = 19; // ~157.9 BPM at 50 Hz
                            const maxLag = 42; // ~71.4 BPM at 50 Hz
                            const scores = new Float32Array(maxLag + 2);
                            let bestLag = -1;
                            let bestScore = -1e9;
                            let sumScore = 0;
                            let cntScore = 0;
                            for (let lag = minLag; lag <= maxLag; lag++) {
                                let acc = 0;
                                const lim = N - lag;
                                for (let i = 0; i < lim; i++) {
                                    acc += env[i] * env[i + lag];
                                }
                                // Reinforce 2x beat period harmonic to prefer true quarter-note pulse
                                if (lag * 2 < N) {
                                    let acc2 = 0;
                                    const lim2 = N - lag * 2;
                                    for (let i = 0; i < lim2; i++) acc2 += env[i] * env[i + lag * 2];
                                    acc = acc / lim + 0.5 * (acc2 / lim2);
                                } else {
                                    acc = acc / lim;
                                }
                                scores[lag] = acc;
                                sumScore += Math.max(0, acc);
                                cntScore++;
                                if (acc > bestScore) {
                                    bestScore = acc;
                                    bestLag = lag;
                                }
                            }
                            const avgScore = sumScore / Math.max(1, cntScore);
                            if (bestLag > minLag && bestLag < maxLag && bestScore > 0.002) {
                                // Parabolic interpolation around bestLag for sub-0.5-BPM resolution
                                const sL = scores[bestLag - 1];
                                const sC = scores[bestLag];
                                const sR = scores[bestLag + 1];
                                const denom = (sL - 2 * sC + sR);
                                const shift = Math.abs(denom) > 1e-6 ? 0.5 * (sL - sR) / denom : 0;
                                const refinedLag = bestLag + Math.max(-0.5, Math.min(0.5, shift));
                                let bpm = 3000.0 / refinedLag; // 50 Hz * 60s = 3000
                                while (bpm < 75.0) bpm *= 2.0;
                                while (bpm > 160.0) bpm /= 2.0;
                                const ratio = avgScore > 1e-6 ? (bestScore / avgScore) : 1.0;
                                const conf = Math.max(0.0, Math.min(1.0, (ratio - 1.2) / 2.2));
                                if (conf >= 0.30) {
                                    meter.liveBpm = meter.liveBpm > 0
                                        ? (meter.liveBpm * 0.65 + bpm * 0.35)
                                        : bpm;
                                    meter.bpmConfidence = Math.max(meter.bpmConfidence * 0.85, conf);
                                }
                            }
                        }

                        // Real-time 12-bin harmonic mid-band (220Hz-2200Hz) chromagram key detector (5 Hz)
                        if (meter.tickCount % 10 === 0 && rms >= 0.015) {
                            const fStart = Math.max(2, Math.floor(220 / binHz));
                            const fEnd = Math.min(meter.freqBuf.length - 1, Math.ceil(2200 / binHz));
                            for (let b = fStart; b <= fEnd; b++) {
                                const mag = meter.freqBuf[b];
                                if (mag < 22) continue;
                                const freq = b * binHz;
                                const midi = Math.round(69 + 12 * Math.log2(freq / 440.0));
                                const pc = ((midi % 12) + 12) % 12;
                                meter.chroma[pc] += (mag / 255.0) * (mag / 255.0);
                            }
                            meter.chromaFrames++;
                            if (meter.chromaFrames >= 12) {
                                let cSum = 0;
                                for (let p = 0; p < 12; p++) cSum += meter.chroma[p];
                                if (cSum > 0.001) {
                                    const norm = [];
                                    for (let p = 0; p < 12; p++) norm.push(meter.chroma[p] / cSum);
                                    const corr = (prof, shift) => {
                                        let mX = 0, mY = 0;
                                        for (let i = 0; i < 12; i++) {
                                            mX += norm[(i + shift) % 12];
                                            mY += prof[i];
                                        }
                                        mX /= 12; mY /= 12;
                                        let num = 0, dX = 0, dY = 0;
                                        for (let i = 0; i < 12; i++) {
                                            const x = norm[(i + shift) % 12] - mX;
                                            const y = prof[i] - mY;
                                            num += x * y; dX += x * x; dY += y * y;
                                        }
                                        return (dX > 0 && dY > 0) ? (num / Math.sqrt(dX * dY)) : -1;
                                    };
                                    let bestK = '';
                                    let bestR = -2;
                                    let secondR = -2;
                                    for (let s = 0; s < 12; s++) {
                                        const rMaj = corr(MAJ_PROF, s);
                                        if (rMaj > bestR) { secondR = bestR; bestR = rMaj; bestK = KEYS_MAJ[s]; }
                                        else if (rMaj > secondR) { secondR = rMaj; }
                                        const rMin = corr(MIN_PROF, s);
                                        if (rMin > bestR) { secondR = bestR; bestR = rMin; bestK = KEYS_MIN[s]; }
                                        else if (rMin > secondR) { secondR = rMin; }
                                    }
                                    if (bestR > 0.2) {
                                        meter.liveKey = bestK;
                                        meter.keyConfidence = Math.max(0.0, Math.min(1.0, (bestR - Math.max(0, secondR)) * 2.5 + bestR * 0.4));
                                    }
                                }
                            }
                        }
                    } catch (e) {}
                }, 20);
                window.__syncmixMeter = meter;
            }
            if (videoId && meter.trackId !== videoId) {
                meter.resetTrack(videoId);
            }
            if (meter.ctx.state === 'suspended') {
                meter.ctx.resume().catch(() => {});
            }
            if (meter.ctx.state === 'running') {
                if (meter.audioRms < 0) {
                    meter.analyser.getFloatTimeDomainData(meter.timeBuf);
                    let sumSq = 0;
                    for (let i = 0; i < meter.timeBuf.length; i++) {
                        const s = meter.timeBuf[i];
                        sumSq += s * s;
                    }
                    meter.audioRms = Math.sqrt(sumSq / meter.timeBuf.length);
                }
                audioRms = meter.audioRms;
                loudnessRms = meter.loudnessRms;
                midEnergy = meter.midEnergy;
                liveBpm = meter.liveBpm;
                bpmConfidence = meter.bpmConfidence;
                liveKey = meter.liveKey;
                keyConfidence = meter.keyConfidence;
                if (meter.liveBpm >= 70 && meter.lastBeatTrackTime >= 0) {
                    const beatPeriod = 60.0 / meter.liveBpm;
                    const deltaBeat = (video.currentTime || 0) - meter.lastBeatTrackTime;
                    beatPhase = (((deltaBeat / beatPeriod) % 1.0) + 1.0) % 1.0;
                }
                if (meter.vizBands) vizBands = meter.vizBands.slice();
                audioReady = true;
            }
        } catch (e) {}
    }

    return JSON.stringify({
        hasVideo: !!video,
        isAd: !!document.querySelector('.ad-showing, .ad-interrupting'),
        currentTime: video ? (video.currentTime || 0) : 0,
        duration: video && isFinite(video.duration) ? video.duration : 0,
        paused: video ? video.paused : true,
        ended: video ? video.ended : false,
        volume: video ? video.volume : 1.0,
        playbackRate: video ? (video.playbackRate || 1.0) : 1.0,
        audioRms,
        audioReady,
        loudnessRms,
        midEnergy,
        liveBpm,
        bpmConfidence,
        liveKey,
        keyConfidence,
        beatPhase,
        vizBands,
        title,
        artist,
        videoId,
        nextVideoId,
        nextTitle
    });
})();
"""


class AdBlockInterceptor(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        url = info.requestUrl().toString().lower()
        for pat in AD_BLOCK_PATTERNS:
            if pat in url:
                info.block(True)
                return


@dataclass
class DeckState:
    has_video: bool = False
    is_ad: bool = False
    current_time: float = 0.0
    duration: float = 0.0
    paused: bool = True
    ended: bool = False
    volume: float = 1.0
    playback_rate: float = 1.0
    audio_rms: float = -1.0
    audio_ready: bool = False
    loudness_rms: float = 0.0
    mid_energy: float = 0.0
    live_bpm: float = 0.0
    bpm_confidence: float = 0.0
    live_key: str = ""
    key_confidence: float = 0.0
    beat_phase: float = -1.0
    title: str = "Play any song or playlist on YouTube Music"
    artist: str = ""
    video_id: str = ""
    next_video_id: str = ""
    next_title: str = ""
    cued_video_id: str = ""
    cue_ready: bool = False
    cued_outro_start: float = 0.0
    warm_running: bool = False

    @property
    def remaining(self) -> float:
        if self.duration <= 0:
            return 0.0
        return max(0.0, self.duration - self.current_time)


@dataclass
class SilenceTracker:
    """Tracks live audio RMS on the active deck to detect silent sections & outro energy drops."""

    video_id: str = ""
    heard_audio: bool = False
    silence_elapsed: float = 0.0
    outro_drop_elapsed: float = 0.0
    body_avg_rms: float = 0.0
    trigger_kind: str = ""
    last_track_time: float = 0.0
    last_poll_ts: float = 0.0

    def reset(self, video_id: str = "") -> None:
        self.video_id = video_id
        self.heard_audio = False
        self.silence_elapsed = 0.0
        self.outro_drop_elapsed = 0.0
        self.body_avg_rms = 0.0
        self.trigger_kind = ""
        self.last_track_time = 0.0
        self.last_poll_ts = 0.0

    def update(self, st: DeckState, now_ts: float) -> bool:
        if not st.video_id or st.video_id != self.video_id:
            self.reset(st.video_id)
            self.last_poll_ts = now_ts
            self.last_track_time = st.current_time
            return False

        # Reset timers if user scrubbed/seeked backward significantly
        if st.current_time + 1.5 < self.last_track_time:
            self.silence_elapsed = 0.0
            self.outro_drop_elapsed = 0.0
            self.trigger_kind = ""

        dt = 0.0
        if self.last_poll_ts > 0.0:
            dt = max(0.0, min(1.0, now_ts - self.last_poll_ts))
        self.last_poll_ts = now_ts
        self.last_track_time = st.current_time

        if (
            st.paused
            or st.is_ad
            or st.duration <= SILENCE_MIN_ELAPSED_SEC
            or st.volume < 0.2
            or not st.audio_ready
            or st.audio_rms < 0.0
        ):
            self.silence_elapsed = 0.0
            self.outro_drop_elapsed = 0.0
            self.trigger_kind = ""
            return False

        if st.audio_rms >= AUDIBLE_CONFIRM_RMS:
            self.heard_audio = True
            # Track rolling chorus/body loudness during main body of song
            if st.current_time < st.duration * OUTRO_DROP_MIN_PROGRESS:
                self.body_avg_rms = (
                    self.body_avg_rms * 0.90 + st.audio_rms * 0.10
                    if self.body_avg_rms > 0.0
                    else st.audio_rms
                )

        if not self.heard_audio:
            self.silence_elapsed = 0.0
            self.outro_drop_elapsed = 0.0
            self.trigger_kind = ""
            return False

        in_silence_zone = (
            st.current_time >= SILENCE_MIN_ELAPSED_SEC
            and (
                st.current_time >= st.duration * SILENCE_MIN_PROGRESS
                or st.remaining <= SILENCE_OUTRO_WINDOW_SEC
            )
        )
        if not in_silence_zone:
            self.silence_elapsed = 0.0
            self.outro_drop_elapsed = 0.0
            self.trigger_kind = ""
            return False

        # 1. Hard silence detection
        if st.audio_rms < SILENCE_RMS_THRESHOLD:
            self.silence_elapsed += dt
        elif st.audio_rms >= SILENCE_RESUME_THRESHOLD:
            self.silence_elapsed = 0.0

        if self.silence_elapsed >= SILENCE_HOLD_SEC:
            self.trigger_kind = "silence"
            return True

        # 2. Outro phrase energy-drop detection (Apple AutoMix-style chorus->outro drop)
        in_outro_zone = (
            st.current_time >= st.duration * OUTRO_DROP_MIN_PROGRESS
            or st.remaining <= OUTRO_DROP_WINDOW_SEC
        )
        ref_rms = max(self.body_avg_rms, st.loudness_rms)
        if in_outro_zone and ref_rms >= OUTRO_DROP_MIN_AVG_RMS:
            if st.audio_rms < ref_rms * OUTRO_DROP_RATIO:
                self.outro_drop_elapsed += dt
            elif st.audio_rms >= ref_rms * (OUTRO_DROP_RATIO + 0.12):
                self.outro_drop_elapsed = 0.0
        else:
            self.outro_drop_elapsed = 0.0

        if self.outro_drop_elapsed >= OUTRO_DROP_HOLD_SEC:
            self.trigger_kind = "outro_drop"
            return True

        self.trigger_kind = ""
        return False

    def is_triggered(self) -> bool:
        return self.heard_audio and bool(self.trigger_kind)


def compute_3band_vocal_eq(
    t: float,
    eq_strength: float,
    in_vocal_energy: float,
    out_vocal_energy: float,
) -> dict[str, float]:
    """Compute Apple-AutoMix / DJ 3-band EQ bass-swap and vocal-safe mid ducking."""
    t = max(0.0, min(1.0, t))
    eq_strength = max(0.3, min(1.3, eq_strength))

    # Clean bass swap at midpoint (t = 0.45 -> 0.60) so two kick drums never clash
    if t < 0.45:
        swap = 0.0
    elif t > 0.60:
        swap = 1.0
    else:
        swap = (t - 0.45) / 0.15
    smooth_swap = swap * swap * (3.0 - 2.0 * swap)

    out_low_db = -18.0 * smooth_swap * min(1.0, eq_strength)
    in_low_db = -18.0 * (1.0 - smooth_swap) * min(1.0, eq_strength)

    # Vocal-safe mid ducking (1.2 kHz peaking) when both tracks have vocal energy
    clash = min(1.0, max(0.0, (in_vocal_energy - 0.18) * 2.4)) * min(1.0, max(0.25, out_vocal_energy * 2.0))
    out_mid_db = -6.5 * clash * min(1.0, t * 2.2) if t > 0.15 else 0.0
    in_mid_db = -3.0 * (1.0 - t) * clash if t < 0.50 else 0.0

    # Gentle high-shelf air blend
    out_high_db = -4.0 * max(0.0, (t - 0.55) / 0.45)
    in_high_db = -3.0 * max(0.0, (0.35 - t) / 0.35)

    return {
        "out_low_db": out_low_db,
        "out_mid_db": out_mid_db,
        "out_high_db": out_high_db,
        "in_low_db": in_low_db,
        "in_mid_db": in_mid_db,
        "in_high_db": in_high_db,
    }


def compute_loudness_trim(out_loudness: float, in_loudness: float) -> float:
    """Compute Apple Sound-Check style loudness trim (0.45x-2.20x, limiter-protected)."""
    if in_loudness < 0.012:
        return 1.0
    target = out_loudness if out_loudness >= 0.02 else TARGET_LOUDNESS_RMS
    # Blend 70% toward outgoing track loudness + 30% toward global target RMS
    blended_target = target * 0.7 + TARGET_LOUDNESS_RMS * 0.3
    raw = blended_target / in_loudness
    return max(LOUDNESS_TRIM_MIN, min(LOUDNESS_TRIM_MAX, raw))


def compute_apple_automix_overlap(
    out_bpm: float,
    in_bpm: float,
    out_key: str,
    in_key: str,
    duration: float,
) -> tuple[float, float]:
    """Compute Apple AutoMix dynamic beat-phrase overlap (up to 24s) and Camelot key score."""
    key_score = calculate_key_compatibility(out_key, in_key)
    eff_bpm = out_bpm if out_bpm >= 60.0 else 120.0
    big_gap = is_large_bpm_gap(out_bpm, in_bpm)

    if big_gap:
        # Echo-out ambient blend for big BPM jumps
        overlap = 9.0
    elif key_score >= 0.8:
        # Harmonic key match: long 32-to-40 beat musical phrase blend (~16s-24s)
        beats = 36.0
        overlap = max(16.0, min(24.0, (beats * 60.0) / eff_bpm))
    elif key_score < 0.0:
        # Key clash: tighter 16-beat blend (~8s-10s)
        beats = 16.0
        overlap = max(8.0, min(10.0, (beats * 60.0) / eff_bpm))
    else:
        # Neutral key: smooth 28-beat phrase blend (~13s-19s)
        beats = 28.0
        overlap = max(13.0, min(19.0, (beats * 60.0) / eff_bpm))

    if duration > 10.0:
        overlap = min(overlap, max(6.0, duration * 0.35))
    return (round(overlap, 2), key_score)


def is_large_bpm_gap(out_bpm: float, in_bpm: float) -> bool:
    """Return True when BPM difference (>14% even after half/double time) shouldn't be stretched."""
    if out_bpm <= 0 or in_bpm <= 0:
        return False
    best_diff = min(
        abs(out_bpm - in_bpm),
        abs(out_bpm - in_bpm * 2.0),
        abs(out_bpm * 2.0 - in_bpm),
    )
    return (best_diff / max(1.0, out_bpm)) > 0.14


def compute_beat_phase_nudge(out_phase: float, in_phase: float) -> float:
    """Compute a gentle ±3% rate nudge to align incoming kick phase with outgoing kick."""
    if out_phase < 0.0 or in_phase < 0.0:
        return 0.0
    err = ((out_phase - in_phase + 0.5) % 1.0) - 0.5  # [-0.5, +0.5] beats
    if abs(err) < 0.05:
        return 0.0
    return max(-0.03, min(0.03, err * 0.08))


def compute_apple_automix_step(
    out_bpm: float,
    in_bpm: float,
    t: float,
    out_phase: float = -1.0,
    in_phase: float = -1.0,
) -> dict[str, float | bool]:
    """Compute Apple AutoMix gains, tempo lock + beat-phase snap, or Echo-Out fallback on big BPM gaps."""
    t = max(0.0, min(1.0, t))
    smooth_t = t * t * (3.0 - 2.0 * t)
    big_gap = is_large_bpm_gap(out_bpm, in_bpm)

    if big_gap:
        # Smart fallback for big BPM gaps (e.g. ballad -> EDM):
        # Natural 1.0x tempo (no awkward stretching) + gentle ambient echo-out & soft LPF
        out_gain = max(0.0, math.cos(min(1.0, smooth_t * 1.15) * math.pi * 0.5))
        in_gain = math.sin(max(0.0, (smooth_t - 0.12) / 0.88) * math.pi * 0.5)
        out_lpf_hz = 16000.0 * (1.0 - smooth_t) + 4200.0 * smooth_t
        echo_wet = 0.36 * math.sin(smooth_t * math.pi)
        return {
            "out_gain": max(0.0, min(1.0, out_gain)),
            "in_gain": max(0.0, min(1.0, in_gain)),
            "out_rate": 1.0,
            "in_rate": 1.0,
            "out_lpf_hz": out_lpf_hz,
            "echo_wet": echo_wet,
            "echo_fallback": True,
        }

    out_gain = math.cos(smooth_t * math.pi * 0.5)
    in_gain = math.sin(smooth_t * math.pi * 0.5)

    out_rate = 1.0
    in_rate = 1.0
    if out_bpm > 0 and in_bpm > 0:
        # Match nearest half/double-time equivalent BPM when appropriate
        eff_in_bpm = in_bpm
        if abs(out_bpm - in_bpm * 2.0) < abs(out_bpm - eff_in_bpm):
            eff_in_bpm = in_bpm * 2.0
        elif abs(out_bpm * 2.0 - in_bpm) < abs(out_bpm - eff_in_bpm):
            eff_in_bpm = in_bpm * 0.5
        target_bpm = out_bpm * (1.0 - smooth_t) + eff_in_bpm * smooth_t
        out_rate = max(0.94, min(1.06, target_bpm / out_bpm))
        in_rate = max(0.94, min(1.06, target_bpm / eff_in_bpm))
        if t < 0.65:
            in_rate = max(0.93, min(1.07, in_rate + compute_beat_phase_nudge(out_phase, in_phase)))

    return {
        "out_gain": max(0.0, min(1.0, out_gain)),
        "in_gain": max(0.0, min(1.0, in_gain)),
        "out_rate": out_rate,
        "in_rate": in_rate,
        "out_lpf_hz": 20000.0,
        "echo_wet": 0.0,
        "echo_fallback": False,
    }


class TrackCache:
    """Persistent per-videoId cache of live-detected BPM, Key, Loudness, and silent intro cue."""

    MAX_ENTRIES = 2000

    def __init__(self, path: str = TRACK_CACHE_PATH):
        self.path = path
        self.data: dict[str, dict] = {}
        self.dirty = False
        self.load()

    def load(self) -> None:
        try:
            if os.path.isfile(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and raw.get("__schema__") == CACHE_SCHEMA_VERSION:
                    tracks = raw.get("tracks", {})
                    if isinstance(tracks, dict):
                        self.data = tracks
                        return
                # Older v1 cache (from pre-fix muted analyser / 40ms BPM): reset cleanly
                self.data = {}
                self.dirty = True
                self.save()
        except Exception:
            self.data = {}

    def get(self, video_id: str) -> dict:
        if not video_id:
            return {}
        return self.data.get(video_id, {})

    def update_track(
        self,
        video_id: str,
        *,
        bpm: float = 0.0,
        bpm_conf: float = 0.0,
        key: str = "",
        key_conf: float = 0.0,
        loudness: float = 0.0,
        intro_sec: float | None = None,
        title: str = "",
    ) -> None:
        if not video_id:
            return
        entry = self.data.get(video_id, {})
        changed = False
        if bpm >= 70.0 and bpm_conf >= max(0.35, float(entry.get("bpm_conf", 0.0)) - 0.05):
            entry["bpm"] = round(bpm, 2)
            entry["bpm_conf"] = round(bpm_conf, 3)
            changed = True
        if key and key_conf >= max(0.25, float(entry.get("key_conf", 0.0)) - 0.05):
            entry["key"] = key
            entry["key_conf"] = round(key_conf, 3)
            changed = True
        if loudness >= 0.015:
            prev_l = float(entry.get("loudness", 0.0))
            new_l = round(prev_l * 0.5 + loudness * 0.5 if prev_l > 0 else loudness, 4)
            if abs(new_l - prev_l) >= 0.002:
                entry["loudness"] = new_l
                changed = True
        if intro_sec is not None and intro_sec >= 0.0:
            new_intro = round(min(INTRO_SKIP_MAX_SEC, intro_sec), 2)
            if entry.get("intro_sec") != new_intro:
                entry["intro_sec"] = new_intro
                changed = True
        if title and entry.get("title") != title:
            entry["title"] = title[:80]
            changed = True
        if changed:
            entry["updated"] = int(time.time())
            self.data[video_id] = entry
            self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        try:
            if len(self.data) > self.MAX_ENTRIES:
                ordered = sorted(
                    self.data.items(),
                    key=lambda kv: kv[1].get("updated", 0),
                    reverse=True,
                )
                self.data = dict(ordered[: self.MAX_ENTRIES])
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"__schema__": CACHE_SCHEMA_VERSION, "tracks": self.data}, f, indent=2)
            os.replace(tmp, self.path)
            self.dirty = False
        except Exception:
            pass


class SettingsStore:
    """Saves and restores SyncMix user preferences and window geometry."""

    def __init__(self, path: str = SETTINGS_PATH):
        self.path = path
        self.data: dict = {}
        self.load()

    def load(self) -> None:
        try:
            if os.path.isfile(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    self.data = raw
        except Exception:
            self.data = {}

    def save(self, payload: dict) -> None:
        try:
            self.data.update(payload)
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            pass


class MiniVisualizerWidget(QWidget):
    """Compact 10-band studio spectrum visualizer with subtle peak caps."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(56, 28)
        self.targets = [0.06] * 10
        self.levels = [0.06] * 10
        self.peaks = [0.06] * 10
        self.bar_color = QColor("#ededed")
        self.peak_color = QColor("#ff4d6d")
        self.setToolTip("Live Web Audio spectrum")

    def set_levels(self, bands: list[float], color_hex: str) -> None:
        if isinstance(bands, list) and len(bands) >= 10:
            self.targets = [max(0.06, min(1.0, float(v))) for v in bands[:10]]
        self.bar_color = QColor(color_hex)

    def step_animation(self) -> None:
        changed = False
        for i in range(10):
            tgt = self.targets[i]
            cur = self.levels[i]
            nxt = cur + (tgt - cur) * (0.52 if tgt > cur else 0.22)
            if abs(nxt - cur) > 0.004:
                self.levels[i] = nxt
                changed = True
            if nxt >= self.peaks[i]:
                self.peaks[i] = nxt
                changed = True
            elif self.peaks[i] > 0.06:
                self.peaks[i] = max(0.06, self.peaks[i] - 0.015)
                changed = True
        if changed:
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        n = len(self.levels)
        gap = 2
        bar_w = max(2, (w - gap * (n - 1)) // n)
        painter.setPen(Qt.PenStyle.NoPen)
        for i, lvl in enumerate(self.levels):
            bar_h = max(3, int(lvl * (h - 4)))
            x = i * (bar_w + gap)
            y = h - bar_h
            c = QColor(self.bar_color)
            c.setAlpha(int(120 + 135 * lvl))
            painter.setBrush(c)
            painter.drawRoundedRect(x, y, bar_w, bar_h, 1.5, 1.5)
            if self.peaks[i] > 0.16:
                peak_y = max(0, h - int(self.peaks[i] * (h - 4)) - 2)
                pc = QColor(self.peak_color)
                pc.setAlpha(210)
                painter.setBrush(pc)
                painter.drawRoundedRect(x, peak_y, bar_w, 2, 1.0, 1.0)
        painter.end()


class SilentWebPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        pass


def compute_fade_gains(t: float, curve: str) -> tuple[float, float]:
    t = max(0.0, min(1.0, t))
    if curve == "Equal Power":
        return math.cos(t * math.pi / 2.0), math.sin(t * math.pi / 2.0)
    elif curve == "Smooth S-Curve":
        s = t * t * (3.0 - 2.0 * t)
        return 1.0 - s, s
    return 1.0 - t, t


def compute_smart_outro_window(duration: float, max_fade: float) -> float:
    if duration < 120.0:
        return max(3.5, round(max_fade * 0.5, 1))
    elif duration <= 270.0:
        return max(5.0, round(max_fade * 0.85, 1))
    return max_fade


def estimate_track_bpm(video_id: str, title: str, duration: float) -> float:
    """Deterministically estimate a track's BPM in [96.0, 140.0] from metadata."""
    text = (title or "").lower()
    base = 114.0
    if any(k in text for k in ("dnb", "drum & bass", "drum and bass", "fast")):
        base = 136.0
    elif any(k in text for k in ("techno", "trance", "hardstyle", "rave")):
        base = 130.0
    elif any(k in text for k in ("house", "edm", "club", "dance", "remix", "dj")):
        base = 124.0
    elif any(k in text for k in ("pop", "synth", "disco", "funk", "electro")):
        base = 116.0
    elif any(k in text for k in ("hip hop", "rap", "trap", "r&b", "chill", "lofi", "acoustic", "ballad")):
        base = 98.0
    elif duration > 0 and duration < 150.0:
        base = 122.0

    sig = f"{video_id}|{title}".encode("utf-8", errors="ignore")
    digest = hashlib.sha256(sig).digest()
    offset = (digest[0] % 13) - 6  # deterministic [-6, +6] BPM variation
    return round(max(96.0, min(140.0, base + offset)), 1)


def compute_automix_32beat_window(duration: float, bpm: float) -> float:
    """Compute a 32-beat (or 16-beat for short songs) bar-quantized transition window independently."""
    safe_bpm = max(80.0, bpm)
    pulses = CUE_PULSE if (0.0 < duration < 120.0) else TRANSITION_PULSES
    beat_sec = 60.0 / safe_bpm
    bar_sec = PULSES_PER_BAR * beat_sec
    raw_32beat = pulses * beat_sec
    quantized = round(raw_32beat / bar_sec) * bar_sec
    window = max(6.0, min(16.0, quantized))
    if duration > 0.0:
        window = min(duration * 0.25, window)
    return round(max(4.0, window), 2)


def compute_vertex_automix_gains(t: float) -> tuple[float, float]:
    """
    Two-stage VertexSelection/automix-engine volume envelope across t in [0.0, 1.0]:
    - Stage 1 (0.0 <= t < 0.45): outgoing holds energy fill shelf (~-6 dB, 0.28-0.65)
      while incoming builds smoothly before CUE_PULSE.
    - Stage 2 (0.45 <= t <= 1.0): vocal-safe handoff reveals full incoming presence
      while outgoing fill tapers cleanly to 0.0.
    """
    t = max(0.0, min(1.0, t))
    target_fill = max(
        OUTGOING_FILL_MIN_GAIN,
        min(
            OUTGOING_FILL_MAX_GAIN,
            10.0 ** (-OUTGOING_FILL_TARGET_DB_BELOW_INCOMING / 20.0),
        ),
    )
    if t < CUE_VOCAL_MIN_TRANSITION_FRACTION:
        u = t / CUE_VOCAL_MIN_TRANSITION_FRACTION
        in_gain = VOCAL_REVEAL_LINE_GAIN_MAX * (u * (0.6 + 0.4 * u))
        out_gain = 1.0 - (1.0 - target_fill) * (u ** 0.85)
        return out_gain, in_gain

    v = (t - CUE_VOCAL_MIN_TRANSITION_FRACTION) / (1.0 - CUE_VOCAL_MIN_TRANSITION_FRACTION)
    s = v * v * (3.0 - 2.0 * v)
    in_gain = VOCAL_REVEAL_LINE_GAIN_MAX + (1.0 - VOCAL_REVEAL_LINE_GAIN_MAX) * s
    out_gain = target_fill * (1.0 - s)
    return out_gain, in_gain


def compute_automix_playback_rates(
    out_bpm: float, in_bpm: float, t: float
) -> tuple[float, float]:
    """
    Compute pitch-preserving playback rates clamped to [0.82, 1.18] so both tracks
    lock onto a shared bridge tempo gliding from out_bpm toward midpoint BPM during
    the 32-beat overlap (leaving a smooth post-fade release to 1.0 over 6.48s).
    """
    t = max(0.0, min(1.0, t))
    safe_out = max(60.0, out_bpm)
    safe_in = max(60.0, in_bpm)
    bridge_bpm = safe_out + 0.5 * (safe_in - safe_out) * t
    out_rate = max(
        CUE_VOCAL_MIN_PLAYBACK_RATE,
        min(CUE_VOCAL_MAX_PLAYBACK_RATE, bridge_bpm / safe_out),
    )
    in_rate = max(
        CUE_VOCAL_MIN_PLAYBACK_RATE,
        min(CUE_VOCAL_MAX_PLAYBACK_RATE, bridge_bpm / safe_in),
    )
    return round(out_rate, 4), round(in_rate, 4)


def estimate_track_key(video_id: str, title: str) -> str:
    """Deterministically assign one of the 24 Camelot Wheel keys from track metadata."""
    sig = f"{video_id}|{title}".encode("utf-8", errors="ignore")
    digest = hashlib.sha256(sig).digest()
    return CAMELOT_KEYS[digest[1] % len(CAMELOT_KEYS)]


def calculate_key_compatibility(key1: str, key2: str) -> float:
    """Camelot Wheel harmonic compatibility score: 1.0 (perfect), 0.8 (relative), -1.0 (clash)."""
    if not key1 or not key2 or key1 not in CAMELOT_WHEEL:
        return 0.5
    if key1 == key2 or key2 in CAMELOT_WHEEL[key1].get("compatible", []):
        return 1.0
    if key1.endswith("m") and key2 == key1[:-1]:
        return 0.8
    if not key1.endswith("m") and key2 == key1 + "m":
        return 0.8
    return -1.0


def detect_track_genre(title: str, artist: str = "") -> str:
    """Infer coarse genre bucket for Pro DJ genre mixing rules."""
    text = f"{title} {artist}".lower()
    if any(k in text for k in ("house", "deep house", "tech house")):
        return "house"
    if any(k in text for k in ("edm", "techno", "trance", "dubstep", "electro", "club", "rave", "remix")):
        return "edm"
    if any(k in text for k in ("hip hop", "hip-hop", "trap", "drill")):
        return "hip"
    if any(k in text for k in ("rap", "freestyle", "cypher")):
        return "rap"
    if any(k in text for k in ("rock", "metal", "punk", "indie")):
        return "rock"
    return "pop"


def calculate_pro_dj_overlap(
    out_bpm: float,
    in_bpm: float,
    out_key: str,
    in_key: str,
    out_genre: str,
    in_genre: str,
    duration: float = 0.0,
) -> tuple[float, float, float]:
    """
    Port of generate_mixing_plan.calculate_dynamic_overlap() + mixing_engine.GENRE_MIXING_RULES:
    Computes dynamic overlap in [4.0, 16.0]s and effective EQ filter strength.
    Returns (overlap_sec, key_score, eq_strength).
    """
    base_overlap = 8.0
    bpm_diff = abs(out_bpm - in_bpm)
    if bpm_diff > 20.0:
        base_overlap += 4.0
    elif bpm_diff > 10.0:
        base_overlap += 2.0

    key_score = calculate_key_compatibility(out_key, in_key)
    if key_score < 0.0:
        base_overlap -= 2.0
    elif key_score >= 1.0:
        base_overlap += 2.0

    out_rule = PRO_DJ_GENRE_RULES.get(out_genre, PRO_DJ_GENRE_RULES["pop"])
    in_rule = PRO_DJ_GENRE_RULES.get(in_genre, PRO_DJ_GENRE_RULES["pop"])
    if out_genre in ("edm", "house") or in_genre in ("edm", "house"):
        base_overlap += 4.0
    elif out_genre in ("hip", "rap") or in_genre in ("hip", "rap"):
        base_overlap -= 2.0
    else:
        base_overlap += out_rule["overlap_delta"]

    overlap = max(4.0, min(16.0, base_overlap))
    if duration > 0.0:
        overlap = min(max(4.0, duration * 0.25), overlap)
    eq_strength = max(out_rule["eq_filter_strength"], in_rule["eq_filter_strength"])
    return round(overlap, 2), key_score, eq_strength


def compute_pro_dj_step(
    out_bpm: float,
    in_bpm: float,
    eq_strength: float,
    t: float,
) -> dict:
    """
    Compute live Pro DJ Mix parameters across normalized overlap t in [0.0, 1.0]:
    - Equal-power gain fade with echo-transition tail in the final 35%
    - Progressive LPF sweep on outgoing (12kHz -> ~2.2-4kHz) + HPF bass-swap sweep on incoming (260Hz -> 20Hz)
    - 32 micro-step CDJ tempo ramp (apply_gradual_tempo_sync) within ±5% stretch bounds
    """
    t = max(0.0, min(1.0, t))
    q = int(t * (PRO_DJ_RAMP_STEPS - 1)) / float(PRO_DJ_RAMP_STEPS - 1)

    safe_out = max(60.0, out_bpm)
    safe_in = max(60.0, in_bpm)
    delta_out = max(-PRO_DJ_MAX_STRETCH, min(PRO_DJ_MAX_STRETCH, (safe_in / safe_out - 1.0) * 0.6))
    delta_in = max(-PRO_DJ_MAX_STRETCH, min(PRO_DJ_MAX_STRETCH, (safe_out / safe_in - 1.0) * 0.6))
    out_rate = round(1.0 + delta_out * q, 4)
    in_rate = round(1.0 + delta_in * (1.0 - q), 4)

    out_gain = math.cos(t * math.pi / 2.0)
    in_gain = math.sin(t * math.pi / 2.0)

    # Progressive LPF on outgoing (mixing_engine.apply_progressive_eq: 12kHz -> 4kHz, deeper during echo tail)
    min_lpf = max(1800.0, 4000.0 / max(0.8, eq_strength))
    out_lpf_hz = 12000.0 - (12000.0 - min_lpf) * t

    # Incoming HPF prevents bass clash early, then opens full sub-bass cleanly
    in_hpf_hz = 260.0 - 240.0 * min(1.0, t / 0.85) if t < 0.85 else 20.0

    # Decaying echo tail on outgoing track for final ~35% (inspired by apply_echo_transition)
    echo_wet = 0.0
    if t >= 0.65:
        eu = (t - 0.65) / 0.35
        echo_wet = 0.45 * math.sin(eu * math.pi * 0.85)

    return {
        "out_gain": out_gain,
        "in_gain": in_gain,
        "out_rate": out_rate,
        "in_rate": in_rate,
        "out_lpf_hz": round(out_lpf_hz, 1),
        "in_hpf_hz": round(in_hpf_hz, 1),
        "echo_wet": round(echo_wet, 3),
    }


def fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    m, sec = divmod(s, 60)
    return f"{m}:{sec:02d}"


class CrossfaderWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SyncMix — YouTube Music AutoMix")
        self.resize(1280, 860)
        self.setStyleSheet("background-color: #0f0f10; color: #ededed;")

        os.makedirs(PROFILE_DIR, exist_ok=True)
        self.profile = QWebEngineProfile("SyncMixProfile", self)
        self.profile.setPersistentStoragePath(PROFILE_DIR)
        self.profile.setCachePath(os.path.join(PROFILE_DIR, "cache"))
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )

        self.ad_blocker = AdBlockInterceptor(self)
        self.profile.setUrlRequestInterceptor(self.ad_blocker)

        ad_script = QWebEngineScript()
        ad_script.setName("SyncMixAdBlock")
        ad_script.setSourceCode(ADBLOCK_SCRIPT_JS)
        ad_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        ad_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        ad_script.setRunsOnSubFrames(True)
        self.profile.scripts().insert(ad_script)

        settings = self.profile.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False
        )

        self.decks: dict[str, QWebEngineView] = {}
        self.states: dict[str, DeckState] = {"A": DeckState(), "B": DeckState()}

        self.is_fading = False
        self.fade_elapsed = 0.0
        self.fade_duration = 6.0
        self.wait_for_incoming_elapsed = 0.0
        self.last_faded_video_id = ""
        self.outgoing_video_id = ""
        self.out_bpm = 120.0
        self.in_bpm = 120.0
        self.out_key = "C"
        self.in_key = "C"
        self.out_genre = "pop"
        self.in_genre = "pop"
        self.pro_eq_strength = 1.0
        self.pro_key_score = 0.5
        self.incoming_bpm_resolved = False
        self.incoming_rate_synced = False
        self.incoming_started = False
        self.fade_paused_by_user = False
        self._active_fade_badge = ("Crossfading", "#4a3210", "#ffb84d")
        self.release_start_rate = 1.0
        self.release_step = 0
        self.last_applied_rate = {"A": 1.0, "B": 1.0}
        self.silence_tracker = SilenceTracker()
        self.out_loudness = 0.0
        self.intro_skip_done = False
        self.intro_silence_elapsed = 0.0
        self.intro_skip_hops = 0
        self.track_cache = TrackCache()
        self.settings_store = SettingsStore()
        self.manual_mix_pending = False
        self._really_quit = False
        self._tray_notified = False
        self._last_seen_video_id = ""
        self.tray_icon: QSystemTrayIcon | None = None

        self._build_ui()
        self._setup_tray()
        self._load_initial_pages()

        self.mix_shortcut = QShortcut(QKeySequence("Ctrl+M"), self)
        self.mix_shortcut.activated.connect(self._trigger_manual_mix)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_decks)
        self.poll_timer.start(250)

        self.fade_timer = QTimer(self)
        self.fade_timer.timeout.connect(self._step_fade)

        self.release_timer = QTimer(self)
        self.release_timer.timeout.connect(self._step_tempo_release)

        self.viz_timer = QTimer(self)
        self.viz_timer.timeout.connect(self.visualizer.step_animation)
        self.viz_timer.start(40)

        self._restore_settings()

    def _create_deck_view(self) -> QWebEngineView:
        view = QWebEngineView(self)
        page = SilentWebPage(self.profile, view)
        view.setPage(page)
        return view

    def _make_divider(self) -> QFrame:
        div = QFrame()
        div.setFixedSize(1, 28)
        div.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); border: none;")
        return div

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # --- Studio Mastering Top Control Bar (64px, 4px grid, Graphite + single coral accent) ---
        top_bar = QFrame()
        top_bar.setFixedHeight(64)
        top_bar.setStyleSheet(
            "QFrame#SyncMixTopBar {"
            " background-color: #141415;"
            " border: 1px solid rgba(255, 255, 255, 0.08);"
            " border-radius: 12px;"
            "}"
        )
        top_bar.setObjectName("SyncMixTopBar")

        bar_layout = QHBoxLayout(top_bar)
        bar_layout.setContentsMargins(16, 12, 16, 12)
        bar_layout.setSpacing(12)

        # Zone 1: Now Playing (Live Visualizer + Track Info + Monospace Telemetry + 4px Rail)
        self.visualizer = MiniVisualizerWidget()

        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        self.title_lbl = QLabel("Play any track on YouTube Music")
        self.title_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
        self.title_lbl.setStyleSheet("color: #ededed; border: none; background: transparent;")

        self.artist_lbl = QLabel("")
        self.artist_lbl.setStyleSheet("color: #85858b; font-size: 12px; border: none; background: transparent;")

        self.time_lbl = QLabel("0:00 / 0:00")
        mono_font = QFont("Cascadia Mono", 9)
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        self.time_lbl.setFont(mono_font)
        self.time_lbl.setStyleSheet("color: #a1a1a6; border: none; background: transparent;")

        title_row.addWidget(self.title_lbl)
        title_row.addWidget(self.artist_lbl)
        title_row.addStretch()
        title_row.addWidget(self.time_lbl)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setStyleSheet(
            """
            QProgressBar {
                background-color: #232325;
                border-radius: 2px;
                border: none;
            }
            QProgressBar::chunk {
                background-color: #ff4d6d;
                border-radius: 2px;
            }
            """
        )

        info_col.addLayout(title_row)
        info_col.addWidget(self.progress)

        # Zone 2: Mix Controls (quiet neutral chrome, uniform 32px height, 8px radius)
        cb_style = (
            "QCheckBox {"
            " color: #a1a1a6;"
            " font-size: 12px;"
            " font-weight: 400;"
            " spacing: 6px;"
            " border: none;"
            " background: transparent;"
            "}"
            "QCheckBox:hover { color: #ededed; }"
            "QCheckBox:checked { color: #ededed; }"
        )
        self.auto_cb = QCheckBox("AutoMix")
        self.auto_cb.setChecked(True)
        self.auto_cb.setToolTip("Automatically blend into the next queued track")
        self.auto_cb.setStyleSheet(cb_style)

        self.silence_cb = QCheckBox("Silence")
        self.silence_cb.setChecked(True)
        self.silence_cb.setToolTip(
            "Detect silent parts and quiet outros in real time and start mixing from that point"
        )
        self.silence_cb.setStyleSheet(cb_style)

        control_style = (
            "QComboBox, QSpinBox {"
            " background-color: #1f1f22;"
            " color: #ededed;"
            " border: 1px solid rgba(255, 255, 255, 0.08);"
            " border-radius: 8px;"
            " padding: 0px 10px;"
            " font-size: 12px;"
            "}"
            "QComboBox:hover, QSpinBox:hover {"
            " border-color: rgba(255, 255, 255, 0.18);"
            " background-color: #252529;"
            "}"
            "QComboBox::drop-down { border: none; width: 20px; }"
            "QComboBox QAbstractItemView {"
            " background-color: #19191b;"
            " color: #ededed;"
            " border: 1px solid rgba(255, 255, 255, 0.10);"
            " selection-background-color: #2a2a2e;"
            " selection-color: #ffffff;"
            " outline: none;"
            "}"
        )

        self.mode_combo = QComboBox()
        self.mode_combo.setFixedHeight(32)
        self.mode_combo.addItems([MIX_MODE_STANDARD, MIX_MODE_AUTOMIX, MIX_MODE_PRO_DJ, MIX_MODE_APPLE])
        self.mode_combo.setToolTip(
            "Crossfade (Spotify-style) · 32-Beat AutoMix (VertexSelection) · Pro DJ (AI-DJ-Mixing-System) · Apple AutoMix"
        )
        self.mode_combo.setStyleSheet(control_style)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)

        self.dur_lbl = QLabel("Fade")
        self.dur_lbl.setStyleSheet("color: #85858b; font-size: 12px; border: none; background: transparent;")
        self.dur_spin = QSpinBox()
        self.dur_spin.setFixedHeight(32)
        self.dur_spin.setRange(1, 15)
        self.dur_spin.setValue(6)
        self.dur_spin.setSuffix("s")
        self.dur_spin.setStyleSheet(control_style)

        self.curve_combo = QComboBox()
        self.curve_combo.setFixedHeight(32)
        self.curve_combo.addItems(["Equal Power", "Smooth S-Curve", "Linear"])
        self.curve_combo.setToolTip("Volume fade curve for Crossfade mode")
        self.curve_combo.setStyleSheet(control_style)

        # Zone 3: Signature Status Dot + Primary Mix Now Action (with Ctrl+M keycap)
        status_wrap = QHBoxLayout()
        status_wrap.setContentsMargins(0, 0, 0, 0)
        status_wrap.setSpacing(6)

        self.status_dot = QFrame()
        self.status_dot.setFixedSize(6, 6)
        self.status_dot.setStyleSheet(
            "background-color: #4cc38a; border-radius: 3px; border: none;"
        )

        self.status_badge = QLabel("Ad-block active")
        self.status_badge.setStyleSheet(
            "color: #a1a1a6; font-size: 12px; font-weight: 400; border: none; background: transparent;"
        )
        status_wrap.addWidget(self.status_dot)
        status_wrap.addWidget(self.status_badge)

        self.mix_now_btn = QPushButton("Mix Now   Ctrl+M")
        self.mix_now_btn.setFixedHeight(32)
        self.mix_now_btn.setToolTip(
            "Start a smooth transition into the next queued track (Ctrl+M)"
        )
        self.mix_now_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mix_now_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #ededed;
                color: #0f0f10;
                border: none;
                border-radius: 8px;
                padding: 0px 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #d4d4d8;
            }
            """
        )
        self.mix_now_btn.clicked.connect(self._trigger_manual_mix)

        bar_layout.addWidget(self.visualizer)
        bar_layout.addLayout(info_col, 3)
        bar_layout.addWidget(self._make_divider())
        bar_layout.addWidget(self.auto_cb)
        bar_layout.addWidget(self.silence_cb)
        bar_layout.addWidget(self.mode_combo)
        bar_layout.addWidget(self.dur_lbl)
        bar_layout.addWidget(self.dur_spin)
        bar_layout.addWidget(self.curve_combo)
        bar_layout.addWidget(self._make_divider())
        bar_layout.addLayout(status_wrap)
        bar_layout.addWidget(self.mix_now_btn)

        self.auto_cb.toggled.connect(self._save_settings)
        self.silence_cb.toggled.connect(self._save_settings)
        self.mode_combo.currentTextChanged.connect(self._save_settings)
        self.dur_spin.valueChanged.connect(self._save_settings)
        self.curve_combo.currentTextChanged.connect(self._save_settings)

        root.addWidget(top_bar)

        # --- Single Visible YouTube Music Deck + Hidden Outgoing-Tail Buffer ---
        self.decks["A"] = self._create_deck_view()
        self.decks["B"] = self._create_deck_view()
        self.decks["B"].hide()

        root.addWidget(self.decks["A"], 1)

    def _restore_settings(self):
        cfg = self.settings_store.data
        if not cfg:
            return
        self._restoring_settings = True
        try:
            if "smart_automix" in cfg:
                self.auto_cb.setChecked(bool(cfg["smart_automix"]))
            if "silence_mix" in cfg:
                self.silence_cb.setChecked(bool(cfg["silence_mix"]))
            if "crossfade_sec" in cfg:
                try:
                    self.dur_spin.setValue(int(cfg["crossfade_sec"]))
                except Exception:
                    pass
            if "curve" in cfg and self.curve_combo.findText(str(cfg["curve"])) >= 0:
                self.curve_combo.setCurrentText(str(cfg["curve"]))
            if "mix_mode" in cfg:
                saved_mode = str(cfg["mix_mode"])
                mapped_mode = LEGACY_MIX_MODE_MAP.get(saved_mode, saved_mode)
                if self.mode_combo.findText(mapped_mode) >= 0:
                    self.mode_combo.setCurrentText(mapped_mode)
            geom = cfg.get("window_geometry")
            if isinstance(geom, list) and len(geom) == 4:
                try:
                    x, y, w, h = [int(v) for v in geom]
                    if w >= 640 and h >= 480:
                        self.setGeometry(x, y, w, h)
                except Exception:
                    pass
        finally:
            self._restoring_settings = False

    def _save_settings(self, *_args):
        if getattr(self, "_restoring_settings", False):
            return
        g = self.geometry()
        self.settings_store.save(
            {
                "mix_mode": self.mode_combo.currentText(),
                "smart_automix": self.auto_cb.isChecked(),
                "silence_mix": self.silence_cb.isChecked(),
                "crossfade_sec": self.dur_spin.value(),
                "curve": self.curve_combo.currentText(),
                "window_geometry": [g.x(), g.y(), g.width(), g.height()],
            }
        )

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = _load_app_icon()
        if icon.isNull():
            return
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("SyncMix — YouTube Music AutoMix Player")

        menu = QMenu(self)
        show_act = QAction("Show SyncMix", self)
        show_act.triggered.connect(self._restore_from_tray)
        play_act = QAction("Play / Pause", self)
        play_act.triggered.connect(self._toggle_play_pause)
        mix_act = QAction("Mix Now (Ctrl+M)", self)
        mix_act.triggered.connect(self._trigger_manual_mix)
        quit_act = QAction("Quit SyncMix", self)
        quit_act.triggered.connect(self._quit_app)

        menu.addAction(show_act)
        menu.addAction(play_act)
        menu.addAction(mix_act)
        menu.addSeparator()
        menu.addAction(quit_act)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _toggle_play_pause(self):
        self.decks["A"].page().runJavaScript(
            "(() => { const v = document.querySelector('video'); if (!v) return; if (v.paused) v.play().catch(()=>{}); else v.pause(); })();"
        )

    def _trigger_manual_mix(self):
        if self.is_fading:
            return
        act = self.states["A"]
        tail = self.states["B"]
        if act.paused or act.is_ad or not act.video_id or act.duration <= 5.0:
            return
        smart_fade = self._get_target_outro_window(act)
        if tail.cue_ready and tail.cued_video_id == act.video_id:
            self.manual_mix_pending = False
            self._trigger_spotify_crossfade(act, smart_fade, trigger_reason="manual")
        else:
            self.manual_mix_pending = True
            self._preload_outgoing_tail()
            self._set_badge("Mix Now Cued...", "#3b234a", "#e879f9")

    def _quit_app(self):
        self._really_quit = True
        self._save_settings()
        self.track_cache.save()
        if self.tray_icon:
            self.tray_icon.hide()
        QApplication.quit()

    def closeEvent(self, event):
        self._save_settings()
        self.track_cache.save()
        if self.tray_icon and self.tray_icon.isVisible() and not self._really_quit:
            event.ignore()
            self.hide()
            if not self._tray_notified:
                self._tray_notified = True
                self.tray_icon.showMessage(
                    "SyncMix Crossfader",
                    "Still playing in the background. Right-click the tray icon to Quit.",
                    QSystemTrayIcon.MessageIcon.Information,
                    3000,
                )
            return
        super().closeEvent(event)

    def _is_standard_crossfade(self) -> bool:
        return self.mode_combo.currentText() == MIX_MODE_STANDARD

    def _is_github_automix(self) -> bool:
        return self.mode_combo.currentText() == MIX_MODE_AUTOMIX

    def _is_pro_dj_mix(self) -> bool:
        return self.mode_combo.currentText() == MIX_MODE_PRO_DJ

    def _is_apple_automix(self) -> bool:
        return self.mode_combo.currentText() == MIX_MODE_APPLE

    def _armed_badge(self) -> tuple[str, str, str]:
        if self._is_apple_automix():
            return ("Armed", "#141415", "#ff4d6d")
        if self._is_github_automix():
            return ("Armed · 32-beat", "#141415", "#ededed")
        if self._is_pro_dj_mix():
            return ("Armed · Pro DJ", "#141415", "#ededed")
        return ("Armed", "#141415", "#ededed")

    def _buffering_badge(self) -> str:
        return "Buffering..."

    def _on_mode_changed(self, _text: str):
        is_standard = self._is_standard_crossfade()
        self.dur_lbl.setVisible(is_standard)
        self.dur_spin.setVisible(is_standard)
        self.curve_combo.setVisible(is_standard)
        if not is_standard:
            if not self.auto_cb.isChecked():
                self.auto_cb.setChecked(True)
            if self._is_pro_dj_mix() or self._is_apple_automix():
                self.release_timer.stop()
                self.release_step = 0
                self._set_deck_playback_rate("A", 1.0, force=True)
                self._set_deck_playback_rate("B", 1.0, force=True)
                self._set_pro_dj_fx("A", 20000.0, 20.0, 0.0)
                if self.states["B"].cued_video_id:
                    self._set_pro_dj_fx("B", 20000.0, 20.0, 0.0)
            if not self.is_fading:
                if self.states["B"].cue_ready:
                    self._set_badge(*self._armed_badge())
                elif self._is_apple_automix():
                    self._set_badge("Ready", "#141415", "#ff4d6d")
                else:
                    self._set_badge("Ready", "#141415", "#a1a1a6")
        else:
            self.release_timer.stop()
            self.release_step = 0
            self._set_deck_playback_rate("A", 1.0, force=True)
            self._set_deck_playback_rate("B", 1.0, force=True)
            self._reset_pro_dj_fx("A")
            self._reset_pro_dj_fx("B")
            if not self.is_fading:
                if self.states["B"].cue_ready:
                    self._set_badge("Armed", "#141415", "#ededed")
                else:
                    self._set_badge("Ad-block active", "#141415", "#4cc38a")

    def _resolve_bpm(self, st: DeckState) -> float:
        if st.live_bpm >= 70.0 and st.bpm_confidence >= 0.35:
            return st.live_bpm
        cached = self.track_cache.get(st.video_id)
        if cached.get("bpm", 0.0) >= 70.0:
            return float(cached["bpm"])
        return estimate_track_bpm(st.video_id, st.title, st.duration)

    def _resolve_key(self, st: DeckState) -> str:
        if st.live_key and st.key_confidence >= 0.25:
            return st.live_key
        cached = self.track_cache.get(st.video_id)
        if cached.get("key"):
            return str(cached["key"])
        return estimate_track_key(st.video_id, st.title)

    def _resolve_next_bpm_key(self, next_video_id: str, next_title: str, fallback_dur: float) -> tuple[float, str]:
        cached = self.track_cache.get(next_video_id)
        bpm = float(cached["bpm"]) if cached.get("bpm", 0.0) >= 70.0 else estimate_track_bpm(
            next_video_id, next_title, fallback_dur
        )
        key = str(cached["key"]) if cached.get("key") else estimate_track_key(next_video_id, next_title)
        return bpm, key

    def _get_target_outro_window(self, st: DeckState) -> float:
        if self._is_apple_automix():
            out_bpm = self._resolve_bpm(st)
            out_key = self._resolve_key(st)
            has_next = bool(st.next_video_id or st.next_title)
            if has_next:
                in_bpm, in_key = self._resolve_next_bpm_key(
                    st.next_video_id or f"{st.video_id}_next",
                    st.next_title or "",
                    st.duration,
                )
            else:
                in_bpm = out_bpm
                in_key = out_key
            overlap, _ = compute_apple_automix_overlap(
                out_bpm, in_bpm, out_key, in_key, st.duration
            )
            return overlap
        if self._is_github_automix():
            bpm = estimate_track_bpm(st.video_id, st.title, st.duration)
            return compute_automix_32beat_window(st.duration, bpm)
        if self._is_pro_dj_mix():
            out_bpm = estimate_track_bpm(st.video_id, st.title, st.duration)
            out_key = estimate_track_key(st.video_id, st.title)
            out_genre = detect_track_genre(st.title, st.artist)
            has_next = bool(st.next_video_id or st.next_title)
            if has_next:
                in_bpm = estimate_track_bpm(
                    st.next_video_id or f"{st.video_id}_next",
                    st.next_title or "",
                    st.duration,
                )
                in_key = estimate_track_key(
                    st.next_video_id or f"{st.video_id}_next",
                    st.next_title or "",
                )
                in_genre = detect_track_genre(st.next_title or "", "")
            else:
                in_bpm = out_bpm
                in_key = out_key
                in_genre = out_genre
            overlap, _, _ = calculate_pro_dj_overlap(
                out_bpm, in_bpm, out_key, in_key, out_genre, in_genre, st.duration
            )
            return overlap
        return compute_smart_outro_window(st.duration, float(self.dur_spin.value()))

    def _set_pro_dj_fx(
        self,
        deck_id: str,
        lpf_hz: float,
        hpf_hz: float,
        echo_wet: float,
        low_db: float = 0.0,
        mid_db: float = 0.0,
        high_db: float = 0.0,
        trim_gain: float = 1.0,
    ):
        lpf_hz = max(400.0, min(20000.0, lpf_hz))
        hpf_hz = max(20.0, min(1000.0, hpf_hz))
        echo_wet = max(0.0, min(0.75, echo_wet))
        low_db = max(-24.0, min(6.0, low_db))
        mid_db = max(-12.0, min(6.0, mid_db))
        high_db = max(-12.0, min(6.0, high_db))
        trim_gain = max(LOUDNESS_TRIM_MIN, min(LOUDNESS_TRIM_MAX, trim_gain))
        js = (
            "(() => {"
            " const v = document.querySelector('video');"
            " if (!v) return;"
            " let fx = window.__syncmixProFx;"
            " if (!fx || fx.video !== v) {"
            "  try {"
            "   const Ctx = window.AudioContext || window.webkitAudioContext;"
            "   const ctx = (fx && fx.ctx) || window.__syncmixAudioCtx || new Ctx();"
            "   window.__syncmixAudioCtx = ctx;"
            "   if (ctx.state === 'suspended') ctx.resume().catch(()=>{});"
            "   const src = v.__syncmixMediaSrc || ctx.createMediaElementSource(v);"
            "   v.__syncmixMediaSrc = src;"
            "   try { src.disconnect(); } catch (e) {}"
            "   v.__syncmixDirectConnected = false;"
            "   let mixGain = window.__syncmixMixGain;"
            "   let limiter = window.__syncmixLimiter;"
            "   if (!mixGain || mixGain.context !== ctx) {"
            "    mixGain = ctx.createGain();"
            "    const initVol = (typeof v.__syncmixTargetVol === 'number') ? v.__syncmixTargetVol : (v.muted ? 0.0 : v.volume);"
            "    mixGain.gain.value = Math.max(0.0, Math.min(1.0, initVol));"
            "    limiter = ctx.createDynamicsCompressor();"
            "    limiter.threshold.value = -2.0; limiter.knee.value = 6.0; limiter.ratio.value = 12.0;"
            "    limiter.attack.value = 0.003; limiter.release.value = 0.12;"
            "    mixGain.connect(limiter); limiter.connect(ctx.destination);"
            "    window.__syncmixMixGain = mixGain; window.__syncmixLimiter = limiter;"
            "   }"
            "   if (!v.__syncmixAdMuted) { v.muted = false; v.volume = 1.0; }"
            "   const hpf = (fx && fx.hpf) || ctx.createBiquadFilter(); hpf.type = 'highpass';"
            "   const lpf = (fx && fx.lpf) || ctx.createBiquadFilter(); lpf.type = 'lowpass';"
            "   const lowEq = (fx && fx.lowEq) || ctx.createBiquadFilter(); lowEq.type = 'lowshelf'; lowEq.frequency.value = 220;"
            "   const midEq = (fx && fx.midEq) || ctx.createBiquadFilter(); midEq.type = 'peaking'; midEq.frequency.value = 1200; midEq.Q.value = 0.8;"
            "   const highEq = (fx && fx.highEq) || ctx.createBiquadFilter(); highEq.type = 'highshelf'; highEq.frequency.value = 4000;"
            "   const trim = (fx && fx.trim) || ctx.createGain();"
            "   const dryGain = (fx && fx.dryGain) || ctx.createGain();"
            "   const delay = (fx && fx.delay) || ctx.createDelay(2.0); delay.delayTime.value = " + f"{PRO_DJ_ECHO_DELAY_SEC:.3f}" + ";"
            "   const fb = (fx && fx.fb) || ctx.createGain(); fb.gain.value = " + f"{PRO_DJ_ECHO_DECAY:.2f}" + ";"
            "   const echoLpf = (fx && fx.echoLpf) || ctx.createBiquadFilter(); echoLpf.type = 'lowpass'; echoLpf.frequency.value = 3200;"
            "   const echoWet = (fx && fx.echoWet) || ctx.createGain();"
            "   if (!fx) {"
            "    hpf.frequency.value = 20; lpf.frequency.value = 20000;"
            "    lowEq.gain.value = 0; midEq.gain.value = 0; highEq.gain.value = 0;"
            "    trim.gain.value = 1.0; dryGain.gain.value = 1.0; echoWet.gain.value = 0.0;"
            "    hpf.connect(lpf); lpf.connect(lowEq); lowEq.connect(midEq); midEq.connect(highEq);"
            "    highEq.connect(trim); trim.connect(dryGain); dryGain.connect(mixGain);"
            "    lpf.connect(delay); delay.connect(echoLpf); echoLpf.connect(fb); fb.connect(delay);"
            "    echoLpf.connect(echoWet); echoWet.connect(mixGain);"
            "   }"
            "   src.connect(hpf);"
            "   if (window.__syncmixMeter && window.__syncmixMeter.video === v && window.__syncmixMeter.analyser) {"
            "    try { src.connect(window.__syncmixMeter.analyser); } catch (e) {}"
            "   }"
            "   fx = { video: v, ctx, src, hpf, lpf, lowEq, midEq, highEq, trim, dryGain, delay, fb, echoLpf, echoWet };"
            "   window.__syncmixProFx = fx;"
            "  } catch (e) { return; }"
            " }"
            " const now = fx.ctx.currentTime;"
            " if (fx.ctx.state === 'suspended') fx.ctx.resume().catch(()=>{});"
            f" fx.lpf.frequency.setTargetAtTime({lpf_hz:.1f}, now, 0.03);"
            f" fx.hpf.frequency.setTargetAtTime({hpf_hz:.1f}, now, 0.03);"
            f" fx.lowEq.gain.setTargetAtTime({low_db:.2f}, now, 0.03);"
            f" fx.midEq.gain.setTargetAtTime({mid_db:.2f}, now, 0.03);"
            f" fx.highEq.gain.setTargetAtTime({high_db:.2f}, now, 0.03);"
            f" fx.trim.gain.setTargetAtTime({trim_gain:.3f}, now, 0.04);"
            f" fx.echoWet.gain.setTargetAtTime({echo_wet:.3f}, now, 0.03);"
            f" fx.dryGain.gain.setTargetAtTime({max(0.35, 1.0 - echo_wet * 0.65):.3f}, now, 0.03);"
            "})();"
        )
        self.decks[deck_id].page().runJavaScript(js)

    def _reset_pro_dj_fx(self, deck_id: str):
        self.decks[deck_id].page().runJavaScript(
            "(() => {"
            " const fx = window.__syncmixProFx;"
            " if (!fx || !fx.ctx) return;"
            " const now = fx.ctx.currentTime;"
            " fx.lpf.frequency.setTargetAtTime(20000.0, now, 0.02);"
            " fx.hpf.frequency.setTargetAtTime(20.0, now, 0.02);"
            " if (fx.lowEq) fx.lowEq.gain.setTargetAtTime(0.0, now, 0.02);"
            " if (fx.midEq) fx.midEq.gain.setTargetAtTime(0.0, now, 0.02);"
            " if (fx.highEq) fx.highEq.gain.setTargetAtTime(0.0, now, 0.02);"
            " if (fx.trim) fx.trim.gain.setTargetAtTime(1.0, now, 0.03);"
            " fx.echoWet.gain.setTargetAtTime(0.0, now, 0.02);"
            " fx.dryGain.gain.setTargetAtTime(1.0, now, 0.02);"
            "})();"
        )

    def _load_initial_pages(self):
        self.decks["A"].setUrl(QUrl(YTM_URL))

    def _set_deck_volume(self, deck_id: str, vol: float, sync_player: bool = False):
        vol = max(0.0, min(1.0, vol))
        if sync_player:
            pct = int(round(vol * 100))
            self.decks[deck_id].page().runJavaScript(
                f"(() => {{"
                f" const v = document.querySelector('video');"
                f" if (v) v.__syncmixTargetVol = {vol:.4f};"
                f" const mg = window.__syncmixMixGain;"
                f" const ctx = window.__syncmixAudioCtx;"
                f" if (mg && ctx) {{"
                f"  if (ctx.state === 'suspended') ctx.resume().catch(()=>{{}});"
                f"  mg.gain.setTargetAtTime({vol:.4f}, ctx.currentTime, 0.015);"
                f"  if (v && !v.__syncmixAdMuted) {{ v.muted = false; v.volume = 1.0; }}"
                f" }} else {{"
                f"  const mp = document.getElementById('movie_player');"
                f"  if (mp && mp.setVolume) mp.setVolume({pct});"
                f"  if (v) {{ v.muted = false; v.volume = {vol:.4f}; }}"
                f" }}"
                f"}})();"
            )
        else:
            self.decks[deck_id].page().runJavaScript(
                f"(() => {{"
                f" const v = document.querySelector('video');"
                f" if (v) v.__syncmixTargetVol = {vol:.4f};"
                f" const mg = window.__syncmixMixGain;"
                f" const ctx = window.__syncmixAudioCtx;"
                f" if (mg && ctx) {{"
                f"  if (ctx.state === 'suspended') ctx.resume().catch(()=>{{}});"
                f"  mg.gain.setTargetAtTime({vol:.4f}, ctx.currentTime, 0.015);"
                f"  if (v && !v.__syncmixAdMuted) {{ v.muted = false; v.volume = 1.0; }}"
                f" }} else if (v) {{"
                f"  if (v.muted) v.muted = false; v.volume = {vol:.4f};"
                f" }}"
                f"}})();"
            )

    def _update_poll_cadence(self):
        fast = self.is_fading or self.states["B"].warm_running
        target_ms = 80 if fast else 250
        if self.poll_timer.interval() != target_ms:
            self.poll_timer.setInterval(target_ms)

    def _set_deck_playback_rate(self, deck_id: str, rate: float, force: bool = False):
        rate = max(
            CUE_VOCAL_MIN_PLAYBACK_RATE,
            min(CUE_VOCAL_MAX_PLAYBACK_RATE, rate),
        )
        prev = self.last_applied_rate.get(deck_id, 1.0)
        live_rate = self.states[deck_id].playback_rate
        reset_to_one = abs(rate - 1.0) < 1e-4 and (
            abs(prev - 1.0) > 1e-4 or abs(live_rate - 1.0) > 1e-4
        )
        actual_mismatch = abs(live_rate - rate) > 0.008
        if not force and not reset_to_one and not actual_mismatch and abs(prev - rate) < 0.008:
            return
        self.last_applied_rate[deck_id] = rate
        self.decks[deck_id].page().runJavaScript(
            f"(() => {{ const v = document.querySelector('video'); if (v) {{ if ('preservesPitch' in v) v.preservesPitch = true; if ('webkitPreservesPitch' in v) v.webkitPreservesPitch = true; v.playbackRate = {rate:.4f}; }} }})();"
        )

    def _play_deck(self, deck_id: str):
        self.decks[deck_id].page().runJavaScript(
            "(() => { const mp = document.getElementById('movie_player'); if (mp && mp.playVideo) mp.playVideo(); const v = document.querySelector('video'); if (v && v.paused) v.play().catch(()=>{}); })();"
        )

    def _pause_deck(self, deck_id: str):
        self.decks[deck_id].page().runJavaScript(
            "(() => { const mp = document.getElementById('movie_player'); if (mp && mp.pauseVideo) mp.pauseVideo(); const v = document.querySelector('video'); if (v && !v.paused) v.pause(); })();"
        )

    def _seek_deck(self, deck_id: str, seconds: float):
        self.decks[deck_id].page().runJavaScript(
            f"(() => {{ const v = document.querySelector('video'); if (v) v.currentTime = {max(0.0, seconds):.2f}; }})();"
        )

    def _advance_main_queue(self):
        self.decks["A"].page().runJavaScript(
            "(() => { const btn = document.querySelector('ytmusic-player-bar .next-button'); if (btn) { btn.click(); return; } const mp = document.getElementById('movie_player'); if (mp && mp.nextVideo) mp.nextVideo(); })();"
        )

    def _set_badge(self, text: str, _bg: str, fg: str):
        self.status_badge.setText(text)
        dot_color = fg if fg else "#a1a1a6"
        self.status_dot.setStyleSheet(
            f"background-color: {dot_color}; border-radius: 3px; border: none;"
        )
        text_color = "#ededed" if self.is_fading else "#a1a1a6"
        self.status_badge.setStyleSheet(
            f"color: {text_color}; font-size: 12px; font-weight: 400; border: none; background: transparent;"
        )

    def _poll_decks(self):
        self.decks["A"].page().runJavaScript(
            INSPECT_JS,
            lambda res: self._handle_deck_info("A", res),
        )
        if self.states["B"].cued_video_id or self.is_fading:
            self.decks["B"].page().runJavaScript(
                INSPECT_JS,
                lambda res: self._handle_deck_info("B", res),
            )

    def _handle_deck_info(self, deck_id: str, res):
        if isinstance(res, str) and res:
            try:
                res = json.loads(res)
            except Exception:
                return
        if not isinstance(res, dict):
            return

        st = self.states[deck_id]
        st.has_video = bool(res.get("hasVideo", False))
        st.is_ad = bool(res.get("isAd", False))
        st.current_time = float(res.get("currentTime", 0.0) or 0.0)
        st.duration = float(res.get("duration", 0.0) or 0.0)
        st.paused = bool(res.get("paused", True))
        st.ended = bool(res.get("ended", False))
        st.volume = float(res.get("volume", 1.0) or 0.0)
        st.playback_rate = float(res.get("playbackRate", 1.0) or 1.0)
        raw_rms = res.get("audioRms", -1.0)
        st.audio_rms = float(raw_rms) if raw_rms is not None else -1.0
        st.audio_ready = bool(res.get("audioReady", False))
        st.loudness_rms = float(res.get("loudnessRms", 0.0) or 0.0)
        st.mid_energy = float(res.get("midEnergy", 0.0) or 0.0)
        st.live_bpm = float(res.get("liveBpm", 0.0) or 0.0)
        st.bpm_confidence = float(res.get("bpmConfidence", 0.0) or 0.0)
        st.live_key = str(res.get("liveKey", "") or "")
        st.key_confidence = float(res.get("keyConfidence", 0.0) or 0.0)
        raw_phase = res.get("beatPhase", -1.0)
        st.beat_phase = float(raw_phase) if raw_phase is not None else -1.0
        st.title = str(res.get("title", "") or "")
        st.artist = str(res.get("artist", "") or "")
        st.video_id = str(res.get("videoId", "") or "")
        st.next_video_id = str(res.get("nextVideoId", "") or "")
        st.next_title = str(res.get("nextTitle", "") or "")

        if deck_id == "A":
            if st.video_id and st.video_id != self._last_seen_video_id:
                if self._last_seen_video_id:
                    self.track_cache.save()
                self._last_seen_video_id = st.video_id
                self.manual_mix_pending = False
            if self.last_faded_video_id and st.video_id and st.video_id != self.last_faded_video_id:
                self.last_faded_video_id = ""

            if st.video_id and not st.paused and not st.is_ad:
                self.track_cache.update_track(
                    st.video_id,
                    bpm=st.live_bpm,
                    bpm_conf=st.bpm_confidence,
                    key=st.live_key,
                    key_conf=st.key_confidence,
                    loudness=st.loudness_rms,
                    title=st.title,
                )

            viz = res.get("vizBands")
            if st.paused or not st.audio_ready or not isinstance(viz, list):
                viz = [0.06] * 10
                viz_color = "#52525b"
            elif self.is_fading:
                viz_color = "#ff4d6d"
            else:
                viz_color = "#ededed"
            self.visualizer.set_levels(viz, viz_color)

            self._update_now_playing_bar(st)
            if self.auto_cb.isChecked() and not self.is_fading:
                self.silence_tracker.update(st, time.monotonic())
                self._preload_outgoing_tail()
                self._check_spotify_crossfade()
        else:
            self._handle_tail_deck_poll(st)

    def _update_now_playing_bar(self, st: DeckState):
        title = st.title or "Play any song or playlist on YouTube Music"
        if len(title) > 42:
            title = title[:40] + "..."
        self.title_lbl.setText(title)

        artist = f"• {st.artist}" if st.artist else ""
        if len(artist) > 35:
            artist = artist[:33] + "..."
        self.artist_lbl.setText(artist)

        cached = self.track_cache.get(st.video_id)
        disp_bpm = st.live_bpm if (st.live_bpm >= 70.0 and st.bpm_confidence >= 0.35) else float(cached.get("bpm", 0.0))
        disp_key = st.live_key if (st.live_key and st.key_confidence >= 0.25) else str(cached.get("key", ""))
        live_tags = []
        if disp_bpm >= 70.0:
            live_tags.append(f"{int(round(disp_bpm))} BPM")
        if disp_key:
            live_tags.append(disp_key)
        meta_prefix = f"{' • '.join(live_tags)}  |  " if live_tags else ""

        if st.duration > 0:
            self.time_lbl.setText(
                f"{meta_prefix}{fmt_time(st.current_time)} / {fmt_time(st.duration)} (-{fmt_time(st.remaining)})"
            )
            self.progress.setValue(int((st.current_time / st.duration) * 1000))
        else:
            self.time_lbl.setText("0:00 / 0:00")
            self.progress.setValue(0)

    def _preload_outgoing_tail(self):
        act = self.states["A"]
        tail = self.states["B"]
        if act.paused or act.is_ad or act.duration <= 10.0 or not act.video_id:
            return

        smart_fade = self._get_target_outro_window(act)
        if (
            act.current_time >= 2.0
            and act.remaining > 3.0
            and tail.cued_video_id != act.video_id
        ):
            tail.cued_video_id = act.video_id
            tail.cue_ready = False
            tail.warm_running = False
            tail.cued_outro_start = max(0.0, act.duration - smart_fade - WARMUP_LEAD_SEC)
            self._set_deck_volume("B", 0.0, sync_player=True)
            self._set_deck_playback_rate("B", 1.0, force=True)
            if self._is_pro_dj_mix() or self._is_apple_automix():
                self._set_pro_dj_fx("A", 20000.0, 20.0, 0.0)
            else:
                self._reset_pro_dj_fx("B")
            self._set_badge(self._buffering_badge(), "#272732", "#b0b0c0")
            self.decks["B"].setUrl(QUrl(f"{YTM_URL}/watch?v={act.video_id}"))

    def _handle_tail_deck_poll(self, tail: DeckState):
        if self.is_fading or not tail.cued_video_id:
            self._update_poll_cadence()
            return

        if not tail.cue_ready:
            self._set_deck_volume("B", 0.0)
            if tail.video_id == tail.cued_video_id and not tail.is_ad and tail.has_video and tail.duration > 10.0:
                ref_state = (
                    self.states["A"]
                    if self.states["A"].video_id == tail.cued_video_id and self.states["A"].duration > 10.0
                    else tail
                )
                target_window = self._get_target_outro_window(ref_state)
                outro_start = max(0.0, tail.duration - target_window - WARMUP_LEAD_SEC)
                if (
                    self.states["A"].video_id == tail.cued_video_id
                    and self.states["A"].current_time > outro_start
                ):
                    outro_start = max(0.0, self.states["A"].current_time - 0.5)
                tail.cued_outro_start = outro_start
                if not tail.paused:
                    self._pause_deck("B")
                if abs(tail.current_time - outro_start) > 3.5:
                    self._seek_deck("B", outro_start)
                elif tail.paused:
                    tail.cue_ready = True
                    if self._is_pro_dj_mix() or self._is_apple_automix():
                        self._set_pro_dj_fx("B", 20000.0, 20.0, 0.0)
                    self._set_badge(*self._armed_badge())
        else:
            act = self.states["A"]
            smart_fade = self._get_target_outro_window(act)
            if (
                act.video_id == tail.cued_video_id
                and not act.paused
                and smart_fade < act.remaining <= smart_fade + WARMUP_LEAD_SEC
            ):
                # Phase-lock hidden Deck B to Deck A at volume 0.0 before handoff
                if not tail.warm_running:
                    tail.warm_running = True
                    self._set_deck_volume("B", 0.0)
                    self._seek_deck("B", act.current_time + 0.35)
                    self._play_deck("B")
                elif tail.paused:
                    self._play_deck("B")
                else:
                    drift = tail.current_time - act.current_time
                    if abs(drift) > 0.65:
                        self._seek_deck("B", act.current_time + 0.25)
                    elif abs(drift) > 0.025:
                        sync_rate = max(0.88, min(1.12, 1.0 - drift * 1.25))
                        self._set_deck_playback_rate("B", sync_rate, force=True)
                    else:
                        self._set_deck_playback_rate("B", 1.0)
            else:
                tail.warm_running = False
                if not tail.paused:
                    self._set_deck_volume("B", 0.0)
                    self._pause_deck("B")
        self._update_poll_cadence()

    def _check_spotify_crossfade(self):
        act = self.states["A"]
        tail = self.states["B"]
        if act.paused or act.is_ad or act.duration <= 10.0 or not act.video_id:
            return
        if self.last_faded_video_id == act.video_id:
            return
        if not tail.cue_ready or tail.cued_video_id != act.video_id:
            return

        smart_fade = self._get_target_outro_window(act)
        if self.manual_mix_pending and act.remaining > 0.5:
            self.manual_mix_pending = False
            self._trigger_spotify_crossfade(act, smart_fade, trigger_reason="manual")
        elif act.remaining <= smart_fade and act.remaining > 0.2:
            self._trigger_spotify_crossfade(act, smart_fade, trigger_reason="time")
        elif (
            self.silence_cb.isChecked()
            and self.silence_tracker.is_triggered()
            and act.remaining > 0.5
        ):
            reason = self.silence_tracker.trigger_kind or "silence"
            # Outro energy-drop phrase trigger belongs specifically to Apple AutoMix
            if reason == "silence" or self._is_apple_automix():
                self._trigger_spotify_crossfade(act, smart_fade, trigger_reason=reason)

    def _mute_and_advance_main_deck(self):
        if not self.is_fading:
            return
        self._set_deck_volume("B", 1.0, sync_player=True)
        self._set_deck_volume("A", 0.0, sync_player=True)
        self.last_applied_rate["A"] = 1.0
        self._advance_main_queue()

    def _trigger_spotify_crossfade(
        self, act: DeckState, smart_fade: float, trigger_reason: str = "time"
    ):
        if self.is_fading:
            return
        self.release_timer.stop()
        self.release_step = 0
        self.manual_mix_pending = False
        cached_out = self.track_cache.get(act.video_id)
        self.out_loudness = max(
            act.loudness_rms,
            self.silence_tracker.body_avg_rms,
            float(cached_out.get("loudness", 0.0)),
        )
        self.silence_tracker.reset()
        self.track_cache.save()
        self.intro_skip_done = False
        self.intro_silence_elapsed = 0.0
        self.intro_skip_hops = 0

        self.is_fading = True
        self.incoming_started = False
        self.fade_paused_by_user = False
        self._update_poll_cadence()
        self.wait_for_incoming_elapsed = 0.0
        self.outgoing_video_id = act.video_id
        self.last_faded_video_id = act.video_id
        self.fade_elapsed = 0.0
        if trigger_reason == "silence":
            self.fade_duration = max(1.5, min(smart_fade, 4.5, act.remaining))
        elif trigger_reason == "outro_drop":
            self.fade_duration = max(4.0, min(smart_fade, act.remaining))
        else:
            self.fade_duration = max(1.0, min(smart_fade, act.remaining))

        if self._is_apple_automix():
            self.out_bpm = self._resolve_bpm(act)
            self.out_key = self._resolve_key(act)
        else:
            self.out_bpm = estimate_track_bpm(act.video_id, act.title, act.duration)
            self.out_key = estimate_track_key(act.video_id, act.title)
        self.out_genre = detect_track_genre(act.title, act.artist)
        self.incoming_bpm_resolved = bool(act.next_video_id or act.next_title)
        self.incoming_rate_synced = False
        next_vid_seed = act.next_video_id or f"{act.video_id}_next"
        if self._is_apple_automix():
            self.in_bpm, self.in_key = self._resolve_next_bpm_key(
                next_vid_seed, act.next_title or "", act.duration
            )
        else:
            self.in_bpm = estimate_track_bpm(
                next_vid_seed,
                act.next_title or "",
                act.duration,
            )
            self.in_key = estimate_track_key(next_vid_seed, act.next_title or "")
        self.in_genre = detect_track_genre(act.next_title or "", "")
        if abs(self.in_bpm - self.out_bpm) < 3.0 and not self._is_apple_automix():
            delta = 6.0 if self.out_bpm <= 128.0 else -6.0
            self.in_bpm = max(92.0, min(144.0, self.out_bpm + delta))
        if self._is_apple_automix():
            _, self.pro_key_score = compute_apple_automix_overlap(
                self.out_bpm, self.in_bpm, self.out_key, self.in_key, act.duration
            )
            self.pro_eq_strength = 1.0
        else:
            _, self.pro_key_score, self.pro_eq_strength = calculate_pro_dj_overlap(
                self.out_bpm, self.in_bpm, self.out_key, self.in_key, self.out_genre, self.in_genre, act.duration
            )
        self.release_start_rate = 1.0

        tail = self.states["B"]
        self._set_deck_playback_rate("B", 1.0, force=True)
        already_warm = (
            tail.warm_running
            and not tail.paused
            and abs(tail.current_time - act.current_time) <= 0.45
        )
        if not already_warm:
            self._seek_deck("B", act.current_time)
            self._play_deck("B")
        self._set_deck_volume("B", 0.85, sync_player=True)
        self._set_deck_volume("A", 0.50)
        if self._is_standard_crossfade():
            self._set_deck_playback_rate("A", 1.0, force=True)
            self._reset_pro_dj_fx("A")
            self._reset_pro_dj_fx("B")
        elif self._is_pro_dj_mix():
            self._set_pro_dj_fx("B", 12000.0, 20.0, 0.0)
        elif self._is_apple_automix():
            self._set_pro_dj_fx("B", 20000.0, 20.0, 0.0)

        # Brief 25ms balanced micro-handoff before Deck A mutes and advances
        QTimer.singleShot(25, self._mute_and_advance_main_deck)

        if self._is_apple_automix():
            if trigger_reason == "silence":
                badge = f"Apple AutoMix (Silence, {self.fade_duration:.1f}s)"
            elif trigger_reason == "outro_drop":
                badge = f"Apple AutoMix (Outro Drop, {self.fade_duration:.1f}s)"
            elif is_large_bpm_gap(self.out_bpm, self.in_bpm):
                badge = f"Apple AutoMix (Echo Fade, {self.fade_duration:.1f}s)"
            else:
                key_tag = "Match" if self.pro_key_score >= 0.8 else ("Clash" if self.pro_key_score < 0 else "Blend")
                badge = f"Apple AutoMix ({self.out_key}->{self.in_key} {key_tag}, {self.fade_duration:.1f}s)"
            self._active_fade_badge = (badge, "#4a1525", "#ff5e7e")
        elif trigger_reason == "silence":
            badge = f"Silence Mix ({self.fade_duration:.1f}s)"
            self._active_fade_badge = (badge, "#0f3a4a", "#38bdf8")
        elif self._is_github_automix():
            badge = f"AutoMix 32-Beat ({self.fade_duration:.1f}s)"
            self._active_fade_badge = (badge, "#4a3210", "#ffb84d")
        elif self._is_pro_dj_mix():
            key_tag = "Match" if self.pro_key_score >= 0.8 else ("Clash" if self.pro_key_score < 0 else "Neutral")
            badge = f"Pro DJ ({self.out_key}->{self.in_key} {key_tag}, {self.fade_duration:.1f}s)"
            self._active_fade_badge = (badge, "#4a3210", "#ffb84d")
        else:
            badge = f"Crossfading ({self.fade_duration:.1f}s)"
            self._active_fade_badge = (badge, "#4a3210", "#ffb84d")
        self._set_badge(*self._active_fade_badge)
        self.fade_timer.start(50)

    def _step_fade(self):
        act = self.states["A"]
        tail = self.states["B"]
        tail_rem = tail.remaining
        if (
            tail.ended
            or (0.0 < tail_rem <= 0.35)
            or (tail.video_id and tail.video_id != self.outgoing_video_id)
        ):
            self._finish_fade()
            return

        new_track_loaded = bool(act.video_id and act.video_id != self.outgoing_video_id)
        if (
            new_track_loaded
            and (self._is_apple_automix() or self._is_github_automix() or self._is_pro_dj_mix())
            and (
                (not self.incoming_bpm_resolved and (act.title or act.duration > 10.0))
                or (self._is_apple_automix() and act.live_bpm >= 70.0 and act.bpm_confidence >= 0.45)
            )
        ):
            self.incoming_bpm_resolved = True
            if self._is_apple_automix():
                self.in_bpm = self._resolve_bpm(act)
                self.in_key = self._resolve_key(act)
                _, self.pro_key_score = compute_apple_automix_overlap(
                    self.out_bpm, self.in_bpm, self.out_key, self.in_key, act.duration
                )
                self.pro_eq_strength = 1.0
            else:
                self.in_bpm = estimate_track_bpm(act.video_id, act.title, act.duration)
                self.in_key = estimate_track_key(act.video_id, act.title)
                self.in_genre = detect_track_genre(act.title, act.artist)
                if abs(self.in_bpm - self.out_bpm) < 3.0:
                    delta = 6.0 if self.out_bpm <= 128.0 else -6.0
                    self.in_bpm = max(92.0, min(144.0, self.out_bpm + delta))
                _, self.pro_key_score, self.pro_eq_strength = calculate_pro_dj_overlap(
                    self.out_bpm, self.in_bpm, self.out_key, self.in_key, self.out_genre, self.in_genre, act.duration
                )

        # Smart Incoming Intro-Skip (Apple AutoMix): instant cached cue OR live silence hop
        intro_skipping = False
        if (
            self._is_apple_automix()
            and new_track_loaded
            and not act.paused
            and not act.is_ad
            and not self.intro_skip_done
            and self.wait_for_incoming_elapsed < 2.2
        ):
            cached_in = self.track_cache.get(act.video_id)
            cached_intro = float(cached_in.get("intro_sec", 0.0) or 0.0)
            if cached_intro > 0.5 and act.current_time + 0.35 < cached_intro:
                self._seek_deck("A", min(INTRO_SKIP_MAX_SEC, cached_intro))
                self.intro_skip_done = True
            elif act.audio_ready and act.audio_rms >= AUDIBLE_CONFIRM_RMS:
                self.intro_skip_done = True
                if self.intro_skip_hops > 0 and act.current_time > 0.5:
                    self.track_cache.update_track(act.video_id, intro_sec=act.current_time)
            elif (
                act.audio_ready
                and 0.0 <= act.audio_rms < SILENCE_RMS_THRESHOLD
                and act.current_time < INTRO_SKIP_MAX_SEC
                and self.intro_skip_hops < 3
            ):
                intro_skipping = True
                self.intro_silence_elapsed += 0.05
                if self.intro_silence_elapsed >= 0.25:
                    self.intro_silence_elapsed = 0.0
                    self.intro_skip_hops += 1
                    next_pos = min(INTRO_SKIP_MAX_SEC, act.current_time + INTRO_SKIP_STEP_SEC)
                    self._seek_deck("A", next_pos)

        incoming_ready = bool(
            new_track_loaded and not act.paused and act.current_time >= 0.25 and not intro_skipping
        )
        if incoming_ready:
            self.incoming_started = True

        # Respect user Pause during an active mix: pause Deck B too & freeze fade progress
        if self.incoming_started and act.paused:
            if not self.fade_paused_by_user:
                self.fade_paused_by_user = True
                self._pause_deck("B")
                self._set_badge("Mix paused", "#141415", "#85858b")
            elif not tail.paused:
                self._pause_deck("B")
            return
        if self.fade_paused_by_user and not act.paused:
            self.fade_paused_by_user = False
            self._play_deck("B")
            self._set_badge(*self._active_fade_badge)

        if not incoming_ready and not self.incoming_started and self.wait_for_incoming_elapsed < 2.2:
            self.wait_for_incoming_elapsed += 0.05
            self._set_deck_volume("B", 1.0)
            if self.wait_for_incoming_elapsed >= 0.05:
                self._set_deck_volume("A", 0.0)
            if new_track_loaded and not act.is_ad:
                if self._is_apple_automix():
                    am0 = compute_apple_automix_step(self.out_bpm, self.in_bpm, 0.0)
                    self._set_deck_playback_rate(
                        "A", am0["in_rate"], force=not self.incoming_rate_synced
                    )
                    self._set_pro_dj_fx("A", 20000.0, 20.0, 0.0, low_db=-18.0)
                    self.incoming_rate_synced = True
                elif self._is_github_automix():
                    _, in_rate_0 = compute_automix_playback_rates(
                        self.out_bpm, self.in_bpm, 0.0
                    )
                    self._set_deck_playback_rate(
                        "A", in_rate_0, force=not self.incoming_rate_synced
                    )
                    self.incoming_rate_synced = True
                elif self._is_pro_dj_mix():
                    pro0 = compute_pro_dj_step(
                        self.out_bpm, self.in_bpm, self.pro_eq_strength, 0.0
                    )
                    self._set_deck_playback_rate(
                        "A", pro0["in_rate"], force=not self.incoming_rate_synced
                    )
                    self._set_pro_dj_fx("A", 20000.0, pro0["in_hpf_hz"], 0.0)
                    self.incoming_rate_synced = True
            if (
                new_track_loaded
                and act.paused
                and int(round(self.wait_for_incoming_elapsed / 0.05)) % 4 == 0
            ):
                self._play_deck("A")
            return

        if self.fade_elapsed == 0.0 and tail_rem > 0.5:
            self.fade_duration = max(1.5, min(self.fade_duration, tail_rem - 0.4))

        self.fade_elapsed += 0.05
        t = min(1.0, self.fade_elapsed / self.fade_duration)

        if self._is_apple_automix():
            am = compute_apple_automix_step(
                self.out_bpm, self.in_bpm, t, tail.beat_phase, act.beat_phase
            )
            out_gain, in_gain = am["out_gain"], am["in_gain"]
            eq = compute_3band_vocal_eq(t, 1.0, act.mid_energy, tail.mid_energy)
            cached_in_loud = float(self.track_cache.get(act.video_id).get("loudness", 0.0) or 0.0)
            in_loud = act.loudness_rms if act.loudness_rms >= 0.015 else cached_in_loud
            raw_trim = compute_loudness_trim(self.out_loudness, in_loud)
            trim_release = max(0.0, (t - 0.75) / 0.25)
            in_trim = raw_trim * (1.0 - trim_release) + 1.0 * trim_release

            self._set_deck_playback_rate("B", am["out_rate"])
            self._set_pro_dj_fx(
                "B", am["out_lpf_hz"], 20.0, am["echo_wet"],
                low_db=eq["out_low_db"], mid_db=eq["out_mid_db"], high_db=eq["out_high_db"],
            )
            if incoming_ready and not act.is_ad:
                force_sync = not self.incoming_rate_synced
                self._set_deck_playback_rate("A", am["in_rate"], force=force_sync)
                self._set_pro_dj_fx(
                    "A", 20000.0, 20.0, 0.0,
                    low_db=eq["in_low_db"], mid_db=eq["in_mid_db"], high_db=eq["in_high_db"],
                    trim_gain=in_trim,
                )
                self.incoming_rate_synced = True
        elif self._is_github_automix():
            out_gain, in_gain = compute_vertex_automix_gains(t)
            out_rate, in_rate = compute_automix_playback_rates(
                self.out_bpm, self.in_bpm, t
            )
            self._set_deck_playback_rate("B", out_rate)
            if incoming_ready and not act.is_ad:
                force_sync = not self.incoming_rate_synced
                self._set_deck_playback_rate("A", in_rate, force=force_sync)
                self.incoming_rate_synced = True
            self.release_start_rate = in_rate
        elif self._is_pro_dj_mix():
            pro = compute_pro_dj_step(
                self.out_bpm, self.in_bpm, self.pro_eq_strength, t
            )
            out_gain, in_gain = pro["out_gain"], pro["in_gain"]
            self._set_deck_playback_rate("B", pro["out_rate"])
            self._set_pro_dj_fx("B", pro["out_lpf_hz"], 20.0, pro["echo_wet"])
            if incoming_ready and not act.is_ad:
                force_sync = not self.incoming_rate_synced
                self._set_deck_playback_rate("A", pro["in_rate"], force=force_sync)
                self._set_pro_dj_fx("A", 20000.0, pro["in_hpf_hz"], 0.0)
                self.incoming_rate_synced = True
        else:
            out_gain, in_gain = compute_fade_gains(t, self.curve_combo.currentText())
            if abs(act.playback_rate - 1.0) > 0.01:
                self._set_deck_playback_rate("A", 1.0, force=True)
            if abs(tail.playback_rate - 1.0) > 0.01:
                self._set_deck_playback_rate("B", 1.0, force=True)

        if (
            tail.paused
            and not self.fade_paused_by_user
            and t < 0.85
            and int(round(self.fade_elapsed / 0.05)) % 8 == 0
        ):
            self._play_deck("B")

        if (
            not self.incoming_started
            and act.video_id
            and act.video_id != self.outgoing_video_id
            and act.paused
            and t < 0.85
            and int(round(self.fade_elapsed / 0.05)) % 8 == 0
        ):
            self._play_deck("A")

        self._set_deck_volume("B", out_gain)
        if not act.is_ad:
            self._set_deck_volume("A", in_gain)

        if t >= 1.0 or tail.ended or (0.0 < tail_rem <= 0.35):
            self._finish_fade()

    def _finish_fade(self):
        self.fade_timer.stop()
        if not self.states["A"].is_ad:
            self._set_deck_volume("A", 1.0, sync_player=True)
        self._set_deck_volume("B", 0.0, sync_player=True)
        self._set_deck_playback_rate("B", 1.0, force=True)
        self._reset_pro_dj_fx("A")
        self._reset_pro_dj_fx("B")
        self._pause_deck("B")

        tail = self.states["B"]
        tail.cued_video_id = ""
        tail.cue_ready = False
        tail.cued_outro_start = 0.0
        tail.warm_running = False

        self.is_fading = False
        self.incoming_started = False
        self.fade_paused_by_user = False
        self.silence_tracker.reset()
        self._update_poll_cadence()
        if self._is_github_automix() and abs(self.release_start_rate - 1.0) > 0.005:
            interval_ms = int(round((TEMPO_RELEASE_SECONDS / RELEASE_CONTROL_POINTS) * 1000))
            self.release_step = 0
            self._set_badge(
                f"Tempo Release ({self.release_start_rate:.2f}x → 1.00x)",
                "#1c2f4a",
                "#66b2ff",
            )
            self.release_timer.start(interval_ms)
        else:
            self._set_deck_playback_rate("A", 1.0, force=True)
            self._set_badge("Ad-block active", "#141415", "#4cc38a")

    def _step_tempo_release(self):
        self.release_step += 1
        r_t = min(1.0, self.release_step / float(RELEASE_CONTROL_POINTS))
        s = 1.0 - (1.0 - r_t) ** 2
        rate = self.release_start_rate + (1.0 - self.release_start_rate) * s
        if not self.states["A"].is_ad:
            self._set_deck_playback_rate("A", rate)

        if self.release_step >= RELEASE_CONTROL_POINTS:
            self.release_timer.stop()
            self._set_deck_playback_rate("A", 1.0, force=True)
            if not self.states["B"].cued_video_id:
                self._set_badge("Ad-Block ON", "#123d28", "#3be38e")
            elif self.states["B"].cue_ready:
                self._set_badge("AutoMix Armed (32-Beat)", "#1c2f4a", "#66b2ff")
        elif not self.states["B"].cued_video_id:
            self._set_badge(f"Tempo Release ({rate:.2f}x → 1.00x)", "#1c2f4a", "#66b2ff")


def _load_app_icon() -> QIcon:
    """Load the SyncMix app icon from bundled or local assets."""
    icon = QIcon()
    for rel in (os.path.join("assets", "icon.ico"), os.path.join("assets", "icon.png")):
        path = _resource_path(rel)
        if os.path.isfile(path):
            icon.addFile(path)
    return icon


def main():
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "SyncMix.Crossfader.App"
            )
        except Exception:
            pass

    app = QApplication(sys.argv)
    app_icon = _load_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    win = CrossfaderWindow()
    if not app_icon.isNull():
        win.setWindowIcon(app_icon)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
