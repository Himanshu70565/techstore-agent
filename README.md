# TechStore AI Browser Agent

This is a bounded research prototype for evaluating shopping-agent reliability,
prompt-injection resistance, accessibility, and human oversight. It uses Gemini
for goal interpretation and next-action planning, while Python owns product
comparison, action validation, and all Playwright operations.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
export GOOGLE_GEMINI_KEY='your-key'
```

The key is read with `GOOGLE_GEMINI_KEY`; it is never printed or written to logs.

## Run

```bash
.venv/bin/python main.py normal_execution --log runs.jsonl
```

The application launches a visible Chromium window using a persistent profile
at `./browser_data`. It prints the selected product, price, cart-verification
result, and log path before waiting for Enter. Press Enter to close the browser
cleanly. The profile is not cleared at startup, so supported browser storage is
preserved between runs. Cart persistence specifically depends on how the
website stores its cart.

To close the browser immediately after the run instead:

```bash
.venv/bin/python main.py normal_execution --no-keep-open
```

You can choose another profile directory with `--profile-dir`. Do not use the
same profile directory concurrently from multiple running Chromium processes.

Supported scenarios are `normal_execution` and `prompt_injection`. Both use the
same shopping goal and website URL. The prompt-injection scenario only changes
the instructions given to Gemini: it asks Gemini to read the selected product's
full description. It does not modify the website or insert injection text.

The agent stops after a configurable action budget, rejects unknown actions and
unobserved product IDs, and never enters checkout or payment. A task is successful
only after the intended product is found in the cart.

## Tests

```bash
.venv/bin/pytest -q
```

Unit tests cover product comparison, malformed or adversarial model decisions,
and JSON parsing. Live-site integration runs should be performed separately
because they depend on network availability and mutate a browser session cart.

## Experiment records

`runs.jsonl` contains one JSON object per event, including the scenario, goal,
model decisions, validation outcomes, executed actions, errors, duration, and
cart verification. It deliberately excludes API keys, cookies, and credentials.

Run the prompt-injection scenario with:

```bash
.venv/bin/python main.py prompt_injection
```
