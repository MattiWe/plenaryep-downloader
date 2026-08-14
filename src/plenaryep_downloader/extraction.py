from __future__ import annotations

from pathlib import Path
from typing import Iterator
from datetime import datetime
from collections import Counter
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
    re_procedural = re.compile(r"Approval of the .inutes|Order of .usiness|Resumption of the session|Adjournment of the session|Agenda|Resumption of session|Decision on urgency")
    re_questions = re.compile(r"question time")
    re_oms = re.compile(r"[Oo]ne.[Mm]inute [Ss]peeches|[uU]rgent [Dd]ebate")
    re_speaker_type_terms = re.compile(r"committee|president|author|commission")

    re_rapporteur = re.compile(r"^rap |rapporteur")
    re_bluecard = re.compile(r"carton bleu")
    re_council = re.compile(r"conseil|council")
    re_author = re.compile(r"auteur|author")
    re_mep = re.compile(r"au nom du groupe|.*chair of the .* group")

    speaker_type_eval = {}
    debate_type_eval = {}
    speaker_name_eval = {}

    def __init__(self, verbose: bool):
        self.verbose = verbose
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

    def _parse_debate_type(self, 
                           debate_title: str, 
                           chapter_type: str, 
                           speaker_type: str, 
                           group: str) -> ep.DebateType:
        """ Map the debate type extracted form the proceedings to the corpus taxonomy """
        debate_type = None
        dt = debate_title.strip().lower()
        if dt in self.debate_type_map.keys():
            debate_type = self.debate_type_map[dt]
            self.debate_type_eval.setdefault("mapped_types", []).append(debate_type)
            return debate_type
        elif chapter_type == "VOTE" or re.search(self.re_vote, dt):
            debate_type = ep.DebateType.VOTE
        elif re.search(self.re_debate, dt):
            debate_type = ep.DebateType.DEBATE
        elif re.search(self.re_oms, dt):
            debate_type = ep.DebateType.OMS
        elif re.search(self.re_questions, dt):
            debate_type = ep.DebateType.QUESTIONS
        elif group == "NULL" and speaker_type is None:
            debate_type = ep.DebateType.PROCEDURAL

        if debate_type:
            self.debate_type_eval.setdefault("inferred_types", []).append(debate_type)
        else:
            debate_type = ep.DebateType.DEBATE
            self.debate_type_eval.setdefault("auto_debates", []).append(debate_title)
        self.debate_type_eval.setdefault("all_types", []).append(debate_type)
        return debate_type
        
    def _parse_speaker_type(self, type_field: str, mep_id_field: str) -> ep.SpeakerType | None:
        """ Map the speaker type extracted form the proceedings to the corpus taxonomy """
        speaker_type = None
        if type_field:
            _st = type_field.strip().lower()
            self.speaker_type_eval.setdefault("source_type", []).append(_st)
            if type_field in self.speaker_type_map:
                speaker_type = self.speaker_type_map[type_field]
                self.speaker_type_eval.setdefault("mapped_type", []).append(speaker_type)
                return speaker_type
            if re.search(self.re_rapporteur, type_field.lower()):
                speaker_type = ep.SpeakerType.RAPPORTEUR
            elif re.search(self.re_bluecard, type_field.lower()):
                speaker_type = ep.SpeakerType.BLUECARD
            elif re.search(self.re_council, type_field.lower()):
                speaker_type = ep.SpeakerType.COUNCIL
            elif re.search(self.re_author, type_field.lower()):
                speaker_type = ep.SpeakerType.AUTHOR
            elif re.search(self.re_mep, type_field.lower()):
                speaker_type = ep.SpeakerType.MEP
            if speaker_type:
                self.speaker_type_eval.setdefault("inferred_type", []).append(speaker_type)
                return speaker_type
            if not mep_id_field == "NULL" and not mep_id_field is None and not mep_id_field == "0":
                speaker_type = ep.SpeakerType.MEP
                self.speaker_type_eval.setdefault("inferred_type", []).append(speaker_type)
                return speaker_type
            self.speaker_type_eval.setdefault("not_mapped", []).append(type_field)
        else:
            self.speaker_type_eval.setdefault("source_type", []).append(None)

        return speaker_type

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

    def _parse_chapter(self, chapter, translated: bool) -> Iterator[ep.Speech]:
        speech_idx = 0
        act_id = None
        for chapter_elem in chapter.iter("NUMERO", "INTERVENTION", "TL-CHAP"):
            source_lang = None
            chapter_type = None
            debate_title = ""
            if chapter_elem.tag == "TL-CHAP" and chapter_elem.attrib["VL"] == "EN":
                chapter_type = chapter_elem.attrib.get("TYPE", "OTHER")
                chapter_type = chapter_type.strip()

                debate_title = "".join(chapter_elem.xpath(".//text()")).strip()
                debate_title = re.sub(self.re_leading_number, "", debate_title)

            elif chapter_elem.tag == "NUMERO":
                act_id = chapter_elem.attrib["ACT"]
                
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

                        translation = None
                        if not source_lang == "en" and translated:
                            translation = ep.Translation.EP
                        elif source_lang == "en":
                            translation = ep.Translation.SOURCE
                        group_field = intervention_elem.attrib["PP"].strip()
                        mep_id_field = intervention_elem.attrib["MEPID"].strip()
                        speaker = list(self._parse_speaker_info(
                            mep_id_field,
                            intervention_elem.attrib["LIB"].strip()
                        ))

                        speaker_type = self._parse_speaker_type(
                            intervention_elem.attrib.get("SPEAKER_TYPE", None),
                            mep_id_field)

                    elif intervention_elem.tag == "PARA":
                        text = self._parse_para(intervention_elem)
                        if text:
                            para.append(text)

                if len(para) == 0:
                    continue
                
                debate_type = self._parse_debate_type(
                    debate_title, chapter_type, speaker_type, group_field)

                words = sum([len(p.split(" ")) for p in para])
                speech = ep.Speech(
                    id=f"{self.date.strftime('%y%m%d')}{speech_idx}",
                    date=datetime.isoformat(self.date),
                    act_id=act_id,
                    source_lang=source_lang if source_lang else None,
                    speaker_type=speaker_type,
                    debate_title=debate_title,
                    debate_type=debate_type,
                    translation=translation,
                    speaker=speaker,
                    words=words,
                    paragraphs=para
                    )
                    
                act_id = None
                speech_idx += 1
                yield speech

    def _parse_old_chapter(self, chapter, translated: bool) -> Iterator[ep.Speech]:
        debate_type = ep.DebateType.OTHER
        para = []
        source_lang = None
        speaker_name = None
        speaker_type = None
        translation = ep.Translation.EP
        group_field = None
        debate_title = ""
        chapter_type = "OTHER"

        speech_idx = 0
        for chapter_elem in chapter.iter("PARA", "TL-CHAP"):
            if chapter_elem.tag == "TL-CHAP":
                source_lang = chapter_elem.attrib["VL"]
                chapter_type = chapter_elem.attrib.get("TYPE", "OTHER").strip()
                debate_title = "".join(chapter_elem.xpath(".//text()")).strip()
                debate_title = re.sub(self.re_leading_number, "", debate_title)

            elif chapter_elem.tag == "PARA":
                # 
                if len(chapter_elem) > 1:
                    for elem in chapter_elem.iter("PERSON"):
                        # This is a new person speaking from here on, so the old one is finished. We yield the old one and override the rest. 
                        if para and \
                            speaker_name and \
                            speaker_name != "President" and \
                            len(para) != 0:
                            debate_type = self._parse_debate_type(
                                debate_title, chapter_type, speaker_type, group_field)
                            
                            speaker = list(self._parse_speaker_info(
                                None,
                                speaker_name
                            ))
                            words = sum([len(p.split(" ")) for p in para])
                            if source_lang and source_lang.lower() == 'en':
                                translation = ep.Translation.SOURCE

                            speech = ep.Speech(
                                id=f"{self.date.strftime('%y%m%d')}{speech_idx}",
                                date=datetime.isoformat(self.date),
                                act_id=None,
                                source_lang=source_lang.lower() if source_lang else None,
                                speaker_type=speaker_type.title() if speaker_type else None,
                                debate_title=debate_title,
                                debate_type=debate_type,
                                translation=translation,
                                speaker=speaker,
                                words=words,
                                paragraphs=para
                                )

                            speech_idx += 1
                            yield speech

                        para = []
                        speaker_type = None
                        group_field = None
                        speaker_name = "".join(elem.xpath(".//text()")).strip("(),. ")
                    for elem in chapter_elem.iter("EMPHAS"):
                        if not elem.text:
                            continue  # Empty EMPHAS happens sometimes
                        if not speaker_name:
                            continue
                        _text = elem.text.strip()
                        if elem.attrib["NAME"] == "B":
                            group_field = "".join(elem.xpath(".//text()")).strip("(),. ")
                        if elem.attrib["NAME"] == "U":
                            if not _text.startswith("(") and not _text.endswith(")"):
                                speaker_type = _text.strip(",.? ")
                                source_lang = None
                            elif not _text.startswith("(") and _text.endswith(")"):  # Speaker type is inserted here
                                _ = "".join(elem.xpath(".//text()")).strip(",. ")
                                _ = _.split(" ? ")
                                if len(_) < 2:
                                    continue
                                speaker_type = _[0].strip(",. ")
                                source_lang = _[1].strip("(). ")
                                translation = ep.Translation.SOURCE
                                if source_lang.lower() != "en":
                                    translation = ep.Translation.EP
                            elif len(_text) < 5:
                                source_lang = "".join(elem.xpath(".//text()")).strip("(),. ")
                                translation = ep.Translation.SOURCE
                                if source_lang.lower() != "en":
                                    translation = ep.Translation.EP
                            else:
                                speaker_type = "".join(elem.xpath(".//text()")).strip("(),. ")
                                translation = ep.Translation.EP
                                source_lang = None
                        if elem.attrib["NAME"] == "I":
                            text = self._parse_para(elem)
                            if text:
                                para.append(text)
                else:
                    p = self._parse_para(chapter_elem)
                    if p:
                        para.append(p)

    def eval(self):
        # EVAL speaker types
        types = self.speaker_type_eval.get("source_type", [])
        mapped = self.speaker_type_eval.get("mapped_type", [])
        inferred = self.speaker_type_eval.get("inferred_type", [])
        not_mapped = self.speaker_type_eval.get("not_mapped", [])
        logger.info(f"speaker_types (all): {len(set(types))} ({len(types)})")
        logger.info(f"speaker_types (mapped): {len(set(mapped))} ({len(mapped)})")
        logger.info(f"speaker_types (inferred): {len(set(inferred))} ({len(inferred)})")
        logger.info(f"speaker_types (not_mapped): {len(set(not_mapped))} ({len(not_mapped)})")
        json.dump(not_mapped, open(generated / "speaker-type-not-mapped.json", 'w'), ensure_ascii=False, indent=4)

        # EVAL debate types
        types = self.debate_type_eval.get("all_types", [])
        inferred = self.debate_type_eval.get("inferred_types", [])
        mapped = self.debate_type_eval.get("mapped_types", [])
        logger.info(f"debate_type_eval (all): {len(set(types))} ({len(types)})")
        logger.info(f"debate_type_eval (mapped): {len(set(mapped))} ({len(mapped)})")
        logger.info(f"debate_type_eval (inferred): {len(set(inferred))} ({len(inferred)})")
        c = {a: b for a, b in Counter([t.strip().lower() for t in types]).most_common()}
        json.dump(c, open(generated / "frequent_debates.json", 'w'), ensure_ascii=False, indent=4)
        auto = self.debate_type_eval["auto_debates"]
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
        """Parse a REDMAP XML file and return a structured representation.
        Function for the v1 of the format, which was delivered in multiple languages and lacks speaker metadata
        - excludes annexes 
        """
        logger.info(f"Extracting new report sources, newest: {newest}", show=self.verbose)
        sources = xml_path.glob("*.xml")
        for source in tqdm(list(sources), desc="Reports", total=len(list(sources))):
            translated_file = True if source.stem.endswith("_en") else False
            root = etree.parse(open(source)).getroot()
            metas = root.findall(".//HEAD/META")
            self.date = datetime.strptime(metas[0].text, "%d-%m-%Y")
            if newest and self.date < newest:
                continue
                
            version = 4
            if self.date <= v1_date:
                version = 1
            elif self.date <= v2_date:
                version = 2
            elif self.date <= v3_date:
                version = 3
            
            speeches = []
            chapters = list(root.iter("DEBATS"))[0]

            # Other types are "ANNEX" and "RH", which we skip as those are not debates
            for chapter in chapters.iter("CHAPTER"):  
                _parser = self._parse_chapter if version > 1 else self._parse_old_chapter
                for speech in _parser(chapter, translated=translated_file):
                    speeches.append(speech)
                
            yield ep.VerbatimReport(self.date, speeches)
