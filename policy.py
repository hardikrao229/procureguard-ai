"""
policy.py - reads the Machine-Readable Parameters from policy/p2p_heuristics.md

The SAME markdown file is:
  * read by the AI agents (as context, through the MCP server's resource), and
  * read by this loader, so the server tools and the approval gate use the same numbers.
Change a number in the markdown file -> behaviour changes everywhere (Lab 11 idea).
"""
import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
POLICY_FILE = PROJECT_DIR / "policy" / "p2p_heuristics.md"

# A parameter line looks like:   PRICE_TOLERANCE_PCT = 2.0
_PARAM_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*=\s*([^\s#]+)\s*$")


def load_policy() -> dict:
    """Return all parameters as a dictionary, e.g. {'PRICE_TOLERANCE_PCT': 2.0, ...}."""
    params = {}
    for line in POLICY_FILE.read_text(encoding="utf-8").splitlines():
        match = _PARAM_LINE.match(line)
        if not match:
            continue
        key, raw_value = match.groups()
        try:
            value = float(raw_value)
            if value.is_integer():
                value = int(value)
        except ValueError:
            value = raw_value          # e.g. the date in AS_OF_DATE stays text
        params[key] = value
    return params


def inr(amount: float) -> str:
    """Format a number the Indian way: 826000 -> 'Rs 8,26,000.00'."""
    amount = round(float(amount), 2)
    rupees, paise = f"{amount:.2f}".split(".")
    sign = "-" if rupees.startswith("-") else ""
    rupees = rupees.lstrip("-")
    if len(rupees) > 3:
        head, tail = rupees[:-3], rupees[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1]
        rupees = f"{head},{tail}"
    return f"{sign}Rs {rupees}.{paise}"


def policy_text() -> str:
    """Return the whole policy document as text (for the agents to read)."""
    return POLICY_FILE.read_text(encoding="utf-8")


if __name__ == "__main__":
    # Quick check: python policy.py
    for k, v in load_policy().items():
        print(f"{k:32} {v}")
