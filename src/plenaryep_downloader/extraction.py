from __future__ import annotations

from pathlib import Path
from typing import Iterator
from datetime import datetime
from collections import Counter
from copy import deepcopy
from contextlib import contextmanager
import json
import re

from wasabi import msg as logger
import lxml.etree as etree
from tqdm import tqdm
from scrapy.crawler import CrawlerProcess

from plenaryep_downloader.spiders.reds_xml_spider import RedsXmlSpider
import plenaryep_downloader.reports as ep

generated = (Path(__file__).parents[2] / "generated").absolute()
data = (Path(__file__).parents[2] / "data").absolute()

"""
Versions:
    v1 - Pre 1999-07-20: TL-CHAP TYPE is either missing or "OTHER", there are no interventions or numberos
    v2 - 1999-07-20 -- 2008-01-30: TL-CHAP TYPE is missing, interverions with orateurs exists
    v3 - 2008-01-31 -- 2012-12-10: TL-CHAP TYPE exists, interverions with orateurs exists
    v4 - since 2012-12-10 only REV files, multiple TL-CHAP, but types only OTHER and VOTE
"""
v1_date = datetime(1999, 7, 19)
v2_date = datetime(2008, 1, 30)
v3_date = datetime(2012, 12, 10)


def download_sources(newest: datetime | None, verbose: bool) -> None:
    source_output = generated / "sources"
    source_output.mkdir(exist_ok=True)

    process = CrawlerProcess()
    process.crawl(RedsXmlSpider, newest=newest, output_dir=source_output, verbose=verbose)
    process.start()


