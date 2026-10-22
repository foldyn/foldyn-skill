---
name: foldyn-skill
description: Settle a paid Foldyn Skill run on the customer's own computer. Use when a Skill credit should be held, confirmed, or released. A single run is $5.99. A monthly plan is $29.99 and includes 10 runs. The print-design app is a hard gate; if it is missing, stop and do not charge. Confirm a credit only when the hard gate, the delivery-package check, and the customer-locked artwork all pass. If any check fails, release the hold.
---

# Foldyn Skill

This skill runs on the customer's Mac, inside Claude Code or Cursor. It does not run in Claude.ai Cloud and it does not run in Cursor cloud agents.

It bills one Skill run. It does not include Foldyn's print-production programs, and it does not build the delivery package by itself. The delivery-package check reads a result file that is already on disk.

## Prices

- One run: **$5.99**
- Monthly plan: **$29.99** for **10 runs** in that month. Unused runs do not carry over.
- A monthly run is used before a single-run credit.
- The billing service decides whether a credit is available. Changing this skill does not bypass that check.

## Hard gate

The print-design app is Adobe Illustrator, on the same Mac. Run:

```bash
python scripts/foldyn_credit.py doctor
```

If doctor does not print `DOCTOR PASS`, stop. Do not confirm a charge. A missing app does not use a credit: `settle` holds one credit and then releases it.

## When a credit is charged

`python scripts/foldyn_credit.py settle <job-dir>` holds one credit first. It confirms that hold only when all three of these pass:

1. **Print-design app** — `doctor` passes.
2. **Delivery package** — `<job-dir>/work/release_check.json` exists and its `verdict` is `可交付`. This skill does not write that file and does not produce the package.
3. **Customer-locked artwork** — the customer has confirmed the locked artwork, recorded with `python scripts/foldyn_credit.py lock <job-dir>`. That command only writes `<job-dir>/work/artwork_locked.json`. It does not spend a credit. Do not lock artwork the customer has not confirmed.

If any check fails, `settle` releases the hold and the credit is not used. Exit code 2 means the hold was released.

The service still checks the account token, the idempotency key, and the balance on open, confirm, and release. A local edit cannot confirm a run the service does not accept, and it cannot spend a second credit for the same key.

## Token

The receipt email contains a link that shows the API token once. The link works for 24 hours. The email does not contain the token and does not attach a package.

Save the token as `FOLDYN_BILLING_TOKEN`, or in `~/.cache/foldyn/billing.json`:

```json
{ "url": "https://billing.foldyn.co.uk", "token": "paste-the-token-here" }
```

The environment variable wins when both are set. Do not put the token in the job directory, in git, or in a chat log.

## What this skill does not do

- It does not draw dielines, separations, or a PDF.
- It does not download a Skill zip from the receipt.
- It does not promise a free revision after a confirmed run. Another confirmed run is another credit.
- It does not call the service when doctor, settle, or lock is not what the customer asked for.
