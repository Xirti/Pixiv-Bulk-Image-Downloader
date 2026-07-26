# Release Checklist

## Legal and repository settings

- [x] Project license selected and added before the final portable build: MIT, Copyright (c) 2026 Aperia.
- [ ] Enable GitHub private vulnerability reporting.
- [ ] Confirm the repository contains no downloaded artwork, logs, runtime descriptors, cookies, local reports, or machine-specific paths.
- [ ] Review Pixiv's current terms and the service notice in `README.md`.

## Verification

- [ ] `python -B run_tests.py`
- [ ] `node --check web/app.js`
- [ ] `powershell -ExecutionPolicy Bypass -File .\build-portable.ps1`
- [ ] Run packaged directory/write, login-window, usage-guide/network, strict multi-tag, alias, and collection-basket probes.
- [ ] Verify `dist\MOKU\SHA256.txt` against `MOKU.exe`.
- [ ] Run `python -B build_manifest.py verify .\dist\MOKU\BUILD_MANIFEST.json .\dist\MOKU\MOKU.exe`.
- [ ] Test the ZIP on a clean Windows 10/11 x64 machine.

## Publish

- [ ] Run `powershell -ExecutionPolicy Bypass -File .\make-release.ps1`.
- [ ] Use `-SkipBuild` only when the source, build inputs, license, and every file in `dist\MOKU` still match `BUILD_MANIFEST.json`.
- [ ] Commit the source and tag the exact verified release commit as `v1.0.11`.
- [ ] Create a GitHub Release from that tag.
- [ ] Upload the ZIP and `SHA256SUMS.txt` from `release\v1.0.11`.
- [ ] Verify the downloaded ZIP hash from another directory.

> Published releases through `v1.0.10` remain immutable. Exact PID/UID lookup, explicit search cancellation, bounded search networking, and basket state fixes are prepared for `v1.0.11`.