class Extractor(object):
    re_strip_leading_na = re.compile(r"^\W ")
    re_leading_number = re.compile(r"^\d*\. ")
    re_vote = re.compile(r"VOTES.*|Votes|vote|vote\)|voting")
    re_debate = re.compile(r"debate\)")
    re_procedural = re.compile(
        r"""
        ^(?:
            (?:approval|adoption)\s+of\s+(?:the\s+)?minutes\b
            | (?:(?:changes?\s+(?:to|in))\s+)?(?:the\s+)?(?:membership|composition)\s+of\s+
              (?:the\s+)?(?:(?:advisory|special|standing)\s+)?
              (?:committees?|(?:interparliamentary\s+)?delegations?|parliament|political\s+groups?)\b
            | appointments?\s+(?:(?:of\s+.+?\s+)?to|of\s+(?:a\s+)?members?\s+(?:of|to))\s+
              (?:the\s+)?(?:(?:advisory|special|standing)\s+)?
              (?:committees?|(?:interparliamentary\s+)?delegations?)\b
            | (?:(?:announcement\s+of|short\s+presentations\s+by)\s+(?:the\s+)?candidates\s+for\s+)?
              elections?\s+(?:of|to)\s+(?:the\s+office\s+of\s+)?(?:(?:the|a|two)\s+)?
              (?:presidents?(?:\s+and\s+vice-presidents?)?|vice-presidents?|quaestors?|(?:committee\s+)?chairs?)
              (?:\s+of\s+(?:the\s+)?(?:european\s+)?parliament)?(?=\s*(?:$|\(|:))
            | (?:order\s+of\s+business|(?:changes?|amendments?)\s+to\s+(?:the\s+)?agenda)\b
            | (?:corrigendum|corrigenda)\b
            | interpretation\s+of\s+(?:the\s+)?rules\s+of\s+procedure\b
            | calendar\s+of\s+(?:parliament['’]s\s+)?part-sessions\b
            | agenda\b
            | (?:resumption|adjournment|closure|opening|suspension)\s+of\s+(?:the\s+)?(?:session|sitting)\b
            | (?:decision|request)\s+(?:on|for)\s+(?:urgency|(?:the\s+)?urgent\s+procedure)\b
        )
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    re_formal = re.compile(
        r"^(?:(?:extraordinary\s+)?formal\s+(?:sitting|session)|commemorative\s+sitting|address\s+by)\b",
        re.IGNORECASE,
    )
    re_questions = re.compile(r"^(?:question\s+(?:time|hour)|oral\s+questions?)\b", re.IGNORECASE)
    re_written = re.compile(r"^written\s+(?:declarations|statements)\b", re.IGNORECASE)
    re_oms = re.compile(
        r"^(?:one[-\s]minute\s+speeches|urgent\s+political\s+matters|(?:topical\s+and\s+)?urgent\s+debates?"
        r"|topical\s+and\s+urgent\s+subjects\s+of\s+major\s+importance)\b",
        re.IGNORECASE,
    )
    re_speaker_type_terms = re.compile(r"committee|president|author|commission",
                                       re.IGNORECASE | re.VERBOSE,)

    re_rapporteur = re.compile(r"^rap |rapporteur",
                               re.IGNORECASE | re.VERBOSE,)
    re_bluecard = re.compile(r"carton bleu|bluecard",
                             re.IGNORECASE | re.VERBOSE,)
    re_council = re.compile(r"conseil|council",
                            re.IGNORECASE | re.VERBOSE,)
    re_author = re.compile(r"auteur|author",
                           re.IGNORECASE | re.VERBOSE,)
    re_mep = re.compile(r"au\s+nom\s+du\s+groupe|on\s+behalf\s+of\s+the\s+.*group|.*chair\s+of\s+the\s+.*group",
                        re.IGNORECASE | re.VERBOSE,)

    re_president = re.compile(
        r"""
        \s*(?:
            president
            | (?:(?:el|il)\s+)?presidente
            | (?:(?:le|la)\s+)?présidente?
            | (?:(?:der|die)\s+)?präsident(?:in)?
            | przewodniczący
            | πρόεδρος
            | elnök
            | předseda
        )[\s.,:;–—−‐-]*
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    speaker_type_eval = {}
    debate_type_eval = {}
    speaker_name_eval = {}

    def __init__(self, verbose: bool):
        self.verbose = verbose
        self.speaker_type_eval = {}
        self.debate_type_eval = {}
        self.speaker_name_eval = {}
        self.debate_type_map = json.load(open(data / "debate-type-map.json"))
        self.speaker_type_map = json.load(open(data / "speaker-type-map.json"))
        self.mep_metadata = json.load(open(data / "mep-metadata.json"))
        self.mep_name_id_map = json.load(open(data / "mep-name-id-map.json"))
        self.last_name_to_id = {}
        for mep_id, mep in self.mep_metadata.items():
            last = ""
            for part in mep["name"].split(" "):
                if part.isupper():
                    last += f" {part}"
            self.last_name_to_id[last.strip().title()] = mep_id

    @contextmanager
    def _without_diagnostics(self):
        """Exclude secondary source-text reads from classification diagnostics."""
        previous = self.speaker_type_eval, self.debate_type_eval, self.speaker_name_eval
        self.speaker_type_eval, self.debate_type_eval, self.speaker_name_eval = {}, {}, {}
        try:
            yield
        finally:
            self.speaker_type_eval, self.debate_type_eval, self.speaker_name_eval = previous

    def _find_date_match(self, memberships: list) -> dict:
        for membership in memberships:
            if datetime.fromisoformat(membership["from"]) <= self.date <= datetime.fromisoformat(membership["to"]):
                return membership
        return {}
                        
    def _parse_speaker_info(self,
                            mep_id_field: str | None, 
                            name_field: str) -> Iterator[ep.Speaker]:
        # 1. If mep_ids are None, try to infer them - this also identifies ideosyncrasies of V1
        def _clean_names(name):
            name = name.strip(".").strip()
            name = name.split("(")[0].strip()
            return name

        if mep_id_field is None or mep_id_field == "NULL":
            if re.search(r".*,.* and .*", name_field):
                s1 = name_field.split(" and ")
                mep_names = [_clean_names(_) for _ in s1[0].split(",")]
                mep_names.append(_clean_names(s1[1]))
                mep_ids = [None for _ in mep_names]
            elif re.search(r".*,.* et .*", name_field):
                s1 = name_field.split(" et ")
                mep_names = [_clean_names(_) for _ in s1[0].split(",")]
                mep_names.append(_clean_names(s1[1]))
                mep_ids = [None for _ in mep_names]
            elif re.search(r" and ", name_field):
                s1 = name_field.split(" and ")
                mep_names = [_clean_names(s1[0]), _clean_names(s1[1])]
                mep_ids = [None for _ in mep_names]
            elif len(re.findall(r",", name_field)) >= 2:
                s1 = name_field.split(", ")
                mep_names = [_clean_names(_) for _ in s1]
                mep_ids = [None for _ in mep_names]
            else:
                mep_ids = [None]
                mep_names = [_clean_names(name_field)]

            for idx, name in enumerate(mep_names):
                mep_id = self.last_name_to_id.get(name.title(), "0")
                if mep_id == "0":
                    mep_id = self.mep_name_id_map.get(name, "0")
                    if mep_id == "0":
                        self.speaker_name_eval.setdefault("mep_id_inference_metadata", []).append([mep_id])
                    else:
                        self.speaker_name_eval.setdefault("mep_id_inference_failed", []).append([mep_id])
                else:
                    self.speaker_name_eval.setdefault("mep_id_inference_lastname", []).append([mep_id])
                mep_ids[idx] = mep_id

        # 2. If there are multiple meps, split them
        elif "-" in mep_id_field:
            mep_ids = mep_id_field.split(" - ")
            mep_names = [_clean_names(_) for _ in name_field.split(" - ")]
        else:
            mep_ids = [mep_id_field]
            mep_names = [_clean_names(name_field)]

        for mep_id, mep_name in zip(mep_ids, mep_names):
            # 3. If there are mep_ids, take the name + info from mep_metadata
            if mep_id != "0" and mep_id in self.mep_metadata:
                metadata = self.mep_metadata[mep_id]
                group = self._find_date_match(metadata["groups"])
                party = self._find_date_match(metadata["parties"])
                if party.get("ches_id", None):
                    self.speaker_name_eval.setdefault("mapped_w_ches", []).append((mep_id, metadata["name"]))
                elif party.get("name", None):
                    self.speaker_name_eval.setdefault("mapped_w_party", []).append((mep_id, metadata["name"]))
                else:
                    self.speaker_name_eval.setdefault("mapped_mep", []).append((mep_id, metadata["name"]))
                yield ep.Speaker(
                    mep_id=mep_id,
                    speaker_name=metadata["name"],
                    country=metadata["country"],
                    group=group.get("name", None),
                    group_code=group.get("code", None),
                    group_family=group.get("family", None),
                    party=party.get("name", None),
                    party_code=party.get("code", None),
                    ches_id=party.get("ches_id", None),
                    ches_family=party.get("ches_family", None)
                )
            else:
                # 4. Normalize the name when mep_id == 0
                if "|" in mep_name:
                    splits = mep_name.split("|")
                    mep_name = f"{splits[0].strip().title()} {splits[1].strip().upper()}"
                if "  " in mep_name:
                    splits = mep_name.split("  ")
                    mep_name = f"{splits[0].strip().title()} {splits[1].strip().upper()}"

                self.speaker_name_eval.setdefault("non_mep", []).append((mep_id, mep_name))
                yield ep.Speaker(
                    mep_id=mep_id,
                    speaker_name=mep_name,
                    country=None,
                    group=None,
                    group_code=None,
                    group_family=None,
                    party=None,
                    party_code=None,
                    ches_id=None,
                    ches_family=None,
                )

    def _parse_debate_type(self, debate_title: str, chapter_type: str) -> ep.DebateType:
        """Classify the chapter from its title and XML type, independently of its speakers."""
        dt = " ".join(debate_title.lower().split()).translate(str.maketrans("‐–−", "---"))
        dt = re.sub(r"^(?:\d+\s*[.\-]\s*)+", "", dt)
        dt = re.sub(r"(?:\s*:\s*see\s+minutes\.?)+$", "", dt).strip()
        if dt in self.debate_type_map:
            debate_type = ep.DebateType(self.debate_type_map[dt])
            self.debate_type_eval.setdefault("mapped_types", []).append(debate_type)
        else:
            if self.re_written.search(dt):
                debate_type = ep.DebateType.DEBATE
            elif self.re_questions.search(dt):
                debate_type = ep.DebateType.QUESTIONS
            elif self.re_procedural.search(dt):
                debate_type = ep.DebateType.PROCEDURAL
            elif self.re_formal.search(dt):
                debate_type = ep.DebateType.FORMAL
            elif chapter_type == "VOTE" or self.re_vote.search(dt):
                debate_type = ep.DebateType.VOTE
            elif self.re_oms.search(dt):
                debate_type = ep.DebateType.OMS
            elif self.re_debate.search(dt):
                debate_type = ep.DebateType.DEBATE
            else:
                debate_type = ep.DebateType.DEBATE
                self.debate_type_eval.setdefault("auto_debates", []).append(dt)
            self.debate_type_eval.setdefault("inferred_types", []).append(debate_type)
        self.debate_type_eval.setdefault("all_types", []).append(debate_type)
        return debate_type

    def _parse_speaker_type(self, type_field: str | None, mep_id_field: str | None) -> ep.SpeakerType | None:
        """Map an explicit role, or infer MEP from a report ID when no role is provided."""
        speaker_type = None
        if type_field and type_field.strip():
            _st = type_field.strip().lower()
            self.speaker_type_eval.setdefault("source_type", []).append(_st)
            if _st in self.speaker_type_map:
                speaker_type = self.speaker_type_map[_st]
                self.speaker_type_eval.setdefault("mapped_type", []).append(speaker_type)
                return speaker_type.lower()
            if re.search(self.re_rapporteur, _st):
                speaker_type = ep.SpeakerType.RAPPORTEUR
            elif re.search(self.re_bluecard, _st):
                speaker_type = ep.SpeakerType.BLUECARD
            elif re.search(self.re_council, _st):
                speaker_type = ep.SpeakerType.COUNCIL
            elif re.search(self.re_author, _st):
                speaker_type = ep.SpeakerType.AUTHOR
            elif re.search(self.re_mep, _st):
                speaker_type = ep.SpeakerType.MEP
            if speaker_type:
                self.speaker_type_eval.setdefault("inferred_type", []).append(speaker_type)
                return speaker_type
            self.speaker_type_eval.setdefault("not_mapped", []).append(_st)
        else:
            self.speaker_type_eval.setdefault("source_type", []).append(None)
            ids = re.split(r"\s*-\s*", (mep_id_field or "").strip())
            if any(mep_id.isdecimal() and int(mep_id) > 0 for mep_id in ids):
                self.speaker_type_eval.setdefault("inferred_type", []).append(ep.SpeakerType.MEP)
                return ep.SpeakerType.MEP

        return None

    def _intervention_speaker_type(self, orateur, type_field: str | None) -> ep.SpeakerType | None:
        """Identify chairs first, then use the explicit role or a known report MEP ID."""
        label = "".join(orateur.xpath("./EMPHAS[@NAME='B']//text()"))
        if self.re_president.fullmatch(label):
            return ep.SpeakerType.PROCEDURAL
        return self._parse_speaker_type(type_field, orateur.get("MEPID"))

    def _parse_para(self, paragraph) -> str:
        segments = []
        for text_block in paragraph.xpath("text()"):
            text_block = text_block.strip()
            if re.match(self.re_strip_leading_na, text_block):
                text_block = text_block[2:]
            
            # Skip interjections like applause
            if text_block.startswith("(") and text_block.endswith(")"):
                continue
            if len(text_block) < 5:
                continue
            segments.append(text_block)
        return " ".join(segments).strip()

    def _is_vote_explanation(self, chapter) -> bool:
        """Recognize dotted subchapters under an English 'Explanations of vote' heading."""
        number = chapter.get("NUMBER", "")
        if not re.fullmatch(r"\d+(?:\.\d+)+", number):
            return False
        container = chapter.getparent()
        if container is None:
            return False
        main_number = number.split(".", 1)[0]
        main = container.find(f"CHAPTER[@NUMBER='{main_number}']")
        if main is None:
            return False
        title = main.find("TL-CHAP[@VL='EN']")
        if title is None:
            return False
        text = " ".join("".join(title.itertext()).split())
        return re.match(r"^explanations?\s+of\s+votes?\b", text, re.IGNORECASE) is not None

    def _classify_chapter(self, chapter, speeches: list[ep.Speech]) -> list[ep.Speech]:
        """Classify vote-explanation subchapters, procedural chapters, and title-based debates."""
        if self._is_vote_explanation(chapter):
            debate_type = ep.DebateType.VOTE
        elif all(speech.speaker_type == ep.SpeakerType.PROCEDURAL for speech in speeches):
            debate_type = ep.DebateType.PROCEDURAL
        else:
            debate_type = None
        if debate_type is not None:
            self.debate_type_eval.setdefault("inferred_types", []).append(debate_type)
            self.debate_type_eval.setdefault("all_types", []).append(debate_type)
        else:
            title = chapter.find("TL-CHAP[@VL='EN']")
            if title is None:
                title = chapter.find("TL-CHAP")
            chapter_type = title.get("TYPE", "OTHER").strip() if title is not None else "OTHER"
            debate_title = re.sub(self.re_leading_number, "", "".join(title.itertext()).strip()) if title is not None else ""
            debate_type = self._parse_debate_type(debate_title, chapter_type)
        for speech in speeches:
            speech.debate_type = debate_type
        return speeches

    def _parse_chapter(self, chapter, idx: int) -> Iterator[ep.Speech]:
        """Parse REV speeches and classify the complete chapter."""
        yield from self._classify_chapter(chapter, list(self._read_chapter(chapter, idx)))

    def _read_chapter(self, chapter, idx: int, classify: bool = True) -> Iterator[ep.Speech]:
        """Read original REV speeches before assigning the chapter's debate type."""
        speech_idx = 0
        act_id = None
        numero_type = None
        title = chapter.find("TL-CHAP[@VL='EN']")
        debate_title = re.sub(self.re_leading_number, "", "".join(title.itertext()).strip()) if title is not None else ""
        for chapter_elem in chapter.iter("NUMERO", "INTERVENTION"):
            source_lang = None
            if chapter_elem.tag == "NUMERO":
                act_id = chapter_elem.attrib["ACT"]
                numero_type = chapter_elem.get("SPEAKER_TYPE")
                
            elif chapter_elem.tag == "INTERVENTION":
                para = []
                for intervention_elem in chapter_elem:
                    if intervention_elem.tag == "ORATEUR":
                        # Language Tags
                        source_lang = intervention_elem.attrib.get("LG", "").lower()
                        if not source_lang:
                            for child in intervention_elem:
                                if child.tag == "LG":
                                    source_lang = child.text
                                    break
                            else:
                                source_lang = None

                        group_field = intervention_elem.attrib["PP"].strip()
                        mep_id_field = intervention_elem.attrib["MEPID"].strip()
                        speaker = list(self._parse_speaker_info(
                            mep_id_field,
                            intervention_elem.attrib["LIB"].strip()
                        ))

                        type_field = (intervention_elem.get("SPEAKER_TYPE")
                                      or chapter_elem.get("SPEAKER_TYPE") or numero_type)
                        speaker_type = self._intervention_speaker_type(intervention_elem, type_field) if classify else None

                    elif intervention_elem.tag == "PARA":
                        text = self._parse_para(intervention_elem)
                        if text:
                            para.append(text)

                if len(para) == 0:
                    act_id = None
                    numero_type = None
                    continue
                
                speech = ep.Speech(
                    id=f"{self.date.strftime('%y%m%d')}-{idx}-{speech_idx}",
                    date=datetime.isoformat(self.date),
                    act_id=act_id,
                    source_lang=source_lang.lower().strip().strip("()") if source_lang else None,
                    speaker_type=speaker_type,
                    debate_title=debate_title,
                    debate_type=None,
                    translation=ep.TranslationType.MISSING,
                    speaker=speaker,
                    paragraphs=para
                    )
                if speech.source_lang == "en":
                    speech.paragraphs_en = para
                    speech.translation = ep.TranslationType.SOURCE
                speech.words = sum(len(p.split()) for p in speech.paragraphs_en)

                act_id = None
                numero_type = None
                speech_idx += 1
                yield speech

    @staticmethod
    def _clean_legacy_field(text: str) -> str:
        """Trim header punctuation and normalize whitespace without changing names or codes."""
        return re.sub(r"^[\W_]+|[\W_]+$", "", " ".join(text.split()))

    def _parse_legacy_header(self, person) -> tuple[str, str | None, str | None, str | None]:
        """Extract the adjacent speaker header, clearing its text while preserving speech tails."""
        name = self._clean_legacy_field("".join(person.itertext()))
        group = language = speaker_type = None
        header = [person]
        element = person
        while not any(char.isalnum() for char in element.tail or ""):
            element = element.getnext()
            if element is None or element.tag != "EMPHAS":
                break
            text = " ".join(element.itertext()).strip()
            style = element.get("NAME")
            clean = self._clean_legacy_field(text)
            if style == "B":
                bracketed = re.fullmatch(r"\s*\(([^()]*)\)[\W_]*", text)
                if bracketed:
                    group = self._clean_legacy_field(bracketed.group(1)) or None
                elif clean:
                    break
            elif style in {"I", "U"}:
                code = re.search(r"\(([A-Za-z]{2})\)", text)
                role = self._clean_legacy_field(text[:code.start()] + text[code.end():] if code else text)
                role_patterns = (self.re_speaker_type_terms, self.re_rapporteur, self.re_bluecard,
                                 self.re_council, self.re_author, self.re_mep)
                if not code and role and role.lower() not in self.speaker_type_map and not any(
                    pattern.search(role.lower()) for pattern in role_patterns
                ):
                    break
                if code:
                    language = code.group(1).lower()
                if role:
                    speaker_type = role
            else:
                break
            header.append(element)

        for element in header:
            tail = element.tail
            element.clear()
            element.tail = tail
        return name, group, language, speaker_type

    def _parse_legacy_chapter(self, chapter, idx: int) -> Iterator[ep.Speech]:
        """Parse PERSON-based speeches and classify the complete chapter."""
        yield from self._classify_chapter(chapter, list(self._read_legacy_chapter(chapter, idx)))

    def _read_legacy_chapter(self, chapter, idx: int, classify: bool = True) -> Iterator[ep.Speech]:
        """Read PERSON-based FNL chapters, preserving speaker positions for alignment."""
        title = chapter.find("TL-CHAP")
        debate_title = re.sub(self.re_leading_number, "", "".join(title.itertext()).strip()) if title is not None else ""
        document_lang = title.get("VL", "").lower() if title is not None else None
        speech_idx = -1
        speaker_name = None
        speaker_type = None
        source_lang = document_lang
        group_field = None
        paragraphs = []

        def finish_speech():
            """Build a completed speech, retaining chair interventions as procedural speakers."""
            if not paragraphs or not speaker_name:
                return None
            speakers = list(self._parse_speaker_info(None, speaker_name))
            for speaker in speakers:
                if group_field:
                    if speaker.group is None:
                        speaker.group = group_field
                    if speaker.group_code is None:
                        speaker.group_code = group_field
            if not classify:
                parsed_type = None
            elif self.re_president.fullmatch(speaker_name):
                parsed_type = ep.SpeakerType.PROCEDURAL
            else:
                parsed_type = self._parse_speaker_type(speaker_type, None)
                groups = [group_field] + [speaker.group_code or speaker.group for speaker in speakers]
                has_group = any(group and group.strip().upper() not in {"NONE", "NULL", "0"} for group in groups)
                if parsed_type is None and self.date != v1_date and has_group:
                    parsed_type = ep.SpeakerType.MEP
            return ep.Speech(
                id=f"{self.date.strftime('%y%m%d')}-{idx}-{speech_idx}",
                date=self.date.isoformat(),
                act_id=None,
                source_lang=source_lang.lower().strip().strip("()") if source_lang else None,
                speaker_type=parsed_type,
                debate_title=debate_title,
                debate_type=None,
                speaker=speakers,
                paragraphs=paragraphs,
            )

        for original in chapter.iter("PARA"):
            paragraph = deepcopy(original)
            person = paragraph.find(".//PERSON")
            if person is not None:
                speech = finish_speech()
                if speech is not None:
                    yield speech
                speech_idx += 1
                paragraphs = []
                speaker_name, group_field, language, speaker_type = self._parse_legacy_header(person)
                source_lang = language or (None if speaker_type else document_lang)
            text = "".join(paragraph.itertext())
            paragraph.clear()
            paragraph.text = text
            text = self._parse_para(paragraph)
            if person is not None:
                text = re.sub(r"^[\W_]+", "", text)
            text = " ".join(text.split())
            if text:
                paragraphs.append(text)

        speech = finish_speech()
        if speech is not None:
            yield speech

    def _chapter_speeches(self, chapter, idx: int) -> Iterator[ep.Speech]:
        """Read either INTERVENTION-based or older PERSON-based XML."""
        if chapter.find(".//INTERVENTION") is not None:
            yield from self._parse_chapter(chapter, idx)
        else:
            yield from self._parse_legacy_chapter(chapter, idx)

    @staticmethod
    def _speech_key(speech: ep.Speech, chapter, idx: int) -> tuple:
        """Align by ACT where available, otherwise by chapter number and speaker position."""
        if speech.act_id:
            return ("act", speech.act_id)
        return ("position", chapter.get("NUMBER", str(idx)), speech.id.rsplit("-", 1)[-1])

    def _index_report(self, root) -> dict[tuple, list[str]]:
        """Index original-language text without classifying it or recording diagnostics."""
        aligned = {}
        with self._without_diagnostics():
            for idx, chapter in enumerate(root.findall(".//DEBATS/CHAPTER")):
                reader = self._read_chapter if chapter.find(".//INTERVENTION") is not None else self._read_legacy_chapter
                for speech in reader(chapter, idx, classify=False):
                    aligned[self._speech_key(speech, chapter, idx)] = speech.paragraphs
        return aligned

    def _index_english_report(self, root) -> tuple[dict, dict]:
        """Classify an English report once and align its speeches by ACT and chapters by number."""
        speeches = {}
        chapters = {}
        for idx, chapter in enumerate(root.findall(".//DEBATS/CHAPTER")):
            parsed = list(self._chapter_speeches(chapter, idx))
            title = chapter.find("TL-CHAP[@VL='EN']")
            if title is None:
                title = chapter.find("TL-CHAP")
            debate_title = re.sub(self.re_leading_number, "", "".join(title.itertext()).strip()) if title is not None else ""
            debate_type = parsed[0].debate_type if parsed else ep.DebateType.PROCEDURAL
            chapters[chapter.get("NUMBER", str(idx))] = (debate_title, debate_type)
            for speech in parsed:
                speeches[self._speech_key(speech, chapter, idx)] = speech
        return speeches, chapters

    def _parse_chapter_multilingual(self, chapter, idx: int, versions: dict) -> Iterator[ep.Speech]:
        """Use English FNL metadata and recover original text from aligned language versions."""
        for speech in self._chapter_speeches(chapter, idx):
            speech.paragraphs_en = speech.paragraphs
            if speech.source_lang == "en":
                speech.translation = ep.TranslationType.SOURCE
            else:
                original = versions.get(speech.source_lang, {}).get(self._speech_key(speech, chapter, idx))
                speech.paragraphs = original or []
                speech.translation = ep.TranslationType.EP
            speech.words = sum(len(p.split()) for p in speech.paragraphs_en)
            yield speech

    def _parse_chapter_translations(self, chapter, idx: int, english: dict, chapters: dict) -> Iterator[ep.Speech]:
        """Retain REV originals and source language, taking types, titles, and translations from English FNL."""
        with self._without_diagnostics():
            originals = list(self._read_chapter(chapter, idx, classify=False))
        debate_title, debate_type = chapters.get(chapter.get("NUMBER", str(idx)), ("", None))
        for speech in originals:
            translated = english.get(("act", speech.act_id)) if speech.act_id else None
            speech.debate_title = translated.debate_title if translated else debate_title
            speech.debate_type = translated.debate_type if translated else debate_type
            speech.speaker_type = translated.speaker_type if translated else None
            if speech.source_lang != "en" and translated:
                speech.paragraphs_en = translated.paragraphs
                speech.translation = ep.TranslationType.EP
            speech.words = sum(len(p.split()) for p in speech.paragraphs_en)
            yield speech

    def eval(self):
        """
        speaker_types (all): 450622 (unqiue 887)
        speaker_types (via mapped_types): 362829 (Counter({'ecrit': 252711, 'mep': 46314, 'commission': 23797, 'rapporteur': 17323, 'bluecard': 9242, 'council': 7392, 'author': 5531, 'guest': 519})))
        speaker_types (via regex): 787 Counter({<SpeakerType.COUNCIL: 'council'>: 572, <SpeakerType.RAPPORTEUR: 'rapporteur'>: 114, <SpeakerType.AUTHOR: 'author'>: 55, <SpeakerType.MEP: 'mep'>: 46}))
        speaker_types (not_mapped): 5043 (unqiue 222))
        debate_type_eval (all): 47262 (unique 6)
        debate_type_eval (mapped): 2020 (Counter({<DebateType.PROCEDURAL: 'procedural'>: 1314, <DebateType.QUESTIONS: 'questions'>: 250, <DebateType.VOTE: 'vote'>: 200, <DebateType.DEBATE: 'debate'>: 141, <DebateType.FORMAL: 'formal'>:
        70, <DebateType.OMS: 'oms'>: 45}))
        debate_type_eval (via regex): 39492 (Counter({<DebateType.PROCEDURAL: 'procedural'>: 26048, <DebateType.DEBATE: 'debate'>: 12115, <DebateType.VOTE: 'vote'>: 6518, <DebateType.OMS: 'oms'>: 360, <DebateType.FORMAL: 'formal'>: 160,
        <DebateType.QUESTIONS: 'questions'>: 41}))
        debate_type_eval (debate by default): 5750 (unique 4982))
        speaker_names - mep_id_inference_metadata 11501 (42402 total)
        speaker_names - mep_id_inference_lastname 25047
        speaker_names - mep_id_inference_failed 5854
        speaker_names - meps 2765
        speaker_names - w_party 70286
        speaker_names - w_ches 544310
        speaker_names - non_mep 42554
        """
        # EVAL speaker types
        types = self.speaker_type_eval.get("source_type", [])
        json.dump(Counter(types).most_common(), open(generated / "frequent_speaker_types.json", 'w'), ensure_ascii=False, indent=4)
        mapped = self.speaker_type_eval.get("mapped_type", [])
        inferred = self.speaker_type_eval.get("inferred_type", [])
        not_mapped = self.speaker_type_eval.get("not_mapped", [])
        logger.info(f"speaker_types (all): {len(types)} (unqiue {len(set(types))})")
        logger.info(f"speaker_types (via mapped_types): {len(mapped)} ({Counter(mapped)}))")
        logger.info(f"speaker_types (via regex): {len(inferred)} {Counter(inferred)})")
        logger.info(f"speaker_types (not_mapped): {len(not_mapped)} (unqiue {len(set(not_mapped))}))")
        json.dump(not_mapped, open(generated / "speaker-type-not-mapped.json", 'w'), ensure_ascii=False, indent=4)

        # EVAL debate types
        types = self.debate_type_eval.get("all_types", [])
        inferred = self.debate_type_eval.get("inferred_types", [])
        mapped = self.debate_type_eval.get("mapped_types", [])
        auto = self.debate_type_eval.get("auto_debates", [])
        logger.info(f"debate_type_eval (all): {len(types)} (unique {len(set(types))})")
        logger.info(f"debate_type_eval (mapped): {len(mapped)} ({Counter(mapped)})")
        logger.info(f"debate_type_eval (via regex): {len(inferred) - len(auto)} ({Counter(inferred)})")
        logger.info(f"debate_type_eval (debate by default): {len(auto)} (unique {len(set(auto))}))")
        c = {a: b for a, b in Counter([t.strip().lower() for t in types]).most_common()}
        json.dump(c, open(generated / "frequent_debates.json", 'w'), ensure_ascii=False, indent=4)
        auto = list(set(auto))
        json.dump(auto, open(generated / "debate_types_auto_mapped.json", 'w'), ensure_ascii=False, indent=4)

        # EVAL speaker name mapping 
        logger.info(f"speaker_names - mep_id_inference_metadata {len(self.speaker_name_eval.get('mep_id_inference_metadata', []))}")
        logger.info(f"speaker_names - mep_id_inference_lastname {len(self.speaker_name_eval.get('mep_id_inference_lastname', []))}")
        logger.info(f"speaker_names - mep_id_inference_failed {len(self.speaker_name_eval.get('mep_id_inference_failed', []))}")

        mapped_mep = self.speaker_name_eval.get("mapped_mep", [])
        mapped_w_party = self.speaker_name_eval.get("mapped_w_party", [])
        mapped_w_ches = self.speaker_name_eval.get("mapped_w_ches", [])
        non_mep = self.speaker_name_eval.get("non_mep", [])
        logger.info(f"speaker_names - meps {len(mapped_mep)}")
        logger.info(f"speaker_names - w_party {len(mapped_w_party)}")
        logger.info(f"speaker_names - w_ches {len(mapped_w_ches)}")
        logger.info(f"speaker_names - non_mep {len(non_mep)}")

    def __call__(self, xml_path: Path, newest: datetime | None = None) -> Iterator[ep.VerbatimReport]:
        """Dispatch flat REV files and report directories to the appropriate chapter parser."""
        logger.info(f"Extracting new report sources, newest: {newest}", show=self.verbose)
        sources = sorted(path for path in xml_path.iterdir() if path.is_dir() or path.suffix.lower() == ".xml")
        for source in tqdm(sources, desc="Reports"):
            files = sorted(source.glob("*.xml")) if source.is_dir() else [source]
            revision = None
            finals = {}
            for path in files:
                if path.name.lower().endswith("rev.xml"):
                    revision = path
                else:
                    match = re.search(r"fnl_([a-z]{2})\.xml$", path.name, re.IGNORECASE)
                    if match:
                        finals[match.group(1).lower()] = path
            primary = revision if revision is not None else finals.get("en")
            if primary is None:
                continue
            if source.is_file() and revision is not None:
                report_dir = xml_path / re.sub(r"-?REV\.xml$", "", source.name, flags=re.IGNORECASE)
                if (report_dir / source.name).exists():
                    continue

            root = etree.parse(str(primary)).getroot()
            self.date = datetime.strptime(root.find(".//HEAD/META").text, "%d-%m-%Y")
            if newest and self.date.date() < newest.date():
                continue

            english = english_chapters = {}
            versions = {}
            if revision is not None and "en" in finals:
                english_root = etree.parse(str(finals["en"])).getroot()
                english, english_chapters = self._index_english_report(english_root)
            elif revision is None:
                versions = {
                    lang: self._index_report(etree.parse(str(path)).getroot())
                    for lang, path in finals.items() if lang != "en"
                }
            speeches = []
            for idx, chapter in enumerate(root.findall(".//DEBATS/CHAPTER")):
                if revision is None:
                    speeches.extend(self._parse_chapter_multilingual(chapter, idx, versions))
                elif "en" in finals:
                    speeches.extend(self._parse_chapter_translations(chapter, idx, english, english_chapters))
                else:
                    speeches.extend(self._parse_chapter(chapter, idx))
            yield ep.VerbatimReport(self.date, speeches)
