import numpy as np 
import sounddevice as sd 
from .base import Sensor 

class AudioSensor(Sensor):
    # here we read laptop mic and turns volume into a number
    def __init__(self, sensitivity: float = 20.0):
        self._loudness = 0.0
        self.sensitivity = sensitivity 
        self._stream = sd.InputStream(
            channels=1, 
            samplerate = 16000, 
            blocksize = 1600, 
            callback = self._on_audio,
        )
        self._stream.start()

    def _on_audio(self, indata, frames, time, status):
        rms = float(np.sqrt(np.mean(indata**2)))
        self._loudness = min(1.0, rms*self.sensitivity) # scale + clamp to a max of 1 
    
    def read(self) -> dict: 
        return {"loudness": self._loudness}
    
    