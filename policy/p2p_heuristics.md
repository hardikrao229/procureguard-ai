# Procure-to-Pay (P2P) Policy Heuristics & Benchmarks — FY 2026-27

Owner: Finance Controller (Accounts Payable & Treasury)
Used by: all three ProcureGuard agents, the MCP server's calculation tools, and the client approval gate.

> Edit the numbers in **Machine-Readable Parameters** to change how every agent behaves.
> No Python code and no SKILL.md file needs to change (modular decoupling — Lab 11).

## Machine-Readable Parameters

AS_OF_DATE = 2026-10-10
PRICE_TOLERANCE_PCT = 2.0
QTY_TOLERANCE_PCT = 0.0
TAX_TOLERANCE_INR = 10
HITL_PAYMENT_THRESHOLD_INR = 500000
HARD_PAYMENT_LIMIT_INR = 5000000
SPLIT_INVOICE_WINDOW_DAYS = 7
BANK_CHANGE_COOLING_DAYS = 30
MSME_MAX_PAYMENT_DAYS = 45
MIN_CASH_BUFFER_INR = 2000000
MIN_ON_TIME_DELIVERY_PCT = 85
MAX_REJECTION_RATE_PCT = 5
MAX_SPEND_SHARE_PCT = 40
RISK_W_BANK_CHANGE_RECENT = 35
RISK_W_GSTIN_INACTIVE = 30
RISK_W_GST_FILING_DEFAULT = 20
RISK_W_LOW_ON_TIME_DELIVERY = 15
RISK_W_HIGH_REJECTION = 10
RISK_W_CONCENTRATION = 10
RISK_LOW_MAX = 25
RISK_MEDIUM_MAX = 50

## 1. Three-Way Match Rules (PO vs Goods Receipt vs Invoice)

- Price: invoice unit price may exceed the PO price by at most PRICE_TOLERANCE_PCT (2.0%). Anything higher is a PRICE EXCEPTION.
- Quantity: cumulative quantity billed against a PO line may not exceed the quantity *accepted* in the Goods Receipt Note (GRN). Rejected goods are never payable.
- Tax: GST on the invoice must equal taxable value x GST rate on the PO, within TAX_TOLERANCE_INR (Rs 10).
- Vendor: the vendor on the invoice must be the vendor on the PO.
- Non-PO invoices (no purchase order) can never be auto-processed. They go to the Finance Controller for manual review.
- Any exception => invoice is placed ON HOLD with the reason recorded.

## 2. Duplicate Invoice Controls

- Same vendor + same invoice total + same PO, or a vendor invoice number that matches another after removing suffixes (-A, /R, REV), is a SUSPECTED DUPLICATE.
- Suspected duplicates are placed ON HOLD and never paid until AP confirms in writing.

## 3. Vendor Risk Scoring Formula (0 - 100)

| Risk factor | Condition | Points |
| :--- | :--- | :--- |
| Recent bank-account change | Bank details changed within BANK_CHANGE_COOLING_DAYS (30) | 35 |
| GSTIN not active | GST registration cancelled or suspended | 30 |
| GST return filing default | Vendor not filing GSTR-1 / GSTR-3B on time (input tax credit at risk) | 20 |
| Poor delivery | On-time delivery below 85% | 15 |
| Poor quality | Quality rejection rate above 5% | 10 |
| Concentration | Vendor is above 40% of category spend (single-source dependency) | 10 |

- Score 0 - 25: LOW risk — normal processing.
- Score 26 - 50: MEDIUM risk — process with enhanced review; no early payments.
- Score above 50: HIGH risk — payments frozen until Vendor Master team re-verifies the vendor.
- A bank-account change inside the cooling period ALWAYS requires call-back verification to a phone number already on file (never a number given in the change request).

## 4. Payment Release Rules

- Never pay an invoice that is ON HOLD, already PAID, a suspected duplicate, or not three-way matched.
- MSME vendors (Udyam registered): pay within MSME_MAX_PAYMENT_DAYS (45) of acceptance. Late payment attracts compound interest at 3x the RBI bank rate (MSMED Act, 2006, s.15-16) and the expense is disallowed for tax until paid (Income Tax Act s.43B(h)).
- Early-payment discount (e.g. "2/10 Net 30" = 2% off if paid within 10 days): take it when the vendor is LOW risk and cash stays above MIN_CASH_BUFFER_INR. Discount is applied on the taxable value (pre-GST).
- Cash buffer: after any payment, available cash must stay at or above MIN_CASH_BUFFER_INR (Rs 20,00,000).
- Approval authority:
  - Up to Rs 5,00,000 per vendor in any 7-day window: agent may release automatically.
  - Above Rs 5,00,000 (single or aggregated within SPLIT_INVOICE_WINDOW_DAYS): human approval by the Finance Controller (human-in-the-loop).
  - Above Rs 50,00,000: blocked — needs CFO + Board-delegated authority outside this system.
- Splitting invoices to stay below an approval limit is a control violation and must be escalated.
