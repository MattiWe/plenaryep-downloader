from __future__ import annotations

from pathlib import Path
from typing import Any, List, Iterator
from datetime import date, datetime
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from tqdm.auto import tqdm
import orjson
import json

logger = logging.getLogger(__name__)


class SpeakerType(str, Enum):
    RAPPORTEUR = "rapporteur"
    MEP = "mep"
    COMISSION = "commission"
    COUNCIL = "council"
    AUTHOR = "author"
    BLUECARD = "bluecard"
    GUEST = "guest"


class DebateType(str, Enum):
    DEBATE = "debate"
    VOTE = "vote"
    OMS = "oms"  # one minute speeches
    PROCEDURAL = "procedural"  # urgend debate
    QUESTIONS = "questions"
    FORMAL = "formal"
    OTHER = "other"


class Translation(str, Enum):
    SOURCE = "source"
    EP = "ep"
    DEEPL = "deepl"
    GOOGLE = "google"


@dataclass
class Speaker:
    mep_id: str | None
    speaker_name: str | None
    country: str | None = None
    group: str | None = None
    group_code: str | None = None
    group_family: str | None = None
    party: str | None = None
    party_code: str | None = None
    ches_id: str | None = None
    ches_family: str | None = None


@dataclass
class Speech:
    id: str
    date: str
    act_id: str | None
    source_lang: str | None
    speaker_type: str | None
    debate_title: str | None
    debate_type: DebateType | None
    translation: Translation | None
    speaker: List[Speaker] = field(default_factory=list) 
    cap_major: List = field(default_factory=list) 
    cap_major_ids: List = field(default_factory=list) 
    cap_minor: List = field(default_factory=list) 
    cap_minor_ids: List = field(default_factory=list) 
    words: int = 0
    paragraphs: List[str] = field(default_factory=list) 

    @classmethod
    def load(cls, speech: dict) -> Speech:
        """ Load the dataclasses from a stored json"""
        speakers = [Speaker(**_) for _ in speech["speaker"]]
        speech["speaker"] = speakers
        return Speech(**speech)   


@dataclass
class VerbatimReport:
    date: datetime
    speeches: List[Speech] = field(default_factory=list) 

    @classmethod
    def load(cls, date, speeches: list) -> VerbatimReport:
        """ Load the dataclasses from a stored json"""
        speeches = [Speech.load(_) for _ in speeches]
        return VerbatimReport(
            date=datetime.fromisoformat(date),
            speeches=speeches)   


def save(dataset: List[VerbatimReport], dataset_path: str | Path, no_filter: bool = True) -> None:
    """Serialize and save a dataset"""
    dataset = sorted(dataset, key=lambda x: x.date)

    with open(dataset_path, 'w') as of:
        for report in tqdm(dataset, desc="saving reports"):
            for speech in report.speeches:
                if not no_filter and (
                    speech.debate_type == DebateType.PROCEDURAL or
                    speech.debate_type == DebateType.VOTE):
                    continue
                of.write(f"{json.dumps(asdict(speech), ensure_ascii=False)}\n")


def load(dataset_path: str) -> List[VerbatimReport]:
    """Load the serialized dataset as a list of `VerbatimReport`s"""
    reports = {}
    for line in tqdm(open(dataset_path), desc="loading dataset"):
        line = orjson.loads(line)
        reports.setdefault(line["date"], []).append(line)

    loaded = [VerbatimReport.load(date=_date, speeches=_speeches) 
              for _date, _speeches in reports.items()]
    dataset = sorted(loaded, key=lambda x: x.date)

    return dataset
