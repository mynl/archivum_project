"""Class to manage a single reference for archivum."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

@dataclass
class Reference:
    source: Optional[Path] = None
    title: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    type: Optional[str] = None
    publisher: Optional[str] = None
    journal: Optional[str] = None

    def to_dict(self):
        return self.__dict__

    def to_bibtex(self):
        # implement later
        pass

    def open(self):
        # open pdf
        pass
