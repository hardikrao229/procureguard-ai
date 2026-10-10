"""
dashboard.py - the colourful AP Dashboard tab of the ProcureGuard app.

Every number comes from the same deterministic MCP-server functions the agents use
(three-way match, duplicate check, vendor risk, payment schedule) - no AI involved.
Charts are Altair (bundled with Streamlit). Colours follow one fixed palette:
  categorical slots - blue, orange, aqua, yellow, magenta, violet (in this order)
  status            - good green, warning amber, critical red (always with a text label)
"""
import json
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

import p2p_server as erp
from policy import inr, load_policy
from reset_data import LIVE

# ---------------------------------------------------------------- palette
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, VIOLET = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6"
GOOD, WARNING, CRITICAL = "#0ca30c", "#fab219", "#d03b3b"
INK, INK_2, MUTED, GRID, BASE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"

CHECK_ORDER = ["Clean match", "Price / qty exception", "Suspected duplicate", "No purchase order", "On hold", "Paid"]
CHECK_COLORS = [BLUE, ORANGE, AQUA, YELLOW, MAGENTA, VIOLET]
BAND_COLORS = alt.Scale(domain=["LOW", "MEDIUM", "HIGH"], range=[GOOD, WARNING, CRITICAL])
READY_COLORS = alt.Scale(domain=["Ready to pay", "Blocked", "Paid"], range=[GOOD, CRITICAL, BLUE])

CSS = f"""
<style>
.pg-hero {{background: linear-gradient(110deg, #184f95 0%, {BLUE} 45%, {VIOLET} 100%);
  border-radius: 16px; padding: 22px 26px; color: #fff; margin-bottom: 18px;}}
.pg-hero h2 {{margin: 0 0 4px 0; font-size: 1.55rem; color: #fff;}}
.pg-hero p {{margin: 0; opacity: .9; font-size: .95rem;}}
.pg-hero .pill {{display: inline-block; background: rgba(255,255,255,.18); border-radius: 999px;
  padding: 3px 12px; margin: 10px 8px 0 0; font-size: .82rem;}}
.pg-card {{background: #fff; border: 1px solid rgba(11,11,11,.08); border-radius: 14px;
  padding: 14px 16px; height: 100%; box-shadow: 0 1px 2px rgba(0,0,0,.04); border-top: 5px solid var(--c);}}
.pg-card .lbl {{color: {INK_2}; font-size: .8rem; font-weight: 600; text-transform: uppercase; letter-spacing: .03em;}}
.pg-card .val {{color: {INK}; font-size: 1.55rem; font-weight: 700; margin-top: 4px; white-space: nowrap;}}
.pg-card .sub {{color: {MUTED}; font-size: .8rem; margin-top: 2px;}}
.pg-sec {{font-weight: 700; font-size: 1.05rem; color: {INK}; margin: 6px 0 0 0;}}
.pg-cap {{color: {INK_2}; font-size: .85rem; margin: 0 0 6px 0;}}
</style>
"""


def short_inr(value: float) -> str:
    """Rs 8,26,000 -> 'Rs 8.26 L'; Rs 1,25,00,000 -> 'Rs 1.25 Cr' (Indian units)."""
    value = float(value)
    if abs(value) >= 1e7:
        return f"Rs {value / 1e7:.2f} Cr"
    if abs(value) >= 1e5:
        return f"Rs {value / 1e5:.2f} L"
    return f"Rs {value:,.0f}"


LAKH = "datum.value == 0 ? '0' : format(datum.value / 100000, '~g') + ' L'"


def card(col, label: str, value: str, sub: str, color: str) -> None:
    col.markdown(f'<div class="pg-card" style="--c:{color}"><div class="lbl">{label}</div>'
                 f'<div class="val">{value}</div><div class="sub">{sub}</div></div>', unsafe_allow_html=True)


def section(title: str, caption: str) -> None:
    st.markdown(f'<p class="pg-sec">{title}</p><p class="pg-cap">{caption}</p>', unsafe_allow_html=True)


