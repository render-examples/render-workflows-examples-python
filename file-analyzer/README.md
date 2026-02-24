# File Analyzer - Client SDK + Task SDK Example

A comprehensive example demonstrating how to use both the **Client SDK** and **Task SDK** together for file analysis workflows.

## Overview

This example shows the complete workflow of building a file analysis system with two separate services:

1. **Workflow Service** (Task SDK): Defines reusable workflow tasks for analyzing CSV files
2. **API Service** (Client SDK): Provides HTTP endpoints that call workflow tasks

This architecture demonstrates how to separate concerns:
- **Task definitions** live in the workflow service
- **Task invocation** happens through the API service
- The API service acts as a gateway between your users/frontend and the workflow tasks

## Use Case

Build a file analysis API that:
- Accepts CSV file uploads via HTTP
- Processes files using workflow tasks
- Returns statistical insights and trends
- Scales independently (API vs compute)

Common applications:
- Data analysis platforms
- File processing pipelines
- Analytics dashboards
- Batch data processing
- Report generation services

## Architecture

```
┌─────────────────┐
│   User/Client   │
└────────┬────────┘
         │ HTTP POST /analyze
         │ (CSV file upload)
         ▼
┌─────────────────────────────┐
│      API Service            │
│  (Client SDK - FastAPI)     │
│                             │
│  - Receives file uploads    │
│  - Calls workflow tasks     │
│  - Returns results          │
└────────┬────────────────────┘
         │ Client SDK:
         │ client.workflows.run_task(
         │   "file-analyzer-workflows/analyze_file",
         │   [file_content]
         │ )
         ▼
┌─────────────────────────────┐
│   Workflow Service          │
│  (Task SDK - Workflows)     │
│                             │
│  - Defines analysis tasks   │
│  - Processes data           │
│  - Returns results          │
│                             │
│  Tasks:                     │
│  - parse_csv_data           │
│  - calculate_statistics     │
│  - identify_trends          │
│  - generate_insights        │
│  - analyze_file             │
└─────────────────────────────┘
```

## Understanding Workflow Slugs

### What is a Workflow Slug?

A **workflow slug** is the unique identifier for your workflow service on Render. It's used to route task calls to the correct service.

### Format

Task calls use the format: `{service-slug}/{task-name}`

**Example:**
- Service slug: `file-analyzer-workflows`
- Task name: `analyze_file`
- Full identifier: `file-analyzer-workflows/analyze_file`

### Finding Your Service Slug

**Option 1: From Service URL**
- Go to your Workflow service in Render Dashboard
- Look at the service URL: `https://file-analyzer-workflows.onrender.com`
- The slug is: `file-analyzer-workflows`

**Option 2: From Service Name**
- Service names are converted to slugs automatically
- Convert to lowercase and replace spaces with hyphens
- Example: "File Analyzer Workflows" → `file-analyzer-workflows`

**Option 3: From Dashboard**
- Open your service in Render Dashboard
- Look at the service details page
- The slug appears in the service information section

### Configuring the Slug

Set the `WORKFLOW_SERVICE_SLUG` environment variable in your API service:

```bash
WORKFLOW_SERVICE_SLUG=file-analyzer-workflows
```

The API service uses this to construct full task identifiers:

```python
def get_task_identifier(task_name: str) -> str:
    service_slug = os.getenv("WORKFLOW_SERVICE_SLUG")
    return f"{service_slug}/{task_name}"

# Example usage:
task_id = get_task_identifier("analyze_file")
# Result: "file-analyzer-workflows/analyze_file"
```

## Project structure

```
file-analyzer/
├── README.md
├── sample_files/                       # Shared sample data
│   ├── sales_data.csv
│   └── customer_data.csv
├── python/
│   ├── api-service/                    # Client SDK - FastAPI
│   │   ├── main.py
│   │   └── requirements.txt
│   └── workflow-service/               # Task SDK - Workflows
│       ├── main.py
│       └── requirements.txt
└── typescript/
    ├── api-service/                    # Client SDK - Express
    │   ├── src/main.ts
    │   ├── package.json
    │   └── tsconfig.json
    └── workflow-service/               # Task SDK - Workflows
        ├── src/main.ts
        ├── package.json
        └── tsconfig.json
```

## Workflow Service (Task SDK)

### Tasks Defined

**`parse_csv_data(file_content: str) -> dict`**
- Parses CSV content into structured data
- Returns rows, columns, and metadata

**`calculate_statistics(data: dict) -> dict`** (Subtask)
- Calculates statistical metrics for numeric columns
- Returns min, max, avg, sum for each numeric column

**`identify_trends(data: dict) -> dict`** (Subtask)
- Identifies patterns in categorical data
- Returns distribution analysis and top values

