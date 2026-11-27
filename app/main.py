from __future__ import annotations
import hashlib, json, os, re, secrets, sqlite3, uuid
from datetime import datetime, timezone
from xml.sax.saxutils import escape
from pathlib import Path
from typing import Any

import fitz
from docx import Document
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from app.llm_client import analyze_document_with_api, ask_with_api, ocr_images_with_api, review_ad_with_api
from app.source_checks import SOURCE_DIRECTORY, check_all_sources
from io import BytesIO

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv('DATABASE_PATH', './data/nextcomply.db')).resolve()
UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', './data/uploads')).resolve()
MAX_UPLOAD = int(os.getenv('MAX_UPLOAD_MB', '15')) * 1024 * 1024
APP_ENV = os.getenv('APP_ENV', 'development')

app = FastAPI(title='NextComply AI', version='0.1.0', description='Advisory healthcare compliance workflow demo')
app.mount('/static', StaticFiles(directory=ROOT / 'app' / 'static'), name='static')

REGULATIONS = [
 {'code':'NABH','name':'National Accreditation Board for Hospitals & Healthcare Providers','domain':'Hospital quality and patient safety','scope':'Accreditation standards and quality systems','status':'topic_only'},
 {'code':'CDSCO','name':'Central Drugs Standard Control Organisation','domain':'Drugs, clinical trials and medical devices','scope':'Central drug and medical-device regulatory functions','status':'topic_only'},
 {'code':'IRDAI','name':'Insurance Regulatory and Development Authority of India','domain':'Insurance-sector oversight','scope':'Insurance-sector requirements; hospital applicability varies','status':'topic_only'},
 {'code':'RBI','name':'Reserve Bank of India','domain':'Banking and payment systems','scope':'Financial-sector requirements; applicability must be established','status':'topic_only'},
 {'code':'NMC','name':'National Medical Commission','domain':'Medical education and professional regulation','scope':'Professional and medical education framework','status':'topic_only'},
 {'code':'PCPNDT','name':'Pre-Conception and Pre-Natal Diagnostic Techniques','domain':'Prenatal diagnostic practices','scope':'Applicability depends on facility services and registration','status':'topic_only'},
 {'code':'DPDP','name':'Digital Personal Data Protection','domain':'Personal data governance','scope':'Applicability and commencement must be checked against current official material','status':'topic_only'},
 {'code':'CERT-In','name':'Indian Computer Emergency Response Team','domain':'Cyber incident response and security','scope':'Cybersecurity directions; applicability and current text require verification','status':'topic_only'},
]
SEED_FINDINGS = [
 ('F-104','NABH','Sample: incident review record','Illustrative policy excerpt does not show a named review owner. This is a document-observation prompt, not a compliance conclusion.','Illustrative incident policy excerpt','High','Ask the quality lead to compare the complete controlled procedure with current approved criteria; record rationale and owner.'),
 ('F-103','DPDP','Sample: retention schedule','No retention period appears in the illustrative excerpt. Context, retention purpose, and applicable rules are not assessed.','Illustrative records-retention policy excerpt','Medium','Have the privacy lead validate the schedule against current requirements and documented operational needs.'),
 ('F-102','CERT-In','Sample: incident contact','The sample excerpt does not name an internal incident contact. No official source was retrieved.','Illustrative IT incident SOP excerpt','Low','Confirm the current escalation roster and document the source and review date.'),
]
SEED_ALERTS = [
 ('A-81','Sample incident escalation awaiting review','Patient safety','Critical','2026-11-25T09:20:00+05:30',0),
 ('A-80','Sample policy acknowledgement due','Workforce','High','2026-11-25T08:10:00+05:30',0),
 ('A-79','Sample audit evidence added','Audit','Medium','2026-11-24T16:45:00+05:30',1),
]
SEED_EMPLOYEES = [('E-014','Aarav Mehta','Nursing · ICU',0,'Overdue'),('E-021','Mira Shah','Quality officer',1,'Complete'),('E-033','Kabir Rao','Clinical operations',0,'Pending'),('E-047','Anaya Das','Information security',1,'Complete')]
POLICIES = [
 {'id':'SOP-01','title':'Incident reporting & escalation','owner':'Quality and patient-safety office','revision':'Illustrative sample · 12 Oct 2026','topic':'Incident management','excerpt':'Reported events are entered in a case log, assigned an accountable reviewer, and tracked through documented follow-up and closure evidence.'},
 {'id':'SOP-02','title':'User access review','owner':'Information security','revision':'Illustrative sample · 03 Sep 2026','topic':'Access governance','excerpt':'Role changes are reviewed by an authorised manager. Access changes and periodic recertification outcomes are recorded for follow-up.'},
 {'id':'SOP-03','title':'Controlled document management','owner':'Quality systems','revision':'Illustrative sample · 18 Aug 2026','topic':'Document control','excerpt':'Controlled procedures identify an owner, version, effective date, approval history, and scheduled review. Superseded copies are removed from active circulation.'},
 {'id':'SOP-04','title':'Staff training records','owner':'Human resources','revision':'Illustrative sample · 02 Nov 2026','topic':'Workforce','excerpt':'Training completion is recorded against a role and assigned module. Overdue items are escalated to the department manager for follow-up.'},
]

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def conn():
 DB_PATH.parent.mkdir(parents=True, exist_ok=True)
 c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def init_db():
 with conn() as c:
  c.executescript('''
  CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,filename TEXT NOT NULL,sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,mime TEXT NOT NULL,uploaded_at TEXT NOT NULL,status TEXT NOT NULL,extracted_chars INTEGER NOT NULL DEFAULT 0,content TEXT NOT NULL DEFAULT '',demo INTEGER NOT NULL DEFAULT 0);
  CREATE TABLE IF NOT EXISTS findings(id TEXT PRIMARY KEY,document_id TEXT,category TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,source TEXT NOT NULL,severity TEXT NOT NULL,action TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'Pending review',demo INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,reviewer TEXT,review_note TEXT,FOREIGN KEY(document_id) REFERENCES documents(id));
  CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY,title TEXT NOT NULL,category TEXT NOT NULL,severity TEXT NOT NULL,created_at TEXT NOT NULL,reviewed INTEGER NOT NULL DEFAULT 0,reviewer TEXT);
  CREATE TABLE IF NOT EXISTS employees(id TEXT PRIMARY KEY,name TEXT NOT NULL,role TEXT NOT NULL,policy_ack INTEGER NOT NULL,training TEXT NOT NULL,updated_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS audits(id TEXT PRIMARY KEY,title TEXT NOT NULL,reviewer TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT NOT NULL,action TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,role TEXT NOT NULL,display_name TEXT NOT NULL,created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS source_check_runs(id TEXT PRIMARY KEY,checked_at TEXT NOT NULL,results_json TEXT NOT NULL);
  ''')
  if c.execute('SELECT COUNT(*) n FROM findings').fetchone()['n']==0:
   for row in SEED_FINDINGS: c.execute('INSERT INTO findings(id,category,title,description,source,severity,action,status,demo,created_at) VALUES(?,?,?,?,?,?,? ,?,1,?)',(*row,'Pending review',now()))
  if c.execute('SELECT COUNT(*) n FROM alerts').fetchone()['n']==0:
   c.executemany('INSERT INTO alerts VALUES(?,?,?,?,?,?,?)',[(i,t,cat,sev,dt,rev,None) for i,t,cat,sev,dt,rev in SEED_ALERTS])
  if c.execute('SELECT COUNT(*) n FROM employees').fetchone()['n']==0:
   c.executemany('INSERT INTO employees VALUES(?,?,?,?,?,?)',[(i,n,r,a,tr,now()) for i,n,r,a,tr in SEED_EMPLOYEES])
  if c.execute('SELECT COUNT(*) n FROM audits').fetchone()['n']==0:
   c.execute('INSERT INTO audits VALUES(?,?,?,?,?)',('AUD-001','Quarterly patient-safety process review','Quality lead','In progress',now()))

