import pvporcupine
import pyaudio
import struct
import os
import logging
from config.settings import PICOVOICE_ACCESS_KEY

class WakeWordDetector:
    def __init__(self, keyword_path=None):
        # 'bumblebee' is a built-in keyword for all platforms in Porcupine
        # 'friday' requires a custom .ppn file.
        try:
            if keyword_path and os.path.exists(keyword_path):
                self.porcupine = pvporcupine.create(
                    access_key=PICOVOICE_ACCESS_KEY,
                    keyword_paths=[keyword_path]
                )
            else:
                logging.warning("Friday custom wake word not found. Defaulting to 'bumblebee'.")
                self.porcupine = pvporcupine.create(
                    access_key=PICOVOICE_ACCESS_KEY,
                    keywords=['bumblebee']
                )
            
            self.pa = pyaudio.PyAudio()
            self.audio_stream = self.pa.open(
                rate=self.porcupine.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=self.porcupine.frame_length
            )
        except Exception as e:
            logging.error(f"Failed to initialize Porcupine: {e}")
            raise

    def listen(self):
        print("Listening for wake word...")
        try:
            while True:
                pcm = self.audio_stream.read(self.porcupine.frame_length)
                pcm = struct.unpack_from("h" * self.porcupine.frame_length, pcm)
                result = self.porcupine.process(pcm)
                if result >= 0:
                    print("Wake word detected!")
                    return True
        except KeyboardInterrupt:
            return False
        except Exception as e:
            logging.error(f"Error during wake word detection: {e}")
            return False

    def close(self):
        if hasattr(self, 'audio_stream'):
            self.audio_stream.close()
        if hasattr(self, 'pa'):
            self.pa.terminate()
        if hasattr(self, 'porcupine'):
            self.porcupine.delete()
