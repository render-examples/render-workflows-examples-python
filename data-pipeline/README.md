# Data Pipeline Workflow Example

A comprehensive data pipeline demonstrating multi-source data extraction, enrichment, and aggregation using Render Workflows.

## Use Case

Build customer analytics by combining data from multiple services:
- User profiles from user service
- Transaction history from payment service
- Engagement metrics from analytics service
- Geographic enrichment from external APIs

Common applications:
- Customer 360 dashboards
- Business intelligence pipelines
- Data warehouse ETL
- Real-time analytics
- Multi-source reporting

## Features

- **Multi-Source Extraction**: Fetch data from multiple APIs/databases in parallel
- **Data Enrichment**: Augment data with external services (geo-location, etc.)
- **Complex Transformations**: Combine and process data from various sources
- **Aggregation**: Generate insights and statistics
- **Full Pipeline Orchestration**: Coordinate Extract → Transform → Load stages
- **Error Handling**: Robust retry logic for external service calls

## Workflow Structure

```
run_data_pipeline (orchestrator)
  │
  ├─ Stage 1: EXTRACT (parallel)
  │  ├── fetch_user_data
  │  ├── fetch_transaction_data
  │  └── fetch_engagement_data
  │
  ├─ Stage 2: TRANSFORM
  │  └── transform_user_data
  │      ├── calculate_user_metrics (for each user)
  │      └── enrich_with_geo_data (for each user)
  │
  └─ Stage 3: AGGREGATE
     └── aggregate_insights
```

## Local development

### Prerequisites

- Python 3.10+ (for the Python version)
- Node.js 18+ (for the TypeScript version)

### Python

```bash
cd data-pipeline/python
pip install -r requirements.txt
python main.py
```

### TypeScript

```bash
cd data-pipeline/typescript
npm install
npm run dev
```

## Deploying to Render

### Service configuration

**Service type**: Workflow

**Python:**

| Setting | Value |
|---|---|
| Build command | `cd data-pipeline/python && pip install -r requirements.txt` |
| Start command | `cd data-pipeline/python && python main.py` |

**TypeScript:**

| Setting | Value |
|---|---|
| Build command | `cd data-pipeline/typescript && npm install && npm run build` |
| Start command | `cd data-pipeline/typescript && npm start` |

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

Once deployed, you can test the data pipeline directly in the Render Dashboard:

### How to Test

1. Go to your Workflow service in Render Dashboard
2. Click the **"Manual Run"** or **"Start Task"** button
3. Select the task you want to test
4. Enter the task input as JSON in the text area
5. Click **"Start task"**

### Example Task Inputs

**Recommended Starting Point:** Start with `run_data_pipeline` - this is the main orchestrator that demonstrates parallel extraction, transformation, and aggregation.

---

**Test the complete pipeline:**

Task: `run_data_pipeline`

Input:
```json
{
  "user_ids": ["user_1", "user_2", "user_3", "user_4"]
}
```

This will:
1. Extract data from 3 sources in parallel (users, transactions, engagement)
2. Transform and enrich the data with geographic information
3. Aggregate insights including revenue, segmentation, and engagement metrics

---

**Test individual extraction tasks:**

Task: `fetch_user_data`

Input:
```json
{
  "user_ids": ["user_1", "user_2"]
}
```

Task: `fetch_transaction_data`

Input:
```json
{
  "user_ids": ["user_1", "user_2"]
}
```

Task: `fetch_engagement_data`

Input:
```json
{
  "user_ids": ["user_1", "user_2"]
}
```

**Tip:** Watch the logs to see parallel execution in action - all three data sources are fetched simultaneously!

## Triggering via SDK

Once deployed, trigger the pipeline via the Render API or SDK:

```python
from render_sdk import Render

# Uses RENDER_API_KEY environment variable automatically
render = Render()

# Run the complete pipeline
task_run = await render.workflows.run_task(
    "data-pipeline-workflows/run_data_pipeline",
    {"user_ids": ["user_1", "user_2", "user_3", "user_4"]}
)

result = await task_run
print(f"Pipeline status: {result.results['status']}")
print(f"Total revenue: ${result.results['insights']['revenue']['total']}")
print(f"Segment distribution: {result.results['segment_distribution']}")
```

## Pipeline Stages

### Stage 1: Extract (Parallel)

Three data sources are queried simultaneously:

**`fetch_user_data`**: User profiles (name, email, plan)

**`fetch_transaction_data`**: Transaction history (purchases, refunds, subscriptions)

**`fetch_engagement_data`**: Analytics (page views, sessions, feature usage)

Using `asyncio.gather()` ensures all sources are fetched in parallel for maximum efficiency.

