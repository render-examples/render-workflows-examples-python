# Hello World - Getting Started with Render Workflows

The simplest possible workflow example to help you understand the basics of Render Workflows. Perfect for beginners!

## What You'll Learn

This example teaches the fundamental concepts:
- **What is a Task?** - A function decorated with `@app.task` that can be executed as a workflow
- **What is Task Chaining?** - Calling one task from another with `await`
- **How to Orchestrate** - Combining multiple tasks to create workflows
- **How to Deploy** - Getting your first workflow running on Render

## Use Case

Simple number processing to demonstrate workflow patterns without complex business logic. If you can understand this example, you can build any workflow!

## Workflow Structure

```
calculate_and_process (multi-step orchestrator)
  ├── add_doubled_numbers
  │   ├── double (chained run #1)
  │   └── double (chained run #2)
  └── process_numbers
      ├── double (chained run for item 1)
      ├── double (chained run for item 2)
      └── double (chained run for item N)
```

## Understanding Tasks and Task Chaining

### What is a Task?

A **task** is simply a Python function decorated with `@app.task`. It becomes a unit of workflow execution that Render can run:

```python
from render_sdk import Workflows

app = Workflows()

@app.task
def double(x: int) -> int:
    """A simple task that doubles a number"""
    return x * 2

app.start()
```

### What is Task Chaining?

Task chaining is when one task calls another task using `await`. This is how you compose runs into a workflow:

```python
@app.task
async def add_doubled_numbers(a: int, b: int) -> dict:
    # Chain two runs of 'double' using await
    doubled_a = await double(a)  # ← Chained run
    doubled_b = await double(b)  # ← Another chained run

    return {
        "sum": doubled_a + doubled_b
    }
```

### Why Use Task Chaining?

1. **Reusability**: Write `double` once, use it everywhere
2. **Composition**: Build complex workflows from simple building blocks
3. **Visibility**: Render shows each task run in the dashboard
4. **Testing**: Test individual tasks independently

## Local Development

### Prerequisites
- Python 3.10+

### Setup and Run

```bash
# Navigate to example directory
cd hello-world

# Install dependencies
pip install -r requirements.txt

# Run the workflow service
python main.py
```

The service will start and register all tasks. You'll see output like:

```
Starting Hello World Workflow Service
Registered tasks:
  - double(x)
  - add_doubled_numbers(a, b)
  - process_numbers(numbers)
  - calculate_and_process(a, b, more_numbers)
Ready to accept task executions!
```

## Deploying to Render

### Service Configuration

**Service Type**: Workflow

**Build Command**:
```bash
cd hello-world && pip install -r requirements.txt
```

**Start Command**:
```bash
cd hello-world && python main.py
```

### Environment Variables

Required:
- `RENDER_API_KEY` - Your Render API key (from Render dashboard)

### Deployment Steps

1. **Create Workflow Service**
   - Go to Render Dashboard
   - Click "New +" → "Workflow"
   - Connect your repository
   - Name: `hello-world-workflows`

2. **Configure Build Settings**
   - Build Command: `cd hello-world && pip install -r requirements.txt`
   - Start Command: `cd hello-world && python main.py`

3. **Set Environment Variables**
   - Add `RENDER_API_KEY` in the Environment section
   - Get API key from: Render Dashboard → Account Settings → API Keys

4. **Deploy**
   - Click "Create Workflow"
   - Render will build and start your workflow service

## Testing in Render Dashboard

Once deployed, test your workflows directly in the Render Dashboard:

### How to Test

1. Go to your Workflow service in Render Dashboard
2. Click the **"Manual Run"** or **"Start Task"** button
3. Select the task you want to test
4. Enter the task input as JSON in the text area
5. Click **"Start task"**

### Example Task Inputs

**Important:** The hello-world workflow expects direct values and arrays, not JSON objects. Use `5` instead of `{"x": 5}`, and `[3, 4]` instead of `{"a": 3, "b": 4}`.

**Recommended Starting Point:** Start with `double` - the simplest possible task, then work your way up to more complex examples.

---

**Test the basic task:**

Task: `double`

Input:
```json
5
```

Expected output: `10`

---

**Test task chaining:**

Task: `add_doubled_numbers`

Input:
```json
[3, 4]
```

Expected output:
```json
{
  "original_numbers": [3, 4],
  "doubled_numbers": [6, 8],
  "sum_of_doubled": 14,
  "explanation": "3 doubled is 6, 4 doubled is 8, sum is 14"
}
```

This task chains two runs of `double`.

---

**Test task chaining in a loop:**

Task: `process_numbers`

Input:
```json
[1, 2, 3, 4, 5]
```

Expected output:
```json
{
  "original_numbers": [1, 2, 3, 4, 5],
  "doubled_numbers": [2, 4, 6, 8, 10],
  "count": 5,
  "explanation": "Processed 5 numbers by chaining runs of double"
}
```

This chains `double` 5 times (once for each number).

---

**Test multi-step workflow:**

Task: `calculate_and_process`

Input:
```json
[2, 3, 10, 20, 30]
```

This is the most complex example: it chains `add_doubled_numbers` and `process_numbers`, which in turn chain `double` multiple times.

## Triggering via SDK

