"""
File Analyzer API Service

This service provides HTTP endpoints that call workflow tasks using the Client SDK.

This demonstrates the separation between:
- Workflow Service (defines tasks using Task SDK)
- API Service (calls tasks using Client SDK)

The API service acts as a frontend/gateway that triggers workflow tasks
and returns results to clients.
"""

import os
import logging
from typing import Any
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from render_sdk import RenderAsync
from render_sdk.client.errors import RenderError, TaskRunError
from pydantic import BaseModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="File Analyzer API",
    description="API service that triggers file analysis workflow tasks",
    version="0.1.0"
)

# Configure CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Response models
class AnalysisResponse(BaseModel):
    """Response model for analysis endpoint."""
    task_run_id: str
    status: str
    message: str
    result: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str
    render_api_key_configured: bool
    workflow_service_slug_configured: bool
    workflow_service_slug: str | None = None


# Client SDK helper functions
def get_client() -> RenderAsync:
    """
    Get async Render API client.

    The Client SDK requires a RENDER_API_KEY environment variable.
    Get your API key from: Render Dashboard → Account Settings → API Keys
    """
    api_key = os.getenv("RENDER_API_KEY")
    if not api_key:
        logger.error("RENDER_API_KEY environment variable not set")
        raise HTTPException(
            status_code=500,
            detail="RENDER_API_KEY not configured. Get your API key from Render Dashboard → Account Settings → API Keys"
        )
    return RenderAsync()  # Uses RENDER_API_KEY env var automatically


def get_task_identifier(task_name: str) -> str:
    """
    Get full task identifier in the format: {service-slug}/{task-name}

    The workflow service slug identifies which workflow service to use.
    You can find your service slug in the Render Dashboard:
    1. Go to your Workflow service
    2. Look at the service URL or service details
    3. The slug is the service name in lowercase with hyphens

    Example:
    - Service name: "file-analyzer-workflows"
    - Task name: "analyze_file"
    - Full identifier: "file-analyzer-workflows/analyze_file"

    Set WORKFLOW_SERVICE_SLUG environment variable to your service slug.
    """
    service_slug = os.getenv("WORKFLOW_SERVICE_SLUG")
    if not service_slug:
        logger.error("WORKFLOW_SERVICE_SLUG environment variable not set")
        raise HTTPException(
            status_code=500,
            detail=(
                "WORKFLOW_SERVICE_SLUG not configured. "
                "Set this to your workflow service slug from Render Dashboard. "
                "Example: 'file-analyzer-workflows'"
            )
        )

    # Format: {service-slug}/{task-name}
    task_identifier = f"{service_slug}/{task_name}"
    logger.info(f"Task identifier: {task_identifier}")
    return task_identifier


# API Endpoints

@app.get("/")
async def root():
    """Root endpoint - API information."""
    return {
        "service": "File Analyzer API",
        "version": "0.1.0",
        "description": "Upload CSV files for analysis via workflow tasks",
        "endpoints": {
            "POST /analyze": "Upload and analyze a CSV file",
            "GET /health": "Health check and configuration status"
        }
    }


@app.get("/health", response_model=HealthResponse)
async def health():
    """
    Health check endpoint.

    Verifies that required environment variables are configured.
    """
    api_key = os.getenv("RENDER_API_KEY")
    service_slug = os.getenv("WORKFLOW_SERVICE_SLUG")

    return HealthResponse(
        status="healthy",
        render_api_key_configured=bool(api_key),
        workflow_service_slug_configured=bool(service_slug),
        workflow_service_slug=service_slug
    )