def log(c, actor, action, kind, ident, details=''):
 c.execute('INSERT INTO audit_log(actor,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)',(actor,action,kind,ident,details,now()))
def user_role(authorization: str | None):
 if APP_ENV == 'development' and not authorization: return {'name':'Compliance reviewer','role':'Compliance Officer'}
 token=(authorization or '').removeprefix('Bearer ').strip()
 with conn() as c: row=c.execute('SELECT role,display_name FROM sessions WHERE token=?',(token,)).fetchone()
 if not row: raise HTTPException(401,'Sign in using the clearly labelled local demo account.')
 return {'name':row['display_name'],'role':row['role']}
def require_admin(user):
 if user['role']!='Admin': raise HTTPException(403,'This action requires the local preview administrator role.')

class Login(BaseModel): email: str; password: str; role: str='Compliance Officer'
class Review(BaseModel): status: str=Field(pattern='^(Pending review|Reviewed|Resolved)$'); reviewer: str=Field(min_length=1,max_length=120); note: str=''
class Ack(BaseModel): acknowledged: bool; actor: str='Demo Admin'
class Training(BaseModel): status: str=Field(pattern='^(Complete|Pending|Overdue)$'); actor: str='Demo Admin'
class AuditIn(BaseModel): title: str=Field(min_length=3,max_length=160); reviewer: str=Field(min_length=2,max_length=120)
class Ask(BaseModel): question: str=Field(min_length=3,max_length=1000)
class AdReviewIn(BaseModel): text: str=Field(min_length=1,max_length=2000)

