import globals from "globals";
import pluginVue from "eslint-plugin-vue";
import {
  defineConfigWithVueTs,
  vueTsConfigs,
} from "@vue/eslint-config-typescript";

export default defineConfigWithVueTs(
  {
    name: "app/files-to-lint",
    ignores: ["**/dist/**", "**/dist-ssr/**", "**/coverage/**", "node_modules/**"],
  },
  {
    name: "app/language-opts",
    languageOptions: {
      globals: {
        ...globals.browser,
      },
    },
  },
  pluginVue.configs["flat/essential"],
  vueTsConfigs.recommended,
  {
    name: "app/rules",
    rules: {
      "vue/multi-word-component-names": "off",
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
);
