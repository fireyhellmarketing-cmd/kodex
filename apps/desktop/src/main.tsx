import React from 'react'
import ReactDOM from 'react-dom/client'
import { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker'
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker'
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'
import typescriptWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker'
import App from './App'
import './styles.css'

type MonacoWorkerEnvironment = {
  getWorker: (_moduleId: string, label: string) => Worker
}

const workerScope = self as typeof self & {
  MonacoEnvironment: MonacoWorkerEnvironment
}

workerScope.MonacoEnvironment = {
  getWorker(_moduleId, label) {
    if (label === 'json') return new jsonWorker()
    if (label === 'css' || label === 'scss' || label === 'less') return new cssWorker()
    if (label === 'html' || label === 'handlebars' || label === 'razor') return new htmlWorker()
    if (label === 'typescript' || label === 'javascript') return new typescriptWorker()
    return new editorWorker()
  },
}

loader.config({ monaco })

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
