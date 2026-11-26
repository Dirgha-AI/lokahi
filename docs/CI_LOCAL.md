# Running LOKAHI CI on a self-hosted Gitea or Forgejo

GitHub Actions minutes are finite and now exhausted, so the CI has a second home:
`.gitea/workflows/ci.yml` (Gitea) and `.forgejo/workflows/ci.yml` (Forgejo), which
are byte-identical. Both are checked in next to `.github/workflows/ci.yml`, which
stays valid for whenever GitHub minutes exist again.

This page covers what the workflow needs from a runner and how to register one.
Nothing here has been exercised end to end: the Gitea 28.4.0 instance on this
machine (`D:\Projects\Public\release\gitea-local`) migrates, creates a user and
starts, then stops after `InitWebInstalled` without ever binding a port, so no
job has run against these files. The YAML, the step targets and the shell syntax
are checked (see [Validation](#validation)); the registration commands below come
from the upstream runner documentation and are unverified on this host.

## What the workflows run

Three jobs, mirroring `.github/workflows/ci.yml` step for step:

| Job | Runs on | What it does |
| --- | --- | --- |
| `python` | `self-hosted` | Matrix over Python 3.10 / 3.11 / 3.12: builds the `wasm32-unknown-unknown` pre-flight module with cargo, then runs the 18 test, conformance, benchmark and demo entry points. |
| `rust` | `self-hosted` | `cargo test --release` and the wasm32 release build in `tools/wasm_preflight`. |
| `packaging` | `self-hosted` | Validates `pyproject.toml`, builds the wheel, installs it, runs `TestC3CLIFlows`, and fails if build artifacts show up as unignored git changes. |

`actions/checkout@v4` is the only upstream action. It is kept because it works on
both forges and because the alternative — a hand-rolled `git clone` with a token —
means handling credentials and shallow fetches ourselves. Every other GitHub-only
action was removed, not emulated:

- `actions/setup-python@v5` → the runner must have `python3.10`, `python3.11` and
  `python3.12` (or `python310`-style names) on `PATH`; each job resolves its
  interpreter, asserts the version, and fails with a clear message if it is absent.
  A venv per matrix entry is created under `$RUNNER_TEMP` and reused while its
  `.lokahi-ci-ready` marker exists.
- `dtolnay/rust-toolchain@stable` → `rustup target add wasm32-unknown-unknown` plus
  `cargo`, with a graceful path for hosts that have cargo but no rustup.
- PyPI Trusted Publishing in `.github/workflows/publish.yml` has no local
  equivalent (OIDC consumer trust does not exist on Gitea or Forgejo), so releasing
  stays a GitHub action. Build and publish locally with `python -m build` and
  `twine` if GitHub is unavailable.

## What the runner host must provide

For host-mode runners (`self-hosted:host`, what the workflow assumes):

- `git` 2.34.1 or newer, and `bash`.
- `node` — `actions/checkout` is a JavaScript action, so the runner needs a Node
  runtime for it even though no other step uses JavaScript.
- Python 3.10, 3.11 and 3.12, each startable as `python3.10` / `python3.11` /
  `python3.12`. If the toolchains live elsewhere, add shims to `PATH` or symlink
  them; the resolve step matches the interpreter's own version, not its name, so
  any name on `PATH` works as long as it reports the right major.minor.
- A Rust toolchain: `cargo` on `PATH`, `rustup` recommended so the job can add the
  `wasm32-unknown-unknown` target itself. A distro-packaged cargo without rustup
  works only if the wasm32 std is already installed; the job then relies on the
  build failing loudly rather than pretending otherwise.
- Network access to PyPI for `cryptography`, `wasmtime`, `pip` and `build`, or a
  reachable mirror / wheelhouse exported through the runner environment:

  ```yaml
  # config.yaml of the runner
  runner:
    envs:
      PIP_INDEX_URL: https://pypi.example.internal/simple
      PIP_FIND_LINKS: /srv/wheels
  ```

For a container runner (`self-hosted:docker://<image>`), all of the above must
live inside the image instead, Python interpreters included.

## The label contract

The workflow says `runs-on: self-hosted`, and a runner serves a job when its label
set contains the name `self-hosted`. Labels are written `name:schema://arg`, where
the schema is `host` (run on the machine, the default when omitted), `docker://`
(a container image) or, on Forgejo, `lxc://`.

Both options work with this repository unchanged:

```text
self-hosted:host                              # steps run directly on the runner host
self-hosted:docker://gitea/runner-images:ubuntu-latest
```

Host mode has no isolation: a job can destroy the runner's filesystem. Use it for
a dedicated CI box or VM. Container mode gives isolation and reproducibility but
needs Docker (or Podman with its socket) and a job image carrying bash, git, node
and three Python versions.

To use a different label — a dedicated `lokahi-ci`, say — change `runs-on:` in
both workflow files and advertise that label instead.

## Gitea and Forgejo use different runners

These are two separate programs from two projects. A runner registered with one
does not serve the other: the registration credentials, the runner protocol and the
configuration file all differ.

| | Gitea | Forgejo |
| --- | --- | --- |
| Runner binary | `gitea-runner` (named `act_runner` in older releases; both connect only to Gitea) | `forgejo-runner` |
| Source | `gitea.com/gitea/runner` | `code.forgejo.org/forgejo/runner` |
| Registration data | one runner token | a UUID plus a token |
| Labels live in | `runner.labels` in `config.yaml`, or `--labels` at registration | `runner.labels` in the config file |
| Server details live in | `.runner` file written by `register` | `server.connections.<name>` in the config file |

Gitea's current runner requires Gitea 1.26.2 or newer. Forgejo Actions is enabled
by default from Forgejo v1.21.

### Actions must be enabled and able to resolve actions

Both forges fetch the workflow files themselves but only the runner executes steps,
so each side has an `[actions]` block in its `app.ini`:

```ini
[actions]
ENABLED = true
; Gitea defaults to https://github.com, Forgejo to https://data.forgejo.org
DEFAULT_ACTIONS_URL = https://github.com
```

The local instance at `D:\Projects\Public\release\gitea-local` has **no**
`[actions]` section at all, and sets `OFFLINE_MODE = true` — so before anything can
queue, that block must be added to `custom/conf/app.ini`, with
`DEFAULT_ACTIONS_URL` pointing at a host that actually serves `actions/checkout`
(a reachable GitHub mirror, or an internal mirror of the action repository). Under
`OFFLINE_MODE` with no reachable actions host, the checkout step is the one that
will fail; the rest of the workflow is pure commands and needs nothing from the
network beyond PyPI. In that case replace the checkout step with a clone:

```yaml
      - name: Clone the repository
        run: |
          git clone --depth 1 --branch "${GITHUB_HEAD_REF:-$GITHUB_REF_NAME}" \
            "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY" .
```

## Registering a runner for Gitea

1. Enable `[actions]` in `app.ini` and restart Gitea.
2. Get a registration token from the instance page that should own the runner:
   - site-wide: `/admin/actions/runners`
   - per organization: `/org/{org}/settings/actions/runners`
   - per repository: `/{owner}/{repo}/settings/actions/runners`

   The token is shown when you create a runner there and is not reused as the
   runner's long-term credential; the runner receives its own after registering.
3. Register and start, from a directory you intend to keep (the runner writes
   `config.yaml` and a `.runner` file beside the binary):

   ```bash
   ./gitea-runner register \
     --instance http://127.0.0.1:3000 \
     --token <registration-token> \
     --name lokahi-ci-1 \
     --labels self-hosted:host \
     --no-interactive

   ./gitea-runner daemon
   ```

   Use the instance's `ROOT_URL` for `--instance`, not a bare `localhost`, or the
   server's callbacks will not come back to the runner.
4. Because this repository runs a three-way Python matrix, raise the runner's
   concurrency so the jobs overlap instead of queuing:

   ```bash
   ./gitea-runner config set runner.capacity 3
   ./gitea-runner config add runner.labels 'self-hosted:host'
   ```

   Label precedence, highest first: `daemon --labels` /
   `GITEA_RUNNER_LABELS` > `runner.labels` in `config.yaml` > the labels stored in
   `.runner`.

As a service on Linux, with `systemd` — adapt paths and user:

```ini
[Unit]
Description=Gitea Actions runner
After=network-online.target

[Service]
Type=simple
User=git
WorkingDirectory=/opt/gitea-runner
ExecStart=/opt/gitea-runner/gitea-runner daemon --config /opt/gitea-runner/config.yaml
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

On Windows, from an elevated prompt (`sc.exe create` takes `key= value` with a
space after the equals sign, and no line breaks inside the command):

```bat
sc.exe create gitea-runner binPath= "\"C:\gitea-runner\gitea-runner.exe\" daemon --config \"C:\gitea-runner\config.yaml\"" start= auto
sc.exe start gitea-runner
```

The service account needs write access to the runner directory and read access to
the Python and Rust toolchains it will invoke.

## Registering a runner for Forgejo

1. Confirm `[actions]` is not disabled in `app.ini`.
2. Create the runner in the Forgejo UI at the scope you want
   (`/admin/actions/runners`, `/org/{org}/settings/actions/runners`,
   `/user/settings/actions/runners` or `/{owner}/{repo}/settings/actions/runners`).
   It shows a UUID and a token; these are the runner's permanent credentials.
3. Put them in the runner's config file together with the label:

   ```yaml
   runner:
     capacity: 3
     labels:
       - self-hosted:host

   server:
     connections:
       lokahi:
         url: http://127.0.0.1:3000/
         uuid: 00000000-0000-0000-0000-000000000000
         token: <token-from-the-ui>
   ```

   `forgejo-runner register` offers an interactive path that writes the same
   section; the file route above is what the Forgejo docs recommend for
   provisioning because the runner can hold several connections at once.
4. Start it: `./forgejo-runner daemon --config config.yaml`. For a service, reuse
   the unit above with `ExecStart=/opt/forgejo-runner/forgejo-runner daemon
   --config /opt/forgejo-runner/config.yaml`.

A `forgejo-runner` pointed at a Gitea instance will register nowhere useful, and
the reverse is equally true: the tokens and endpoints are not interchangeable.

## Verifying the setup

1. Push to `main`, or open a pull request against it — those are the triggers, plus
   `workflow_dispatch` for a manual run from the Actions tab.
2. If a run stays queued with "no matching runner", the label is the problem: check
   that the runner's label list contains `self-hosted` (`gitea-runner config get
   runner.labels`), not only `ubuntu-latest`.
3. To exercise one job without pushing, both runners can execute a workflow file
   locally (`gitea-runner exec` / `forgejo-runner exec`). Host-mode jobs do not
   support service containers and do not get a clean checkout, so expect to run
   `git clean -xfd` yourself afterwards.

Common failures and their causes:

| Symptom | Cause |
| --- | --- |
| `Python 3.10 is not installed on this runner` | Interpreter missing, or present under a name not on `PATH` |
| `cargo is not on PATH` | Rust installed for a different user than the service account |
| `Unable to resolve action ... actions/checkout` | `DEFAULT_ACTIONS_URL` unreachable, or `[actions]` missing |
| `pip install` cannot reach the index | Offline instance; set `PIP_INDEX_URL`/`PIP_FIND_LINKS` in `runner.envs` |
| A dependency change is ignored | The cached venv under `$RUNNER_TEMP` carries a `.lokahi-ci-ready` marker; delete `$RUNNER_TEMP/lokahi-venv-*` |
| Wheel build cannot find `setuptools` | `python -m build` needs the index for its isolated build environment |

## Validation

The three files were checked from the working tree on the machine that wrote them
(Windows, git-bash) with PyYAML 6.0.4. No runner was involved:

- Both workflow files parse as YAML, and the job list, triggers, `runs-on` values
  and every step's `uses:` are inspected programmatically. A bare `on:` key loads
  as the boolean `True` under YAML 1.1, so the check reads it either way; nothing
  else in these files is YAML-1.1 sensitive.
- Every `run:` block — 32 of them — passed `bash -n`, run against the git-bash
  interpreter rather than the System32 `bash.exe` WSL stub that `bash` resolves to
  on Windows.
- Every repository path a step invokes (`tests/*.py`, `tools/*.py`, `demo.py`,
  `examples/*.py`, `tools/wasm_preflight`) exists in the checkout, and every
  `$LOKAHI_PY` use comes after the step that sets it.
- Step names were compared with `.github/workflows/ci.yml`: 23 / 4 / 7 source
  steps map to 23 / 4 / 8 here, with the three GitHub-only steps accounted for by
  their replacements and one venv step added for the packaging job.
- The two workflow copies compare byte for byte.

That harness was a throwaway script and is not committed. To repeat the two checks
worth keeping:

```bash
py -3 -c "import yaml; [yaml.safe_load(open(p, encoding='utf-8')) for p in ('.gitea/workflows/ci.yml', '.forgejo/workflows/ci.yml')]; print('yaml OK')"
diff .gitea/workflows/ci.yml .forgejo/workflows/ci.yml && echo identical
```

What has not been checked: that either forge schedules these jobs, that either
runner binary accepts the flags above on this host, or that any job passes. The
instance never bound a port here, so treat this workflow as written, reviewed and
syntax-checked — not as proven green.
