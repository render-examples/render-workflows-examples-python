# File Processing and Analysis Workflow Example

Process and analyze multiple file formats in parallel using Render Workflows, with automated report generation.

## Use Case

Batch process files from various sources (storage, uploads, APIs) and generate consolidated analytics. Common scenarios:
- Document processing pipelines
- Data ingestion from multiple formats
- Automated report generation
- File format conversion
- Content analysis and extraction
- Batch data validation

## Features

- **Multi-Format Support**: Process CSV, JSON, and text files
- **Parallel Processing**: Process multiple files concurrently with asyncio.gather()
- **Automatic Analysis**: Extract insights from each file type
- **Report Generation**: Create consolidated reports from batch results
- **Error Handling**: Graceful handling of missing or corrupt files
- **Extensible**: Easy to add support for new file formats

## Workflow Structure

```
process_file_batch (orchestrator)
  └── process_single_file (for each file, in parallel)
      ├── read_csv_file → analyze_csv_data
      ├── read_json_file → analyze_json_structure
      └── read_text_file → analyze_text_content

generate_consolidated_report (final aggregation)
```

## Local Development

### Prerequisites
- Python 3.10+

### Setup and Run

```bash
# Navigate to example directory
cd file-processing

# Install dependencies
pip install -r requirements.txt

# Run the workflow service
python main.py
```

## Deploying to Render

### Service Configuration

**Service Type**: Workflow

**Build Command**:
```bash
cd file-processing && pip install -r requirements.txt
```

**Start Command**:
```bash
cd file-processing && python main.py
```

### Environment Variables

Required:
- `RENDER_API_KEY` - Your Render API key (from Render dashboard)

### Deployment Steps

1. **Create Workflow Service**
   - Go to Render Dashboard
   - Click "New +" → "Workflow"
   - Connect your repository
   - Name: `file-processing-workflows`

2. **Configure Build Settings**
   - Build Command: `cd file-processing && pip install -r requirements.txt`
   - Start Command: `cd file-processing && python main.py`

3. **Set Environment Variables**
   - Add `RENDER_API_KEY` in the Environment section
   - Get API key from: Render Dashboard → Account Settings → API Keys

4. **Deploy**
   - Click "Create Workflow"
   - Render will build and start your workflow service

## Testing in Render Dashboard

Once deployed, you can test file processing directly in the Render Dashboard:

### How to Test

1. Go to your Workflow service in Render Dashboard
2. Click the **"Manual Run"** or **"Start Task"** button
3. Select the task you want to test
4. Enter the task input as JSON in the text area
5. Click **"Start task"**

### Example Task Inputs

**Important:** The file processing workflow expects an array of file paths, not a JSON object with a `file_paths` key.

**Recommended Starting Point:** Start with `process_file_batch` - this is the main orchestrator that processes multiple files in parallel and shows the complete workflow.

---

**Test batch file processing:**

Task: `process_file_batch`

Input:
```json
[
  "sample_files/sales_data.csv",
  "sample_files/config.json",
  "sample_files/report.txt"
]
```

This will process all three sample files in parallel and return analysis for each.

---

**Test single file processing:**

Task: `process_single_file`

Input (CSV file):
```json
{
  "file_path": "sample_files/sales_data.csv"
}
```

Input (JSON file):
```json
{
  "file_path": "sample_files/config.json"
}
```

Input (Text file):
```json
{
  "file_path": "sample_files/report.txt"
}
```

---

**Test consolidated report generation:**

First run `process_file_batch`, then use its result as input:

Task: `generate_consolidated_report`

Input:
```json
{
  "batch_result": {
    "results": [...],
    "successful": 3,
    "failed": 0,
    "total_files": 3
  }
}
```

Note: You'll need to copy the actual result from `process_file_batch` to test this task.

**Tip:** The Dashboard will show you processing progress for parallel operations, demonstrating how multiple files are analyzed concurrently.

## Triggering via SDK

Once deployed, trigger file processing via the Render API or SDK:

