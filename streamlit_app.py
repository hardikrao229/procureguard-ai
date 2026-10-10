"""
streamlit_app.py - ProcureGuard AI web app (the MCP HOST, in a browser).

Run locally:   streamlit run streamlit_app.py
Deploy:        push to GitHub, then create the app on share.streamlit.io (main file: streamlit_app.py)

Same architecture as agent.py, with a web front end:
  Browser (this app) -> Gemini (reasons) -> PolicyGate (approval queue) -> MCP client -> p2p_server.py
Large payments are parked in an APPROVAL QUEUE; the Finance Controller clicks Approve / Reject.
"""
import asyncio
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import errors
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import p2p_server as erp                      # read-only views for the dashboard
from agent import ProcureGuardAgent
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


def chat_for(skill_name: str) -> dict:
    return state.chats.setdefault(skill_name, {"messages": [], "history": []})


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
                if not approved:
                    return {"status": "REJECTED_BY_APPROVER"}
                result = await session.call_tool("release_payment", arguments=request["arguments"])
                return json.loads(result.content[0].text)        # the server still re-checks every control


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

if api_key and "llm" not in state:
    state.llm = genai.Client(api_key=api_key)


# ======================================================================
# Tabs
# ======================================================================
queue_label = f"✅ Approvals ({len(state.queue)})" if state.queue else "✅ Approvals"
tab_desk, tab_approvals, tab_dash, tab_audit, tab_policy, tab_how = st.tabs(
    ["🤖 Agent Desk", queue_label, "📊 AP Dashboard", "🧾 Audit Trail", "📘 Policy & Skills", "🧭 How it works"])

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
    invoices = json.loads((LIVE / "invoices.json").read_text(encoding="utf-8"))
    vendors = {v["vendor_id"]: v for v in json.loads((LIVE / "vendors.json").read_text(encoding="utf-8"))}
    cash = erp.get_cash_position()
    open_inv = [i for i in invoices if i["status"] == "RECEIVED"]
    held = [i for i in invoices if i["status"] == "ON_HOLD"]
    paid = [i for i in invoices if i["status"] == "PAID"]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Open invoices", len(open_inv), help=inr(sum(i["total_inr"] for i in open_inv)), border=True)
    k2.metric("On hold", len(held), help=inr(sum(i["total_inr"] for i in held)), border=True)
    k3.metric("Paid", len(paid), help=inr(sum(i["total_inr"] for i in paid)), border=True)
    k4.metric("Payable capacity today", inr(cash["payable_capacity_today_inr"]), border=True)

    rows = []
    for inv in invoices:
        match = erp.three_way_match(inv["invoice_id"])
        dup = erp.check_duplicate_invoice(inv["invoice_id"])
        risk = erp.calculate_vendor_risk_score(inv["vendor_id"])
        rows.append({
            "Invoice": inv["invoice_id"], "Vendor": vendors[inv["vendor_id"]]["name"],
            "PO": inv["po_id"] or "NONE", "Date": inv["invoice_date"], "Total": inr(inv["total_inr"]),
            "Status": inv["status"], "3-way match": match["match_result"],
            "Duplicate check": dup["duplicate_verdict"], "Vendor risk": f"{risk['risk_band']} ({risk['risk_score']})",
        })
    st.markdown("**Invoice queue** · live ERP checks (deterministic, no AI)")
    st.dataframe(pd.DataFrame(rows), hide_index=True)

    risk_rows = []
    for vid, v in vendors.items():
        r = erp.calculate_vendor_risk_score(vid)
        risk_rows.append({"Vendor": v["name"], "Risk score": r["risk_score"], "Band": r["risk_band"],
                          "GSTIN": v["gstin_status"], "MSME": "Yes" if v["msme_udyam_no"] else "No",
                          "Terms": v["payment_terms"]})
    left, right = st.columns([3, 2])
    left.markdown("**Vendor risk register**")
    left.dataframe(pd.DataFrame(risk_rows), hide_index=True)
    right.markdown("**Vendor risk score (0-100)**")
    right.bar_chart(pd.DataFrame(risk_rows).set_index("Vendor")["Risk score"], horizontal=True)

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
  server [label="p2p_server.py\\n13 tools, 1 resource, 3 prompts"]; erp [label="Demo ERP\\n(JSON files)"];
  user -> app; skill -> llm; app -> llm [label="question"]; llm -> gate [label="tool call"];
  gate -> fc [label="above limit"]; gate -> client [label="approved"]; fc -> client;
  client -> server [label="JSON-RPC 2.0 / stdio"]; server -> erp;
}""")
    st.markdown("""
| Step | What happens | Course link |
| :--- | :--- | :--- |
| 1. Discover | The app starts `p2p_server.py`, does the `initialize` handshake, lists tools and reads the policy resource | Lab 7, Lab 8 |
| 2. Reason | Gemini gets only the chosen SKILL.md, the policy and that skill's allowed tools | Lab 10, Lab 11 |
| 3. Authorize | Every proposed call passes the policy gate: auto, blocked, or parked for a human | Lab 9 |
| 4. Execute | Approved calls go to the server, which re-checks payments before money moves | Lab 7, Lab 12 |
""")
