from render_sdk import Workflows, Retry
import asyncio
import random

app = Workflows()


@app.task
def calculate_square(a: int) -> int:
    return a * a


@app.task
async def sum_squares(a: int, b: int) -> int:
    result1, result2 = await asyncio.gather(
        calculate_square(a),
        calculate_square(b),
    )
    return result1 + result2


@app.task(
    retry=Retry(
        max_retries=3,
        wait_duration_ms=1000,
        backoff_scaling=1.5,
    )
)
def flip_coin() -> str:
    if random.random() < 0.5:
        raise Exception("Flipped tails! Retrying.")
    return "Flipped heads!"


if __name__ == "__main__":
    app.start()
