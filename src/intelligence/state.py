from dataclasses import dataclass, asdict

@dataclass
class EnvironmentState:
    activity_level: float = 0.0
    mood: str = "neutral"
    presence_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

