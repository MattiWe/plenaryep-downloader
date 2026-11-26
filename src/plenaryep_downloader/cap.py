# from sentence_transformers import CrossEncoder
import json
from pathlib import Path
from typing import Iterable

from wasabi import msg as logger
from openai import OpenAI
from jinja2 import Template
from tqdm.auto import tqdm
from sentence_transformers import CrossEncoder

import plenaryep_downloader.reports as ep

data = (Path(__file__).parents[2] / "data").absolute()
generated = (Path(__file__).parents[2] / "generated").absolute()


class CapClassifier(object):
    def __init__(self, 
                 verbose: bool, 
                 server: str, 
                 token: str, 
                 rank: bool,
                 api_checkpoint: str = "Qwen/Qwen3.5-27B") -> None:
        self.verbose = verbose
        self.rank = rank
        self.debate_dataset = {}
        self.cap = json.load(open(data / "cap.json"))
        if rank:
            self.thresholds_file = data / "cap-thresholds.json"
            self.thresholds = json.load(open(self.thresholds_file))
            self.ranking_model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2", max_length=512)
        self.api_checkpoint = api_checkpoint
        template_path = Path(__file__).parent / "templates/"
        self.template_titles_coarse = Template(open(template_path / "cap-titles-coarse.jinja2", 'r').read())
        self.template_titles_detailed = Template(open(template_path / "cap-titles-detailed.jinja2", 'r').read())
        self.template_multilabel = Template(open(template_path / "cap-multilabel.jinja2", 'r').read())
        self.client = OpenAI(
            base_url=f"{server}/v1",
            api_key=token,
        )

    def _cap_name(self, cap_ids: Iterable):
        for cid in cap_ids:
            if cid == "0":
                continue 
            if cid not in self.cap:
                print(f"impossible code found: {cid}")
                continue
            name = f"{self.cap[cid]['area']}-{self.cap[cid]['topic']}"
            yield name

    def _classify_by_debate(self, speech: ep.Speech) -> Iterable[str]:
        # If debate was classified, find its classes and return
        if "cap" in self.debate_dataset[speech.debate_title]:
            return self.debate_dataset[speech.debate_title]["cap"]
        # otherwise, classify debate-level classes, add to debates, then return 

        representative_speech = self.debate_dataset[speech.debate_title]
        content = self.template_titles_coarse.render(
            title=speech.debate_title,
            speech="\n\n".join(representative_speech.paragraphs_en))
        messages = [{"role": "user", "content": content}]
                
        completion = self.client.responses.create(
            model=self.api_checkpoint,
            input=messages,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": True},
            },
        )
        output = completion.output_text.lower().strip(".")
        prediction_coarse = [_.strip() for _ in output.split(',') if _.strip() in self.cap.keys()]

        # given the previous predictions, make fine-granular classification
        topics = [{"code": _id, **topic} for _id, topic in self.cap.items() 
                if (str(int(_id[:-2]) * 100)) in prediction_coarse]

        content = self.template_titles_detailed.render(
            title=speech.debate_title,
            speech="\n\n".join(representative_speech.paragraphs_en),
            topics=topics)
        messages = [{"role": "user", "content": content}]
                
        completion = self.client.responses.create(
            model=self.api_checkpoint,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": True}
            },
            input=messages,)
        output = completion.output_text.lower().strip(".")
        prediction_fine = [_.strip() for _ in output.split(',') if _.strip() in self.cap.keys()]
        
        self.debate_dataset[speech.debate_title]["cap"] = prediction_fine
        return prediction_fine

    def _classify_by_multilabel(self, 
                                speech: ep.Speech, 
                                topics: Iterable|None = None) -> Iterable[str]:
        if not topics:
            topics = [{"code": _id, **topic} for _id, topic in self.cap.items()]
        
        content = self.template_multilabel.render(
                title=speech.debate_title,
                speech="\n\n".join(speech.paragraphs_en),
                topics=topics)
        messages = [{"role": "user", "content": content}]
        completion = self.client.responses.create(
            model=self.api_checkpoint,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": True},
            },
            input=messages)
        classes = completion.output_text.lower().strip(".")
        classes = [_.strip() for _ in classes.split(',') if _.strip() in self.cap.keys()]

        return classes
    
    def _classify_by_rank(self, speech: ep.Speech) -> Iterable[str]:
        # Identify CAP IDs there the speech surpasses the threshold
        rank_topics = []
        for cid, _cap in self.cap.items():
            query = f"{_cap["area"]} {_cap["topic"]} {_cap['description']}"

            examples = [(query, _) for _ in speech.paragraphs_en]
            _scores = self.ranking_model.predict(examples)

            # if max pooled score > minimum threshold: classify
            if max(_scores) > self.thresholds[cid]:
                rank_topics.append(cid)
        rank_topics = [{"code": _id, **topic} for _id, topic in self.cap.items() 
                        if _id in rank_topics]
        
        if len(rank_topics) == 0:
            return []
        return self._classify_by_multilabel(speech, topics=rank_topics)
    
    def __call__(self, report: ep.VerbatimReport) -> ep.VerbatimReport | None:
        # Determine if the report was already classified
        for speech in report.speeches:
            if speech.cap or speech.cap_debate:
                logger.info(f"Found cap topics for report {report.date}, skipping", 
                            show=self.verbose)
                return None

        # Make debate-title grouping
        logger.info("Find debate titles to classify")
        _debates = {}
        self.debate_dataset = {}
        for speech in report.speeches:
            if speech.debate_type != ep.DebateType.DEBATE:
                continue
            _debates.setdefault(speech.debate_title, []).append(speech)

        for title, debate in _debates.items():
            if title in self.debate_dataset:
                continue
            for speech in debate:
                if speech.speaker_type == "rapporteur":
                    self.debate_dataset[title] = {"speech": "\n\n".join(speech.paragraphs_en)}
                    break
            else:
                self.debate_dataset[title] = {"speech": "\n\n".join(debate[0].paragraphs_en)}

        # Classify speeches
        for speech in tqdm(report.speeches, desc="speeches", total=len(report.speeches)):
            # Skip short or empty speeches
            if len(speech.paragraphs_en) == 1 and len(speech.paragraphs_en[0]) < 100:
                continue
            if len(speech.paragraphs_en) == 0:
                logger.fail(f"Empy paragraphs_en for speech {speech.id}")
                continue

            # decide if debate or not-debate and classify accordingly
            if speech.debate_type == ep.DebateType.DEBATE:
                _ids = self._classify_by_debate(speech)
                speech.cap_debate_id = list(_ids)
                speech.cap_debate = list(self._cap_name(_ids))
            elif self.rank:
                _ids = self._classify_by_rank(speech)
            else:
                _ids = self._classify_by_multilabel(speech)
            speech.cap_id = list(_ids)
            speech.cap = list(self._cap_name(_ids))

        return report
