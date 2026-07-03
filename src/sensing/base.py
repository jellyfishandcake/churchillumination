from abc import ABC, abstractmethod

class Sensor(ABC):
    @abstractmethod
    def read(self) -> dict:
        """Return a dict of normalised readings, each value clamped to a
        usable/acceptable range (e.g. 0..1 for levels). Keys must be unique
        across all sensors in use, since main.py merges every sensor's dict
        together with `readings.update(...)`."""