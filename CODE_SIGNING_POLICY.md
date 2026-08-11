# Code Signing Policy

## Current status

BenchForge release binaries are currently **unsigned**. Windows may therefore
show an Unknown Publisher or Microsoft Defender SmartScreen warning. Verify
downloads against the SHA-256 checksum attached to the same GitHub release.

The project intends to apply for the SignPath Foundation open-source program
after it has established a public release history. Acceptance has not been
requested or granted. If accepted, this page will be updated to state:

> Free code signing provided by SignPath.io, certificate by SignPath Foundation.

Under that program Windows would identify **SignPath Foundation** as the
verified publisher. It would not identify BenchForge or an individual
maintainer as the certificate subject.

## Project roles

- Committers and reviewers: [@Cyclotronic](https://github.com/Cyclotronic)
- Release and signing approver: [@Cyclotronic](https://github.com/Cyclotronic)

Changes from contributors require maintainer review. The maintainer is also the
trusted author for direct maintenance changes. If additional maintainers join,
this policy will name their roles before they can approve signing requests.

## Release provenance

Official binaries are built from tagged source by the repository's GitHub
Actions workflow on a GitHub-hosted Windows runner. The workflow runs static
analysis, unit tests, offline protocol-fidelity checks, a PyInstaller build,
and frozen-application verification before packaging the release.

Every release artifact receives a SHA-256 checksum. A future SignPath signing
request will require manual approval and will sign only BenchForge-owned
binaries. Bundled third-party Python and Qt libraries will not be represented
as BenchForge-owned code.

The Windows ProductName and ProductVersion metadata are generated from the
source tree and must match the release being approved.

## Privacy

See [PRIVACY.md](PRIVACY.md). BenchForge does not transfer information to the
maintainers or other network services unless the user or a connected emulator
client specifically initiates the documented network operation.
