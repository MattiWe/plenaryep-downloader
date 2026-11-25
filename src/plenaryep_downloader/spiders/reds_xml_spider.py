from __future__ import annotations

from datetime import datetime
import re
from pathlib import Path
from urllib.parse import urlparse

import scrapy
from scrapy.http import Response
from wasabi import msg as logger


ROOT_URL = "https://redmapl3.europarl.europa.eu/RedmapFront/media/reds_iPlCre_Sit/"


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
    }

    def __init__(self, newest: datetime | None, output_dir: Path, verbose: bool, *args, **kwargs):
        """Configure the output directory, optional date cutoff, and progress logging."""
        super().__init__(*args, **kwargs)
        self.output_dir = output_dir
        self.newest = newest
        self.verbose = verbose

    def parse(self, response: Response):
        """Request the inventory of each sitting on or after the date cutoff."""
        for href in response.css("pre a::attr(href)").getall():
            if href == "../" or not href.endswith("/"):
                continue
            if href.endswith(("entity/", "technical/")):
                continue
            if self._is_older_sitting(href):
                continue
            yield response.follow(href, self.parse_sitting)

    def _is_older_sitting(self, href: str) -> bool:
        """Compare the date in a sitting directory name with the cutoff date."""
        if self.newest is None:
            return False
        date_parts = Path(urlparse(href).path).name.split("-")[2:5]
        sitting_date = datetime.strptime("-".join(date_parts), "%Y-%m-%d").date()
        return sitting_date < self.newest.date()

    def parse_sitting(self, response: Response):
        """Request selected versions, grouping translations by sitting and skipping local files."""
        links = self._select_xml_links(response.css("pre a::attr(href)").getall())
        if not links:
            logger.info(f"Skipping {response.url}: no REV.xml or FNL_en.xml report available.")
            return

        output_dir = self.output_dir
        if len(links) > 1 or not Path(urlparse(links[0]).path).name.lower().endswith("rev.xml"):
            output_dir /= Path(urlparse(response.url).path).name
            output_dir.mkdir(parents=True, exist_ok=True)

        for href in links:
            filename = Path(urlparse(href).path).name
            if (output_dir / filename).exists():
                continue
            yield response.follow(href, self.save_xml, cb_kwargs={"output_dir": output_dir})

    @staticmethod
    def _select_xml_links(links: list[str]) -> list[str]:
        """Select REV plus English FNL, REV alone, or all FNL languages with English."""
        revision = None
        english = None
        finals = []
        for href in links:
            filename = Path(urlparse(href).path).name.lower()
            if filename.endswith("rev.xml") and revision is None:
                revision = href
            if re.search(r"fnl_[a-z]{2}\.xml$", filename):
                finals.append(href)
                if filename.endswith("fnl_en.xml") and english is None:
                    english = href
        if revision is not None:
            return [revision, english] if english is not None else [revision]
        return finals if english is not None else []

    def save_xml(self, response: Response, output_dir: Path | None = None):
        """Save the report in its selected directory and emit its path and source URL."""
        if output_dir is None:
            output_dir = self.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / Path(urlparse(response.url).path).name
        output_path.write_bytes(response.body)
        logger.info(f"Saved XML file {output_path.name}", show=self.verbose)
        yield {"file_path": str(output_path), "url": response.url}
