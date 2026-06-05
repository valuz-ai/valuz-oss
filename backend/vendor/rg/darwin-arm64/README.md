# ripgrep — vendored binary

Source: https://github.com/BurntSushi/ripgrep/releases/tag/15.1.0
Archive: ripgrep-15.1.0-aarch64-apple-darwin.tar.gz
License: MIT (see https://github.com/BurntSushi/ripgrep/blob/master/LICENSE-MIT)

Used by backend/valuz_agent/providers/docs_embedded.py via the
``VALUZ_RG_PATH`` env injected from the Electron sidecar.

## Refresh

```
bash scripts/download-rg.sh aarch64-apple-darwin
# then copy the result into backend/vendor/rg/darwin-arm64/rg
# and regenerate SHA256SUMS:
( cd backend/vendor/rg/darwin-arm64 && shasum -a 256 rg > SHA256SUMS )
```
