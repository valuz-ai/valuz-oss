import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["**/dist/**", "**/node_modules/**", ".turbo/**"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
    },
  },
  // ─────────────────────────────────────────────────────────────────
  // 包边界硬门禁（Slice 2）
  //
  // 拓扑：shared ← ui ← core ← apps；apps 互不依赖。
  // main / preload 是 Node 端，不能拉 React/Zustand。CLI 同理。
  // 类型 import 在编译时被擦除，不计入运行时边界（allowTypeImports）。
  // ─────────────────────────────────────────────────────────────────
  {
    files: ["packages/shared/src/**/*.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [
                "@valuz/core",
                "@valuz/core/*",
                "@valuz/ui",
                "@valuz/ui/*",
              ],
              message:
                "@valuz/shared 是最底层包，禁止 import 任何内部 @valuz/* 包",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["packages/ui/src/**/*.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@valuz/core", "@valuz/core/*"],
              message:
                "@valuz/ui 不允许 import @valuz/core 运行时（store / transport / hook 等）；类型用 import type 通过——堵的是状态耦合，不是类型依赖",
              allowTypeImports: true,
            },
          ],
        },
      ],
    },
  },
  {
    files: ["packages/core/src/**/*.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@valuz/ui", "@valuz/ui/*"],
              message: "@valuz/core 不允许 import @valuz/ui",
            },
          ],
        },
      ],
    },
  },
  {
    files: [
      "apps/desktop/src/main/**/*.ts",
      "apps/desktop/src/preload/**/*.ts",
    ],
    rules: {
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@valuz/core", "@valuz/core/*"],
              message:
                "desktop main/preload 不允许 import @valuz/core 运行时（含 React/Zustand）；类型用 import type 通过",
              allowTypeImports: true,
            },
            {
              group: ["@valuz/ui", "@valuz/ui/*"],
              message: "desktop main/preload 不允许 import @valuz/ui",
              allowTypeImports: true,
            },
            {
              group: [
                "react",
                "react-dom",
                "react-router-dom",
                "zustand",
                "sonner",
              ],
              message: "desktop main/preload 不允许 import React/路由/状态管理",
              allowTypeImports: true,
            },
          ],
        },
      ],
    },
  },
  {
    files: ["apps/cli/**/*.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [
                "react",
                "react-dom",
                "react-router-dom",
                "zustand",
                "@valuz/ui",
                "@valuz/ui/*",
              ],
              message: "CLI 不允许 import React / UI / Zustand",
            },
            {
              group: ["@valuz/core", "@valuz/core/*"],
              message:
                "CLI 不允许 import @valuz/core；通用工具放 @valuz/shared（Slice 6 落地）",
            },
          ],
        },
      ],
    },
  },
  // ─────────────────────────────────────────────────────────────────
  // 历史 override（保留）
  // ─────────────────────────────────────────────────────────────────
  {
    files: [
      "packages/ui/src/components/ui/**/*.{ts,tsx}",
      "packages/ui/src/hooks/use-mobile.ts",
    ],
    rules: {
      "react-refresh/only-export-components": "off",
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/purity": "off",
    },
  },
  {
    files: ["packages/ui/src/components/ui/combobox.tsx"],
    rules: {
      "@typescript-eslint/no-unused-vars": "off",
    },
  },
);
