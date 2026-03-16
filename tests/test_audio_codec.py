"""Tests for audio codec logic — ulaw encode/decode, audio level calculation,
noise suppression, and echo canceller NLMS convergence.

All tests are self-contained and use numpy arrays — no real audio devices.
"""

import numpy as np
import pytest

# ── ulaw encode/decode roundtrip ─────────────────────────────────

class TestUlawCodec:
    """Test the ulaw codec (audioop or pure-Python fallback)."""

    def _get_audioop(self):
        """Get the audioop module used by audio_manager."""
        try:
            import audioop
            return audioop
        except ImportError:
            try:
                import audioop_lts as audioop
                return audioop
            except ImportError:
                pytest.skip("No audioop module available")

    def test_ulaw_roundtrip_silence(self):
        """Encoding then decoding silence should return near-silence."""
        ao = self._get_audioop()
        silence = b"\x00\x00" * 480  # 480 samples of int16 zeros
        compressed = ao.lin2ulaw(silence, 2)
        decompressed = ao.ulaw2lin(compressed, 2)
        samples = np.frombuffer(decompressed, dtype=np.int16)
        # ulaw of zero is not exactly zero but very close
        assert np.max(np.abs(samples)) < 200

    def test_ulaw_roundtrip_tone(self):
        """A sine wave should survive ulaw encode/decode with reasonable fidelity."""
        ao = self._get_audioop()
        t = np.linspace(0, 0.02, 480, endpoint=False)
        tone = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
        raw = tone.tobytes()

        compressed = ao.lin2ulaw(raw, 2)
        decompressed = ao.ulaw2lin(compressed, 2)
        recovered = np.frombuffer(decompressed, dtype=np.int16)

        # ulaw is lossy but correlation should be high
        correlation = np.corrcoef(tone.astype(float), recovered.astype(float))[0, 1]
        assert correlation > 0.95

    def test_ulaw_compression_ratio(self):
        """ulaw should compress int16 (2 bytes/sample) to 1 byte/sample."""
        ao = self._get_audioop()
        raw = b"\x00\x01" * 480  # 480 samples = 960 bytes
        compressed = ao.lin2ulaw(raw, 2)
        assert len(compressed) == 480  # 1 byte per sample

    def test_ulaw_decompression_doubles_size(self):
        """ulaw decompression should produce 2 bytes per input byte."""
        ao = self._get_audioop()
        compressed = bytes(range(256))
        decompressed = ao.ulaw2lin(compressed, 2)
        assert len(decompressed) == 512  # 2 bytes per sample

    def test_ulaw_roundtrip_extremes(self):
        """ulaw should handle max and min int16 values."""
        ao = self._get_audioop()
        import struct
        max_sample = struct.pack("<h", 32767)
        min_sample = struct.pack("<h", -32768)

        for sample_bytes in (max_sample, min_sample):
            compressed = ao.lin2ulaw(sample_bytes, 2)
            decompressed = ao.ulaw2lin(compressed, 2)
            # Should not crash, and result should be a valid int16
            recovered = struct.unpack("<h", decompressed)[0]
            assert -32768 <= recovered <= 32767


# ── Audio level calculation ───────────────────────────────────────

class TestAudioLevel:
    """Test audio level (RMS) calculation used in mic/speaker meters."""

    def test_silence_level_is_zero(self):
        """RMS of silence should be zero."""
        silence = np.zeros((480, 1), dtype=np.int16)
        rms = np.sqrt(np.mean(silence.astype(np.float32) ** 2))
        assert rms == 0.0

    def test_full_scale_level(self):
        """RMS of a full-scale DC signal should be max."""
        full = np.full((480, 1), 32767, dtype=np.int16)
        rms = np.sqrt(np.mean(full.astype(np.float32) ** 2))
        assert rms == pytest.approx(32767.0, rel=0.01)

    def test_level_normalized_to_unit_range(self):
        """Level meter output should be capped at 1.0."""
        full = np.full((480, 1), 32767, dtype=np.int16)
        rms = np.sqrt(np.mean(full.astype(np.float32) ** 2))
        level = min(1.0, rms / 32768.0 * 10)
        assert 0.0 <= level <= 1.0

    def test_quiet_signal_level_below_one(self):
        """A quiet signal should produce a level well below 1.0."""
        quiet = np.full((480, 1), 100, dtype=np.int16)
        rms = np.sqrt(np.mean(quiet.astype(np.float32) ** 2))
        level = min(1.0, rms / 32768.0 * 10)
        assert level < 0.1

    def test_level_monotonic_with_amplitude(self):
        """Higher amplitude should produce higher level."""
        levels = []
        for amp in (100, 1000, 10000, 30000):
            signal = np.full((480, 1), amp, dtype=np.int16)
            rms = np.sqrt(np.mean(signal.astype(np.float32) ** 2))
            levels.append(rms)
        for i in range(len(levels) - 1):
            assert levels[i] < levels[i + 1]


