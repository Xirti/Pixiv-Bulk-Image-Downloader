# Release Checklist

## Legal and repository settings

- [x] Project license selected and added before the final portable build: MIT, Copyright (c) 2026 Aperia.
- [ ] Enable GitHub private vulnerability reporting.
- [x] Confirm the repository contains no downloaded artwork, logs, runtime descriptors, cookies, local reports, or machine-specific paths. Verified against the staged first-commit closure.
- [ ] Review Pixiv's current terms and the service notice in `README.md`.

## Verification

- [x] `python -B -m unittest discover -s tests -v`
- [x] `node --check web/app.js`
- [x] `powershell -ExecutionPolicy Bypass -File .\build-portable.ps1`
- [x] Run packaged directory/write, login-window, usage-guide/network, and multi-tag paging probes.
- [x] Verify `dist\MOKU\SHA256.txt` against `MOKU.exe`.
- [x] Run `python -B build_manifest.py verify .\dist\MOKU\BUILD_MANIFEST.json .\dist\MOKU\MOKU.exe`.
- [ ] Test the ZIP on a clean Windows 10/11 x64 machine.

## Publish

- [x] Run `powershell -ExecutionPolicy Bypass -File .\make-release.ps1`.
- [ ] Use `-SkipBuild` only when the source, build inputs, license, and every file in `dist\MOKU` still match `BUILD_MANIFEST.json`.
- [ ] Commit the source and tag the exact verified commit as `v1.0.1`.
- [ ] Create a GitHub Release from that tag.
- [ ] Upload the ZIP and `SHA256SUMS.txt` from `release\v1.0.1`.
- [ ] Verify the downloaded ZIP hash from another directory.
