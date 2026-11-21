Dataset: 25 studies from ClinicalTrials.gov (17 test, 8 dev; 15 with a sponsor summary of changes, 25 with two posted protocol versions). Frozen 2026-11-21.

**Two versions, summary of changes removed (the model must find the changes)**, test split

| Arm | Studies | Change recall | Precision | Quote on cited page | Invented changes per study | Cost per amendment | Latency |
|---|---|---|---|---|---|---|---|
| RELAY (Neon + citation check) | not run | | | | | | |
| B1: one Neon call | not run | | | | | | |
| B2: one Hydrogen call | not run | | | | | | |

_Not run yet: these need `GEMINI_API_KEY`._

**Single PDF, summary of changes visible**, test split

| Arm | Studies | Change recall | Precision | Quote on cited page | Invented changes per study | Cost per amendment | Latency |
|---|---|---|---|---|---|---|---|
| RELAY (Neon + citation check) | not run | | | | | | |
| B1: one Neon call | not run | | | | | | |
| B2: one Hydrogen call | not run | | | | | | |

_Not run yet: these need `GEMINI_API_KEY`._

**Auditor catch rate** (errors planted in Argon's diff; Carbon is not told where)

_Not run yet._

**Navigator reliability** (full sponsor flow in the real UI)

| Mode | Runs | Completed end to end | Steps | Step success (verifier) | Goal success | Recoveries | Median time |
|---|---|---|---|---|---|---|---|
| Live: Hydrogen picks the actions | not run | | | | | | |
| Fixture: hand-written actions (tests the UI and action layer only) | 20 | 20 (100%) | 1808 | n/a | 100% | 0 | 16.60 s |

**Voice robustness**

_Not run yet: needs recordings from at least three speakers (see `eval/audio/README.md`) and `GEMINI_API_KEY`._
