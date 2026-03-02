"""
Hello World - Getting Started with Render Workflows

This is the simplest possible workflow example to help you understand the basics.
It demonstrates:
- How to define a task using the @app.task decorator
- How to call a task as a subtask using await
- How to orchestrate multiple subtask calls

No complex business logic - just simple number operations to show the patterns clearly.
"""

import logging

from render_sdk import Workflows

# Configure logging to see what's happening
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Workflows app
app = Workflows()


# ============================================================================
# BASIC TASK - The building block
# ============================================================================

@app.task
def double(x: int) -> int:
    """
    A basic task that doubles a number.

    This is the simplest possible task - it takes an input, does something,
    and returns a result. Tasks are the building blocks of workflows.

    Args:
        x: The number to double

    Returns:
        The doubled number
    """
    logger.info(f"[TASK] Doubling {x}")
    result = x * 2
    logger.info(f"[TASK] Result: {result}")
    return result


# ============================================================================
# SUBTASK CALLING - Tasks calling other tasks
# ============================================================================

@app.task
async def add_doubled_numbers(*args: int) -> dict:
    """
    Demonstrates calling a task as a subtask.

    This task calls the 'double' task twice as a subtask using await.
    This is the fundamental pattern in Render Workflows - tasks can call
    other tasks to break down complex operations into simple, reusable pieces.

    KEY PATTERN: Use 'await task_name(args)' to call a task as a subtask.

    Args:
        *args: Two numbers to process

    Returns:
        Dictionary with original numbers, doubled values, and their sum
    """
    if len(args) != 2:
        raise ValueError(f"Expected exactly 2 arguments, got {len(args)}")
    
    a, b = args
    logger.info(f"[WORKFLOW] Starting: add_doubled_numbers({a}, {b})")

    # SUBTASK CALL #1: Call 'double' as a subtask
    # The 'await' keyword tells Render to execute this as a subtask
    logger.info(f"[WORKFLOW] Calling subtask: double({a})")
    doubled_a = await double(a)

    # SUBTASK CALL #2: Call 'double' as a subtask again
    logger.info(f"[WORKFLOW] Calling subtask: double({b})")
    doubled_b = await double(b)

    # Now we have the results from both subtasks, let's combine them
    total = doubled_a + doubled_b

    result = {
        "original_numbers": [a, b],
        "doubled_numbers": [doubled_a, doubled_b],
        "sum_of_doubled": total,
        "explanation": f"{a} doubled is {doubled_a}, {b} doubled is {doubled_b}, sum is {total}"
    }

    logger.info(f"[WORKFLOW] Complete: {result}")
    return result


# ============================================================================
# SUBTASK IN A LOOP - Processing multiple items
# ============================================================================

@app.task
async def process_numbers(*numbers: int) -> dict:
    """
    Demonstrates calling a subtask in a loop.

    This pattern is useful when you need to process multiple items,
    and each item requires the same operation (calling the same subtask).

    KEY PATTERN: Call subtasks in a loop to process lists/batches.

    Args:
        numbers: List of numbers to process

    Returns:
        Dictionary with original numbers and their doubled values
    """
    numbers_list = list(numbers)
    
    logger.info(f"[WORKFLOW] Starting: process_numbers({numbers_list})")

    doubled_results = []

    # Process each number by calling 'double' as a subtask
    for i, num in enumerate(numbers_list, 1):
        logger.info(f"[WORKFLOW] Processing item {i}/{len(numbers_list)}: {num}")

        # SUBTASK CALL: Call 'double' as a subtask for each number
        doubled = await double(num)
        doubled_results.append(doubled)

    result = {
        "original_numbers": numbers_list,
        "doubled_numbers": doubled_results,
        "count": len(numbers_list),
        "explanation": f"Processed {len(numbers_list)} numbers through the double subtask"
    }

    logger.info(f"[WORKFLOW] Complete: {result}")
    return result


# ============================================================================
# MULTI-STEP WORKFLOW - Chaining multiple subtasks
# ============================================================================

@app.task
async def calculate_and_process(a: int, b: int, *more_numbers: int) -> dict:
    """
    Demonstrates a multi-step workflow that chains multiple subtasks.

    This is a more complex example showing how you can build workflows
    that call multiple different subtasks in sequence, using the results
    from one subtask as input to the next.

    KEY PATTERN: Chain multiple subtask calls to create complex workflows.

    Args:
        a: First number
        b: Second number
        more_numbers: Additional numbers to process

    Returns:
        Dictionary with results from multiple workflow steps
    """
    more_numbers_list = list(more_numbers)
    
    logger.info("[WORKFLOW] Starting multi-step workflow")

    # STEP 1: Add two doubled numbers
    logger.info("[WORKFLOW] Step 1: Adding doubled numbers")
    step1_result = await add_doubled_numbers(a, b)

    # STEP 2: Process a list of numbers
    logger.info("[WORKFLOW] Step 2: Processing number list")
    step2_result = await process_numbers(*more_numbers_list)

    # STEP 3: Combine the results
    logger.info("[WORKFLOW] Step 3: Combining results")
    final_result = {
        "step1_sum": step1_result["sum_of_doubled"],
        "step2_doubled": step2_result["doubled_numbers"],
        "total_operations": 2 + len(more_numbers_list),
        "summary": f"Added doubled {a} and {b}, then doubled {len(more_numbers_list)} more numbers"
    }

    logger.info("[WORKFLOW] Multi-step workflow complete")
    return final_result


if __name__ == "__main__":
    app.start()
