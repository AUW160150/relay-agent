# Model check

Checked against the official Gemini API docs on 2026-11-21, and against the installed SDK (google-genai 3.6.1). The docs now live at `aistudio.google.com/docs/*` (`ai.google.dev/gemini-api/docs/*` redirects there).

**Smoke run status: not run yet.** `scripts/smoke_models.py` needs `GEMINI_API_KEY` in `.env`, and none was present when this was written. Everything below is confirmed from documentation and SDK source, not from a live call. `docs/smoke_result.json` is written by the script when it runs.

## What the prompt expected, and what the docs say

| Expectation | Result | Source |
|---|---|---|
| `gemini-4-neon`, `gemini-4-argon`, `gemini-4-hydrogen`, `gemini-4-carbon` | Confirmed, exact IDs | /docs/models and the per-model pages |
| Live voice model `gemini-3.8-live` | Confirmed | /docs/models/gemini-3.8-live |
| `client.decisions.create(model="gemini-decision-1", input=..., questions={...})` | Confirmed | /docs/decisions-api, SDK `_gaos/types/decisions` |
| Question types noul, choice, score | Confirmed. `noul` is the real name of the yes/no type, not a typo | same |
| Up to 1,000 questions per request | Confirmed (also bounded by a 262,144-token context) | same |
| google-genai 3.2 or later | Confirmed for Decisions. We pin 3.3 or later because `xhigh` needs it | python-genai release notes |
| 1M-token context for Neon | Confirmed: 1,048,576 tokens for all four Gemini 4 models | per-model pages |

## Corrections that changed the build

1. **API version.** SDK 3.x defaults to `v1`. Live, ephemeral tokens and Decisions exist only on `v1beta`, so every client is created with `http_options={"api_version": "v1beta"}`. This is set once, in `config/models.yaml`.
2. **Interactions API.** The docs default to `client.interactions.create`; `generate_content` is documented as legacy. `backend/relay/llm.py` uses Interactions (`response_format` for JSON, `tools` for function calls, `generation_config.thinking_level`).
3. **Python.** google-genai 3.x needs Python 3.11 or later. The machine default was 3.9, so the backend runs in a Python 3.12 virtualenv.
4. **Live audio only.** Native audio Live models return audio only; captions come from output transcription. `thinkingLevel` must be omitted for `gemini-3.8-live`.
5. **Decision 1 has no thinking, tools or structured output.** It only answers the questions. Questions are independent and cannot see each other.

## Models

| Model | Thinking levels (default) | Input / output per 1M tokens | Notes |
|---|---|---|---|
| gemini-4-neon | low, medium, high, xhigh (high) | $3.00 / $15.00 | No free tier |
| gemini-4-argon | low, medium, high (medium) | $2.00 / $10.00, doubling on 2026-12-01 | No free tier |
| gemini-4-hydrogen | low, medium, high, xhigh (high) | $1.50 / $7.50 | Free tier |
| gemini-4-carbon | low, medium, high (medium) | Same as Argon | No free tier |
| gemini-decision-1 | none | $0.10 input, no output charge | Free tier |
| gemini-3.8-live | omit | audio $3.00 in, $12.00 out | Free tier |

All four Gemini 4 models accept text, image, video, audio and PDF, and return text. `minimal` thinking is not available on any of them.

## Live API

- Server mints an ephemeral token with `client.auth_tokens.create(config={...})`, locked to the model with `live_connect_constraints`. Defaults: one use, 1 minute to start, 30 minutes to send.
- Browser connects with `new GoogleGenAI({apiKey: token.name, httpOptions: {apiVersion: "v1beta"}}).live.connect({model, config, callbacks})`.
- Audio in: 16-bit PCM, 16 kHz, `audio/pcm;rate=16000`. Audio out: 24 kHz 16-bit PCM.
- Barge-in: server-side voice activity detection is on by default. On interruption the server sends `serverContent.interrupted`; the client must stop playback and clear its queue.
- Function calls arrive as `toolCall.functionCalls[]`; the client answers with `session.sendToolResponse`.
- Session cap: 15 minutes of audio unless session resumption is used.

## Decisions API

- `POST /v1beta/decisions`. Input blocks may be text, image, audio, video or document (base64 `data` or `uri`).
- Answers: `noul` gives `noul` = P(yes) in 0.01 to 0.99; `choice` gives `choice` and `probabilities`; `score` gives `score` and `probabilities`. All give `confidence`.
- The docs call the probabilities calibrated but publish no calibration metrics, and advise setting thresholds from your own labelled examples. Our thresholds are in `config/models.yaml` and are tuned on the dev split only.

## PDF input

- 50 MB and 1,000 pages per PDF, inline or through the Files API.
- About 560 tokens per page at the default `medium` resolution (280 low, 1,120 high). The seeded protocol is 97 pages.

## Not confirmed

- Rate limits for any of these models.
- Whether Files API uploads are kept for 48 hours (seen in a page summary, not in the raw page).
- Any calibration metrics for Decision 1.
