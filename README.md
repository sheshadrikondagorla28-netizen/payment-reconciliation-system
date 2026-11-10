# Payment Reconciliation System

A deterministic, synthetic end-to-end payment reconciliation project. It
generates payment orders and attempts, processor events, settlement
batches and lines, bank statement lines, reconciliation matches,
exceptions and resolutions. It stores the workflow in CSV files and
SQLite, validates data integrity, exposes a FastAPI REST API, and
provides an optional Streamlit dashboard.

> **Data safety:** All generated records are synthetic. Do not use real
> cardholder data, bank account details, credentials, or confidential
> payment information in this demonstration.

## 1. Project capabilities

-   Deterministic synthetic data generation using seed `20261022`.
-   Starter and full data-generation profiles.
-   Ten relational entities: `merchants`, `payment_orders`,
    `payment_attempts`, `processor_events`, `settlement_batches`,
    `settlement_lines`, `bank_statement_lines`,
    `reconciliation_matches`, `exceptions`, and `resolutions`.
-   Payment lifecycle and processor-event tracking.
-   Gross, refund, fee, and net settlement calculations.
-   Bank-to-settlement matching using reference, currency, amount
    tolerance, and date window.
-   Explicit exception handling for unmatched records and injected
    anomalies.
-   Separate `ground_truth.csv` for known synthetic anomaly labels.
-   CSV exports, SQLite database, SQL export, data dictionary, source
    manifest, and validation reports.
-   Automated database and API tests.
-   FastAPI endpoints and optional Streamlit analyst dashboard.

## 2. Technology stack

-   Python
-   SQLite
-   pandas
-   FastAPI
-   Uvicorn
-   Streamlit
-   pytest

Python's built-in SQLite support is used; a separate database server is
not required.

## 3. Project structure

The main files and folders are:

``` text
payment_reconciliation_project/
│
├── data/
│   ├── *.csv
│   ├── reconciliation.db
│   ├── ground_truth.csv
│   ├── reconciliation_seed.sql
│   ├── validation_summary.json
│   ├── validation_detailed.json
│   └── source_manifest.json
│
├── tests/
│   ├── test_project.py
│   └── test_acceptance.py
│
├── generate.py
├── validate.py
├── main.py
├── dashboard.py
├── requirements.txt
├── SCHEMA.md
└── README.md
```

The exact generated files in `data/` depend on the generator version and
selected scale.

## 4. Requirements

-   Windows 10/11, macOS, or Linux.
-   Python 3.10 or newer.
-   Internet access for initial package installation.
-   VS Code is recommended but not required.

Check Python:

``` powershell
python --version
```

Run all commands below from the project root---the directory containing
`generate.py`, `validate.py`, and `main.py`.

## 5. Open the project and activate the virtual environment

Windows PowerShell:

``` powershell
cd "$HOME\OneDrive\Desktop\Projects\payment_reconciliation_project"
Get-Location
```

Create a virtual environment if one does not already exist:

``` powershell
py -m venv .venv
```

Activate it:

``` powershell
.\.venv\Scripts\Activate.ps1
```

Your terminal prompt should begin with `(.venv)`.

If activation is blocked, use the environment's Python directly:

``` powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Check that Python points to the project environment:

``` powershell
python -c "import sys; print(sys.executable)"
```

The displayed path should contain `.venv`.

## 6. Install dependencies

``` powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The automated API tests use FastAPI's test client. If your installed
Starlette version explicitly reports that `httpx2` is required, install
it in the same virtual environment:

``` powershell
python -m pip install httpx2
```

Confirm key packages:

``` powershell
python -m pip show fastapi starlette streamlit uvicorn pytest httpx2
```

Do not install packages globally while the project virtual environment
is active.

## 7. Run the project from beginning to end

Follow the steps in order. Run each command from the project root and
check its output before moving to the next step.

### Step 1 --- Check the required source files

``` powershell
Test-Path .\generate.py
Test-Path .\validate.py
Test-Path .\main.py
Test-Path .\dashboard.py
Test-Path .\requirements.txt
Test-Path .\tests\test_project.py
Test-Path .\tests\test_acceptance.py
```

Each command should return `True`. If a file returns `False`, resolve
the missing file before continuing.

### Step 2 --- Generate a starter dataset

Use the starter profile first for a quick end-to-end check:

``` powershell
python generate.py --scale starter --seed 20261022
```

Read the generator summary. It should complete without a traceback and
report that the data and SQLite database were created.

**Important:** Generation rewrites generated outputs in `data/`. Close
CSV files in Excel and stop the API/dashboard before regenerating. Back
up any manually edited data first. If Windows reports `PermissionError`,
check whether a generated file is open or locked by OneDrive.

### Step 3 --- Validate the generated database and records

``` powershell
python validate.py
```

A successful validation should report:

``` json
{
  "passed": true,
  "errors": []
}
```

The detailed report is written to:

``` text
data/validation_detailed.json
```