**`generate_insights(stats: dict, trends: dict, metadata: dict) -> dict`** (Subtask)
- Generates final insights report
- Combines statistics and trends into actionable findings

**`analyze_file(file_content: str) -> dict`** (Main orchestrator)
- Coordinates the entire analysis pipeline
- Calls parse → calculate → identify → generate as subtasks

### Subtask Pattern

The main `analyze_file` task demonstrates subtask orchestration:

```python
@app.task
async def analyze_file(file_content: str) -> dict:
    # SUBTASK CALL: Parse CSV data
    parsed_data = await parse_csv_data(file_content)

    # SUBTASK CALL: Calculate statistics
    stats = await calculate_statistics(parsed_data)

    # SUBTASK CALL: Identify trends
    trends = await identify_trends(parsed_data)

    # SUBTASK CALL: Generate insights
    insights = await generate_insights(stats, trends, parsed_data)

    return {"statistics": stats, "trends": trends, "insights": insights}
```

## API Service (Client SDK)

### Endpoints

**`GET /`** - API information and available endpoints

**`GET /health`** - Health check with configuration status

**`POST /analyze`** - Upload and analyze a CSV file
- Accepts: `multipart/form-data` with CSV file
- Returns: Task run ID, status, and analysis results

**`POST /analyze-task/{task_name}`** - Call specific workflow task
- Allows calling individual tasks (e.g., just parsing)
- Useful for testing specific pipeline stages

### Client SDK Usage Pattern

The API service demonstrates the complete Client SDK workflow:

```python
from render_sdk import Render

# 1. Get client instance (uses RENDER_API_KEY env var automatically)
render = Render()

# 2. Construct task identifier: {service-slug}/{task-name}
service_slug = os.getenv("WORKFLOW_SERVICE_SLUG")
task_identifier = f"{service_slug}/analyze_file"

# 3. Call the workflow task with arguments as a dict
task_run = await render.workflows.run_task(
    task_identifier,
    {"file_content": file_content}
)

# 4. Await the task completion
result = await task_run

# 5. Access the results
print(result.id)        # Task run ID
print(result.status)    # Task status (e.g., "SUCCEEDED")
print(result.results)   # Task return value
```

## Local development

### Prerequisites

- Python 3.10+ (for the Python version)
- Node.js 18+ (for the TypeScript version)
- Render API key

### Running the workflow service

**Python:**

```bash
cd file-analyzer/python/workflow-service
pip install -r requirements.txt
python main.py
```

**TypeScript:**

```bash
cd file-analyzer/typescript/workflow-service
npm install
npm run dev
```

Keep this running.

### Running the API service

In a separate terminal:

**Python:**

```bash
cd file-analyzer/python/api-service
pip install -r requirements.txt
cp .env.example .env  # then edit .env with your keys
uvicorn main:app --host 0.0.0.0 --port 8000
```

**TypeScript:**

```bash
cd file-analyzer/typescript/api-service
npm install
cp .env.example .env  # then edit .env with your keys
npm run dev
```

### Testing Locally

**Using curl:**

```bash
curl -X POST "http://localhost:8000/analyze" \
  -F "file=@sample_files/sales_data.csv"
```

**Using Python:**

```python
import requests

with open('sample_files/sales_data.csv', 'rb') as f:
    response = requests.post(
        'http://localhost:8000/analyze',
        files={'file': f}
    )
    print(response.json())
```

**Check health:**

```bash
curl http://localhost:8000/health
```

## Deploying to Render

### Step 1: Deploy workflow service

**Service type**: Workflow

**Python:**

| Setting | Value |
|---|---|
| Name | `file-analyzer-workflows` (this becomes your slug) |
| Build command | `cd file-analyzer/python/workflow-service && pip install -r requirements.txt` |
| Start command | `cd file-analyzer/python/workflow-service && python main.py` |

**TypeScript:**

| Setting | Value |
|---|---|
| Name | `file-analyzer-workflows` (this becomes your slug) |
| Build command | `cd file-analyzer/typescript/workflow-service && npm install && npm run build` |
| Start command | `cd file-analyzer/typescript/workflow-service && npm start` |

**Environment variables:**

| Variable | Description |
|---|---|
| `RENDER_API_KEY` | Your Render API key (from Account Settings > API Keys) |

**Deployment steps:**

1. Go to Render Dashboard.
1. Click **New +** > **Workflow**.
1. Connect your repository and configure as above.
1. Click **Create Workflow**.

Note the service slug -- you'll need this for the API service.

## Testing Workflow Service in Render Dashboard

Once the workflow service is deployed, you can test tasks directly in the Render Dashboard:

### How to Test

