# Notes and assumptions

Written as the build went. Where the prompt was ambiguous, the assumption is here.

## Status of the model-dependent parts

No `GEMINI_API_KEY` was available on the build machine. Everything that calls a model is written against the official docs and the SDK's own types (see `docs/model_check.md`) but **has not been run against the API**: the smoke test, Neon diff, Argon planning, Carbon audit, Hydrogen navigation, Decision 1 gates and the Live voice session. The first thing to do with a key is `scripts/smoke_models.py`, then a live run of the sponsor flow, and expect to fix things there.

What has been run and verified without a key:
- The rule engine, ledger, signatures, state machine, casebook diffing and transition plan (unit tests).
- The orchestrator through all three roles (integration test with a fake UI).
- The real UI through all three roles in a headless browser, with the agent acting through the DOM action layer.
- The dataset builder against the real ClinicalTrials.gov API.

These runs use **fixture mode**: hand-written stand-ins for the model outputs (`backend/relay/fixtures.py`), adapted from the reference concept demo. Fixture mode exists only so the application can be developed and tested without a key. It is labelled in red in the UI, in the proof rail and in the ledger (`model = "fixture (hand-written, not a model)"`). It is not a replay, it is never used for eval numbers, and it must not be used for the demo.

## Decisions

- **Backend in Python (FastAPI), not Node.** The Decisions API and Live examples in the official docs are Python-first, the eval harness needs Python for PDF parsing and plotting, and one language for orchestrator and eval means the eval calls the same agent functions the app does.
- **Port 8017.** Port 8000 was in use on the build machine.
- **Interactions API, not `generate_content`.** The docs mark `generate_content` as legacy.
- **Only one protocol PDF exists for the seeded study.** Protocol Ver. 1.0 is not public. So for NCT03235752, Neon gets the Ver. 1.1 PDF plus a text description of casebook version 1, and finds changes from the document's own version history and from schedule elements the build does not contain. The two-PDF path (previous and new) is implemented and is what the eval's `two_version` setting uses.
- **Casebook version 1 is ours.** The sponsor's real build is not public. The visit schedule follows Table 1 of the protocol; form items and existing rules are a plausible minimal build.
- **A protocol from a different study** (dropped on the dot) starts from an empty casebook rather than being forced onto the seeded one. The agent says so on the proof rail.
- **Design options.** Argon first proposes two to four options in one call, then writes the full diff and test subjects for every option in parallel, so that "try option B instead" does not wait for a model.
- **One repair round.** If the rule engine rejects Argon's diff or a synthetic subject fails, the engine's error messages go back to Argon once. If it is still invalid the option is dropped; the agent never applies a diff the engine rejected.
- **Carbon never sees Argon's reasoning.** `agents.diff_for_audit` strips `rationale`, `uncertainty` and `basis` from every operation before the audit.
- **Audit findings that need a person** (Decision 1 probability at or above the threshold) are asked one at a time after the option is chosen and before anything is built. "Drop it" removes that operation from the build.
- **Navigator batches.** Hydrogen may return several function calls in one turn (fill a dialog, then save). Decision 1 still verifies after every action, from a screenshot taken after that action; the checks run concurrently and are awaited before the next model turn. A failed verify is fed back to Hydrogen as text.
- **Two layers of checking for each build step.** Decision 1 looks at the screenshot (did the action take effect), and a fixed rule compares the casebook with the planned operation (`casebook.reflects`). The proof rail shows the second, because it is deterministic.
- **"Why?" does not call a model.** The agent reads back the stored rationale (Argon's) or finding (Carbon's). No model is the owner of an "explain" job, and re-generating reasons after the fact would not be the real reason.
- **Typed input** goes through the same Decision 1 gate as speech; only the "addressed to the agent" question is waived, since typing into the agent's box is unambiguous. Clicking a gate button is logged as a decision by the signed-in person.
- **Signatures.** Always both components (user ID and password), every time, not only the first signing of a session. Three failed attempts lock signing for that user until the server restarts. The signature binds to the SHA-256 of the record content; `Verify chain` recomputes both the ledger chain and each signature's binding.
- **Site flow is per site.** After the CRO signs, each deployed site gets its own small state machine (handoff, transition, sign-off).
- **Demo accounts** share one password, printed on the sign-in page and in the README. This is a demo, not an identity system.
- **Dot states.** Exactly four, as specified. After a role has nothing left to do the dot stays on "waiting" with a caption that says nothing is waiting.
- **Browser speech synthesis** is used for the agent's replies only when the Live session is off. The strip at the top says which is in use.

## Eval dataset

- 25 studies, not the 30 to 50 aimed for. The three registry queries (protocol documents labelled "amendment", "summary of changes" or "version") returned 258 candidates; after size and page limits, 15 had a detectable summary of changes and 10 more (capped) post two protocol versions without one. Widening the queries to every study with a protocol document would reach the target but means downloading several GB of PDFs.
- All 25 post at least two protocol versions, so every study can run in the `two_version` setting.
- The manifest was rebuilt once with `--force` after the detector bug (FAILURES F-01), before any model run.
- Gold labels exist only for the 15 studies with a summary of changes, and the parser yields 3 or more changes for 5 of the 8 dev studies.

## Known gaps

- Voice has not been tested at all (needs a key and a microphone). The turn-handling logic in `frontend/src/voice.ts` is the least certain code in the repo.
- Gold labels: the summary-of-changes parser is heuristic. Hand-checking on dev has not been done yet, and the five hand-labelled EDC-impact studies do not exist yet (`eval/edc_labels.json` is absent). EDC-impact accuracy is therefore not reported.
- Replays: the recorder and replay path exist (`POST /api/replay`), but `replays/` is empty because no live session has been recorded.
- The ledger is a file per server run under `data/`. State other than the ledger is in memory and resets on restart.
- Removing things (unassigning a form, deleting an event) is not supported by the diff operations; amendments here only add or modify.
