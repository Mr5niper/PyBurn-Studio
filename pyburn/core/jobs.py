from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
class JobType(str, Enum):
    DATA = "data"
    AUDIO = "audio"
    VIDEO_DVD = "video_dvd"
    VIDEO_BD = "video_bd"
    RIP = "rip"
@dataclass
class JobOptions:
    temp_dir: Path
    verify: bool = False
    speed: Any = "Auto"
    volume_label: str = "DATA_DISC"
    output_dir: Optional[Path] = None
    rip_format: str = "MP3"
    rip_bitrate: int = 320
    auto_blank: bool = True
    eject_after: bool = True
    dummy: bool = False
    album_title: Optional[str] = None
    album_performer: Optional[str] = None
    track_titles: Optional[List[str]] = None
    track_performers: Optional[List[str]] = None
@dataclass
class Job:
    job_type: JobType
    files: List[Path] = field(default_factory=list)
    device: str = "/dev/sr0"
    options: JobOptions = field(default_factory=lambda: JobOptions(temp_dir=Path("/tmp")))
    id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d%H%M%S%f"))
    status: str = "PENDING"
    progress: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "job_type": self.job_type.value,
            "files": [str(p) for p in self.files],
            "device": self.device,
            "options": {
                **asdict(self.options),
                "temp_dir": str(self.options.temp_dir),
                "output_dir": str(self.options.output_dir) if self.options.output_dir else None,
            },
            "created_at": self.created_at,
        }
    @property
    def display_name(self) -> str:
        if self.job_type == JobType.DATA:
            return f"Data Burn ({self.options.volume_label})"
        if self.job_type == JobType.AUDIO:
            return "Audio CD"
        if self.job_type == JobType.VIDEO_DVD:
            return "Video DVD"
        if self.job_type == JobType.VIDEO_BD:
            return "Blu-ray (BDMV)"
        if self.job_type == JobType.RIP:
            return f"Rip CD ({self.options.rip_format})"
        return "Job"