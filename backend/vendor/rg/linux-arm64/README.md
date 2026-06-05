# ripgrep — vendored binary

Source: https://github.com/BurntSushi/ripgrep/releases/tag/15.1.0
Archive: ripgrep-15.1.0-aarch64-unknown-linux-gnu.tar.gz
License: MIT (see https://github.com/BurntSushi/ripgrep/blob/master/LICENSE-MIT)

ARM64 (aarch64) GNU build for Linux. Dynamically linked against glibc.
Used by the backend's `docs_embedded` integration via the `VALUZ_RG_PATH`
env injected from the Electron sidecar. `scripts/build-desktop.sh` stages
this binary into `libexec/rg` at build time (no network needed).

## Refresh

```
bash scripts/download-rg.sh aarch64-unknown-linux-gnu
# then copy the result into backend/vendor/rg/linux-arm64/rg
# and regenerate SHA256SUMS:
( cd backend/vendor/rg/linux-arm64 && shasum -a 256 rg > SHA256SUMS )
```
