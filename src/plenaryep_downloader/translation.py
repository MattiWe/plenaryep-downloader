from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, List, Iterator
from tqdm.auto import tqdm 
import logging
import json
import re
from reports import VerbatimReport, Speech, Translation
import click 
import translators as ts
from translators.server import TranslatorError
from requests.exceptions import HTTPError, ReadTimeout, ConnectionError
import traceback
from resiliparse.parse.lang import detect_fast as d


logger = logging.getLogger(__name__)
logger.setLevel(level=logging.INFO)

re_prefix = re.compile(r"^On behalf of the.*-|^\(?.{2,8}\)(, in writing)?\.?-?|^Rapporteur.?.?-|^Blue[ -][cC]ard.? question\..?-|^Deputy rapporteur\.?.?-|^Blue[ -][cC]ard.? ([Aa]nswer|[Rr]esponse)\..?-|^Author\.?.?-")

LANG = ['aa', 'ab', 'ace', 'ach', 'af', 'ak', 'alz', 'am', 'ar', 'as', 'auto', 'av', 'awa', 'ay', 'az', 'ba', 'bal', 'ban', 'bbc', 'bci', 'be', 'bem', 'ber', 'ber-Latn', 'bew', 'bg', 'bho', 'bik', 'bm', 'bm-Nkoo', 'bn', 'bo', 'br', 'bs', 'bts', 'btx', 'bua', 'ca', 'ce', 'ceb', 'cgg', 'ch', 'chk', 'chm', 'ckb', 'cnh', 'co', 'crh', 'crh-Latn', 'crs', 'cs', 'cv', 'cy', 'da', 'de', 'din', 'doi', 'dov', 'dv', 'dyu', 'dz', 'ee', 'el', 'en', 'eo', 'es', 'et', 'eu', 'fa', 'fa-AF', 'ff', 'fi', 'fj', 'fo', 'fon', 'fr', 'fr-CA', 'fur', 'fy', 'ga', 'gaa', 'gd', 'gl', 'gn', 'gom', 'gu', 'gv', 'ha', 'haw', 'hi', 'hil', 'hmn', 'hr', 'hrx', 'ht', 'hu', 'hy', 'iba', 'id', 'ig', 'ilo', 'is', 'it', 'iu', 'iu-Latn', 'iw', 'ja', 'jam', 'jw', 'ka', 'kac', 'kek', 'kg', 'kha', 'kk', 'kl', 'km', 'kn', 'ko', 'kr', 'kri', 'ktu', 'ku', 'kv', 'ky', 'la', 'lb', 'lg', 'li', 'lij', 'lmo', 'ln', 'lo', 'lt', 'ltg', 'lua', 'luo', 'lus', 'lv', 'mad', 'mai', 'mak', 'mam', 'mfe', 'mg', 'mh', 'mi', 'min', 'mk', 'ml', 'mn', 'mni-Mtei', 'mr', 'ms', 'ms-Arab', 'mt', 'mwr', 'my', 'ndc-ZW', 'ne', 'new', 'nhe', 'nl', 'no', 'nr', 'nso', 'nus', 'ny', 'oc', 'om', 'or', 'os', 'pa', 'pa-Arab', 'pag', 'pam', 'pap', 'pl', 'ps', 'pt', 'pt-PT', 'qu', 'rn', 'ro', 'rom', 'ru', 'rw', 'sa', 'sah', 'sat', 'sat-Latn', 'scn', 'sd', 'se', 'sg', 'shn', 'si', 'sk', 'sl', 'sm', 'sn', 'so', 'sq', 'sr', 'ss', 'st', 'su', 'sus', 'sv', 'sw', 'szl', 'ta', 'tcy', 'te', 'tet', 'tg', 'th', 'ti', 'tiv', 'tk', 'tl', 'tn', 'to', 'tpi', 'tr', 'trp', 'ts', 'tt', 'tum', 'ty', 'tyv', 'udm', 'ug', 'uk', 'ur', 'uz', 've', 'vec', 'vi', 'war', 'wo', 'xh', 'yi', 'yo', 'yua', 'yue', 'zap', 'zh-CN', 'zh-TW', 'zu']


def sync_deepl_translations(reports: List[VerbatimReport], dataset_v1: Path) -> List[VerbatimReport]:
    _translations = [json.loads(line) for line in open(dataset_v1)]
    translations = {}
    for t in _translations:
        key = f"{t['date']}-{t['speech_paragraph']}" 
        if key in translations:
            print("conflict")
        translations[key] = t["text_en"]

    for report in tqdm(reports, desc="reports", total=len(reports)):
        for speech in report.speeches:
            if speech.translation:
                continue
            if f"{report.date.strftime('%Y-%m-%d')}-{speech.act_id}.1" in translations:
                speech.translation = Translation.DEEPL
                try:
                    _translations = [translations[f"{report.date.strftime('%Y-%m-%d')}-{speech.act_id}.{idx}"]
                                     for idx, _ in enumerate(speech.paragraphs, start=1)]
                    if len(_translations) == 0:
                        print(speech.paragraphs)
                        print(translations[f"{report.date.strftime('%Y-%m-%d')}-{speech.act_id}.1"])
                    _translations[0] = re.sub(re_prefix, "", _translations[0])
                    _translations[0] = _translations[0].strip(" -")
                except KeyError:
                    # skip if there are fewer
                    continue

                # skip if there are more
                if f"{report.date.strftime('%Y-%m-%d')}-{speech.act_id}.{len(speech.paragraphs) + 1}" in translations:
                    continue
                speech.paragraphs = _translations
    
    return reports


