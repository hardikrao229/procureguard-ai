"""
policy_gate.py - the client-side SECURITY GATE with Human-in-the-Loop (Lab 9).

The MCP server does not know who is typing or why. So before ANY tool call that the
AI proposes is sent to the server, this gate inspects it:

  * Least privilege   - tools not listed in the skill's allowed_tools are BLOCKED
  * Tiered autonomy   - read-only tools and 'place on hold' run automatically
  * Approval limits   - payments are aggregated per vendor over the policy window
                        (this defeats invoice-splitting / salami slicing, Lab 12)
  * Human-in-the-loop - above the threshold a human must approve
                        (terminal: type YES  |  web app: click Approve)
  * Hard ceiling      - above the hard limit the payment is BLOCKED outright
  * Audit trail       - every decision is written to logs/audit_log.jsonl
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from policy import inr

LOG_DIR = Path(__file__).resolve().parent / "logs"

READ_ONLY_TOOLS = {
    "list_open_invoices", "get_invoice", "get_purchase_order", "get_goods_receipt",
    "get_vendor_profile", "get_payment_history", "get_cash_position", "three_way_match",
    "check_duplicate_invoice", "calculate_vendor_risk_score", "calculate_payment_schedule",
    "search_p2p_knowledge",
}
PROTECTIVE_TOOLS = {"place_invoice_on_hold"}      # changes data, but only makes things safer
HIGH_STAKES_TOOLS = {"release_payment"}           # moves money


class PolicyGate:
    """approval_mode decides who answers a human-approval request:
         'ask'   - terminal: the person types YES / NO          (agent.py)
         'deny'  - automatic NO, for unattended tests           (red_team.py)
         'defer' - park it; the web app shows Approve / Reject  (streamlit_app.py)
    """

    def __init__(self, session, policy: dict, skill_meta: dict, approval_mode: str = "ask", notify=print):
        self.session = session                    # MCP session, used for read-only look-ups
        self.policy = policy
        self.skill_name = skill_meta["name"]
        self.skill_version = skill_meta["version"]
        self.allowed_tools = set(skill_meta["allowed_tools"])
        self.approval_mode = approval_mode
        self.notify = notify                      # where gate messages go (terminal or web page)
        self.pending = []                         # payments parked for a human (defer mode)
        LOG_DIR.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    async def authorize(self, tool_name: str, args: dict) -> tuple:
        """Returns (approved: bool, route: str, message: str)."""
        if tool_name not in self.allowed_tools:
            return self._log(tool_name, args, False, "BLOCKED",
                             f"'{tool_name}' is not in the allowed_tools of skill {self.skill_name}")

        if tool_name in READ_ONLY_TOOLS:
            return self._log(tool_name, args, True, "AUTO", "Read-only tool - auto-authorised")

        if tool_name in PROTECTIVE_TOOLS:
            return self._log(tool_name, args, True, "AUTO", "Protective action (hold) - auto-authorised and logged")

        if tool_name in HIGH_STAKES_TOOLS:
            return await self._check_payment(tool_name, args)

        return self._log(tool_name, args, False, "BLOCKED", "Unknown tool - blocked by default (zero trust)")

    # ------------------------------------------------------------------
    async def _check_payment(self, tool_name: str, args: dict) -> tuple:
        amount = float(args.get("amount_inr", 0) or 0)
        hitl_limit = self.policy["HITL_PAYMENT_THRESHOLD_INR"]
        hard_limit = self.policy["HARD_PAYMENT_LIMIT_INR"]

        if amount > hard_limit:
            return self._log(tool_name, args, False, "BLOCKED",
                             f"{inr(amount)} is above the hard limit of {inr(hard_limit)} - Board authority needed")

        # Who is being paid, and how much have they received recently?
        invoice = await self._call("get_invoice", {"invoice_id": args.get("invoice_id", "")})
        vendor_id = invoice.get("vendor_id", "UNKNOWN")
        history = await self._call("get_payment_history", {"vendor_id": vendor_id})
        as_of = datetime.strptime(str(self.policy["AS_OF_DATE"]), "%Y-%m-%d")
        window_start = as_of - timedelta(days=self.policy["SPLIT_INVOICE_WINDOW_DAYS"])
        recent = sum(p["amount_inr"] for p in history.get("payments", [])
                     if datetime.strptime(p["paid_on"], "%Y-%m-%d") >= window_start)
        aggregate = recent + amount

        self.notify(f"[POLICY GATE] Payment request: {inr(amount)} to vendor {vendor_id} "
                    f"for {args.get('invoice_id')}")
        self.notify(f"[POLICY GATE] Already paid to this vendor in last "
                    f"{self.policy['SPLIT_INVOICE_WINDOW_DAYS']} days: {inr(recent)} -> aggregate {inr(aggregate)}")

        if aggregate <= hitl_limit:
            return self._log(tool_name, args, True, "AUTO",
                             f"Aggregate {inr(aggregate)} within auto-approval limit {inr(hitl_limit)}",
                             extra={"vendor_id": vendor_id, "aggregate_inr": aggregate})

        # ---------------- Human-in-the-loop ----------------
        reason = (f"Aggregate payments to {vendor_id} would be {inr(aggregate)}, above the "
                  f"{inr(hitl_limit)} auto-approval limit")
        if recent > 0:
            reason += " (possible invoice splitting - earlier payments in the same window)"
        extra = {"vendor_id": vendor_id, "aggregate_inr": aggregate}

        if self.approval_mode == "defer":               # web app: wait for a button click
            self.pending.append({"tool": tool_name, "arguments": args, "reason": reason, **extra})
            return self._log(tool_name, args, False, "PENDING APPROVAL",
                             reason + ". Sent to the Finance Controller's approval queue in the app. "
                             "Do not retry; tell the user approval is pending.", extra=extra)

        print("\n" + "!" * 68)
        print("  HUMAN-IN-THE-LOOP APPROVAL REQUIRED (Finance Controller)")
        print(f"  Invoice {args.get('invoice_id')} | Vendor {vendor_id} | Amount {inr(amount)}")
        print(f"  Reason: {reason}")
        print(f"  Agent's note: {args.get('approver_note', '')}")
        print("!" * 68)
        if self.approval_mode == "ask":
            answer = input("  Do you approve this payment? (YES / NO): ").strip().upper()
        else:
            answer = "NO"
            print("  (non-interactive test run - approver automatically answers NO)")

        if answer == "YES":
            return self._log(tool_name, args, True, "HUMAN APPROVED", reason, extra=extra)
        return self._log(tool_name, args, False, "HUMAN REJECTED",
                         reason + ". The approver rejected it - do not retry.", extra=extra)

    def record_human_decision(self, request: dict, approved: bool, approver: str = "Finance Controller") -> None:
        """Web app: write the Approve / Reject click to the audit log."""
        route = "HUMAN APPROVED" if approved else "HUMAN REJECTED"
        self._log(request["tool"], request["arguments"], approved, route,
                  f"{request['reason']}. Decision by {approver} in the web app.",
                  extra={"vendor_id": request.get("vendor_id"), "aggregate_inr": request.get("aggregate_inr")})

    # ------------------------------------------------------------------
    async def _call(self, tool: str, args: dict) -> dict:
        result = await self.session.call_tool(tool, arguments=args)
        try:
            return json.loads(result.content[0].text)
        except (ValueError, IndexError):
            return {}

    def _log(self, tool, args, approved, route, message, extra=None) -> tuple:
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "agent": self.skill_name, "skill_version": self.skill_version,
            "tool": tool, "arguments": args, "route": route,
            "approved": approved, "message": message, **(extra or {}),
        }
        with open(LOG_DIR / "audit_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return approved, route, message
