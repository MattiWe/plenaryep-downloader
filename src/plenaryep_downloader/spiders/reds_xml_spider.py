from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
from datetime import datetime
import logging

import scrapy
from scrapy.http import Response
from scrapy.utils.defer import deferred_from_coro

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

ROOT_URL = "https://redmapl3.europarl.europa.eu/RedmapFront/media/reds_iPlCre_Sit/"
REMOTE_ROOT_PATH = "/RedmapFront/media/reds_iPlCre_Sit/"
XML_NAME_RE = re.compile(
    r"^(?P<base>.+-?)(?P<type>REV|PRV|FNL)(?:_(?P<lang>[a-z]{2}))?\.xml$",
    re.IGNORECASE,
)


class RedsXmlSpider(scrapy.Spider):
    name = "reds_xml"
    allowed_domains = ["redmapl3.europarl.europa.eu"]
    start_urls = [ROOT_URL]
    custom_settings = {
        "LOG_LEVEL": "WARNING",
        "USER_AGENT": "Mozilla/5.0 (compatible; plenaryep-prd-0.1.0)",
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_TIMEOUT": 30,
        "DOWNLOAD_DELAY": 0.5,
        "handle_httpstatus_list": [429],
    }

    def __init__(self, newest: datetime | None, output_dir: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_dir = output_dir
        self.newest = newest
        # self.logger.setLevel(logging.WARNING)

    def parse(self, response: Response):
        xml_links = []

        for href in response.css("pre a::attr(href)").getall():
            if href == "../":
                continue
            if href.endswith("entity/"):
                continue
            if href.endswith("/"):
                # Skip reports given in self.existing_dates
                splits = href.strip(" /").split("-")[2:]
                date = datetime(year=int(splits[0]), month=int(splits[1]), day=int(splits[2]))
                if self.newest and date <= self.newest: 
                    self.logger.info(f"date {date} is older than {self.newest} ; skipping")
                    continue
                yield response.follow(href, self.parse)
            if href.lower().endswith(".xml"):
                xml_links.append(href)

        if xml_links:
            selected_links = self._select_xml_links(xml_links, response.url)
            for href in selected_links:
                filename = Path(href).name
                output_path = self.output_dir / filename
                # Skip existing/downloaded source
                if output_path.exists():
                    self.logger.info(
                        f"XML file exists {filename}; skipping"
                    )
                    continue
                
                yield response.follow(
                    href,
                    self.save_xml,
                    meta={"retry_wait": 5, "retry_times": 2},
                )

    def _select_xml_links(self, xml_links: list[str], page_url: str) -> list[str]:
        self._log_naming_scheme(xml_links, page_url)

        # Group by base name (shared prefix before REV/PRV/FNL)
        groups: dict[str, list[str]] = {}
        for href in xml_links:
            filename = Path(href).name
            match = XML_NAME_RE.match(filename)
            if match is None:
                # If we can't parse the name, keep the link as-is under a special key
                groups.setdefault(filename, []).append(href)
                continue

            base = match.group("base")
            groups.setdefault(base, []).append(href)

        # For each base, if there are PRV and REV variants, prefer REV only
        selected: list[str] = []
        for base, hrefs in groups.items():
            # If this base has multiple variants (multilingual/multiple files) but
            # none of them is English, skip downloading any of them.
            langs = [self._xml_language(h) for h in hrefs]
            language_set = set(x for x in langs if x is not None)
            is_multilingual = len(language_set) > 1
            if is_multilingual and "en" not in language_set:
                # Skip this base completely because it's multilingual but lacks an English version
                self.logger.info(
                    "Skipping multilingual group for base '%s' in %s because no English variant found.",
                    base,
                    page_url,
                )
                continue

            # Prefer English final versions (FNL_en) over REV; otherwise fall back to REV or available variants
            fnl_en: list[str] = []
            revs: list[str] = []
            for h in hrefs:
                m = XML_NAME_RE.match(Path(h).name)
                if not m:
                    continue
                typ = (m.group("type") or "").upper()
                lang = m.group("lang")
                if typ == "FNL" and lang == "en":
                    fnl_en.append(h)
                elif typ == "REV":
                    revs.append(h)

            if fnl_en:
                selected.extend(fnl_en)
            elif revs:
                selected.extend(revs)
            else:
                selected.extend(hrefs)

        # If English versions exist among the selected set, pick only those
        english_links = [href for href in selected if self._xml_language(href) == "en"]
        if english_links:
            self.logger.info(
                "Found multilingual XML files in %s, selecting only English versions (%s files).",
                page_url,
                len(english_links),
            )
            return english_links

        return selected

    def _xml_language(self, href: str) -> str | None:
        filename = Path(href).name
        match = XML_NAME_RE.match(filename)
        if not match:
            return None
        return match.group("lang")

    def _log_naming_scheme(self, xml_links: Iterable[str], page_url: str) -> None:
        groups: dict[str, list[tuple[str | None, str]]] = {}
        for href in xml_links:
            filename = Path(href).name
            match = XML_NAME_RE.match(filename)
            if match is None:
                continue

            base = match.group("base")
            lang = match.group("lang")
            typ = match.group("type")
            groups.setdefault(base, []).append((lang, f"{typ}:{filename}"))

        for base, variants in groups.items():
            langs = [lang or "none" for lang, _ in variants]
            if len(variants) > 1:
                self.logger.info(
                    "Multiple language/name variants detected for base '%s' in %s: %s",
                    base,
                    page_url,
                    ", ".join(langs),
                )

    def save_xml(self, response: Response):
        if response.status == 429:
            retry_times = response.meta.get("retry_times", 0)
            retry_wait = response.meta.get("retry_wait", 5)
            self.logger.warning(
                "Received HTTP 429 for %s; retrying after %s seconds (attempt %s).",
                response.url,
                retry_wait,
                retry_times + 1,
            )

            if retry_times >= 5:
                self.logger.error(
                    "Maximum retry attempts reached for %s, skipping download.",
                    response.url,
                )
                return

            if response.request is None:
                self.logger.error(
                    "No original request available for retry of %s, skipping.",
                    response.url,
                )
                return

            next_request = response.request.replace(
                callback=self.save_xml,
                dont_filter=True,
                meta={"retry_wait": retry_wait * 2, "retry_times": retry_times + 1},
            )
            return deferred_from_coro(self._retry_after_delay(next_request, retry_wait))

        if response.status != 200:
            self.logger.warning(
                "Unexpected HTTP status %s for %s, skipping.",
                response.status,
                response.url,
            )
            return

        parsed = urlparse(response.url)
        if not parsed.path.startswith(REMOTE_ROOT_PATH):
            self.logger.warning(
                "Skipping XML save because the path does not start with the expected root prefix: %s",
                response.url,
            )
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(parsed.path).name
        output_path = self.output_dir / filename

        if output_path.exists():
            self.logger.info(
                "Skipping save for %s because file already exists at %s.",
                response.url,
                output_path,
            )
            return

        self.logger.info("Saving XML file %s to %s", response.url, output_path)
        output_path.write_bytes(response.body)

        yield {
            "file_path": str(output_path),
            "url": response.url,
        }

    async def _retry_after_delay(self, request: scrapy.Request, delay: int) -> scrapy.Request:
        await asyncio.sleep(delay)
        return request