### Stage 2: Transform

**`transform_user_data`**: Combines data from all sources and enriches each user by calling subtasks:
```python
for user in users:
    # SUBTASK CALL: Calculate metrics for this user
    user_metrics = await calculate_user_metrics(user, transactions, engagement)

    # SUBTASK CALL: Enrich with geographic data
    geo_data = await enrich_with_geo_data(user['email'])

    enriched_users.append({**user_metrics, 'geo': geo_data})
```
This demonstrates **sequential subtask calls per item** in a transformation loop.

**`calculate_user_metrics`**: Calculates per-user metrics:
- Total spent and refunded
- Net revenue
- Engagement score
- User segment classification

**`enrich_with_geo_data`**: Adds geographic information (country, timezone, language)

### Stage 3: Aggregate

**`aggregate_insights`**: Generates high-level insights:
- Segment distribution
- Revenue metrics (total, average, top users)
- Engagement metrics (average score, total activity)
- Geographic distribution

## Output Example

```json
{
  "status": "success",
  "user_count": 4,
  "insights": {
    "total_users": 4,
    "segment_distribution": {
      "high_value": 1,
      "premium": 1,
      "engaged": 1,
      "standard": 1
    },
    "revenue": {
      "total": 458.23,
      "average_per_user": 114.56,
      "top_users": [
        {"name": "Alice Johnson", "revenue": 152.34, "segment": "high_value"},
        {"name": "Charlie Brown", "revenue": 145.67, "segment": "premium"}
      ]
    },
    "engagement": {
      "average_score": 67.8,
      "total_page_views": 2456,
      "total_sessions": 189
    },
    "geographic_distribution": {
      "USA": 2,
      "Canada": 1,
      "UK": 1
    }
  }
}
```

## Key Patterns Demonstrated

### Parallel Extraction with Subtasks

```python
# SUBTASK PATTERN: Launch multiple subtasks in parallel
user_task = fetch_user_data(user_ids)
transaction_task = fetch_transaction_data(user_ids)
engagement_task = fetch_engagement_data(user_ids)

# SUBTASK CALLS: Wait for all three subtasks to complete
user_data, transaction_data, engagement_data = await asyncio.gather(
    user_task,
    transaction_task,
    engagement_task
)
```

This demonstrates **parallel subtask execution** - all three data sources are fetched simultaneously.
This reduces total extraction time from sum(A+B+C) to max(A,B,C).

### Data Enrichment with Subtasks

Each user is enriched by calling multiple subtasks:
```python
for user in users:
    # SUBTASK CALL: Calculate user-specific metrics
    metrics = await calculate_user_metrics(user, transactions, engagement)

    # SUBTASK CALL: Enrich with geographic data
    geo = await enrich_with_geo_data(user['email'])

    enriched_users.append({**metrics, 'geo': geo})
```

This shows **sequential subtask calls** for per-item enrichment.

### User Segmentation

Business logic classifies users into segments:
- **high_value**: Premium plan + high revenue
- **premium**: Premium plan
- **engaged**: High engagement score
- **standard**: Default category

## Extending This Example

**Add Real APIs**:
```python
@app.task
async def fetch_user_data_from_api(user_ids: list[str]) -> dict:
    client = get_http_client()
    response = await client.post(
        "https://api.yourservice.com/users",
        json={"user_ids": user_ids}
    )
    return response.json()
```

**Add Database Integration**:
```python
@app.task
async def load_to_warehouse(insights: dict) -> dict:
    # Connect to data warehouse (Snowflake, BigQuery, etc.)
    # Insert aggregated insights
    # Return confirmation
    pass
```

**Add Caching**:
```python
@app.task
async def fetch_with_cache(source: str, key: str) -> dict:
    # Check Redis/Memcached
    # If miss, fetch from source and cache
    # Return data
    pass
```

**Add Notifications**:
```python
@app.task
async def send_pipeline_notification(result: dict) -> dict:
    # Send to Slack, email, etc.
    # Notify stakeholders of pipeline completion
    pass
```

## Performance Considerations

1. **Parallel Extraction**: All data sources fetched simultaneously
2. **Batch Processing**: Users processed in groups, not one-by-one
3. **Retry Logic**: All external calls have retry configuration
4. **Timeout Settings**: HTTP client configured with 30s timeout
5. **Error Isolation**: One source failure doesn't block others

## Important notes

- **Mock data**: This example uses simulated data. Replace with real API calls in production.
- **Idempotency**: Design pipeline to be safely re-runnable.
- **Monitoring**: Add logging and metrics for production deployments.
- **Cost**: Consider API rate limits and costs for external services.
