from dataclasses import dataclass 

@dataclass 
class EnvironmentState: 
    activity_level: float = 0.0
    mood: str = "neutral"
    presence_count: int = 0

