from __future__ import annotations

from typing import Iterable

from wasabi import msg as logger
import click
from tqdm import tqdm
from elasticsearch import Elasticsearch, helpers as es_helpers
from elasticsearch.helpers import BulkIndexError

import plenaryep_downloader.reports as ep


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
        'ches_id': {"type": "keyword"},
        'ches_family': {"type": "keyword"},
        'cap': {"type": "keyword"},
        'cap_id': {"type": "short"},
        'cap_debate': {"type": "keyword"},
        'cap_debate_id': {"type": "short"},
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
        'text': {"type": "text"},
        'text_en': {
            "type": "text",
            "analyzer": "paper_text_analyzer", 
            "search_analyzer": "query_analyzer", 
            "search_quote_analyzer": "paper_text_analyzer" 
        },
    }
}


class Indexer(object):
    def __init__(self, server: str, token: str, index_pattern: str, create: bool, verbose: bool) -> None:
        self.verbose = verbose
        self.index_pattern = index_pattern
        self.es = Elasticsearch(
            hosts=[server], 
            api_key=token,
            retry_on_timeout=True
            )
        if create:
            logger.info(text="Create Index", show=self.verbose)
            self.es.indices.create(index=f"{index_pattern}", body={
                "mappings": MAPPING,
                "settings": SETTINGS})

        pass

    def __call__(self, dataset: Iterable[ep.VerbatimReport]) -> None:
        """

        :param api_key:
        :param index_pattern:
        :param parser:
        :param source_format:
        :return:
        """
        try:
            es_helpers.bulk(
                self.es, 
                actions=self._vrop_parse(dataset), 
                max_retries=2, 
                request_timeout=240, 
                chunk_size=100
                )
        except BulkIndexError as e:
            print(e.errors[0])


    def _vrop_parse(self, reports: Iterable[ep.VerbatimReport]):
        """_summary_

        :param paper: a dict with the paper scraped from OUP

        Return a dict of that paper for the bulk indexer -> see _jats_parse
        :param return_type: `full` or `paragraph`
        """
        for report in tqdm(reports, desc="Reports", total=len(reports)):
            for speech in report.speeches:
        
                text = "\n".join(speech.paragraphs)
                text_en = "\n".join(speech.paragraphs_en)

                yield {
                    '_id': speech.id,
                    '_index': self.index_pattern,
                    'act_id': speech.act_id,
                    'date': speech.date,
                    'source_lang': speech.source_lang,
                    'debate_type': speech.debate_type,
                    'speaker_type': speech.speaker_type,
                    'translation': speech.translation,
                    'debate_title': speech.debate_title,
                    'mep_id': [_.mep_id for _ in speech.speaker],
                    'speaker_name': [_.speaker_name for _ in speech.speaker],
                    'country': [_.country for _ in speech.speaker],
                    'group': [_.group for _ in speech.speaker],
                    'group_code': [_.group_code for _ in speech.speaker],
                    'group_family': [_.group_family for _ in speech.speaker],
                    'party': [_.party for _ in speech.speaker],
                    'party_code': [_.party_code for _ in speech.speaker],
                    'ches_id': [_.ches_id for _ in speech.speaker],
                    'ches_family': [_.ches_family for _ in speech.speaker],
                    'cap': speech.cap,
                    'cap_id': speech.cap_id,
                    'cap_debate': speech.cap_debate,
                    'cap_debate_id': speech.cap_debate_id,
                    'text': text,
                    'text_en': text_en,
                    'length': speech.words,
                }


@click.group()
def cli():
    pass


@click.option('--server', type=str,
              help="Address of the elasticsearch server to be used https://server.name:port")
@click.option('--token', type=str, default="token-abc123",
              help="Authentication token for the server")
@click.option('--index', type=str, default="ep-vrop",
              help="Name of the index")
@click.option('-c', '--create', is_flag=True, default=False,
              help="If true, create the index")
@click.option('-v', '--verbose', is_flag=True, default=False)
@click.argument('input', type=click.Path(exists=True, file_okay=True, dir_okay=False))
@cli.command()
def index(input: str,
          server: str, 
          token: str,
          index: str,
          create: bool,
          verbose: bool):    
    """ Index the dataset to an elasticsearch index. 

    Input is a PlenaryEP dataset. 
    """
    logger.info("load data")
    dataset = ep.load(input)

    logger.info("indexing")
    indexer = Indexer(server, 
                      token, 
                      index_pattern=index, 
                      create=create, 
                      verbose=verbose)
    indexer(dataset)


if __name__ == "__main__":
    cli()
