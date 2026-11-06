# Lesson 5 practical application: Sales Reporting Agent

Build a reusable reporting agent and run it against different months of sales data. This command-line application accompanies **Lesson 5: Anatomy of an Agent Configuration** and slides **5A–5F**.

## Purpose and learning outcomes

The agent reads monthly revenue and cost through one read-only function and produces a short report containing gross profit, gross margin, and limitations. January's sample values are revenue **100 EUR** and cost **60 EUR**, giving gross profit **40 EUR** and gross margin **40%**.

| Lesson | What students can inspect or change |
| --- | --- |
| 5: Configuration anatomy | `config.json` holds the model, instructions, tool choice, and behavior settings. |
| 5A: Configuration, session, environment | One configuration is reused with different period inputs; each run has its own ID and results. Live sessions use `environment.type = none`. |
| 5B: Model selection | `--model` selects a candidate for live evaluation. Compare accuracy, response time, usage, and tool behavior on identical cases. |
| 5C: Checkable instructions | Instructions define evidence, calculations, missing-data behavior, and three output bullet points. |
| 5D: Tools | `SALES_TOOL` describes the interface; `handle_action()` validates and executes a read-only lookup. |
| 5E: Reasoning and output | Reasoning effort and verbosity are independent configuration fields. |
| 5F: Session creation | Live mode creates an actual Agents API session, processes events, returns function results, and saves the final text. |

There are two modes:

- **Demo (default):** runs offline using deterministic Python calculations and simulated function calls. No API key, dependencies, or network required. IDs begin with `demo_`. Model choice, instructions, reasoning effort, and verbosity are displayed but do **not** affect demo text.
- **Live:** uses the OpenAI Python SDK and **Agents API** (`client.beta.agents.sessions`). The model chooses tool calls and generates the report. The application supplies function results. Live inference uses your API account and requires access to the configured model and API.

## Files

```text
practical_application/
  app.py             # CLI, reusable configuration, tool handler, session flow
  config.json        # Enduring agent behavior
  data/sales.csv     # Fictional classroom data
  requirements.txt   # SDK dependency for live mode
  test_app.py        # Offline calculations, validation, and mocked API flow
  Readme.md
  output/            # Generated run logs (created on execution)
```

## Run immediately, without an API key

Requires Python **3.10 or later**. In a terminal opened at the lesson folder:

```powershell
cd practical_application
python app.py
python app.py --period 2026-02
```

For January, the report is:

```text
- 2026-01: revenue 100.00 EUR; cost 60.00 EUR.
- Gross profit = 100.00 - 60.00 = 40.00 EUR; gross margin = 40.00%.
- Based only on the supplied sample data; records were not changed.
```

Each run also prints its mode, selected model, run-log location, ID, and status. The two months use the same configuration but create independent demo sessions.

Try the edge cases:

```powershell
python app.py --period 2026-03
python app.py --period 2026-04
python app.py --period 2026-12
python app.py --no-tools
```

March has a missing cost, April has zero revenue, and December has no record. With tools disabled, the application has no financial evidence. These examples show why an agent must report missing information and avoid inventing figures.

## Configure and run live mode

Create an isolated Python environment and install the current OpenAI SDK:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade -r requirements.txt
```

Activation is optional; the explicit interpreter paths above also work when PowerShell restricts activation scripts.

Set your **OpenAI application API key** in the current PowerShell process. Replace the placeholder locally:

```powershell
$env:OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
.\.venv\Scripts\python.exe app.py --mode live --period 2026-01
```

For macOS/Linux, use `.venv/bin/python` and `export OPENAI_API_KEY="YOUR_OPENAI_API_KEY"`.

The key needs the project permissions documented for Agents API session reads/writes and model inference (`api.agents.read`, `api.agents.write`, `api.responses.write`). An Inworld TTS key cannot authenticate OpenAI requests. Keys are read from the environment and are never added to `config.json`. This application does not load `.env` files automatically.

The provided configuration uses `gpt-6-astra`, matching the lesson's model example. It is a starting configuration, not a claim that this model is optimal for every reporting task. Choose a model available to your account that supports the Agents API and your selected settings:

```powershell
.\.venv\Scripts\python.exe app.py --mode live --model YOUR_SUPPORTED_MODEL --period 2026-02
```

Edit `config.json` to change lasting behavior. The application accepts `low`, `medium`, or `high` for `reasoning_effort` and `verbosity`; set either to JSON `null` to omit that optional setting. Supported values vary by model. `enable_sales_tool` must be `true` or `false`. `--model` and `--no-tools` override the configuration in memory for one invocation without rewriting the file.

The `--data` option selects a local CSV file for the application handler. The model cannot select an arbitrary file path. Required columns are `period,revenue,cost,currency`, with one row per month. The provided amounts are fictional and use a single currency per row.

## How the application was built

### 1. Keep reusable behavior in one object

`AgentConfig` is a frozen dataclass loaded from `config.json`. Its `api_agent()` method translates application settings into API fields:

```python
agent = {"model": self.model, "instructions": self.instructions}
agent["tools"] = [SALES_TOOL] if self.enable_sales_tool else []
if self.reasoning_effort is not None:
    agent["reasoning"] = {"effort": self.reasoning_effort}
