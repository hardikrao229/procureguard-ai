---
title: Duplicate Payment Prevention and Recovery
doc_id: POL-AP-04
doc_type: policy
trust: internal
owner: Finance Controller - Accounts Payable
effective: 2026-04-01
---
# Duplicate Payment Prevention and Recovery

## How duplicates happen
- The vendor re-sends the same invoice by email and by courier.
- The vendor issues a "revised" copy with a suffix such as -A, /R, REV1 or COPY.
- The same supply is billed against the same PO for the same total under a new invoice number.

## Detection rules
- Same vendor, same PO and same total amount: suspected duplicate.
- Vendor invoice numbers that match after removing suffixes and punctuation: suspected duplicate.
- The EARLIER invoice of a matching pair is treated as the original and stays payable. The LATER one is put on hold.

## What AP does with a suspected duplicate
- Place the later invoice on hold with the reason "suspected duplicate of <original>".
- Write to the vendor asking them to confirm in writing whether the later document is a copy or a separate supply.
- If it is a separate supply, the vendor must show a separate GRN or service entry sheet. Without one it is not payable.

## If a duplicate has already been paid
- Ask the vendor for a refund or set it off against the next payment, with the vendor's written agreement.
- Report every duplicate payment above Rs 50,000 to Internal Audit.
- Duplicate payments are counted as "leakage" in the monthly AP dashboard.
