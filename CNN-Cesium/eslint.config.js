import globals from 'globals'
import pluginJs from '@eslint/js'
import tseslint from 'typescript-eslint'
import pluginVue from 'eslint-plugin-vue'

export default [
  // 指定文件匹配模式 和语言选项
  { files: ['**/*.{js,mjs,cjs,ts,vue}'] }, //["**/*.vue"]

  // 指定全局变量和环境
  {
    languageOptions: {
      globals: {
        ...globals.browser,
        process: 'readonly' // 明确声明 process 是只读的全局变量
      },
      ecmaVersion: 12, // 使用最新的 ECMAScript 语法
      sourceType: 'module', // 代码是 ECMAScript 模块
      parserOptions: { parser: tseslint.parser } // 使用 TypeScript 解析器
    }
  },

  // 使用的扩展配置 解析器选项
  pluginJs.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/essential'],

  // 自定义规则
  {
    rules: {
      'no-console': 'warn', // 生产环境中警告 console 使用，开发环境中关闭规则
      'no-debugger': 'warn', // 生产环境中警告 debugger 使用，开发环境中关闭规则
      'indent': ['warn', 2, { SwitchCase: 1 }], // 缩进使用 2 个空格 而不是 4 个  error
      'linebreak-style': ['warn', 'windows'], // 使用 windows 风格的换行符
      'quotes': ['warn', 'single'], // 使用单引号
      'semi': ['warn', 'never'], // 语句末尾不加分号
      'no-undef': 'off', // 关闭未定义变量警告
      // 'no-unused-vars': 'off', // 关闭未使用变量警告
      // '@typescript-eslint/no-unused-vars': 'off', // 关闭未使用变量警告
      'vue/multi-word-component-names': 'off' //Vue 组件的名称应该是多词的，以提高可读性和维护性
    }
  },
  // 忽略文件
  {
    ignores: [
      '**/dist',
      './src/main.ts',
      '.vscode',
      '.idea',
      '*.sh',
      '**/node_modules',
      '*.md',
      '*.woff',
      '*.woff',
      '*.ttf',
      'yarn.lock',
      'package-lock.json',
      '/public',
      '/docs',
      '**/output',
      '.husky',
      '.local',
      '/bin',
      'Dockerfile'
    ]
  }
]
