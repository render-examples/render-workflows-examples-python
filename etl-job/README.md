# ETL Job Workflow Example

A complete ETL (Extract, Transform, Load) pipeline demonstrating data processing workflows on Render.

## Use Case

Process customer signup data from CSV files with validation, cleaning, and statistical analysis. This pattern is common for:
- Data migration and imports
- Batch data processing
- Data quality monitoring
- Customer onboarding pipelines
- Report generation

## Features

- **Subtask Execution**: Demonstrates calling tasks from other tasks using `await`
- **Extract**: Read data from CSV files (extensible to APIs, databases)
- **Transform**: Validate records with comprehensive error tracking
- **Load**: Compute statistics and prepare aggregated insights
- **Error Handling**: Track valid and invalid records separately
- **Retry Logic**: Automatic retries for transient failures
- **Batch Processing**: Process records individually with status tracking

## Workflow Structure

```
run_etl_pipeline (main orchestrator)
  ├── extract_csv_data (reads CSV file)
  ├── transform_batch (validates all records)
  │   └── validate_record (called for each record)
  └── compute_statistics (aggregates results)
```

## Local Development

### Prerequisites

- Python 3.10+ (for the Python version)
- Node.js 18+ (for the TypeScript version)

### Python

```bash
cd etl-job/python
pip install -r requirements.txt
python main.py
```

### TypeScript

```bash
cd etl-job/typescript
npm install
npm run dev
```

## Deploying to Render

### Service configuration

**Service type**: Workflow

**Python:**

| Setting | Value |
|---|---|
| Build command | `cd etl-job/python && pip install -r requirements.txt` |
| Start command | `cd etl-job/python && python main.py` |

**TypeScript:**

| Setting | Value |
|---|---|
| Build command | `cd etl-job/typescript && npm install && npm run build` |
| Start command | `cd etl-job/typescript && npm start` |

### Environment variables

| Variable | Description |
|---|---|
| `RENDER_API_KEY` | Your Render API key (from Render Dashboard) |

### Deployment steps

1. Go to Render Dashboard.
1. Click **New +** > **Workflow**.
1. Connect your repository.
1. Set the build and start commands for your chosen language.
1. Add `RENDER_API_KEY` in the Environment section.
1. Click **Create Workflow**.

## Testing in Render Dashboard

Once deployed, you can test tasks directly in the Render Dashboard without writing any code:

### How to Test

1. Go to your Workflow service in Render Dashboard
2. Click the **"Manual Run"** or **"Start Task"** button
3. Select the task you want to test
4. Enter the task input as JSON in the text area
5. Click **"Start task"**

### Example Task Inputs

**Important:** The ETL pipeline expects a simple string input (the file path), not a JSON object.

**Recommended Starting Point:** Start with `run_etl_pipeline` - this is the main orchestrator task that demonstrates the complete ETL workflow (extract → transform → load).

---

**Test the main ETL pipeline:**

Task: `run_etl_pipeline`

Input:
```json
"sample_data.csv"
```

**Note:** The Render Dashboard will show you the task execution status, logs, and results in real-time.

## Triggering via SDK

Once deployed, trigger the ETL pipeline via the Render API or SDK:

```python
from render_sdk import Render

# Uses RENDER_API_KEY environment variable automatically
render = Render()

# Run the ETL pipeline
task_run = await render.workflows.run_task(
    "etl-job-workflows/run_etl_pipeline",
    {"source_file": "sample_data.csv"}
)

# Wait for completion
result = await task_run
print(f"Pipeline status: {result.results['status']}")
print(f"Valid records: {result.results['transform']['valid_count']}")
```

## Sample Data

The example includes `sample_data.csv` with test data containing:
- Valid records (proper format)
- Invalid records (missing fields, bad email formats, invalid ages)

This demonstrates how the pipeline handles data quality issues.

## Task Descriptions

### ETL Pipeline Tasks

**`extract_csv_data`**: Reads CSV file and returns records as list of dictionaries. Includes retry logic for file system issues.

**`validate_record`**: Validates a single record:
- Checks required fields (name, email)
- Validates email format
- Validates age range (0-120)
- Returns cleaned data with error tracking

**`transform_batch`**: Processes all records by calling `validate_record` as a subtask for each one:
```python
for record in records:
    # Call validate_record as a subtask
    validated = await validate_record(record)
```
This demonstrates **calling subtasks in a loop** for batch processing.

**`compute_statistics`**: Aggregates valid records to produce:
- Country distribution
- Age statistics (min, max, average)
- Data quality metrics

**`run_etl_pipeline`**: Main orchestrator that calls three subtasks sequentially:
1. `await extract_csv_data(source_file)` - Extract data
2. `await transform_batch(raw_records)` - Validate records (which calls `validate_record` for each)
3. `await compute_statistics(valid_records)` - Generate insights

This demonstrates **sequential subtask orchestration** for multi-stage pipelines.

## Extending This Example

**Add Database Loading**:
```python
@app.task
async def load_to_database(records: list[dict]) -> dict:
    # Connect to database
    # Insert records
    # Return confirmation
    pass
```

**Add API Data Source**:
```python
@app.task
async def extract_from_api(api_url: str) -> list[dict]:
    # Fetch from REST API
    # Parse JSON response
    # Return records
    pass
```

**Add Parallel Processing**:
```python
import asyncio

@app.task
async def transform_batch_parallel(records: list[dict]) -> dict:
    # Validate all records in parallel
    tasks = [validate_record(record) for record in records]
    results = await asyncio.gather(*tasks)
    # Aggregate results
    return results
```

## Important notes

- **Service type**: Deploy as a Workflow service on Render (not Background Worker or Web Service).
