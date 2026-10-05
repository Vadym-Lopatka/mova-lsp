# mova-lsp (nx)

This repository holds **nx**: a language server (LSP) and a command line
tool for Clojure and Mova code. "mova-lsp" is the repository name; "nx" is the name of
the tool and of its commands (`nx`, `nx-lsp`).

nx runs on [Mova](https://github.com/Vadym-Lopatka/mova), a Clojure
dialect in Rust. It has two parts, in two repositories:

| part | what it is | where |
|---|---|---|
| server | the LSP server, a Mova program | this repository, `nx/src` |
| engine | `nx-core`, native Rust: reader, analyzer, linters, queries; it builds the `nx` binary | the [mova](https://github.com/Vadym-Lopatka/mova) repository, `crates/nx-core` |

To use nx you need both: the `mova` and `nx` binaries from the mova
repository, and a clone of this one. See "Install".

This repository is a fork of
[clojure-lsp](https://github.com/clojure-lsp/clojure-lsp) at commit
`8ad65c1d681d2fc9022b3854f6dcaf1677d29631`. nx aims to give the same LSP
answers as clojure-lsp.

Layout:

- `nx/` is the nx work: the server (`nx/src`, `nx/bin/nx-lsp`), the `nx`
  launcher, tests (`nx/test`), benchmarks (`nx/bench`) and the oracle
  tool (`nx/oracle`).
- `mova/` is an earlier port of clojure-lsp itself to Mova (`overlay`,
  `shims`, `smoke`, `bin`, `metrics`). nx does not use it at run time.
  `mova/lsp-e2e` holds the recorded clojure-lsp answers that the nx
  tests compare against.
- Everything else is clojure-lsp, unmodified. `lib/`, `cli/` and
  `test-helper/` are used by the tests (the real clojure-lsp is the
  reference). The rest (`docs/`, `release/`, `scripts/`, `install/`,
  `flake.nix`, ...) is kept as it came from upstream and is not used by nx.

## Status

Experimental. Tested on macOS on Apple silicon only.

On the recorded comparison (`nx/bin/nx-gate`), 673 of 675 core answers
and 1803 of 1803 extended answers equal clojure-lsp. Two reference-list
answers in the `bb` test project differ. In `nx/test/cmd_e2e.py`, 220 of
221 cases equal; the one difference is a dependency version in the `bb`
test project (the recorded answer names a different patch version of a
jar than the one resolved on the machine).

These tests are known to fail: `nx/test/docs_test.mova`, `nx/test/pipe_test.mova`,
`nx/test/met_e2e.py`, `nx/test/completion_e2e.py`.

## Install

You need a Rust toolchain and `python3` for the tests. For JVM Clojure
projects you also need a JDK and the `clojure` CLI on `PATH` (nx asks it
for the classpath). Mova projects need only `mova`.

1. Install Mova:

   ```
   cargo install --locked --git https://github.com/Vadym-Lopatka/mova mova
   ```

2. Install the `nx` binary (package `nx-core`, it installs one binary,
   `nx`):

   ```
   cargo install --locked --git https://github.com/Vadym-Lopatka/mova nx-core
   ```

3. Clone this repository:

   ```
   git clone https://github.com/Vadym-Lopatka/mova-lsp
   ```

Both binaries must be on your `PATH` (cargo puts them in
`~/.cargo/bin`). If they are elsewhere, set `MOVA_BIN` and `NX_BIN` to
their full paths.

## Editor setup

The server speaks LSP on stdin and stdout. Configure your editor to start

```
/path/to/mova-lsp/nx/bin/nx-lsp
```

as the Clojure language server (replace `/path/to` with where you cloned
the repository). The launcher runs `mova` (or `$MOVA_BIN`) on `nx/src`.
It keeps a start-up image under `$XDG_CACHE_HOME/nx` (default
`~/.cache/nx`). Set `MOVA_IMAGE=` (empty) to turn the image off.

## CLI

`nx` prints one answer per call and keeps no daemon. Run it inside a
project, or pass `--root <dir>`:

```
nx check src/app/core.clj     # diagnostics
nx def my.ns/my-fn            # where, signature, doc, source
nx refs my.ns/my-fn           # uses, grouped by file
nx outline src/app/core.clj   # vars of a namespace
nx ns                         # project namespaces
nx doc map                    # origin, arglists and doc of a name
nx find greet                 # vars whose name contains the text
nx hook                       # Claude Code hook (reads JSON on stdin)
nx --help                     # usage; `nx --version` prints the version
```

Long lists are capped (for example 40 lines of findings or matches, 12
lines of doc). `--all` shows everything: `nx --all check`.
In a Mova project `nx` finds `mova` through `$MOVA_BIN` or `PATH`; without
it, Mova core names are known but their docs and source locations are not.

Call the cargo-installed `nx` directly. Do not put the `nx/bin` directory
of this repository on your `PATH`: `nx/bin/nx` is a wrapper that runs
the `nx` binary found on `PATH`.

## Uninstall

```
cargo uninstall nx-core && rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/nx"
```

This removes the `nx` binary and the nx cache. Then delete the clone. To
remove Mova too, use the uninstall line in the
[mova README](https://github.com/Vadym-Lopatka/mova#install).

## Tests

The main gate compares nx with recorded clojure-lsp answers:

```
export MOVA_BIN=$(command -v mova)
nx/bin/nx-gate < /dev/null --cmd
```

The gate needs the `clojure` CLI and the dependencies of the test
projects in `mova/lsp-e2e/projects`. It was run with `XDG_CACHE_HOME` set to
a directory that held a copy of `~/.cache/clojure-lsp`.

Other tests are Python scripts and Mova scripts in `nx/test`
(for example `python3 nx/test/feat_e2e.py`,
`mova --module-path nx/src nx/test/latency_test.mova`).

## License

The nx work (`nx/`, `mova/`) is under the Eclipse Public License 1.0, see
`LICENSE`. The clojure-lsp sources are under the MIT license, see
`LICENSE-MIT`. See `NOTICE` for details, including the files in
`mova/overlay` that are derived from other projects.