If validation fails, do not proceed as if the dataset is verified. Read
the listed errors, fix the underlying issue, regenerate if necessary,
and validate again.

### Step 4 --- Run all automated tests

Use this command from the project root:

``` powershell
python -m pytest -v
```

The `python -m pytest` form is recommended because it runs pytest
through the active virtual environment and includes the current project
directory on Python's module search path.

The current test suite in this project has previously collected **62
tests**. When the same two test files and parameterization are present,
the expected successful summary is:

``` text
62 passed
```

The collected count can change if tests are added, removed, or
parameterized differently. The important condition is that the final
summary reports zero failures and zero errors.

You can also run a shorter summary:

``` powershell
python -m pytest -q
```

Do not treat `ERROR` as a passing test. Resolve all failures and errors
before calling the project fully tested.

### Step 5 --- Generate the full requested dataset

After the starter run, validation, and tests succeed, generate the full
profile:

``` powershell
python generate.py --scale full --seed 20261022
```

The full profile is intended to generate these core volumes:

  Entity                   Target records
  ---------------------- ----------------
  Merchants                         2,000
  Payment orders                   50,000
  Payment attempts                 55,000
  Settlement batches                1,000
  Bank statement lines              5,000

Processor events, settlement lines, reconciliation matches, exceptions,
and resolutions are derived from generated source records and scenario
rules. Their exact counts depend on the generator's logic; use the
generator output and validation report as the source of truth.

### Step 6 --- Validate the full dataset

``` powershell
python validate.py
```

Proceed only when the report indicates:

``` json
{
  "passed": true,
  "errors": []
}
```

Review `data/validation_detailed.json`. Check the reported row counts
and validation checks rather than relying only on the word `PASS`.

### Step 7 --- Run the complete automated test suite against the full dataset

``` powershell
python -m pytest -v
```

Confirm the final summary reports zero failed tests and zero errors.
Save the terminal output or a screenshot as test evidence.

## 8. What the validation checks

The validation script checks the generated SQLite data for the rules
implemented in this project, including:

-   Required tables and generated records.
-   Primary-key uniqueness and non-null identifiers.
-   Foreign-key/parent-reference integrity.
-   Payment-order status consistency with attempt history.
-   Settlement amount arithmetic.
-   Settlement batch totals compared with settlement-line totals.
-   Reconciliation reference, currency, amount, and date-window rules.
-   Exception coverage for unmatched settlement batches and bank
    statement lines.
-   Labelling of duplicate processor-event/idempotency-key groups.
-   Presence of required labelled anomaly cases.

The automated tests add checks for database records, anomaly examples,
and API behavior. Test names and assertions in `tests/` define the exact
test coverage.

## 9. Start and test the REST API

Start the API from the project root:

``` powershell
uvicorn main:app
```

Keep this terminal open. Do not run data generation while the API is
using the database.

Open these URLs in a browser:

  URL                            Purpose
  ------------------------------ ---------------------------------------
  http://127.0.0.1:8000/         API root
  http://127.0.0.1:8000/health   Health check
  http://127.0.0.1:8000/docs     Interactive Swagger API documentation

Use a second PowerShell terminal to make basic GET requests:

``` powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/" -Method Get
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get
Invoke-RestMethod -Uri "http://127.0.0.1:8000/metrics" -Method Get
Invoke-RestMethod -Uri "http://127.0.0.1:8000/matches" -Method Get
Invoke-RestMethod -Uri "http://127.0.0.1:8000/exceptions" -Method Get
Invoke-RestMethod -Uri "http://127.0.0.1:8000/settlements" -Method Get
```

A successful request should return a response rather than a connection
error or server traceback.

### API endpoint reference

  --------------------------------------------------------------------------------------
  Method                  Endpoint                               Purpose
  ----------------------- -------------------------------------- -----------------------
  GET                     `/`                                    API root

  GET                     `/health`                              API/database health

  GET                     `/metrics`                             Summary metrics

  GET                     `/matches`                             Reconciliation matches

  GET                     `/exceptions`                          Exception records;
                                                                 supports the filters
                                                                 implemented by the API

  POST                    `/exceptions/{exception_id}/resolve`   Record an exception
                                                                 resolution

  GET                     `/settlements`                         Settlement batches

  GET                     `/settlements/{batch_id}/lines`        Lines belonging to a
                                                                 settlement batch
  --------------------------------------------------------------------------------------

Use `/docs` to inspect exact query parameters, request fields, and
response schemas before sending POST requests.

### Test an exception resolution safely

Use the Swagger page at `http://127.0.0.1:8000/docs`. Find the
`POST /exceptions/{exception_id}/resolve` operation and use an existing
exception ID from the generated data.

Example request body (confirm the API schema in Swagger first):

``` json
{
  "resolved_by": "operations.analyst",
  "resolution_action": "BANK_CONFIRMED",
  "resolution_notes": "Compared the bank advice with the processor settlement report.",
  "outcome": "RESOLVED"
}
```