@app.on_event('startup')
def startup(): init_db()
@app.get('/',response_class=HTMLResponse)
def home(): return FileResponse(ROOT/'app'/'static'/'index.html')
@app.get('/api/health')
def health(): return {'status':'ok','mode':'illustrative_demo','time':now()}
@app.post('/api/login')
def login(data: Login):
 if APP_ENV!='development': raise HTTPException(501,'Configure an identity provider before non-demo use.')
 valid={'officer@demo.local':'Compliance Officer','admin@demo.local':'Admin'}
 expected=valid.get(data.email.lower())
 # The displayed demo password is deliberately not a real credential; no patient data belongs here.
 if not expected or data.password!='NextComply-Demo-2026' or data.role!=expected: raise HTTPException(401,'Demo credentials or selected role did not match.')
 token=secrets.token_urlsafe(32)
 display_name='Administrator' if expected=='Admin' else 'Compliance reviewer'
 with conn() as c: c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(token,expected,display_name,now()))
 return {'token':token,'role':expected,'name':display_name,'demo':True}
@app.get('/api/me')
def me(authorization: str|None=Header(default=None)): return {**user_role(authorization),'demo':True,'warning':'Local demo identity only; not production authentication.'}

@app.get('/api/dashboard')
def dashboard(authorization: str|None=Header(default=None)):
 user_role(authorization)
 with conn() as c:
  count=lambda q:c.execute(q).fetchone()[0]
  return {'documents':count('SELECT COUNT(*) FROM documents'),'findings':count('SELECT COUNT(*) FROM findings'),'pending_reviews':count("SELECT COUNT(*) FROM findings WHERE status='Pending review'"),'resolved':count("SELECT COUNT(*) FROM findings WHERE status='Resolved'"),'open_alerts':count('SELECT COUNT(*) FROM alerts WHERE reviewed=0'),'employees':count('SELECT COUNT(*) FROM employees'),'coverage_topics':len(REGULATIONS),'recent_activity':[dict(r) for r in c.execute('SELECT actor,action,entity_type,entity_id,details,created_at FROM audit_log ORDER BY id DESC LIMIT 8')]}
