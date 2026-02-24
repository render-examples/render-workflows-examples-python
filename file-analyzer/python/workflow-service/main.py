"""
File Analyzer Workflow Service

This service defines workflow tasks for analyzing CSV files.
Tasks defined here can be called remotely using the Client SDK.

Task Definitions (using Task SDK):
- parse_csv_data: Parse CSV content into structured data
- calculate_statistics: Calculate numerical statistics
- identify_trends: Identify patterns and trends
- generate_insights: Generate final insights report
- analyze_file: Main orchestrator task
"""

import logging
import csv
import io
from datetime import datetime

from render_sdk import Retry, Workflows

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Workflows app with defaults
app = Workflows(
    default_retry=Retry(max_retries=2, wait_duration_ms=1000, backoff_scaling=1.5),
    default_timeout=300,
    auto_start=True,
)


@app.task
def parse_csv_data(file_content: str) -> dict:
    """
    Parse CSV file content into structured data.

    Args:
        file_content: Raw CSV file content as string

    Returns:
        dict with parsed data, column info, and metadata
    """
    logger.info("[PARSE] Starting CSV parsing")

    try:
        # Parse CSV content
        csv_reader = csv.DictReader(io.StringIO(file_content))
        rows = list(csv_reader)

        if not rows:
            logger.warning("[PARSE] No data rows found in CSV")
            return {
                "success": False,
                "error": "No data rows found",
                "rows": [],
                "columns": [],
                "row_count": 0
            }

        columns = list(rows[0].keys())

        logger.info(f"[PARSE] Successfully parsed {len(rows)} rows with {len(columns)} columns")

        return {
            "success": True,
            "rows": rows,
            "columns": columns,
            "row_count": len(rows),
            "parsed_at": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"[PARSE] Error parsing CSV: {e}")
        return {
            "success": False,
            "error": str(e),
            "rows": [],
            "columns": [],
            "row_count": 0
        }


@app.task
def calculate_statistics(data: dict) -> dict:
    """
    Calculate statistical metrics from parsed data.

    This is called as a SUBTASK by the main analyze_file task.

    Args:
        data: Parsed CSV data from parse_csv_data

    Returns:
        dict with statistical metrics
    """
    logger.info("[STATS] Calculating statistics")

    if not data["success"] or not data["rows"]:
        logger.warning("[STATS] No data to analyze")
        return {
            "success": False,
            "error": "No data available for statistics"
        }

    rows = data["rows"]
    columns = data["columns"]

    # Find numeric columns
    numeric_columns = []
    numeric_data = {}

    for col in columns:
        try:
            # Try to convert first non-empty value to float
            values = [float(row[col]) for row in rows if row.get(col) and row[col].strip()]
            if values:
                numeric_columns.append(col)
                numeric_data[col] = values
        except (ValueError, TypeError):
            # Not a numeric column
            pass

    # Calculate statistics for numeric columns
    statistics = {}
    for col in numeric_columns:
        values = numeric_data[col]
        statistics[col] = {
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
            "sum": sum(values),
            "count": len(values)
        }

    logger.info(f"[STATS] Calculated statistics for {len(numeric_columns)} numeric columns")

    return {
        "success": True,
        "numeric_columns": numeric_columns,
        "statistics": statistics,
        "total_rows": len(rows),
        "calculated_at": datetime.now().isoformat()
    }


@app.task
def identify_trends(data: dict) -> dict:
    """
    Identify trends and patterns in the data.

    This is called as a SUBTASK by the main analyze_file task.

    Args:
        data: Parsed CSV data from parse_csv_data

    Returns:
        dict with identified trends
    """
    logger.info("[TRENDS] Identifying trends")

    if not data["success"] or not data["rows"]:
        logger.warning("[TRENDS] No data to analyze")
        return {
            "success": False,
            "error": "No data available for trend analysis"
        }

    rows = data["rows"]
    columns = data["columns"]

    # Analyze categorical columns
    categorical_analysis = {}

    for col in columns:
        # Count unique values
        values = [row.get(col, "") for row in rows if row.get(col)]
        unique_values = set(values)

        if len(unique_values) < len(rows) / 2:  # If less than half are unique, treat as categorical
            value_counts = {}
            for val in values:
                value_counts[val] = value_counts.get(val, 0) + 1

            # Sort by count
            sorted_counts = sorted(value_counts.items(), key=lambda x: x[1], reverse=True)

            categorical_analysis[col] = {
                "unique_count": len(unique_values),
                "total_count": len(values),
                "top_5": sorted_counts[:5],
                "distribution": dict(sorted_counts)
            }

    logger.info(f"[TRENDS] Analyzed {len(categorical_analysis)} categorical columns")

    return {
        "success": True,
        "categorical_columns": list(categorical_analysis.keys()),
        "categorical_analysis": categorical_analysis,
        "analyzed_at": datetime.now().isoformat()
    }


