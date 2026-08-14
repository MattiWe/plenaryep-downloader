# from sentence_transformers import CrossEncoder
import logging
import json
from pathlib import Path
import random
from typing import Any, Iterable

from wasabi import msg as logger
from openai import OpenAI
from jinja2 import Template
import numpy as np
from tqdm.auto import tqdm
from sentence_transformers import CrossEncoder

import plenaryep_downloader.reports as ep

data = (Path(__file__).parents[2] / "data").absolute()
generated = (Path(__file__).parents[2] / "generated").absolute()


class CapClassifier(object):
    def __init__(self, verbose: bool, server: str, token: str, api_checkpoint: str = "Qwen/Qwen3-8B") -> None:
        self.verbose = verbose
        self.truncate = 10000
        self.thresholds_file = data / "cap-thresholds.json"
        self.thresholds = json.load(open(self.thresholds_file))
        self.cap = json.load(open(data / "cap.json"))
        self.ranking_model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2", max_length=512)
        self.api_checkpoint = api_checkpoint
        template_path = Path(__file__) / "templates/cap.jinja2"
        self.template = Template(open(template_path, 'r').read())
        self.client = OpenAI(
            base_url=f"{server}/v1",
            api_key=token,
        )

    def _make_chat(self, 
                   group: str, 
                   topic: str, 
                   description: str, 
                   title: str, 
                   text: str) -> list:
        
        content = self.template.render(
            group=group,
            topic=topic,
            description=description,
            title=title,
            text=text)

        messages = [{"role": "user", "content": content}]
        return messages

    def _classify(self, cap_id: str, text: str, title: str) -> bool:
        messages = self._make_chat(
            group=self.cap[cap_id]["group"],
            topic=self.cap[cap_id]["topic"],
            description=self.cap[cap_id]["description"],
            title=title,
            text=text,
        )
                
        completion = self.client.responses.create(
            model=self.api_checkpoint,
            reasoning={"effort": "none"},
            input=messages,)
        return completion.output_text.lower().startswith("yes")
        
    def __call__(self, report: ep.VerbatimReport) -> ep.VerbatimReport | None:
        # Determine if the report was already classified
        for speech in tqdm(report.speeches, desc="speeches", total=len(report.speeches)):
            if speech.cap_major or speech.cap_minor:
                logger.info(f"Found cap classes for report {report.date}, skipping", 
                            show=self.verbose)
                return None
                
            for major_topic, _minor_topics in self.cap.items():
                for minor_topic, desc in _minor_topics.items():
                    cap_id = desc['id']
                    query = f"{major_topic} {minor_topic} {desc['description']}"

                    # ranking scores
                    examples = [(query, _) for _ in speech.paragraphs]
                    _scores = self.ranking_model.predict(examples)

                    # if max pooled score > minimum threshold: classify
                    if max(_scores) > self.thresholds[cap_id][0]:
                        if self._classify(cap_id=cap_id,
                                          text=" ".join(speech.paragraphs),
                                          title=str(speech.debate_title)):
                            
                            if max(_scores) > self.thresholds[cap_id][1]:
                                speech.cap_major_ids.append(cap_id)
                                speech.cap_major.append(f"{major_topic}-{minor_topic}")
                            else:
                                speech.cap_minor_ids.append(cap_id)
                                speech.cap_minor.append(f"{major_topic}-{minor_topic}")

        return report
