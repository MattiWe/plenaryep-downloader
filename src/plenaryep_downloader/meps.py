import json 
import re
from typing import Tuple, Iterable
from datetime import datetime, timedelta
from pathlib import Path
import traceback
import unicodedata
from abc import ABC, abstractmethod

from wasabi import msg as logger
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from tqdm.auto import tqdm
from lxml.html import fromstring
from xml.etree import ElementTree

CURRENT_TERM = 10
EP_API_BASE = "https://data.europarl.europa.eu/api/v2"

generated = (Path(__file__).parents[2] / "generated").absolute()
data = (Path(__file__).parents[2] / "data").absolute()

headers = {'user-agent': 'Mozilla/5.0 (compatible; plenaryep-prd-0.1.0)'}


class MepMetadata(ABC):
    delta = timedelta(days=2)
    re_sc = re.compile(r"[\W]")

    def __init__(self, verbose: bool):
        self.verbose = verbose

    def _request(self, url: str) -> dict:
        s = requests.Session()
        retries = Retry(
            total=15,
            backoff_factor=0.5,
            status_forcelist=[429],
            allowed_methods={'GET'},
        )
        s.mount('https://', HTTPAdapter(max_retries=retries))
        response = s.get(url, headers=headers)
        return response.json()

    def _map_country(self, c):
        if c == "Czechia":
            return "Czech Republic"
        return c
    
    def _merge_adjecent(self, memberships: list, sort: bool = False) -> list:
        """When two memberships to the same org are continuous, merge then

        :param memberships: a list of membership dicts {name, code, alt_code, from, to}
        :param sort: sort before merging, defaults to False
        :return: a sorted list of membership dicts
        """
        if len(memberships) == 1:
            return memberships

        if sort:
            memberships = sorted(memberships, key=lambda x: x["from"])
        new_memberships = []
        while memberships:
            next = memberships.pop(0)
            if len(new_memberships) == 0:
                new_memberships.append(next)
                continue
            prev = new_memberships.pop()

            if next["name"] == prev["name"] and (next["from"] - prev["to"]) < self.delta:
                prev["to"] = next["to"]
                new_memberships.append(prev)
            else:
                new_memberships.append(prev)
                new_memberships.append(next)
        return new_memberships

    def _serialize_dates(self, memberships: list) -> list:
        for membership in memberships:
            membership["to"] = membership["to"].isoformat()
            membership["from"] = membership["from"].isoformat()
        return memberships

    def _normalize_party_name(self, pn):
        pn = re.sub(self.re_sc, "", pn)
        pn = pn.lower()
        pn = ''.join(c for c in unicodedata.normalize('NFD', pn)
                     if unicodedata.category(c) != 'Mn')
        return pn

    @abstractmethod
    def get_mep_ids(self, *args, **kwargs) -> dict[str | None, str | None]:
        pass

    @abstractmethod
    def get_meps(self, *args, **kwargs) -> Iterable[Tuple[str, dict]]:
        pass

    def __call__(self, min_term: int, existing: str):  
        """

        :param ches_map: _description_
        :param groups_map: _description_
        :param min_term: _description_
        :param verbose: _description_
        """
        ches_map = json.load(open(data / "ches-map.json"))
        group_map = json.load(open(data / "group-map.json"))
        
        mep_temp_file = generated / "tmp-meps.json"
        mep_metadata = {}
        if existing:
            mep_metadata = json.load(open(existing))

        meps = self.get_mep_ids(min_term)
        try:
            for mepid, mep in self.get_meps(meps, mep_metadata.keys()):
                mep_metadata[mepid] = mep

        except Exception as e:
            logger.fail(traceback.format_exc())
            logger.fail(f"Error while retrieving mep info: {e}. \n\nSaving current state to {mep_temp_file.absolute()}")
            json.dump(mep_metadata, open(mep_temp_file, 'w'), ensure_ascii=False, indent=4)

        logger.info(f"Finished scraping MEPs, saved the temporary results at {mep_temp_file.absolute()}")
        json.dump(mep_metadata, open(mep_temp_file, 'w'), ensure_ascii=False, indent=4)

        # Add CHES Scores
        inv_party_map = {}
        for _id, _party in ches_map.items():
            inv_party_map.setdefault(_party["country"], {})
            _party["id"] = _id

            for _party_name in _party["name"]:
                inv_party_map[_party["country"]][self._normalize_party_name(_party_name)] = _party

        # This maps country abbreviations used by the EP to full names used by CHES
        country_map = json.load(open(data / "country-map.json"))
        
        party_misses = {}
        group_misses = set()
        for _, mep in mep_metadata.items():
            # Add party membership to the mep
            for _party in mep["parties"]:
                country = _party.get("country", mep["country"])
                if country in country_map.keys():
                    country = country_map[country]
                party_name = _party["name"]

                # map ches familiy to mep party membership
                party_mapped = inv_party_map.get(self._map_country(country), {}).get(self._normalize_party_name(party_name), {})
                if not party_mapped:
                    party_misses.setdefault(country, set()).add((_party["code"], party_name))
                    ches_family = ""
                else:
                    ches_family = party_mapped.get("ches", {})[0].get("family", "")

                # add party id, abbrv, country to mep    
                _party["ches_family"] = ches_family
                _party["ches_id"] = party_mapped.get("id", "")

            for _group in mep["groups"]:
                if not _group["name"] in group_map:
                    group_misses.add(_group["name"])
                    group_map[_group["name"]] = ["", ""]
                if not _group["code"]:
                    _group["code"] = group_map[_group["name"]][0]
                _group["family"] = group_map[_group["name"]][1]
                
        ches_misses = {country: list(parties) for country, parties in party_misses.items()}
        misses_count = len([1 for parties in party_misses.values() for _ in parties])
        logger.info(f"Recorded {misses_count} missed parties for ches mapping. Saving to `generated/ches_misses.json`")
        json.dump(ches_misses, open(generated / "ches_misses.json", 'w'), ensure_ascii=False, indent=4)

        if group_misses:
            logger.fail("Not all groups are mapped in `data/group-map.json`, perhaps it was not updated for a newer term? -> see `generated/group_misses.json` for missing group names.")
            json.dump(list(group_misses), open(generated / "group_misses.json", 'w'), ensure_ascii=False, indent=4)
        
        return mep_metadata