Once deployed, trigger workflows via the Render Client SDK:

```python
from render_sdk import Render

# Uses RENDER_API_KEY environment variable automatically
render = Render()

# Call the simple double task
task_run = await render.workflows.run_task(
    "hello-world-workflows/double",
    [5]
)
print(f"Result: {task_run.results}")  # Output: 10

# Call a task-chaining example
task_run = await render.workflows.run_task(
    "hello-world-workflows/add_doubled_numbers",
    [3, 4]
)
print(f"Sum of doubled: {task_run.results['sum_of_doubled']}")  # Output: 14
```

## Tasks Explained

### `double(x: int) -> int`

The simplest possible task. Takes a number, doubles it, returns the result.

**Purpose**: Show what a basic task looks like.

**Can be chained from other tasks**: Yes.

---

### `add_doubled_numbers(a: int, b: int) -> dict`

Demonstrates the fundamental task-chaining pattern.

**What it does**:
1. Chains `double(a)`
2. Chains `double(b)`
3. Adds the results together

**Purpose**: Show how to chain task runs with `await`.

**Key Pattern**:
```python
result = await double(a)  # ← Chained run with await
```

---

### `process_numbers(numbers: list[int]) -> dict`

Demonstrates chaining task runs in a loop.

**What it does**:
1. Takes a list of numbers
2. Chains `double` for each number
3. Collects all the results

**Purpose**: Show how to process lists/batches with task chaining.

**Key Pattern**:
```python
for num in numbers:
    doubled = await double(num)  # ← Task chaining in a loop
```

---

### `calculate_and_process(a: int, b: int, more_numbers: list[int]) -> dict`

Demonstrates a multi-step workflow with multiple chained task runs.

**What it does**:
1. Chains `add_doubled_numbers`
2. Chains `process_numbers`
3. Combines the results

**Purpose**: Show how to chain multiple task runs to create complex workflows.

**Key Pattern**:
```python
step1 = await add_doubled_numbers(a, b)   # ← First chained run
step2 = await process_numbers(numbers)    # ← Second chained run
# Combine results
```

## Key Concepts

### The `@app.task` Decorator

Every workflow function needs the `@app.task` decorator:

```python
from render_sdk import Workflows

app = Workflows()

@app.task
def my_task():
    return "Hello World"

app.start()
```

### The `async` Keyword

Tasks that chain other tasks must be `async`:

```python
@app.task
async def orchestrator():
    result = await task_b()  # ← Chains another task
    return result
```

### The `await` Keyword

Use `await` to chain a run of another task:

```python
result = await task_name(arguments)
```

Without `await`, you're just calling a regular Python function!

### Task Registration

All `@app.task` decorated functions are registered when defined. Call `app.start()` at the end of your module to start the workflow service and make all registered tasks available for execution.

## Common Patterns

### Pattern 1: Sequential Task Chaining

Execute chained runs one after another:

```python
@app.task
async def sequential():
    step1 = await task_a()
    step2 = await task_b(step1)  # Uses result from step1
    step3 = await task_c(step2)  # Uses result from step2
    return step3
```

### Pattern 2: Independent Chained Runs

Execute chained runs where order doesn't matter:

```python
@app.task
async def independent():
    result_a = await task_a()
    result_b = await task_b()
    return combine(result_a, result_b)
```

### Pattern 3: Task Chaining in a Loop

Process a list by chaining a task run for each item:

```python
@app.task
async def batch_process(items: list):
    results = []
    for item in items:
        result = await process_item(item)
        results.append(result)
    return results
```

### Pattern 4: Nested Task Chaining

Tasks can chain other tasks, which can chain additional tasks:

```python
@app.task
async def level_1():
    return await level_2()

@app.task
async def level_2():
    return await level_3()

@app.task
def level_3():
    return "Done!"
```

## Next Steps

Once you understand this example, check out:

1. **ETL Job** - Learn data processing patterns with CSV files
2. **File Processing** - Learn parallel execution with `asyncio.gather()`
3. **Data Pipeline** - Learn complex multi-stage workflows
4. **OpenAI Agent** - Learn advanced patterns with AI integration
5. **File Analyzer** - Learn how to call workflows from APIs using Client SDK

## Troubleshooting

### "Task not found" error

Make sure:
- The service is deployed and running
- The task name matches exactly (case-sensitive)
- You're using the correct service slug

### Import errors

Make sure:
- `requirements.txt` includes `render-sdk>=0.5.0`
- Build command is running correctly
- Python version is 3.10 or higher

### Task chaining calls not working

Make sure:
- Your task function is marked `async`
- You're using `await` before the task call
- Both tasks are decorated with `@app.task`

## Important Notes

- **SDK languages**: Workflows support Python and TypeScript; this repo's examples are Python.
- **No Blueprint Support**: Workflows don't support render.yaml blueprint configuration
- **Service Type**: Deploy as a Workflow service on Render (not Background Worker or Web Service)
- **Async Functions**: Tasks that chain other tasks must be declared as `async`

## Resources

- [Render Workflows Documentation](https://docs.render.com/workflows)
- [Render SDK on PyPI](https://pypi.org/project/render-sdk/)
- [Render Dashboard](https://dashboard.render.com/)

---

**Start simple, build powerful workflows!**
