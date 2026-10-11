"""
streamlit_app.py - ProcureGuard AI web app (the MCP HOST, in a browser).

Run locally:   streamlit run streamlit_app.py
Deploy:        push to GitHub, then create the app on share.streamlit.io (main file: streamlit_app.py)

Same architecture as agent.py, with a web front end:
  Browser (this app) -> Gemini (reasons) -> PolicyGate (approval queue) -> MCP client -> p2p_server.py
Large payments are parked in an APPROVAL QUEUE; the Finance Controller clicks Approve / Reject.
Added in v2.1: a Policy & Contract Assistant (agentic RAG over SOPs, MSME/GST notes, vendor contracts
and vendor emails), n8n automation for follow-up work, a red-team lab and a session record.
"""
import asyncio
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import base64
import zlib

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import errors
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import automation
import kb_index
import rag_agent
from agent import ProcureGuardAgent
from dashboard import render as render_dashboard
from policy import POLICY_FILE, inr, load_policy
from policy_gate import LOG_DIR, PolicyGate
from reset_data import LIVE, reset
from skill_loader import SKILLS_DIR, list_skills, load_skill

HERE = Path(__file__).resolve().parent
DEFAULT_POLICY = HERE / "policy" / "p2p_heuristics.default.md"
MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
SUGGESTIONS = {
    "invoice-verification-agent": ["Verify INV-1001", "Verify INV-1002", "Verify INV-1004", "Verify INV-1006"],
    "vendor-risk-agent": ["Assess vendor V002", "Assess vendor V004", "Assess vendor V003"],
    "payment-approval-agent": ["Should we pay INV-1007?", "Pay INV-1003", "Pay INV-1008", "Pay INV-1006"],
}
ICONS = {"invoice-verification-agent": "🧾", "vendor-risk-agent": "🏭", "payment-approval-agent": "💸"}

st.set_page_config(page_title="ProcureGuard AI", page_icon="🛡️", layout="wide")
LOG_DIR.mkdir(exist_ok=True)
if not LIVE.exists():
    reset()


# ======================================================================
# Settings: API key (Streamlit secrets on the cloud, .env on your laptop)
# ======================================================================
def setting(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:                       # no secrets file - that's fine locally
        pass
    load_dotenv(HERE / ".env")
    return os.getenv(name, default).strip()


state = st.session_state
state.setdefault("chats", {})                # one conversation per agent
state.setdefault("queue", [])                # payments waiting for a human
state.setdefault("rag_runs", [])             # Policy Assistant questions and answers
state.setdefault("red_team", [])             # red-team results run in this session
os.environ.setdefault("N8N_WEBHOOK_URL", setting("N8N_WEBHOOK_URL"))
REPLAY_DIR = os.getenv("PG_REPLAY_DIR", "")  # local only: re-display a recorded cloud session


def chat_for(skill_name: str) -> dict:
    return state.chats.setdefault(skill_name, {"messages": [], "history": []})


# ======================================================================
# Session record: export what happened in this browser session, and replay it locally
# ======================================================================
state.setdefault("started_at", datetime.now().isoformat(timespec="seconds"))


def _jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def session_record(model_used: str) -> dict:
    since = state.started_at
    return {
        "format": "procureguard-session-v1", "exported_at": datetime.now().isoformat(timespec="seconds"),
        "model": model_used,
        "chats": {name: c["messages"] for name, c in state.chats.items()},
        "queue": state.queue, "rag_runs": state.rag_runs, "red_team": state.red_team,
        "invoices": json.loads((LIVE / "invoices.json").read_text(encoding="utf-8")),
        "treasury": json.loads((LIVE / "treasury.json").read_text(encoding="utf-8")),
        "audit": [e for e in _jsonl(LOG_DIR / "audit_log.jsonl") if e["timestamp"] >= since],
        "outbox": [e for e in automation.read_outbox() if e["occurred_at"] >= since],
        "policy": POLICY_FILE.read_text(encoding="utf-8"),
    }


def encode_record(record: dict) -> str:
    return base64.b64encode(zlib.compress(json.dumps(record, default=str).encode("utf-8"), 9)).decode("ascii")


def load_record(record: dict) -> None:
    """Replay: put a recorded session back on screen (no AI calls are made)."""
    state.chats = {name: {"messages": msgs, "history": []} for name, msgs in record["chats"].items()}
    state.queue, state.rag_runs, state.red_team = record["queue"], record["rag_runs"], record["red_team"]
    (LIVE / "invoices.json").write_text(json.dumps(record["invoices"], indent=2), encoding="utf-8")
    (LIVE / "treasury.json").write_text(json.dumps(record["treasury"], indent=2), encoding="utf-8")
    (LOG_DIR / "audit_log.jsonl").write_text("".join(json.dumps(e) + "\n" for e in record["audit"]), encoding="utf-8")
    automation.OUTBOX.write_text("".join(json.dumps(e) + "\n" for e in record["outbox"]), encoding="utf-8")
    POLICY_FILE.write_text(record["policy"], encoding="utf-8")


# ======================================================================
# Talking to the MCP server
# ======================================================================
def server_params() -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=[str(HERE / "p2p_server.py")], cwd=str(HERE))


