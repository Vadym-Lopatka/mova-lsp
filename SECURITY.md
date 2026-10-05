# Security

## What nx does on your machine

nx is a language server. It runs as the user who starts it and speaks
LSP over stdin/stdout. It has no network listener.

- It reads and analyzes the project it is pointed at: source files,
  `deps.edn` and similar config, `.lsp/config.edn`, and the jars on the
  project classpath. It writes caches under `$XDG_CACHE_HOME` (default
  `~/.cache`) in an `nx` directory.
- To find the classpath it runs the project's build tool (for example
  `clojure -Spath`). That can run code from the project's `deps.edn`
  and its dependencies, as with any Clojure editor integration. Open
  only projects you trust.
- Refactorings and code actions are sent to the editor as edits. The
  editor applies them.
- The `nx` command line tool runs one process per call and has no daemon.
  `nx hook` is meant to be used as a Claude Code hook: it reads one JSON
  event from stdin, analyzes the edited files, and prints new errors on
  stderr. It does not send data anywhere.
- With `NX_METRICS=1` the server writes timing data (no document text) to
  local files under `$XDG_STATE_HOME`. It is off by default.

## Report a vulnerability

Use GitHub private vulnerability reporting on this repository.
Open the "Security" tab and choose "Report a vulnerability".
