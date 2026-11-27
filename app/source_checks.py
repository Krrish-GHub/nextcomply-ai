"""Manual, best-effort checks of fixed public regulator notice pages."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import httpx

SOURCE_DIRECTORY = [
    {"id":"cdsco", "name":"CDSCO", "kind":"Government regulator", "url":"https://www.cdsco.gov.in/opencms/opencms/en/Latest-Public-Notices/"},
    {"id":"irdai", "name":"IRDAI", "kind":"Government regulator", "url":"https://irdai.gov.in/circulars"},
    {"id":"rbi", "name":"RBI", "kind":"Government regulator", "url":"https://notifics.rbi.org.in/"},
    {"id":"nmc", "name":"National Medical Commission (NMC)", "kind":"Statutory regulator", "url":"https://nmc.org.in/whats-new/"},
    {"id":"pcpndt", "name":"MoHFW · PCPNDT portal", "kind":"Government ministry portal", "url":"https://www.pndt.mohfw.gov.in/"},
    {"id":"dpdp", "name":"MeitY · DPDP resources", "kind":"Government ministry", "url":"https://www.meity.gov.in/data-protection-framework"},
    {"id":"certin", "name":"CERT-In advisories", "kind":"Government cybersecurity agency", "url":"https://www.cert-in.org.in/s2cMainServlet?pageid=PUBADVLIST02"},
    {"id":"nabh", "name":"NABH announcements", "kind":"Accreditation body · not a government regulator", "url":"https://nabh.co/"},
]

class _Rows(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.rows=[]; self.row=None; self.anchor=None
        self.in_script=0; self.in_style=0
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag in ('script','style'): self.in_script += tag=='script'; self.in_style += tag=='style'
        if tag=='tr':
            self.row={'text':[], 'links':[]}; self.rows.append(self.row)
        if tag=='a' and self.row is not None: self.anchor={'text':[], 'href':attrs.get('href','')}
    def handle_endtag(self, tag):
        if tag=='a' and self.anchor is not None and self.row is not None:
            self.row['links'].append(self.anchor); self.anchor=None
        if tag=='tr': self.row=None
        if tag=='script' and self.in_script: self.in_script-=1
        if tag=='style' and self.in_style: self.in_style-=1
    def handle_data(self, data):
        if self.in_script or self.in_style: return
        if self.row is not None:
            self.row['text'].append(data)
            if self.anchor is not None: self.anchor['text'].append(data)

def _date(text: str):
    import re
    # Prefer full dates, avoiding table serial numbers mistaken for partial dates.
    patterns=(r'\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b',r'\b\d{4}-\d{2}-\d{2}\b',r'\b\d{1,2}[ ./-](?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[ ./-]\d{2,4}\b')
    for pattern in patterns:
        match=re.search(pattern,text,re.I)
        if match:return match.group(0)
    return None

def extract_notice_rows(html: str, page_url: str, source_id: str|None=None):
    parser=_Rows(); parser.feed(html); items=[]; seen=set()
    nav_phrases=('sitemap','full member','operational member','accredited member','global research partner','associate partner','short description','sl no description document published on','directions by cert-in under section 70b')
    notice_terms=('advisory','circular','notification','public notice','press release','gazette','draft','order','corrigendum')
    for row in parser.rows:
        text=' '.join(' '.join(row['text']).split())
        normalized=text.casefold()
        if len(text)<10 or any(term in normalized for term in nav_phrases): continue
        links=[]
        for link in row['links']:
            title=' '.join(' '.join(link['text']).split())
            href=urljoin(page_url,link['href']) if link['href'] else ''
            if title and href and urlparse(href).scheme in ('http','https'):
                links.append((title,href))
        # Only emit dated rows or clearly named notice-like links; do not turn menus into updates.
        observed_date=_date(text)
        useful=[(t,u) for t,u in links if len(t)>8 and any(term in t.casefold() for term in notice_terms)]
        dated_link=next(((t,u) for t,u in links if len(t)>8 and t.casefold() not in ('circular','document','download')),None)
        if source_id=='certin' and not observed_date: continue
        if not observed_date and not useful: continue
        chosen=useful[0] if useful else dated_link
        generic=not chosen or chosen[0].casefold() in ('circular','document','download') or chosen[0].casefold().endswith(' / circular')
        # Some public tables use a generic anchor label; retain the observed row text as its title.
        title=text if generic else chosen[0]
        url=(chosen[1] if chosen else (links[0][1] if links else page_url))
        key=(title,url)
        if key in seen: continue
        seen.add(key)
        items.append({'title':title[:240], 'date':observed_date, 'url':url, 'snippet':text[:360]})
        if len(items)>=12: break
    return items

async def check_source(client: httpx.AsyncClient, source: dict):
    checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds')
    base={**source,'checked_at':checked_at,'items':[]}
    try:
        request_url=source['url']
        if source['id']=='certin':
            separator='&' if '?' in request_url else '?'
            request_url=f'{request_url}{separator}year={datetime.now(timezone.utc).year}'
            base['url']=request_url
        response=await client.get(request_url)
        if response.status_code<200 or response.status_code>=300:
            return {**base,'status':'failed','message':f'HTTP {response.status_code}; page content was not assessed, so no notice result can be inferred.'}
        ctype=response.headers.get('content-type','').lower()
        if 'html' not in ctype and 'text/plain' not in ctype:
            return {**base,'status':'failed','message':'Page returned a non-HTML response; notices were not parsed.'}
        items=extract_notice_rows(response.text,str(response.url),source['id'])
        if items:
            return {**base,'status':'checked','items':items,'message':f'Fetched page and extracted {len(items)} visible notice row(s); review source pages for completeness.'}
        return {**base,'status':'checked','message':'Page loaded, but no reliable notice rows were extracted; open the source page to review.'}
    except httpx.TimeoutException:
        return {**base,'status':'failed','message':'Request timed out; no notice result can be inferred.'}
    except httpx.RequestError as exc:
        return {**base,'status':'failed','message':f'Network request failed ({type(exc).__name__}); no notice result can be inferred.'}
    except Exception as exc:
        return {**base,'status':'failed','message':f'Could not parse this page ({type(exc).__name__}); no notice result can be inferred.'}

async def check_all_sources():
    limits=httpx.Limits(max_connections=4,max_keepalive_connections=4)
    timeout=httpx.Timeout(12.0,connect=6.0)
    headers={'User-Agent':'NextComply-Local-Preview/1.0 (manual public-source check)'}
    async with httpx.AsyncClient(follow_redirects=True,timeout=timeout,limits=limits,headers=headers) as client:
        semaphore=asyncio.Semaphore(4)
        async def run(source):
            async with semaphore:return await check_source(client,source)
        return await asyncio.gather(*(run(source) for source in SOURCE_DIRECTORY))
