"""Inspect public publication links while developing historical connectors."""
import argparse
import sys
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from civic_metrics.http import HttpClient

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('urls', nargs='+')
args = parser.parse_args()
http = HttpClient(30)
try:
    for url in args.urls:
        response = http.get(url)
        print('\nPAGE', url)
        soup = BeautifulSoup(response.body, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = urljoin(response.source_url, a['href'])
            label = a.get_text(' ', strip=True)
            if any(s in (label + href).lower() for s in (
                '202', '.xls', 'histor', 'anterior', 'serie', 'tabla', 'jaxi',
                'pension', 'afilia', 'cuadro', 'extracto', 'dato',
            )):
                print(label, '::', href)
finally:
    http.close()