1. Go to your Workflow service in Render Dashboard
2. Click the **"Manual Run"** or **"Start Task"** button
3. Select the task you want to test
4. Enter the task input as JSON in the text area
5. Click **"Start task"**

### Example Task Inputs

**Recommended Starting Point:** Start with `analyze_file` - this is the main orchestrator task that runs the complete analysis pipeline (parse → statistics → trends → insights).

---

**Test the complete analysis pipeline:**

Task: `analyze_file`

Input:
```json
{
  "file_content": "date,product,quantity,price\n2024-01-15,Laptop,5,1200.00\n2024-01-16,Mouse,25,25.99\n2024-01-17,Monitor,8,350.00"
}
```

This will parse the CSV, calculate statistics, identify trends, and generate insights.

---

**Test individual tasks:**

Task: `parse_csv_data`

Input:
```json
{
  "file_content": "name,age,country\nAlice,28,USA\nBob,35,Canada"
}
```

Returns parsed CSV structure with rows and columns.

---

Task: `calculate_statistics`

Input (requires parsed data structure):
```json
{
  "data": {
    "success": true,
    "rows": [
      {"age": "28", "score": "85"},
      {"age": "35", "score": "92"}
    ],
    "columns": ["age", "score"],
    "row_count": 2
  }
}
```

Returns statistical metrics for numeric columns.

**Note:** The workflow service doesn't handle file uploads - it processes raw CSV content. For file uploads, use the API service (tested via HTTP endpoints, not the Dashboard).

### Step 2: Deploy API service

**Service type**: Web Service

**Python:**

| Setting | Value |
|---|---|
| Name | `file-analyzer-api` |
| Build command | `cd file-analyzer/python/api-service && pip install -r requirements.txt` |
| Start command | `cd file-analyzer/python/api-service && uvicorn main:app --host 0.0.0.0 --port $PORT` |

**TypeScript:**

| Setting | Value |
|---|---|
| Name | `file-analyzer-api` |
| Build command | `cd file-analyzer/typescript/api-service && npm install && npm run build` |
| Start command | `cd file-analyzer/typescript/api-service && npm start` |

**Environment variables:**

| Variable | Description |
|---|---|
| `RENDER_API_KEY` | Your Render API key (same as workflow service) |
| `WORKFLOW_SERVICE_SLUG` | Your workflow service slug (e.g., `file-analyzer-workflows`) |

**Deployment steps:**

1. Go to Render Dashboard.
1. Click **New +** > **Web Service**.
1. Connect your repository and configure as above.
1. Set environment variables (including `WORKFLOW_SERVICE_SLUG`).
1. Click **Create Web Service**.

### Step 3: Test the Deployed Services

Once both services are deployed and healthy:

```bash
# Get your API service URL from Render Dashboard
# Example: https://file-analyzer-api.onrender.com

# Test health endpoint
curl https://file-analyzer-api.onrender.com/health

# Upload a file for analysis
curl -X POST "https://file-analyzer-api.onrender.com/analyze" \
  -F "file=@path/to/your/file.csv"
```

## Environment Variables Reference

### Workflow Service

| Variable | Required | Description | Where to Get |
|----------|----------|-------------|--------------|
| `RENDER_API_KEY` | Yes | Your Render API key | Render Dashboard → Account Settings → API Keys |

### API Service

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `RENDER_API_KEY` | Yes | Your Render API key | Get from Account Settings |
| `WORKFLOW_SERVICE_SLUG` | Yes | Your workflow service slug | `file-analyzer-workflows` |

## Sample Data

### sales_data.csv

Contains sales transaction data with columns:
- `date`: Transaction date
- `product`: Product name
- `category`: Product category
- `quantity`: Items sold
- `price`: Unit price
- `region`: Sales region
- `sales_rep`: Sales representative

**Analysis Output:**
- Statistics: Total revenue, average order value, quantity metrics
- Trends: Most popular products, top regions, best sales reps
- Insights: Revenue by category, regional performance

### customer_data.csv

Contains customer information with columns:
- `customer_id`: Unique identifier
- `name`: Customer name
- `email`: Email address
- `age`: Customer age
- `country`: Customer country
- `plan_type`: Subscription plan
- `monthly_spend`: Monthly spending
- `signup_date`: Account creation date
- `status`: Account status

**Analysis Output:**
- Statistics: Average age, total monthly revenue, spending patterns
- Trends: Most common plans, country distribution, account status
- Insights: Customer segmentation, revenue by plan type

## Key Concepts Demonstrated

### 1. Client SDK Usage

**Creating the Client:**
```python
from render_sdk import Render

# Uses RENDER_API_KEY environment variable automatically
render = Render()
```

