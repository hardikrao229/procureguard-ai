# ProcureGuard — Domain Expert AI Agents for Procure-to-Pay

Three expert AI agents that run an Indian company's Procure-to-Pay (P2P) process, built on the
Model Context Protocol (MCP), Agent Skills and a human-in-the-loop approval gate. Version 2.1 adds
agentic RAG over company documents and n8n workflows for the follow-up work.

| Agent (skill) | Business role | Can change data? |
| :--- | :--- | :--- |
| `invoice-verification-agent` | AP Analyst: three-way match, duplicate detection | Can only put invoices ON HOLD |
| `vendor-risk-agent` | Vendor Risk Manager: bank-change fraud, GST, delivery, concentration | No (recommends only) |
| `payment-approval-agent` | Treasury: pay / schedule / block, MSME deadlines, discounts, cash buffer | Can release payments, through the approval gate |

## Architecture

```
You (browser) -> streamlit_app.py [MCP Host] -> agent loop [MCP Client]
                    |-- Gemini (reasons, picks tools)
                    |-- SKILL.md + p2p_heuristics.md (the expert's SOP and policy)
                    |-- policy_gate.py (least privilege, limits, human approval, audit log)
                    '-- MCP over JSON-RPC 2.0 / stdio --> p2p_server.py [MCP Server]
                                                            tools / resources / prompts
                                                            over data/live/*.json (demo ERP)
                                                            + search_p2p_knowledge -> kb_index.py (RAG)
   policy gate / approvals --> automation.py --webhook--> n8n (alerts, digest, call-back form, Postgres)
```

## Knowledge base and RAG (new in 2.1)

`knowledge_base/` holds 13 documents: AP and treasury SOPs, MSME and GST guidance notes, one contract or
rate card per vendor, and two emails sent by vendors (untrusted).

* `kb_index.py` splits them at headings (63 chunks), masks prompt-injection text in vendor emails
  (`injection_screen.py`), and builds a hybrid index: TF-IDF + SVD vectors in ChromaDB, BM25 keywords,
  merged with Reciprocal Rank Fusion. Metadata filters: document type and vendor.
* The agents call the MCP tool `search_p2p_knowledge` to cite the clause behind a decision
  (contract price, call-back phone on file, MSME interest rule).
* `rag_agent.py` is the Policy Assistant: route, retrieve, Gemini grades each passage, rewrite and
  retrieve again if needed, answer with [S1] citations, then code checks the citations and flags
  phone numbers that are not on file. It also runs without a key (backup mode).

## n8n automation (new in 2.1)

`automation.py` turns holds, approvals, fraud flags, blocked calls and payments into events. They are
always kept in `logs/n8n_outbox.jsonl`; with `N8N_WEBHOOK_URL` set they are posted to n8n.

| Workflow (`n8n/`) | Trigger | Does |
| :--- | :--- | :--- |
| `1_ap_control_alerts.json` | Webhook | Routes each event to the right team, emails them, logs it in Postgres |
| `2_daily_msme_digest.json` | Every day 09:00 IST | MSME deadlines within 7 days, open discounts, overdue and non-PO invoices, emailed and saved |
| `3_bank_change_callback_form.json` | n8n form | Vendor Master records the call-back; n8n checks the number against the phone on file and escalates failures |

Postgres tables: `n8n/postgres_schema.sql`.

## Run it

```
python -m venv .venv
.venv\Scripts\activate            (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            then paste your Gemini key into .env
streamlit run streamlit_app.py
```

Live deployment: Streamlit Community Cloud, main file `streamlit_app.py`, secret `GEMINI_API_KEY`
(optional secret `N8N_WEBHOOK_URL`).

## The app

| Tab | What the business user does |
| :--- | :--- |
| Agent Desk | Chats with the chosen expert agent; sees every tool call it made |
| Policy Assistant | Asks questions about SOPs, laws, contracts and vendor emails (agentic RAG with citations) |
| Approvals | Finance Controller approves or rejects payments above the limit |
| Automation (n8n) | Sees every event sent to n8n and the workflow files |
| AP Dashboard | Live invoice queue with match, duplicate and vendor-risk checks |
| Audit Trail | Every gate decision and every payment, downloadable |
| Policy & Skills | Changes policy numbers without code; reads each agent's SKILL.md |
| Red Team | Runs the five attacks against the v1 and v2 payment skills |
| How it works | Architecture of host, client and server |

## Files

| File | What it does | Lab |
| :--- | :--- | :--- |
| `p2p_server.py` | MCP server: 14 tools, 2 resources, 3 prompts | 7 |
| `smoke_test.py` | Tests the server with no AI | 7 |
| `protocol_tracer.py` | Prints raw JSON-RPC messages | 8 |
| `policy_gate.py` | Client-side security gate with human approval | 9 |
| `skills/*/SKILL.md` | The three expert agents (YAML + phases + gates + output schema) | 10 |
| `policy/p2p_heuristics.md` | Policy numbers, shared by agents, server and gate | 11 |
| `red_team.py`, `skills/archive/` | Adversarial tests, v1 vs v2 skill | 12 |
| `streamlit_app.py` | The web app (MCP host in the browser) | all |
| `agent.py` | Same agents in the terminal; also the shared agent loop | all |
| `reset_data.py` | Restores demo data | - |
| `knowledge_base/`, `kb_index.py`, `rag_agent.py` | Documents, hybrid retrieval, Policy Assistant | RAG |
| `injection_screen.py` | Regex screen for instructions hidden in supplier text | 12 |
| `automation.py`, `n8n/` | Events to n8n and the three workflows | n8n |

## Demo test cases (business date 10 Oct 2026)

| Invoice | Situation | Expected outcome |
| :--- | :--- | :--- |
| INV-1001 | Clean match, Shree Packaging | Payable |
| INV-1002 | Price +8.1%, 50 rejected sheets billed, vendor changed bank 3 days ago | ON HOLD, vendor HIGH risk |
| INV-1003 | MSME vendor, Rs 8.26 lakh, due in 6 days | Pay - needs human approval |
| INV-1004 | Re-sent copy of INV-1001 ("-A" suffix) | Suspected duplicate |
| INV-1005 | No PO, vendor GSTIN cancelled | Escalate |
| INV-1006 | Fake "CFO override" hidden in notes, price +2.5% | Injection flagged, ON HOLD |
| INV-1007 + INV-1008 | Same vendor, each under Rs 5 lakh, together Rs 6.96 lakh | Second one goes to human (anti-splitting) |
