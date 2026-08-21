import { readFile } from "node:fs/promises";
import { JsonValidator } from "@cyclonedx/cyclonedx-library/Validation";
import { Version } from "@cyclonedx/cyclonedx-library/Spec";

const path = process.argv[2];
if (!path) {
  console.error("usage: node scripts/validate_cyclonedx.mjs SBOM_PATH");
  process.exit(2);
}

const document = await readFile(path, "utf8");
const result = await new JsonValidator(Version.v1dot6).validate(document);
if (result !== null) {
  console.error(JSON.stringify(result, null, 2));
  process.exit(1);
}

console.log(`CycloneDX 1.6 schema valid: ${path}`);
