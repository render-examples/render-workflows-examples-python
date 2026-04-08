# Hello World - Render Workflows (Python)

This hello-world example demonstrates three foundational workflow patterns:

- A minimal task definition (`calculate_square`)
- A task that chains runs of another task (`sum_squares`)
- A task with custom retry behavior (`flip_coin`)

## What You'll Learn

- How to define tasks with `@app.task`
- How to chain task runs using `await` and `asyncio.gather`
- How to customize retry behavior with `Retry`

## Example Tasks

### `calculate_square(a: int) -> int`

The smallest possible task: takes one integer and returns its square.

### `sum_squares(a: int, b: int) -> int`

Chains two runs of `calculate_square` and sums the results.

It uses `asyncio.gather(...)` to chain the two runs in parallel:

```python
result1, result2 = await asyncio.gather(
    calculate_square(a),
    calculate_square(b),
)
```

### `flip_coin() -> str`

Simulates a coin flip:

- Heads: Returns success
- Tails: Raises an error to trigger retry

Retry policy in this example:

- max retries: `3`
- wait duration: `1000ms`
- backoff scaling: `1.5`

## Local Development

### Prerequisites

- Python 3.10+

### Run locally

> Make sure you've installed the latest version of the [Render CLI](https://render.com/docs/cli).

1. From this template's root, start the local task server:

    ```bash
    pip install -r requirements.txt
    render workflows dev -- python main.py
    ```

2. In a separate terminal, trigger task runs:

    ```bash
    render workflows tasks start calculate_square --local --input='{"a": 5}'
    render workflows tasks start sum_squares --local --input='{"a": 3, "b": 4}'
    render workflows tasks start flip_coin --local --input='{}'
    ```

Expected behavior:

- `calculate_square` with `a=5` returns `25`
- `sum_squares` with `a=3,b=4` returns `25`
- `flip_coin` may fail and retry before succeeding

## Deploying to Render

Configure your Workflow service with:

| Option | Value |
| --- | --- |
| Build command | `pip install -r requirements.txt` |
| Start command | `python main.py` |

## Key Concepts

### Task registration

Any function decorated with `@app.task` is registered when your service starts via `app.start()`.

### Subtasks

Inside an `async` task, calling `await other_task(...)` runs that task as a subtask.

### Retries

Use `@app.task(retry=Retry(...))` when transient failures should be retried automatically.

## Troubleshooting

### "Task not found"

- Confirm the service is running
- Verify task names exactly match: `calculate_square`, `sum_squares`, `flip_coin`

### Import or dependency issues

- Confirm dependency install completed from `requirements.txt`
- Confirm Python version is 3.10+

## Resources

- [Render Workflows documentation](https://render.com/docs/workflows)
- [Workflows tutorial](https://render.com/docs/workflows-tutorial)
- [Local development guide](https://render.com/docs/workflows-local-development)
