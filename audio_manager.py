import collections
import os
import sys
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

# Opus codec — high-quality, low-latency voice compression
try:
    # opuslib uses ctypes.util.find_library which often fails on macOS/Homebrew
    # and Windows (DLL not on PATH). Pre-load from known paths.
    import ctypes
    import ctypes.util
    _app_dir = os.path.dirname(os.path.abspath(__file__))
    if ctypes.util.find_library('opus') is None:
        _search_paths = []
        if sys.platform == 'win32':
            # Windows: check app directory first (bundled DLL), then common locations
            _search_paths = [
                os.path.join(_app_dir, 'opus.dll'),
                os.path.join(_app_dir, 'libopus-0.dll'),
                os.path.join(_app_dir, 'libopus.dll'),
                os.path.join(_app_dir, 'libs', 'opus.dll'),
                os.path.join(_app_dir, 'libs', 'libopus-0.dll'),
            ]
        elif sys.platform == 'darwin':
            _search_paths = [
                '/opt/homebrew/lib/libopus.dylib',       # macOS ARM Homebrew
                '/usr/local/lib/libopus.dylib',           # macOS Intel Homebrew
            ]
        else:
            _search_paths = [
                '/usr/lib/x86_64-linux-gnu/libopus.so.0', # Debian/Ubuntu
                '/usr/lib/libopus.so.0',                   # Other Linux
            ]
        for _opus_path in _search_paths:
            try:
                ctypes.CDLL(_opus_path)
                # Monkey-patch so opuslib finds it, but chain to original for other libs
                _orig_find_library = ctypes.util.find_library
                ctypes.util.find_library = lambda name, _p=_opus_path, _o=_orig_find_library: _p if name == 'opus' else _o(name)
                break
            except OSError:
                continue
    import opuslib
    _HAS_OPUS = True
except (ImportError, Exception):
    _HAS_OPUS = False

# µ-law codec — always loaded as fallback (needed even when Opus is primary,
# because codec negotiation may fall back to µ-law with older peers)
try:
    import audioop
except ImportError:
    try:
        import audioop_lts as audioop
    except ImportError:
        # Pure-Python µ-law fallback (slower but functional)
        import struct
        _ULAW_EXP_TABLE = [0,0,1,1,2,2,2,2,3,3,3,3,3,3,3,3,
                           4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,
                           5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
                           5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
                           6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
                           6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
                           6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
                           6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
                           7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
                           7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
                           7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
                           7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
                           7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
                           7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
                           7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
                           7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7]
        class audioop:
            @staticmethod
            def lin2ulaw(data, width):
                samples = struct.unpack(f'<{len(data)//width}h', data)
                BIAS = 0x84
                CLIP = 32635
                result = bytearray(len(samples))
                for i, s in enumerate(samples):
                    sign = 0x80 if s < 0 else 0
                    s = min(abs(s), CLIP) + BIAS
                    exp = _ULAW_EXP_TABLE[(s >> 7) & 0xFF]
                    mantissa = (s >> (exp + 3)) & 0x0F
                    result[i] = ~(sign | (exp << 4) | mantissa) & 0xFF
                return bytes(result)
            @staticmethod
            def ulaw2lin(data, width):
                result = bytearray(len(data) * 2)
                for i, b in enumerate(data):
                    b = ~b & 0xFF
                    sign = b & 0x80
                    exp = (b >> 4) & 0x07
                    mantissa = b & 0x0F
                    sample = ((mantissa << 3) + 0x84) << exp
                    sample -= 0x84
                    if sign:
                        sample = -sample
                    struct.pack_into('<h', result, i * 2, max(-32768, min(32767, sample)))
                return bytes(result)

from config import CHANNELS, CHUNK_SIZE, DTYPE, SAMPLE_RATE

# Opus codec settings
OPUS_BITRATE = 48000          # 48 kbps — clear voice, low bandwidth
OPUS_FRAME_MS = 20            # 20ms frames (Opus sweet spot: low latency + good quality)
OPUS_FRAME_SIZE = int(SAMPLE_RATE * OPUS_FRAME_MS / 1000)  # 480 samples at 24kHz

# NLMS Echo Canceller settings
AEC_FILTER_LEN = 1200         # 50ms at 24kHz — sufficient for near-field echo
AEC_MU = 0.3                  # Step size (0.1=slow convergence, 1.0=fast but unstable)
AEC_DELTA = 1e-6              # Regularisation to prevent divide-by-zero

