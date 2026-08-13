from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
from datetime import datetime

from wasabi import msg as logger
import scrapy
from scrapy.http import Response
from scrapy.utils.defer import deferred_from_coro


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

    def __init__(self, newest: datetime | None, output_dir: Path, verbose: bool, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_dir = output_dir
        self.newest = newest
        self.verbose = verbose

    def parse(self, response: Response):
        xml_links = []

        _ = [_file.stem.split("-") for _file in self.output_dir.glob("*.xml")]
        existing_file_dates = [datetime(year=int(splits[2]), 
                                        month=int(splits[3]), 
                                        day=int(splits[4])) for splits in _]

        # find all subdirectories for the individual reports
        for href in response.css("pre a::attr(href)").getall():
            if href == "../":
                continue
            if href.endswith("entity/") or href.endswith("technical/"):
                continue
            if href.endswith("/"):
                splits = href.strip(" /").split("-")[2:]
                date = datetime(year=int(splits[0]), month=int(splits[1]), day=int(splits[2]))
                # Skip reports older than the state of the provided dataset
                if self.newest and date <= self.newest: 
                    logger.info(f"skipping {href} as it is older than the dataset", show=self.verbose)
                    continue
                # Skip already downloaded reports
                if date in existing_file_dates: 
                    logger.info(f"skipping {href} as a file for it was already downloaded", show=self.verbose)
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
                    logger.info(f"XML file exists {filename}; skipping", show=self.verbose)
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
                logger.warn("Skipping multilingual group for {base} because no English variant found.")
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
            logger.info("Found multilingual XML files in {page_url}, selecting only English version.", show=self.verbose)
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
                logger.info(f"Multiple language/name variants detected for '{base}' in {page_url}: {', '.join(langs)}", show=self.verbose)

    def save_xml(self, response: Response):
        if response.status == 429:
            retry_times = response.meta.get("retry_times", 0)
            retry_wait = response.meta.get("retry_wait", 5)
            logger.warn(f"Received HTTP 429 for {response.url}; retrying", show=self.verbose)

            if retry_times >= 5:
                logger.fail(f"Maximum retry attempts reached for {response.url}, skipping...")
                return

            if response.request is None:
                logger.fail(f"No original request available for retry of {response.url}, skipping.")
                return

            next_request = response.request.replace(
                callback=self.save_xml,
                dont_filter=True,
                meta={"retry_wait": retry_wait * 2, "retry_times": retry_times + 1},
            )
            return deferred_from_coro(self._retry_after_delay(next_request, retry_wait))

        if response.status != 200:
            logger.warn(f"Unexpected HTTP status {response.status} for {response.url}, skipping.")
            return

        parsed = urlparse(response.url)
        if not parsed.path.startswith(REMOTE_ROOT_PATH):
            logger.warn(f"Skipping XML save because the path does not start with the expected root prefix: {response.url}")
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(parsed.path).name
        output_path = self.output_dir / filename

        if output_path.exists():
            logger.info(f"Skipping save for {response.url}. File exists.", show=self.verbose)
            return

        logger.info(f"Saving XML file {response.url}", show=self.verbose)
        output_path.write_bytes(response.body)

        yield {
            "file_path": str(output_path),
            "url": response.url,
        }

    async def _retry_after_delay(self, request: scrapy.Request, delay: int) -> scrapy.Request:
        await asyncio.sleep(delay)
        return request
