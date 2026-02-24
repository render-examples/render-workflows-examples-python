"""
File Processing and Analysis Workflow Example

This example demonstrates processing multiple file formats in parallel using
Render Workflows. It showcases:
- Reading and parsing various file formats (CSV, JSON, text)
- Parallel file processing with asyncio.gather()
- Data extraction and analysis
- Report generation and aggregation
- I/O operations in workflows

Use Case: Batch process files from storage, analyze content, and generate
consolidated reports
"""

import asyncio
import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from render_sdk import Retry, Workflows

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Workflows app with defaults
app = Workflows(
    default_retry=Retry(max_retries=3, wait_duration_ms=1000, backoff_scaling=1.5),
    default_timeout=300,
    auto_start=True,
)


# ============================================================================
# File Reading Tasks
# ============================================================================

@app.task
def read_csv_file(file_path: str) -> dict:
    """
    Read and parse a CSV file.

    Args:
        file_path: Path to CSV file

    Returns:
        Dictionary containing parsed data and metadata
    """
    logger.info(f"[CSV] Reading file: {file_path}")

    try:
        path = Path(file_path)
        if not path.exists():
            logger.warning(f"[CSV] File not found: {file_path}")
            return {
                "success": False,
                "error": "File not found",
                "file_path": file_path
            }

        rows = []
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        logger.info(f"[CSV] Successfully read {len(rows)} rows")

        return {
            "success": True,
            "file_path": file_path,
            "file_type": "csv",
            "row_count": len(rows),
            "data": rows,
            "columns": list(rows[0].keys()) if rows else []
        }

    except Exception as e:
        logger.error(f"[CSV] Error reading file: {e}")
        return {
            "success": False,
            "error": str(e),
            "file_path": file_path
        }


@app.task
def read_json_file(file_path: str) -> dict:
    """
    Read and parse a JSON file.

    Args:
        file_path: Path to JSON file

    Returns:
        Dictionary containing parsed data and metadata
    """
    logger.info(f"[JSON] Reading file: {file_path}")

    try:
        path = Path(file_path)
        if not path.exists():
            logger.warning(f"[JSON] File not found: {file_path}")
            return {
                "success": False,
                "error": "File not found",
                "file_path": file_path
            }

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        logger.info("[JSON] Successfully parsed JSON")

        return {
            "success": True,
            "file_path": file_path,
            "file_type": "json",
            "data": data,
            "keys": list(data.keys()) if isinstance(data, dict) else None
        }

    except Exception as e:
        logger.error(f"[JSON] Error reading file: {e}")
        return {
            "success": False,
            "error": str(e),
            "file_path": file_path
        }


@app.task
def read_text_file(file_path: str) -> dict:
    """
    Read and analyze a text file.

    Args:
        file_path: Path to text file

    Returns:
        Dictionary containing text content and analysis
    """
    logger.info(f"[TEXT] Reading file: {file_path}")

    try:
        path = Path(file_path)
        if not path.exists():
            logger.warning(f"[TEXT] File not found: {file_path}")
            return {
                "success": False,
                "error": "File not found",
                "file_path": file_path
            }

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Basic text analysis
        lines = content.split('\n')
        words = content.split()

        logger.info(f"[TEXT] Successfully read {len(lines)} lines")

        return {
            "success": True,
            "file_path": file_path,
            "file_type": "text",
            "content": content,
            "line_count": len(lines),
            "word_count": len(words),
            "char_count": len(content)
        }

    except Exception as e:
        logger.error(f"[TEXT] Error reading file: {e}")
        return {
            "success": False,
            "error": str(e),
            "file_path": file_path
        }


# ============================================================================
# Analysis Tasks
# ============================================================================

