# CLAUDE.md

Project root: `C:\Users\ralie\mt5-ai-bridge`

## Working agreement

- Work only in this directory. Preserve existing functionality.
- Default `MODE=APPROVAL`. Always use a demo account.
- Refactor incrementally. After each phase: run tests, fix issues, update docs,
  commit.

## Conventions established in the foundation phase

- **Never import `MetaTrader5` outside `mt5_client.py`.** All other modules take
  a `client` argument. Add new broker calls as methods on `RealMT5Client` and
  mirror them on `tests/fakes.py::FakeMT5Client`.
- Configuration goes through `Settings` (`config.py`) sourced from `.env`. Do
  not hard-code tunables; add a field and an `.env.example` entry.
- Journal anything decision-relevant via `Journal` so the future dashboard and
  analysis can read it.
- Keep indicator/strategy/risk functions pure and unit-tested.

## Commands

```bash
pip install -r requirements.txt
python -m pytest -q        # run tests (no MetaTrader5 needed)
python bridge.py           # run the live bridge (Windows + MT5 terminal)
```

## Note on tooling

`.env` and `*.db` are git-ignored. Commit from this machine; git operations are
not reliable from the assistant's sandbox mount.