# Jitter buffer settings
JITTER_MIN_FRAMES = 3         # Buffer this many frames before starting playback
JITTER_MAX_FRAMES = 15        # Max buffer depth before dropping old frames

# Hotline (always-on) soft noise suppression settings
HOTLINE_SUPPRESS_DB = 15        # dB of suppression when no voice detected
HOTLINE_VOICE_THRESH = 2.5      # RMS must exceed noise_floor * this to count as voice
HOTLINE_NOISE_ADAPT_RATE = 0.03 # How fast noise floor tracks up
HOTLINE_NOISE_DECAY_RATE = 0.005  # How fast noise floor drops
HOTLINE_NOISE_FLOOR_INIT = 80
HOTLINE_NOISE_FLOOR_MIN = 20
HOTLINE_ATTACK_MS = 5           # Fade from suppressed to full (fast, natural)
HOTLINE_RELEASE_MS = 300        # Fade from full to suppressed (slow, keeps tails)
HOTLINE_VOICE_BAND = (200, 3500)  # Wider band than old VOX for naturalness
HOTLINE_VOICE_RATIO = 0.3      # Lower threshold — let more through
HOTLINE_BITRATE = 32000         # 32kbps for continuous streaming (saves bandwidth)

def _configure_audio_backend():
    """Configure sounddevice for lowest latency on the current platform."""
    if sys.platform == 'win32':
        try:
            hostapis = sd.query_hostapis()
            wasapi_idx = None
            for i, api in enumerate(hostapis):
                if 'wasapi' in api['name'].lower():
                    wasapi_idx = i
                    break
            if wasapi_idx is not None:
                sd.default.hostapi = wasapi_idx
                # Verify WASAPI works with a quick device query
                try:
                    sd.query_devices()
                    print(f"Audio: Using WASAPI (host API {wasapi_idx})")
                except Exception as e:
                    sd.default.hostapi = 0
                    print(f"Audio: WASAPI failed ({e}), using default host API")
            else:
                print("Audio: WASAPI not found, using default host API")
        except Exception as e:
            print(f"Audio: Could not configure backend ({e}), using defaults")

_configure_audio_backend()


class JitterBuffer:
    """Adaptive jitter buffer that smooths network timing variations.

    Buffers a minimum number of frames before allowing playback to start,
    then serves frames on demand. Repeats the last frame on underrun instead
    of playing silence (packet loss concealment). Drops oldest frames when
    the buffer grows too large to prevent latency buildup.
    """

    def __init__(self, min_frames=JITTER_MIN_FRAMES, max_frames=JITTER_MAX_FRAMES):
        self.min_frames = min_frames
        self.max_frames = max_frames
        self._buf = collections.deque(maxlen=max_frames)
        self._primed = False       # Have we buffered enough to start?
        self._last_frame = None    # For packet loss concealment

    def push(self, frame):
        """Add a decoded audio frame to the buffer.
        deque(maxlen=) auto-drops oldest when full."""
        self._buf.append(frame)
        if not self._primed and len(self._buf) >= self.min_frames:
            self._primed = True

    def pull(self, frame_shape):
        """Get next frame for playback. Returns None if not yet primed."""
        if not self._primed:
            return np.zeros(frame_shape, dtype=DTYPE)

        if self._buf:
            frame = self._buf.popleft()
            self._last_frame = frame
            return frame
        elif self._last_frame is not None:
            # Packet loss concealment: repeat last frame with slight fade
            self._last_frame = (self._last_frame.astype(np.float32) * 0.8).astype(DTYPE)
            return self._last_frame
        else:
            return np.zeros(frame_shape, dtype=DTYPE)

    def reset(self):
        self._buf.clear()
        self._primed = False
        self._last_frame = None


