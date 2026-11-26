from __future__ import annotations

from pathlib import Path
from typing import List, Iterator
from datetime import datetime
from wasabi import msg as logger
import json
from tqdm import tqdm

from elasticsearch import Elasticsearch, helpers as es_helpers
from elasticsearch.helpers import BulkIndexError


SETTINGS = {
    "index.query.default_field": "text",
    "number_of_shards": 10,
    "number_of_replicas": 2,
    "analysis": {
        "filter": {
            "en_stem_filter": {
                "type": "stemmer",
                "name": "minimal_english"
            },
            "english_stop": {
                "type": "stop",
                "stopwords": "_english_"
            }
        },
        "analyzer": {
            "paper_text_analyzer": {
                "type": "custom",
                "filter": [
                    "icu_normalizer",
                    "en_stem_filter",
                    "icu_folding"
                ],
                "tokenizer": "icu_tokenizer"
            },
            "query_analyzer": {
                "type": "custom",
                "filter": [
                    "icu_normalizer",
                    "en_stem_filter",
                    "english_stop",
                    "icu_folding"
                ],
                "tokenizer": "icu_tokenizer"
            }
        },
    }
}

MAPPING = {
    "properties": {
        'act_id': {"type": "keyword"},
        'date': {"type": "date"},
        'debate_type': {"type": "keyword"},
        'party': {"type": "text"},
        'party_code': {"type": "keyword"},
        'group': {"type": "text"},
        'group_code': {"type": "keyword"},
        'group_family': {"type": "keyword"},
        'country': {"type": "keyword"},
        'ches_code': {"type": "keyword"},
        'ches_family': {"type": "keyword"},
        'cap': {"type": "keyword"},
        'cap_code': {"type": "short"},
        'cap_minor': {"type": "keyword"},
        'length': {"type": "integer"},
        'speaker_type': {"type": "keyword"},
        'source_lang': {"type": "keyword"},
        'mep_id': {"type": "keyword"},
        'speaker_name': {"type": "text"},
        'translation': {"type": "keyword"},
        'debate_title': {
            "type": "text",
            "analyzer": "paper_text_analyzer", 
            "search_analyzer": "query_analyzer", 
            "search_quote_analyzer": "paper_text_analyzer" 
        },
        'text': {
            "type": "text",
            "analyzer": "paper_text_analyzer", 
            "search_analyzer": "query_analyzer", 
            "search_quote_analyzer": "paper_text_analyzer" 
        },
    }
}


def create_index(api_key: str, index_pattern: str = "ep-vrop"):
    es = Elasticsearch(hosts=[f"https://elasticsearch.bw.webis.de:9200"], api_key=api_key)
    es.indices.create(index=f"{index_pattern}", body={
        "mappings": MAPPING,
        "settings": SETTINGS})


def index_papers(api_key: str, parser: Iterator):
    """

    :param api_key:
    :param index_pattern:
    :param parser:
    :param source_format:
    :return:
    """
    es = Elasticsearch(
        hosts=[f"https://elasticsearch.bw.webis.de:9200"], 
        api_key=api_key,
        retry_on_timeout=True, 
        timeout=240
        )
    try:
        es_helpers.bulk(
            es, 
            actions=parser, 
            max_retries=2, 
            request_timeout=240, 
            chunk_size=100
            )
    except BulkIndexError as e:
        print(e.errors[0])


def vrop_parse( 
        reports: List,
        mep_info: dict,
        index_pattern: str = "ep-vrop"):
    """_summary_

    :param paper: a dict with the paper scraped from OUP

    Return a dict of that paper for the bulk indexer -> see _jats_parse
    :param return_type: `full` or `paragraph`
    """
    for report in tqdm(reports, desc="Reports", total=len(reports)):
        for speech in report["speeches"]:
     
            text = "\n".join(speech["paragraphs"])

            yield {
                '_id': speech["id"],
                '_index': f"{index_pattern}",
                'act_id': speech["act_id"],
                'date': report["date"],
                'source_lang': speech["source_lang"],
                'debate_type': speech["debate_type"],
                'speaker_type': speech["speaker_type"],
                'translation': speech["translation"],
                'debate_title': speech["debate_title"],
                'mep_id': [_["mep_id"] for _ in speech["speaker"]],
                'speaker_name': [_["speaker_name"] for _ in speech["speaker"]],
                'country': [_["country"] for _ in speech["speaker"]],
                'group': [_["group"] for _ in speech["speaker"]],
                'group_code': [_["group_code"] for _ in speech["speaker"]],
                'group_family': [_["group_family"] for _ in speech["speaker"]],
                'party': [_["party"] for _ in speech["speaker"]],
                'party_code': [_["party_code"] for _ in speech["speaker"]],
                'ches_code': [_["ches_code"] for _ in speech["speaker"]],
                'ches_family': [_["ches_family"] for _ in speech["speaker"]],
                'cap': speech["cap"],
                'cap_code': speech["cap_code"],
                'cap_minor': speech["cap_minor"],
                'text': text,
                'length': speech["tokens"],
            }


if __name__ == "__main__":
    dataset = Path("/home/mike4537/code/github/plenaryep-downloader/generated/plenaryep_26-08-14.jsonl")
    mep_info = Path("/home/mike4537/code/git-webis-de/code-research/arguana/narratives-in-political-speeches/data/mep_metadata.json")
    mep_info = json.load(open(mep_info))
    api_key = open("/home/mike4537/Sync/es-key.txt", 'r').read()

    # create_index(api_key=api_key)
    logger.info("load data")
    reports = [json.loads(line) for line in open(dataset)]
    logger.info("indexing")
    index_papers(api_key=api_key, parser=vrop_parse(reports, mep_info))
