# ripgrep — vendored binary

Source: https://github.com/BurntSushi/ripgrep/releases/tag/15.1.0
Archive: ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz
Archive sha256: 1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599
License: MIT (see https://github.com/BurntSushi/ripgrep/blob/master/LICENSE-MIT)

Statically linked (musl) x86-64 build — portable across Linux distributions,
no glibc dependency. Used by the backend's `docs_embedded` integration via the
`VALUZ_RG_PATH` env injected from the Electron sidecar. `scripts/build-desktop.sh`
stages this binary into `libexec/rg` at build time (no network needed).

## Refresh

```
bash scripts/download-rg.sh x86_64-unknown-linux-musl
# then copy the result into backend/vendor/rg/linux-amd64/rg
# and regenerate SHA256SUMS:
( cd backend/vendor/rg/linux-amd64 && shasum -a 256 rg > SHA256SUMS )
```
