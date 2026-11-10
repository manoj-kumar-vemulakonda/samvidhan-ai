---
id: cheque-bounce-notice
title: Cheque-bounce notice (s.138 NI Act)
icon: 🧾
where: both
documents: optional
output: draft
inputs:
- cheque | Cheque number, date, amount, bank | text
- return_memo | Date the bank's return memo was received, and the reason for return | text
- parties | Payee (your client) and drawer | text
- debt | What the cheque was for (the legally enforceable debt) | textarea
---
## Purpose
Checks the time limits and drafts the statutory demand notice for a dishonoured cheque.

## Steps
1. Check the time limits: whether the cheque was presented within its validity, whether the notice is within the period after the return memo, and the payment period that follows the notice.
2. Confirm the ingredients of the offence: a legally enforceable debt, the reason for dishonour, and who signed. Note whether a company or firm is involved.
3. Draft the demand notice with all the particulars and the demand for payment within the statutory period.
4. Add a timeline: when the notice period ends, when the complaint becomes possible, and the last date for filing it.

## Instructions
- Cite the NI Act sections from the sources (s. 138, 141 and 142).
- Compute the actual dates from the inputs and show the arithmetic.
- The full notice goes under "## Result", with placeholders in capitals for unknown details.
