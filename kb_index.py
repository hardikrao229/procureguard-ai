"""
kb_index.py - the ProcureGuard knowledge base: RETRIEVAL half of RAG.

knowledge_base/
  policies/        internal SOPs (AP processing, vendor master changes, treasury, duplicates)
  regulations/     internal notes on the MSMED Act and GST input-tax-credit rules
  contracts/       one contract or rate card per vendor (prices, terms, call-back phone on file)
  correspondence/  emails and letters SENT BY VENDORS - untrusted, screened for prompt injection

How a search works (hybrid retrieval):
  1. Chunking   - every document is split at '## ' headings; each chunk keeps its metadata
                  (doc_id, section, doc_type, vendor_id, trust level).
  2. Screening  - vendor-submitted chunks pass the injection screen; instruction-like
                  sentences are masked BEFORE indexing, so the model never sees them.
  3. Dense      - TF-IDF + truncated SVD (latent semantic analysis) gives a 32-number
                  "meaning" vector per chunk, stored in a ChromaDB collection (cosine).
                  Metadata filters (doc_type, vendor_id) run inside Chroma.
  4. Sparse     - BM25 keyword ranking over the same chunks (exact words like "43B(h)").
  5. Fusion     - weighted Reciprocal Rank Fusion (RRF) merges the dense and BM25 rankings.
Everything runs in memory and offline: no embedding API, no extra key.
"""
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import yaml

from injection_screen import mask

HERE = Path(__file__).resolve().parent
KB_DIR = HERE / "knowledge_base"
DOC_TYPES = ("policy", "regulation", "contract", "correspondence")
RRF_K = 60
DENSE_WEIGHT = 0.5             # BM25 counts double: exact terms (43B(h), GSTIN, V002) matter most here
DENSE_DIMS = 32               # chosen with rag_eval.py (32 beat 64 and 16)
VENDOR_BONUS = 0.002          # about the gap between rank 1 and rank 8 - a nudge, not an override
STOPWORDS = set("""a an the and or of to in on for by with from at as is are was were be been it its this that
these those any all not no can may must will shall if then than into per our we you your their they he she
which who what when where how do does done has have had also only such""".split())


# ----------------------------------------------------------------------------- loading
def _front_matter(text: str) -> tuple:
    if not text.startswith("---"):
        return {}, text
    _, head, body = text.split("---", 2)
    return yaml.safe_load(head) or {}, body.strip()


def load_chunks() -> list:
    chunks = []
    for path in sorted(KB_DIR.rglob("*.md")):
        meta, body = _front_matter(path.read_text(encoding="utf-8"))
        title = meta.get("title") or path.stem
        sections = re.split(r"\n(?=## )", body)
        for n, part in enumerate(sections):
            lines = part.strip().splitlines()
            if not lines or not lines[0].startswith("## "):
                continue                                    # the H1 title line on its own
            section = lines[0][3:].strip()
            text = "\n".join(lines[1:]).strip()
            flags = []
            if meta.get("trust") != "internal":
                text, flags = mask(text)
            chunks.append({
                "chunk_id": f"{meta.get('doc_id', path.stem)}#{n}",
                "doc_id": meta.get("doc_id", path.stem),
                "title": title,
                "section": section,
                "doc_type": meta.get("doc_type", "policy"),
                "vendor_id": str(meta.get("vendor_id") or ""),
                "trust": meta.get("trust", "internal"),
                "path": str(path.relative_to(HERE)),
                "text": text,
                "injection_flags": flags,
            })
    return chunks


def tokens(text: str) -> list:
    words = re.findall(r"[a-z0-9]+(?:\([a-z0-9]+\))?", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


# ----------------------------------------------------------------------------- BM25
class BM25:
    def __init__(self, docs: list, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(d) for d in docs]
        self.lengths = [len(d) for d in docs]
        self.avg = sum(self.lengths) / max(len(docs), 1)
        df = Counter(w for d in docs for w in set(d))
        n = len(docs)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}

    def scores(self, query: list) -> list:
        out = []
        for doc, length in zip(self.docs, self.lengths):
            s = 0.0
            for w in query:
                if w in doc:
                    tf = doc[w]
                    s += self.idf[w] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / self.avg))
            out.append(s)
        return out