```python
from render_sdk import Render

# Uses RENDER_API_KEY environment variable automatically
render = Render()

# Process a batch of files
task_run = await render.workflows.run_task(
    "file-processing-workflows/process_file_batch",
    [
        "sample_files/sales_data.csv",
        "sample_files/config.json",
        "sample_files/report.txt"
    ]
)
print(f"Processed {task_run.results['successful']}/{task_run.results['total_files']} files")

# Generate consolidated report
report_run = await render.workflows.run_task(
    "file-processing-workflows/generate_consolidated_report",
    {"batch_result": task_run.results}
)
print(f"Report: {report_run.results['summary']}")
```

## Sample Files

The example includes sample files in `sample_files/`:

**sales_data.csv**: Sales transaction data
- 8 rows of sales data
- Columns: date, product, quantity, price, region

**config.json**: Configuration file
- Nested JSON structure
- Product catalog and settings

**report.txt**: Text report
- Multi-section text document
- Sales analysis narrative

## Task Descriptions

### File Reading Tasks

**`read_csv_file`**: Reads CSV and returns rows as dictionaries with column metadata.

**`read_json_file`**: Parses JSON and returns data with structure information.

**`read_text_file`**: Reads text content and provides basic statistics (lines, words, chars).

### Analysis Tasks

**`analyze_csv_data`**: Analyzes CSV for metrics like total revenue, unique products/regions.

**`analyze_json_structure`**: Examines JSON structure, counts keys, detects nesting.

**`analyze_text_content`**: Analyzes text for keywords, sections, and content patterns.

### Orchestration Tasks

**`process_single_file`**: Routes file to appropriate reader and analyzer based on extension.

**`process_file_batch`**: Processes multiple files in parallel using asyncio.gather().

**`generate_consolidated_report`**: Aggregates results from all files into a single report.

## Parallel Processing Pattern

The key to efficient batch processing is using `asyncio.gather()`:

```python
@app.task
async def process_file_batch(file_paths: list[str]) -> dict:
    # Launch all file processing tasks concurrently
    tasks = [process_single_file(fp) for fp in file_paths]
    results = await asyncio.gather(*tasks)

    # Results from all files are ready
    return aggregate_results(results)
```

This processes all files simultaneously rather than sequentially, dramatically reducing total processing time.

## Extending This Example

**Add New File Format**:
```python
@app.task
def read_xml_file(file_path: str) -> dict:
    # Parse XML file
    # Return structured data
    pass

@app.task
def analyze_xml_data(xml_result: dict) -> dict:
    # Analyze XML content
    # Return insights
    pass

# Update process_single_file to handle .xml extension
```

**Add Cloud Storage Integration**:
```python
@app.task
async def download_from_s3(bucket: str, key: str) -> str:
    # Download file from S3
    # Save to temp location
    # Return local path
    pass

@app.task
async def process_s3_batch(bucket: str, keys: list[str]) -> dict:
    # Download files in parallel
    paths = await asyncio.gather(*[download_from_s3(bucket, k) for k in keys])
    # Process files
    return await process_file_batch(paths)
```

**Add Database Export**:
```python
@app.task
async def export_to_database(report: dict) -> dict:
    # Connect to database
    # Insert report data
    # Return confirmation
    pass
```

## Performance Tips

1. **Parallel Processing**: Always use asyncio.gather() for independent operations
2. **Batch Size**: Process files in batches of 10-50 for optimal performance
3. **Memory Management**: For large files, consider streaming or chunked processing
4. **Error Isolation**: One file failure shouldn't stop the entire batch

## Important Notes

- **SDK languages**: Workflows support Python and TypeScript; this repo's examples are Python.
- **No Blueprint Support**: Workflows don't support render.yaml blueprint configuration
- **File Access**: In production, integrate with cloud storage (S3, GCS) or databases
- **Retry Logic**: All read operations include retry configuration for transient failures
- **Local Paths**: Sample uses local paths; adapt for your storage solution