@app.get('/api/regulations')
def regulations(q: str='',authorization: str|None=Header(default=None)):
 user_role(authorization); term=q.lower()
 return {'items':[r for r in REGULATIONS if term in json.dumps(r).lower()], 'notice':'Directory topics only; official source text is not connected or verified.'}
@app.get('/api/source-checks/latest')
def latest_source_checks(authorization: str|None=Header(default=None)):
 user_role(authorization)
 with conn() as c: row=c.execute('SELECT checked_at,results_json FROM source_check_runs ORDER BY rowid DESC LIMIT 1').fetchone()
 if not row:return {'checked_at':None,'sources':SOURCE_DIRECTORY,'results':[],'message':'No source check has been run yet. Checks run only when you request them.'}
 return {'checked_at':row['checked_at'],'sources':SOURCE_DIRECTORY,'results':json.loads(row['results_json'])}

@app.post('/api/source-checks')
async def run_source_checks(authorization: str|None=Header(default=None)):
 user=user_role(authorization)
 results=await check_all_sources(); checked_at=now(); ident='SRC-'+uuid.uuid4().hex[:10].upper()
 with conn() as c:
  c.execute('INSERT INTO source_check_runs(id,checked_at,results_json) VALUES(?,?,?)',(ident,checked_at,json.dumps(results,ensure_ascii=False)))
  log(c,user['name'],'ran manual public source check','source_check',ident,f'{sum(r["status"]=="checked" for r in results)} checked; {sum(r["status"]=="failed" for r in results)} failed')
 return {'id':ident,'checked_at':checked_at,'sources':SOURCE_DIRECTORY,'results':results,'message':'One-time page retrieval only. No background or scheduled monitoring runs.'}

@app.get('/api/findings')
def findings(status: str='',q: str='',authorization: str|None=Header(default=None)):
 user_role(authorization)
 with conn() as c:
  rows=[dict(r) for r in c.execute('SELECT * FROM findings ORDER BY CASE severity WHEN "Critical" THEN 0 WHEN "High" THEN 1 WHEN "Medium" THEN 2 ELSE 3 END,created_at DESC')]
 return {'items':[x for x in rows if (not status or x['status']==status) and q.lower() in (x['title']+x['category']+x['description']).lower()]}
@app.post('/api/findings/{finding_id}/review')
def review_finding(finding_id: str,data: Review,authorization: str|None=Header(default=None)):
 user=user_role(authorization)
 with conn() as c:
  if not c.execute('SELECT 1 FROM findings WHERE id=?',(finding_id,)).fetchone(): raise HTTPException(404,'Finding not found.')
  c.execute('UPDATE findings SET status=?,reviewer=?,review_note=? WHERE id=?',(data.status,data.reviewer,data.note,finding_id));log(c,user['name'],'updated review status','finding',finding_id,data.status)
 return {'ok':True,'id':finding_id,'status':data.status}
@app.get('/api/alerts')
def alerts(authorization: str|None=Header(default=None)):
 user_role(authorization)
 with conn() as c:return {'items':[dict(r) for r in c.execute('SELECT * FROM alerts ORDER BY created_at DESC')]}
@app.post('/api/alerts/{alert_id}/review')
def review_alert(alert_id: str,authorization: str|None=Header(default=None)):
 user=user_role(authorization)
 with conn() as c:
  cur=c.execute('UPDATE alerts SET reviewed=1,reviewer=? WHERE id=?',(user['name'],alert_id))
  if not cur.rowcount: raise HTTPException(404,'Alert not found.')
  log(c,user['name'],'marked alert reviewed','alert',alert_id)
 return {'ok':True}
@app.get('/api/employees')
def employees(authorization: str|None=Header(default=None)):
 user=user_role(authorization)
 with conn() as c:return {'items':[dict(r) for r in c.execute('SELECT * FROM employees ORDER BY name')]}