# ----------------------------------------------------------------------------- index
@lru_cache(maxsize=1)
def index():
    """Builds everything once per process (about a second)."""
    import chromadb
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import normalize

    chunks = load_chunks()
    corpus = [f"{c['section']}. {c['text']}" for c in chunks]
    tfidf = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True, min_df=1)
    matrix = tfidf.fit_transform(corpus)
    dims = max(2, min(DENSE_DIMS, len(chunks) - 1, matrix.shape[1] - 1))
    svd = TruncatedSVD(n_components=dims, random_state=7)
    dense = normalize(svd.fit_transform(matrix))

    client = chromadb.EphemeralClient()
    try:
        client.delete_collection("p2p_knowledge")
    except Exception:                                   # noqa: BLE001 - first run, nothing to delete
        pass
    col = client.create_collection("p2p_knowledge", metadata={"hnsw:space": "cosine"}, embedding_function=None)
    col.add(
        ids=[c["chunk_id"] for c in chunks],
        embeddings=[row.tolist() for row in dense],
        documents=[c["text"] for c in chunks],
        metadatas=[{k: c[k] for k in ("doc_id", "title", "section", "doc_type", "vendor_id", "trust")} for c in chunks],
    )
    bm25 = BM25([tokens(t) for t in corpus])
    return {"chunks": chunks, "by_id": {c["chunk_id"]: c for c in chunks}, "tfidf": tfidf, "svd": svd,
            "normalize": normalize, "collection": col, "bm25": bm25, "dims": dims}


def _where(doc_types, vendor_id):
    conditions = []
    if doc_types:
        conditions.append({"doc_type": {"$in": list(doc_types)}})
    if vendor_id:
        conditions.append({"vendor_id": {"$in": [vendor_id, ""]}})   # that vendor's docs + general ones
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def search(query: str, doc_types=None, vendor_id: str = "", k: int = 5) -> list:
    """Hybrid search. Returns the top-k chunks, best first, with every score shown."""
    ix = index()
    doc_types = [d for d in (doc_types or []) if d in DOC_TYPES]
    vendor_id = (vendor_id or "").strip().upper()
    allowed = [c for c in ix["chunks"]
               if (not doc_types or c["doc_type"] in doc_types)
               and (not vendor_id or c["vendor_id"] in (vendor_id, ""))]
    if not allowed:
        return []
    pool = min(len(allowed), 12)

    # dense ranking from Chroma (metadata filter applied inside the vector store)
    q = ix["normalize"](ix["svd"].transform(ix["tfidf"].transform([query])))[0]
    res = ix["collection"].query(query_embeddings=[q.tolist()], n_results=pool, where=_where(doc_types, vendor_id))
    dense_ids = res["ids"][0]
    dense_sim = {cid: round(1 - d, 3) for cid, d in zip(res["ids"][0], res["distances"][0])}

    # keyword ranking (BM25) over the same allowed chunks
    all_scores = ix["bm25"].scores(tokens(query))
    position = {c["chunk_id"]: i for i, c in enumerate(ix["chunks"])}
    bm = sorted(((all_scores[position[c["chunk_id"]]], c["chunk_id"]) for c in allowed), reverse=True)
    bm_ids = [cid for s, cid in bm[:pool] if s > 0]
    bm_score = {cid: round(s, 2) for s, cid in bm}

    # reciprocal rank fusion; a small bonus when the chunk belongs to the vendor asked about
    fused = Counter()
    for ranking, weight in ((dense_ids, DENSE_WEIGHT), (bm_ids, 1.0)):
        for rank, cid in enumerate(ranking):
            fused[cid] += weight / (RRF_K + rank + 1)
    if vendor_id:
        for cid in fused:
            if ix["by_id"][cid]["vendor_id"] == vendor_id:
                fused[cid] += VENDOR_BONUS

    hits = []
    for cid, score in fused.most_common(k):
        c = ix["by_id"][cid]
        hits.append({**c, "rrf": round(score * 1000, 2), "dense_sim": dense_sim.get(cid, 0.0),
                     "bm25": bm_score.get(cid, 0.0),
                     "dense_rank": dense_ids.index(cid) + 1 if cid in dense_ids else None,
                     "bm25_rank": bm_ids.index(cid) + 1 if cid in bm_ids else None})
    return hits


def stats() -> dict:
    ix = index()
    chunks = ix["chunks"]
    return {
        "documents": len({c["doc_id"] for c in chunks}),
        "chunks": len(chunks),
        "dense_dimensions": ix["dims"],
        "by_type": dict(Counter(c["doc_type"] for c in chunks)),
        "screened_chunks": [{"chunk": c["chunk_id"], "flags": c["injection_flags"]} for c in chunks if c["injection_flags"]],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(stats(), indent=2))
    for h in search("who do we call to verify an Apex Steel bank change", vendor_id="V002", k=4):
        print(h["chunk_id"], h["section"], "| rrf", h["rrf"], "| dense", h["dense_sim"], "| bm25", h["bm25"])