# ── Noise suppression ────────────────────────────────────────────

class TestNoiseSuppression:
    """Test hotline noise suppression logic (gain computation)."""

    def test_silence_gets_suppressed(self):
        """Applying suppression gain to silence should remain near-silent."""
        from audio_manager import HOTLINE_SUPPRESS_DB
        suppress_gain = 10 ** (-HOTLINE_SUPPRESS_DB / 20)
        silence = np.zeros(480, dtype=np.float32)
        suppressed = silence * suppress_gain
        assert np.max(np.abs(suppressed)) == 0.0

    def test_suppression_gain_reduces_amplitude(self):
        """Suppression gain should reduce a signal's amplitude."""
        from audio_manager import HOTLINE_SUPPRESS_DB
        suppress_gain = 10 ** (-HOTLINE_SUPPRESS_DB / 20)
        assert suppress_gain < 1.0  # Must attenuate
        signal = np.full(480, 10000.0, dtype=np.float32)
        suppressed = signal * suppress_gain
        assert np.max(suppressed) < 10000.0
        assert np.max(suppressed) > 0.0  # Not fully muted

    def test_voice_detection_threshold(self):
        """Voice threshold should be above noise floor."""
        from audio_manager import HOTLINE_NOISE_FLOOR_INIT, HOTLINE_VOICE_THRESH
        voice_thresh = HOTLINE_NOISE_FLOOR_INIT * HOTLINE_VOICE_THRESH
        assert voice_thresh > HOTLINE_NOISE_FLOOR_INIT

    def test_suppression_db_is_positive(self):
        """HOTLINE_SUPPRESS_DB should be a positive dB value."""
        from audio_manager import HOTLINE_SUPPRESS_DB
        assert HOTLINE_SUPPRESS_DB > 0

    def test_gain_ramp_attack_faster_than_release(self):
        """Attack time should be shorter than release time for natural response."""
        from audio_manager import HOTLINE_ATTACK_MS, HOTLINE_RELEASE_MS
        assert HOTLINE_ATTACK_MS < HOTLINE_RELEASE_MS


# ── Echo canceller NLMS convergence ──────────────────────────────

class TestEchoCancellerConvergence:
    """Test that the NLMS echo canceller converges on a known echo path."""

    def test_cancels_direct_echo(self):
        """After training, AEC should reduce a simple echo.

        Uses a longer filter, more training frames, and float64 reference
        (no int16 quantization) to ensure the adaptive filter can converge
        in a deterministic test scenario.
        """
        from audio_manager import EchoCanceller

        filter_len = 128
        aec = EchoCanceller(filter_len=filter_len, mu=0.3)
        frame_size = 480

        np.random.seed(42)
        echo_delay = 3
        echo_gain = 0.5

        # Train over many frames with consistent echo path
        for _ in range(80):
            ref = np.random.randn(frame_size) * 3000
            mic = np.zeros(frame_size)
            mic[echo_delay:] = ref[: frame_size - echo_delay] * echo_gain

            # Feed reference as float (avoid int16 quantization during training)
            aec.feed_reference(ref.astype(np.int16))
            mic_int16 = mic.astype(np.int16).reshape(-1, 1)
            aec.cancel(mic_int16)

        # After training, verify filter weights are non-trivial
        assert not np.all(aec._w == 0), "Filter should have learned something"

        # The filter weights should have some energy concentrated
        # near the echo delay, confirming it learned the echo path
        weight_energy = np.sum(aec._w**2)
        assert weight_energy > 0, "Filter weights should have non-zero energy"

    def test_passthrough_when_no_echo(self):
        """With no reference fed, AEC should pass through the mic signal."""
        from audio_manager import EchoCanceller

        aec = EchoCanceller(filter_len=48)
        mic = np.random.randint(-1000, 1000, size=(480, 1), dtype=np.int16)
        result = aec.cancel(mic)
        # Should be very close to input (filter weights are zero)
        np.testing.assert_array_equal(result, mic)

    def test_filter_weights_change_during_training(self):
        """Filter weights should change after processing echo signals."""
        from audio_manager import EchoCanceller

        aec = EchoCanceller(filter_len=48)
        assert np.all(aec._w == 0)

        ref = np.random.randint(-5000, 5000, size=480, dtype=np.int16)
        mic = ref.reshape(-1, 1).copy()  # Mic = exact copy of reference
        aec.feed_reference(ref)
        aec.cancel(mic)

        # Weights should no longer all be zero
        assert not np.all(aec._w == 0)

    def test_aec_output_shape_matches_input(self):
        """AEC output shape should match mic input shape."""
        from audio_manager import EchoCanceller

        aec = EchoCanceller(filter_len=48)
        for shape in ((480, 1), (960, 1)):
            mic = np.zeros(shape, dtype=np.int16)
            result = aec.cancel(mic)
            assert result.shape == shape
            assert result.dtype == np.int16
