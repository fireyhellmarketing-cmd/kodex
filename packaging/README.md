# Codex Distribution

The development runtime is release-validated with:

```sh
npm run release:validate
```

Production builds use PyInstaller to bundle Kodex Core and electron-builder to
produce native artifacts:

```sh
npm run package:mac -w apps/desktop
npm run package:win -w apps/desktop
npm run package:linux -w apps/desktop
```

The GitHub Actions packaging workflow builds on native macOS, Windows, and Linux
runners. macOS signing/notarization uses the standard `CSC_*` and `APPLE_*`
secrets. Set `KODEX_UPDATE_URL` at runtime to enable signed generic-provider
update checks; publishing remains an explicit release operation.
