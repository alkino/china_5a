#!/usr/bin/python

import requests
from pathlib import Path
from datetime import datetime, timedelta
import json
import logging
import argparse
from jinja2 import Environment, FileSystemLoader


class Input:
    wikidata_cache: Path

    def __init__(self, wikidata_cache: Path):
        self.wikidata_cache = wikidata_cache

    def is_cache_outdated(self):
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
        SELECT ?site ?siteLabel ?lon ?lat ?common ?article ?wiki
               (SAMPLE(?all_image) AS ?image) WHERE {
          ?site wdt:P31/wdt:P279* wd:Q6838244 .
          OPTIONAL { ?site p:P625/psv:P625 [ wikibase:geoLongitude ?lon ; wikibase:geoLatitude ?lat ] . }
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
          OPTIONAL { ?site wdt:P373 ?common . }
          OPTIONAL { ?site wdt:P18 ?all_image . }
          ?article schema:about ?site ;
                   schema:isPartOf ?wiki .
          FILTER(CONTAINS(STR(?wiki), "en.wikipedia.org"))
        }
        GROUP BY ?site ?siteLabel ?lon ?lat ?common ?article ?wiki
        '''

        headers = {
                'User-Agent': 'China_5A/0.1 (https://github.com/alkino/china_5a; me@alkino.fr)'
        }
        r = requests.get(url, params={'format': 'json', 'query': query}, headers=headers)

        if r.status_code != 200:
            print(r.text)
            exit()

        data = r.json()
        print([i for i in data['results']['bindings'] if 'article' not in i])
        print("====")
        titles = [i['article']['value'] for i in data['results']['bindings'] if 'article' in i]
        titles = [i.rsplit('/')[-1] for i in titles]
        extracts = self.get_data_from_wiki(titles)

        for i in data['results']['bindings']:
            i["intro"] = extracts[i['siteLabel']['value']] if i['siteLabel']['value'] in extracts else ""


        with self.wikidata_cache.open('w') as f:
            json.dump(data, f)

    def parse_wikidata_cache(self):
        with self.wikidata_cache.open('r') as f:
            data = json.load(f)
        sites = []
        for i in data["results"]["bindings"]:
            if 'image' in i:
                link = i['image']['value']
                image_name = link.rsplit('/')[-1]
            else:
                logging.debug(f"${i['siteLabel']['value']} has no picture")
                image_name = None
            site = {
                "name": i['siteLabel']['value'],
                "lon": i['lon']['value'] if 'lon' in i else None,
                "lat": i['lat']['value'] if 'lat' in i else None,
                "common": i['common']['value'] if 'common' in i else None,
                "image": image_name,
                "en_wiki": i['intro'],
            }
            if site['lon'] is None or site['lat'] is None:
                print(site)
            sites.append(site)
        return sites

    def get_data_from_wiki(self, titles, lang="en"):
        url = f"https://{lang}.wikipedia.org/w/api.php"
        result = {}
        headers = {
            'User-Agent': 'China_5A/0.1 (https://github.com/alkino/china_5a; me@alkino.fr)'
        }

        batch_size = 50
        for start_idx in range(0, len(titles), batch_size):
            batch = titles[start_idx:start_idx + batch_size]
            params = {
                "action": "query",
                "titles": '|'.join(batch),
                "prop": "extracts",
                "format": "json",
                "exintro": 1,
                "redirects": 1,
            }
            r = requests.get(url, params=params, headers=headers)
            data = r.json()
            pages = data["query"]["pages"]
            for page in pages.values():
                title = page["title"]
                if "extract" in page:
                    result[title] = page["extract"]

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