@app.task
async def generate_insights(stats: dict, trends: dict, metadata: dict) -> dict:
    """
    Generate final insights report combining statistics and trends.

    This is called as a SUBTASK by the main analyze_file task.

    Args:
        stats: Statistics from calculate_statistics
        trends: Trends from identify_trends
        metadata: Original parsed data metadata

    Returns:
        dict with comprehensive insights
    """
    logger.info("[INSIGHTS] Generating insights report")

    insights = {
        "success": True,
        "summary": {
            "total_rows": metadata.get("row_count", 0),
            "total_columns": len(metadata.get("columns", [])),
            "numeric_columns_count": len(stats.get("numeric_columns", [])),
            "categorical_columns_count": len(trends.get("categorical_columns", []))
        },
        "key_findings": []
    }

    # Generate key findings from statistics
    if stats.get("success") and stats.get("statistics"):
        for col, stat in stats["statistics"].items():
            insights["key_findings"].append({
                "type": "numeric",
                "column": col,
                "finding": f"{col}: avg={stat['avg']:.2f}, min={stat['min']:.2f}, max={stat['max']:.2f}, sum={stat['sum']:.2f}"
            })

    # Generate key findings from trends
    if trends.get("success") and trends.get("categorical_analysis"):
        for col, analysis in trends["categorical_analysis"].items():
            top_value = analysis["top_5"][0] if analysis["top_5"] else None
            if top_value:
                insights["key_findings"].append({
                    "type": "categorical",
                    "column": col,
                    "finding": f"{col}: Most common value is '{top_value[0]}' ({top_value[1]} occurrences, {len(analysis['distribution'])} unique values)"
                })

    insights["generated_at"] = datetime.now().isoformat()

    logger.info(f"[INSIGHTS] Generated {len(insights['key_findings'])} key findings")

    return insights


@app.task
async def analyze_file(file_content: str) -> dict:
    """
    Main orchestrator task for file analysis.

    This task coordinates the entire analysis pipeline by calling
    other tasks as SUBTASKS.

    Pipeline:
    1. Parse CSV data
    2. Calculate statistics (SUBTASK)
    3. Identify trends (SUBTASK)
    4. Generate insights (SUBTASK)

    Args:
        file_content: Raw CSV file content as string

    Returns:
        dict with complete analysis results
    """
    logger.info("[ANALYZE_FILE] Starting file analysis pipeline")

    # Stage 1: Parse CSV data
    logger.info("[ANALYZE_FILE] Stage 1: Parsing CSV data")
    # SUBTASK CALL: Parse the CSV content
    parsed_data = await parse_csv_data(file_content)

    if not parsed_data["success"]:
        logger.error("[ANALYZE_FILE] Failed to parse CSV data")
        return {
            "success": False,
            "error": "Failed to parse CSV data",
            "details": parsed_data.get("error")
        }

    logger.info(f"[ANALYZE_FILE] Parsed {parsed_data['row_count']} rows")

    # Stage 2: Calculate statistics (SUBTASK)
    logger.info("[ANALYZE_FILE] Stage 2: Calculating statistics")
    # SUBTASK CALL: Calculate statistical metrics
    stats = await calculate_statistics(parsed_data)

    # Stage 3: Identify trends (SUBTASK)
    logger.info("[ANALYZE_FILE] Stage 3: Identifying trends")
    # SUBTASK CALL: Identify patterns and trends
    trends = await identify_trends(parsed_data)

    # Stage 4: Generate insights (SUBTASK)
    logger.info("[ANALYZE_FILE] Stage 4: Generating insights")
    # SUBTASK CALL: Generate final insights report
    insights = await generate_insights(stats, trends, parsed_data)

    logger.info("[ANALYZE_FILE] Analysis pipeline completed successfully")

    return {
        "success": True,
        "file_metadata": {
            "row_count": parsed_data["row_count"],
            "columns": parsed_data["columns"]
        },
        "statistics": stats,
        "trends": trends,
        "insights": insights,
        "completed_at": datetime.now().isoformat()
    }