@app.post('/api/employees/{employee_id}/ack')
def set_ack(employee_id: str,data: Ack,authorization: str|None=Header(default=None)):
 user=user_role(authorization);require_admin(user)
 with conn() as c:
  cur=c.execute('UPDATE employees SET policy_ack=?,updated_at=? WHERE id=?',(int(data.acknowledged),now(),employee_id))
  if not cur.rowcount: raise HTTPException(404,'Employee not found.')
  log(c,user['name'],'updated policy acknowledgement','employee',employee_id,str(data.acknowledged))
 return {'ok':True}
@app.post('/api/employees/{employee_id}/training')
def set_training(employee_id: str,data: Training,authorization: str|None=Header(default=None)):
 user=user_role(authorization);require_admin(user)
 with conn() as c:
  cur=c.execute('UPDATE employees SET training=?,updated_at=? WHERE id=?',(data.status,now(),employee_id))
  if not cur.rowcount: raise HTTPException(404,'Employee not found.')
  log(c,user['name'],'updated training status','employee',employee_id,data.status)
 return {'ok':True}
@app.get('/api/audits')
def audits(authorization: str|None=Header(default=None)):
 user_role(authorization)
 with conn() as c:return {'items':[dict(r) for r in c.execute('SELECT * FROM audits ORDER BY created_at DESC')]}
@app.post('/api/audits')
def create_audit(data: AuditIn,authorization: str|None=Header(default=None)):
 user=user_role(authorization);require_admin(user); ident='AUD-'+uuid.uuid4().hex[:6].upper()
 with conn() as c:c.execute('INSERT INTO audits VALUES(?,?,?,?,?)',(ident,data.title,data.reviewer,'Planned',now()));log(c,user['name'],'created audit','audit',ident,data.title)
 return {'id':ident,'status':'Planned'}
@app.get('/api/policies')
def policies(q: str='',authorization: str|None=Header(default=None)):
 user_role(authorization);term=q.lower()
 return {'items':[p for p in POLICIES if term in json.dumps(p).lower()], 'notice':'Illustrative local sample SOPs; not hospital-approved policies.'}

async def extract(filename: str, data: bytes) -> tuple[str, bool]:
 import base64
 ext=Path(filename).suffix.lower()
 if ext=='.txt': return data.decode('utf-8-sig',errors='replace'), False
 if ext=='.pdf':
  with fitz.open(stream=data,filetype='pdf') as doc:
   text='\n'.join(p.get_text() for p in doc)
   if text.strip(): return text, False
   imgs=[('image/png',base64.b64encode(p.get_pixmap(dpi=150).tobytes('png')).decode('ascii')) for p in doc[:5]]
  ocr_text=await ocr_images_with_api(imgs)
  return (ocr_text or ''), True
 if ext=='.docx':
  import io
  doc=Document(io.BytesIO(data));return '\n'.join(p.text for p in doc.paragraphs), False
 if ext in {'.png','.jpg','.jpeg','.webp'}:
  mime='image/jpeg' if ext in {'.jpg','.jpeg'} else f'image/{ext[1:]}'
  ocr_text=await ocr_images_with_api([(mime,base64.b64encode(data).decode('ascii'))])
  return (ocr_text or ''), True
 raise HTTPException(415,'Supported upload formats: PDF, DOCX, TXT, PNG, JPG, WEBP.')
def chunks(text: str, size=900, overlap=120):
 clean=re.sub(r'\s+',' ',text).strip(); out=[]; i=0
 while i<len(clean):
  end=min(len(clean),i+size)
  if end<len(clean):
   boundary=clean.rfind(' ',i,end)
   if boundary>i+size//2:end=boundary
  out.append(clean[i:end]);i=max(end-overlap,i+1)
 return out
