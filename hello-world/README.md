# Hello World - Getting Started with Render Workflows

The simplest possible workflow example to help you understand the basics of Render Workflows. Perfect for beginners!

## What you'll learn

This example teaches the fundamental concepts:
- **What is a task?** A function that can be executed as a workflow
- **What is a subtask?** A task called by another task using `await`
- **How to orchestrate:** Combining multiple tasks to create workflows
- **How to deploy:** Getting your first workflow running on Render

## Use case

Simple number processing to demonstrate workflow patterns without complex business logic. If you can understand this example, you can build any workflow!

## Workflow structure

```
calculateAndProcess (multi-step orchestrator)
  ├── addDoubledNumbers
  │   ├── double (subtask #1)
  │   └── double (subtask #2)
  └── processNumbers
      ├── double (subtask for item 1)
      ├── double (subtask for item 2)
      └── double (subtask for item N)
```

## Understanding tasks and subtasks

### What is a task?

A **task** is a function registered with the workflow runtime. It becomes a workflow step that Render can execute.

**Python:**

```python
from render_sdk import Workflows

app = Workflows(auto_start=True)

@app.task
def double(x: int) -> int:
    return x * 2
```

**TypeScript:**

```typescript
import { task } from "@renderinc/sdk/workflows";

const double = task({ name: "double" }, function double(x: number): number {
  return x * 2;
});
```

### What is a subtask?

A **subtask** is when one task calls another task using `await`:

**Python:**

```python
@app.task
async def add_doubled_numbers(a: int, b: int) -> dict:
    doubled_a = await double(a)   # subtask call
    doubled_b = await double(b)   # subtask call
    return {"sum": doubled_a + doubled_b}
```

**TypeScript:**

```typescript
const addDoubledNumbers = task(
  { name: "addDoubledNumbers" },
  async function addDoubledNumbers(a: number, b: number) {
    const doubledA = await double(a);   // subtask call
    const doubledB = await double(b);   // subtask call
    return { sum: doubledA + doubledB };
  },
);
```

### Why use subtasks?

1. **Reusability**: Write `double` once, use it everywhere
2. **Composition**: Build complex workflows from simple building blocks
3. **Visibility**: Render shows each subtask execution in the dashboard
4. **Testing**: Test individual tasks independently

## Local development

### Prerequisites

- Python 3.10+ (for the Python version)
- Node.js 18+ (for the TypeScript version)

### Python

```bash
cd hello-world/python
pip install -r requirements.txt
python main.py
```

### TypeScript

```bash
cd hello-world/typescript
npm install
npm run dev
```

## Deploying to Render

### Service configuration

**Service type**: Workflow

**Python:**

| Setting | Value |
|---|---|
| Build command | `cd hello-world/python && pip install -r requirements.txt` |
| Start command | `cd hello-world/python && python main.py` |

**TypeScript:**

| Setting | Value |
|---|---|
| Build command | `cd hello-world/typescript && npm install && npm run build` |
| Start command | `cd hello-world/typescript && npm start` |

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

Once deployed, test your workflows directly in the Render Dashboard:

1. Go to your Workflow service in Render Dashboard.
1. Click **Manual Run** or **Start Task**.
1. Select the task you want to test.
1. Enter the task input as JSON.
1. Click **Start task**.

### Example task inputs

**Test the basic task** (`double`):

```json
5
```

Expected output: `10`

---

**Test subtask calling** (`addDoubledNumbers`):

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

---

**Test subtask in a loop** (`processNumbers`):

```json
[1, 2, 3, 4, 5]
```

Expected output:

```json
{
  "original_numbers": [1, 2, 3, 4, 5],
  "doubled_numbers": [2, 4, 6, 8, 10],
  "count": 5,
  "explanation": "Processed 5 numbers through the double subtask"
}
```

---

**Test multi-step workflow** (`calculateAndProcess`):

```json
[2, 3, 10, 20, 30]
```

This calls `addDoubledNumbers` and `processNumbers` as subtasks, which in turn call `double` multiple times.

## Tasks explained

### `double(x) -> number`

The simplest possible task. Takes a number, doubles it, returns the result.

### `addDoubledNumbers(a, b) -> object`

Demonstrates the fundamental subtask pattern: calls `double(a)` and `double(b)` as subtasks, then sums the results.

### `processNumbers(...numbers) -> object`

Demonstrates calling a subtask in a loop: calls `double` for each number in the list.

### `calculateAndProcess(a, b, ...moreNumbers) -> object`

Demonstrates a multi-step workflow: chains `addDoubledNumbers` and `processNumbers` as subtasks, combining their results.

## Common patterns

### Sequential subtasks

```python
# Python
@app.task
async def sequential():
    step1 = await task_a()
    step2 = await task_b(step1)
    return step2
```

```typescript
// TypeScript
task({ name: "sequential" }, async function sequential() {
  const step1 = await taskA();
  const step2 = await taskB(step1);
  return step2;
});
```

### Subtasks in a loop

```python
# Python
@app.task
async def batch_process(items: list):
    results = []
    for item in items:
        result = await process_item(item)
        results.append(result)
    return results
```

```typescript
// TypeScript
task({ name: "batchProcess" }, async function batchProcess(items: string[]) {
  const results = [];
  for (const item of items) {
    results.push(await processItem(item));
  }
  return results;
});
```

## Next steps

Once you understand this example, check out:

1. **ETL Job** - Data processing patterns with CSV files
2. **File Processing** - Parallel execution with fan-out
3. **Data Pipeline** - Complex multi-stage workflows
4. **OpenAI Agent** - Advanced patterns with AI integration
5. **File Analyzer** - Calling workflows from APIs using the Client SDK

## Troubleshooting

### "Task not found" error

- Verify that the service is deployed and running.
- Check that the task name matches exactly (case-sensitive).
- Confirm you're using the correct service slug.

### Import errors

- **Python**: Verify that `requirements.txt` includes `render-sdk>=0.2.0` and Python 3.10+ is installed.
- **TypeScript**: Run `npm install` and verify Node.js 18+ is installed.

### Subtask calls not working

- **Python**: Verify your task function is `async` and you're using `await`.
- **TypeScript**: Verify you're using `await` on subtask calls.

## Resources

- [Render Workflows documentation](https://docs.render.com/workflows)
- [Render SDK on PyPI](https://pypi.org/project/render-sdk/)
- [Render SDK on npm](https://www.npmjs.com/package/@renderinc/sdk)
- [Render Dashboard](https://dashboard.render.com/)
