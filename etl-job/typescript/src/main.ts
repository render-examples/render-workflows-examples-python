import "dotenv/config";
import { task } from "@renderinc/sdk/workflows";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

interface Record {
  id?: string;
  name?: string;
  email?: string;
  age?: string;
  country?: string;
  [key: string]: string | undefined;
}

interface ValidatedRecord {
  id: string | undefined;
  name: string;
  email: string | null;
  age: number | null;
  country: string;
  is_valid: boolean;
  errors: string[];
  warnings: string[];
}

const retry = {
  maxRetries: 3,
  waitDurationMs: 1000,
  factor: 1.5,
};

// Subtask: extract rows from a CSV file
const extractCsvData = task(
  { name: "extractCsvData", retry },
  function extractCsvData(filePath: string): Record[] {
    console.log(`[EXTRACT] Reading CSV file: ${filePath}`);

    const fullPath = resolve(filePath);
    if (!existsSync(fullPath)) {
      console.warn("[EXTRACT] File not found, using sample data");
      return [
        { id: "1", name: "Alice", email: "alice@example.com", age: "28", country: "USA" },
        { id: "2", name: "Bob", email: "bob@example.com", age: "34", country: "Canada" },
        { id: "3", name: "Charlie", email: "invalid-email", age: "invalid", country: "UK" },
      ];
    }

    const content = readFileSync(fullPath, "utf-8");
    const lines = content.trim().split("\n");
    if (lines.length < 2) return [];

    const headers = lines[0].split(",").map((h) => h.trim());
    const records: Record[] = [];

    for (let i = 1; i < lines.length; i++) {
      const values = lines[i].split(",").map((v) => v.trim());
      const record: Record = {};
      headers.forEach((h, idx) => {
        record[h] = values[idx];
      });
      records.push(record);
    }

    console.log(`[EXTRACT] Successfully extracted ${records.length} records`);
    return records;
  },
);

// Subtask: validate and clean a single record
const validateRecord = task(
  { name: "validateRecord", retry },
  function validateRecord(record: Record): ValidatedRecord {
    console.log(`[TRANSFORM] Validating record ID: ${record.id ?? "unknown"}`);

    const errors: string[] = [];
    const warnings: string[] = [];

    if (!record.name) errors.push("Missing name");
    if (!record.email) errors.push("Missing email");

    const email = record.email ?? "";
    if (email && !email.includes("@")) errors.push("Invalid email format");

    let age: number | null = null;
    try {
      age = parseInt(record.age ?? "0", 10);
      if (isNaN(age) || age < 0 || age > 120) {
        errors.push(`Invalid age: ${record.age}`);
        age = null;
      }
    } catch {
      errors.push(`Age must be a number: ${record.age}`);
    }

    const cleaned: ValidatedRecord = {
      id: record.id,
      name: (record.name ?? "").trim(),
      email: email ? email.toLowerCase().trim() : null,
      age,
      country: (record.country ?? "").trim(),
      is_valid: errors.length === 0,
      errors,
      warnings,
    };

    const status = cleaned.is_valid ? "VALID" : "INVALID";
    console.log(`[TRANSFORM] Record ${record.id}: ${status}`);
    return cleaned;
  },
);

// Subtask: validate a batch of records by calling validateRecord for each
const transformBatch = task(
  { name: "transformBatch", retry },
  async function transformBatch(records: Record[]) {
    console.log(`[TRANSFORM] Starting batch transformation of ${records.length} records`);

    const validRecords: ValidatedRecord[] = [];
    const invalidRecords: ValidatedRecord[] = [];

    for (let i = 0; i < records.length; i++) {
      console.log(`[TRANSFORM] Processing record ${i + 1}/${records.length}`);
      const validated = await validateRecord(records[i]);

      if (validated.is_valid) {
        validRecords.push(validated);
      } else {
        invalidRecords.push(validated);
      }
    }

    const result = {
      valid_records: validRecords,
      invalid_records: invalidRecords,
      total_processed: records.length,
      valid_count: validRecords.length,
      invalid_count: invalidRecords.length,
      success_rate: records.length > 0 ? validRecords.length / records.length : 0,
    };

    console.log(
      `[TRANSFORM] Batch complete: ${result.valid_count} valid, ${result.invalid_count} invalid`,
    );
    return result;
  },
);

// Subtask: compute statistics from validated records
const computeStatistics = task(
  { name: "computeStatistics", retry },
  function computeStatistics(validRecords: ValidatedRecord[]) {
    console.log(`[LOAD] Computing statistics for ${validRecords.length} records`);

    if (validRecords.length === 0) {
      console.warn("[LOAD] No valid records to analyze");
      return { total_records: 0, country_distribution: {}, age_stats: {} };
    }

    const countryCounts: { [key: string]: number } = {};
    for (const record of validRecords) {
      const country = record.country || "Unknown";
      countryCounts[country] = (countryCounts[country] ?? 0) + 1;
    }

    const ages = validRecords
      .filter((r) => r.age !== null)
      .map((r) => r.age as number);

    const ageStats =
      ages.length > 0
        ? {
            min: Math.min(...ages),
            max: Math.max(...ages),
            average: ages.reduce((a, b) => a + b, 0) / ages.length,
            count: ages.length,
          }
        : {};

    const statistics = {
      total_records: validRecords.length,
      country_distribution: countryCounts,
      age_stats: ageStats,
      timestamp: new Date().toISOString(),
    };

    console.log("[LOAD] Statistics computed successfully");
    return statistics;
  },
);

// Root task: orchestrates the full ETL pipeline
task(
  { name: "runEtlPipeline", retry, timeoutSeconds: 300 },
  async function runEtlPipeline(sourceFile: string) {
    console.log("=".repeat(80));
    console.log("[PIPELINE] Starting ETL Pipeline");
    console.log(`[PIPELINE] Source: ${sourceFile}`);
    console.log("=".repeat(80));

    try {
      console.log("[PIPELINE] Stage 1/3: EXTRACT");
      const rawRecords = await extractCsvData(sourceFile);
      console.log(`[PIPELINE] Extracted ${rawRecords.length} records`);

      console.log("[PIPELINE] Stage 2/3: TRANSFORM");
      const transformResult = await transformBatch(rawRecords);
      console.log(
        `[PIPELINE] Transformation complete: ${(transformResult.success_rate * 100).toFixed(1)}% success rate`,
      );

      console.log("[PIPELINE] Stage 3/3: LOAD");
      const statistics = await computeStatistics(transformResult.valid_records);
      console.log("[PIPELINE] Statistics computed");

      const pipelineResult = {
        status: "success",
        extract: {
          records_extracted: rawRecords.length,
          source: sourceFile,
        },
        transform: {
          valid_count: transformResult.valid_count,
          invalid_count: transformResult.invalid_count,
          success_rate: transformResult.success_rate,
          invalid_records: transformResult.invalid_records,
        },
        load: { statistics },
        completed_at: new Date().toISOString(),
      };

      console.log("=".repeat(80));
      console.log("[PIPELINE] ETL Pipeline Complete!");
      console.log(`[PIPELINE] Processed: ${rawRecords.length} records`);
      console.log(`[PIPELINE] Valid: ${transformResult.valid_count} records`);
      console.log(`[PIPELINE] Invalid: ${transformResult.invalid_count} records`);
      console.log("=".repeat(80));

      return pipelineResult;
    } catch (error) {
      console.error(`[PIPELINE] ETL Pipeline failed: ${error}`);
      return {
        status: "failed",
        error: String(error),
        failed_at: new Date().toISOString(),
      };
    }
  },
);