@app.post('/api/documents')
async def upload_document(file: UploadFile=File(...),category: str=Form('NABH'),authorization: str|None=Header(default=None)):
 user=user_role(authorization)
 allowed={'NABH','CDSCO','IRDAI','RBI','NMC','PCPNDT','DPDP','CERT-In'}
 if category not in allowed: raise HTTPException(422,'Choose one listed topic category.')
 if not file.filename or Path(file.filename).name!=file.filename: raise HTTPException(400,'Use a plain filename without a path.')
 raw=await file.read(MAX_UPLOAD+1)
 if len(raw)>MAX_UPLOAD: raise HTTPException(413,f'File exceeds configured {MAX_UPLOAD//(1024*1024)} MB limit.')
 try:
  text,used_ocr=await extract(file.filename,raw)
 except HTTPException:
  raise
 except Exception:
  text,used_ocr='',False
 if not text.strip(): raise HTTPException(422,'No readable text was extracted. Check that an API key is set for scanned PDFs/images.')
 ident='DOC-'+uuid.uuid4().hex[:8].upper(); digest=hashlib.sha256(raw).hexdigest(); safe=ident+Path(file.filename).suffix.lower();UPLOAD_DIR.mkdir(parents=True,exist_ok=True);(UPLOAD_DIR/safe).write_bytes(raw)
 status_label='Extracted (OCR)' if used_ocr else 'Extracted'
 with conn() as c:
  c.execute('INSERT INTO documents(id,filename,sha256,bytes,mime,uploaded_at,status,extracted_chars,content,demo) VALUES(?,?,?,?,?,?,?,?,?,1)',(ident,file.filename,digest,len(raw),file.content_type or 'application/octet-stream',now(),status_label,len(text),text))
  log(c,user['name'],'uploaded and extracted document','document',ident,f'{len(text)} characters; ocr={used_ocr}; hash {digest[:12]}…')
 return {'id':ident,'filename':file.filename,'sha256':digest,'bytes':len(raw),'characters':len(text),'chunks':len(chunks(text)),'status':status_label,'ocr':used_ocr,'message':'Text extracted via Gemini OCR.' if used_ocr else 'Text extracted locally.'}
@app.get('/api/documents')
def documents(authorization: str|None=Header(default=None)):
 user_role(authorization)
 with conn() as c:return {'items':[dict(r) for r in c.execute('SELECT id,filename,sha256,bytes,mime,uploaded_at,status,extracted_chars,demo FROM documents ORDER BY uploaded_at DESC')]}