class LegacyMepScaper(MepMetadata):
    re_country = re.compile(r"\([\w\s]*\)$")
    re_suffixes = re.compile(r"( - |/)?(Vice-Chair/)?(Member( of the Bureau)?|Secretary to the Bureau|Chair|Vice-Chair|Co-Chair|Deputy Chair|Co-treasurer|Observer|Deputy Treasurer|Treasurer|First Vice-Chair|Ally|mixed group|Co-President|Vice-President)?")
    re_history = re.compile(r"/history/")
    meps_with_large_term_pauses = ["2268", "5736", "28419", "33998", "1566", "4344", "4395"]

    def _parse_mep_status(self, status_string: str, type: str) -> dict[str, datetime | str | None]:
        """When scraping: extract party or group info from a string
        # 16-07-2024 ... : Group of the European People's Party (Christian Democrats) - Member
        # 16-07-2024 ... : Kansallinen Kokoomus (Finland)
        :param type: `party` or `group`
        """
        splits = status_string.split(":")
        _ = splits[0].strip()
        if _.endswith("..."):
            d1 = datetime.strptime(_.strip(". "), "%d-%m-%Y")
            d2 = datetime.today()
        else:
            date_split = _.split("/")
            d1 = datetime.strptime(date_split[0].strip(), "%d-%m-%Y")
            d2 = datetime.strptime(date_split[1].strip(), "%d-%m-%Y")
        
        status = re.sub(self.re_suffixes, "", splits[1].strip(". "))

        country = ""
        name = status
        if type == "party":
            country_search = re.search(self.re_country, name)
            try:
                country = country_search.group(0).strip(" ()")
                name = re.sub(self.re_country, "", name).strip()
            except AttributeError:
                logger.info("No country given", name)
                name = name.strip()

        _result = {"from": d1, "to": d2, "name": name, "code": None, "alt_code": None}
        if country:
            _result["country"] = country

        return _result

    def _dmin(self, d1, d2):
        if d1 < d2: 
            return d1
        return d2

    def _dmax(self, d1, d2):
        if d1 > d2: 
            return d1
        return d2

    def _parse_mep(self, response) -> Tuple[list, list]:
        """When scraping: extract MEP data from a html snippet

        :param response: a requests response from an MEP profile page
        :return: two lists of groups and parties
        """
        http = fromstring(response.text)
        groups = []
        parties = []
        for elem in http.find_class("erpl_meps-status"):
            if elem.find_class("es_title-h4")[0].text_content() == "Political groups":
                for _group in elem.cssselect("ul li"):
                    group = self._parse_mep_status(_group.text_content(), type="group")
                    groups.append(group)

            elif elem.find_class("es_title-h4")[0].text_content() == "National parties":
                for _party in elem.cssselect("ul li"):
                    party = self._parse_mep_status(_party.text_content(), type="party")
                    parties.append(party)

        # merge adjecent dates
        groups = self._merge_adjecent(groups)
        parties = self._merge_adjecent(parties)
        return groups, parties

    def _load_mep(self, 
                  base_url: str, 
                  period: int, 
                  mepid: str, 
                  skipped: int = 0) -> tuple[list[tuple[datetime, str]], list[tuple[datetime, str]]]:
        """ When scraping the website: Recursively 
            - fake the url for the MEP history page 
            - check if it exists and, if yes, extract party and group membership
            """
        # 1. make request
        request = requests.get(f"{base_url}/{period}")
        # print(period, skipped, request.url)

        # 2a. sometimes, a non-existant request redirects to /home instead of /history/period. This happens for skipped sites to. 
        if (skipped > 1 and (request.url.endswith("home") 
            or request.status_code != requests.codes.ok)):
            return [], []
        elif (request.url.endswith("home") 
            or request.status_code != requests.codes.ok):
            request_period = period - 1
        else:
            request_period = int(request.url.split("/")[-1])

        # 2b. skip-1 stopping criterion
        if (request_period != period and skipped > 1):
            return [], []

        # 3a. recurse - if an MEP skipped a term, we recurse but do not parse or add anything from here
        elif (request_period != period):
            groups, parties = [], []
            if mepid not in self.meps_with_large_term_pauses:
                skipped += 1
        # 3b. recurse - regular case 
        else:
            groups, parties = self._parse_mep(request)
        
        if period > 1:
            groups_new, parties_new = self._load_mep(base_url, period - 1, mepid, skipped)
            groups_new.extend(groups)
            parties_new.extend(parties)
            groups = self._merge_adjecent(groups_new)
            parties = self._merge_adjecent(parties_new)
        
        return groups, parties

    def get_mep_ids(self, *args, **kwargs) -> dict[str | None, str | None]:
        """ If the API is unavailable, there is an xml list of all mep_ids on the website 
        
        :return: A dict with {mep_id: mep_name}
        """
        backup_id_list_url = "https://www.europarl.europa.eu/meps/en/directory/xml"
        r = requests.get(backup_id_list_url, headers=headers)
        root = ElementTree.fromstring(r.content)
        meps = {element[1].text: element[0].text for element in root.findall(".//mep")}
        # Skip Frans TIMMERMANS, who got elected but did not accept the seat -> he has a profile but no membership
        meps.pop("129141", None)
        return meps

    def get_meps(self, meps: dict, *args, **kwargs) -> Iterable[Tuple[str, dict]]:
        """Backup: Scrape MEP Data from the Website when the API is unavailable

        :return: _description_
        """
        for mepid, name in tqdm(meps.items(), desc="meps", total=len(meps.keys())):        
            r = requests.get(f"https://www.europarl.europa.eu/meps/en/{mepid}/", headers=headers)
            if not re.search(self.re_history, r.url):
                r = requests.get(f"{r.url[:-5]}/history")
            
            split = r.url.split("/")
            period = int(split[-1])
            base_url = f"{"/".join(split[:-1])}"
            groups, parties = self._load_mep(base_url, period, mepid)
            groups, parties = self._serialize_dates(groups), self._serialize_dates(parties)

            mep = {
                "name": name,
                "groups": groups,
                "parties": parties}
            
            yield mepid, mep


