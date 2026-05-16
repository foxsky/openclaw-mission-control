module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  transform: { "^.+\\.tsx?$": "ts-jest" },
  testMatch: ["**/__tests__/**/*.test.ts"],
  collectCoverageFrom: ["src/**/*.ts", "!src/__tests__/**"]
};
