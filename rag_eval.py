"""
rag_eval.py - measures how well the knowledge base finds the right passage (no AI, no key).

Twelve questions an AP team really asks, each with the chunk that answers it.
Three retrievers are compared on the same questions:
   dense only  (ChromaDB vectors)   |   BM25 only (keywords)   |   hybrid (both, fused with RRF)
Metrics: Hit@1, Hit@3 (is the right chunk in the top 1 / top 3?) and MRR (1 / rank of the right chunk).
The same questions were used to choose the vector size and the fusion weight in kb_index.py,
so treat the numbers as a development check, not an independent test.

Run:  python rag_eval.py
"""
from collections import Counter

import kb_index

GOLD = [
    ("Who do we call to verify an Apex Steel bank change?", "V002", "CON-V002#5"),
    ("What price can Apex Steel charge for 2 mm sheets and when can it change?", "V002", "CON-V002#2"),
    ("Can Greenleaf increase the price of A4 paper this year?", "V005", "CON-V005#2"),
    ("What is the contracted rate for a Gurugram to Pune truck?", "V004", "CON-V004#2"),
    ("Is a verbal approval enough for an ad-hoc freight trip?", "V004", "CON-V004#3"),
    ("What interest do we owe an MSME if we pay late?", "", "REG-MSME-01#3"),
    ("When is an amount owed to a small enterprise allowed as a tax expense?", "", "REG-MSME-01#4"),
    ("Can we claim input tax credit if the supplier's GSTIN is cancelled?", "", "REG-GST-02#2"),
    ("What must happen during the bank-change cooling period?", "", "SOP-VM-02#4"),
    ("Who approves a payment above Rs 5 lakh?", "", "POL-TR-03#2"),
    ("Should we take a 2/10 Net 30 early-payment discount?", "", "POL-TR-03#3"),
    ("Which of two matching invoices is the duplicate?", "", "POL-AP-04#2"),
]


def ranks(question: str, vendor: str):
    ix = kb_index.index()
    hybrid = [h["chunk_id"] for h in kb_index.search(question, vendor_id=vendor, k=10)]
    allowed = [c for c in ix["chunks"] if not vendor or c["vendor_id"] in (vendor, "")]
    q = ix["normalize"](ix["svd"].transform(ix["tfidf"].transform([question])))[0]
    res = ix["collection"].query(query_embeddings=[q.tolist()], n_results=10,
                                 where=kb_index._where([], vendor))
    dense = res["ids"][0]
    scores = ix["bm25"].scores(kb_index.tokens(question))
    pos = {c["chunk_id"]: i for i, c in enumerate(ix["chunks"])}
    bm25 = [cid for s, cid in sorted(((scores[pos[c["chunk_id"]]], c["chunk_id"]) for c in allowed), reverse=True)[:10]]
    return {"dense only": dense, "BM25 only": bm25, "hybrid (RRF)": hybrid}


def main():
    totals = {name: Counter() for name in ("dense only", "BM25 only", "hybrid (RRF)")}
    print("=" * 96)
    print(" PROCUREGUARD - RETRIEVAL EVALUATION (12 questions, right answer = one specific chunk)")
    print("=" * 96)
    print(f"{'Question':58} {'dense':>8} {'BM25':>8} {'hybrid':>8}")
    for question, vendor, gold in GOLD:
        r = ranks(question, vendor)
        cells = []
        for name, ids in r.items():
            rank = ids.index(gold) + 1 if gold in ids else None
            cells.append(f"#{rank}" if rank else "miss")
            totals[name]["hit1"] += rank == 1
            totals[name]["hit3"] += bool(rank and rank <= 3)
            totals[name]["mrr"] += 1 / rank if rank else 0
        print(f"{question[:57]:58} {cells[0]:>8} {cells[1]:>8} {cells[2]:>8}")
    n = len(GOLD)
    print("-" * 96)
    print(f"{'Retriever':20} {'Hit@1':>8} {'Hit@3':>8} {'MRR':>8}")
    for name, t in totals.items():
        print(f"{name:20} {t['hit1'] / n:8.0%} {t['hit3'] / n:8.0%} {t['mrr'] / n:8.2f}")
    print(f"\nIndex: {kb_index.stats()['chunks']} chunks from {kb_index.stats()['documents']} documents.")


if __name__ == "__main__":
    main()
