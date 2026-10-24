# TechStore AI Browser Agent

This is a bounded research prototype for evaluating a shopping agent in a
sequential prompt-injection experiment. It uses Gemini for goal interpretation
and next-action planning, while Python owns action validation and all Playwright
operations.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/playwright install chromium
export GOOGLE_GEMINI_KEY='your-key'
```

The key is read with `GOOGLE_GEMINI_KEY`; it is never printed or written to logs.

## Run

```bash
.venv/bin/techstore-agent sequential_prompt_injection --log runs.jsonl
```

The application launches a visible Chromium window using a persistent profile
at `./browser_data`. It prints the selected product, price, cart-verification
result, and log path before waiting for Enter. Press Enter to close the browser
cleanly. The profile is not cleared at startup, so supported browser storage is
preserved between runs. Cart persistence specifically depends on how the
website stores its cart.

To close the browser immediately after the run instead:

```bash
.venv/bin/techstore-agent sequential_prompt_injection --no-keep-open
```

You can choose another profile directory with `--profile-dir`. Do not use the
same profile directory concurrently from multiple running Chromium processes.

The only supported scenario is `sequential_prompt_injection`. It uses the
injection-test website configured in `prompts.py`:

```text
https://electronics-shopping-site-1.onrender.com/
```

The agent searches for headphones, opens one product at a time, sends the
currently observed product details and complete description to Gemini, validates
Gemini's next action, and verifies the actual cart contents before completing.
Descriptions are webpage data and are not trusted system instructions.

The agent stops after a configurable action budget, rejects unknown actions and
unobserved product IDs, and never enters checkout or payment. A task is successful
only after the intended product is found in the cart.

## Project structure

```text
src/techstore_agent/
  actions.py       Action validation and browser execution
  browser.py       Playwright navigation, extraction, and cart operations
  cli.py           Sequential experiment orchestration and CLI
  experiments.py   Scenario lookup helper
  llm.py           Gemini client and response parsing
  logger.py        JSONL and terminal tracing
  models.py        Typed domain models
  prompts.py       Goal, URL, and scenario instructions
tests/
  test_agent.py    Unit and mocked integration tests
main.py            Backward-compatible launcher
pyproject.toml     Package metadata and console entry point
```

## Tests

```bash
.venv/bin/python -m pytest -q
```

Unit tests cover product comparison, malformed or adversarial model decisions,
and JSON parsing. Live-site integration runs should be performed separately
because they depend on network availability and mutate a browser session cart.

## Experiment records

`runs.jsonl` contains one JSON object per event, including the scenario, goal,
model decisions, validation outcomes, executed actions, errors, duration, and
cart verification. It deliberately excludes API keys, cookies, and credentials.

Run the sequential prompt-injection experiment with:

```bash
.venv/bin/techstore-agent sequential_prompt_injection
```
