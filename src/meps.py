import json 
import requests
import re
from lxml.html import fromstring
from datetime import datetime
from pathlib import Path
import traceback
from tqdm.auto import tqdm
import pandas as pd
import unicodedata
import click 
import logging
import requests
from xml.etree import ElementTree

logger = logging.getLogger(__name__)
generated = (Path(__file__).parents[1] / "generated").absolute()
headers = {'user-agent': 'Mozilla/5.0 (compatible; EU-REDMAP-Scraper/1.0)'}

re_country = re.compile(r"\([\w\s]*\)$")
re_sc = re.compile(r"[\W]")
re_suffixes = re.compile(r"( - |/)?(Vice-Chair/)?(Member( of the Bureau)?|Secretary to the Bureau|Chair|Vice-Chair|Co-Chair|Deputy Chair|Co-treasurer|Observer|Deputy Treasurer|Treasurer|First Vice-Chair|Ally|mixed group|Co-President|Vice-President)?")
re_history = re.compile(r"/history/")
meps_with_large_term_pauses = ["2268", "5736", "28419", "33998", "1566", "4344", "4395"]


def parse_mep_status(status_string: str) -> list[datetime | str]:
    # 16-07-2024 ... : Group of the European People's Party (Christian Democrats) - Member
    # 16-07-2024 ... : Kansallinen Kokoomus (Finland)
    splits = status_string.split(":")
    _ = splits[0].strip()
    if _.endswith("..."):
        d1 = datetime.strptime(_.strip(". "), "%d-%m-%Y")
        d2 = datetime.today()
    else:
        date_split = _.split("/")
        d1 = datetime.strptime(date_split[0].strip(), "%d-%m-%Y")
        d2 = datetime.strptime(date_split[1].strip(), "%d-%m-%Y")
    
    status = re.sub(re_suffixes, "", splits[1].strip(". "))

    return [d1, d2, status]


def _dmin(d1, d2):
    if d1 < d2: 
        return d1
    return d2


def _dmax(d1, d2):
    if d1 > d2: 
        return d1
    return d2


def merge_adjecent(statuses: list) -> list:
    if len(statuses) == 1:
        return statuses
    
    new_statuses = []
    prev = ""
    while statuses:
        next = statuses.pop(0)
        if len(new_statuses) == 0:
            new_statuses.append(next)
            continue
        prev = new_statuses.pop()

        if next[2] == prev[2]:
            new_status = [_dmin(prev[0], next[0]), _dmax(prev[1], next[1]), next[2]]
            new_statuses.append(new_status)
        else:
            new_statuses.append(prev)
            new_statuses.append(next)
    return new_statuses


def serialize_dates(statuses: list) -> list:
    for status in statuses:
        status[0] = status[0].isoformat()
        status[1] = status[1].isoformat()
    return statuses


def _normalize_party_name(pn):
    pn = re.sub(re_sc, "", pn)
    pn = pn.lower()
    pn = ''.join(c for c in unicodedata.normalize('NFD', pn)
                 if unicodedata.category(c) != 'Mn')
    return pn


def _map_country(c):
    if c == "Czechia":
        return "Czech Republic"
    return c


def parse_mep(response):
    http = fromstring(response.text)
    groups = []
    parties = []
    for elem in http.find_class("erpl_meps-status"):
        if elem.find_class("es_title-h4")[0].text_content() == "Political groups":
            for _group in elem.cssselect("ul li"):
                groups.append(parse_mep_status(_group.text_content()))

        elif elem.find_class("es_title-h4")[0].text_content() == "National parties":
            for _party in elem.cssselect("ul li"):
                parties.append(parse_mep_status(_party.text_content()))

    # merge adjecent dates
    groups = merge_adjecent(groups)
    parties = merge_adjecent(parties)
    return groups, parties


