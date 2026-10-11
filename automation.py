"""
automation.py - sends ProcureGuard business events to n8n (workflow automation).

The agents decide; n8n does the follow-up work that a person would otherwise do by hand:
emails to the Finance Controller, a call-back task for the Vendor Master team, a log of every
control event in Postgres, and a daily MSME / cash digest (see the n8n/ folder).

Every event is ALWAYS written to logs/n8n_outbox.jsonl (so nothing is lost and the app can show it).
If N8N_WEBHOOK_URL is set (in .env or Streamlit secrets) the event is also POSTed to n8n.

Events:
  INVOICE_ON_HOLD           an agent put an invoice on hold
  APPROVAL_REQUIRED         the policy gate parked a payment for the Finance Controller
  APPROVAL_DECISION         the Finance Controller clicked Approve or Reject
  PAYMENT_RELEASED          money left the bank account
  SERVER_CONTROL_REJECTION  the MCP server refused a payment (defence in depth)
  GATE_BLOCKED              the policy gate blocked a tool call
  VENDOR_RISK_ALERT         vendor is HIGH risk or inside the bank-change cooling period
  INJECTION_DETECTED        supplier text tried to instruct the AI
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTBOX = HERE / "logs" / "n8n_outbox.jsonl"
SEVERITY = {
    "INVOICE_ON_HOLD": "warning", "APPROVAL_REQUIRED": "action", "APPROVAL_DECISION": "info",
    "PAYMENT_RELEASED": "info", "SERVER_CONTROL_REJECTION": "critical", "GATE_BLOCKED": "critical",
    "VENDOR_RISK_ALERT": "critical", "INJECTION_DETECTED": "critical", "TEST": "info",
}
DEDUPE_MINUTES = 30            # the same alert for the same invoice/vendor is not sent twice in this window


def webhook_url() -> str:
    return os.getenv("N8N_WEBHOOK_URL", "").strip()


def read_outbox() -> list:
    if not OUTBOX.exists():
        return []
    return [json.loads(line) for line in OUTBOX.read_text(encoding="utf-8").splitlines() if line.strip()]


def _duplicate(event: str, key: str) -> bool:
    since = (datetime.now() - timedelta(minutes=DEDUPE_MINUTES)).isoformat(timespec="seconds")
    return any(r["event"] == event and r.get("key") == key and r["occurred_at"] >= since
               for r in read_outbox()[-60:])


def emit(event: str, agent: str, key: str = "", **payload) -> dict:
    """Write the event to the outbox and, if configured, POST it to n8n."""
    if key and event not in ("TEST", "APPROVAL_DECISION", "PAYMENT_RELEASED") and _duplicate(event, key):
        return {}
    try:
        from policy import load_policy
        business_date = str(load_policy()["AS_OF_DATE"])
    except Exception:                                                  # noqa: BLE001
        business_date = ""
    record = {
        "event_id": f"EVT-{datetime.now():%Y%m%d%H%M%S%f}"[:22],
        "event": event, "severity": SEVERITY.get(event, "info"), "key": key,
        "occurred_at": datetime.now().isoformat(timespec="seconds"), "business_date": business_date,
        "app": "ProcureGuard AI", "agent": agent, "payload": payload,
    }
    url = webhook_url()
    if url:
        try:
            import requests
            r = requests.post(url, json=record, timeout=6)
            record["delivery"] = f"sent to n8n (HTTP {r.status_code})"
        except Exception as e:                                         # noqa: BLE001
            record["delivery"] = f"n8n unreachable ({type(e).__name__}) - kept in outbox"
    else:
        record["delivery"] = "outbox only (N8N_WEBHOOK_URL not set)"
    OUTBOX.parent.mkdir(exist_ok=True)
    with open(OUTBOX, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def from_tool_call(agent: str, tool: str, args: dict, route: str, data: dict) -> list:
    """Turns one tool call (after the gate) into zero or more n8n events."""
    out = []
    inv = args.get("invoice_id", "")
    if route == "PENDING APPROVAL":
        out.append(emit("APPROVAL_REQUIRED", agent, key=inv, invoice_id=inv,
                        amount_inr=args.get("amount_inr"), reason=data.get("message", ""),
                        agent_note=args.get("approver_note", "")))
    elif route == "BLOCKED":
        out.append(emit("GATE_BLOCKED", agent, key=f"{tool}:{inv}", tool=tool, arguments=args,
                        reason=data.get("message", "")))
    if tool == "place_invoice_on_hold" and data.get("new_status") == "ON_HOLD":
        out.append(emit("INVOICE_ON_HOLD", agent, key=inv, invoice_id=inv, reason=data.get("reason", "")))
    if tool == "release_payment":
        if data.get("status") == "PAYMENT_RELEASED":
            out.append(emit("PAYMENT_RELEASED", agent, key=inv, invoice_id=inv, amount_inr=data.get("amount_inr"),
                            reference=data.get("reference"), new_bank_balance_inr=data.get("new_bank_balance_inr")))
        elif data.get("status") == "REJECTED_BY_SERVER_CONTROLS":
            out.append(emit("SERVER_CONTROL_REJECTION", agent, key=inv, invoice_id=inv,
                            reasons=data.get("reasons", [])))
    if tool == "calculate_vendor_risk_score" and (data.get("risk_band") == "HIGH"
                                                  or data.get("bank_change_in_cooling_period")):
        out.append(emit("VENDOR_RISK_ALERT", agent, key=data.get("vendor_id", ""), vendor_id=data.get("vendor_id"),
                        vendor_name=data.get("vendor_name"), risk_score=data.get("risk_score"),
                        risk_band=data.get("risk_band"),
                        bank_change_in_cooling_period=data.get("bank_change_in_cooling_period"),
                        triggered=[b["factor"] for b in data.get("breakdown", []) if b.get("triggered")]))
    if tool == "get_invoice" and (data.get("notes_screening") or {}).get("red_flags"):
        out.append(emit("INJECTION_DETECTED", agent, key=inv, invoice_id=inv, source="invoice notes",
                        red_flags=data["notes_screening"]["red_flags"]))
    return [e for e in out if e]


if __name__ == "__main__":
    print(emit("TEST", "terminal", note="Hello from ProcureGuard"))
