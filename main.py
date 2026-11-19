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
from PySide6.QtGui import QFont, QIcon
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
    QProgressBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

def _resource_path(rel_path: str) -> str:
    """Resolve asset paths for both source execution and PyInstaller bundles."""
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, rel_path)


def _get_profile_dir() -> str:
    """Store QtWebEngine browser profile in LocalAppData when running frozen."""
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(local_app_data, "SyncMix Crossfader", "profile_data")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile_data")


YTM_URL = "https://music.youtube.com"
PROFILE_DIR = _get_profile_dir()

MIX_MODE_STANDARD = "Standard Crossfade (Spotify)"
MIX_MODE_AUTOMIX = "GitHub AutoMix (VertexSelection)"
MIX_MODE_PRO_DJ = "Pro DJ Mix (AI-DJ-Mixing-System)"
WARMUP_LEAD_SEC = 6.0

# Real-time Web Audio silence detection (detect silent outro / quiet part and mix early)
SILENCE_RMS_THRESHOLD = 0.008      # ~ -42 dBFS: audio below this counts as silent
SILENCE_RESUME_THRESHOLD = 0.014   # ~ -37 dBFS: hysteresis to reset silence timer
AUDIBLE_CONFIRM_RMS = 0.015        # ~ -36.5 dBFS: require real audio before allowing silence trigger
SILENCE_HOLD_SEC = 1.6             # Continuous silence duration required before triggering mix
SILENCE_MIN_ELAPSED_SEC = 20.0     # Never trigger during track intros / first 20s
SILENCE_MIN_PROGRESS = 0.55        # Allow silence mix once past 55% of track OR within last 45s
SILENCE_OUTRO_WINDOW_SEC = 45.0

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
    if (video && !video.paused && video.readyState >= 2) {
        try {
            let meter = window.__syncmixMeter;
            if (!meter || meter.video !== video) {
                const Ctx = window.AudioContext || window.webkitAudioContext;
                const fx = window.__syncmixProFx;
                const ctx = (fx && fx.ctx) || window.__syncmixAudioCtx || new Ctx();
                window.__syncmixAudioCtx = ctx;
                const src = video.__syncmixMediaSrc || ctx.createMediaElementSource(video);
                video.__syncmixMediaSrc = src;
                const analyser = ctx.createAnalyser();
                analyser.fftSize = 1024;
                src.connect(analyser);
                if (!fx || fx.video !== video) {
                    if (!video.__syncmixDirectConnected) {
                        src.connect(ctx.destination);
                        video.__syncmixDirectConnected = true;
                    }
                }
                meter = {
                    video,
                    ctx,
                    src,
                    analyser,
                    buf: new Float32Array(analyser.fftSize)
                };
                window.__syncmixMeter = meter;
            }
            if (meter.ctx.state === 'suspended') {
                meter.ctx.resume().catch(() => {});
            }
            if (meter.ctx.state === 'running') {
                meter.analyser.getFloatTimeDomainData(meter.buf);
                let sumSq = 0;
                for (let i = 0; i < meter.buf.length; i++) {
                    const s = meter.buf[i];
                    sumSq += s * s;
                }
                audioRms = Math.sqrt(sumSq / meter.buf.length);
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
    """Tracks live audio RMS on the active deck to detect silent sections safely."""

    video_id: str = ""
    heard_audio: bool = False
    silence_elapsed: float = 0.0
    last_track_time: float = 0.0
    last_poll_ts: float = 0.0

    def reset(self, video_id: str = "") -> None:
        self.video_id = video_id
        self.heard_audio = False
        self.silence_elapsed = 0.0
        self.last_track_time = 0.0
        self.last_poll_ts = 0.0

    def update(self, st: DeckState, now_ts: float) -> bool:
        if not st.video_id or st.video_id != self.video_id:
            self.reset(st.video_id)
            self.last_poll_ts = now_ts
            self.last_track_time = st.current_time
            return False

        # Reset silence timer if user scrubbed/seeked backward significantly
        if st.current_time + 1.5 < self.last_track_time:
            self.silence_elapsed = 0.0

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
            return False

        if st.audio_rms >= AUDIBLE_CONFIRM_RMS:
            self.heard_audio = True

        if not self.heard_audio:
            self.silence_elapsed = 0.0
            return False

        in_mixable_zone = (
            st.current_time >= SILENCE_MIN_ELAPSED_SEC
            and (
                st.current_time >= st.duration * SILENCE_MIN_PROGRESS
                or st.remaining <= SILENCE_OUTRO_WINDOW_SEC
            )
        )
        if not in_mixable_zone:
            self.silence_elapsed = 0.0
            return False

        if st.audio_rms < SILENCE_RMS_THRESHOLD:
            self.silence_elapsed += dt
        elif st.audio_rms >= SILENCE_RESUME_THRESHOLD:
            self.silence_elapsed = 0.0

        return self.silence_elapsed >= SILENCE_HOLD_SEC

    def is_triggered(self) -> bool:
        return self.heard_audio and self.silence_elapsed >= SILENCE_HOLD_SEC


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
        self.setWindowTitle("SyncMix — YouTube Music Crossfade Player")
        self.resize(1280, 860)
        self.setStyleSheet("background-color: #0e0e13; color: #eaeaf0;")

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
        self.release_start_rate = 1.0
        self.release_step = 0
        self.last_applied_rate = {"A": 1.0, "B": 1.0}
        self.silence_tracker = SilenceTracker()

        self._build_ui()
        self._load_initial_pages()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_decks)
        self.poll_timer.start(250)

        self.fade_timer = QTimer(self)
        self.fade_timer.timeout.connect(self._step_fade)

        self.release_timer = QTimer(self)
        self.release_timer.timeout.connect(self._step_tempo_release)

    def _create_deck_view(self) -> QWebEngineView:
        view = QWebEngineView(self)
        page = SilentWebPage(self.profile, view)
        view.setPage(page)
        return view

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # --- Spotify-Style Top Crossfade / AutoMix Bar ---
        top_bar = QFrame()
        top_bar.setFixedHeight(74)
        top_bar.setStyleSheet(
            "background-color: #16161c; border: 1px solid #2c2c36; border-radius: 10px;"
        )
        bar_layout = QHBoxLayout(top_bar)
        bar_layout.setContentsMargins(14, 8, 14, 8)
        bar_layout.setSpacing(10)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)

        title_row = QHBoxLayout()
        self.title_lbl = QLabel("Play any song or playlist on YouTube Music")
        self.title_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        self.title_lbl.setStyleSheet("color: #f4f4f8; border: none;")

        self.artist_lbl = QLabel("")
        self.artist_lbl.setStyleSheet("color: #8e8ea0; font-size: 11px; border: none;")

        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setStyleSheet("color: #b0b0c0; font-size: 11px; border: none;")

        title_row.addWidget(self.title_lbl)
        title_row.addWidget(self.artist_lbl)
        title_row.addStretch()
        title_row.addWidget(self.time_lbl)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setStyleSheet(
            """
            QProgressBar {
                background-color: #262632;
                border-radius: 2px;
                border: none;
            }
            QProgressBar::chunk {
                background-color: #1db954;
                border-radius: 2px;
            }
            """
        )

        info_col.addLayout(title_row)
        info_col.addWidget(self.progress)

        self.auto_cb = QCheckBox("Smart AutoMix")
        self.auto_cb.setChecked(True)
        self.auto_cb.setStyleSheet("font-weight: bold; color: #1db954; border: none;")

        self.silence_cb = QCheckBox("Silence Mix")
        self.silence_cb.setChecked(True)
        self.silence_cb.setToolTip(
            "Detect silent parts / quiet outros in real time and start mixing automatically from that point"
        )
        self.silence_cb.setStyleSheet("font-weight: bold; color: #38bdf8; border: none;")

        mode_lbl = QLabel("Mix Mode:")
        mode_lbl.setStyleSheet("color: #b0b0c0; border: none;")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([MIX_MODE_STANDARD, MIX_MODE_AUTOMIX, MIX_MODE_PRO_DJ])
        self.mode_combo.setStyleSheet(
            "background-color: #22222a; border: 1px solid #383844; border-radius: 4px; padding: 3px 8px;"
        )
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)

        self.dur_lbl = QLabel("Crossfade:")
        self.dur_lbl.setStyleSheet("color: #b0b0c0; border: none;")
        self.dur_spin = QSpinBox()
        self.dur_spin.setRange(1, 15)
        self.dur_spin.setValue(6)
        self.dur_spin.setSuffix("s")
        self.dur_spin.setStyleSheet(
            "background-color: #22222a; border: 1px solid #383844; border-radius: 4px; padding: 3px 6px;"
        )

        self.curve_combo = QComboBox()
        self.curve_combo.addItems(["Equal Power", "Smooth S-Curve", "Linear"])
        self.curve_combo.setToolTip("Select volume fade curve for Standard Crossfade")
        self.curve_combo.setStyleSheet(
            "background-color: #22222a; border: 1px solid #383844; border-radius: 4px; padding: 3px 8px;"
        )

        self.status_badge = QLabel("Ad-Block ON")
        self.status_badge.setStyleSheet(
            "background-color: #123d28; color: #3be38e; padding: 4px 10px; border-radius: 8px; font-size: 11px; font-weight: bold; border: none;"
        )

        bar_layout.addLayout(info_col, 3)
        bar_layout.addWidget(self.auto_cb)
        bar_layout.addWidget(self.silence_cb)
        bar_layout.addWidget(mode_lbl)
        bar_layout.addWidget(self.mode_combo)
        bar_layout.addWidget(self.dur_lbl)
        bar_layout.addWidget(self.dur_spin)
        bar_layout.addWidget(self.curve_combo)
        bar_layout.addWidget(self.status_badge)

        root.addWidget(top_bar)

        # --- Single Visible YouTube Music Deck + Hidden Outgoing-Tail Buffer ---
        self.decks["A"] = self._create_deck_view()
        self.decks["B"] = self._create_deck_view()
        self.decks["B"].hide()

        root.addWidget(self.decks["A"], 1)

    def _is_standard_crossfade(self) -> bool:
        return self.mode_combo.currentText().startswith("Standard Crossfade")

    def _is_github_automix(self) -> bool:
        return self.mode_combo.currentText().startswith("GitHub AutoMix")

    def _is_pro_dj_mix(self) -> bool:
        return self.mode_combo.currentText().startswith("Pro DJ Mix")

    def _armed_badge(self) -> tuple[str, str, str]:
        if self._is_github_automix():
            return ("AutoMix Armed (32-Beat)", "#1c2f4a", "#66b2ff")
        if self._is_pro_dj_mix():
            return ("Pro DJ Armed (Camelot+EQ)", "#251b45", "#b98eff")
        return ("Crossfade Armed", "#1c2f4a", "#66b2ff")

    def _buffering_badge(self) -> str:
        if self._is_github_automix():
            return "Buffering AutoMix..."
        if self._is_pro_dj_mix():
            return "Buffering Pro DJ..."
        return "Buffering Crossfade..."

    def _on_mode_changed(self, _text: str):
        is_standard = self._is_standard_crossfade()
        self.dur_lbl.setVisible(is_standard)
        self.dur_spin.setVisible(is_standard)
        self.curve_combo.setVisible(is_standard)
        if not is_standard:
            if not self.auto_cb.isChecked():
                self.auto_cb.setChecked(True)
            if self._is_pro_dj_mix():
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
                elif self._is_pro_dj_mix():
                    self._set_badge("Pro DJ Mix Ready", "#251b45", "#b98eff")
                else:
                    self._set_badge("GitHub AutoMix Ready", "#1c2f4a", "#66b2ff")
        else:
            self.release_timer.stop()
            self.release_step = 0
            self._set_deck_playback_rate("A", 1.0, force=True)
            self._set_deck_playback_rate("B", 1.0, force=True)
            self._reset_pro_dj_fx("A")
            self._reset_pro_dj_fx("B")
            if not self.is_fading:
                if self.states["B"].cue_ready:
                    self._set_badge("Crossfade Armed", "#1c2f4a", "#66b2ff")
                else:
                    self._set_badge("Ad-Block ON", "#123d28", "#3be38e")

    def _get_target_outro_window(self, st: DeckState) -> float:
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

    def _set_pro_dj_fx(self, deck_id: str, lpf_hz: float, hpf_hz: float, echo_wet: float):
        lpf_hz = max(400.0, min(20000.0, lpf_hz))
        hpf_hz = max(20.0, min(1000.0, hpf_hz))
        echo_wet = max(0.0, min(0.75, echo_wet))
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
            "   const hpf = (fx && fx.hpf) || ctx.createBiquadFilter(); hpf.type = 'highpass';"
            "   const lpf = (fx && fx.lpf) || ctx.createBiquadFilter(); lpf.type = 'lowpass';"
            "   const dryGain = (fx && fx.dryGain) || ctx.createGain();"
            "   const delay = (fx && fx.delay) || ctx.createDelay(2.0); delay.delayTime.value = " + f"{PRO_DJ_ECHO_DELAY_SEC:.3f}" + ";"
            "   const fb = (fx && fx.fb) || ctx.createGain(); fb.gain.value = " + f"{PRO_DJ_ECHO_DECAY:.2f}" + ";"
            "   const echoLpf = (fx && fx.echoLpf) || ctx.createBiquadFilter(); echoLpf.type = 'lowpass'; echoLpf.frequency.value = 3200;"
            "   const echoWet = (fx && fx.echoWet) || ctx.createGain();"
            "   if (!fx) {"
            "    hpf.frequency.value = 20; lpf.frequency.value = 20000; dryGain.gain.value = 1.0; echoWet.gain.value = 0.0;"
            "    hpf.connect(lpf); lpf.connect(dryGain); dryGain.connect(ctx.destination);"
            "    lpf.connect(delay); delay.connect(echoLpf); echoLpf.connect(fb); fb.connect(delay);"
            "    echoLpf.connect(echoWet); echoWet.connect(ctx.destination);"
            "   }"
            "   src.connect(hpf);"
            "   if (window.__syncmixMeter && window.__syncmixMeter.video === v && window.__syncmixMeter.analyser) {"
            "    try { src.connect(window.__syncmixMeter.analyser); } catch (e) {}"
            "   }"
            "   fx = { video: v, ctx, src, hpf, lpf, dryGain, delay, fb, echoLpf, echoWet };"
            "   window.__syncmixProFx = fx;"
            "  } catch (e) { return; }"
            " }"
            " const now = fx.ctx.currentTime;"
            " if (fx.ctx.state === 'suspended') fx.ctx.resume().catch(()=>{});"
            f" fx.lpf.frequency.setTargetAtTime({lpf_hz:.1f}, now, 0.03);"
            f" fx.hpf.frequency.setTargetAtTime({hpf_hz:.1f}, now, 0.03);"
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
                f"(() => {{ const mp = document.getElementById('movie_player'); if (mp && mp.setVolume) mp.setVolume({pct}); const v = document.querySelector('video'); if (v) {{ v.muted = false; v.volume = {vol:.4f}; }} }})();"
            )
        else:
            self.decks[deck_id].page().runJavaScript(
                f"(() => {{ const v = document.querySelector('video'); if (v) {{ if (v.muted) v.muted = false; v.volume = {vol:.4f}; }} }})();"
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

    def _set_badge(self, text: str, bg: str, fg: str):
        self.status_badge.setText(text)
        self.status_badge.setStyleSheet(
            f"background-color: {bg}; color: {fg}; padding: 4px 10px; border-radius: 8px; font-size: 11px; font-weight: bold; border: none;"
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
        st.title = str(res.get("title", "") or "")
        st.artist = str(res.get("artist", "") or "")
        st.video_id = str(res.get("videoId", "") or "")
        st.next_video_id = str(res.get("nextVideoId", "") or "")
        st.next_title = str(res.get("nextTitle", "") or "")

        if deck_id == "A":
            if self.last_faded_video_id and st.video_id and st.video_id != self.last_faded_video_id:
                self.last_faded_video_id = ""
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

        if st.duration > 0:
            self.time_lbl.setText(
                f"{fmt_time(st.current_time)} / {fmt_time(st.duration)} (-{fmt_time(st.remaining)})"
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
            if self._is_pro_dj_mix():
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
                    if self._is_pro_dj_mix():
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
        if act.remaining <= smart_fade and act.remaining > 0.2:
            self._trigger_spotify_crossfade(act, smart_fade, trigger_reason="time")
        elif (
            self.silence_cb.isChecked()
            and self.silence_tracker.is_triggered()
            and act.remaining > 0.5
        ):
            self._trigger_spotify_crossfade(act, smart_fade, trigger_reason="silence")

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
        self.silence_tracker.reset()

        self.is_fading = True
        self._update_poll_cadence()
        self.wait_for_incoming_elapsed = 0.0
        self.outgoing_video_id = act.video_id
        self.last_faded_video_id = act.video_id
        self.fade_elapsed = 0.0
        if trigger_reason == "silence":
            self.fade_duration = max(1.5, min(smart_fade, 4.5, act.remaining))
        else:
            self.fade_duration = max(1.0, min(smart_fade, act.remaining))

        self.out_bpm = estimate_track_bpm(act.video_id, act.title, act.duration)
        self.out_key = estimate_track_key(act.video_id, act.title)
        self.out_genre = detect_track_genre(act.title, act.artist)
        self.incoming_bpm_resolved = bool(act.next_video_id or act.next_title)
        self.incoming_rate_synced = False
        next_vid_seed = act.next_video_id or f"{act.video_id}_next"
        self.in_bpm = estimate_track_bpm(
            next_vid_seed,
            act.next_title or "",
            act.duration,
        )
        self.in_key = estimate_track_key(next_vid_seed, act.next_title or "")
        self.in_genre = detect_track_genre(act.next_title or "", "")
        if abs(self.in_bpm - self.out_bpm) < 3.0:
            delta = 6.0 if self.out_bpm <= 128.0 else -6.0
            self.in_bpm = max(92.0, min(144.0, self.out_bpm + delta))
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

        # Brief 25ms balanced micro-handoff before Deck A mutes and advances
        QTimer.singleShot(25, self._mute_and_advance_main_deck)

        if trigger_reason == "silence":
            badge = f"Silence Mix ({self.fade_duration:.1f}s)"
            self._set_badge(badge, "#0f3a4a", "#38bdf8")
        elif self._is_github_automix():
            badge = f"AutoMix 32-Beat ({self.fade_duration:.1f}s)"
            self._set_badge(badge, "#4a3210", "#ffb84d")
        elif self._is_pro_dj_mix():
            key_tag = "Match" if self.pro_key_score >= 0.8 else ("Clash" if self.pro_key_score < 0 else "Neutral")
            badge = f"Pro DJ ({self.out_key}->{self.in_key} {key_tag}, {self.fade_duration:.1f}s)"
            self._set_badge(badge, "#4a3210", "#ffb84d")
        else:
            badge = f"Crossfading ({self.fade_duration:.1f}s)"
            self._set_badge(badge, "#4a3210", "#ffb84d")
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
            and (self._is_github_automix() or self._is_pro_dj_mix())
            and not self.incoming_bpm_resolved
            and (act.title or act.duration > 10.0)
        ):
            self.incoming_bpm_resolved = True
            self.in_bpm = estimate_track_bpm(act.video_id, act.title, act.duration)
            self.in_key = estimate_track_key(act.video_id, act.title)
            self.in_genre = detect_track_genre(act.title, act.artist)
            if abs(self.in_bpm - self.out_bpm) < 3.0:
                delta = 6.0 if self.out_bpm <= 128.0 else -6.0
                self.in_bpm = max(92.0, min(144.0, self.out_bpm + delta))
            _, self.pro_key_score, self.pro_eq_strength = calculate_pro_dj_overlap(
                self.out_bpm, self.in_bpm, self.out_key, self.in_key, self.out_genre, self.in_genre, act.duration
            )

        incoming_ready = bool(
            new_track_loaded and not act.paused and act.current_time >= 0.25
        )
        if not incoming_ready and self.wait_for_incoming_elapsed < 1.8:
            self.wait_for_incoming_elapsed += 0.05
            self._set_deck_volume("B", 1.0)
            if self.wait_for_incoming_elapsed >= 0.05:
                self._set_deck_volume("A", 0.0)
            if new_track_loaded and not act.is_ad:
                if self._is_github_automix():
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
        if self._is_github_automix():
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
            and t < 0.85
            and int(round(self.fade_elapsed / 0.05)) % 8 == 0
        ):
            self._play_deck("B")

        if (
            act.video_id
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
            self._set_badge("Ad-Block ON", "#123d28", "#3be38e")

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
