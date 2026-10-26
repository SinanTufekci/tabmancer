# Contributing

Thanks for wanting to help. Bug reports, ideas and pull requests are all welcome.

## Reporting a bug

Open an issue with what you did, what you expected and what happened instead. `tabmancer doctor`
prints what tabmancer can see of your setup (the CLI's version, the terminal, how many
transcripts it could read); paste its output. Please leave out anything private from your
sessions: titles and prompts can say more than you mean them to.

## Working on it

tabmancer is Python 3.10+ and [Textual](https://textual.textualize.io/), managed with
[uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/SinanTufekci/tabmancer
cd tabmancer
uv sync
uv run tabmancer          # the dashboard, from your checkout
uv run pytest -q          # the tests (they never touch your real sessions or open tabs)
uv run ruff check .
```

- The tests run on Windows, Linux and macOS in CI. They build their own `~/.claude` in a temporary
  folder and stub everything that would open a tab, call a model or touch the clipboard; a new test
  should do the same (see `tests/conftest.py`).
- tabmancer is read-only by design: it reads the transcripts the agents keep and writes under
  `~/.claude` only when you ask it to clean up. Please keep it that way.
- The README pictures are of a real Windows Terminal window over made-up sessions
  (`scripts/demo_data.py`): `scripts/window_shots.py` takes them, `scripts/readme_media.py` makes
  the PNGs and the moving demo, `scripts/promo.py` draws the banner. The usage line at the top of
  each says how to run it.
- Words on screen are short, plain English that says what happens ("Opened 3 sessions in new
  tabs"), not what the code does.

## Releasing

PyPI trusts the release workflow (trusted publishing), so no token is kept anywhere.

1. Once: on pypi.org, under *Publishing*, add a pending publisher with project `tabmancer`,
   owner `SinanTufekci`, repository `tabmancer`, workflow `release.yml` and environment `pypi`.
   In the repository's settings, add an environment called `pypi`.
2. Set the version in `src/tabmancer/__init__.py`, add it to `CHANGELOG.md`, and set
   `TESTED_WITH` in `src/tabmancer/doctor.py` to the Claude Code version you checked the release
   with (`claude --version`; `tabmancer doctor` then says so). Commit.
3. `git tag v0.1.0 && git push --tags`. The workflow checks that the tag matches the version,
   runs the tests, builds and publishes.
