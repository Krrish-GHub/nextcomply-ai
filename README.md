# NextComply AI: local advisory demo

A runnable source project focused on reviewing healthcare advertising copy for clinics and healthcare brands. The primary page demonstrates a small browser-side English phrase scan with exact matches, reviewer context, and a neutral drafting aid. Supporting areas include a FastAPI service, local SQLite records, PDF/DOCX/TXT text extraction, internal sample workflows, audit logging, a PDF review report, and an explicit user-triggered fetch of selected public notice pages.

> This is a learning/demo system, not a hospital-ready product, legal advice, an audit opinion, or a compliance determination. Use fictional data only. Do not upload patient-identifiable or confidential information. Every observation requires qualified human verification.

## Run on Windows (PowerShell)

The terminal must be opened in the extracted `nextcomply-ai` project root, the folder that directly contains `app\main.py`, `requirements.txt`, and `pyproject.toml`. In VS Code, use **File → Open Folder** to open that `nextcomply-ai` folder, then open a new PowerShell terminal.

```powershell
# Replace this example with the actual folder where you extracted the ZIP.
Set-Location "C:\path\to\NextComply-AI-Local-Demo\nextcomply-ai"
Test-Path .\app\main.py   # Must print True before starting Uvicorn.
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. API docs are at http://127.0.0.1:8000/docs. Stop the server with **Ctrl+C** in that terminal.

If Uvicorn reports `ModuleNotFoundError: No module named 'app'`, it was launched from the wrong working directory. Run `Get-Location` and `Test-Path .\app\main.py`; if the latter prints `False`, `Set-Location` to the extracted `nextcomply-ai` folder above and start the server again. This diagnoses the path; it does not claim any prior error has been fixed.

Demo-only credentials shown in the app:

- Compliance Officer: `officer@demo.local` / `NextComply-Demo-2026`
- Admin: `admin@demo.local` / `NextComply-Demo-2026`

In local `APP_ENV=development`, the UI works without sign-in for convenience; the demo login endpoint validates the two clearly labelled demo accounts. This authentication is not suitable for production. Admin-only edits require the Admin demo role after signing in.

## Stack: current prototype and recommended target

This ZIP is an HTML/CSS/JavaScript frontend served by FastAPI, with SQLite storage. It does not implement React, TypeScript, Vite, Tailwind, Framer Motion, PostgreSQL, pgvector, or LlamaIndex. Recommended next stack: React + TypeScript + Vite, Tailwind + Framer Motion, FastAPI, PostgreSQL + pgvector, and PyMuPDF. Add LlamaIndex only after verified, versioned retrieval sources and evaluation data are ready.

## Walkthrough

The detailed product walkthrough is included as `docs/NextComply-AI-Walkthrough.pdf` and `docs/NextComply-AI-Walkthrough.md`. It explains the healthcare-ad review flow, site sections, phrase-scan limits, the manual public-source fetch workflow, supporting pages, Windows setup, current prototype stack and recommended target stack.

## Run tests

```powershell
pytest -q
```

## What works

- Primary ad-copy review workspace with a local phrase-pattern scan, quoted matches, plain-language reviewer cues, and a separate generic neutral wording example. This is not a rule engine, medical analysis, legal assessment, or compliance score.
- Supporting document and review queues plus internal operations using clearly fictional records.
- Document extraction for text-based PDF, DOCX and TXT up to configured upload limit.
- A deterministic phrase-presence observation for document text that explicitly avoids asserting compliance or a gap. The main ad-copy scan runs in the browser and does not persist the text or result to the finding register or PDF report.
- A question endpoint that states the absence of verified sources and optionally shows lexical matches in uploaded text.
- Review status updates, sample alerts, employee acknowledgement/training updates (Admin role), audit creation, and a local audit log.
- Searchable topic directory and internal sample SOP snippets.
- A manual “Check sources now” page that fetches fixed public pages for CDSCO, IRDAI, RBI, NMC, MoHFW/PCPNDT, MeitY/DPDP, CERT-In and NABH (an accreditation body, not a government regulator). Each page reports whether the fetch succeeded or failed, and whether reliable dated rows were extracted from observed HTML. Each run is stored in local SQLite, while the page displays only the latest run. This is not continuous monitoring, and failures or pages with no reliable rows never mean “no updates.”
- Downloadable PDF report labeling its illustrative scope and review status. The optional Company name field starts blank, is saved only in this browser's local storage, and is passed to the report download; it is not written into the database. An old placeholder value is cleared rather than presented as the organisation.
- Original conceptual artwork on the overview page, captioned as illustration rather than source evidence or an official seal.

## Deliberately not implemented

No verified regulatory corpus or citations, NMC rule engine, exact rule mapping from ad copy, multilingual ad analysis, complete image/video/website review, legally compliant rewrite, exhaustive website crawling, scheduled/background monitoring or notifications, embeddings, pgvector, LlamaIndex, LLM, LangGraph agents, OCR, video processing, PDF export from a source-grounded analysis, hospital integrations, SSO, production authorization, security certification, clinical workflow, or legal determination. Public page requests are best-effort; the basic parser can miss dynamic or PDF content and must be checked against the original source. The architecture screen describes the supplied guide's intended stack separately from features that run here. The guide's claimed nine regulators vs. its eight listed entries should be reconciled before treating its inventory as authoritative.

## Data and privacy

SQLite database and uploaded source files are stored under `./data/`. They are local demo data, not encrypted or designed for clinical information. The app records audit actions but does not provide tamper-evident audit storage. Remove `data/` to reset the demo. Configure `DATABASE_PATH`, `UPLOAD_DIR`, and `MAX_UPLOAD_MB` through environment variables.

## Production engineering path

Before any real hospital pilot, implement and independently review: identity/SSO and tenant-scoped authorization; encrypted storage and transport; data retention/deletion controls; safe malware-scanned file staging; verified and versioned source ingestion with provenance and update workflow; robust retrieval/evaluation/abstention; signed reviewer decisions and tamper-evident audit trails; backup/restore, monitoring and incident response; legal/privacy/security review and explicit organizational approval. PostgreSQL/pgvector, provider integrations, OCR and LangGraph are not wired into this demo.
