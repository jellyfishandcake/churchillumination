from dataclasses import dataclass, asdict

@dataclass
class EnvironmentState:
    activity_level: float = 0.0
    mood: str = "neutral"
    presence_count: int = 0
    audio_scene: str | None = None  # e.g. "Speech"/"Music"/"Silence" - the raw YAMNet class, not folded into mood

    def to_dict(self) -> dict:
        return asdict(self)

