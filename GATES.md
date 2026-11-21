# Gates

One checkbox per build step, with the command that proves it. A box is ticked only if the command was run and passed. Commands run from the repo root; `py` means `backend/.venv/bin/python`.

Last updated 2026-11-21. An API key was added later the same day; gates below are ticked only where a live run has passed.

## 1. Models

- [x] Model IDs, Decisions request shape, Live setup, thinking levels and PDF limits confirmed against the official docs: `docs/model_check.md`
- [x] All model IDs live only in `config/models.yaml`: `grep -rn "gemini-" backend/relay frontend/src eval scripts --include=*.py --include=*.ts --include=*.tsx | grep -v models.yaml` (no matches)
- [x] Requests serialise through the real SDK and responses parse (mocked transport, not the API): `pytest tests/test_llm.py`
- [x] Smoke test passes for all six models, including a Live session through an ephemeral token: `py scripts/smoke_models.py` (results in `docs/smoke_result.json`)

## 2. EDC environment

- [x] Three role workspaces render and work, seeded with NCT03235752, 27 sites and casebook v1: `py eval/run_flow.py --shots docs/shots` (fixture mode)
- [x] Every interactive element has a stable `data-agent-id`; the action layer finds, types, selects, clicks and reports errors: `cd frontend && npm test`

## 3. Diff, plan, rules, audit

- [x] Rule engine: `cd backend && .venv/bin/python -m pytest tests/test_core.py -q`
- [x] Casebook diff applies, validates and compares: same command
- [x] Citation check finds quotes on the real protocol PDF and rejects an invented one: same command plus `backend/relay/pdfutil.py`
- [x] Neon diff on `assets/protocol.pdf`: 24 changes, every quote checked against the PDF (live run, recorded in `replays/`)
- [x] Argon casebook diff applies and its synthetic subjects pass in the engine (live, 3 options)
- [x] Carbon audit and Decision 1 scoring (live, 3 options)

## 4. Ledger and signatures

- [x] Hash chain links, verifies, and detects edits, deletions and re-hashed entries: `pytest tests/test_core.py -k "chain or tamper"`
- [x] Signature needs both components, the signed-in person and the right meaning; binds to the record hash; locks after three failures: `pytest tests/test_core.py -k signature`
- [x] Changing the signed casebook afterwards is detected: `pytest tests/test_flow.py -k three_roles`

## 5. Navigator

- [x] DOM action layer drives the real UI through a full build (124 actions) with hand-written actions: `py eval/run_flow.py --roles sponsor` (fixture mode)
- [x] Hydrogen chooses the actions; Decision 1 verifies each step: 23 of 23 goals, build equals plan (live run recorded as `replays/live5`). 24 of 152 verifier checks said "not confirmed" on actions that had in fact worked; the verifier is too strict and is not yet tuned
- [x] Text box for debugging: the "Say it, or type here" box

## 6. Voice

- [ ] Live session, utterance gate, barge-in — needs key and a microphone; **untested**
- [x] Four dot states implemented (listening, thinking, acting, waiting)

## 7. Role handoffs

- [x] Sponsor signs, CRO deploys and rolls out to the 24 approved sites only, site signs the transition plan: `pytest tests/test_flow.py -q` and `py eval/run_flow.py` (also passed live with real models)
- [x] A role cannot act out of turn: `pytest tests/test_flow.py -k out_of_turn`

## 8. Eval, polish, replay

- [x] Dataset built from ClinicalTrials.gov and frozen: `py eval/fetch_ctgov.py` then `eval/manifest.json` (25 studies, below the 30 to 50 target; see NOTES.md)
- [x] Gold labels parsed from the sponsors' summaries of changes: `py eval/run_eval.py gold --split dev` (5 of 8 dev and 5 of 7 test studies yield 3 or more changes)
- [x] Navigator harness: 20 of 20 runs complete, 1,808 UI actions (fixture mode, so this tests the UI and action layer, not Hydrogen): `py eval/run_eval.py nav --runs 20`
- [ ] Gold labels hand-checked on dev — spot-checked only
- [ ] Five studies hand-labelled for EDC impact (`eval/edc_labels.json`) — not done
- [ ] System against B1 and B2 on the test split: `py eval/run_eval.py diff --split test --arm system|b1|b2` — needs key
- [ ] Auditor catch rate: `py eval/run_eval.py auditor` — needs key
- [ ] Navigator reliability, live, 20 runs: `py eval/run_eval.py nav --runs 20` against a live-mode server — needs key
- [ ] Voice robustness: `py eval/voice_eval.py` — needs key and recordings from three speakers
- [x] A recorded replay of a full live run, all three roles: `RELAY_REPLAY=live5 RELAY_REPLAY_SPEED=8 scripts/dev_server.sh` then `py eval/run_flow.py`