Use a synthetic test record only. A resolution operation changes the
operational database, so do not perform it if you need to preserve a
pristine generated database. The automated API test for resolution uses
a temporary database copy.

Stop the API with `Ctrl+C` in its terminal.

## 10. Start the Streamlit dashboard

Start the dashboard from another terminal in the project root:

``` powershell
streamlit run dashboard.py
```

Streamlit normally opens the dashboard in your browser. If it does not,
open:

``` text
http://localhost:8501
```

Check that the dashboard loads and that its displayed metrics and
exception information are populated.

Stop the dashboard with `Ctrl+C`.

## 11. Generated output files

The generator creates files under `data/`. Common outputs include:

  -----------------------------------------------------------------------
  Output                              Purpose
  ----------------------------------- -----------------------------------
  Entity CSV files                    Tabular exports for each generated
                                      entity

  `ground_truth.csv`                  Separate labels for intentionally
                                      injected synthetic anomaly cases

  `reconciliation.db`                 SQLite database used by the API and
                                      dashboard

  `reconciliation_seed.sql`           SQL export of generated schema/data

  `validation_summary.json`           Generation summary, counts, and
                                      file checksums

  `validation_detailed.json`          Detailed results from `validate.py`

  `source_manifest.json`              Synthetic-data provenance and
                                      generation metadata
  -----------------------------------------------------------------------

`SCHEMA.md` documents the entity relationships, ID conventions,
lifecycle states, and reconciliation rules. The data dictionary
documents fields and entities.

## 12. Synthetic scenarios represented

The generated dataset includes deliberate examples of:

-   Duplicate settlement line.
-   Bank amount differing from expected net settlement (fee-variance
    case).
-   Refund event timestamp preceding capture.
-   Missing bank record for a settlement batch.
-   Currency mismatch.
-   Duplicate processor event/webhook delivery.
-   Unmatched bank-side activity.

These are controlled synthetic test cases. Their labels are recorded
separately in `ground_truth.csv`; the ground-truth labels should not be
used as input to the reconciliation matching logic.

## 13. Reproducibility

To reproduce the generated logical dataset, use the same scale and seed:

``` powershell
python generate.py --scale full --seed 20261022
```

The seed makes the generator deterministic. Review the SHA-256 values in
`data/validation_summary.json` or the source manifest to compare
generated files across runs.

For a fair comparison: 1. Stop the API and dashboard. 2. Close generated
CSV files in other applications. 3. Generate with the same scale and
seed. 4. Compare the relevant generated CSV checksums. 5. Run
`python validate.py` again.

Operational changes, such as recording a resolution through the
API/dashboard, can change the SQLite database after generation. Compare
pristine generated outputs when checking reproducibility.

## 14. Troubleshooting

### `ModuleNotFoundError: No module named 'main'` during pytest

Run tests this way from the project root:

``` powershell
python -m pytest -v
```

Also verify:

``` powershell
Test-Path .\main.py
python -c "import main; print(main.__file__)"
```

### TestClient reports that `httpx2` is required

Install it into the active environment:

``` powershell
python -m pip install httpx2
```

Then rerun:

``` powershell
python -m pytest -v
```

### `PermissionError` while generating CSV/database files

-   Close the affected CSV in Excel or another editor.
-   Stop Uvicorn and Streamlit before regenerating.
-   Check whether the file is read-only.
-   Allow OneDrive to finish syncing, or temporarily work from a local
    folder outside OneDrive.
-   Retry generation only after the file is no longer locked.

### API cannot connect

Confirm Uvicorn is still running and that the terminal shows it is
listening on port `8000`. Open `http://127.0.0.1:8000/health`.

### Dashboard cannot connect or show data

Confirm the database exists at `data/reconciliation.db`, the full
generation and validation steps completed, and the dashboard is running
from the project root.

## 15. Important limitations

This is a local synthetic demonstration, not a production payment
platform. It does not connect to real processors or banks. The synthetic
fee assumptions and matching tolerances are examples only. A production
system would additionally require authentication, authorization, secure
secrets management, audit controls, concurrency-safe processing,
database migrations, operational monitoring, backups, processor-specific
fee/refund rules, currency minor-unit handling, and formal
reconciliation sign-off.

## 16. Final completion checklist

Before presenting or publishing the project, verify all of the
following:

-   [ ] Dependencies install successfully in `.venv`.
-   [ ] Required source files are present.
-   [ ] Starter data generation completes.
-   [ ] Starter validation passes.
-   [ ] Automated tests pass on starter data.
-   [ ] Full data generation completes with seed `20261022`.
-   [ ] Full validation passes with no errors.
-   [ ] Automated tests pass on full data.
-   [ ] API root, health, metrics, matches, exceptions, and settlements
    endpoints respond.
-   [ ] Streamlit dashboard opens and displays data.
-   [ ] Generated outputs and documentation are present.
-   [ ] README and test evidence are ready for the repository.