class EchoCanceller:
    """NLMS (Normalised Least Mean Squares) adaptive echo canceller.

    Maintains a model of the acoustic path from speaker to microphone and
    subtracts the predicted echo from the mic signal in real-time. This
    replaces the old ducking approach with true full-duplex capability.

    The reference signal (what we're playing to the speaker) is stored in a
    circular buffer. The adaptive filter learns the room's impulse response
    and predicts what the mic will pick up, then subtracts it.
    """

    def __init__(self, filter_len=AEC_FILTER_LEN, mu=AEC_MU, frame_size=None):
        self.filter_len = filter_len
        self.mu = mu
        self._w = np.zeros(filter_len, dtype=np.float64)  # Filter coefficients
        self._ref_buf = np.zeros(filter_len, dtype=np.float64)  # Reference signal history
        self._enabled = True

    def feed_reference(self, samples):
        """Feed speaker output samples as reference for echo prediction."""
        ref = samples.astype(np.float64).flatten()
        # Shift reference buffer and append new samples
        n = len(ref)
        if n >= self.filter_len:
            self._ref_buf[:] = ref[-self.filter_len:]
        else:
            self._ref_buf[:-n] = self._ref_buf[n:]
            self._ref_buf[-n:] = ref

    def cancel(self, mic_samples):
        """Remove predicted echo from mic signal. Returns cleaned int16 array.

        Uses a lightweight block NLMS: one filter update per frame using
        numpy stride tricks to build the reference matrix without copying.
        Much faster than the old per-sample Python loop.
        """
        if not self._enabled:
            return mic_samples

        mic = mic_samples.astype(np.float64).flatten()
        N = len(mic)
        L = self.filter_len

        # We need L + N - 1 reference samples. Pad ref_buf with zeros.
        padded = np.concatenate([np.zeros(N - 1), self._ref_buf]) if N > 1 else self._ref_buf
        total = len(padded)

        # Build reference matrix using stride tricks (zero-copy, very fast)
        # Each row is a reversed length-L window aligned with the mic sample
        from numpy.lib.stride_tricks import as_strided
        # First build a forward Toeplitz-like matrix, then reverse each row
        # Start index for row i: total - N + i + 1 - L
        start0 = total - N - L + 1
        if start0 < 0:
            # Not enough reference history — pad more
            padded = np.concatenate([np.zeros(-start0), padded])
            start0 = 0

        # Contiguous slice that covers all windows
        block = padded[start0:start0 + N + L - 1]
        strides = (block.strides[0], block.strides[0])
        ref_matrix = as_strided(block, shape=(N, L), strides=strides).copy()
        # Reverse each row (convolution order: newest first)
        ref_matrix = ref_matrix[:, ::-1]

        # Predict echo: matrix @ filter
        echo_est = ref_matrix @ self._w

        # Error signal
        error = mic - echo_est

        # Block NLMS update
        ref_power = np.mean(np.sum(ref_matrix ** 2, axis=1)) + AEC_DELTA
        grad = ref_matrix.T @ error / N
        self._w += (self.mu / ref_power) * grad

        # Clip and return as int16
        out = np.clip(error, -32768, 32767).astype(DTYPE)
        if mic_samples.ndim > 1:
            out = out.reshape(mic_samples.shape)
        return out

    def reset(self):
        self._w[:] = 0
        self._ref_buf[:] = 0


