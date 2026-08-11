# Contributing to BenchForge

Issues and focused pull requests are welcome. For behavior changes, explain
which emulation mode is affected and distinguish measured hardware behavior
from an inference or compatibility choice.

## Development checks

Install the development dependencies and run the same checks used by CI:

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pyflakes .
python -m unittest discover -s tests
python tools/verify_offline.py
```

For Windows packaging changes, also run:

```powershell
python build_exe.py
python tools/verify_frozen_build.py
```

Do not commit `build/`, `dist/`, local captures containing confidential bench
data, signing credentials, API tokens, or certificate private keys.

## Pull requests

- Keep changes narrow and include tests for protocol or packaging behavior.
- Update hardware profiles when new behavior was physically measured.
- Preserve third-party license notices when dependencies change.
- Never replace measured hardware fidelity merely to accommodate one client
  without documenting the compatibility tradeoff.

All contributions are accepted under the repository's MIT License.

## Account and commit privacy

Maintainers and release approvers should enable multi-factor authentication on
GitHub. Contributors who do not want an email address recorded in public Git
history should select **Keep my email addresses private** in GitHub and use the
no-reply address GitHub provides before creating commits. Changing a local Git
configuration does not rewrite an address already present in published
history.

Git commit signatures and Windows executable signatures solve different
problems. Never place a Windows code-signing private key in this repository.
