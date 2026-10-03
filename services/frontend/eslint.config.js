import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      // Preserve the React Hooks checks enforced before plugin 7. The new
      // recommended preset also enables React Compiler migration rules, which
      // should be adopted separately rather than bundled with a security patch.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      // Allow unused variables that start with underscore
      "@typescript-eslint/no-unused-vars": [
        "warn",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
        },
      ],
      // Allow any type in some cases
      "@typescript-eslint/no-explicit-any": "warn",
      // Allow empty interfaces for component props
      "@typescript-eslint/no-empty-interface": "off",
      // ESLint 10 added these to its recommended set. Keep this dependency
      // security update behavior-neutral; adopt the new rules separately.
      "no-useless-assignment": "off",
      "preserve-caught-error": "off",
    },
  },
);
