"""Ask TownshipOS: SOP/SLA documents passed as text context. No vector DB."""
import re
from dataclasses import dataclass
from pathlib import Path

from core.llm import API_ERRORS, MODEL, cache_key, cached, get_client, unavailable

MAX_QUESTION = 1000
DOCS_DIR = Path("data/docs")

SYSTEM = """You are TownshipOS, an assistant for a Malaysian township property-management team.
Answer only from the attached documents (SLAs, guidelines, SOPs, Strata Management Act extracts).
Cite the clause you rely on using the format [Source: document name]. If the documents do not answer
the question, say so plainly and suggest who to ask.
Be concise: a direct answer first, then the supporting clause. Reply in the language of the question."""


@dataclass
class Citation:
    doc_title: str
    cited_text: str


@dataclass
class Answer:
    text: str
    citations: list[Citation]
    refused: bool


def load_docs(dir=DOCS_DIR) -> list[dict]:
    return [{"title": p.stem.replace("_", " "), "content": p.read_text(encoding="utf-8")}
            for p in sorted(Path(dir).glob("*.txt"))]


def _call(question: str, docs: list[dict], client) -> dict:
    doc_text = "\n\n".join(f"[{d['title']}]\n{d['content']}" for d in docs)
    user_msg = f"{doc_text}\n\n---\nQuestion: {question}" if doc_text else question
    try:
        r = client.chat.completions.create(
            model=MODEL, max_tokens=4096,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": user_msg}],
        )
        text = r.choices[0].message.content or ""
    except API_ERRORS as e:
        raise unavailable(e) from e
    cites = [{"doc_title": m.group(1).strip(), "cited_text": ""}
             for m in re.finditer(r'\[Source:\s*([^\]]+)\]', text)]
    return {"text": text, "citations": cites, "refused": False}


def ask(question: str, docs: list[dict], *, client=None) -> Answer:
    question = (question or "").strip()[:MAX_QUESTION]
    if not question:
        raise ValueError("Ask a question")
    key = cache_key("ask", question, [d["content"] for d in docs])
    data = cached(key, lambda: _call(question, docs, client or get_client()))
    return Answer(text=data["text"], citations=[Citation(**c) for c in data["citations"]], refused=data["refused"])
