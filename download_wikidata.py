#!/usr/bin/python

import requests
from pathlib import Path
from datetime import datetime, timedelta
import json
import logging
import argparse
from jinja2 import Environment, FileSystemLoader
from urllib.parse import urlsplit, unquote


class Input:
    wikidata_cache: Path

    def __init__(self, wikidata_cache: Path):
        self.wikidata_cache = wikidata_cache

    def is_cache_outdated(self):
        """
        Return a boolean to tell if the wikidata cache is older than one day.
        """
        if not self.wikidata_cache.exists():
            return True

        limit = datetime.now() - timedelta(days=1)
        mtime = datetime.fromtimestamp(self.wikidata_cache.stat().st_mtime)
        return limit > mtime

    def get_data_from_wikidata(self):
        if not self.is_cache_outdated():
            logging.info("Loading from cache")
            with self.wikidata_cache.open() as f:
                return json.load(f)

        url = 'https://query.wikidata.org/sparql'
        query = '''
        SELECT ?site ?siteLabel ?lon ?lat ?common
               (SAMPLE(?all_image) AS ?image)
               (GROUP_CONCAT(DISTINCT ?article; SEPARATOR="|") AS ?articles)
               (GROUP_CONCAT(DISTINCT ?wiki;    SEPARATOR="|") AS ?wikis)
        WHERE {
          ?site wdt:P31/wdt:P279* wd:Q6838244 .
          OPTIONAL { ?site p:P625/psv:P625 [ wikibase:geoLongitude ?lon ; wikibase:geoLatitude ?lat ] . }
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
          OPTIONAL { ?site wdt:P373 ?common . }
          OPTIONAL { ?site wdt:P18 ?all_image . }
          OPTIONAL {
            ?article schema:about ?site ;
                   schema:isPartOf ?wiki .
          }
        }
        GROUP BY ?site ?siteLabel ?lon ?lat ?common
        '''

        headers = {
                'User-Agent': 'China_5A/0.1 (https://github.com/alkino/china_5a; me@alkino.fr)'
        }
        r = requests.get(url,
                         params={'format': 'json', 'query': query},
                         headers=headers)

        if r.status_code != 200:
            logging.error(r.text)
            exit()

        data = r.json()

        sites = dict()

        for i in data['results']['bindings']:
            url = i["site"]["value"]
            QID = urlsplit(url).path.rsplit("/")[-1]

            if QID in sites:
                logging.warning(f"There is a duplicate for '{QID}'!")

            sites[QID] = dict()
            site = sites[QID]

            site["name"] = i["siteLabel"]["value"]
            site["image"] = i["image"]["value"] if "image" in i else None
            site["lat"] = i["lat"]["value"] if "lat" in i else None
            site["lon"] = i["lon"]["value"] if "lon" in i else None
            site["common"] = i["common"]["value"] if "common" in i else None

            articles = i["articles"]["value"].split("|") if "articles" in i else []
            wikis = [a for a in articles if a and "en.wikipedia.org" in a]
            if wikis:
                site["slug"] = unquote(wikis[0].rsplit('/')[-1])
            else:
                site["slug"] = None

        titles = {sites[qid]["slug"]: qid for qid in sites if sites[qid]["slug"] is not None}
        extracts = self.get_data_from_wiki(titles, 'en')

        for s in sites:
            sites[s]["en_wiki"] = extracts.get(s, "")

        with self.wikidata_cache.open('w') as f:
            json.dump(sites, f)

    def parse_wikidata_cache(self):
        with self.wikidata_cache.open('r') as f:
            data = json.load(f)
        sites = []
        for k, v in data.items():
            if v["image"]:
                image_name = v["image"].rsplit('/')[-1]
            else:
                logging.info(f"'{v['name']}' has no picture")
                image_name = None
            site = {
                "name": v['name'],
                "lon": v['lon'],
                "lat": v['lat'],
                "common": v['common'],
                "image": image_name,
                "en_wiki": v['en_wiki'],
            }
            if site['lon'] is None or site['lat'] is None:
                logging.warning(f"'{site["name"]}' has no location")
            else:
                sites.append(site)
        return sites

    def get_data_from_wiki(self, titles, lang="en"):
        url = f"https://{lang}.wikipedia.org/w/api.php"
        result = {}

        s = requests.Session()
        s.headers.update({
            'User-Agent': 'China_5A/0.1 (https://github.com/alkino/china_5a; me@alkino.fr)'
            })

        batch_size = 20
        for start_idx in range(0, len(titles), batch_size):
            batch = list(titles.keys())[start_idx:start_idx + batch_size]
            batch = [a.replace(' ', '_') for a in batch]
            params = {
                "action": "query",
                "titles": '|'.join(batch),
                "prop": "extracts",
                "exintro": 1,
                "format": "json",
                "redirects": 1,
            }
            r = s.get(url, params=params)
            data = r.json()

            pages = data["query"]["pages"]
            for page in pages.values():
                title = page["title"].replace(' ', '_')
                if "extract" in page:
                    if title in titles:
                        result[titles[title]] = page["extract"]
                    else:
                        logging.warning(f"'{title}' seems to be a redirection")
                else:
                    logging.warning(f"'{title}' has no extract in wikipedia")

        return result


def generate_site(sites, outdir):
    website_out = outdir / "website"
    website_out.mkdir(parents=True, exist_ok=True)

    env = Environment(loader=FileSystemLoader('templates'))
    template = env.get_template('index.html')

    carte_html = template.render(
        title="Sites 5A Chine",
        sites_json=sites,
        center_lat=39.9,
        center_lon=105.0,
        zoom=5,
    )
    (website_out / "index.html").write_text(carte_html)

    (Path("templates") / "style.css").copy_into(website_out)
    (Path("templates") / "china_provinces.js").copy_into(website_out)


def generate_gpx(sites, outdir):
    website_out = outdir / "website"
    website_out.mkdir(parents=True, exist_ok=True)

    gpx_str = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
    <gpx>
"""

    for s in sites:
        if "lat" in s and "lon" in s:
            gpx_str += f"        <wpt lat=\"{s["lat"]}\" lon=\"{s["lon"]}\"><name>{s["name"]}</name></wpt>\n"

    gpx_str += "</gpx>"

    (website_out / "china_5a.gpx").write_text(gpx_str)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create map for 5A in China")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--clean-wikidata-cache", action="store_true")
    parser.add_argument("--logging", type=str, choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.logging)
    cache_dir = args.output / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if args.clean_wikidata_cache:
        (cache_dir / "wikidata.cache").unlink(missing_ok=True)

    openData = Input(cache_dir / "wikidata.cache")
    openData.get_data_from_wikidata()
    sites = openData.parse_wikidata_cache()

    generate_site(sites, args.output)
    generate_gpx(sites, args.output)