class AudioManager:
    # Codec identifiers for negotiation
    CODEC_OPUS = "opus"
    CODEC_ULAW = "ulaw"

    def __init__(self, network_manager, log_callback=None):
        self.network_manager = network_manager
        self.log_callback = log_callback
        self.recording = False
        self.streaming = False
        self.input_device = None
        self.output_device = None

        # Voicemail / Message
        self.message_buffer = []

        # Opus encoder/decoder (created lazily to handle import failure)
        self._opus_encoder = None
        self._opus_decoder = None
        self._codec_lock = threading.Lock()
        self._init_opus()

        # Codec state — default to best available codec immediately.
        # Negotiation can downgrade if the peer doesn't support it.
        self._active_codec = self.CODEC_OPUS if self._opus_encoder else self.CODEC_ULAW
        self._negotiated = False  # True once both sides agree

        # Jitter buffer replaces the old raw queue
        self._jitter_buf = JitterBuffer()
        self._play_stream_obj = None  # Reference to active OutputStream

        # NLMS echo canceller replaces ducking
        self._aec = EchoCanceller(frame_size=CHUNK_SIZE)

        # Hotline state (always-on with soft suppression)
        self._hotline_enabled = False
        self._hotline_gain = 10 ** (-HOTLINE_SUPPRESS_DB / 20)  # Start suppressed
        self._hotline_noise_floor = HOTLINE_NOISE_FLOOR_INIT
        self._hotline_opus_encoder = None  # Separate encoder at lower bitrate

        # Audio level callbacks (0.0–1.0 range)
        self.mic_level_callback = None
        self.speaker_level_callback = None

        # Threading state
        self.listening = False
        self.stream_thread = None
        self.play_thread = None
        self._stream_lock = threading.Lock()

    def _init_opus(self):
        """Initialise Opus encoder/decoder if available."""
        if _HAS_OPUS:
            try:
                self._opus_encoder = opuslib.Encoder(
                    SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_VOIP
                )
                # Set bitrate — opuslib's property setter is buggy, use CTL directly
                try:
                    import ctypes
                    OPUS_SET_BITRATE_REQUEST = 4002
                    opuslib.api.encoder.encoder_ctl(
                        self._opus_encoder.encoder_state,
                        OPUS_SET_BITRATE_REQUEST,
                        ctypes.c_int32(OPUS_BITRATE)
                    )
                except Exception:
                    pass  # Default VOIP bitrate is fine
                self._opus_decoder = opuslib.Decoder(SAMPLE_RATE, CHANNELS)
                print(f"[Audio] Opus codec active: {OPUS_BITRATE//1000}kbps, "
                      f"{OPUS_FRAME_MS}ms frames")
            except Exception as e:
                print(f"[Audio] Opus init failed, falling back to µ-law: {e}")
                self._opus_encoder = None
                self._opus_decoder = None
        else:
            print("[Audio] opuslib not available, using µ-law codec")

    def get_supported_codecs(self):
        """Return list of codecs this client supports, best first."""
        codecs = []
        if self._opus_encoder:
            codecs.append(self.CODEC_OPUS)
        codecs.append(self.CODEC_ULAW)  # Always available as fallback
        return codecs

    def negotiate_codec(self, peer_codecs):
        """Choose the best codec both sides support. Call with peer's codec list.
        Returns the chosen codec name."""
        my_codecs = self.get_supported_codecs()
        # Pick the first codec in our preference order that the peer also supports
        for codec in my_codecs:
            if codec in peer_codecs:
                self._active_codec = codec
                self._negotiated = True
                if codec == self.CODEC_ULAW:
                    self.log(f"[Audio] ⚠ Using µ-law codec (low quality) — "
                             f"peer only supports: {peer_codecs}")
                else:
                    self.log(f"[Audio] Codec: {codec}")
                return codec
        # Should never happen since both sides support ulaw
        self._active_codec = self.CODEC_ULAW
        self._negotiated = True
        self.log("[Audio] ⚠ Codec fallback: µ-law (peer codecs: {peer_codecs})")
        return self.CODEC_ULAW

    def reset_codec(self):
        """Reset codec for next connection. Defaults to best available
        (Opus if supported) so audio quality is maximised even if
        negotiation messages are delayed or lost."""
        self._active_codec = self.CODEC_OPUS if self._opus_encoder else self.CODEC_ULAW
        self._negotiated = False

    def _encode(self, raw_bytes):
        """Encode raw int16 PCM to compressed bytes using the active codec."""
        with self._codec_lock:
            if self._active_codec == self.CODEC_OPUS and self._opus_encoder:
                return self._opus_encoder.encode(raw_bytes, OPUS_FRAME_SIZE)
            else:
                return audioop.lin2ulaw(raw_bytes, 2)

    def _decode(self, data):
        """Decode compressed bytes back to int16 PCM using the active codec.
        Returns silence on failure — never tries the wrong codec, since
        µ-law will happily decode any bytes into noise."""
        with self._codec_lock:
            if self._active_codec == self.CODEC_OPUS and self._opus_decoder:
                try:
                    return self._opus_decoder.decode(data, OPUS_FRAME_SIZE)
                except Exception:
                    return b'\x00' * OPUS_FRAME_SIZE * 2
            else:
                try:
                    return audioop.ulaw2lin(data, 2)
                except Exception:
                    return b'\x00' * CHUNK_SIZE * 2

    @property
    def active_frame_size(self):
        """Current frame size based on active codec."""
        if self._active_codec == self.CODEC_OPUS and self._opus_encoder:
            return OPUS_FRAME_SIZE
        return CHUNK_SIZE

    def log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
        else:
            print(msg)

    def list_devices(self):
        """List available audio devices"""
        return sd.query_devices()

    def set_input_device(self, device_index):
        """Set input device. None = System Default"""
        print(f"Setting Input Device to: {device_index}")
        self.input_device = device_index
        if self.streaming:
            self.stop_streaming()
            self.start_streaming()

    def set_output_device(self, device_index):
        """Set output device. None = System Default"""
        print(f"Setting Output Device to: {device_index}")
        if self.output_device != device_index:
            self.output_device = device_index
            self.restart_listening()

    def start_streaming(self):
        """Green Mode: Start streaming mic to UDP"""
        with self._stream_lock:
            if self.streaming:
                return
            if self.stream_thread and self.stream_thread.is_alive():
                self.stream_thread.join(timeout=2.0)
            self.streaming = True
            self._aec.reset()
            self.stream_thread = threading.Thread(target=self._stream_mic, daemon=True)
            self.stream_thread.start()

    def stop_streaming(self):
        self.streaming = False
        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=2.0)

    def _stream_mic(self):
        frame_size = self.active_frame_size

        def callback(indata, frames, time_info, status):
            if status:
                self.log(f"[Mic] stream status: {status}")
            if not self.streaming:
                return

            try:
                self._mic_callback_inner(indata, frames, time_info)
            except Exception as e:
                self.log(f"[Mic] callback error: {e}")

        try:
            with sd.InputStream(device=self.input_device, samplerate=SAMPLE_RATE,
                                channels=CHANNELS, dtype=DTYPE, callback=callback,
                                blocksize=frame_size, latency='low'):
                while self.streaming:
                    sd.sleep(50)
        except sd.PortAudioError as e:
            self.log(f"Mic Stream Error (device may have been removed): {e}")
            self.streaming = False
        except Exception as e:
            self.log(f"Mic Stream Error: {e}")
            self.streaming = False

    def _mic_callback_inner(self, indata, frames, time_info):
        # Calculate RMS for level meter and VOX
        rms = np.sqrt(np.mean(indata.astype(np.float32) ** 2))

        # Report mic level
        if self.mic_level_callback:
            level = min(1.0, rms / 32768.0 * 10)
            self.mic_level_callback(level)

        # ── Hotline path: always-on with soft noise suppression ──
        if self._hotline_enabled:
            audio_f32 = indata[:, 0].astype(np.float32) if indata.ndim > 1 else indata.astype(np.float32).flatten()
            frame_len = len(audio_f32)

            # Adaptive noise floor (tracks ambient level)
            if rms < self._hotline_noise_floor:
                self._hotline_noise_floor += (rms - self._hotline_noise_floor) * HOTLINE_NOISE_DECAY_RATE
            else:
                self._hotline_noise_floor += (rms - self._hotline_noise_floor) * HOTLINE_NOISE_ADAPT_RATE
            self._hotline_noise_floor = max(self._hotline_noise_floor, HOTLINE_NOISE_FLOOR_MIN)

            # Determine if voice is present (spectral + level check)
            voice_thresh = self._hotline_noise_floor * HOTLINE_VOICE_THRESH
            is_voice = False
            if rms >= voice_thresh:
                try:
                    fft = np.abs(np.fft.rfft(audio_f32))
                    freqs = np.fft.rfftfreq(frame_len, 1.0 / SAMPLE_RATE)
                    total_energy = np.sum(fft ** 2)
                    if total_energy > 0:
                        voice_mask = (freqs >= HOTLINE_VOICE_BAND[0]) & (freqs <= HOTLINE_VOICE_BAND[1])
                        voice_energy = np.sum(fft[voice_mask] ** 2)
                        is_voice = (voice_energy / total_energy) >= HOTLINE_VOICE_RATIO
                except Exception:
                    is_voice = True  # If FFT fails, assume voice

            # Smooth gain: ramp toward 1.0 (voice) or suppressed level (no voice)
            suppress_gain = 10 ** (-HOTLINE_SUPPRESS_DB / 20)  # ~0.18 for 15dB
            target_gain = 1.0 if is_voice else suppress_gain

            # Ramp gain based on frame duration (20ms per frame)
            frame_sec = frame_len / SAMPLE_RATE
            if target_gain > self._hotline_gain:
                # Attack: fast ramp up
                attack_sec = max(0.001, HOTLINE_ATTACK_MS / 1000)
                self._hotline_gain += (1.0 - suppress_gain) * (frame_sec / attack_sec)
                self._hotline_gain = min(self._hotline_gain, 1.0)
            else:
                # Release: slow ramp down (keeps natural tails)
                release_sec = max(0.001, HOTLINE_RELEASE_MS / 1000)
                self._hotline_gain -= (1.0 - suppress_gain) * (frame_sec / release_sec)
                self._hotline_gain = max(self._hotline_gain, suppress_gain)

            # Apply gain — never fully silent, always some room tone
            indata = (indata.astype(np.float32) * self._hotline_gain).astype(DTYPE)

            # AEC is critical for hotline (both sides have open mics)
            cleaned = self._aec.cancel(indata)
            raw = cleaned.tobytes()

            # Encode with hotline encoder (lower bitrate for continuous streaming)
            if self._hotline_opus_encoder:
                compressed = self._hotline_opus_encoder.encode(raw, OPUS_FRAME_SIZE)
            else:
                compressed = self._encode(raw)
            self.network_manager.send_audio(compressed)
            return

        # ── PTT path: only sends when streaming flag is set ──
        # AEC: remove echo from mic signal using speaker reference
        cleaned = self._aec.cancel(indata)
        raw = cleaned.tobytes()

        # Encode and send
        compressed = self._encode(raw)
        self.network_manager.send_audio(compressed)

    def set_hotline(self, enabled):
        """Enable/disable hotline (always-on) mode with soft noise suppression."""
        self._hotline_enabled = enabled
        if enabled:
            self._hotline_noise_floor = HOTLINE_NOISE_FLOOR_INIT
            self._hotline_gain = 10 ** (-HOTLINE_SUPPRESS_DB / 20)  # Start suppressed
            # Create a separate Opus encoder at lower bitrate for continuous streaming
            if _HAS_OPUS and not self._hotline_opus_encoder:
                try:
                    self._hotline_opus_encoder = opuslib.Encoder(
                        SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_VOIP
                    )
                    import ctypes
                    OPUS_SET_BITRATE_REQUEST = 4002
                    opuslib.api.encoder.encoder_ctl(
                        self._hotline_opus_encoder.encoder_state,
                        OPUS_SET_BITRATE_REQUEST,
                        ctypes.c_int32(HOTLINE_BITRATE)
                    )
                    self.log(f"[Audio] Hotline encoder: {HOTLINE_BITRATE // 1000}kbps continuous")
                except Exception as e:
                    self.log(f"[Audio] Hotline encoder failed, using main encoder: {e}")
                    self._hotline_opus_encoder = None
        else:
            self._hotline_opus_encoder = None

    def start_recording_message(self):
        """Yellow Mode: Start recording to buffer"""
        self.message_buffer = []
        self.recording = True
        self.record_thread = threading.Thread(target=self._record_buffer)
        self.record_thread.start()

    def stop_recording_message(self, filename=None):
        """Stop recording and save to file"""
        self.recording = False
        if not self.message_buffer:
            return None

        if filename is None:
            from user_settings import _config_dir
            filename = os.path.join(_config_dir(), "outgoing_message.wav")

        data = np.concatenate(self.message_buffer, axis=0)
        sf.write(filename, data, SAMPLE_RATE)
        return filename

    def _record_buffer(self):
        def callback(indata, frames, time, status):
            if status:
                self.log(f"[Record] stream status: {status}")
            try:
                if self.recording:
                    self.message_buffer.append(indata.copy())
            except Exception as e:
                self.log(f"[Record] callback error: {e}")

        try:
            with sd.InputStream(device=self.input_device, samplerate=SAMPLE_RATE,
                                channels=CHANNELS, dtype=DTYPE, callback=callback):
                while self.recording:
                    sd.sleep(100)
        except sd.PortAudioError as e:
            self.log(f"Record Error (device may have been removed): {e}")
            self.recording = False
        except Exception as e:
            self.log(f"Record Error: {e}")

    def start_listening(self):
        """Start output stream for incoming audio"""
        self.listening = True
        self._jitter_buf.reset()
        self._aec.reset()
        self.play_thread = threading.Thread(target=self._play_stream, daemon=True)
        self.play_thread.start()

    def play_audio_chunk(self, data):
        """Decode compressed audio and push into jitter buffer for playback."""
        try:
            # Decode (Opus or µ-law)
            raw = self._decode(data)
            audio_data = np.frombuffer(raw, dtype=DTYPE).copy()
            if len(audio_data) % CHANNELS != 0:
                return

            audio_data = audio_data.reshape(-1, CHANNELS)

            # Report speaker level
            if self.speaker_level_callback:
                rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
                level = min(1.0, rms / 32768.0 * 10)
                self.speaker_level_callback(level)

            # Push into jitter buffer
            self._jitter_buf.push(audio_data)

        except Exception as e:
            print(f"Audio Decode Error: {e}")

    def _play_stream(self):
        frame_size = self.active_frame_size

        def callback(outdata, frames, time, status):
            if status:
                self.log(f"[Play] stream status: {status}")
            try:
                data = self._jitter_buf.pull(outdata.shape)
                outdata[:] = data[:len(outdata)] if len(data) >= len(outdata) else np.pad(
                    data, ((0, len(outdata) - len(data)), (0, 0)), mode='constant'
                )

                # Feed played audio to AEC as reference signal
                self._aec.feed_reference(outdata)
            except Exception as e:
                self.log(f"Play Callback Error: {e}")
                outdata.fill(0)

        try:
            self._play_stream_obj = sd.OutputStream(
                device=self.output_device, samplerate=SAMPLE_RATE,
                channels=CHANNELS, dtype=DTYPE, callback=callback,
                blocksize=frame_size, latency='low')
            with self._play_stream_obj:
                while self.listening:
                    sd.sleep(50)
            self._play_stream_obj = None
        except sd.PortAudioError as e:
            self._play_stream_obj = None
            self.log(f"Audio Stream Error (device may have been removed): {e}")
            self.listening = False
        except Exception as e:
            self._play_stream_obj = None
            self.log(f"Audio Stream Error: {e}")

    def stop_listening(self):
        self.listening = False
        if self.play_thread and self.play_thread.is_alive():
            self.play_thread.join(timeout=1.0)
        try:
            sd.stop()
        except Exception:
            pass

    def restart_listening(self):
        self.stop_listening()
        self.start_listening()

    def play_file(self, filename):
        """Play a WAV file. If the playback stream is active, feed into
        jitter buffer at real-time pace to avoid PortAudio double-free."""
        import time as _time
        try:
            data, fs = sf.read(filename)
            if hasattr(self, '_play_stream_obj') and self._play_stream_obj and self._play_stream_obj.active:
                if data.dtype != np.int16:
                    data = (data * 32767).astype(np.int16)
                if data.ndim == 1 and CHANNELS == 2:
                    data = np.column_stack([data, data])
                elif data.ndim == 1:
                    data = data.reshape(-1, 1)

                # Resample if file sample rate differs from stream rate
                if fs != SAMPLE_RATE:
                    n_samples = int(len(data) * SAMPLE_RATE / fs)
                    indices = np.linspace(0, len(data) - 1, n_samples).astype(int)
                    data = data[indices]

                # Feed frame-sized chunks at real-time pace so the
                # jitter buffer (maxlen=15) doesn't overflow.
                frame_size = self.active_frame_size
                frame_dur = frame_size / SAMPLE_RATE  # ~20ms per frame
                start = _time.monotonic()
                for idx, i in enumerate(range(0, len(data), frame_size)):
                    chunk = data[i:i + frame_size]
                    self._jitter_buf.push(chunk)
                    # Pace delivery: sleep until this frame's scheduled time,
                    # staying ~2 frames ahead of the playback callback.
                    target = start + (idx + 1) * frame_dur
                    ahead = target - _time.monotonic()
                    if ahead > 0 and idx > 1:
                        _time.sleep(ahead)

                # Wait for the last frames to drain
                remaining = len(self._jitter_buf._buf)
                if remaining > 0:
                    _time.sleep(remaining * frame_dur + 0.05)
            else:
                sd.play(data, fs, device=self.output_device)
                sd.wait()
        except Exception as e:
            self.log(f"Play File Error: {e}")

    def play_notification(self):
        """Play a short notification chime (two-tone).
        If the playback stream is active, inject into jitter buffer to avoid
        PortAudio double-free from concurrent sd.play()."""
        try:
            duration = 0.15
            if hasattr(self, '_play_stream_obj') and self._play_stream_obj and self._play_stream_obj.active:
                # Jitter buffer path: use SAMPLE_RATE (matches the stream)
                t1 = np.linspace(0, duration, int(SAMPLE_RATE * duration), False)
                t2 = np.linspace(0, duration, int(SAMPLE_RATE * duration), False)
                tone1 = 0.3 * np.sin(2 * np.pi * 659 * t1)
                tone2 = 0.3 * np.sin(2 * np.pi * 784 * t2)
                gap = np.zeros(int(SAMPLE_RATE * 0.05))
                chime = np.concatenate([tone1, gap, tone2]).astype(np.float32)
                mono = (chime * 32767).astype(np.int16)
                stereo = np.column_stack([mono, mono]) if CHANNELS == 2 else mono.reshape(-1, 1)
                # Push in frame-sized chunks so jitter buffer can serve them
                frame_size = self.active_frame_size
                for i in range(0, len(stereo), frame_size):
                    self._jitter_buf.push(stereo[i:i + frame_size])
            else:
                # sd.play() path: use 44100 Hz stereo for broad device compatibility
                play_sr = 44100
                t1 = np.linspace(0, duration, int(play_sr * duration), False)
                t2 = np.linspace(0, duration, int(play_sr * duration), False)
                tone1 = 0.3 * np.sin(2 * np.pi * 659 * t1)
                tone2 = 0.3 * np.sin(2 * np.pi * 784 * t2)
                gap = np.zeros(int(play_sr * 0.05))
                chime = np.concatenate([tone1, gap, tone2]).astype(np.float32)
                stereo_chime = np.column_stack([chime, chime])
                def _play():
                    try:
                        sd.play(stereo_chime, play_sr, device=self.output_device)
                        sd.wait()
                    except Exception as e:
                        self.log(f"Notification sound error: {e}")
                threading.Thread(target=_play, daemon=True).start()
        except Exception as e:
            self.log(f"Notification sound error: {e}")

    def play_talk_ended(self):
        """Play a gentle descending two-tone chirp when the other user stops talking.
        If the playback stream is active, inject into jitter buffer to avoid
        PortAudio double-free from concurrent sd.play()."""
        try:
            # Two clean fixed-frequency tones (descending) with a tiny gap —
            # avoids the garbled artifacts of a frequency sweep.
            tone_dur = 0.08
            gap_dur = 0.03
            freq_hi = 659.0   # E5
            freq_lo = 523.0   # C5
            amplitude = 0.18

            def _generate(sr):
                n1 = int(sr * tone_dur)
                n2 = int(sr * tone_dur)
                n_gap = int(sr * gap_dur)
                t1 = np.arange(n1) / sr
                t2 = np.arange(n2) / sr
                # Apply fade-in/out envelope to each tone to avoid clicks
                fade = min(int(sr * 0.008), n1 // 4)  # 8ms fade
                env1 = np.ones(n1, dtype=np.float32)
                env1[:fade] = np.linspace(0, 1, fade)
                env1[-fade:] = np.linspace(1, 0, fade)
                env2 = np.ones(n2, dtype=np.float32)
                env2[:fade] = np.linspace(0, 1, fade)
                env2[-fade:] = np.linspace(1, 0, fade)
                tone1 = amplitude * env1 * np.sin(2 * np.pi * freq_hi * t1)
                tone2 = amplitude * env2 * np.sin(2 * np.pi * freq_lo * t2)
                gap = np.zeros(n_gap, dtype=np.float32)
                return np.concatenate([tone1, gap, tone2]).astype(np.float32)

            if hasattr(self, '_play_stream_obj') and self._play_stream_obj and self._play_stream_obj.active:
                signal = _generate(SAMPLE_RATE)
                mono = (signal * 32767).astype(np.int16)
                shaped = np.column_stack([mono, mono]) if CHANNELS == 2 else mono.reshape(-1, 1)
                # Push in frame-sized chunks so jitter buffer can serve them
                frame_size = self.active_frame_size
                for i in range(0, len(shaped), frame_size):
                    self._jitter_buf.push(shaped[i:i + frame_size])
            else:
                play_sr = 44100
                signal = _generate(play_sr)
                stereo_signal = np.column_stack([signal, signal])
                def _play():
                    try:
                        sd.play(stereo_signal, play_sr, device=self.output_device)
                        sd.wait()
                    except Exception as e:
                        self.log(f"Talk-ended sound error: {e}")
                threading.Thread(target=_play, daemon=True).start()
        except Exception as e:
            self.log(f"Talk-ended sound error: {e}")
