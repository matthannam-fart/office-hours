"""Tests for audio helper classes (JitterBuffer, EchoCanceller) from audio_manager.py.

These test the pure data-processing logic without requiring audio hardware.
"""

import numpy as np


def test_jitter_buffer_starts_empty():
    """JitterBuffer should not be primed initially."""
    from audio_manager import JitterBuffer
    jb = JitterBuffer(min_frames=3, max_frames=10)
    # pull() before primed should return zeros
    frame = jb.pull((480, 1))
    assert frame.shape == (480, 1)
    assert np.all(frame == 0)


def test_jitter_buffer_primes_after_min_frames():
    """JitterBuffer should start serving frames after min_frames are pushed."""
    from audio_manager import JitterBuffer
    jb = JitterBuffer(min_frames=3, max_frames=10)
    frames = [np.ones((480, 1), dtype='int16') * (i + 1) for i in range(3)]
    for f in frames:
        jb.push(f)

    # Should now be primed and return the first pushed frame
    result = jb.pull((480, 1))
    assert np.all(result == 1)  # First frame


def test_jitter_buffer_fifo_order():
    """JitterBuffer should return frames in FIFO order."""
    from audio_manager import JitterBuffer
    jb = JitterBuffer(min_frames=2, max_frames=10)
    for i in range(5):
        jb.push(np.full((480, 1), i, dtype='int16'))

    for i in range(5):
        result = jb.pull((480, 1))
        assert np.all(result == i)


def test_jitter_buffer_packet_loss_concealment():
    """On underrun, JitterBuffer should repeat last frame with fade."""
    from audio_manager import JitterBuffer
    jb = JitterBuffer(min_frames=1, max_frames=10)
    frame = np.full((480, 1), 1000, dtype='int16')
    jb.push(frame)

    # Pull the real frame
    result1 = jb.pull((480, 1))
    assert np.all(result1 == 1000)

    # Pull again — should get faded repeat
    result2 = jb.pull((480, 1))
    assert result2.shape == (480, 1)
    # Should be approximately 1000 * 0.8 = 800
    assert np.all(result2 == 800)


def test_jitter_buffer_max_frames_drops_old():
    """JitterBuffer should drop oldest frames when max is exceeded."""
    from audio_manager import JitterBuffer
    jb = JitterBuffer(min_frames=1, max_frames=3)
    for i in range(5):
        jb.push(np.full((480, 1), i, dtype='int16'))

    # Only the last 3 frames should remain (2, 3, 4)
    result = jb.pull((480, 1))
    assert np.all(result == 2)


def test_jitter_buffer_reset():
    """reset() should clear buffer and require re-priming."""
    from audio_manager import JitterBuffer
    jb = JitterBuffer(min_frames=2, max_frames=10)
    for i in range(3):
        jb.push(np.ones((480, 1), dtype='int16'))

    jb.reset()
    # Should return zeros (not primed)
    result = jb.pull((480, 1))
    assert np.all(result == 0)


def test_echo_canceller_passthrough_no_reference():
    """With no reference signal, EchoCanceller should pass through mic signal."""
    from audio_manager import EchoCanceller
    aec = EchoCanceller(filter_len=48)
    mic = np.random.randint(-1000, 1000, size=(480, 1), dtype='int16')
    result = aec.cancel(mic)
    assert result.shape == mic.shape
    assert result.dtype == np.int16


def test_echo_canceller_reset():
    """reset() should zero out filter coefficients."""
    from audio_manager import EchoCanceller
    aec = EchoCanceller(filter_len=48)
    # Feed some reference and mic data to build up filter state
    ref = np.random.randint(-1000, 1000, size=(480,), dtype='int16')
    mic = np.random.randint(-1000, 1000, size=(480, 1), dtype='int16')
    aec.feed_reference(ref)
    aec.cancel(mic)

    aec.reset()
    assert np.all(aec._w == 0)
    assert np.all(aec._ref_buf == 0)


def test_echo_canceller_disabled():
    """When disabled, cancel() should return mic signal unchanged."""
    from audio_manager import EchoCanceller
    aec = EchoCanceller(filter_len=48)
    aec._enabled = False
    mic = np.random.randint(-1000, 1000, size=(480, 1), dtype='int16')
    result = aec.cancel(mic)
    np.testing.assert_array_equal(result, mic)


def test_audio_constants():
    """Audio-related constants should have expected values."""
    from audio_manager import (
        AEC_FILTER_LEN,
        JITTER_MAX_FRAMES,
        JITTER_MIN_FRAMES,
        OPUS_BITRATE,
        OPUS_FRAME_MS,
        OPUS_FRAME_SIZE,
    )
    assert OPUS_FRAME_MS == 20
    assert OPUS_FRAME_SIZE == 480  # 24000 * 20 / 1000
    assert OPUS_BITRATE == 48000
    assert JITTER_MIN_FRAMES == 3
    assert JITTER_MAX_FRAMES == 15
    assert AEC_FILTER_LEN == 1200
