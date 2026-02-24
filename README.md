# Render Workflow Examples

A curated collection of production-ready workflow examples demonstrating various use cases for Render Workflows. Each example is self-contained, deployment-ready, and showcases different patterns and capabilities.

## Overview

These examples demonstrate how to build robust, scalable workflows using Render's SDK. Each example is available in both **Python** and **TypeScript**, with implementations side by side in `python/` and `typescript/` subdirectories. All examples follow best practices for production deployments and include comprehensive documentation.

**Service type**: All workflow services must be deployed as Workflow services on Render.

## Examples

### Quick Comparison

| Example | Use Case | Key Patterns | Extra Dependencies |
|---------|----------|--------------|-------------------|
| [**Hello World**](./hello-world/) 👈 **START HERE** | Learn workflow basics with simple number processing | Task definition, subtask calling with `await`, basic orchestration | None |
| [**ETL Job**](./etl-job/) | Process CSV data with validation and statistics | Subtasks, sequential processing, batch operations, data validation | None |
| [**OpenAI Agent**](./openai-agent/) | AI customer support agent with tool calling | Tool calling, nested subtasks (3 levels deep), stateful workflows, dynamic orchestration | `openai` |
| [**File Processing**](./file-processing/) | Batch process multiple file formats in parallel | Parallel execution with `asyncio.gather()`, multi-format handling, aggregation | None |
| [**Data Pipeline**](./data-pipeline/) | Multi-source customer analytics pipeline | Parallel extraction, data enrichment, combining parallel + sequential patterns | None |
| [**File Analyzer**](./file-analyzer/) ⭐ | API service calling workflow tasks for file analysis | **Client SDK + Task SDK**, workflow slugs, service separation, FastAPI integration | `fastapi`, `uvicorn` |

---

### 1. Hello World (`hello-world/`) 👈 **START HERE**

The simplest possible workflow example - perfect for beginners!

**Use Case**: Learn workflow fundamentals through simple number processing operations.

**Key Features**:
- Ultra-simple task definitions
- Clear subtask calling examples
- Subtasks in loops demonstration
- Multi-step workflow orchestration
- Heavily commented code explaining every pattern

**Learn**: What is a task? What is a subtask? How to use `await` for subtask calls. The foundational patterns you need before anything else.

**Why Start Here**: If you're new to Render Workflows, this is your starting point. It teaches the core concepts with minimal complexity - no CSV files, no APIs, no databases. Just pure workflow patterns.

[View Hello World Example →](./hello-world/)

---

### 2. ETL Job (`etl-job/`)

Complete Extract, Transform, Load pipeline for data processing.

**Use Case**: Process customer data from CSV files with validation, cleaning, and statistical analysis.

**Key Features**:
- CSV data extraction with retry logic
- Record validation and error tracking
- Batch processing with subtasks
- Statistical aggregation
- Comprehensive error handling

**Learn**: Basic workflow patterns, subtask execution, data validation

[View ETL Job Example →](./etl-job/)

---

### 3. OpenAI Agent (`openai-agent/`)

Intelligent conversational agent with tool calling capabilities.

**Use Case**: Customer support agent that can answer questions, look up orders, and process refunds.

**Key Features**:
- Multi-turn conversations with context
- Dynamic tool/function calling
- Stateful workflow management
- Integration with OpenAI GPT-4
- Extensible tool framework

**Learn**: AI integration, tool calling, stateful workflows, complex orchestration

[View OpenAI Agent Example →](./openai-agent/)

---

### 4. File Processing (`file-processing/`)

Parallel file processing and analysis for multiple formats.

**Use Case**: Batch process files from storage, analyze content, generate consolidated reports.

**Key Features**:
- Multi-format support (CSV, JSON, text)
- Parallel file processing
- Automatic content analysis
- Report generation
- Format-specific insights

**Learn**: Parallel execution, I/O operations, multi-format handling, aggregation

[View File Processing Example →](./file-processing/)

---

### 5. Data Pipeline (`data-pipeline/`)

Comprehensive multi-source data pipeline with enrichment.

**Use Case**: Build customer analytics by combining data from user service, transaction service, and analytics platform.

**Key Features**:
- Multi-source parallel extraction
- Data enrichment with external APIs
- Complex transformations
- User segmentation
- Aggregate insights generation

**Learn**: Complex pipelines, parallel extraction, data enrichment, multi-stage workflows

[View Data Pipeline Example →](./data-pipeline/)

---

### 6. File Analyzer (`file-analyzer/`)

**CLIENT SDK + TASK SDK INTEGRATION** - Complete example showing how to use both SDKs together.

**Use Case**: Build a file analysis API with separate workflow and API services. Upload CSV files via HTTP, process with workflow tasks, return insights.

**Key Features**:
- **Two-Service Architecture**: Workflow service (Task SDK) + API service (Client SDK)
- **Workflow Slug Pattern**: Learn how `{service-slug}/{task-name}` routing works
- **Client SDK Usage**: Complete examples of calling workflow tasks remotely
- **File Analysis Pipeline**: Parse → Statistics → Trends → Insights
- **FastAPI Integration**: HTTP endpoints that trigger workflow tasks
- **Production Pattern**: Separation of concerns (API gateway vs compute)

**Learn**: Client SDK usage, workflow slugs, service separation, calling tasks remotely, API integration

**What's Unique**: This is the **only example that shows both Task SDK and Client SDK** together. Perfect for understanding how to build APIs that call workflow tasks.

[View File Analyzer Example →](./file-analyzer/)