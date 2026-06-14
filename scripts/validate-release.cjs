const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')
const checks = [
  ['renderer build', 'apps/desktop/dist/index.html'],
  ['Electron main', 'apps/desktop/electron/main.cjs'],
  ['Electron preload', 'apps/desktop/electron/preload.cjs'],
  ['Terminal manager', 'apps/desktop/electron/terminal-manager.cjs'],
  ['Core entrypoint', 'apps/core/src/codex_core/main.py'],
  ['Core project metadata', 'apps/core/pyproject.toml'],
  ['macOS entitlements', 'packaging/entitlements.mac.plist'],
  ['Core bundle script', 'scripts/build-core.cjs'],
  ['Cross-platform packaging workflow', '.github/workflows/package.yml'],
]

let failed = false
for (const [name, relative] of checks) {
  const available = fs.existsSync(path.join(root, relative))
  console.log(`${available ? 'PASS' : 'FAIL'} ${name}: ${relative}`)
  failed ||= !available
}

const signingConfigured = Boolean(process.env.CSC_NAME || process.env.CSC_LINK)
console.log(`${signingConfigured ? 'PASS' : 'INFO'} macOS signing identity: ${signingConfigured ? 'configured' : 'not configured'}`)
console.log(`INFO current packaging host: ${process.platform}-${process.arch}`)
const packageConfig = require(path.join(root, 'apps/desktop/package.json')).build
console.log(`${packageConfig?.mac && packageConfig?.win && packageConfig?.linux ? 'PASS' : 'FAIL'} platform package targets`)
failed ||= !(packageConfig?.mac && packageConfig?.win && packageConfig?.linux)

try {
  require('node-pty')
  const nativeBinary = path.join(root, 'node_modules/node-pty/build/Release/pty.node')
  const nativeAvailable = fs.existsSync(nativeBinary)
  console.log(`${nativeAvailable ? 'PASS' : 'FAIL'} node-pty native binary: ${path.relative(root, nativeBinary)}`)
  failed ||= !nativeAvailable
} catch (error) {
  console.log(`FAIL node-pty load: ${error instanceof Error ? error.message : String(error)}`)
  failed = true
}

if (failed) process.exitCode = 1
