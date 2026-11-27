from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

SYSTEM_PROMPT = (
    "You are NextComply AI, an advisory healthcare compliance assistant for India "
    "(covering awareness of NABH, CDSCO, NMC, IRDAI, PCPNDT, DPDP, and CERT-In). "
    "GOLDEN RULE: NEVER say 'you are non-compliant' or 'you are violating'. "
    "Always use advisory language such as 'flagged for review', 'potential gap identified', "
    "or 'suggested for human review'. A human compliance officer always makes the final call. "
    "Do NOT invent or cite unverified rule numbers. "
    "Return valid JSON only with keys: "
    '"summary" (1 short advisory sentence), '
    '"neutral" (1 neutral, non-promissory replacement sentence), and '
    '"cues" (array of objects with keys "phrase", "title", "why", "edit"). '
    'Each "phrase" must be an exact substring from the input ad text.'
)


def api_config() -> dict[str, str]:
    _load_dotenv()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()

    if provider == "openai" or (openai_key and not gemini_key):
        if openai_key:
            return {
                "provider": "openai",
                "api_key": openai_key,
                "base_url": os.getenv("LLM_API_BASE", "https://api.openai.com/v1").rstrip("/"),
                "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
            }
    if gemini_key:
        return {
            "provider": "gemini",
            "api_key": gemini_key,
            "base_url": os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/"),
            "model": os.getenv("LLM_MODEL", "gemini-2.5-flash"),
        }
    return {}


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    return json.loads(text)


async def review_ad_with_api(ad_text: str) -> dict[str, Any] | None:
    cfg = api_config()
    if not cfg:
        return None

    async with httpx.AsyncClient(timeout=30.0) as client:
        if cfg["provider"] == "gemini":
            url = f"{cfg['base_url']}/models/{cfg['model']}:generateContent"
            payload = {
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": f"Ad copy:\n{ad_text}"}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            }
            resp = await client.post(url, params={"key": cfg["api_key"]}, json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            url = f"{cfg['base_url']}/chat/completions"
            payload = {
                "model": cfg["model"],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Ad copy:\n{ad_text}"},
                ],
            }
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["choices"][0]["message"]["content"]

    parsed = _extract_json(raw_text)
    cues = [
        {
            "phrase": str(c.get("phrase", "")).strip(),
            "title": str(c.get("title", "Language cue")).strip(),
            "why": str(c.get("why", "")).strip(),
            "edit": str(c.get("edit", "")).strip(),
            "count": 1,
        }
        for c in (parsed.get("cues") or [])
        if str(c.get("phrase", "")).strip()
    ]
    return {
        "mode": "api",
        "provider": cfg["provider"],
        "model": cfg["model"],
        "summary": str(parsed.get("summary", "")).strip(),
        "neutral": str(parsed.get("neutral", "")).strip(),
        "cues": cues,
    }


ASK_SYSTEM_PROMPT = (
    "You are NextComply AI, an advisory healthcare compliance assistant for India. "
    "Answer ONLY using the provided document excerpts. Do NOT use outside general knowledge "
    "or invent legal citations. Never say 'you are non-compliant' or 'violating'—use advisory "
    "language ('flagged for review', 'suggested for human review'). If the excerpts do not "
    "answer the question, state clearly that the uploaded text does not cover it."
)


async def ask_with_api(question: str, matches: list[dict[str, Any]]) -> str | None:
    cfg = api_config()
    if not cfg or not matches:
        return None

    context = "\n\n".join(
        f"[{m.get('document', 'doc')} · chunk {m.get('chunk', '?')}]\n{m.get('excerpt', '')}"
        for m in matches
    )
    user_msg = f"Question: {question}\n\nRetrieved document excerpts:\n{context}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        if cfg["provider"] == "gemini":
            url = f"{cfg['base_url']}/models/{cfg['model']}:generateContent"
            payload = {
                "systemInstruction": {"parts": [{"text": ASK_SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
                "generationConfig": {"temperature": 0.2},
            }
            resp = await client.post(url, params={"key": cfg["api_key"]}, json=payload)
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        url = f"{cfg['base_url']}/chat/completions"
        payload = {
            "model": cfg["model"],
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": ASK_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        }
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


DOC_SYSTEM_PROMPT = (
    "You are NextComply AI, an advisory healthcare compliance assistant for India. "
    "Review the provided internal document excerpt under the requested topic category "
    "(e.g. NABH, CDSCO, NMC, IRDAI, RBI, PCPNDT, DPDP, CERT-In). "
    "GOLDEN RULE: NEVER say 'you are non-compliant' or 'violating'. Use advisory language "
    "('flagged for review', 'potential gap identified', 'suggested for human review'). "
    "Return valid JSON only with keys: "
    '"title" (short observation title), '
    '"description" (1-2 sentence advisory observation grounded in the text), '
    '"severity" (one of "High", "Medium", "Low"), and '
    '"action" (1 sentence suggested next step for a human reviewer).'
)


async def analyze_document_with_api(doc_text: str, category: str) -> dict[str, str] | None:
    cfg = api_config()
    if not cfg:
        return None

    excerpt = doc_text.strip()[:6000]
    user_msg = f"Topic category: {category}\n\nDocument text excerpt:\n{excerpt}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        if cfg["provider"] == "gemini":
            url = f"{cfg['base_url']}/models/{cfg['model']}:generateContent"
            payload = {
                "systemInstruction": {"parts": [{"text": DOC_SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            }
            resp = await client.post(url, params={"key": cfg["api_key"]}, json=payload)
            resp.raise_for_status()
            raw_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        else:
            url = f"{cfg['base_url']}/chat/completions"
            payload = {
                "model": cfg["model"],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": DOC_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            }
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json=payload,
            )
            resp.raise_for_status()
            raw_text = resp.json()["choices"][0]["message"]["content"]

    parsed = _extract_json(raw_text)
    sev = str(parsed.get("severity", "Medium")).strip()
    if sev not in {"Critical", "High", "Medium", "Low"}:
        sev = "Medium"
    return {
        "title": str(parsed.get("title", f"AI observation · {category}")).strip(),
        "description": str(parsed.get("description", "")).strip(),
        "severity": sev,
        "action": str(
            parsed.get(
                "action",
                "Have a qualified reviewer compare this document with current official requirements.",
            )
        ).strip(),
        "model": f"{cfg['provider']}:{cfg['model']}",
    }