class ApiMepScaper(MepMetadata):
    def get_mep_ids(self, min_term: int, *args, **kwargs) -> dict[str | None, str | None]:
        """ Iteratively request the meps from the EP API. 

        :param min_term: earliest term to include.
        :return: A dict with {mep_id: mep_name}
        """
        meps = {}
        for term in range(min_term, CURRENT_TERM + 1):
            r = self._request(f"{EP_API_BASE}/meps?parliamentary-term={term}&format=application%2Fld%2Bjson&offset=0&limit=1000")
            _meps = r["data"]
            meps.update({mep["identifier"]: mep["label"] for mep in _meps})
        return meps       

    def get_meps(self, meps: dict, existing: Iterable, *args, **kwargs) -> Iterable[Tuple[str, dict]]:
        """Get MEP Data from the EP API

        :param meps: Dict with {mep_id: mep_name} as returned by `_get_mep_ids_api`
        :param existing: Iterable with mep_ids that will be skipped
        :return: Yields a Tuple of mep_id and a mep data dict {name, bday, gender, country, groups, parties}
        """
        organizations = {}
        for mep_id, mep_name in tqdm(meps.items(), desc="meps", total=len(meps.keys())): 
            # If the crawling was interrupted, skip the already existing entries
            if str(mep_id) in existing:
                continue

            r = self._request(f"{EP_API_BASE}/meps/{mep_id}?format=application%2Fld%2Bjson") 
            _mep = r["data"][0]

            groups = []
            parties = []
            for membership in _mep["hasMembership"]:
                if "membershipClassification" not in membership:
                    logger.info(f"Org missing classification: {membership.get('organization', '')}")
                    continue
                
                member_class = membership["membershipClassification"].split("/")[-1]
                
                if member_class != "EU_POLITICAL_GROUP" \
                    and member_class != "NATIONAL_POLITICAL_GROUP":
                    continue

                org_id = membership.get('organization', '').split("/")[-1]
                if not org_id:
                    continue
                if org_id not in organizations:
                    r_class = self._request(
                        f"{EP_API_BASE}/corporate-bodies/{org_id}?format=application%2Fld%2Bjson&language=en")
                    class_data = r_class["data"][0] 
                    organizations[org_id] = (
                        class_data["label"], 
                        class_data["prefLabel"]["en"],
                        class_data["altLabel"]["en"])
                
                membership = {
                    "name": organizations[org_id][1],
                    "code": organizations[org_id][0],
                    "alt_code": organizations[org_id][2],
                    "from": datetime.fromisoformat(membership["memberDuring"]["startDate"]),
                    "to": datetime.fromisoformat(
                        membership["memberDuring"].get("endDate", datetime.today().isoformat()))
                }
                if member_class == "EU_POLITICAL_GROUP":  # Group
                    groups.append(membership)
                elif member_class == "NATIONAL_POLITICAL_GROUP":  # Party
                    parties.append(membership)

            groups, parties = self._merge_adjecent(groups, sort=True), self._merge_adjecent(parties, sort=True)
            groups, parties = self._serialize_dates(groups), self._serialize_dates(parties)
            mep = {
                "name": mep_name,
                "bday": _mep.get("bday", None),
                "gender": _mep.get("hasGender", "").split("/")[-1],
                "country": _mep.get("citizenship", "").split("/")[-1],
                "groups": groups,
                "parties": parties}
            
            yield mep_id, mep