@app.task
def analyze_csv_data(csv_result: dict) -> dict:
    """
    Analyze CSV data and extract insights.

    Args:
        csv_result: Result from read_csv_file

    Returns:
        Dictionary with analysis results
    """
    logger.info("[ANALYSIS] Analyzing CSV data")

    if not csv_result.get("success"):
        return {"success": False, "error": "No data to analyze"}

    rows = csv_result.get("data", [])
    if not rows:
        return {"success": False, "error": "Empty dataset"}

    # Example analysis for sales data
    total_quantity = 0
    total_revenue = 0
    products = set()
    regions = set()

    for row in rows:
        try:
            quantity = int(row.get('quantity', 0))
            price = float(row.get('price', 0))
            total_quantity += quantity
            total_revenue += quantity * price

            if 'product' in row:
                products.add(row['product'])
            if 'region' in row:
                regions.add(row['region'])
        except (ValueError, TypeError):
            continue

    analysis = {
        "success": True,
        "total_records": len(rows),
        "total_quantity": total_quantity,
        "total_revenue": round(total_revenue, 2),
        "unique_products": len(products),
        "unique_regions": len(regions),
        "products": list(products),
        "regions": list(regions)
    }

    logger.info(f"[ANALYSIS] Total revenue: ${analysis['total_revenue']}")
    logger.info(f"[ANALYSIS] Products: {analysis['unique_products']}, "
               f"Regions: {analysis['unique_regions']}")

    return analysis


@app.task
def analyze_json_structure(json_result: dict) -> dict:
    """
    Analyze JSON structure and extract metadata.

    Args:
        json_result: Result from read_json_file

    Returns:
        Dictionary with structure analysis
    """
    logger.info("[ANALYSIS] Analyzing JSON structure")

    if not json_result.get("success"):
        return {"success": False, "error": "No data to analyze"}

    data = json_result.get("data", {})

    def count_keys(obj, depth=0):
        """Recursively count keys in nested structure."""
        if isinstance(obj, dict):
            count = len(obj)
            for value in obj.values():
                count += count_keys(value, depth + 1)
            return count
        elif isinstance(obj, list):
            return sum(count_keys(item, depth + 1) for item in obj)
        return 0

    analysis = {
        "success": True,
        "type": type(data).__name__,
        "top_level_keys": list(data.keys()) if isinstance(data, dict) else None,
        "total_keys": count_keys(data),
        "is_nested": any(isinstance(v, (dict, list)) for v in (data.values() if isinstance(data, dict) else []))
    }

    logger.info(f"[ANALYSIS] JSON type: {analysis['type']}, "
               f"Total keys: {analysis['total_keys']}")

    return analysis


@app.task
def analyze_text_content(text_result: dict) -> dict:
    """
    Analyze text content for insights.

    Args:
        text_result: Result from read_text_file

    Returns:
        Dictionary with text analysis
    """
    logger.info("[ANALYSIS] Analyzing text content")

    if not text_result.get("success"):
        return {"success": False, "error": "No data to analyze"}

    content = text_result.get("content", "")
    lines = content.split('\n')
    words = content.split()

    # Count sections (lines starting with uppercase or dashes)
    sections = [line for line in lines if line.strip() and (
        line.strip()[0].isupper() or line.strip().startswith('-')
    )]

    # Find keywords (words longer than 6 characters)
    long_words = [w.strip('.,!?') for w in words if len(w) > 6]
    keyword_freq = {}
    for word in long_words:
        word_lower = word.lower()
        keyword_freq[word_lower] = keyword_freq.get(word_lower, 0) + 1

    # Top keywords
    top_keywords = sorted(keyword_freq.items(), key=lambda x: x[1], reverse=True)[:5]

    analysis = {
        "success": True,
        "total_lines": len(lines),
        "total_words": len(words),
        "total_chars": len(content),
        "section_count": len(sections),
        "top_keywords": dict(top_keywords),
        "avg_line_length": len(content) / len(lines) if lines else 0
    }

    logger.info(f"[ANALYSIS] Text analysis: {len(words)} words, "
               f"{len(sections)} sections")

    return analysis


# ============================================================================
# Orchestration Tasks
# ============================================================================

