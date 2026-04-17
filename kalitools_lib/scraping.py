import json
import re

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    BeautifulSoup = None  # type: ignore


def parse_tool_page(html: str) -> tuple[str, str | None, list[str]] | None:
    """Parse a Kali tool page HTML, returning (package, category, tags).

    - Prefers structured <dl><dt>Package</dt><dd>name</dd>
    - Extracts tags from <dt>Tags</dt><dd>...</dd> or similar
    - Maps tags to a coarse category using a simple mapping
    """
    if not BeautifulSoup:
        return None
    soup = BeautifulSoup(html, 'html.parser')

    # Package
    package_candidates: list[str] = []
    for dl in soup.find_all('dl'):
        for dt in dl.find_all('dt'):
            label = dt.get_text(strip=True).lower()
            if label in {'package', 'tool', 'name'}:
                dd = dt.find_next('dd')
                if dd:
                    txt = dd.get_text(strip=True).lower()
                    if re.match(r'^[a-z0-9][a-z0-9+\-.]{2,}$', txt):
                        package_candidates.append(txt)
    pkg = package_candidates[0] if package_candidates else None

    # Fallback 1: meta tags
    if not pkg:
        meta = soup.find('meta', attrs={'name': 'package'})
        if meta and meta.get('content'):
            pkg = meta['content'].strip().lower()

    # Fallback 2: JSON-LD script with potential name
    if not pkg:
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string or '{}')
                if isinstance(data, dict):
                    candidate = data.get('name') or data.get('headline')
                    if candidate and re.match(r'^[a-z0-9][a-z0-9+\-.]{2,}$', candidate.lower()):
                        pkg = candidate.lower()
                        break
            except Exception:
                continue

    # Fallback 3: textual regex search
    if not pkg:
        text = soup.get_text('\n', strip=True)
        m = re.search(r'Package\s*:\s*([a-z0-9][a-z0-9+\-.]+)', text, re.IGNORECASE)
        if m:
            pkg = m.group(1).lower()

    if not pkg:
        return None

    # Tags and category
    tag_values: list[str] = []
    for dl in soup.find_all('dl'):
        for dt in dl.find_all('dt'):
            label = dt.get_text(strip=True).lower()
            if any(k in label for k in ('category', 'tags', 'tag')):
                dd = dt.find_next('dd')
                if dd:
                    links = [a.get_text(strip=True).lower() for a in dd.find_all('a') if a.get_text(strip=True)]
                    if links:
                        tag_values.extend(links)
                    else:
                        raw = dd.get_text(' ', strip=True).lower()
                        tag_values.extend([t.strip() for t in re.split(r'[;,]', raw) if t.strip()])

    # Fallback: look for a tag cloud div or list
    if not tag_values:
        tag_cloud = soup.find(class_=re.compile(r'(tag|category)-cloud')) or soup.find(id=re.compile(r'(tags|categories)'))
        if tag_cloud:
            tag_values.extend([a.get_text(strip=True).lower() for a in tag_cloud.find_all('a') if a.get_text(strip=True)])

    # Fallback: table rows mentioning Category/Tags
    if not tag_values:
        for tr in soup.find_all('tr'):
            cells = tr.find_all(['th','td'])
            if len(cells) >= 2:
                header = cells[0].get_text(strip=True).lower()
                if any(k in header for k in ('category','tags','tag')):
                    links = [a.get_text(strip=True).lower() for a in cells[1].find_all('a') if a.get_text(strip=True)]
                    if links:
                        tag_values.extend(links)
                    else:
                        raw = cells[1].get_text(' ', strip=True).lower()
                        tag_values.extend([t.strip() for t in re.split(r'[;,]', raw) if t.strip()])

    mapping = {
        'web': 'web', 'crawler': 'web', 'http': 'web', 'recon': 'recon', 'enumeration': 'recon',
        'wireless': 'wireless', 'wifi': 'wireless', 'forensics': 'forensics', 'memory': 'forensics',
        'exploitation': 'exploitation', 'exploit': 'exploitation', 'password': 'password', 'cracking': 'password',
        'bruteforce': 'password', 'sniffing': 'sniffing', 'capture': 'sniffing', 'reverse': 'reverse',
        'phishing': 'social', 'social': 'social', 'database': 'database', 'sql': 'database'
    }
    category: str | None = None
    for tag in tag_values:
        for key, cat in mapping.items():
            if key in tag:
                category = cat
                break
        if category:
            break
    if not category and tag_values:
        category = 'other'

    return pkg, category, tag_values
