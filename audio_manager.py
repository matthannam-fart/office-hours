import sounddevice as sd
import soundfile as sf
import numpy as np
import threading
import queue
import os
import audioop
from config import SAMPLE_RATE, CHANNELS, CHUNK_SIZE, DTYPE

class AudioManager:
    def __init__(self, network_manager, log_callback=None):
        self.network_manager = network_manager
        self.log_callback = log_callback
        self.recording = False
        self.streaming = False
        self.audio_queue = queue.Queue()
        self.input_device = None
        self.output_device = None
        
        # Voicemail / Message
        self.message_buffer = []

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
        if self.streaming: return
        self.streaming = True
        self.stream_thread = threading.Thread(target=self._stream_mic)
        self.stream_thread.start()

    def stop_streaming(self):
        self.streaming = False

    def _stream_mic(self):
        def callback(indata, frames, time, status):
            if status:
                self.log(str(status))
            if self.streaming:
                raw = indata.tobytes()
                # Compress with µ-law: 16-bit → 8-bit (halves bandwidth)
                compressed = audioop.lin2ulaw(raw, 2)
                self.network_manager.send_audio(compressed)

        try:
            with sd.InputStream(device=self.input_device, samplerate=SAMPLE_RATE,
                                channels=CHANNELS, dtype=DTYPE, callback=callback,
                                blocksize=CHUNK_SIZE):
                while self.streaming:
                    sd.sleep(100)
        except Exception as e:
            self.log(f"Mic Stream Error: {e}")

    def start_recording_message(self):
        """Yellow Mode: Start recording to buffer"""
        self.message_buffer = []
        self.recording = True
        self.record_thread = threading.Thread(target=self._record_buffer)
        self.record_thread.start()

    def stop_recording_message(self, filename="outgoing_message.wav"):
        """Stop recording and save to file"""
        self.recording = False
        if not self.message_buffer:
            return None
        
        # Save to file
        data = np.concatenate(self.message_buffer, axis=0)
        sf.write(filename, data, SAMPLE_RATE)
        return filename

    def _record_buffer(self):
        def callback(indata, frames, time, status):
            if self.recording:
                self.message_buffer.append(indata.copy())

        try:
            with sd.InputStream(device=self.input_device, samplerate=SAMPLE_RATE,
                                channels=CHANNELS, dtype=DTYPE, callback=callback):
                while self.recording:
                    sd.sleep(100)
        except Exception as e:
            self.log(f"Record Error: {e}")

    def start_listening(self):
        """Start output stream for incoming audio"""
        self.listening = True
        self.play_thread = threading.Thread(target=self._play_stream, daemon=True)
        self.play_thread.start()

    def play_audio_chunk(self, data):
        """Queue compressed audio chunk for playback"""
        try:
            # Decompress µ-law: 8-bit → 16-bit
            raw = audioop.ulaw2lin(data, 2)
            audio_data = np.frombuffer(raw, dtype=DTYPE)
            if len(audio_data) % CHANNELS != 0:
                 return
            
            audio_data = audio_data.reshape(-1, CHANNELS)
            self.audio_queue.put(audio_data)
        except Exception as e:
            print(f"Audio Decode Error: {e}")

    def _play_stream(self):
        def callback(outdata, frames, time, status):
            if status:
                print(status)
            try:
                data = self.audio_queue.get_nowait()
                
                if len(data) > len(outdata):
                    outdata[:] = data[:len(outdata)]
                elif len(data) < len(outdata):
                    outdata[:len(data)] = data
                    outdata[len(data):] = 0
                else:
                    outdata[:] = data
            except queue.Empty:
                outdata.fill(0)
            except Exception as e:
                self.log(f"Play Callback Error: {e}")
                outdata.fill(0)

        try:
             with sd.OutputStream(device=self.output_device, samplerate=SAMPLE_RATE,
                                  channels=CHANNELS, dtype=DTYPE, callback=callback,
                                  blocksize=CHUNK_SIZE):
                while self.listening:
                    sd.sleep(100)
        except Exception as e:
            self.log(f"Audio Stream Error: {e}")
            
    def stop_listening(self):
        self.listening = False
        if hasattr(self, 'play_thread') and self.play_thread.is_alive():
            self.play_thread.join(timeout=1.0)
            
    def restart_listening(self):
        self.stop_listening()
        self.start_listening()

    def play_file(self, filename):
        """Play a WAV file"""
        try:
            data, fs = sf.read(filename)
            sd.play(data, fs, device=self.output_device)
            sd.wait()
        except Exception as e:
            self.log(f"Play File Error: {e}")

    def play_notification(self):
        """Play a short notification chime (two-tone)"""
        def _play():
            try:
                duration = 0.15
                t1 = np.linspace(0, duration, int(SAMPLE_RATE * duration), False)
                t2 = np.linspace(0, duration, int(SAMPLE_RATE * duration), False)
                # Two-note chime: E5 then G5
                tone1 = 0.3 * np.sin(2 * np.pi * 659 * t1)
                tone2 = 0.3 * np.sin(2 * np.pi * 784 * t2)
                # Small gap between notes
                gap = np.zeros(int(SAMPLE_RATE * 0.05))
                chime = np.concatenate([tone1, gap, tone2]).astype(np.float32)
                sd.play(chime, SAMPLE_RATE, device=self.output_device)
                sd.wait()
            except Exception as e:
                self.log(f"Notification sound error: {e}")
        threading.Thread(target=_play, daemon=True).start()
