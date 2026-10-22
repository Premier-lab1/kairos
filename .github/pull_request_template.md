## What and why

<!-- What does this change, and what problem does it solve? Link the issue if there is one. -->

## Area

- [ ] Backend (`backend/`)
- [ ] Web / dashboard (`web/`)
- [ ] Scoring or outcome rules (`docs/signals.md`, `docs/outcomes.md`)
- [ ] Deploy / CI
- [ ] Docs only

## Checklist

- [ ] `cd backend && python -m pytest -q` passes (needs a local Postgres, see the README)
- [ ] `cd web && npm run lint && npx tsc --noEmit` passes (if `web/` changed)
- [ ] Scoring or outcome changes keep the no-future-leak rule: features at time `t` use only data known at `t`
- [ ] A schema change is a new numbered file in `backend/migrations/`; existing migrations are not edited
- [ ] Docs updated if behaviour, thresholds or schema changed
- [ ] No API keys, tokens, wallet secrets or private data in the diff

## Notes for the reviewer

<!-- Anything that needs a closer look: trade-offs, follow-ups, how you tested it. -->
