import { defineConfig } from "drizzle-kit";

export default defineConfig({
  out: "./drizzle",
  schema: "./examples/d1/db/schema.ts",
  dialect: "sqlite",
});
