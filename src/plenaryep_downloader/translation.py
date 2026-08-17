from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, List, Iterator
from tqdm.auto import tqdm 
import json
import re
from collections import Counter
import traceback

from wasabi import msg as logger
import translators as ts
from requests.exceptions import HTTPError, ReadTimeout, ConnectionError
from resiliparse.parse.lang import detect_fast as d

import plenaryep_downloader.reports as ep


class Translator(object):
    languages = set(['aa', 'ab', 'ace', 'ach', 'af', 'ak', 'alz', 'am', 'ar', 'as', 'auto', 'av', 'awa', 'ay', 'az', 'ba', 'bal', 'ban', 'bbc', 'bci', 'be', 'bem', 'ber', 'ber-Latn', 'bew', 'bg', 'bho', 'bik', 'bm', 'bm-Nkoo', 'bn', 'bo', 'br', 'bs', 'bts', 'btx', 'bua', 'ca', 'ce', 'ceb', 'cgg', 'ch', 'chk', 'chm', 'ckb', 'cnh', 'co', 'crh', 'crh-Latn', 'crs', 'cs', 'cv', 'cy', 'da', 'de', 'din', 'doi', 'dov', 'dv', 'dyu', 'dz', 'ee', 'el', 'en', 'eo', 'es', 'et', 'eu', 'fa', 'fa-AF', 'ff', 'fi', 'fj', 'fo', 'fon', 'fr', 'fr-CA', 'fur', 'fy', 'ga', 'gaa', 'gd', 'gl', 'gn', 'gom', 'gu', 'gv', 'ha', 'haw', 'hi', 'hil', 'hmn', 'hr', 'hrx', 'ht', 'hu', 'hy', 'iba', 'id', 'ig', 'ilo', 'is', 'it', 'iu', 'iu-Latn', 'iw', 'ja', 'jam', 'jw', 'ka', 'kac', 'kek', 'kg', 'kha', 'kk', 'kl', 'km', 'kn', 'ko', 'kr', 'kri', 'ktu', 'ku', 'kv', 'ky', 'la', 'lb', 'lg', 'li', 'lij', 'lmo', 'ln', 'lo', 'lt', 'ltg', 'lua', 'luo', 'lus', 'lv', 'mad', 'mai', 'mak', 'mam', 'mfe', 'mg', 'mh', 'mi', 'min', 'mk', 'ml', 'mn', 'mni-Mtei', 'mr', 'ms', 'ms-Arab', 'mt', 'mwr', 'my', 'ndc-ZW', 'ne', 'new', 'nhe', 'nl', 'no', 'nr', 'nso', 'nus', 'ny', 'oc', 'om', 'or', 'os', 'pa', 'pa-Arab', 'pag', 'pam', 'pap', 'pl', 'ps', 'pt', 'pt-PT', 'qu', 'rn', 'ro', 'rom', 'ru', 'rw', 'sa', 'sah', 'sat', 'sat-Latn', 'scn', 'sd', 'se', 'sg', 'shn', 'si', 'sk', 'sl', 'sm', 'sn', 'so', 'sq', 'sr', 'ss', 'st', 'su', 'sus', 'sv', 'sw', 'szl', 'ta', 'tcy', 'te', 'tet', 'tg', 'th', 'ti', 'tiv', 'tk', 'tl', 'tn', 'to', 'tpi', 'tr', 'trp', 'ts', 'tt', 'tum', 'ty', 'tyv', 'udm', 'ug', 'uk', 'ur', 'uz', 've', 'vec', 'vi', 'war', 'wo', 'xh', 'yi', 'yo', 'yua', 'yue', 'zap', 'zh-CN', 'zh-TW', 'zu'])

    def __init__(self, verbose: bool, detect_lang: bool = False, accelerate: bool = False) -> None:
        """
        :param detect_lang: use language detection to double-check the translation and the source, defaults to False
        :param accelerate: use translators preacceleration, which might be worth it if there are many translations to do, defaults to False
        """
        self.verbose = verbose
        self.detect_lang = detect_lang
        if accelerate:
            _ = ts.preaccelerate_and_speedtest()    
    
    def _t(self, text: str, source_lang: str, depth: int = 1) -> str:
        if depth > 2:
            return text
        _translations = ts.translate_text(
            query_text=text,
            translator='google',
            to_language='en', 
            from_language=source_lang,
            sleep_seconds=0.3,
            timeout=60,
            if_use_async=False
            )

        if self.detect_lang:
            if len(text) > 20 and d(_translations)[0] != 'en':
                logger.warn(f"Translation to en failed {_translations}", show=self.verbose)
                _translations = self._t(text, 'auto', depth=depth + 1)

        return _translations

    def _translate_speeches(self, speech: ep.Speech) -> ep.Speech:
        if speech.source_lang == "en":
            speech.translation = ep.Translation.SOURCE
            logger.info(f"speech {speech.id} source language is english, but translation field is {speech.translation}. Set translation to {ep.Translation.SOURCE}", 
                        show=self.verbose)
            return speech
        
        # Determine source language
        source_lang = speech.source_lang
        if source_lang:
            source_lang = source_lang.lower()
        if source_lang not in self.languages:
            detected_lang = d(" ".join(speech.paragraphs))[0]
            logger.info(f"Text language {source_lang} is not a known abbreviation, using detected language {detected_lang} insted.", 
                        show=self.verbose)
            source_lang = detected_lang
        # Google Translate has a 5000 word cap per request. 
        # Concatenate paragraphs up to the limit to make as few requests as possible.
        try:   
            lengths = [len(_) for _ in speech.paragraphs]
            if sum(lengths) < 4950:
                paragraphs = "\n\n".join(speech.paragraphs)
                _translations = self._t(paragraphs, source_lang)
                speech.paragraphs = _translations.split("\n\n")
            else:
                joined_paragraphs = [speech.paragraphs[0][:min(4998, len(speech.paragraphs[0]))]]
                for _para, _len in zip(speech.paragraphs[1:], lengths[1:]):
                    if len(joined_paragraphs[-1]) + _len < 4997:
                        joined_paragraphs[-1] = joined_paragraphs[-1] + f"\n\n{_para}"
                    else:
                        joined_paragraphs.append(_para[:min(4998, _len)])
                _translations = []
                for joined_paragraph in joined_paragraphs:
                    _translations.extend(self._t(joined_paragraph, source_lang).split("\n\n"))
                speech.paragraphs = _translations

            speech.translation = ep.Translation.GOOGLE   
        except ReadTimeout as e:
            logger.warn(f"Translation server ReadTimeout: {e}, skipping speeches ...")
        except ConnectionError as e:
            logger.warn(f"Translation server ConnectionError: {e}, skipping speeches ...")

        return speech

    def __call__(self, report: ep.VerbatimReport) -> ep.VerbatimReport | None:
        translated = 0
        for speech in tqdm(report.speeches, desc="speeches", total=len(report.speeches)):
            if speech.translation:
                continue
            try:
                speech_transl = self._translate_speeches(speech)
                speech = speech_transl
                translated += 1
            except Exception as e:
                logger.fail(f"Unexpected exception {e}. Translations are likely incomplete.")
                traceback.print_stack()
        if translated > 1:
            return report
        else:
            return None
