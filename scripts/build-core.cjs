const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')
const core = path.join(root, 'apps', 'core')
const output = path.join(core, 'dist')
const work = path.join(root, '.kodex-agent', 'pyinstaller')
const environment = path.join(root, '.kodex-agent', 'bundle-venv')
const hostPython = process.platform === 'win32' ? 'python' : 'python3'
const bundledPython = process.platform === 'win32'
  ? path.join(environment, 'Scripts', 'python.exe')
  : path.join(environment, 'bin', 'python')
fs.rmSync(output, { recursive: true, force: true })
fs.rmSync(environment, { recursive: true, force: true })
fs.mkdirSync(work, { recursive: true })

const venv = spawnSync(hostPython, ['-m', 'venv', environment], {
  cwd: root,
  stdio: 'inherit',
})
if (venv.status !== 0) process.exit(venv.status || 1)

const install = spawnSync(
  bundledPython,
  ['-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', 'pip', 'pyinstaller>=6.10', core],
  { cwd: root, stdio: 'inherit' },
)
if (install.status !== 0) process.exit(install.status || 1)
const pythonVersion = spawnSync(
  bundledPython,
  ['-c', 'import sys; print(sys.version_info[:2] >= (3, 10))'],
  { cwd: root, encoding: 'utf8' },
).stdout.trim()
const treeSitterPackage = pythonVersion === 'True'
  ? 'tree_sitter_language_pack'
  : 'tree_sitter_languages'

const entry = path.join(core, 'kodex_core_entry.py')
const build = spawnSync(
  bundledPython,
  [
    '-m',
    'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--name',
    'kodex-core',
    '--distpath',
    output,
    '--workpath',
    work,
    '--specpath',
    work,
    '--collect-all',
    treeSitterPackage,
    entry,
  ],
  { cwd: root, stdio: 'inherit' },
)
if (build.status !== 0) process.exit(build.status || 1)
