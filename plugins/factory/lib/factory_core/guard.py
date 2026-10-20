"""The command guard: decides allow / ask / deny for a Bash command a factory agent wants to run.

The guard reads a command the way a shell would, well enough to see through the usual disguises: it splits compound
commands (`;`, `&&`, `||`, `|`, `&`, newlines, subshells), follows `$(…)`, backticks, process substitution,
`bash -c`, `eval`, heredocs fed to a shell and wrappers (`env`, `sudo`, `xargs`, `timeout`, `npx`, …), drops
leading `VAR=value` assignments, and skips each tool's global options before it looks at the subcommand. Then
tool-specific checks run on every simple command, and the regex rules in `guard-policy.json` run on each command
and on the whole text (all patterns are bounded so a padded command cannot stall the hook).

It is defence in depth on top of Claude Code's permissions, not a sandbox: an agent can still write a script and
run it. The guard stops the direct forms and makes the factory's rules (never push, deploy, publish or apply; never
touch the user's checkout or the factory's own state) hard to break by accident or by a prompt injection.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .paths import plugin_path
from .util import read_json

MAX_CHARS = 16000
MAX_DEPTH = 6
ORDER = {"allow": 0, "ask": 1, "deny": 2}

# ---- the lexer --------------------------------------------------------------------------------------------------
OPERATORS = ("&&", "||", ";;", "|&", "<<<", "<<-", "<<", ">>", ">&", "<&", "&>", ">|", "<>", ";", "&", "|", "(",
             ")", "<", ">")
SEPARATORS = {"&&", "||", ";;", "|&", ";", "&", "|", "(", ")"}
REDIRECTS = {">>", ">&", "<&", "&>", ">|", "<>", "<", ">"}
WRITE_REDIRECTS = {">>", "&>", ">|", ">", "<>"}


@dataclass
class Command:
    words: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)  # redirection targets written to
    stdin: list[str] = field(default_factory=list)   # heredoc bodies and here-strings
    piped: bool = False                              # stdin comes from a pipe


@dataclass
class Parsed:
    commands: list[Command]
    substitutions: list[str]
    complete: bool


def _scan_balanced(text: str, i: int, open_ch: str, close_ch: str) -> int:
    """Index just past the `close_ch` matching an already-consumed `open_ch` at text[i-1]; len(text) if none."""
    depth, n = 1, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        if c == '"':
            i = _scan_double(text, i + 1)[0]
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _scan_double(text: str, i: int, subs: list[str] | None = None) -> tuple[int, str]:
    """Read a double-quoted string starting after the opening quote; returns (index after it, its value)."""
    out, n = [], len(text)
    while i < n:
        c = text[i]
        if c == '"':
            return i + 1, "".join(out)
        if c == "\\" and i + 1 < n:
            out.append(text[i + 1] if text[i + 1] in '"\\$`' else c + text[i + 1])
            i += 2
            continue
        if c == "$" and text.startswith("$(", i):
            end = _scan_balanced(text, i + 2, "(", ")")
            if subs is not None:
                subs.append(text[i + 2:end - 1])
            out.append(text[i:end])
            i = end
            continue
        if c == "`":
            end = text.find("`", i + 1)
            end = n if end < 0 else end
            if subs is not None:
                subs.append(text[i + 1:end])
            out.append(text[i:end + 1])
            i = end + 1
            continue
        out.append(c)
        i += 1
    return n, "".join(out)


def parse(text: str) -> Parsed:
    commands: list[Command] = []
    subs: list[str] = []
    complete = True
    cur = Command()
    word: list[str] = []
    in_word = False
    pending_redirect: str | None = None
    heredocs: list[tuple[Command, str, bool]] = []
    after_heredoc_op: tuple[Command, bool] | None = None
    i, n = 0, len(text)

    def end_word():
        nonlocal word, in_word, pending_redirect, after_heredoc_op
        if not in_word:
            return
        value = "".join(word)
        if after_heredoc_op is not None:
            heredocs.append((after_heredoc_op[0], value, after_heredoc_op[1]))
            after_heredoc_op = None
        elif pending_redirect == "<<<":
            cur.stdin.append(value)
        elif pending_redirect is not None:
            if pending_redirect in WRITE_REDIRECTS and not value.startswith("&") and not value.isdigit():
                cur.writes.append(value)
        else:
            cur.words.append(value)
        pending_redirect = None
        word, in_word = [], False

    def end_command(piped_next: bool):
        nonlocal cur
        end_word()
        if cur.words or cur.stdin or cur.writes:
            commands.append(cur)
        cur = Command(piped=piped_next)

    while i < n:
        c = text[i]
        if c in " \t\r":
            end_word()
            i += 1
            continue
        if c == "\n":
            end_command(False)
            i += 1
            for owner, delim, strip_tabs in heredocs:  # heredoc bodies start on the next line
                body = []
                while i < n:
                    j = text.find("\n", i)
                    line = text[i:] if j < 0 else text[i:j]
                    i = n if j < 0 else j + 1
                    if (line.lstrip("\t") if strip_tabs else line) == delim:
                        break
                    body.append(line)
                owner.stdin.append("\n".join(body))
            heredocs.clear()
            continue
        if c == "#" and not in_word:
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "\\":
            if i + 1 < n and text[i + 1] == "\n":
                i += 2
                continue
            word.append(text[i + 1] if i + 1 < n else "")
            in_word = True
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                complete, j = False, n
            word.append(text[i + 1:j])
            in_word = True
            i = j + 1
            continue
        if c == '"':
            end, value = _scan_double(text, i + 1, subs)
            if end >= n and not text.endswith('"'):
                complete = False
            word.append(value)
            in_word = True
            i = end
            continue
        if c == "$" and text.startswith("$(", i):
            end = _scan_balanced(text, i + 2, "(", ")")
            subs.append(text[i + 2:end - 1])
            word.append(text[i:end])
            in_word = True
            i = end
            continue
        if c == "$" and text.startswith("${", i):
            end = _scan_balanced(text, i + 2, "{", "}")
            word.append(text[i:end])
            in_word = True
            i = end
            continue
        if c == "`":
            j = text.find("`", i + 1)
            j = n if j < 0 else j
            subs.append(text[i + 1:j])
            word.append(text[i:j + 1])
            in_word = True
            i = j + 1
            continue
        if c in "<>" and text.startswith("(", i + 1) and not in_word:  # process substitution
            end = _scan_balanced(text, i + 2, "(", ")")
            subs.append(text[i + 2:end - 1])
            word.append(text[i:end])
            in_word = True
            i = end
            continue
        op = next((o for o in OPERATORS if text.startswith(o, i)), None)
        if op is not None:
            if op in (">", "<") and in_word and "".join(word).isdigit():
                word, in_word = [], False  # `2>`: the digits name a file descriptor
            end_word()
            i += len(op)
            if op in SEPARATORS:
                end_command(piped_next=op in ("|", "|&"))
            elif op in ("<<", "<<-"):
                after_heredoc_op = (cur, op == "<<-")
                in_word = False
            else:
                pending_redirect = op
            continue
        word.append(c)
        in_word = True
        i += 1
    end_command(False)
    for owner, _delim, _strip in heredocs:  # a heredoc without its body: the shell would wait for input
        owner.stdin.append("")
    return Parsed(commands, subs, complete)


# ---- shared word helpers ----------------------------------------------------------------------------------------
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "!", "{", "}", "esac", "time",
            "coproc"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish", "ash", "busybox"}
INTERPRETERS = {"python", "python2", "python3", "node", "nodejs", "perl", "ruby", "php", "deno", "bun", "osascript",
                "pwsh", "powershell", "lua", "Rscript"}
INLINE_FLAGS = {"-c", "-e", "--eval", "-r", "-p", "--print", "-E", "-Command", "-command", "eval"}
SENSITIVE_VERBS = {"push", "publish", "apply", "destroy", "deploy", "delete", "merge", "up", "upload", "release",
                   "send-pack", "rm", "remove", "unpublish", "terminate"}


def base(word: str) -> str:
    name = word.rsplit("/", 1)[-1]
    return re.sub(r"(\.exe|\.cmd)$", "", name)


def positional(args: list[str], with_value: set[str] = frozenset(), limit: int = 8) -> list[str]:
    """Non-option arguments in order, skipping options and the values of options listed in `with_value`."""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--":
            continue
        if a.startswith("-") and len(a) > 1:
            if a in with_value:
                skip = True
            continue
        out.append(a)
        if len(out) >= limit:
            break
    return out


def has_flag(args: list[str], *flags: str) -> bool:
    for a in args:
        if a in flags:
            return True
        for f in flags:
            if f.startswith("--") and a.startswith(f + "="):
                return True
            if len(f) == 2 and f[0] == "-" and a.startswith("-") and not a.startswith("--") and f[1] in a[1:] \
                    and a[1:].isalpha():
                return True
    return False


# ---- the analyzer -----------------------------------------------------------------------------------------------
@dataclass
class Finding:
    rule: str
    decision: str
    detail: str = ""


@dataclass
class Context:
    agent: bool = False
    cwd: str | None = None
    protect: str | None = None       # the user's main checkout (agents never change it)
    state: str | None = None         # its .factory folder
    allow_writes: tuple | None = ()  # files inside .factory the agent may write (its Output); None = step unknown
    findings: list[Finding] = field(default_factory=list)

    def hit(self, rule: str, decision: str, detail: str = "") -> None:
        self.findings.append(Finding(rule, decision, detail))


def _resolve(ctx: Context, path: str) -> str | None:
    if not path or "$" in path or "`" in path:
        return None
    if path.startswith("~"):
        path = os.path.expanduser(path)
    if not os.path.isabs(path):
        if ctx.cwd is None:
            return None
        path = os.path.join(ctx.cwd, path)
    return os.path.realpath(path)


def _inside(path: str | None, root: str | None) -> bool:
    return bool(path and root) and (path == root or path.startswith(root.rstrip(os.sep) + os.sep))


def analyze(text: str, ctx: Context, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        ctx.hit("guard-limits", "ask", "nested too deeply to check")
        return
    parsed = parse(text)
    if not parsed.complete:
        ctx.hit("guard-limits", "ask", "unbalanced quotes; the guard cannot read it reliably")
    for sub in parsed.substitutions:
        analyze(sub, ctx, depth + 1)
    for cmd in parsed.commands:
        _command(cmd, ctx, depth)


def _command(cmd: Command, ctx: Context, depth: int) -> None:
    for target in cmd.writes:
        _check_write_target(target, ctx)
    words = list(cmd.words)
    while words and (ASSIGNMENT.match(words[0]) or words[0] in KEYWORDS):
        words.pop(0)
    if not words:
        return
    words = _unwrap(words, cmd, ctx, depth)
    if not words:
        return
    name = base(words[0])
    args = words[1:]
    if "$" in words[0] or "`" in words[0]:
        if SENSITIVE_VERBS & set(args[:4]):
            ctx.hit("variable-command", "ask", "runs a command named by a variable with a sensitive verb")
        return
    if name in SHELLS:
        _shell(name, args, cmd, ctx, depth)
        return
    if name == "eval":
        _dynamic(" ".join(args), ctx)
        analyze(" ".join(args), ctx, depth + 1)
        return
    if name == "bun" and args[:1] not in (["-e"], ["--eval"], ["-p"], ["--print"]):
        _node_pm(name, args, ctx)
        return
    if name == "deno" and args[:1] == ["publish"]:
        ctx.hit("publish", "deny", "deno publish")
        return
    if name in INTERPRETERS or name.startswith("python3."):
        _interpreter(args, cmd, ctx)
        return
    if name in ("cd", "pushd"):
        target = positional(args)
        if not target:
            ctx.cwd = os.path.expanduser("~")
        else:
            ctx.cwd = _resolve(ctx, target[0])
        return
    check = _check_for(name)
    if check is not None:
        check(name, args, ctx)


WRAPPER_VALUE_OPTS = {
    "env": {"-u", "--unset", "-C", "--chdir"},
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "--user", "--group", "--host", "--prompt"},
    "doas": {"-u", "-C"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "--class", "--classdata"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-i", "-n", "-P", "-L", "-l", "-d", "-E", "-e", "-s", "-a", "--max-args", "--max-procs",
              "--delimiter", "--arg-file", "--replace"},
    "watch": {"-n", "--interval"},
    "flock": {"-w", "--timeout", "-E", "--conflict-exit-code"},
    "exec": {"-a"},
    "npx": {"-p", "--package", "--cache"},
    "bunx": {"-p", "--package"},
    "uvx": {"--from", "--with", "-p", "--python"},
    "aws-vault": set(),
    "chrt": set(),
    "taskset": set(),
}
SIMPLE_WRAPPERS = {"nohup", "builtin", "noglob", "unbuffer", "caffeinate", "nocorrect", "chronic", "time",
                   "proxychains", "proxychains4", "torsocks", "faketime", "setsid", "catchsegv", "strace", "ltrace"}
RUNNERS = {  # tool + leading words that mean "run the next word as a command"
    ("npm", "exec"), ("npm", "x"), ("pnpm", "exec"), ("pnpm", "dlx"), ("pnpm", "x"), ("yarn", "dlx"),
    ("yarn", "exec"), ("bun", "x"), ("uv", "run"), ("poetry", "run"), ("pdm", "run"), ("pipenv", "run"),
    ("hatch", "run"), ("bundle", "exec"), ("pipx", "run"), ("rye", "run"), ("mise", "exec"), ("mise", "x"),
    ("asdf", "exec"), ("direnv", "exec"), ("nix", "run"), ("op", "run"), ("dotenv", "--"), ("doppler", "run"),
    ("aws-vault", "exec"), ("infisical", "run"), ("conda", "run"), ("micromamba", "run"), ("rtx", "exec"),
}


def _unwrap(words: list[str], cmd: Command, ctx: Context, depth: int) -> list[str]:
    for _ in range(12):
        if not words:
            return words
        name = base(words[0])
        if name in KEYWORDS or ASSIGNMENT.match(words[0]):
            words = words[1:]
            continue
        if name in ("sudo", "doas", "run0", "pkexec"):
            ctx.hit("sudo", "ask")
        if name == "command" and any(a in ("-v", "-V") for a in words[1:2]):
            return []
        if name == "env" and any(a == "-S" or a.startswith("--split-string") or a.startswith("-S") for a in words):
            idx = next(i for i, a in enumerate(words) if a == "-S" or a.startswith("-S") or
                       a.startswith("--split-string"))
            rest = words[idx][2:] if words[idx].startswith("-S") and len(words[idx]) > 2 else \
                (words[idx].split("=", 1)[1] if "=" in words[idx] else " ".join(words[idx + 1:idx + 2]))
            analyze(rest + " " + " ".join(words[idx + 2:]), ctx, depth + 1)
            return []
        if name in ("npx", "bunx", "uvx"):
            if len(words) < 2:
                return []
            if "-c" in words or "--call" in words:
                idx = words.index("-c") if "-c" in words else words.index("--call")
                analyze(" ".join(words[idx + 1:idx + 2]), ctx, depth + 1)
                return []
            rest = words[1:]
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if rest[0] in WRAPPER_VALUE_OPTS[name] else rest[1:]
            if rest:
                rest[0] = re.sub(r"(?<=.)@[^/]*$", "", rest[0]).rsplit("/", 1)[-1]  # pkg@1.2 → pkg; @scope/pkg → pkg
            words = rest
            continue
        if name in WRAPPER_VALUE_OPTS or name in SIMPLE_WRAPPERS or name in ("command", "exec", "doas", "run0"):
            opts = WRAPPER_VALUE_OPTS.get(name, set())
            i = 1
            while i < len(words):
                a = words[i]
                if a == "--":
                    i += 1
                    break
                if a.startswith("-") and len(a) > 1:
                    i += 2 if (a in opts and "=" not in a) else 1
                    continue
                if name == "env" and ASSIGNMENT.match(a):
                    i += 1
                    continue
                break
            if name == "timeout" and i < len(words):
                i += 1  # the duration
            if name in ("chrt", "taskset") and i < len(words):
                i += 1  # priority or cpu mask
            if name == "aws-vault":
                i = words.index("--") + 1 if "--" in words else len(words)
            if name in ("flock",) and i < len(words):
                i += 1  # the lock file
                if i < len(words) and words[i] in ("-c", "--command"):
                    analyze(" ".join(words[i + 1:i + 2]), ctx, depth + 1)
                    return []
            if name == "watch" and i < len(words):
                analyze(" ".join(words[i:]), ctx, depth + 1)
                return []
            if name == "xargs" and i >= len(words):
                return []
            words = words[i:]
            continue
        if len(words) > 1 and (name, words[1]) in RUNNERS:
            rest = words[2:]
            while rest and rest[0].startswith("-"):
                rest = rest[1:]
            words = rest
            continue
        if name in ("ssh", "mosh"):
            ctx.hit("remote-shell", "ask", "runs commands on another machine")
            return []
        return words
    ctx.hit("guard-limits", "ask", "too many wrappers")
    return []


def _shell(name: str, args: list[str], cmd: Command, ctx: Context, depth: int) -> None:
    if name == "busybox" and args:
        args = args[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-c",) or (a.startswith("-") and not a.startswith("--") and "c" in a[1:] and a[1:].isalpha()):
            script = args[i + 1] if i + 1 < len(args) else ""
            _dynamic(script, ctx)
            analyze(script, ctx, depth + 1)
            return
        if a in ("-o", "-O", "--rcfile", "--init-file"):
            i += 2
            continue
        if a.startswith("-") or a.startswith("+"):
            i += 1
            continue
        return  # a script file: the guard cannot see inside it
    for body in cmd.stdin:
        analyze(body, ctx, depth + 1)
    if cmd.piped and not cmd.stdin:
        ctx.hit("pipe-to-shell", "ask", "a shell reads commands from a pipe")


def _dynamic(script: str, ctx: Context) -> None:
    """A script for `sh -c` or `eval` whose text is produced by a command substitution is only known at run time."""
    if "$(" in script or "`" in script:
        ctx.hit("guard-limits", "ask", "the command is assembled at run time")


INLINE_DANGER = re.compile(
    r"(?i)\bgit\b.{0,80}?\b(?:push|send-pack)\b|\b(?:npm|pnpm|yarn|twine|cargo|gem|poetry)\b.{0,40}?\b(?:publish|upload|push)\b"
    r"|\b(?:terraform|tofu|terragrunt|pulumi)\b.{0,60}?\b(?:apply|destroy|up)\b|\b(?:docker|podman)\b.{0,40}?\bpush\b"
    r"|\bkubectl\b.{0,80}?\b(?:apply|delete)\b|\bfactoryctl\b|messages\.jsonl|\bjob\.json\b|rmtree\(\s*['\"](?:/|~)",
    re.S)


def _interpreter(args: list[str], cmd: Command, ctx: Context) -> None:
    code = []
    for i, a in enumerate(args):
        if a in INLINE_FLAGS and i + 1 < len(args):
            code.append(args[i + 1])
    if not args or args[0] == "-":
        code += cmd.stdin
    if cmd.piped and (not args or args[0] == "-"):
        ctx.hit("pipe-to-shell", "ask", "an interpreter reads a program from a pipe")
    for text in code:
        if INLINE_DANGER.search(text[:MAX_CHARS]):
            ctx.hit("inline-code", "ask", "inline code mentions a push, publish, deploy or the factory's state")
            return


def _check_write_target(target: str, ctx: Context) -> None:
    if not ctx.agent:
        return
    path = _resolve(ctx, target)
    if path is None:
        return
    if ctx.state and _inside(path, ctx.state):
        if not may_write_state(path, ctx.state, ctx.allow_writes):
            ctx.hit("factory-state", "deny", f"writes {target} inside .factory")
        return
    if ctx.protect and _inside(path, ctx.protect):
        ctx.hit("main-checkout", "deny", f"writes {target} in your checkout")


ENGINE_FILES = {"job.json", "workflow.json", "events.jsonl", "telemetry.jsonl", "messages.jsonl", "agents.json",
                "index.json", "config.json", "standards.md", "backlog.md", "report.json"}


def may_write_state(path: str, state: str, allowed: tuple | None) -> bool:
    """May an agent write `path` (resolved, inside `.factory`)? Only its own Output, or — when its step is unknown —
    any job document that is not engine state."""
    if allowed is not None:
        return path in allowed
    rel = os.path.relpath(path, state).split(os.sep)
    return (len(rel) >= 3 and rel[0] == "jobs" and rel[-1] not in ENGINE_FILES and "entries" not in rel
            and not rel[-1].startswith("."))


# ---- tool checks ------------------------------------------------------------------------------------------------
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix",
                  "--exec-path", "--list-cmds"}
GIT_READ_ONLY = {"status", "log", "show", "diff", "blame", "annotate", "rev-parse", "rev-list", "ls-files",
                 "ls-tree", "ls-remote", "grep", "describe", "cat-file", "for-each-ref", "merge-base", "shortlog",
                 "reflog", "name-rev", "show-ref", "show-branch", "whatchanged", "count-objects", "fsck",
                 "check-ignore", "check-attr", "var", "version", "help", "fetch", "range-diff", "cherry",
                 "verify-commit", "verify-tag", "difftool", "archive", "bundle", "remote", "config", "branch",
                 "tag", "worktree", "stash", "notes", "submodule", "lfs", "sparse-checkout", "maintenance", "gc",
                 "prune", "repack"}
GIT_CONFIG_RISKY = re.compile(r"^(alias\.|core\.(hookspath|fsmonitor|sshcommand|pager|editor|askpass|gitproxy)"
                              r"|credential\.|url\.|remote\.|include\.|includeif\.|filter\.|diff\..*\.textconv"
                              r"|merge\..*\.driver|protocol\.|http\.|uploadpack\.|receivepack\.|sequence\.editor"
                              r"|gpg\.program|init\.templatedir)", re.I)


def _git(name: str, args: list[str], ctx: Context) -> None:
    i, workdir, config = 0, ctx.cwd, []
    while i < len(args):
        a = args[i]
        if a in ("-C",) and i + 1 < len(args):
            workdir = _resolve(Context(cwd=workdir), args[i + 1])
            i += 2
            continue
        if a == "-c" and i + 1 < len(args):
            config.append(args[i + 1])
            i += 2
            continue
        if a.startswith("--git-dir") or a.startswith("--work-tree"):
            value = a.split("=", 1)[1] if "=" in a else (args[i + 1] if i + 1 < len(args) else "")
            resolved = _resolve(Context(cwd=workdir), value) if value else None
            if resolved is not None:
                workdir = resolved[:-5] if resolved.endswith(os.sep + ".git") else resolved
            i += 1 if "=" in a else 2
            continue
        if a in GIT_VALUE_OPTS and "=" not in a:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        break
    sub = args[i] if i < len(args) else ""
    rest = args[i + 1:]
    for item in config:
        key, _, value = item.partition("=")
        if key.lower().startswith("alias."):
            if re.search(r"push|send-pack|!", value):
                ctx.hit("no-push", "deny", f"git -c {key} defines an alias that can push or run a shell")
            else:
                ctx.hit("git-config", "ask", f"git -c {key} defines an alias")
        elif GIT_CONFIG_RISKY.match(key):
            ctx.hit("git-config", "ask", f"git -c {key} changes how git runs programs or reaches remotes")
    if sub in ("push", "send-pack", "http-push", "receive-pack") or (sub == "subtree" and "push" in rest[:3]) or (
            sub == "lfs" and rest[:1] == ["push"]) or (sub == "svn" and rest[:1] == ["dcommit"]) or (
            sub == "p4" and rest[:1] == ["submit"]):
        ctx.hit("no-push", "deny")
        return
    if sub == "config":
        if has_flag(rest, "--global", "--system"):
            ctx.hit("git-config-global", "ask")
        keys = [a for a in rest if not a.startswith("-")]
        if keys and GIT_CONFIG_RISKY.match(keys[0]) and len(keys) > 1:
            ctx.hit("git-config", "ask", f"sets {keys[0]}")
    if ctx.agent and ctx.protect and _inside(workdir, ctx.protect):
        mutating = sub not in GIT_READ_ONLY or _mutating_listing(sub, rest)
        if mutating and sub:
            ctx.hit("main-checkout", "deny", f"git {sub} in your checkout")


def _mutating_listing(sub: str, rest: list[str]) -> bool:
    """Subcommands that list by default but change things with arguments."""
    pos = [a for a in rest if not a.startswith("-")]
    if sub in ("branch", "tag"):
        return bool(pos) or has_flag(rest, "-d", "-D", "-m", "-M", "-c", "-C", "-f", "--delete", "--move",
                                     "--copy", "--force", "--set-upstream-to", "-u", "--edit-description")
    if sub == "config":
        return len(pos) > 1 or has_flag(rest, "--unset", "--unset-all", "--add", "--replace-all", "--rename-section",
                                        "--remove-section", "-e", "--edit")
    if sub in ("remote", "worktree", "stash", "notes", "submodule", "lfs", "sparse-checkout", "maintenance"):
        return bool(pos) and pos[0] not in ("list", "show", "-v", "get-url", "status", "ls-files", "env")
    return False


GH_DENY = {("pr", "merge"), ("pr", "create"), ("release", "create"), ("release", "upload"), ("release", "delete"),
           ("release", "edit"), ("workflow", "run"), ("repo", "delete"), ("secret", "set"), ("secret", "delete"),
           ("variable", "set"), ("variable", "delete"), ("repo", "archive")}
GH_ASK = {"pr": {"close", "comment", "review", "edit", "ready", "reopen", "lock"},
          "issue": {"create", "comment", "close", "edit", "delete", "reopen", "transfer", "lock", "pin"},
          "repo": {"create", "edit", "rename", "fork", "sync", "set-default", "deploy-key"},
          "workflow": {"enable", "disable"}, "run": {"rerun", "cancel", "delete"}, "gist": {"create", "edit", "delete"},
          "label": {"create", "edit", "delete", "clone"}, "cache": {"delete"}, "ruleset": {"create"},
          "project": {"create", "edit", "delete", "item-add", "item-edit", "item-delete", "close"},
          "ssh-key": {"add", "delete"}, "gpg-key": {"add", "delete"}, "auth": {"login", "logout", "refresh", "token"},
          "codespace": {"create", "delete", "ssh"}, "extension": {"install", "upgrade", "exec"}}


GH_VALUE_OPTS = {"-R", "--repo", "--hostname", "-X", "--method", "-H", "--header", "-f", "-F", "--field",
                 "--raw-field", "--input", "-q", "--jq", "-t", "--template", "--cache", "-p", "--preview"}


def _gh(name: str, args: list[str], ctx: Context) -> None:
    pos = positional(args, GH_VALUE_OPTS)
    if len(pos) >= 2 and (pos[0], pos[1]) in GH_DENY:
        ctx.hit("gh-publish", "deny", f"gh {pos[0]} {pos[1]}")
        return
    if pos and pos[0] == "api":
        method = None
        for k, a in enumerate(args):
            if a in ("-X", "--method") and k + 1 < len(args):
                method = args[k + 1]
            elif a.startswith("--method="):
                method = a.split("=", 1)[1]
            elif a.startswith("-X") and len(a) > 2:
                method = a[2:]
        writes = method and method.upper() != "GET" or has_flag(args, "-f", "-F", "--field", "--raw-field",
                                                                "--input")
        if writes or (len(pos) > 1 and pos[1] == "graphql" and re.search(r"(?i)\bmutation\b", " ".join(args))):
            if re.search(r"/(merges?|pulls/\d+/merge|releases|git/refs|dispatches|deployments)\b", " ".join(pos[1:])):
                ctx.hit("gh-publish", "deny", "gh api call that merges, releases, moves refs or deploys")
            else:
                ctx.hit("gh-api-write", "ask")
        return
    if len(pos) >= 2 and pos[1] in GH_ASK.get(pos[0], ()):
        ctx.hit("gh-write", "ask", f"gh {pos[0]} {pos[1]}")


PUBLISH_VERBS = {"publish", "unpublish", "dist-tag"}
NPM_VALUE_OPTS = {"-w", "--workspace", "--filter", "-F", "-C", "--dir", "--prefix", "--registry", "--tag",
                  "--otp", "--access", "--cwd"}


def _node_pm(name: str, args: list[str], ctx: Context) -> None:
    pos = positional(args, NPM_VALUE_OPTS, limit=4)
    if PUBLISH_VERBS & set(pos[:3]) or (name == "yarn" and pos[:2] == ["npm", "publish"]):
        ctx.hit("publish", "deny", f"{name} {' '.join(pos[:2])}")
    elif pos[:1] and pos[0] in ("run", "run-script") and len(pos) > 1 and re.match(
            r"^(publish|release|deploy)(:|$)", pos[1]):
        ctx.hit("publish", "deny", f"{name} run {pos[1]}")
    elif name in ("yarn", "bun") and pos[:1] and re.match(r"^(publish|release|deploy)(:|$)", pos[0]):
        ctx.hit("publish", "deny", f"{name} {pos[0]}")
    elif pos[:1] and pos[0] in ("deprecate", "owner", "access", "token", "team", "adduser", "login"):
        ctx.hit("registry-write", "ask", f"{name} {pos[0]}")


def _simple_publish(verbs: dict):
    def check(name: str, args: list[str], ctx: Context) -> None:
        pos = positional(args, {"-r", "--repository", "--repository-url", "-p", "--project", "--index",
                                "--registry", "-t", "--token", "-s", "--settings", "-P", "-D"}, limit=6)
        want = verbs[name]
        pairs = {" ".join(pos[k:k + 2]) for k in range(len(pos))} | set(pos)
        if want & pairs:
            ctx.hit("publish", "deny", f"{name} {' '.join(pos[:2])}")
    return check


PUBLISHERS = {"twine": {"upload", "register"}, "cargo": {"publish", "yank", "owner"}, "gem": {"push", "yank", "owner"},
              "poetry": {"publish"}, "flit": {"publish"}, "hatch": {"publish"}, "uv": {"publish"},
              "pdm": {"publish"}, "vsce": {"publish", "unpublish"}, "ovsx": {"publish"}, "mvn": {"deploy"},
              "dotnet": {"nuget push", "nuget delete"}, "nuget": {"push", "delete"}, "pod": {"trunk push"},
              "goreleaser": {"release"}, "jreleaser": {"release", "full-release", "deploy"}, "jf": {"rt upload"},
              "rubygems": {"push"}, "swift": {"package-registry publish"}, "dart": {"pub publish"},
              "flutter": {"pub publish"}, "mix": {"hex.publish"}, "crane": {"push"}, "oras": {"push"}}


def _gradle(name: str, args: list[str], ctx: Context) -> None:
    if any(re.match(r"^(:?[\w:-]*:)?publish\w*$", a) for a in args if not a.startswith("-")):
        ctx.hit("publish", "deny", f"{name} publish")


def _containers(name: str, args: list[str], ctx: Context) -> None:
    pos = positional(args, {"-H", "--host", "--context", "-c", "--config", "--log-level", "--url", "--connection"},
                        limit=4)
    if not pos:
        return
    if pos[0] == "push" or pos[:2] in (["image", "push"], ["manifest", "push"], ["compose", "push"]) or (
            pos[0] == "compose" and "push" in pos[1:3]):
        ctx.hit("publish", "deny", f"{name} {' '.join(pos[:2])}")
    elif pos[0] in ("buildx", "build", "builder") and (
            "--push" in args or any(re.search(r"type=(registry|image).*push=true|type=registry", a) for a in args)):
        ctx.hit("publish", "deny", f"{name} build --push")


IAC_DENY = {"apply", "destroy", "import", "taint", "untaint", "force-unlock", "apply-all", "destroy-all"}
TF_VALUE_OPTS = {"-var", "-var-file", "-target", "-replace", "-state", "-state-out", "-backup", "-lock-timeout",
                 "-parallelism", "-out", "--terragrunt-working-dir", "--working-dir", "--terragrunt-config",
                 "--config", "--queue-include-dir", "--queue-exclude-dir", "--filter"}


def _terraform(name: str, args: list[str], ctx: Context) -> None:
    pos = positional(args, TF_VALUE_OPTS, limit=6)
    if not pos:
        return
    head = pos[:3] if name == "terragrunt" else pos[:1]
    if IAC_DENY & set(head):
        ctx.hit("iac-apply", "deny", f"{name} {' '.join(pos[:2])}")
    elif "state" in pos[:2] and set(pos[pos.index("state") + 1:pos.index("state") + 2]) & {
            "rm", "mv", "push", "replace-provider"}:
        ctx.hit("iac-apply", "deny", f"{name} state {pos[pos.index('state') + 1]}")
    elif pos[:2] == ["workspace", "delete"]:
        ctx.hit("iac-apply", "deny", f"{name} workspace delete")


PULUMI_VALUE_OPTS = {"-s", "--stack", "-C", "--cwd", "--config-file", "--color", "-v", "--verbose", "--target",
                     "-t", "--parallel", "-p", "--message", "-m"}


def _pulumi(name: str, args: list[str], ctx: Context) -> None:
    pos = positional(args, PULUMI_VALUE_OPTS, limit=3)
    if pos and (pos[0] in ("up", "update", "destroy", "import", "refresh", "cancel", "watch") or pos[:2] in (
            ["stack", "rm"], ["state", "delete"], ["state", "edit"], ["state", "move"], ["state", "unprotect"])):
        ctx.hit("deploy", "deny", f"pulumi {' '.join(pos[:2])}")


DEPLOYERS = {  # tool → (subcommands that deploy, deploy when called with no subcommand)
    "cdk": ({"deploy", "destroy", "import", "bootstrap", "watch"}, False),
    "serverless": ({"deploy", "remove", "rollback"}, False), "sls": ({"deploy", "remove", "rollback"}, False),
    "sam": ({"deploy", "delete", "sync"}, False), "vercel": ({"deploy", "--prod", "promote", "rollback", "remove",
                                                              "rm", "redeploy", "alias"}, True),
    "netlify": ({"deploy"}, False), "firebase": ({"deploy", "hosting:channel:deploy"}, False),
    "fly": ({"deploy", "launch", "destroy", "scale"}, False), "flyctl": ({"deploy", "launch", "destroy"}, False),
    "railway": ({"up", "down", "redeploy"}, False), "wrangler": ({"deploy", "publish", "delete", "rollback"}, False),
    "eb": ({"deploy", "terminate", "create"}, False), "kamal": ({"deploy", "rollback", "remove", "setup"}, False),
    "heroku": ({"releases:rollback", "apps:destroy", "pg:reset"}, False), "copilot": ({"deploy", "delete"}, False),
    "amplify": ({"publish", "push", "delete"}, False), "helmfile": ({"apply", "sync", "destroy", "delete"}, False),
    "argocd": ({"sync"}, False), "skaffold": ({"run", "deploy", "delete"}, False),
    "dokku": ({"apps:destroy"}, False), "zappa": ({"deploy", "update", "undeploy"}, False),
}


def _deployer(name: str, args: list[str], ctx: Context) -> None:
    verbs, bare = DEPLOYERS[name]
    pos = positional(args, {"--token", "--scope", "-t", "--profile", "--project", "-P", "--app", "-a", "-c",
                            "--context", "--region", "-r", "--env", "-e", "--config", "--cwd", "--site"}, limit=3)
    if (bare and not pos) or set(pos[:2]) & verbs or (name == "vercel" and "--prod" in args) or (
            name == "argocd" and pos[:2] == ["app", "sync"]):
        ctx.hit("deploy", "deny", f"{name} {' '.join(pos[:2]) or '(deploys by default)'}")


AWS_VALUE_OPTS = {"--profile", "--region", "--output", "--endpoint-url", "--query", "--color", "--ca-bundle",
                  "--cli-read-timeout", "--cli-connect-timeout", "--cli-binary-format"}
AWS_MUTATING = re.compile(
    r"^(create|delete|put|update|terminate|run|modify|attach|detach|remove|start|stop|reboot|deregister|register|"
    r"revoke|authorize|restore|copy|sync|mv|rm|rb|mb|cp|invoke|publish|send|tag|untag|deploy|execute|import|reset|"
    r"replace|associate|disassociate|enable|disable|cancel|set|add|apply|purge|release|allocate|accept|reject|"
    r"change|batch|upload|initiate|complete|abort|rotate|schedule|promote|failover|rollback|admin-)")
CFN_DENY = {"deploy", "create-stack", "update-stack", "delete-stack", "execute-change-set", "create-stack-set",
            "update-stack-set", "delete-stack-set", "rollback-stack", "continue-update-rollback"}


def _aws(name: str, args: list[str], ctx: Context) -> None:
    pos = positional(args, AWS_VALUE_OPTS, limit=2)
    if len(pos) < 2:
        return
    service, op = pos[0], pos[1]
    if service == "cloudformation" and op in CFN_DENY:
        ctx.hit("deploy", "deny", f"aws cloudformation {op}")
    elif AWS_MUTATING.match(op):
        ctx.hit("cloud-mutate", "ask", f"aws {service} {op}")


GCLOUD_MUTATING = {"create", "delete", "deploy", "update", "set-iam-policy", "add-iam-policy-binding",
                   "remove-iam-policy-binding", "patch", "resize", "reset", "import", "rm", "cp", "mv", "rsync",
                   "remove", "stop", "start", "restart", "undelete", "execute", "submit", "promote", "rollback",
                   "add", "apply", "enable", "disable", "rotate", "upload", "exec", "suspend", "resume", "rollout",
                   "replace", "move", "attach", "detach", "abandon", "revoke", "destroy", "disable-vpc-peering"}
GCLOUD_VALUE_OPTS = {"--project", "--account", "--configuration", "--impersonate-service-account", "--format",
                     "--verbosity", "--region", "--zone", "--billing-project", "--flags-file"}


def _gcloud(name: str, args: list[str], ctx: Context) -> None:
    pos = positional(args, GCLOUD_VALUE_OPTS, limit=6)
    if name == "gsutil":
        pos = positional(args, {"-o", "-h", "-u", "-i"}, limit=2)
        if pos and pos[0] in ("rm", "mv", "cp", "rsync", "rb", "mb", "setmeta", "acl", "iam", "defacl", "lifecycle",
                              "retention", "notification", "compose"):
            ctx.hit("cloud-mutate", "ask", f"gsutil {pos[0]}")
        return
    if name == "bq":
        pos = positional(args, {"--project_id", "--location", "--dataset_id", "--format", "-n"}, limit=2)
        if pos and (pos[0] in ("rm", "mk", "load", "cp", "update", "insert", "truncate", "undelete") or (
                pos[0] == "query" and re.search(r"(?i)\b(insert|update|delete|merge|drop|truncate|create|alter)\b",
                                                " ".join(args)[:MAX_CHARS]))):
            ctx.hit("cloud-mutate", "ask", f"bq {pos[0]}")
        return
    if pos[:1] == ["config"] or (pos[:1] == ["auth"] and not set(pos[1:2]) & {"revoke"}):
        return
    if set(pos[1:6]) & GCLOUD_MUTATING:
        ctx.hit("cloud-mutate", "ask", "gcloud " + " ".join(pos[:3]))


AZ_MUTATING = {"create", "delete", "deploy", "update", "set", "purge", "restart", "stop", "start", "remove", "add",
               "import", "assign", "upload", "swap", "scale", "invoke", "run-command", "reset", "rotate",
               "regenerate", "failover", "restore", "deallocate", "redeploy", "reimage", "upgrade", "sync"}


def _az(name: str, args: list[str], ctx: Context) -> None:
    pos = positional(args, {"--subscription", "-o", "--output", "--query", "-g", "--resource-group", "-n", "--name"},
                     limit=6)
    if pos[:1] in (["login"], ["logout"], ["account"]) and not set(pos[1:]) & {"clear"}:
        return
    if set(pos[1:6]) & AZ_MUTATING:
        ctx.hit("cloud-mutate", "ask", "az " + " ".join(pos[:3]))


def _doctl(name: str, args: list[str], ctx: Context) -> None:
    pos = positional(args, {"--context", "-t", "--access-token", "-o", "--output"}, limit=5)
    if set(pos[1:5]) & (GCLOUD_MUTATING | {"create-deployment", "deploy"}):
        ctx.hit("cloud-mutate", "ask", f"{name} " + " ".join(pos[:3]))


KUBE_VALUE_OPTS = {"--context", "-n", "--namespace", "--kubeconfig", "-s", "--server", "--cluster", "--user",
                   "--token", "--as", "--as-group", "--request-timeout", "-l", "--selector", "-o", "--output"}
KUBE_MUTATING = {"apply", "create", "delete", "replace", "patch", "scale", "edit", "drain", "cordon", "uncordon",
                 "taint", "label", "annotate", "set", "autoscale", "expose", "run", "cp", "debug", "certificate"}


def _kubectl(name: str, args: list[str], ctx: Context) -> None:
    pos = positional(args, KUBE_VALUE_OPTS, limit=3)
    if not pos:
        return
    if pos[0] in KUBE_MUTATING or (pos[0] == "rollout" and set(pos[1:2]) & {"restart", "undo", "pause", "resume"}) \
            or (pos[0] == "kustomize" and False):
        ctx.hit("kubectl-mutate", "ask", f"{name} {' '.join(pos[:2])}")


def _helm(name: str, args: list[str], ctx: Context) -> None:
    pos = positional(args, KUBE_VALUE_OPTS | {"--kube-context", "-f", "--values", "--version", "--set"}, limit=3)
    if not pos:
        return
    if pos[0] == "push" or pos[:2] == ["cm-push"] or pos[0] == "cm-push":
        ctx.hit("publish", "deny", "helm push")
    elif pos[0] in ("install", "upgrade", "uninstall", "rollback", "delete", "del", "un") or pos[:2] == [
            "plugin", "install"]:
        ctx.hit("helm-mutate", "ask", f"helm {pos[0]}")


SYSTEM_DIRS = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64", "/opt", "/proc", "/root", "/sbin",
               "/srv", "/sys", "/usr", "/var", "/Users", "/System", "/Library", "/Applications", "/private",
               "/Volumes", "/nix", "/snap", "/mnt", "/media"}


def _dangerous_target(word: str, dot: bool = True) -> bool:
    w = word.strip()
    if not w:
        return False
    w = re.sub(r"(/\*|/\.\*|/)+$", "", w) or "/"
    if w in ("*", ".*", "~", "$HOME", "${HOME}", "~root") or (w.startswith("~") and "/" not in w):
        return True
    if w == "." and dot:
        return True
    if re.fullmatch(r"\.\.(/\.\.)*", w):
        return True
    if w in SYSTEM_DIRS or w in ("/*",):
        return True
    home = os.path.expanduser("~").rstrip("/")
    if home and home != "/" and w == home:
        return True
    return False


def _rm(name: str, args: list[str], ctx: Context) -> None:
    if "--no-preserve-root" in args:
        ctx.hit("rm-root", "deny", "--no-preserve-root")
        return
    recursive = any(a in ("--recursive",) or (a.startswith("-") and not a.startswith("--") and set(a[1:]) & {"r", "R"})
                    for a in args)
    targets = positional(args, limit=64)
    if recursive and any(_dangerous_target(t) for t in targets):
        ctx.hit("rm-root", "deny")
    for t in targets:
        _check_write_target(t, ctx)


def _find(name: str, args: list[str], ctx: Context) -> None:
    paths = []
    for a in args:
        if a.startswith("-") or a in ("(", "!", ")"):
            break
        paths.append(a)
    deletes = "-delete" in args or any(a in ("-exec", "-execdir", "-ok", "-okdir") and k + 1 < len(args) and
                                       base(args[k + 1]) in ("rm", "shred", "unlink") for k, a in enumerate(args))
    if deletes and any(_dangerous_target(p, dot=False) for p in paths):
        ctx.hit("rm-root", "deny", "find … -delete on a root, home or system folder")
    for k, a in enumerate(args):
        if a in ("-exec", "-execdir", "-ok", "-okdir"):
            end = next((j for j in range(k + 1, len(args)) if args[j] in (";", "+", "\\;")), len(args))
            inner = [w for w in args[k + 1:end] if w != "{}"]
            if inner:
                _command(Command(words=inner), ctx, 1)


def _chmod(name: str, args: list[str], ctx: Context) -> None:
    if any(a in ("-R", "--recursive") or (a.startswith("-") and "R" in a[1:]) for a in args) and any(
            _dangerous_target(t, dot=False) for t in positional(args)[1:]):
        ctx.hit("chmod-root", "deny")


def _disk(name: str, args: list[str], ctx: Context) -> None:
    if name == "dd":
        if any(a.startswith("of=/dev/") and not re.match(r"of=/dev/(null|stdout|stderr|fd/)", a) for a in args):
            ctx.hit("disk-wipe", "deny")
    elif name == "diskutil":
        if any(a.lower().startswith(("erase", "zerodisk", "randomdisk", "secureerase", "partitiondisk")) for a in args):
            ctx.hit("disk-wipe", "deny")
    elif name in ("shred", "wipefs", "blkdiscard", "sgdisk", "parted", "sfdisk", "cfdisk"):
        if any(a.startswith("/dev/") for a in args):
            ctx.hit("disk-wipe", "deny")
    elif not (name == "fdisk" and "-l" in args):
        ctx.hit("disk-wipe", "deny")


SQL_CLIENTS = {"psql", "mysql", "mariadb", "sqlcmd", "snowsql", "clickhouse-client", "clickhouse", "duckdb",
               "sqlite3", "cqlsh", "mongosh", "mongo", "redis-cli", "pgcli", "mycli", "litecli", "usql", "trino",
               "presto", "spark-sql", "beeline", "isql", "sqlplus", "cockroach", "turso", "supabase"}


def _sql(name: str, args: list[str], ctx: Context) -> None:
    ctx.hit("sql-client", "allow")  # marker: the text-level destructive-SQL rule applies to this command


def _factoryctl(name: str, args: list[str], ctx: Context) -> None:
    if ctx.agent:
        ctx.hit("factory-state", "deny", "agents never drive factoryctl")


CHECKS = {
    "git": _git, "gh": _gh, "hub": _gh,
    "npm": _node_pm, "pnpm": _node_pm, "yarn": _node_pm, "bun": _node_pm, "lerna": _node_pm, "changeset": _node_pm,
    "docker": _containers, "podman": _containers, "nerdctl": _containers, "buildah": _containers,
    "terraform": _terraform, "tofu": _terraform, "terragrunt": _terraform, "pulumi": _pulumi,
    "aws": _aws, "gcloud": _gcloud, "gsutil": _gcloud, "bq": _gcloud, "az": _az, "doctl": _doctl, "linode-cli": _doctl,
    "kubectl": _kubectl, "oc": _kubectl, "k": _kubectl, "helm": _helm,
    "rm": _rm, "find": _find, "chmod": _chmod, "chown": _chmod, "chgrp": _chmod,
    "dd": _disk, "mkfs": _disk, "fdisk": _disk, "diskutil": _disk, "shred": _disk, "wipefs": _disk,
    "blkdiscard": _disk, "sgdisk": _disk, "parted": _disk, "sfdisk": _disk, "cfdisk": _disk,
    "gradle": _gradle, "gradlew": _gradle, "factoryctl": _factoryctl,
}
CHECKS.update({k: _deployer for k in DEPLOYERS})
CHECKS.update({k: _sql for k in SQL_CLIENTS})
CHECKS.update({k: _simple_publish(PUBLISHERS) for k, v in PUBLISHERS.items() if v})


def _check_for(name: str):
    if name.startswith("mkfs."):
        return _disk
    return CHECKS.get(name)


# ---- policy -----------------------------------------------------------------------------------------------------
_POLICY: dict | None = None


def policy() -> dict:
    global _POLICY
    if _POLICY is None:
        data = read_json(plugin_path("guard-policy.json"), default={"rules": []}) or {"rules": []}
        rules = []
        for r in data.get("rules", []):
            entry = dict(r)
            if r.get("pattern"):
                entry["rx"] = re.compile(r["pattern"], re.S)
            rules.append(entry)
        _POLICY = {"rules": rules, "by_id": {r["id"]: r for r in rules},
                   "max_chars": int(data.get("max_command_chars") or MAX_CHARS)}
    return _POLICY


def evaluate(command: str, *, agent: bool = False, cwd: str | None = None, protect: str | None = None,
             allow_writes: tuple | None = None) -> tuple[str, dict | None]:
    """('allow'|'ask'|'deny', rule) for one Bash command. deny wins over ask."""
    pol = policy()
    text = command or ""
    if len(text) > pol["max_chars"]:
        return "ask", _rule(pol, "guard-limits", "ask", f"the command is longer than {pol['max_chars']} characters")
    root = os.path.realpath(protect) if protect else None
    ctx = Context(agent=agent, cwd=os.path.realpath(cwd) if cwd else None, protect=root,
                  state=os.path.join(root, ".factory") if root else None,
                  allow_writes=None if allow_writes is None else tuple(os.path.realpath(p) for p in allow_writes))
    analyze(text, ctx)
    sql = any(f.rule == "sql-client" for f in ctx.findings)
    for r in pol["rules"]:
        rx = r.get("rx")
        if rx is None or (r.get("agents_only") and not agent) or (r.get("when") == "sql-client" and not sql):
            continue
        if rx.search(text):
            ctx.hit(r["id"], r["decision"])
    best: tuple[str, dict | None] = ("allow", None)
    for f in ctx.findings:
        rule = _rule(pol, f.rule, f.decision, f.detail)
        decision = rule["decision"] if f.decision != "allow" else "allow"
        if ORDER[decision] > ORDER[best[0]]:
            best = (decision, rule)
            if decision == "deny":
                break
    return best


def _rule(pol: dict, rule_id: str, decision: str, detail: str = "") -> dict:
    known = pol["by_id"].get(rule_id) or {}
    reason = known.get("reason") or {
        "guard-limits": "The guard could not check this command, so a human should look at it.",
        "variable-command": "This runs a command whose name is in a variable.",
        "remote-shell": "This runs commands on another machine.",
        "inline-code": "This inline program mentions a push, publish, deploy or the factory's own state.",
        "main-checkout": "Factory agents change code only in the job's worktree; your checkout belongs to you.",
        "factory-state": "Agents never touch the factory's own state (job.json, messages, entries, factoryctl).",
    }.get(rule_id, "Blocked by the factory guard.")
    out = {"id": rule_id, "decision": known.get("decision", decision) if decision != "allow" else "allow",
           "reason": reason}
    if detail:
        out["reason"] = f"{reason} ({detail})"
    return out