@app.post("/analyze", response_model=AnalysisResponse)
async def analyze_file(file: UploadFile = File(...)):
    """
    Analyze a CSV file using the workflow service.

    This endpoint demonstrates how to use the Client SDK to call workflow tasks:

    1. Read the uploaded file content
    2. Get the Client SDK instance
    3. Get the task identifier (format: {service-slug}/{task-name})
    4. Start the task using client.workflows.start_task()
    5. Await the result
    6. Return the analysis results

    Args:
        file: Uploaded CSV file

    Returns:
        AnalysisResponse with task run ID, status, and results
    """
    logger.info(f"Received file upload: {file.filename}")

    # Validate file type
    if not file.filename or not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=400,
            detail="Only CSV files are supported. Please upload a .csv file."
        )

    try:
        # Read file content
        file_content = await file.read()
        file_content_str = file_content.decode('utf-8')
        logger.info(f"File content size: {len(file_content_str)} bytes")

        # Get Client SDK instance
        client = get_client()

        # Get task identifier: {service-slug}/{task-name}
        task_identifier = get_task_identifier("analyze_file")

        logger.info(f"Calling workflow task: {task_identifier}")

        # CLIENT SDK CALL: Start the workflow task
        # start_task returns an AwaitableTaskRun immediately
        started_run = await client.workflows.start_task(
            task_identifier,
            {"file_content": file_content_str}
        )

        logger.info(f"Task started: {started_run.id}")

        # Await the AwaitableTaskRun to wait for completion
        result = await started_run

        logger.info(f"Task completed with status: {result.status}")

        return AnalysisResponse(
            task_run_id=result.id,
            status=result.status,
            message=f"File '{file.filename}' analyzed successfully",
            result=result.results
        )

    except TaskRunError as e:
        logger.error(f"Task run error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Workflow task failed: {str(e)}"
        )
    except RenderError as e:
        logger.error(f"Render API error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Render API error: {str(e)}"
        )
    except UnicodeDecodeError:
        logger.error("File encoding error")
        raise HTTPException(
            status_code=400,
            detail="Unable to read file. Please ensure the CSV file is UTF-8 encoded."
        )
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {str(e)}"
        )


@app.post("/analyze-task/{task_name}", response_model=AnalysisResponse)
async def analyze_with_custom_task(task_name: str, file: UploadFile = File(...)):
    """
    Analyze a file using a specific workflow task.

    This demonstrates how to dynamically call different workflow tasks.

    Available tasks in the workflow service:
    - parse_csv_data: Just parse the CSV
    - calculate_statistics: Parse and calculate statistics
    - identify_trends: Parse and identify trends
    - analyze_file: Full analysis pipeline (recommended)

    Args:
        task_name: Name of the workflow task to call
        file: Uploaded CSV file

    Returns:
        AnalysisResponse with task run ID, status, and results
    """
    logger.info(f"Received file upload for task '{task_name}': {file.filename}")

    # Validate file type
    if not file.filename or not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=400,
            detail="Only CSV files are supported. Please upload a .csv file."
        )

    try:
        # Read file content
        file_content = await file.read()
        file_content_str = file_content.decode('utf-8')

        # Get Client SDK instance
        client = get_client()

        # Get task identifier for the specified task
        task_identifier = get_task_identifier(task_name)

        logger.info(f"Calling workflow task: {task_identifier}")

        # CLIENT SDK CALL: Start the specified workflow task
        started_run = await client.workflows.start_task(
            task_identifier,
            {"file_content": file_content_str}
        )

        logger.info(f"Task started: {started_run.id}")

        # Await the AwaitableTaskRun to wait for completion
        result = await started_run

        logger.info(f"Task '{task_name}' completed with status: {result.status}")

        return AnalysisResponse(
            task_run_id=result.id,
            status=result.status,
            message=f"File '{file.filename}' processed with task '{task_name}'",
            result=result.results
        )

    except TaskRunError as e:
        logger.error(f"Task run error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Workflow task '{task_name}' failed: {str(e)}"
        )
    except RenderError as e:
        logger.error(f"Render API error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Render API error: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting File Analyzer API Service")
    logger.info("This service calls workflow tasks using the async Client SDK (RenderAsync)")
    logger.info("Required environment variables:")
    logger.info("  - RENDER_API_KEY: Your Render API key")
    logger.info("  - WORKFLOW_SERVICE_SLUG: Your workflow service slug (e.g., 'file-analyzer-workflows')")

    uvicorn.run(app, host="0.0.0.0", port=8000)
