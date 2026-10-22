# Foldyn Skill

Public install of the Foldyn Skill for Claude Code and Cursor. The source in this folder is what gets published as [foldyn/foldyn-skill](https://github.com/foldyn/foldyn-skill). This repository does not create that public GitHub repo.

The skill bills a run on the customer's Mac. It does not include Foldyn's print-production programs.

## Install

Claude Code:

```bash
mkdir -p ~/.claude/skills && git clone https://github.com/foldyn/foldyn-skill.git ~/.claude/skills/foldyn-skill
```

Cursor:

```bash
mkdir -p ~/.cursor/skills && git clone https://github.com/foldyn/foldyn-skill.git ~/.cursor/skills/foldyn-skill
```

Python 3.11 or newer is enough to run `scripts/foldyn_credit.py`. No extra packages.

## After you pay

The receipt email has two things: a one-time link that shows your API token, and these install commands. It does not attach a Skill package. The link works for 24 hours and shows the token once.

Save the token as `FOLDYN_BILLING_TOKEN`, or in `~/.cache/foldyn/billing.json`:

```json
{ "url": "https://billing.foldyn.co.uk", "token": "paste-the-token-here" }
```

Do not commit that file.

## Billing

- One run is $5.99.
- A monthly plan is $29.99 and includes 10 runs. Monthly runs are used first.
- The print-design app (Adobe Illustrator on the Mac) is a hard gate. If it is missing, the run stops and is not charged.
- A credit is confirmed only when the hard gate, the delivery-package check, and the customer-locked artwork all pass. Otherwise the hold is released.

```bash
python scripts/foldyn_credit.py doctor
python scripts/foldyn_credit.py lock /path/to/job
python scripts/foldyn_credit.py settle /path/to/job
```

`lock` records the customer's confirmation. It does not spend a credit. `settle` holds one credit before the checks.

The service checks the token, the idempotency key, and the balance. Editing this client does not bypass those checks.

## What is not in this folder

No print-production pipeline, no server source, and no secrets.
