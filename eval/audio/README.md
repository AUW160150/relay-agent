# Voice recordings for the robustness eval

Nothing in this folder is generated. The recordings have to be made by real people at the venue.

## What to record

At least 3 different speakers. For each speaker, one folder under `raw/`, with 16 kHz mono 16-bit WAV files named `<label>__<n>.wav`:

| Label | Say | How many |
|---|---|---|
| `open_amendment` | "Open the new amendment", "Open it" | 2 |
| `choose_option` | "Go with option B", "Try option A instead" | 2 |
| `approve` | "Approve", "Yes, go ahead" | 2 |
| `explain` | "Why?" | 1 |
| `show_source` | "Show me the source" | 1 |
| `stop` | "Stop" | 1 |
| `none` | Things not meant for the agent: "Did you get lunch yet?", "I'll call you back after the demo", "Option B was the cheaper flight, right?", a sentence read from a news site | 4 |

Example: `raw/speaker1/choose_option__1.wav`, `raw/speaker1/none__3.wav`.

The `none` clips matter most: include some that use task words ("option B", "approve") in a sentence addressed to someone else.

Record with: `ffmpeg -f avfoundation -i ":0" -ar 16000 -ac 1 -t 4 raw/speaker1/approve__1.wav`

## Noise

Put two files in `noise/`, each at least 30 seconds, 16 kHz mono:

- `cafe.wav`: room noise recorded at the venue.
- `talker.wav`: a second person talking continuously (reading an article).

## Run

    backend/.venv/bin/python eval/voice_eval.py --snr 20,10,5,0,-5

Raw recordings are not committed (`eval/audio/raw/` is in `.gitignore`) because they are people's voices. Commit them only with the speakers' consent.