async def ask_agent(skill: dict, question: str, history: list, llm, model: str):
    """One question: start the server, discover, let the agent work, return everything."""
    with open(LOG_DIR / "server_stderr.log", "a", encoding="utf-8") as errlog:
        async with stdio_client(server_params(), errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()                                   # 01 Discover
                tools = (await session.list_tools()).tools
                policy_doc = (await session.read_resource("policy://p2p-heuristics")).contents[0].text
                gate = PolicyGate(session, load_policy(), skill["meta"],
                                  approval_mode="defer", notify=lambda msg: None)
                agent = ProcureGuardAgent(session, llm, model, skill, policy_doc, tools, gate,
                                          transcript=None, log=lambda msg: None)
                agent.history = history                                      # continue the conversation
                answer = await agent.ask(question)                           # 02-04 Reason/Authorize/Execute
                return answer, agent.trace, agent.history, gate.pending


async def decide(request: dict, approved: bool, approver: str):
    """The Finance Controller's click: log it, and if approved send the payment to the server."""
    payment_skill = load_skill(SKILLS_DIR / "payment-approval-agent" / "SKILL.md")
    with open(LOG_DIR / "server_stderr.log", "a", encoding="utf-8") as errlog:
        async with stdio_client(server_params(), errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                gate = PolicyGate(session, load_policy(), payment_skill["meta"], approval_mode="defer")
                gate.record_human_decision(request, approved, approver)
                invoice_id = request["arguments"].get("invoice_id", "")
                automation.emit("APPROVAL_DECISION", "finance-controller", key=invoice_id, invoice_id=invoice_id,
                                decision="APPROVED" if approved else "REJECTED", approver=approver,
                                amount_inr=request["arguments"].get("amount_inr"))
                if not approved:
                    return {"status": "REJECTED_BY_APPROVER"}
                result = await session.call_tool("release_payment", arguments=request["arguments"])
                data = json.loads(result.content[0].text)        # the server still re-checks every control
                automation.from_tool_call("finance-controller", "release_payment", request["arguments"],
                                          "HUMAN APPROVED", data)
                return data


def friendly_error(e: Exception) -> str:
    text = str(e)
    if isinstance(e, errors.APIError):
        if e.code == 400 and "API key" in text:
            return "Your Gemini API key is not valid. Check it in the sidebar or in your Streamlit secrets."
        if e.code == 429:
            return "Gemini free-tier limit reached. Wait a minute, or pick a lighter model in the sidebar."
        if e.code == 404:
            return "That Gemini model is not available to your key. Pick another model in the sidebar."
    return f"Something went wrong: {text[:400]}"


# ======================================================================
# Sidebar
# ======================================================================
skills = list_skills()
skill_by_name = {s["meta"]["name"]: s for s in skills}

with st.sidebar:
    st.title("🛡️ ProcureGuard AI")
    st.caption("Domain-expert AI agents for Procure-to-Pay")

    agent_name = st.radio(
        "Choose your expert agent",
        list(skill_by_name),
        format_func=lambda n: f"{ICONS[n]} {n.replace('-', ' ').title()}",
        captions=[f"v{s['meta']['version']} · {len(s['meta']['allowed_tools'])} tools allowed" for s in skills],
    )
    skill = skill_by_name[agent_name]

    st.divider()
    api_key = setting("GEMINI_API_KEY")
    if not api_key or api_key.startswith("paste"):
        api_key = st.text_input("Gemini API key", type="password",
                                help="Free key from aistudio.google.com/apikey. On Streamlit Cloud, add it under Settings > Secrets.")
    else:
        st.success("Gemini API key loaded", icon="🔑")
    default_model = setting("GEMINI_MODEL", MODELS[0])
    model = st.selectbox("Gemini model", MODELS, index=MODELS.index(default_model) if default_model in MODELS else 0)
    approver = st.text_input("Approver name (for the audit log)", value="Finance Controller")

    st.divider()
    policy = load_policy()
    st.caption(f"Business date: **{policy['AS_OF_DATE']}** · Auto-approval limit: **{inr(policy['HITL_PAYMENT_THRESHOLD_INR'])}**")
    c1, c2 = st.columns(2)
    if c1.button("New chat", width="stretch"):
        state.chats.pop(agent_name, None)
        st.rerun()
    if c2.button("Reset data", width="stretch", help="Restore all invoices and the bank account to the starting state"):
        reset()
        state.queue = []
        state.chats = {}
        st.toast("Demo data reset")
        st.rerun()

    if REPLAY_DIR:
        files = sorted(Path(REPLAY_DIR).glob("*.json"))
        pick = st.selectbox("Recorded session", [f.stem for f in files])
        if pick and state.get("replayed") != pick:
            load_record(json.loads((Path(REPLAY_DIR) / f"{pick}.json").read_text(encoding="utf-8")))
            state.replayed = pick
            st.rerun()
        st.caption("Replay of a session recorded on the live app. No AI calls are made.")
    else:
        with st.expander("Session record (for the project report)"):
            st.caption("A compressed copy of this session: chats, tool calls, approvals, RAG answers, "
                       "n8n events and the audit log. It can be re-displayed offline.")
            if st.button("Create record", width="stretch"):
                st.code(encode_record(session_record(model)), language=None, wrap_lines=True)

if api_key and "llm" not in state:
    state.llm = genai.Client(api_key=api_key)


# ======================================================================
# Tabs
# ======================================================================
queue_label = f"✅ Approvals ({len(state.queue)})" if state.queue else "✅ Approvals"
tab_dash, tab_desk, tab_rag, tab_approvals, tab_auto, tab_audit, tab_policy, tab_red, tab_how = st.tabs(
    ["📊 AP Dashboard", "🤖 Agent Desk", "📚 Policy Assistant", queue_label, "⚡ Automation (n8n)",
     "🧾 Audit Trail", "📘 Policy & Skills", "🧪 Red Team", "🧭 How it works"])

# ----------------------------------------------------------------------
# 1. Agent Desk - chat with the chosen expert
# ----------------------------------------------------------------------
with tab_desk:
    st.subheader(f"{ICONS[agent_name]} {agent_name.replace('-', ' ').title()}")
    st.caption(skill["meta"]["description"])
    if state.queue:
        st.warning(f"{len(state.queue)} payment(s) waiting for approval - open the Approvals tab.", icon="⏳")

    chat = chat_for(agent_name)
    for msg in chat["messages"]:
        with st.chat_message(msg["role"], avatar="🧑‍💼" if msg["role"] == "user" else ICONS[agent_name]):
            st.markdown(msg["content"])
            if msg.get("trace"):
                with st.expander(f"🔧 {len(msg['trace'])} tool calls through the policy gate"):
                    st.dataframe(pd.DataFrame(msg["trace"]), hide_index=True)

    clicked = None
    cols = st.columns(len(SUGGESTIONS[agent_name]))
    for col, text in zip(cols, SUGGESTIONS[agent_name]):
        if col.button(text, key=f"sug-{agent_name}-{text}", width="stretch"):
            clicked = text
    prompt = st.chat_input(f"Ask the {agent_name.replace('-', ' ')}...") or clicked

    if prompt:
        if not api_key and "llm" not in state:
            st.error("Add your Gemini API key in the sidebar first.")
            st.stop()
        chat["messages"].append({"role": "user", "content": prompt})
        with st.chat_message("user", avatar="🧑‍💼"):
            st.markdown(prompt)
        with st.chat_message("assistant", avatar=ICONS[agent_name]):
            with st.status("Agent is working: reading the ERP, running checks...", expanded=False) as status:
                try:
                    answer, trace, history, pending = asyncio.run(
                        ask_agent(skill, prompt, chat["history"], state.llm, model))
                    chat["history"] = history
                    for request in pending:
                        request.update({"id": f"APR-{len(state.queue) + 1:03d}-{datetime.now():%H%M%S}",
                                        "agent": agent_name, "requested_at": datetime.now().strftime("%H:%M:%S")})
                        state.queue.append(request)
                    status.update(label=f"Done - {len(trace)} tool calls", state="complete")
                except Exception as e:                                   # noqa: BLE001
                    answer, trace = f"⚠️ {friendly_error(e)}", []
                    status.update(label="Failed", state="error")
        chat["messages"].append({"role": "assistant", "content": answer, "trace": trace})
        st.rerun()

# ----------------------------------------------------------------------
# 2. Approvals - human-in-the-loop queue (Lab 9)
# ----------------------------------------------------------------------
with tab_approvals:
    st.subheader("Finance Controller approval queue")
    st.caption("Payments above the auto-approval limit (aggregated per vendor over the policy window) wait here. "
               "Approving sends the payment to the server, which re-checks every control before money moves.")
    if not state.queue:
        st.info("No payments are waiting. Ask the Payment Approval agent to pay INV-1003 to see one arrive.")
    for request in list(state.queue):
        args = request["arguments"]
        with st.container(border=True):
            a, b = st.columns([3, 1])
            a.markdown(f"**{request['id']}** · Invoice **{args.get('invoice_id')}** · Vendor **{request.get('vendor_id')}**")
            a.markdown(f"Amount: **{inr(float(args.get('amount_inr', 0)))}** · "
                       f"Aggregate for vendor in window: **{inr(request.get('aggregate_inr', 0))}**")
            a.caption(f"Why it needs you: {request['reason']}")
            a.caption(f"Agent's note: {args.get('approver_note', '-')}")
            approve = b.button("Approve", key=f"ok-{request['id']}", type="primary", width="stretch")
            reject = b.button("Reject", key=f"no-{request['id']}", width="stretch")
            if approve or reject:
                result = asyncio.run(decide(request, approve, approver))
                state.queue.remove(request)
                if result.get("status") == "PAYMENT_RELEASED":
                    note = (f"✅ **{approver} approved {request['id']}.** Payment released: "
                            f"{result['amount_text']}, reference `{result['reference']}`.")
                elif result.get("status") == "REJECTED_BY_APPROVER":
                    note = f"⛔ **{approver} rejected {request['id']}.** No payment was made."
                else:
                    note = (f"🛑 **{approver} approved {request['id']}, but the server refused:** "
                            + "; ".join(result.get("reasons", [])))
                chat_for(request["agent"])["messages"].append({"role": "assistant", "content": note})
                st.toast(note.replace("**", ""))
                st.rerun()

# ----------------------------------------------------------------------
# 3. AP Dashboard - the ERP as the agents see it
# ----------------------------------------------------------------------
with tab_dash:
    render_dashboard()

# ----------------------------------------------------------------------
# 4. Audit Trail - every gate decision and every payment
# ----------------------------------------------------------------------
with tab_audit:
    st.subheader("Audit trail")
    log_file = LOG_DIR / "audit_log.jsonl"
    entries = []
    if log_file.exists():
        entries = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if entries:
        df = pd.DataFrame(entries)[["timestamp", "agent", "tool", "route", "approved", "message"]].iloc[::-1]
        routes = st.multiselect("Show routes", sorted(df["route"].unique()), default=sorted(df["route"].unique()))
        st.dataframe(df[df["route"].isin(routes)], hide_index=True)
        st.download_button("Download audit log (CSV)", df.to_csv(index=False), "audit_log.csv", "text/csv")
    else:
        st.info("No tool calls yet. Ask an agent something on the Agent Desk.")
    st.markdown("**Payments ledger**")
    ledger = json.loads((LIVE / "treasury.json").read_text(encoding="utf-8"))["payments_ledger"]
    st.dataframe(pd.DataFrame(ledger) if ledger else pd.DataFrame(columns=["reference", "invoice_id", "amount_inr"]),
                 hide_index=True)

# ----------------------------------------------------------------------
# 5. Policy & Skills - change the rules without code (Lab 11), read the SOPs (Lab 10)
# ----------------------------------------------------------------------
with tab_policy:
    st.subheader("Company policy (p2p_heuristics.md)")
    st.caption("These numbers are read by the agents, the MCP server and the approval gate. Change one and every "
               "part of the system follows - no code changes.")
    editable = {                                     # key: (label, step, number type)
        "PRICE_TOLERANCE_PCT": ("Price tolerance over PO (%)", 0.5, float),
        "HITL_PAYMENT_THRESHOLD_INR": ("Auto-approval limit per vendor (Rs)", 50000, int),
        "SPLIT_INVOICE_WINDOW_DAYS": ("Invoice-splitting window (days)", 1, int),
        "BANK_CHANGE_COOLING_DAYS": ("Bank-change cooling period (days)", 1, int),
        "MIN_CASH_BUFFER_INR": ("Minimum cash buffer (Rs)", 100000, int),
    }
    with st.form("policy"):
        new_values = {}
        cols = st.columns(len(editable))
        for col, (key, (label, step, kind)) in zip(cols, editable.items()):
            new_values[key] = col.number_input(label, value=kind(policy[key]), step=kind(step),
                                               min_value=kind(0), key=f"pol-{key}")
        if st.form_submit_button("Save policy", type="primary"):
            text = POLICY_FILE.read_text(encoding="utf-8")
            for key, value in new_values.items():
                text = re.sub(rf"(?m)^{key}\s*=.*$", f"{key} = {value}", text)
            POLICY_FILE.write_text(text, encoding="utf-8")
            st.toast("Policy saved - agents use it from the next question")
            st.rerun()
    if st.button("Restore default policy"):
        shutil.copyfile(DEFAULT_POLICY, POLICY_FILE)
        st.rerun()
    with st.expander("Read the full policy document"):
        st.markdown(POLICY_FILE.read_text(encoding="utf-8"))

    st.subheader("Agent skills (SKILL.md)")
    archived = load_skill(SKILLS_DIR / "archive" / "payment-approval-agent_v1.0.0.md")
    options = {f"{s['meta']['name']} v{s['meta']['version']}": s for s in skills + [archived]}
    chosen = options[st.selectbox("Skill file", list(options))]
    m1, m2 = st.columns([1, 2])
    m1.markdown("**YAML metadata**")
    m1.json({k: v for k, v in chosen["meta"].items()}, expanded=True)
    m2.markdown("**Procedure, guardrails and output schema**")
    with m2.container(height=420):
        st.markdown(chosen["body"])


# ----------------------------------------------------------------------
# 7. Policy Assistant - agentic RAG over SOPs, regulations, contracts and vendor emails
# ----------------------------------------------------------------------
RAG_SUGGESTIONS = [
    "Who do we call to verify the Apex Steel bank change?",
    "Can Greenleaf charge Rs 410 per carton of A4 paper?",
    "Nova says the Pune trip was approved verbally. Is that enough, and what rate applies?",
    "What happens if we pay QuickTech's invoice late?",
    "Can we claim GST credit on Nova Logistics' invoice?",
    "Is it allowed to pay two Greenleaf invoices separately to stay under Rs 5 lakh?",
]
TRUST_BADGE = {"internal": "🟢 company document", "vendor-submitted": "🔴 vendor-submitted (untrusted)"}

with tab_rag:
    st.subheader("📚 Policy & Contract Assistant")
    st.caption("Agentic RAG: route → hybrid retrieval (ChromaDB dense vectors + BM25 keywords, fused) → "
               "Gemini grades each passage → rewrite and retrieve again if needed → answer with citations → "
               "code checks the citations. Vendor emails are screened for prompt injection before indexing.")
    info = kb_index.stats()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Documents", info["documents"])
    k2.metric("Chunks indexed", info["chunks"])
    k3.metric("Vector size", info["dense_dimensions"])
    k4.metric("Chunks masked by injection screen", len(info["screened_chunks"]))

    use_ai = st.toggle("Use Gemini for grading, rewriting and answering", value=bool(api_key),
                       help="Off = backup mode: rule-based grading and an extractive answer, no API calls.")
    rag_clicked = None
    rcols = st.columns(3)
    for i, text in enumerate(RAG_SUGGESTIONS):
        if rcols[i % 3].button(text, key=f"rag-{i}", width="stretch"):
            rag_clicked = text
    with st.form("rag-form", clear_on_submit=True):
        typed = st.text_input("Ask about a policy, a law, a vendor contract or a vendor's email")
        asked = st.form_submit_button("Ask", type="primary")
    question = rag_clicked or (typed if asked and typed.strip() else None)
    if question:
        with st.spinner("Retrieving, grading and answering..."):
            try:
                llm = state.get("llm") if use_ai else None
                result = rag_agent.ask(question, llm, model)
            except Exception as e:                                       # noqa: BLE001
                st.warning(f"Gemini step failed ({friendly_error(e)}). Answering in backup mode.")
                result = rag_agent.ask(question, None, model)
        result["asked_at"] = datetime.now().strftime("%H:%M:%S")
        state.rag_runs.append(result)

    for run in reversed(state.rag_runs):
        with st.container(border=True):
            st.markdown(f"**Q: {run['question']}**  \n<span style='color:#52514e;font-size:.85rem'>"
                        f"{run.get('asked_at', '')} · mode: {run['mode']}</span>", unsafe_allow_html=True)
            st.markdown(run["answer"])
            for w in run["warnings"]:
                st.warning(w, icon="⚠️")
            a, b = st.columns([2, 3])
            with a.expander("Agent steps", expanded=False):
                st.dataframe(pd.DataFrame(run["steps"]), hide_index=True)
            with b.expander(f"Sources used ({len(run['sources'])})", expanded=False):
                for src in run["sources"]:
                    st.markdown(f"**[{src['source']}] {src['title']} › {src['section']}**  \n"
                                f"{TRUST_BADGE.get(src['trust'], src['trust'])} · `{src['chunk_id']}` · "
                                f"fused {src['rrf']} · dense {src['dense_sim']} · BM25 {src['bm25']}")
                    if src["injection_flags"]:
                        st.caption("Injection screen masked text here: " + ", ".join(src["injection_flags"]))
                    st.caption(src["text"][:600])

    with st.expander("What is in the knowledge base"):
        rows = {}
        for c in kb_index.load_chunks():
            row = rows.setdefault(c["doc_id"], {"doc_id": c["doc_id"], "title": c["title"], "type": c["doc_type"],
                                                "vendor": c["vendor_id"] or "general", "trust": c["trust"],
                                                "chunks": 0, "file": c["path"]})
            row["chunks"] += 1
        st.dataframe(pd.DataFrame(rows.values()), hide_index=True)
        if info["screened_chunks"]:
            st.markdown("**Masked by the injection screen before indexing**")
            st.dataframe(pd.DataFrame(info["screened_chunks"]), hide_index=True)

# ----------------------------------------------------------------------
# 8. Automation - n8n follow-up workflows
# ----------------------------------------------------------------------
with tab_auto:
    st.subheader("⚡ Automation with n8n")
    url = automation.webhook_url()
    if url:
        st.success(f"Events are sent to the n8n webhook ({url.split('/webhook')[0]}/webhook/...).", icon="🔗")
    else:
        st.info("No N8N_WEBHOOK_URL set: events are kept in the outbox below. Run n8n and add the webhook URL "
                "to .env or Streamlit secrets to send them live.", icon="📮")
    st.markdown("""
| n8n workflow (in `n8n/`) | Trigger | What it does |
| :--- | :--- | :--- |
| 1. AP control alerts | Webhook from this app | Formats each event, emails the Finance Controller (approvals, fraud, blocks), opens a call-back task for bank changes, logs every event in Postgres |
| 2. Daily MSME & discount digest | Schedule, 09:00 IST | Reads the open invoices and vendors, lists MSME deadlines within 7 days, discount windows and non-PO invoices, emails the digest |
| 3. Bank-change call-back form | n8n form filled by the Vendor Master team | Checks the number called against the phone on file in the contract, records VERIFIED or FAILED, escalates failures |
""")
    events = automation.read_outbox()
    since = state.started_at
    mine = [e for e in events if e["occurred_at"] >= since] if not REPLAY_DIR else events
    c1, c2, c3 = st.columns(3)
    c1.metric("Events this session", len(mine))
    c2.metric("Critical", sum(e["severity"] == "critical" for e in mine))
    c3.metric("Waiting for a human", sum(e["event"] == "APPROVAL_REQUIRED" for e in mine))
    if mine:
        df = pd.DataFrame([{"time": e["occurred_at"][11:19], "event": e["event"], "severity": e["severity"],
                            "agent": e["agent"], "about": e.get("key", ""), "delivery": e["delivery"],
                            "payload": json.dumps(e["payload"])[:160]} for e in reversed(mine)])
        st.dataframe(df, hide_index=True)
        st.download_button("Download events (JSON)", json.dumps(mine, indent=2), "n8n_events.json", "application/json")
    else:
        st.caption("No events yet. Verify INV-1002 or ask the Payment agent to pay INV-1003.")
    if st.button("Send a test event"):
        rec = automation.emit("TEST", "streamlit-app", note="Test event from the Automation tab")
        st.toast(f"Test event: {rec['delivery']}")
        st.rerun()
    with st.expander("Workflow files"):
        for f in sorted((HERE / "n8n").glob("*.json")):
            st.download_button(f"Download {f.name}", f.read_text(encoding="utf-8"), f.name, "application/json",
                               key=f"dl-{f.name}")

# ----------------------------------------------------------------------
# 9. Red Team - adversarial tests, v1 skill vs v2 skill (Lab 12)
# ----------------------------------------------------------------------
with tab_red:
    import red_team
    st.subheader("🧪 Red-team lab: weak v1 skill vs hardened v2 skill")
    st.caption("Each attack resets the demo data, runs the Payment Approval agent with the chosen skill version, "
               "and the human approver automatically answers NO. Uses several Gemini calls per attack.")
    picks = st.multiselect("Attacks", list(red_team.ATTACKS), default=[1],
                           format_func=lambda n: f"{n}. {red_team.ATTACKS[n]['name']}")
    versions = st.multiselect("Skill versions", ["v1", "v2"], default=["v1", "v2"])
    if st.button("Run selected attacks", type="primary", disabled=not (picks and versions)):
        if "llm" not in state:
            st.error("Add your Gemini API key in the sidebar first.")
        else:
            bar = st.progress(0.0, text="Running attacks...")
            jobs = [(n, v) for n in picks for v in versions]
            for i, (n, v) in enumerate(jobs, 1):
                bar.progress((i - 1) / len(jobs), text=f"Attack {n} on {v}...")
                try:
                    r = asyncio.run(red_team.run_attack(n, v, state.llm))
                    gate = [f"{c['arguments'].get('invoice_id')}: {c['route']}" for c in r["calls"]
                            if c["tool"] == "release_payment"]
                    state.red_team.append({
                        "attack": f"{n}. {red_team.ATTACKS[n]['name']}", "skill": v, "tool_calls": len(r["calls"]),
                        "release_payment gate": "; ".join(gate) or "no payment attempted",
                        "money paid": ", ".join(f"{p['invoice_id']} Rs {p['amount_inr']:,.0f}" for p in r["payments"]) or "None",
                        "expected": red_team.ATTACKS[n]["expect"], "answer": r["answer"],
                        "tools": [f"{c['tool']} [{c['route']}]" for c in r["calls"]]})
                except Exception as e:                                   # noqa: BLE001
                    state.red_team.append({"attack": f"{n}. {red_team.ATTACKS[n]['name']}", "skill": v,
                                           "tool_calls": 0, "release_payment gate": "error",
                                           "money paid": "-", "expected": red_team.ATTACKS[n]["expect"],
                                           "answer": friendly_error(e), "tools": []})
            reset()
            bar.progress(1.0, text="Done - demo data reset")
    if state.red_team:
        st.dataframe(pd.DataFrame(state.red_team)[["attack", "skill", "tool_calls", "release_payment gate",
                                                    "money paid", "expected"]], hide_index=True)
        for r in state.red_team:
            with st.expander(f"Attack {r['attack']} - skill {r['skill']}: agent's answer and tool calls"):
                st.caption(" → ".join(r["tools"]) or "no tool calls")
                st.markdown(r["answer"])

# ----------------------------------------------------------------------
# 6. How it works
# ----------------------------------------------------------------------
with tab_how:
    st.subheader("Architecture: MCP host, client and server")
    st.graphviz_chart("""
digraph {
  rankdir=LR; node [shape=box, style="rounded", fontname="Helvetica", fontsize=11];
  user [label="You (browser)"]; app [label="Streamlit app\\nMCP host"];
  skill [label="SKILL.md +\\npolicy file"]; llm [label="Gemini\\nreasons, picks tools"];
  gate [label="Policy gate\\nlimits, audit, approval queue", style="rounded,filled", fillcolor="#e3eefc"];
  fc [label="Finance Controller\\nApprove / Reject"]; client [label="MCP client"];
  server [label="p2p_server.py\\n14 tools, 2 resources, 3 prompts"]; erp [label="Demo ERP\\n(JSON files)"];
  kb [label="Knowledge base (RAG)\\nChromaDB + BM25\\nSOPs, laws, contracts, emails", style="rounded,filled", fillcolor="#e6f6ef"];
  n8n [label="n8n workflows\\nalerts, digest, call-back form", style="rounded,filled", fillcolor="#fdf0e6"];
  user -> app; skill -> llm; app -> llm [label="question"]; llm -> gate [label="tool call"];
  gate -> fc [label="above limit"]; gate -> client [label="approved"]; fc -> client;
  client -> server [label="JSON-RPC 2.0 / stdio"]; server -> erp; server -> kb [label="search_p2p_knowledge"];
  gate -> n8n [label="events (webhook)"]; app -> kb [label="Policy Assistant"];
}""")
    st.markdown("""
| Step | What happens | Course link |
| :--- | :--- | :--- |
| 1. Discover | The app starts `p2p_server.py`, does the `initialize` handshake, lists tools and reads the policy resource | Lab 7, Lab 8 |
| 2. Reason | Gemini gets only the chosen SKILL.md, the policy and that skill's allowed tools | Lab 10, Lab 11 |
| 3. Authorize | Every proposed call passes the policy gate: auto, blocked, or parked for a human | Lab 9 |
| 4. Execute | Approved calls go to the server, which re-checks payments before money moves | Lab 7, Lab 12 |
| 5. Retrieve | Agents cite contracts and rules through `search_p2p_knowledge`; the Policy Assistant runs agentic RAG | RAG module |
| 6. Follow up | Holds, approvals, fraud flags and payments become n8n events: emails, call-back tasks, Postgres log | n8n module |
""")
