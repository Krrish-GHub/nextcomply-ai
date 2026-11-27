import io
import fitz
from fastapi.testclient import TestClient
from app.main import app, init_db
init_db()

client=TestClient(app)
def test_health_and_topics():
 page=client.get('/')
 assert page.status_code==200 and 'NextComply' in page.text and 'Source checks' in page.text
 assert client.get('/static/app.js').status_code==200
 assert client.get('/api/health').json()['status']=='ok'
 x=client.get('/api/regulations').json();assert len(x['items'])==8 and 'not connected' in x['notice'].lower()
def test_demo_question_abstains():
 x=client.post('/api/ask',json={'question':'NABH patient safety'}).json()
 assert x['confidence'] is None and 'cannot substantiate' in x['answer'].lower()
def test_findings_review_and_pdf():
 items=client.get('/api/findings').json()['items']; assert items
 ident=items[0]['id']
 r=client.post(f'/api/findings/{ident}/review',json={'status':'Reviewed','reviewer':'QA reviewer'})
 assert r.status_code==200 and r.json()['status']=='Reviewed'
 pdf=client.get('/api/report.pdf',params={'company_name':'North & Shore <Clinic>'});assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
 text='\n'.join(page.get_text() for page in fitz.open(stream=pdf.content,filetype='pdf'))
 assert 'Organisation: North & Shore <Clinic>' in text
def test_upload_and_phrase_analysis():
 f={'file':('sample.txt',io.BytesIO(b'Incident escalation has an assigned review owner.'),'text/plain')}
 up=client.post('/api/documents',files=f,data={'category':'NABH'});assert up.status_code==200,up.text
 doc=up.json();assert doc['characters']>0
 result=client.post(f"/api/documents/{doc['id']}/analyze?category=NABH").json()
 assert result['status']=='Pending review' and 'AI' in result['method']
def test_admin_protected():
 e=client.get('/api/employees').json()['items'][0]
 r=client.post(f"/api/employees/{e['id']}/ack",json={'acknowledged':True})
 assert r.status_code==403


def test_company_field_and_local_illustration():
 page=client.get('/')
 assert 'id="companyName"' in page.text and 'COMPLIANCE WORKSPACE' in page.text
 assert 'Demo Hospital' not in page.text and 'Good morning, team.' not in page.text
 assert 'LOCAL PREVIEW' in page.text
 image=client.get('/static/assets/healthcare-ad-studio.webp')
 assert image.status_code==200 and image.headers['content-type'].startswith('image/webp')
 js=client.get('/static/app.js').text
 assert 'nc_company_name' in js and 'reportHref' in js
 assert 'Document review' in js and 'Run AI compliance check' in js
 assert 'COMPLIANCE OPERATIONS · DEMO' not in js and 'Good morning, team.' not in js
 assert "saved.trim().toLowerCase()==='demo hospital'" in js


def test_notice_row_extraction_uses_observed_text_and_link():
    from app.source_checks import extract_notice_rows
    html='''<html><table><tr><td>12 Nov 2026</td><td><a href="/notice/42">Updated public notice</a></td><td>Read the new circular.</td></tr></table></html>'''
    items=extract_notice_rows(html,'https://example.gov/notices')
    assert len(items)==1
    assert items[0]['title']=='Updated public notice'
    assert items[0]['date']=='12 Nov 2026'
    assert items[0]['url']=='https://example.gov/notice/42'
    assert 'new circular' in items[0]['snippet']
    assert extract_notice_rows('<table><tr><td>Sitemap</td><td><a href="/about">Full Member</a></td></tr></table>','https://example.gov')==[]


def test_source_fetch_mocked_success_failure_and_unparsed():
    import asyncio, httpx
    from app.source_checks import check_source
    async def run():
        def handler(request):
            if request.url.path=='/ok':
                return httpx.Response(200,headers={'content-type':'text/html'},text='<table><tr><td>2026-11-24</td><td><a href="/notice">Official notice title</a></td></tr></table>')
            if request.url.path=='/blocked': return httpx.Response(503,headers={'content-type':'text/html'},text='Unavailable')
            if request.url.path=='/timeout': raise httpx.ReadTimeout('slow page',request=request)
            return httpx.Response(200,headers={'content-type':'text/html'},text='<html><p>Navigation only</p></html>')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=True) as client:
            ok=await check_source(client,{'id':'test','name':'Test source','kind':'Government regulator','url':'https://example.gov/ok'})
            failed=await check_source(client,{'id':'blocked','name':'Blocked source','kind':'Government regulator','url':'https://example.gov/blocked'})
            empty=await check_source(client,{'id':'unparsed','name':'Unparsed source','kind':'Government regulator','url':'https://example.gov/unparsed'})
            timeout=await check_source(client,{'id':'timeout','name':'Slow source','kind':'Government regulator','url':'https://example.gov/timeout'})
        return ok,failed,empty,timeout
    ok,failed,empty,timeout=asyncio.run(run())
    assert ok['status']=='checked' and ok['items'][0]['title']=='Official notice title'
    assert failed['status']=='failed' and 'HTTP 503' in failed['message'] and not failed['items']
    assert 'no notice result can be inferred' in failed['message']
    assert empty['status']=='checked' and empty['items']==[] and 'no reliable notice rows' in empty['message']
    assert timeout['status']=='failed' and 'timed out' in timeout['message'] and not timeout['items']


def test_manual_source_check_is_saved_and_returns_per_site_status(monkeypatch):
    import asyncio
    import app.main as main
    result=[{'id':'cdsco','name':'CDSCO','kind':'Government regulator','url':'https://example.gov/notices','checked_at':'2026-11-25T10:00:00+00:00','status':'checked','message':'Fetched page and extracted 1 visible notice row(s).','items':[{'title':'Observed notice','date':'2026-11-24','url':'https://example.gov/notice/1','snippet':'Observed notice text.'}]},{'id':'rbi','name':'RBI','kind':'Government regulator','url':'https://example.gov/rbi','checked_at':'2026-11-25T10:00:00+00:00','status':'failed','message':'Request timed out; no notice result can be inferred.','items':[]}]
    async def fake_check_all_sources(): return result
    monkeypatch.setattr(main,'check_all_sources',fake_check_all_sources)
    response=client.post('/api/source-checks')
    assert response.status_code==200,response.text
    assert response.json()['results'][1]['status']=='failed'
    saved=client.get('/api/source-checks/latest').json()
    assert saved['results']==result and saved['checked_at']
    assert 'no notice result' in saved['results'][1]['message']
