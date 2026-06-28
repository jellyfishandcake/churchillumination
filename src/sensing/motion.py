import numpy as np 
import cv2 # OpenCV -- the library for computer vision 
from .base import Sensor 

class MotionSensor(Sensor):
    def __init__(self, sensitivity: float = 8.0):
        self.sensitivity = sensitivity 
        self._cam = cv2.VideoCapture(0) # open the default camera (built in)
        self._prev = None
    
    def read(self) -> dict: 
        ok, frame = self._cam.read()
        if not ok: 
            return {"motion": 0.0}
        
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (160, 120)) # resize to smaller for faster processing

        if self._prev is None:
            self._prev = gray
            return {"motion": 0.0}

        diff = np.abs(gray.astype(float) - self._prev.astype(float)) # pixel changes between frames
        self._prev = gray
        motion = min(1.0, (diff.mean() / 255.0) * self.sensitivity) # scale + clamp to a max of 1

        return {"motion": motion}