def load_mep(base_url: str, period: int, mepid: str, skipped: int = 0) -> tuple[list[tuple[datetime, str]], list[tuple[datetime, str]]]:
    """ Recursively 
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
        if mepid not in meps_with_large_term_pauses:
            skipped += 1
    # 3b. recurse - regular case 
    else:
        groups, parties = parse_mep(request)
    
    if period > 1:
        groups_new, parties_new = load_mep(base_url, period - 1, mepid, skipped)
        groups_new.extend(groups)
        parties_new.extend(parties)
        groups = merge_adjecent(groups_new)
        parties = merge_adjecent(parties_new)
    
    return groups, parties


def get_meps(
        ches_map: dict,
        group_map: dict, 
        verbose: bool):  
    """
    :param input_ids: Downloaded from https://www.europarl.europa.eu/meps/en/directory/xml (via https://www.europarl.europa.eu/meps/en/home -> Directory -> complete list -> download xml format)
    :param output_file: _description_
    :param groups_map: _description_
    :param ches_parties: _description_
    :param debug: _description_
    """
    backup_id_list_url = "https://www.europarl.europa.eu/meps/en/directory/xml"
    mep_temp_file = generated / "tmp-meps.json"

    # TODO scrape as a backup
    r = requests.get(backup_id_list_url, headers=headers)
    root = ElementTree.fromstring(r.content)
    meps = {element[1].text: element[0].text for element in root.findall(".//mep")}

    # TODO scrape as a backup
    # Skip Frans TIMMERMANS, who got elected but did not accept the seat -> he has a profile but no membership
    meps.pop("129141", None)
    for mepid, name in tqdm(meps.items(), desc="meps", total=len(meps.keys())):
        if isinstance(name, dict):
            continue
        try:
            r = requests.get(f"https://www.europarl.europa.eu/meps/en/{mepid}/")
            if not re.search(re_history, r.url):
                r = requests.get(f"{r.url[:-5]}/history")
            
            split = r.url.split("/")
            period = int(split[-1])
            base_url = f"{"/".join(split[:-1])}"
            groups, parties = load_mep(base_url, period, mepid)
            groups, parties = serialize_dates(groups), serialize_dates(parties)

            meps[mepid] = {
                "name": name,
                "groups": groups,
                "parties": parties,
                }
        except Exception as e:
            print(mepid)
            print(e)
            print(traceback.format_exc())
            json.dump(meps, open(mep_temp_file, 'w'), ensure_ascii=False, indent=4)

    logger.info(f"Finished scraping MEPs, saved the temporary results at {mep_temp_file.absolute()}")
    json.dump(meps, open(mep_temp_file, 'w'), ensure_ascii=False, indent=4)
    
    # Add CHES Scores
    inv_party_map = {}
    for _id, _party in ches_parties.items():
        inv_party_map.setdefault(_party["country"], {})
        _party["id"] = _id

        for _party_name in _party["name"]:
            inv_party_map[_party["country"]][_normalize_party_name(_party_name)] = _party

    party_misses = {}
    for _, mep in meps.items():
        # Add party membership to the mep
        new_parties = []
        for _party in mep["parties"]:
            party_name = _party[2]
            country_search = re.search(re_country, party_name)
            try:
                country = country_search.group(0).strip(" ()")
                party_name = re.sub(re_country, "", party_name).strip()
            except AttributeError:
                print("No country given", party_name)
                country = "None"
                party_name = party_name.strip()

            # map ches familiy to mep party membership
            party_mapped = inv_party_map.get(_map_country(country), {}).get(_normalize_party_name(party_name), {})
            if not party_mapped:
                party_misses.setdefault(country, set()).add(party_name)
                ches_family = ""
            else:
                ches_family = party_mapped.get("ches", {})[0].get("family", "")
            
            # add party id, abbrv, country to mep    
            new_parties.append({
                "name": party_name,
                "code": party_mapped.get("abbr", ""),
                "ches_family": ches_family,
                "ches_id": party_mapped.get("id", ""),
                "country": country,
                "from": _party[0], 
                "to": _party[1], 
            })
        mep["parties"] = new_parties

        new_groups = []
        for _group in mep["groups"]:
            _g = _group[2]
            new_groups.append({
                "name": _g,
                "code": groups_map[_g][0],
                "family": groups_map[_g][1],
                "from": _group[0], 
                "to": _group[1], 
            })
        mep["groups"] = new_groups

    ches_misses = {country: list(parties) for country, parties in party_misses.items()}

    return meps, ches_misses


if __name__ == "__main__":
    logger.warning("Don't call this file directly, use plenaryep.py")
    pass