@app.task
async def process_single_file(file_path: str) -> dict:
    """
    Process a single file based on its extension.

    Args:
        file_path: Path to file

    Returns:
        Dictionary with file data and analysis
    """
    logger.info(f"[PROCESS] Processing file: {file_path}")

    # Determine file type
    path = Path(file_path)
    extension = path.suffix.lower()

    # Read file based on type
    # SUBTASK PATTERN: Chain multiple subtask calls together
    if extension == '.csv':
        # SUBTASK CALL: Read CSV file
        read_result = await read_csv_file(file_path)
        # SUBTASK CALL: Analyze the CSV data (if read was successful)
        analysis = await analyze_csv_data(read_result) if read_result.get("success") else {}
    elif extension == '.json':
        # SUBTASK CALL: Read JSON file
        read_result = await read_json_file(file_path)
        # SUBTASK CALL: Analyze JSON structure
        analysis = await analyze_json_structure(read_result) if read_result.get("success") else {}
    elif extension == '.txt':
        # SUBTASK CALL: Read text file
        read_result = await read_text_file(file_path)
        # SUBTASK CALL: Analyze text content
        analysis = await analyze_text_content(read_result) if read_result.get("success") else {}
    else:
        logger.warning(f"[PROCESS] Unsupported file type: {extension}")
        return {
            "success": False,
            "file_path": file_path,
            "error": f"Unsupported file type: {extension}"
        }

    logger.info(f"[PROCESS] File processed: {file_path}")

    return {
        "success": read_result.get("success", False),
        "file_path": file_path,
        "file_type": extension[1:],  # Remove dot
        "read_result": read_result,
        "analysis": analysis
    }


@app.task
async def process_file_batch(*file_paths: str) -> dict:
    """
    Process multiple files in parallel.

    This demonstrates parallel task execution using asyncio.gather().
    All files are processed concurrently for maximum efficiency.

    Args:
        *file_paths: Variable number of file paths to process

    Returns:
        Dictionary with results for all files
    """
    # Convert to list for easier handling
    file_paths_list = list(file_paths)
    
    logger.info("=" * 80)
    logger.info(f"[BATCH] Starting batch processing of {len(file_paths_list)} files")
    logger.info("=" * 80)

    # Process all files in parallel
    # SUBTASK PATTERN: Call multiple subtasks concurrently using asyncio.gather()
    logger.info("[BATCH] Launching parallel file processing tasks...")
    # Create list of subtask calls (one per file)
    tasks = [process_single_file(fp) for fp in file_paths_list]
    # SUBTASK CALLS: Execute all process_single_file subtasks in parallel
    results = await asyncio.gather(*tasks)

    # Aggregate results
    successful = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    # Collect file types
    file_types = {}
    for result in successful:
        file_type = result.get("file_type", "unknown")
        file_types[file_type] = file_types.get(file_type, 0) + 1

    batch_result = {
        "total_files": len(file_paths_list),
        "successful": len(successful),
        "failed": len(failed),
        "success_rate": len(successful) / len(file_paths_list) if file_paths_list else 0,
        "file_types": file_types,
        "results": results,
        "processed_at": datetime.now().isoformat()
    }

    logger.info("=" * 80)
    logger.info("[BATCH] Batch processing complete!")
    logger.info(f"[BATCH] Successful: {len(successful)}/{len(file_paths_list)}")
    logger.info(f"[BATCH] File types: {file_types}")
    logger.info("=" * 80)

    return batch_result


@app.task
async def generate_consolidated_report(batch_result: dict) -> dict:
    """
    Generate a consolidated report from batch processing results.

    Args:
        batch_result: Results from process_file_batch

    Returns:
        Dictionary with consolidated report
    """
    logger.info("[REPORT] Generating consolidated report")

    results = batch_result.get("results", [])
    successful_results = [r for r in results if r.get("success")]

    # Aggregate data from all files
    total_csv_rows = 0
    total_text_words = 0
    total_json_keys = 0

    for result in successful_results:
        file_type = result.get("file_type")
        analysis = result.get("analysis", {})

        if file_type == "csv":
            total_csv_rows += analysis.get("total_records", 0)
        elif file_type == "text":
            total_text_words += analysis.get("total_words", 0)
        elif file_type == "json":
            total_json_keys += analysis.get("total_keys", 0)

    report = {
        "title": "File Processing Report",
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_files_processed": batch_result.get("total_files"),
            "successful": batch_result.get("successful"),
            "failed": batch_result.get("failed"),
            "success_rate_pct": round(batch_result.get("success_rate", 0) * 100, 1)
        },
        "data_summary": {
            "total_csv_rows": total_csv_rows,
            "total_text_words": total_text_words,
            "total_json_keys": total_json_keys
        },
        "file_breakdown": batch_result.get("file_types", {}),
        "detailed_results": successful_results
    }

    logger.info("[REPORT] Report generated successfully")
    logger.info(f"[REPORT] CSV rows: {total_csv_rows}, "
               f"Text words: {total_text_words}, "
               f"JSON keys: {total_json_keys}")

    return report