**Calling Tasks:**
```python
# Format: render.workflows.run_task(task_identifier, {args})
task_run = await render.workflows.run_task(
    "service-slug/task-name",
    {"arg1": value1, "arg2": value2}
)

# Await completion
result = await task_run

# Access results
print(result.id)       # Task run ID
print(result.status)   # "SUCCEEDED", "FAILED", etc.
print(result.results)  # Return value from task
```

### 2. Task SDK Usage

**Defining Tasks:**
```python
from render_sdk import Workflows

app = Workflows(auto_start=True)

@app.task
def my_task(param: str) -> dict:
    return {"result": param}
```

### 3. Service Separation

**Why separate services?**

1. **Scalability**: Scale API and compute independently
2. **Security**: Workflow tasks run in isolated environment
3. **Flexibility**: Multiple frontends can call the same workflows
4. **Maintenance**: Update task logic without touching API code

### 4. Workflow Slug Pattern

**Service slug determines routing:**
```python
# Service slug: "file-analyzer-workflows"
# Task name: "analyze_file"
# Full identifier: "file-analyzer-workflows/analyze_file"

# This routes the call to:
# - Service: file-analyzer-workflows
# - Task: analyze_file
```

## Extending This Example

### Add Authentication

```python
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer

security = HTTPBearer()

@app.post("/analyze")
async def analyze_file(
    file: UploadFile,
    token: str = Depends(security)
):
    # Verify token
    if not verify_token(token.credentials):
        raise HTTPException(status_code=401)
    # ... rest of logic
```

### Add Database Storage

```python
@app.post("/analyze")
async def analyze_file(file: UploadFile):
    # Trigger analysis
    result = await task_run

    # Store in database
    db.insert({
        "filename": file.filename,
        "task_run_id": result.id,
        "results": result.results,
        "created_at": datetime.now()
    })
```

### Add Webhook Notifications

```python
@app.task
async def analyze_file(file_content: str, webhook_url: str = None) -> dict:
    # ... perform analysis ...

    if webhook_url:
        # Notify completion
        await send_webhook(webhook_url, results)

    return results
```

### Add More File Formats

```python
@app.task
def parse_json_data(file_content: str) -> dict:
    # Parse JSON files
    pass

@app.task
def parse_excel_data(file_content: bytes) -> dict:
    # Parse Excel files
    pass
```

## Troubleshooting

### "RENDER_API_KEY not configured"

**Solution:** Set the `RENDER_API_KEY` environment variable in both services:
1. Go to Render Dashboard → Account Settings → API Keys
2. Copy your API key
3. Add to Environment section of both services

### "WORKFLOW_SERVICE_SLUG not configured"

**Solution:** Set the `WORKFLOW_SERVICE_SLUG` in the API service:
1. Find your workflow service slug (see "Finding Your Service Slug" above)
2. Add to API service Environment section: `WORKFLOW_SERVICE_SLUG=your-slug`
3. Redeploy the API service

### "Task not found" error

**Solution:**
1. Verify the workflow service is deployed and healthy
2. Check the task name matches exactly (case-sensitive)
3. Verify the service slug is correct
4. Check workflow service logs to see registered tasks

### "Connection timeout" error

**Solution:**
1. Ensure workflow service is running and healthy
2. Check Render service status in dashboard
3. Verify network connectivity between services
4. Check task execution time (may need to increase timeout)

## Performance Considerations

1. **File Size Limits**: Current implementation loads entire file into memory
   - For large files, consider streaming or chunked processing
   - Add file size validation in API service

2. **Task Timeout**: Long-running analysis may timeout
   - Configure appropriate timeout in Client SDK
   - Consider breaking into smaller subtasks

3. **Concurrent Requests**: FastAPI handles concurrent requests well
   - Monitor workflow service capacity
   - Consider rate limiting for production

4. **Result Caching**: Cache analysis results for identical files
   - Use file hash as cache key
   - Store results in Redis or database

## Important notes

- **Service types**: Workflow service (for tasks) vs. Web Service (for API).
- **Task arguments**: Passed as a dict/object: `{"arg1": value1, "arg2": value2}`.
- **Awaitable pattern**: Use `await taskRun` to wait for completion.
- **Service slug**: Set correctly in `WORKFLOW_SERVICE_SLUG` environment variable.
- **API key**: Required in both services, get from Account Settings.

## Resources

- [Render Workflows documentation](https://docs.render.com/workflows)
- [Render SDK on PyPI](https://pypi.org/project/render-sdk/)
- [Render SDK on npm](https://www.npmjs.com/package/@renderinc/sdk)
- [FastAPI documentation](https://fastapi.tiangolo.com/)
- [Express documentation](https://expressjs.com/)
- [Render Dashboard](https://dashboard.render.com/)
