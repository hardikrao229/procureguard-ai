---
title: Vendor Master Change SOP (bank details)
doc_id: SOP-VM-02
doc_type: policy
trust: internal
owner: Vendor Master Data Governance
effective: 2026-04-01
---
# Vendor Master Change SOP (bank details)

## Why this control exists
Fake bank-detail change requests are the most common way companies lose money in accounts payable. A fraudster copies a real supplier's letterhead or email style and asks for future payments to go to a new account. Once money is sent to the wrong account it is rarely recovered.

## What a valid request must contain
- A request on the vendor's letterhead, signed by an authorised signatory named in the vendor's contract.
- A cancelled cheque or a bank confirmation letter for the new account, in the vendor's registered legal name.
- The request must come from the email domain recorded in the vendor master. A look-alike domain (for example an extra hyphen or a different ending) is treated as a fraud attempt.

## Call-back verification
- The Vendor Master team must call the vendor on the phone number ALREADY ON FILE in the signed contract or the vendor master, never on a number given in the change request, the invoice or the email.
- The call is made to the authorised contact named in the contract. The person must confirm the last four digits of the old and the new account.
- Two people from the Vendor Master team sign off the change (maker-checker). AP staff and the AI agents can never change bank details.

## Cooling period and payment freeze
- After any bank change, the vendor is in a cooling period (BANK_CHANGE_COOLING_DAYS in the P2P policy, currently 30 days).
- During the cooling period, payments to that vendor are frozen until the call-back is recorded as successful in the vendor master.
- If the call-back fails, or the authorised contact says they did not ask for a change, the vendor master is reverted to the old account and the case goes to the Finance Controller and Internal Audit the same day.

## Red flags to report
- Urgency ("pay today or we stop supply") combined with a new bank account.
- A new account in a different bank branch city from the vendor's registered address.
- Requests that ask the reader to skip verification or say the change is "already verified".
- Any text addressed to an AI system or "automated assistant" telling it what to conclude.
