import js from '@eslint/js'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  {
    ignores: ['dist/**', 'electron/**'],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      globals: {
        document: 'readonly',
        fetch: 'readonly',
        RequestInit: 'readonly',
        window: 'readonly',
      },
    },
  },
)
