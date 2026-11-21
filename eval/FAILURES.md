# Failure log

Each entry: what failed, the class of failure, the fix, and the numbers before and after. Entries are only added for failures that actually happened.

## F-01 Dataset builder found no summary-of-changes sections

- **Class:** dataset construction (no model involved).
- **What happened:** the first version of `eval/fetch_ctgov.py` only looked for a "Summary of Changes" heading in the first 80 pages and in the first 1,500 characters of a page. It kept 45 studies and reported 0 with a summary. A scan of every page showed 9 of the 45 had one, usually as an appendix near the end.
- **Fix:** scan every page; match the heading at the start of a line; skip table-of-contents pages; prefer studies with a summary (those are the only ones with gold labels) and cap studies without one at 10; draw the dev split only from studies with a summary.
- **Before / after:** studies with gold labels 0 of 45, then see `eval/manifest.json` counts.
- **Note on the freeze:** the manifest was rebuilt with `--force` once because of this. No model had been run on any study at that point and no prompt or threshold had been tuned, so the test split was not used for tuning.

## F-02 Shared test double was mutated between runs

- **Class:** orchestrator bug, found by `tests/test_flow.py`.
- **What happened:** dropping an operation at the audit question removed it from the module-level fixture, so the next run started with 12 operations instead of 13.
- **Fix:** fixtures are deep-copied when returned (`agents._fx`). In live mode each model response is a fresh object, so this only affected fixture mode.
- **Before / after:** `test_option_b_then_back_to_a` failed (8 PK review events instead of 15), then passed.

## F-03 "Keep it" was not understood at the audit question

- **Class:** intent routing, found by the browser end-to-end run.
- **What happened:** the agent asks "Keep it?" and the answer "Keep it" matched no intent, so the flow stalled.
- **Fix:** the `approve` intent description now includes "says to keep it" and `reject` includes "says to drop or remove it" (these descriptions are the Decision 1 choice criteria). The fixture gate got the same keywords.
- **Before / after:** fixture-mode browser flow stalled at the first question, then completed. Not yet measured with Decision 1 (needs the API key).

## F-04 Navigator harness aborted half its own runs

- **Class:** eval harness bug (fixture mode; no model involved).
- **What happened:** `run_eval.py nav` varies the human's answers by seed. On odd seeds it answered "Drop it" to every audit question, including one where the only alternative to keeping is "Stop". Stopping aborts the build by design, so 10 of 20 runs ended at the design gate and were counted as failures.
- **Fix:** the harness answers "Drop it" only when dropping is what the dialog offers.
- **Before / after:** 10 of 20 completed, then see the navigator table in the README (fixture row).

## F-05 Gold parser split numbered summaries on the wrong lines

- **Class:** gold-label parsing (no model involved), found by reading parser output on the dev split.
- **What happened:** some sponsors write the summary of changes as a numbered list ("1. Throughout the document / Description of Change: ... / Purpose for Change: ..."). The parser started a new item at every section reference, so items were bare headings with no description, or nothing at all.
- **Fix:** when the text contains "Description of Change" three or more times, split on the running item numbers instead and drop the "Purpose for Change" rationale.
- **Before / after (dev):** the count of studies with 3 or more parsed changes stayed at 5 of 8, but NCT02585960 went from 24 items that were bare section headings to 19 items that each carry the description of the change. A first attempt at this fix made things worse (3 of 8) because it also fired on table-style summaries whose column header is "Description of Change"; the trigger now requires the colon form and the list to start at 1.
- **Still unparsed on dev:** NCT01945593 (numbered list, three pages), NCT03692078 (free prose), NCT02055118 (2 items). Studies with fewer than 3 parsed changes are left out of recall and precision and counted in the report.
- **Still open:** the parse has only been spot-checked, not fully hand-checked, on dev.

## F-06 Protocol diff took 385 seconds

- **Class:** latency, first live run.
- **What happened:** Neon at thinking level `high` spent 45,679 thinking tokens on the 97-page protocol: 385 s and $1.09 for one call.
- **Fix:** thinking level `low` for Neon, Argon and Carbon (`config/models.yaml`).
- **Before / after:** 385 s and $1.09, then 60 s and $0.43 for the same protocol. Both runs returned 24 changes; whether `low` loses recall has not been measured on the eval set yet.

## F-07 The plan was too large to review or demo

- **Class:** scope, second live run.
- **What happened:** with only the new protocol PDF and a short description of casebook version 1, Neon reported 15 build-affecting changes and Argon's recommended design had 57 operations (25 new items, 8 new forms). Carbon raised 21 findings on it. The navigator worked through the build correctly but it would have taken many minutes, and the agent would have asked about every routed finding one at a time.
- **Fix:** (1) Neon is told the casebook description is the complete approved build for the previous version, so it stops reporting forms and fields as changes just because the description is brief. (2) Argon is told to keep the diff to 10 to 20 operations, never more than 24, and to add a form or item only for a genuinely new data point. (3) The agent asks about the two most serious routed findings and lists the rest on the Amendment page and in the ledger; none is dropped.
- **Before / after:** 57 operations and 21 findings, then see the next live run.

## F-08 Step verifier rejected correct dropdown selections and saves

- **Class:** verification false negatives, second live run.
- **What happened:** Decision 1 gave P(succeeded) of 0.07 for a correct `select` and 0.26 for a correct save click. The screenshot library draws dropdowns from DOM attributes, so a programmatic selection was drawn with the old value; and the expectation text for a save click was vague.
- **Fix:** mirror live dropdown and checkbox state into attributes before each screenshot; give save, open-dialog and other clicks their own expectation text.
- **Before / after:** not yet re-measured.

## F-09 A correct build was rejected over one rule description

- **Class:** orchestrator, third live run (24 operations, the first that ran to verification).
- **What happened:** Hydrogen built all 24 operations and every per-operation check passed; the 4 synthetic subjects passed on the built casebook. The final build-equals-plan check still failed, and the agent correctly refused to ask for approval. The whole difference was one field: Argon's `modify_rule` changed a rule's description, and the goal text given to the navigator for a modified rule never mentioned the description.
- **Fix:** the goal for a modified rule includes the description; the per-operation check compares it; and when the build differs from the plan the differences are now listed by path instead of only two hashes.
- **Before / after:** build check failed with 1 differing field of the whole casebook, then see the next live run.

## F-10 Cost shown in the UI was about four times too high

- **Class:** accounting bug, third live run.
- **What happened:** the proof rail showed $7.27 for a run whose recorded model calls add up to $1.93. A model call's cost was added once for every proof card it produced (Neon's single call was counted 13 times).
- **Fix:** cost is recorded once, at the model call.
- **Before / after:** $7.27 shown, $1.93 actual.

## F-11 The audit chain broke itself

- **Class:** ledger bug, fourth live run. The most serious failure so far.
- **What happened:** the build equalled the plan (identical content hashes) and 5 of 5 synthetic subjects passed, but the pre-approval chain verification failed, so the agent refused to ask for approval. The ledger had stored a reference to a newly added form; later operations added items to that form, which changed the content of an entry that had already been hashed. The hand-written fixture never adds a form and then items, so no test had caught it.
- **Fix:** the ledger freezes every entry (a deep copy through canonical JSON) at the moment it is written. A unit test reproduces the live failure.
- **Before / after:** chain verification failed on a correct run, then see the next live run.
- **Worth noting:** the check did its job. A mutated ledger entry was caught before a signature could be bound to it.

## Not yet measured

Everything that needs a model call: diff recall and precision, auditor catch rate, live navigator reliability, voice robustness. See the README tables; rows say "not run" until `eval/results/*.json` exists.
