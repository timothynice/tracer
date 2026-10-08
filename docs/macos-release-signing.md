# macOS release signing

Studi0Trace is distributed from
[timothynice/Studi0Trace](https://github.com/timothynice/Studi0Trace/releases),
outside the Mac App Store. Release builds use
`Developer ID Application: Timothy Nice (B7VLZJMQAL)` with hardened runtime and
secure timestamps. Apple notarizes the app and final disk image; tickets are
stapled to both so Gatekeeper can verify them offline. The updater archive
contains the same signed, stapled universal app.

## GitHub Actions

The repository's Release workflow needs these **Actions secrets**:

| Secret | Value |
| --- | --- |
| `APPLE_CERTIFICATE` | Base64 encoding of the Developer ID Application certificate and matching private key exported as `.p12` |
| `APPLE_CERTIFICATE_PASSWORD` | Password protecting that `.p12` |
| `APPLE_API_PRIVATE_KEY` | App Store Connect team API key's `.p8` contents |
| `APPLE_API_KEY` | API key ID |
| `APPLE_API_ISSUER` | API issuer ID |
| `TAURI_SIGNING_PRIVATE_KEY` | Existing Tauri updater signing key; preserve it so installed apps continue trusting updates |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Updater key password, if the key is encrypted |

Signing credentials are never committed. Actions writes the notarization key
under `RUNNER_TEMP` with owner-only permissions and removes it even on failure.
Tauri imports the certificate into a temporary signing keychain.
Export `.p12` files using Keychain Access or a format compatible with macOS
Keychain. OpenSSL 3's default PKCS#12 encryption can produce a misleading
“MAC verification failed (wrong password?)” error on import; its
`-keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1` export options are
compatible. Include the Developer ID G2 intermediate certificate in the export
and protect it with a strong password. Test importing the export before updating
the repository secret.

The workflow calls `scripts/release.sh` through `tauri-action`. The script checks
credentials, builds the universal app with `tauri.release.conf.json`, notarizes
and staples the DMG, and runs `scripts/verify-release.sh` **before** returning
control to the action for uploads. A failure prevents uploading new release
assets. Releases are drafts for review; publish a successful draft to make its
signed updater manifest available to users. The workflow also checks that a
release tag matches the app version.

To release a new version:

```bash
bash apps/desktop/scripts/version.sh X.Y.Z
bash apps/desktop/scripts/notices.sh
git add apps/desktop/package.json apps/desktop/package-lock.json apps/desktop/src-tauri/tauri.conf.json apps/desktop/src-tauri/Cargo.toml apps/desktop/src-tauri/resources/THIRD_PARTY_NOTICES.html Cargo.lock
git commit -m "Release Studi0Trace X.Y.Z"
git push origin main
git tag vX.Y.Z
git push origin vX.Y.Z
```

Only publish after the Release workflow passes. Never replace a published
version's updater archive: use a new version so existing installations can
discover the update. Older 0.3.0 downloads are ad hoc signed and cannot gain a
Developer ID signature without being replaced.

## Local distribution build

Install the Developer ID certificate with its matching private key in Keychain,
or set `APPLE_CERTIFICATE` and `APPLE_CERTIFICATE_PASSWORD` as above. Then set
`APPLE_API_KEY`, `APPLE_API_ISSUER`, `APPLE_API_KEY_PATH` (the local `.p8` path),
and `TAURI_SIGNING_PRIVATE_KEY` (the existing updater key's contents or path).
Set `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` if needed. Keep all key files outside
the checkout and avoid shell tracing (`set -x`) when loading credentials.

```bash
cd frontend && npm ci
cd ../apps/desktop && npm ci
npm run build:release
```

The universal artifacts are under
`target/universal-apple-darwin/release/bundle/`. `verify-release.sh` checks the
Developer ID authority and team, secure timestamp, hardened runtime, both CPU
architectures, stapled tickets, Gatekeeper acceptance, and that the updater's
extracted app matches the standalone app. Recheck existing artifacts with:

```bash
bash apps/desktop/scripts/verify-release.sh
```

If Apple rejects a submission, use `xcrun notarytool log` with the submission
ID and the same API key credentials. The final DMG's submission result is saved
in `bundle/dmg/notarization.json`. Fix the reported issue and rebuild; do not
publish a failed build or advise users to disable Gatekeeper.

The current G2 Developer ID certificate was created October 8, 2026 and expires
September 17, 2031 (UTC). Its certificate, private key and protected `.p12`
backup are stored locally under `~/.codex/secrets/studi0trace/`; the existing
updater key remains under `~/.tauri/`. Renew the Developer ID certificate before
it expires and update the two certificate secrets together.

References: [Tauri macOS signing](https://v2.tauri.app/distribute/sign/macos/),
[Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
