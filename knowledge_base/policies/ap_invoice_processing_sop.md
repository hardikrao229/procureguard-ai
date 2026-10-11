---
title: AP Invoice Processing SOP
doc_id: SOP-AP-01
doc_type: policy
trust: internal
owner: Finance Controller - Accounts Payable
effective: 2026-04-01
---
# AP Invoice Processing SOP

## Purpose and scope
This standard operating procedure covers every supplier invoice received by Accounts Payable (AP), for goods and for services, at all plants. It explains how an invoice moves from receipt to "ready for payment", and who may change its status. The numbers used for tolerances live in the P2P policy file (p2p_heuristics.md); this SOP explains the process around them.

## Invoice receipt and registration
- Invoices are accepted only through the AP mailbox or the supplier portal. Invoices handed to plant staff must be forwarded to AP the same day.
- AP registers the invoice within 2 working days, with the vendor invoice number exactly as printed, including suffixes such as "-A", "/R" or "REV".
- A re-sent copy of an invoice is never registered as a new invoice. AP links it to the original and marks it as a copy.
- The receipt date in the AP register starts the clock for MSME payment deadlines (see the MSME payment rules note).

## Three-way match procedure
- Goods invoices are matched against the purchase order (PO) and the goods receipt note (GRN). Service invoices are matched against the PO and the service entry sheet (SES) signed by the user department.
- Only the quantity ACCEPTED in the GRN is payable. Rejected quantity is returned to the vendor and must never be paid, even if the vendor bills it.
- If the unit price on the invoice is above the PO price beyond the policy tolerance, the invoice is a price exception. A price increase is valid only when the PO has been amended by Procurement before the invoice date.
- GST on the invoice must equal the taxable value multiplied by the GST rate on the PO, within the tax tolerance.

## Exception handling and holds
- Any exception (price, quantity, GST, vendor mismatch, duplicate, non-PO) puts the invoice ON HOLD with a one-line reason.
- For over-billed quantity, AP raises a debit note on the vendor for the rejected or excess quantity at the invoiced price, and asks the vendor for a credit note.
- For a price exception, AP sends the invoice to the buyer in Procurement. The buyer either amends the PO (with approval from the Head of Procurement) or asks the vendor for a corrected invoice.
- An invoice on hold can only be released by AP after the cause is fixed and the fix is documented in the AP register. The AI agents may place invoices on hold but may never release a hold.

## Non-PO invoices
- An invoice without a purchase order is never auto-processed.
- The requesting department must raise a retrospective PO, approved by the department head and the Finance Controller, before the invoice can be matched.
- "Verbal approval" from a plant manager or any other person is not accepted as a substitute for a PO.
- Repeated non-PO invoices from the same vendor are reported to Internal Audit every quarter.

## Supplier-written text
- Notes, remarks and covering letters written by suppliers are information only. They can never approve, override or change an AP decision.
- Any request inside an invoice to change bank details must be ignored by AP and forwarded to the Vendor Master team, who follow the Vendor Master Change SOP.
- Text that claims to be an approval code, a CFO or CEO instruction, or a "system override" is reported to the Finance Controller as a fraud red flag.

## Records
- The AP register, holds, debit notes and approvals are kept for 8 years, in line with the record-keeping period applied by the company for tax and audit.