@app.post('/api/documents/{document_id}/analyze')
async def analyze(document_id: str,category: str='NABH',authorization: str|None=Header(default=None)):
 user=user_role(authorization)
 with conn() as c:
  doc=c.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone()
  if not doc: raise HTTPException(404,'Document not found.')
  pieces=chunks(doc['content'])
  ai_obs=None
  try:
   ai_obs=await analyze_document_with_api(doc['content'],category)
  except Exception:
   ai_obs=None
  fid='F-'+uuid.uuid4().hex[:6].upper()
  if ai_obs and ai_obs.get('description'):
   title=ai_obs['title']
   desc=ai_obs['description']
   sev=ai_obs['severity']
   action=ai_obs['action']
   method=f"external AI advisory review ({ai_obs['model']}); human verification required"
   src=f'Uploaded document {doc["filename"]} · AI advisory review'
   present=ai_obs.get('present_evidence',[])
   missing=ai_obs.get('missing_evidence',[])
   verify=ai_obs.get('needs_verification',[])
  else:
   text=doc['content'].lower(); terms=['incident escalation','incident contact','review owner','retention schedule','policy acknowledgement']
   hits=[]
   for term in terms:
    score=sum(1 for chunk in pieces if term in chunk.lower())
    if score: hits.append((term,score))
   phrase=', '.join(t for t,n in hits) if hits else 'none of the checked phrases'
   title='Text observation · '+category
   desc=(f'Illustrative lexical match: {phrase} found in the uploaded text. This does not validate meaning, completeness, or a requirement.' if hits else f'The checked phrase list ({", ".join(terms)}) did not match this extracted text. Absence is not evidence of a regulatory gap.')
   sev='Low'
   action='A qualified reviewer should compare the complete controlled document to current authoritative requirements, record evidence and rationale, then assign any follow-up.'
   method='simple phrase presence; not embeddings, RAG, AI, or legal analysis'
   src=f'Uploaded document {doc["filename"]} · extracted text only'
   present=[f'Matched term: {t} ({n} chunks)' for t,n in hits]
   missing=[]
   verify=[]
  c.execute('INSERT INTO findings(id,document_id,category,title,description,source,severity,action,status,demo,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(fid,document_id,category,title,desc,src,sev,action,'Pending review',1,now()))
  log(c,user['name'],'completed document review','document',document_id,f'finding {fid}; {len(pieces)} chunks; {method}')
 return {'finding_id':fid,'filename':doc['filename'],'category':category,'title':title,'description':desc,'severity':sev,'action':action,'present_evidence':present,'missing_evidence':missing,'needs_verification':verify,'chunks_examined':len(pieces),'method':method,'status':'Pending review'}
def _local_ad_scan(text: str) -> dict[str, Any]:
 patterns=[
  (r'\b(?:no\.?\s*1|number\s+one|best|top[- ]rated|leading)\b','Ranking or superiority language','Comparative or top-ranking wording may need clear, current evidence and context before publication.','Remove the ranking unless it is supported by reliable, current evidence and an appropriate comparison.'),
  (r'(?:\bguarantee\w*\b|\b100\s*%|\b100\s+percent\b)','Absolute promise','Absolute wording can imply a certain outcome for every person; the local check cannot assess what the service can support.','Describe the service factually. Avoid promising an individual outcome; ask a qualified clinician to explain suitability and expected results.'),
  (r'\b(?:cure[sd]?|permanent(?:ly)?|instant(?:ly)?)\b','Certain-outcome wording','Medical outcome language needs careful context and substantiation; the check cannot evaluate clinical evidence or a rule.','Use a factual description and have a qualified clinician review claims, risks, and the range of possible outcomes.'),
 ]
 found=[]
 for pat,title,why,edit in patterns:
  for m in re.finditer(pat,text,re.IGNORECASE):
   found.append((m.start(),{'phrase':m.group(0),'title':title,'why':why,'edit':edit,'count':1}))
 found.sort(key=lambda x:x[0])
 cues=[item for _,item in found]
 neutral='Treatment options vary by individual. Consult a qualified clinician to discuss suitability, expected outcomes, and risks.' if cues else ''
 return {'mode':'local_fallback','provider':'local','model':'phrase-scan','summary':'','neutral':neutral,'cues':cues}

@app.post('/api/ad-review')
async def ad_review(data: AdReviewIn,authorization: str|None=Header(default=None)):
 user_role(authorization)
 text=data.text.strip()
 if not text: raise HTTPException(422,'Ad copy cannot be empty.')
 try:
  result=await review_ad_with_api(text)
  if result is not None: return result
 except Exception as exc:
  fallback=_local_ad_scan(text)
  fallback['api_error']=str(exc)
  return fallback
 return _local_ad_scan(text)

@app.post('/api/ask')
async def ask(data: Ask,authorization: str|None=Header(default=None)):
 user_role(authorization)
 q=data.question.lower()
 with conn() as c: docs=[dict(r) for r in c.execute('SELECT id,filename,content FROM documents')]
 candidates=[]
 for d in docs:
  for idx,chunk in enumerate(chunks(d['content'])):
   tokens=set(re.findall(r'[a-z0-9]{3,}',q)); words=set(re.findall(r'[a-z0-9]{3,}',chunk.lower())); score=len(tokens&words)/max(len(tokens),1)
   if score>0:candidates.append({'document_id':d['id'],'document':d['filename'],'chunk':idx+1,'score':round(score,2),'excerpt':chunk[:300]})
 candidates=sorted(candidates,key=lambda x:x['score'],reverse=True)[:3]
 if candidates:
  try:
   api_answer=await ask_with_api(data.question,candidates)
   if api_answer:
    return {'answer':api_answer,'matches':candidates,'confidence':None,'retrieval':'lexical overlap over user-uploaded text + external API summary','limitations':['Advisory output only; grounded strictly in uploaded document excerpts.','No external regulator corpus is indexed; human compliance review is required.']}
  except Exception:
   pass
 return {'answer':'This local preview does not have a verified regulatory source corpus or a connected language model. It cannot substantiate a regulatory answer. Use current official material and qualified human review.','matches':candidates,'confidence':None,'retrieval':'lexical overlap only across user-uploaded extracted text; not semantic search','limitations':['No regulator source documents or citations are indexed.','Keyword overlap is not evidence of authority or applicability.','No LLM was called; no generated legal conclusion was produced.']}
@app.get('/api/report.pdf')
def report(authorization: str|None=Header(default=None), company_name: str=Query(default='', max_length=120)):
 user=user_role(authorization)
 with conn() as c:
  rows=[dict(r) for r in c.execute('SELECT * FROM findings ORDER BY created_at DESC')]
  docs=[dict(r) for r in c.execute('SELECT id,filename,uploaded_at,extracted_chars FROM documents ORDER BY uploaded_at DESC')]
 buf=BytesIO();styles=getSampleStyleSheet();org_label=escape(company_name.strip()) if company_name.strip() else 'Not specified';story=[Paragraph('NextComply AI · Advisory review report',styles['Title']),Paragraph('ILLUSTRATIVE SAMPLE ONLY · NOT A COMPLIANCE DETERMINATION',styles['Heading2']),Paragraph(f'<b>Organisation:</b> {org_label}',styles['BodyText']),Paragraph(f'Generated {now()} · Reviewer: {user["name"]}',styles['BodyText']),Spacer(1,12),Paragraph('Scope and limitations',styles['Heading2']),Paragraph('This report summarizes local illustrative records and simple text observations. This report does not include the separate public-page check results and does not verify current regulatory requirements. It is not legal advice, an audit opinion, or a statement of compliance. A qualified reviewer must verify applicability, source versions, evidence, and conclusions.',styles['BodyText']),Spacer(1,10),Paragraph('Documents',styles['Heading2'])]
 table_data=[['ID','Filename','Uploaded','Characters']]+[[d['id'],d['filename'],d['uploaded_at'],str(d['extracted_chars'])] for d in docs]
 t=Table(table_data,repeatRows=1,colWidths=[58,190,150,65]);t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8f2f1')),('GRID',(0,0),(-1,-1),.4,colors.HexColor('#b9c9c8')),('FONTSIZE',(0,0),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'TOP')]));story += [t,Spacer(1,12),Paragraph('Findings for human review',styles['Heading2'])]
 for f in rows:
  story += [Paragraph(f'{f["id"]} · {f["category"]} · {f["severity"]} · {f["status"]}',styles['Heading3']),Paragraph(f'<b>{f["title"]}</b>',styles['BodyText']),Paragraph(f["description"],styles['BodyText']),Paragraph(f'<b>Source:</b> {f["source"]}',styles['BodyText']),Paragraph(f'<b>Suggested reviewer action:</b> {f["action"]}',styles['BodyText']),Spacer(1,7)]
 SimpleDocTemplate(buf,pagesize=A4,rightMargin=40,leftMargin=40,topMargin=40,bottomMargin=40,title='NextComply AI advisory review report').build(story)
 return Response(buf.getvalue(),media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="nextcomply-advisory-report.pdf"'})