def style(chart, height: int = 300):
    return (chart.properties(height=height)
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor=INK_2, titleColor=INK_2, gridColor=GRID, domainColor=BASE,
                            tickColor=BASE, labelFontSize=12, titleFontSize=12, titleFontWeight="normal")
            .configure_legend(labelColor=INK_2, titleColor=INK_2, orient="bottom", labelFontSize=12,
                              titleFontSize=12, symbolType="circle")
            .configure_text(color=INK))


# ---------------------------------------------------------------- data
def build_data() -> dict:
    invoices = json.loads((LIVE / "invoices.json").read_text(encoding="utf-8"))
    vendors = {v["vendor_id"]: v for v in json.loads((LIVE / "vendors.json").read_text(encoding="utf-8"))}
    policy = load_policy()
    risk = {vid: erp.calculate_vendor_risk_score(vid) for vid in vendors}

    rows, leakage = [], []
    for inv in invoices:
        iid, vendor = inv["invoice_id"], vendors[inv["vendor_id"]]
        match = erp.three_way_match(iid)
        dup = erp.check_duplicate_invoice(iid)
        sched = erp.calculate_payment_schedule(iid)
        r = risk[inv["vendor_id"]]

        if inv["status"] == "PAID":
            check = "Paid"
        elif inv["status"] == "ON_HOLD":
            check = "On hold"
        elif dup["duplicate_verdict"] == "SUSPECTED DUPLICATE":
            check = "Suspected duplicate"
        elif not inv["po_id"]:
            check = "No purchase order"
        elif match["match_result"] == "EXCEPTION":
            check = "Price / qty exception"
        else:
            check = "Clean match"

        vendor_blocked = r["risk_band"] == "HIGH" or r["bank_change_in_cooling_period"]
        if check == "Paid":
            ready = "Paid"
        elif check == "Clean match" and not vendor_blocked:
            ready = "Ready to pay"
        else:
            ready = "Blocked"

        # money each control stops (pre-GST), counted once per invoice
        if check == "Suspected duplicate":
            leakage.append({"Control": "Duplicate invoice", "Invoice": iid, "Amount": inv["total_inr"]})
        elif check == "No purchase order":
            leakage.append({"Control": "No purchase order", "Invoice": iid, "Amount": inv["total_inr"]})
        elif check == "Price / qty exception":
            for line in match["lines"]:
                over_price = max(0.0, line["invoice_unit_price_inr"] - line["po_unit_price_inr"]) * line["invoice_qty"]
                if over_price:
                    leakage.append({"Control": "Price above PO", "Invoice": iid, "Amount": over_price})
                over_qty = max(0, line["cumulative_billed_qty_on_po"] - line["grn_accepted_qty"])
                if over_qty:
                    leakage.append({"Control": "Billed above accepted qty", "Invoice": iid,
                                    "Amount": over_qty * line["invoice_unit_price_inr"]})

        price_var = match["lines"][0]["price_variance_pct"] if match.get("lines") else None
        rows.append({
            "Invoice": iid, "Vendor": vendor["name"], "PO": inv["po_id"] or "NONE",
            "Total": inv["total_inr"], "Amount": inr(inv["total_inr"]), "Status": inv["status"],
            "Check": check, "Readiness": ready, "Price variance %": price_var,
            "Due": sched["contractual_or_statutory_due_date"], "Days to due": sched["days_until_due"],
            "Discount": sched["discount_amount_inr"], "MSME": "Yes" if sched["msme_vendor"] else "No",
            "Risk band": r["risk_band"], "Risk score": r["risk_score"],
        })

    vend_rows, factor_rows = [], []
    for vid, v in vendors.items():
        r = risk[vid]
        open_value = sum(x["Total"] for x in rows if x["Vendor"] == v["name"] and x["Readiness"] != "Paid")
        vend_rows.append({"Vendor": v["name"], "Risk score": r["risk_score"], "Band": r["risk_band"],
                          "On-time delivery %": v["on_time_delivery_pct"],
                          "Rejection %": v["quality_rejection_pct"], "Open value": open_value,
                          "GSTIN": v["gstin_status"], "MSME": "Yes" if v["msme_udyam_no"] else "No",
                          "Terms": v["payment_terms"]})
        for f in r["breakdown"]:
            factor_rows.append({"Vendor": v["name"], "Factor": f["factor"], "Points": f["points"],
                                "Triggered": "Yes" if f["triggered"] else "No", "Evidence": f["evidence"]})

    return {"inv": pd.DataFrame(rows), "leak": pd.DataFrame(leakage, columns=["Control", "Invoice", "Amount"]),
            "vend": pd.DataFrame(vend_rows), "factors": pd.DataFrame(factor_rows),
            "cash": erp.get_cash_position(), "policy": policy}


