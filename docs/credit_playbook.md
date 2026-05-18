# Credit Validation Playbook

This checklist must be completed before running any non-trivial batch.

## 1) Verify account-level entitlements
- Confirm Cloudflare startup credit balance, tier, and expiration date in billing.
- Confirm DigitalOcean startup credit balance, monthly ceiling, and expiration date.
- Record proof (screenshot or billing export) in private ops notes.

## 2) Verify product eligibility
- Cloudflare: confirm Workers AI and required data products are credit-eligible for the current tier.
- DigitalOcean: confirm core-credit eligible services and explicitly verify GPU exclusions.
- Track any temporary promotions separately from program credits.

## 3) Update runtime config
- Copy `configs/credits.example.json` to `configs/credits.local.json`.
- Set `validated=true` for each provider only after manual billing verification.
- Fill `eligible_products`, `blocked_products`, and `expires_at` using exact account values.

## 4) Enforce pre-run guardrail
- Pipeline must fail fast if any provider in active routing has:
  - `validated=false`
  - expired credits
  - empty `eligible_products`

## 5) Monthly governance
- Re-validate credits at least once per month.
- Re-validate immediately after provider policy updates.
- Keep a changelog entry when routing policy changes due to credit restrictions.
