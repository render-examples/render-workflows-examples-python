import "dotenv/config";
import { task } from "@renderinc/sdk/workflows";

// Subtask: doubles a number
const double = task({ name: "double" }, function double(x: number): number {
  console.log(`[TASK] Doubling ${x}`);
  const result = x * 2;
  console.log(`[TASK] Result: ${result}`);
  return result;
});

// Subtask (also callable as root): doubles two numbers and sums them
const addDoubledNumbers = task(
  { name: "addDoubledNumbers" },
  async function addDoubledNumbers(a: number, b: number) {
    console.log(`[WORKFLOW] Starting: addDoubledNumbers(${a}, ${b})`);

    const doubledA = await double(a);
    const doubledB = await double(b);
    const total = doubledA + doubledB;

    const result = {
      original_numbers: [a, b],
      doubled_numbers: [doubledA, doubledB],
      sum_of_doubled: total,
      explanation: `${a} doubled is ${doubledA}, ${b} doubled is ${doubledB}, sum is ${total}`,
    };

    console.log("[WORKFLOW] Complete:", result);
    return result;
  },
);

// Subtask (also callable as root): doubles each number in a list
const processNumbers = task(
  { name: "processNumbers" },
  async function processNumbers(...numbers: number[]) {
    console.log(`[WORKFLOW] Starting: processNumbers(${numbers})`);

    const doubledResults: number[] = [];

    for (let i = 0; i < numbers.length; i++) {
      console.log(
        `[WORKFLOW] Processing item ${i + 1}/${numbers.length}: ${numbers[i]}`,
      );
      const doubled = await double(numbers[i]);
      doubledResults.push(doubled);
    }

    const result = {
      original_numbers: numbers,
      doubled_numbers: doubledResults,
      count: numbers.length,
      explanation: `Processed ${numbers.length} numbers through the double subtask`,
    };

    console.log("[WORKFLOW] Complete:", result);
    return result;
  },
);

// Root task: chains addDoubledNumbers and processNumbers
task(
  { name: "calculateAndProcess" },
  async function calculateAndProcess(
    a: number,
    b: number,
    ...moreNumbers: number[]
  ) {
    console.log("[WORKFLOW] Starting multi-step workflow");

    console.log("[WORKFLOW] Step 1: Adding doubled numbers");
    const step1Result = await addDoubledNumbers(a, b);

    console.log("[WORKFLOW] Step 2: Processing number list");
    const step2Result = await processNumbers(...moreNumbers);

    console.log("[WORKFLOW] Step 3: Combining results");
    const finalResult = {
      step1_sum: step1Result.sum_of_doubled,
      step2_doubled: step2Result.doubled_numbers,
      total_operations: 2 + moreNumbers.length,
      summary: `Added doubled ${a} and ${b}, then doubled ${moreNumbers.length} more numbers`,
    };

    console.log("[WORKFLOW] Multi-step workflow complete");
    return finalResult;
  },
);