@click.group()
def cli():
    pass


@click.option('--input', 
              default='/home/mike4537/code/git-webis-de/code-research/arguana/narratives-in-political-speeches/generated/dataset/vrop-raw.jsonl',
              type=click.Path(exists=True, file_okay=True),
              help='Where to add the translations too')
@click.option('--source', 
              default='/home/mike4537/code/git-webis-de/code-research/arguana/narratives-in-political-speeches/data/ep-reports-of-proceedings.jsonl',
              type=click.Path(exists=True, file_okay=True),
              help='Where to sync from')
@click.option('-s', '--savepoint', 
              type=click.Path(file_okay=False),
              default="/home/mike4537/code/git-webis-de/code-research/arguana/narratives-in-political-speeches/generated/dataset",
              help="Path where to store the run results.")
@click.option('--debug', is_flag=True, default=False)
@cli.command()
def sync_translations(
        input: str, 
        source: str, 
        savepoint: str, 
        debug: bool):
    
    if debug:
        logger.setLevel(level=logging.DEBUG)
        logger.debug("DEBUG Debug Mode")
    
    output = Path(savepoint) / "vrop-deepl.jsonl"
    output.parent.mkdir(exist_ok=True)

    reports = [VerbatimReport.from_dict(json.loads(line)) for line in tqdm(open(input), desc="loading reports")]
    reports_deepl = sync_deepl_translations(reports, Path(source))
    open(output, 'w').writelines([
        f"{json.dumps(report.as_dict(), ensure_ascii=False)}\n"
        for report in reports_deepl
    ])


@click.option('--input', 
              default='/mnt/ceph/storage/data-in-progress/data-research/computational-social-science/political-narratives/dataset/vrop.jsonl',
              type=click.Path(exists=True, file_okay=True),
              help='Where to add the translations too')
@click.option('-s', '--savepoint', 
              type=click.Path(file_okay=False),
              default="/mnt/ceph/storage/data-in-progress/data-research/computational-social-science/political-narratives/dataset/",
              help="Path where to store the run results.")
@click.option('--debug', is_flag=True, default=False)
@cli.command()
def google_translate(
        input: str, 
        savepoint: str, 
        debug: bool):
    
    if debug:
        logger.setLevel(level=logging.DEBUG)
        logger.debug("DEBUG Debug Mode")

    # _ = ts.preaccelerate_and_speedtest()    
    output = input
    # output.parent.mkdir(exist_ok=True)

    reports = [VerbatimReport.from_dict(json.loads(line)) for line in tqdm(open(input), desc="loading reports")]

    def _t(text: str, sl: str) -> str:

        if sl:
            sl = sl.lower()
        if sl not in LANG:
            sl = 'auto'
        _translations = ts.translate_text(
            query_text=text,
            translator='google',
            to_language='en', 
            from_language=sl,
            sleep_seconds=0.3,
            timeout=30,
            if_use_async=False
            )
        return _translations
    
    report_count = len(reports)
    try:
        for ind, report in enumerate(reports):
            t_counter = 0
            for speech in tqdm(report.speeches, desc="speeches", total=len(report.speeches)):
                if len(speech.paragraphs) < 2:
                    continue
                lang1 = d(speech.paragraphs[0])[0]
                if lang1 == 'en':
                    continue
                lang2 = d(speech.paragraphs[1])[0]
                if lang2 == 'en':
                    continue
                
                source_lang = 'auto'
                if lang1 == lang2:
                    source_lang = lang1
                try:
                    lengths = [len(_) for _ in speech.paragraphs]
                    if sum(lengths) < 4950:
                        paragraphs = "\n\n".join(speech.paragraphs)
                        _translations = _t(paragraphs, source_lang)
                        speech.paragraphs = _translations.split("\n\n")
                    else:
                        lengths = [len(_) for _ in speech.paragraphs]
                        speech.paragraphs = [
                            _t(_para[:min(4998, _len)], source_lang)
                            for _para, _len in zip(speech.paragraphs, lengths)
                            ]
                    speech.translation = Translation.GOOGLE   
                    t_counter += 1 
                except ReadTimeout:
                    print("Timeout")
                except ConnectionError:
                    print("Timeout")
            if t_counter > 0:
                open(output, 'w').writelines([
                    f"{json.dumps(report.as_dict(), ensure_ascii=False)}\n"
                    for report in reports
                ])
                print(f"translated {t_counter} speeches in report. {report_count - ind} reports to go")
    except HTTPError:
        logger.error("429 timeout")
    except Exception as e:
        logger.error(f"Unexpected exception: {e}")
        print(traceback.format_exc())

    open(output, 'w').writelines([
        f"{json.dumps(report.as_dict(), ensure_ascii=False)}\n"
        for report in reports
    ])


if __name__ == "__main__":
    cli()
