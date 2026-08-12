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
    ches_code: str | None = None
    ches_family: str | None = None


@dataclass
class Speech:
    id: str | None
    date: datetime
    act_id: str | None
    source_lang: str | None
    speaker_type: str | None
    debate_title: str | None
    debate_type: DebateType | None
    translation: Translation | None
    speaker: List[Speaker]
    cap_major: List
    cap_major_ids: List
    cap_minor: List
    cap_minor_ids: List
    words: int
    paragraphs: List[str] = field(default_factory=list) 

    @classmethod
    def load(cls, speech: dict) -> Speech:
        """ Load the dataclasses from a stored json"""
        speakers = [Speaker(**_) for _ in speech["speaker"]]
        speech["speaker"] = speakers
        return Speech(**speech)   

    # TODO check if needed
    # def is_complete(self) -> bool:
    #     if not self.group or not self.paragraphs:
    #         return False
    #     return True

    # def as_dict(self) -> dict:
    #     return {
    #         "act_id": self.act_id,
    #         "source_lang": self.source_lang,
    #         "speaker_type": self.speaker_type,
    #         "party": self.party,
    #         "country": self.country,
    #         "group": self.group,
    #         "ches": self.ches,
    #         "mep_id": self.mep_id,
    #         "speaker_name": self.speaker_name,
    #         "debate_title": self.debate_title,
    #         "debate_type": self.debate_type,
    #         "translation": self.translation,
    #         "cap": self.cap,
    #         "cap_code": self.cap_code,
    #         "paragraphs": self.paragraphs,
    #     }


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
            speeches=speeches
            )   

    # def as_dict(self, include_incomplete=True):
    #     """
    #     :param include_incomplete: If False, omits speeches with incomplete speaker metadata , defaults to True
    #     """
    #     _speeches = [_.as_dict() 
    #                  for _ in self.speeches
    #                  if include_incomplete or _.is_complete()]
    #     return {
    #         "date": self.date.isoformat(),
    #         "speeches": _speeches
    #     }


def save(dataset: List[VerbatimReport], dataset_path: str) -> None:
    """Serialize and save a dataset"""
    dataset = sorted(dataset, key=lambda x: x.date)

    with open(dataset_path, 'w') as of:
        for report in tqdm(dataset, desc="saving reports"):
            for speech in report.speeches:
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
