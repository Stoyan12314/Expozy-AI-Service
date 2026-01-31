// ajv_validate.mjs
import fs from "node:fs";
import process from "node:process";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

function finish(obj, exitCode = 0) {
  console.log(JSON.stringify(obj));
  process.exit(exitCode);
}

const [schemaPath, dataPath] = process.argv.slice(2);

if (!schemaPath || !dataPath) {
  finish({ valid: false, fatal: true, errors: [{ message: "Usage: node ajv_validate.mjs <schema> <data>" }] }, 2);
}

let schema, data;

try {
  schema = JSON.parse(fs.readFileSync(schemaPath, "utf-8"));
} catch (e) {
  finish({ valid: false, fatal: true, errors: [{ message: `Schema read error: ${e.message}` }] }, 2);
}

try {
  data = JSON.parse(fs.readFileSync(dataPath, "utf-8"));
} catch (e) {
  finish({ valid: false, fatal: true, errors: [{ message: `Data read error: ${e.message}` }] }, 2);
}

try {
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  addFormats(ajv);
  
  const validate = ajv.compile(schema);
  const valid = validate(data);
  
  finish({ valid: Boolean(valid), fatal: false, errors: validate.errors || [] }, valid ? 0 : 1);
} catch (e) {
  finish({ valid: false, fatal: true, errors: [{ message: `Ajv error: ${e.message}` }] }, 3);
}