# ---------------------------------------------------------------- charts
def donut(inv: pd.DataFrame):
    d = inv.groupby("Check", as_index=False).agg(Value=("Total", "sum"), Invoices=("Invoice", "count"))
    d["Share"] = d["Value"] / d["Value"].sum()
    d["Label"] = d["Value"].map(short_inr)
    d["Legend"] = d["Check"] + "  ·  " + d["Label"]
    present = [c for c in CHECK_ORDER if c in set(d["Check"])]
    colors = [CHECK_COLORS[CHECK_ORDER.index(c)] for c in present]
    legend_domain = [d.loc[d["Check"] == c, "Legend"].iloc[0] for c in present]
    base = alt.Chart(d).encode(
        theta=alt.Theta("Value:Q", stack=True),
        order=alt.Order("Value:Q", sort="descending"),
        tooltip=[alt.Tooltip("Check:N", title="Check result"), alt.Tooltip("Invoices:Q"),
                 alt.Tooltip("Label:N", title="Value"), alt.Tooltip("Share:Q", format=".0%")])
    ring = base.mark_arc(innerRadius=74, outerRadius=118, stroke="#fff", strokeWidth=2).encode(
        color=alt.Color("Legend:N", scale=alt.Scale(domain=legend_domain, range=colors),
                        legend=alt.Legend(title=None, columns=2, labelLimit=260)))
    total = alt.Chart(pd.DataFrame({"t": [short_inr(d["Value"].sum())], "s": ["in AP queue"]}))
    center = (total.mark_text(fontSize=20, fontWeight="bold", color=INK, dy=-8).encode(text="t:N")
              + total.mark_text(fontSize=12, color=INK_2, dy=12).encode(text="s:N"))
    return style(ring + center, 340)


