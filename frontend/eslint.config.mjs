// Flat config. ESLint 9 dropped `.eslintrc.json`, and eslint-config-next 16
// (which pairs with Next 16) ships flat-config arrays rather than presets a
// legacy `extends` can name.
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

export default [
  // Build output and dependencies. `ignores` in a config object with no other
  // key is the flat-config equivalent of the old `ignorePatterns`.
  { ignores: ["node_modules/**", ".next/**", "coverage/**"] },
  ...nextCoreWebVitals,
  ...nextTypeScript,
];
