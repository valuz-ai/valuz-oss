# ripgrep — vendored binary

Source: https://github.com/BurntSushi/ripgrep/releases/tag/15.1.0
Archive: ripgrep-15.1.0-x86_64-apple-darwin.tar.gz
License: MIT (see https://github.com/BurntSushi/ripgrep/blob/master/LICENSE-MIT)

x86_64 (Intel) macOS build. Used by the backend's `docs_embedded`
integration via the `VALUZ_RG_PATH` env injected from the Electron
sidecar. `scripts/build-desktop.sh` stages this binary into `libexec/rg`
at build time (no network needed).

## Refresh

```
bash scripts/download-rg.sh x86_64-apple-darwin
# then copy the result into backend/vendor/rg/darwin-amd64/rg
# and regenerate SHA256SUMS:
( cd backend/vendor/rg/darwin-amd64 && shasum -a 256 rg > SHA256SUMS )
```