if self.verbosity is not None:
    agent["text"] = {"format": {"type": "text"}, "verbosity": self.verbosity}
```

The current month and environment are supplied separately. Reusing this object demonstrates configuration reuse; this example supplies an inline agent for every live session rather than creating a saved server-side agent resource.

### 2. Write instructions whose results can be checked

The instructions require supplied evidence, define gross profit and margin, explain zero-revenue and missing-data behavior, and require three concise bullet points. Students can assess those requirements against each report. Instructions guide behavior; handler validation separately restricts tool access.

### 3. Define a function interface and implement its handler

The function schema names `get_sales_total` and accepts exactly one argument, `period`. Schema metadata does not execute the lookup. The application dispatches approved requests:

```python
if not config.enable_sales_tool or action["name"] != "get_sales_total":
    raise ValueError("This function is not enabled")
if not isinstance(arguments, dict) or set(arguments) != {"period"}:
    raise ValueError("Expected exactly one argument: period")
output = read_sales(arguments["period"], data_file)
```

`read_sales()` checks the calendar-month format, rejects duplicate matching rows and nonfinite amounts, and returns missing values explicitly. Decimal arithmetic in the offline report avoids binary floating-point artifacts. Live calculations and formatting are model-generated and should be checked against the known answers. No update, refund, or delete tool exists.

### 4. Create a session with its own input

The essential live call is:

```python
client.beta.agents.sessions.create(
    agent=config.api_agent(),
    environment={"type": "none"},
    input=task,
    stream=True,
).with_result_collection()
```

The task identifies the month. `none` means no execution sandbox is attached to the agent. Python still runs the CSV handler in this local application; the CSV file is not mounted into a hosted sandbox. Only the selected lookup result is submitted to the API.

### 5. Return results when the session requests a tool

On a `requires_action` event, the runner reads `session.required_actions`. Each function result carries the pending call's `turn_id` and `call_id`, a success flag, and JSON serialized as a string. The runner submits it through `sessions.events.create()` so the agent can continue. Failed lookups return an error instead. Repeated pending-call notifications are deduplicated within the current process.

### 6. Check the outcome and save work

The runner watches turn-completion, failure, and cancellation events. It only accepts a completed turn with nonempty output. The stream collector provides `session_id` and `output_text`; an ID alone is not the report.

Every invocation writes a JSON log to `output/` with the configuration snapshot, task, environment, events, ID, status, and final text or error. View the newest log in PowerShell:

```powershell
$latestRun = Get-ChildItem output -Filter *.json | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content -LiteralPath $latestRun.FullName
```

Live session history also remains on the service. Local log files contain the supplied task, tool data, and model output. This small example starts a fresh session for each invocation; it does not implement continuation or automatic restart recovery.

## Tests and classroom exercises

Run the offline checks with the standard library:

```powershell
python -m unittest -v
```

The tests check known financial results, missing information, division by zero, rejected tool requests, conflicting data, independent demo IDs, omitted optional settings, and the live runner's event handling using a fake client. They make no network requests. A live model's quality and account availability require a separate live run.

Suggested exercises:

1. Run January and February with the same configuration. Compare their IDs and inputs in the logs.
2. Disable the sales tool. Observe which evidence is no longer available.
3. In live mode, change the instructions to request a different report structure. Compare output consistency.
4. In live mode, vary verbosity while keeping reasoning effort fixed, then vary effort while keeping verbosity fixed.
5. Compare candidate models using the same four sample cases. Record correctness, missing-data behavior, formatting, wall-clock time, and available usage information. Measure API usage separately before making cost comparisons.

## Troubleshooting and scope

| Symptom | Action |
| --- | --- |
| Missing SDK or `beta.agents` namespace | Install/upgrade `openai` using the same interpreter used to run the app. |
| Authentication or permission error | Check the OpenAI key, project permissions, API availability, and selected model access. |
| Unsupported reasoning/text setting | Choose supported values or set the field to `null`. |
| Missing figures | Use a populated period or supply the missing data; the application intentionally avoids filling gaps. |
| Network interruption or timeout | Inspect the saved session ID and events. The remote turn may still exist; this example does not automatically retry session creation or cancel remote work. |
| Demo ignores prompt/model changes | Use live mode to measure model behavior. Demo text is deterministic Python. |

Live network operations have SDK timeouts. The additional local observation deadline is checked between received events, not a hard interrupt for a blocked stream. Tool execution is read-only, so replay has no write side effects. Automatic durable recovery, usage billing reports, multi-currency aggregation, and production authorization are outside this lesson's scope.

## Official references

Implementation checked against official OpenAI documentation on November 6, 2026:

- [Agents API quickstart](https://developers.openai.com/api/docs/guides/agents-api/quickstart)
- [Configuring agents](https://developers.openai.com/api/docs/guides/agents-api/configuration)
- [Function tools](https://developers.openai.com/api/docs/guides/agents-api/tools/functions)
- [Running sessions](https://developers.openai.com/api/docs/guides/agents-api/sessions)

Consult these references when upgrading the SDK or changing model settings.
