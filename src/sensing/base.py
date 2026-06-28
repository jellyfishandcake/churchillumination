from abc import ABC, abstractmethod

class Sensor(ABC): 
    @abstractmethod 
    def read(self) -> dict:
        # helps return normalised readings and cap readings to useable / acceptable range 
        readings = {"loudness": 0.4, 
                    "motion": 0.2}