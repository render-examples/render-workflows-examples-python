# Render Workflow Examples (Python)

A curated collection of production-ready workflow examples demonstrating various use cases for Render Workflows. Each example is self-contained, deployment-ready, and showcases different patterns and capabilities.

## Overview

These examples demonstrate how to build robust, scalable workflows using Render's Python SDK. All examples follow best practices for production deployments and include comprehensive documentation.

Render Workflows support both Python and TypeScript. This repo contains Python examples using the `render` package, deployed as Workflow services on Render.

## Examples

> **New to Workflows?** Start with Hello World — it covers the core concepts with minimal complexity.

### Quick Comparison

| Example | Use Case | Key Patterns | Extra Dependencies |
|---------|----------|--------------|-------------------|
| [**Hello World**](./hello-world/) | Learn workflow basics with simple number processing | Task definition, subtask calling with `ctx.run`, basic orchestration | None |
| [**ETL Job**](./etl-job/) | Process CSV data with validation and statistics | Subtasks, sequential processing, batch operations, data validation | None |
| [**OpenAI Agent**](./openai-agent/) | AI customer support agent with tool calling | Tool calling, nested subtasks (3 levels deep), stateful workflows, dynamic orchestration | `openai` |
| [**File Processing**](./file-processing/) | Batch process multiple file formats in parallel | Parallel execution with `asyncio.gather()`, multi-format handling, aggregation | None |
| [**Data Pipeline**](./data-pipeline/) | Multi-source customer analytics pipeline | Parallel extraction, data enrichment, combining parallel + sequential patterns | `httpx` |
| [**File Analyzer**](./file-analyzer/) | API service calling workflow tasks for file analysis | Client SDK + Task SDK, workflow slugs, service separation, FastAPI integration | `fastapi`, `uvicorn` |
| [**Browser Screenshots**](./browser-screenshots/) | Check many web pages with headless Chromium | Playwright on the native runtime, fan-out with a concurrency cap, retries per URL, small outputs | `playwright` |

### 1. Hello World

The simplest possible workflow — learn the fundamentals through simple number processing.

- Ultra-simple task definitions
- Clear `ctx.run` subtask examples
- Subtasks in loops demonstration
- Multi-step workflow orchestration
- Heavily commented code explaining every pattern

If you're new to Render Workflows, start here. No CSV files, no APIs, no databases — just pure workflow patterns.

[View Hello World Example →](./hello-world/)

### 2. ETL Job

Complete Extract, Transform, Load pipeline — process customer data from CSV files with validation, cleaning, and statistical analysis.

- CSV data extraction with retry logic
- Record validation and error tracking
- Batch processing with subtasks
- Statistical aggregation
- Comprehensive error handling

[View ETL Job Example →](./etl-job/)

### 3. OpenAI Agent

Intelligent conversational agent — a customer support bot that can answer questions, look up orders, and process refunds.

- Multi-turn conversations with context
- Dynamic tool/function calling
- Stateful workflow management
- Integration with OpenAI GPT-4
- Extensible tool framework

[View OpenAI Agent Example →](./openai-agent/)

### 4. File Processing

Parallel file processing — batch process files from storage, analyze content, and generate consolidated reports.

- Multi-format support (CSV, JSON, text)
- Parallel file processing
- Automatic content analysis
- Report generation
- Format-specific insights

[View File Processing Example →](./file-processing/)

### 5. Data Pipeline

Multi-source data pipeline — build customer analytics by combining data from user service, transaction service, and analytics platform.

- Multi-source parallel extraction
- Data enrichment with external APIs
- Complex transformations
- User segmentation
- Aggregate insights generation

[View Data Pipeline Example →](./data-pipeline/)

### 6. File Analyzer

Complete Client SDK + Task SDK integration — a file analysis API with separate workflow and API services.

- Two-service architecture: Workflow service (Task SDK) + API service (Client SDK)
- Workflow slug pattern (`{service-slug}/{task-name}` routing)
- Client SDK usage for calling workflow tasks remotely
- File analysis pipeline: Parse → Statistics → Trends → Insights
- FastAPI integration with HTTP endpoints triggering workflow tasks

The only example that shows both Task SDK and Client SDK together — ideal for understanding how to build APIs that call workflow tasks.

[View File Analyzer Example →](./file-analyzer/)

### 7. Browser Screenshots

Check many web pages with headless Chromium: one task run per URL records the status code, title, load time, console errors and broken images, and takes a screenshot.

- Playwright's headless Chromium installed at build time into the project, no Docker image
- One task run per URL, with a concurrency cap in code (Render has no per-task cap)
- Retries per URL for timeouts and 5xx; bad URLs and unknown hosts come back as results
- Small outputs back (a 320 px JPEG thumbnail per page); shows where a real app would upload full screenshots to object storage

[View Browser Screenshots Example →](./browser-screenshots/)