def leakage_bars(leak: pd.DataFrame):
    d = leak.groupby("Control", as_index=False).agg(Amount=("Amount", "sum"),
                                                     Invoices=("Invoice", lambda s: ", ".join(sorted(set(s)))))
    d["Label"] = d["Amount"].map(short_inr)
    bars = alt.Chart(d).mark_bar(color=CRITICAL, cornerRadiusEnd=4, height=26).encode(
        y=alt.Y("Control:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200)),
        x=alt.X("Amount:Q", title="Rs at risk (pre-GST)", axis=alt.Axis(labelExpr=LAKH, grid=True)),
        tooltip=["Control:N", alt.Tooltip("Label:N", title="Amount"), "Invoices:N"])
    text = bars.mark_text(align="left", dx=6, color=INK, fontSize=12).encode(text="Label:N")
    return style(bars + text, 230)


def payment_calendar(inv: pd.DataFrame, as_of: str):
    d = inv.copy()
    d["Due date"] = pd.to_datetime(d["Due"])
    d["Pay note"] = d.apply(lambda r: ("MSME 45-day rule" if r["MSME"] == "Yes" else "")
                            + (f" · 2% discount Rs {r['Discount']:,.0f}" if r["Discount"] else ""), axis=1)
    pts = alt.Chart(d).mark_circle(opacity=0.9, stroke="#fff", strokeWidth=2).encode(
        x=alt.X("Due date:T", title="Due date", axis=alt.Axis(format="%d %b", tickCount=8)),
        y=alt.Y("Total:Q", title="Invoice value", axis=alt.Axis(labelExpr=LAKH)),
        size=alt.Size("Total:Q", scale=alt.Scale(range=[140, 1100]), legend=None),
        color=alt.Color("Readiness:N", scale=READY_COLORS, legend=alt.Legend(title=None)),
        tooltip=["Invoice:N", "Vendor:N", "Amount:N", "Readiness:N", "Check:N",
                 alt.Tooltip("Due date:T", format="%d %b %Y"), "Days to due:Q", "Pay note:N"])
    names = alt.Chart(d[d["Total"] >= 400000]).mark_text(dy=-22, fontSize=11, color=INK_2).encode(
        x="Due date:T", y="Total:Q", text="Invoice:N")
    today = alt.Chart(pd.DataFrame({"d": [pd.to_datetime(as_of)], "t": ["Today"]}))
    rule = today.mark_rule(color=MUTED, strokeDash=[4, 4]).encode(x="d:T")
    rule_lbl = today.mark_text(align="left", dx=4, color=MUTED, fontSize=11).encode(
        x="d:T", y=alt.value(8), text="t:N")
    return style(pts + names + rule + rule_lbl, 320)


def cash_bridge(cash: dict, inv: pd.DataFrame):
    ready = inv.loc[inv["Readiness"] == "Ready to pay", "Total"].sum()
    bal, committed, buffer = cash["bank_balance_inr"], cash["committed_outflows_next_7_days_inr"], cash["min_cash_buffer_inr"]
    headroom = bal - committed - buffer - ready
    steps = [("Bank balance", 0, bal, "Balance"), ("Committed 7d", bal - committed, bal, "Outflow"),
             ("Cash buffer", bal - committed - buffer, bal - committed, "Outflow"),
             ("Ready invoices", headroom, bal - committed - buffer, "Outflow"),
             ("Headroom", 0, headroom, "Balance")]
    d = pd.DataFrame(steps, columns=["Step", "Low", "High", "Kind"])
    d["Amount"] = d["High"] - d["Low"]
    d["Label"] = d.apply(lambda r: ("-" if r["Kind"] == "Outflow" else "") + short_inr(r["Amount"]), axis=1)
    order = list(d["Step"])
    bars = alt.Chart(d).mark_bar(cornerRadius=4, size=52).encode(
        x=alt.X("Step:N", sort=order, title=None, axis=alt.Axis(labelAngle=0, labelLimit=130, labelOverlap=False)),
        y=alt.Y("Low:Q", title=None, axis=alt.Axis(labelExpr=LAKH)), y2="High:Q",
        color=alt.Color("Kind:N", scale=alt.Scale(domain=["Balance", "Outflow"], range=[BLUE, ORANGE]),
                        legend=alt.Legend(title=None)),
        tooltip=["Step:N", alt.Tooltip("Label:N", title="Amount")])
    text = alt.Chart(d).mark_text(dy=-8, fontSize=12, color=INK).encode(
        x=alt.X("Step:N", sort=order), y="High:Q", text="Label:N")
    return style(bars + text, 320)


def vendor_risk(vend: pd.DataFrame, policy: dict):
    d = vend.copy()
    bars = alt.Chart(d).mark_bar(cornerRadiusEnd=4, height=24).encode(
        y=alt.Y("Vendor:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200, labelOverlap=False)),
        x=alt.X("Risk score:Q", scale=alt.Scale(domain=[0, 100]), title="Risk score (0-100)"),
        color=alt.Color("Band:N", scale=BAND_COLORS, legend=alt.Legend(title=None)),
        tooltip=["Vendor:N", "Risk score:Q", "Band:N", "GSTIN:N", "MSME:N", "Terms:N"])
    text = bars.mark_text(align="left", dx=6, fontSize=12).encode(text="Risk score:Q", color=alt.value(INK))
    lines = pd.DataFrame({"x": [policy["RISK_LOW_MAX"], policy["RISK_MEDIUM_MAX"]], "t": ["Medium from 26", "High above 50"]})
    rules = alt.Chart(lines).mark_rule(color=MUTED, strokeDash=[4, 4]).encode(x="x:Q")
    return style(bars + text + rules, 260)


def factor_heatmap(factors: pd.DataFrame):
    d = factors.copy()
    short = {"Recent bank-account change": "Bank change", "GSTIN not active": "GSTIN inactive",
             "GST return filing default": "GST filing", "Poor on-time delivery": "Late delivery",
             "High quality rejection": "Quality", "Spend concentration": "Spend share"}
    d["Factor"] = d["Factor"].map(lambda f: short.get(f, f))
    d["Cell"] = d["Points"].map(lambda p: f"+{p}" if p else "·")
    base = alt.Chart(d).encode(
        x=alt.X("Factor:N", title=None, sort=list(short.values()),
                axis=alt.Axis(labelAngle=0, labelLimit=120, labelOverlap=False, orient="top", domain=False,
                              ticks=False, labelExpr="split(datum.label, ' ')", labelLineHeight=13)),
        y=alt.Y("Vendor:N", title=None, axis=alt.Axis(labelLimit=200, labelOverlap=False)))
    cells = base.mark_rect(cornerRadius=6, stroke="#fff", strokeWidth=3).encode(
        color=alt.Color("Points:Q", scale=alt.Scale(domain=[0, 35], range=["#f0efec", CRITICAL]),
                        legend=alt.Legend(title="Risk points", orient="bottom", gradientLength=160)),
        tooltip=["Vendor:N", "Factor:N", "Triggered:N", "Points:Q", "Evidence:N"])
    text = base.mark_text(fontSize=12, fontWeight="bold").encode(
        text="Cell:N", color=alt.condition("datum.Points >= 20", alt.value("#fff"), alt.value(INK_2)))
    return style(cells + text, 280)


def price_variance(inv: pd.DataFrame, policy: dict):
    d = inv.dropna(subset=["Price variance %"]).copy()
    tol = policy["PRICE_TOLERANCE_PCT"]
    d["Result"] = d["Price variance %"].map(lambda v: "Above tolerance" if v > tol else "Within tolerance")
    bars = alt.Chart(d).mark_bar(cornerRadiusEnd=4, size=28).encode(
        x=alt.X("Invoice:N", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("Price variance %:Q", title="Invoice price vs PO (%)"),
        color=alt.Color("Result:N", scale=alt.Scale(domain=["Within tolerance", "Above tolerance"],
                                                     range=[GOOD, CRITICAL]), legend=alt.Legend(title=None)),
        tooltip=["Invoice:N", "Vendor:N", alt.Tooltip("Price variance %:Q", format="+.2f"), "Result:N"])
    text = bars.mark_text(dy=-8, fontSize=12).encode(text=alt.Text("Price variance %:Q", format="+.1f"),
                                                     color=alt.value(INK))
    rule = alt.Chart(pd.DataFrame({"y": [tol], "t": [f"Tolerance {tol}%"]}))
    line = rule.mark_rule(color=CRITICAL, strokeDash=[5, 4], strokeWidth=1.5).encode(y="y:Q")
    lbl = rule.mark_text(align="right", x="width", dx=-2, dy=-7, color=CRITICAL, fontSize=11).encode(y="y:Q", text="t:N")
    return style(bars + text + line + lbl, 280)


def supplier_quadrant(vend: pd.DataFrame, policy: dict):
    d = vend.copy()
    d["Short"] = d["Vendor"].str.split().str[0]
    X = lambda: alt.X("x:Q", scale=alt.Scale(domain=[70, 100], zero=False), title="On-time delivery (%)")
    Y = lambda: alt.Y("y:Q", scale=alt.Scale(domain=[-0.6, 8.4], zero=False), title="Quality rejection (%)")
    d["x"], d["y"] = d["On-time delivery %"], d["Rejection %"]
    pts = alt.Chart(d).mark_circle(opacity=0.9, stroke="#fff", strokeWidth=2).encode(
        x=X(), y=Y(),
        size=alt.Size("Open value:Q", scale=alt.Scale(range=[160, 1400]), legend=None),
        color=alt.Color("Band:N", scale=BAND_COLORS, legend=alt.Legend(title="Risk band")),
        tooltip=["Vendor:N", "On-time delivery %:Q", "Rejection %:Q", "Band:N",
                 alt.Tooltip("Open value:Q", format=",.0f", title="Open invoices (Rs)")])
    # label placement by hand so names in the cluster don't collide: (dx, dy, align)
    spots = {"Apex": (14, 0, "left"), "QuickTech": (-16, -4, "right"), "Shree": (12, -8, "left"),
             "Greenleaf": (14, 10, "left"), "Nova": (-14, 0, "right")}
    names = alt.layer(*[
        alt.Chart(d[d["Short"] == k]).mark_text(dx=dx, dy=dy, align=al, fontSize=11, color=INK_2).encode(
            x=X(), y=Y(), text="Short:N")
        for k, (dx, dy, al) in spots.items()])
    zone = alt.Chart(pd.DataFrame({"x": [policy["MIN_ON_TIME_DELIVERY_PCT"]]})).mark_rule(
        color=MUTED, strokeDash=[4, 4]).encode(x=X())
    zone2 = alt.Chart(pd.DataFrame({"y": [policy["MAX_REJECTION_RATE_PCT"]]})).mark_rule(
        color=MUTED, strokeDash=[4, 4]).encode(y=Y())
    good = alt.Chart(pd.DataFrame({"x": [99.5], "y": [8.0], "t": ["Fast but poor quality"]})).mark_text(
        align="right", color=WARNING, fontSize=11, fontWeight="bold").encode(x=X(), y=Y(), text="t:N")
    best = alt.Chart(pd.DataFrame({"x": [99.5], "y": [3.6], "t": ["Best suppliers"]})).mark_text(
        align="right", color=GOOD, fontSize=11, fontWeight="bold").encode(x=X(), y=Y(), text="t:N")
    bad = alt.Chart(pd.DataFrame({"x": [70.5], "y": [8.0], "t": ["Late and poor quality"]})).mark_text(
        align="left", color=CRITICAL, fontSize=11, fontWeight="bold").encode(x=X(), y=Y(), text="t:N")
    return style(zone + zone2 + pts + names + good + best + bad, 300)


# ---------------------------------------------------------------- page
def render() -> None:
    data = build_data()
    inv, leak, vend, cash, policy = data["inv"], data["leak"], data["vend"], data["cash"], data["policy"]
    st.markdown(CSS, unsafe_allow_html=True)

    open_inv = inv[inv["Readiness"] != "Paid"]
    ready = inv[inv["Readiness"] == "Ready to pay"]
    blocked = inv[inv["Readiness"] == "Blocked"]
    discounts = inv.loc[inv["Readiness"] == "Ready to pay", "Discount"].sum()
    high_vendors = int((vend["Band"] == "HIGH").sum())
    msme_due = inv[(inv["MSME"] == "Yes") & (inv["Readiness"] != "Paid") & (inv["Days to due"] <= 7)]

    st.markdown(
        f'<div class="pg-hero"><h2>Accounts Payable control tower</h2>'
        f'<p>Live three-way match, duplicate, vendor-risk and payment checks on every invoice · '
        f'business date {datetime.strptime(str(policy["AS_OF_DATE"]), "%Y-%m-%d"):%d %b %Y}</p>'
        f'<span class="pill">🧾 {len(inv)} invoices</span><span class="pill">🏭 {len(vend)} vendors</span>'
        f'<span class="pill">🛡️ {short_inr(leak["Amount"].sum())} leakage caught</span>'
        f'<span class="pill">⏰ {len(msme_due)} MSME payment(s) due within 7 days</span></div>',
        unsafe_allow_html=True)

    c = st.columns(6)
    card(c[0], "Open payables", short_inr(open_inv["Total"].sum()), f"{len(open_inv)} invoices", BLUE)
    card(c[1], "Ready to pay", short_inr(ready["Total"].sum()), f"{len(ready)} invoices, all checks passed", GOOD)
    card(c[2], "Blocked", short_inr(blocked["Total"].sum()), f"{len(blocked)} invoices need action", CRITICAL)
    card(c[3], "Leakage caught", short_inr(leak["Amount"].sum()), "duplicates, over-billing, no PO", ORANGE)
    card(c[4], "Discounts to capture", short_inr(discounts), "early-payment terms", AQUA)
    card(c[5], "Payable today", short_inr(cash["payable_capacity_today_inr"]),
         f"keeps {short_inr(cash['min_cash_buffer_inr'])} buffer", VIOLET)
    st.write("")

    left, right = st.columns(2, gap="large")
    with left:
        section("Where the money in the AP queue stands", "Invoice value by the result of the automated checks.")
        st.altair_chart(donut(inv), width="stretch")
    with right:
        section("Leakage the controls caught", f"Rs {leak['Amount'].sum():,.0f} that would have been paid "
                "without the agents - hover a bar for the invoices.")
        st.altair_chart(leakage_bars(leak), width="stretch")
        st.markdown(f'<p class="pg-cap">Vendor risk: <b style="color:{CRITICAL}">{high_vendors} HIGH</b> · '
                    f'MSME invoices due within 7 days: <b>{", ".join(msme_due["Invoice"]) or "none"}</b></p>',
                    unsafe_allow_html=True)

    left, right = st.columns(2, gap="large")
    with left:
        section("Payment calendar", "Each bubble is an invoice: position = due date, size = value, "
                "colour = whether it can be paid.")
        st.altair_chart(payment_calendar(inv, str(policy["AS_OF_DATE"])), width="stretch")
    with right:
        section("Cash bridge for this week", "From the bank balance to headroom after paying every ready invoice.")
        st.altair_chart(cash_bridge(cash, inv), width="stretch")

    left, right = st.columns(2, gap="large")
    with left:
        section("Vendor risk scores", "Policy formula; dashed lines mark the MEDIUM and HIGH thresholds.")
        st.altair_chart(vendor_risk(vend, policy), width="stretch")
    with right:
        section("What drives each vendor's risk", "Points per risk factor; darker red = bigger contributor.")
        st.altair_chart(factor_heatmap(data["factors"]), width="stretch")

    left, right = st.columns(2, gap="large")
    with left:
        section("Price check against the purchase order", "Invoice unit price vs agreed PO price, "
                "with the policy tolerance line.")
        st.altair_chart(price_variance(inv, policy), width="stretch")
    with right:
        section("Supplier performance", "Delivery vs quality; bubble size = open invoice value.")
        st.altair_chart(supplier_quadrant(vend, policy), width="stretch")

    section("Invoice queue", "Every invoice with its live check results.")
    table = inv[["Invoice", "Vendor", "PO", "Amount", "Check", "Readiness", "Due", "Days to due",
                 "Risk band", "Risk score"]]

    def tint(col):
        palette = {"Ready to pay": "#e3f5e3", "Blocked": "#fbe4e4", "Paid": "#e2edfb",
                   "LOW": "#e3f5e3", "MEDIUM": "#fff3d6", "HIGH": "#fbe4e4"}
        return [f"background-color: {palette.get(v, '')}" for v in col]

    st.dataframe(table.style.apply(tint, subset=["Readiness", "Risk band"]), hide_index=True,
                 column_config={"Risk score": st.column_config.ProgressColumn(
                     "Risk score", min_value=0, max_value=100, format="%d", color="red")})
