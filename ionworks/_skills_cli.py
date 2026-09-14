"""``ionworks skills`` — install and update the Ionworks Agentic Toolkit.

The toolkit is a set of ``SKILL.md`` files served as a zip by the Ionworks API
to any caller holding an API key. Fetching it by hand takes a curl, an unzip, a
per-agent copy, and — to update — a loop that removes the previously installed
skills before the new ones land. This module does that instead.

Scope and agent selection deliberately mirror the ``npx skills`` CLI, which is
the convention this ecosystem already has:

* project scope by default, ``-g/--global`` for the user-level directory
* ``-a/--agent`` to target agents explicitly, auto-detection otherwise

Removal-before-copy is the part worth having in code rather than in docs: the
set to remove has to be read from the *previous* install, because a skill
retired upstream is absent from the incoming release and would otherwise be
left behind for the agent to keep loading.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

__all__ = ["main"]

#: Where each agent reads skills from, relative to the project root or to the
#: user's home. Mirrors the layout ``npx skills`` writes.
_AGENT_SKILL_DIRS: dict[str, tuple[str, ...]] = {
    "claude-code": (".claude", "skills"),
    "cursor": (".cursor", "skills"),
    "github-copilot": (".github", "skills"),
    "codex": (".codex", "skills"),
    "opencode": (".opencode", "skills"),
    "windsurf": (".windsurf", "skills"),
}

#: Marker that means "this agent is set up here" during auto-detection: the
#: agent's own config directory, not its skills subdirectory (which does not
#: exist until something is installed).
_AGENT_ROOT_DIRS: dict[str, str] = {
    name: parts[0] for name, parts in _AGENT_SKILL_DIRS.items()
}

#: Written next to the installed skills so a later update knows exactly what
#: this tool put there, instead of inferring it from the new release.
_MANIFEST_NAME = ".ionworks-skills.json"

_DEFAULT_API_URL = "https://api.ionworks.com"


class SkillsCliError(Exception):
    """A user-facing failure: reported as a message, not a traceback."""


@dataclass(frozen=True)
class Target:
    """One agent's skills directory, resolved to an absolute path."""

    agent: str
    path: Path


def _api_url() -> str:
    return (os.getenv("IONWORKS_API_URL") or _DEFAULT_API_URL).rstrip("/")


def _api_key() -> str:
    key = os.getenv("IONWORKS_API_KEY")
    if not key:
        raise SkillsCliError(
            "IONWORKS_API_KEY is not set. Create a key on your Ionworks Account "
            "page, then export it:\n\n    export IONWORKS_API_KEY='iw_...'"
        )
    return key


def detect_agents(root: Path) -> list[str]:
    """Return agents that appear to be set up under ``root``.

    Parameters
    ----------
    root : Path
        Directory to probe — the project root, or the user's home for a global
        install.

    Returns
    -------
    list of str
        Agent names, sorted for stable output. Empty when none are found.
    """
    return sorted(
        agent
        for agent, config_dir in _AGENT_ROOT_DIRS.items()
        if (root / config_dir).is_dir()
    )


def resolve_targets(
    agents: list[str] | None, *, global_scope: bool, project_root: Path, home: Path
) -> list[Target]:
    """Resolve which agent directories to write to.

    Parameters
    ----------
    agents : list of str, optional
        Explicit agent names. When None, agents are auto-detected in the scope's
        root.
    global_scope : bool
        Install to the user-level directory rather than the project.
    project_root : Path
        Directory treated as the project root for project scope.
    home : Path
        The user's home directory, used for global scope.

    Returns
    -------
    list of Target
        One entry per agent to install into.

    Raises
    ------
    SkillsCliError
        If an agent name is unknown, or if nothing could be detected.
    """
    root = home if global_scope else project_root

    if agents:
        unknown = [a for a in agents if a not in _AGENT_SKILL_DIRS]
        if unknown:
            raise SkillsCliError(
                f"Unknown agent(s): {', '.join(unknown)}. "
                f"Supported: {', '.join(sorted(_AGENT_SKILL_DIRS))}"
            )
        chosen = agents
    else:
        chosen = detect_agents(root)
        if not chosen:
            scope = "globally" if global_scope else f"in {project_root}"
            raise SkillsCliError(
                f"No coding agent detected {scope}. Pass --agent to choose one "
                f"explicitly, e.g. --agent claude-code. "
                f"Supported: {', '.join(sorted(_AGENT_SKILL_DIRS))}"
            )

    return [Target(agent=a, path=root.joinpath(*_AGENT_SKILL_DIRS[a])) for a in chosen]


def fetch_bundle(*, api_url: str, api_key: str) -> bytes:
    """Download the toolkit zip.

    Parameters
    ----------
    api_url : str
        API base URL.
    api_key : str
        Ionworks API key, sent as ``X-API-Key``.

    Returns
    -------
    bytes
        The zip archive.

    Raises
    ------
    SkillsCliError
        On any HTTP or transport failure, translated to an actionable message.
    """
    import requests

    url = f"{api_url}/agent/skills.zip"
    try:
        response = requests.get(url, headers={"X-API-Key": api_key}, timeout=120)
    except requests.RequestException as exc:
        raise SkillsCliError(f"Could not reach {url}: {exc}") from exc

    if response.status_code in (401, 403):
        raise SkillsCliError(
            "The API rejected IONWORKS_API_KEY (HTTP "
            f"{response.status_code}). Check the key is current and belongs to "
            "the environment you are targeting."
        )
    if not response.ok:
        raise SkillsCliError(
            f"Download failed with HTTP {response.status_code} from {url}."
        )
    return response.content


def _skill_names(archive: zipfile.ZipFile) -> list[str]:
    """Names of the skills inside the archive's ``<root>/skills/`` directory."""
    names = set()
    for entry in archive.namelist():
        parts = Path(entry).parts
        # <archive-root>/skills/<skill-name>/...
        if len(parts) >= 3 and parts[1] == "skills":
            names.add(parts[2])
    return sorted(names)


def _read_manifest(target: Path) -> list[str]:
    """Skills this tool installed into ``target`` previously, if recorded."""
    manifest = target / _MANIFEST_NAME
    try:
        data = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    skills = data.get("skills")
    return [str(s) for s in skills] if isinstance(skills, list) else []


def _write_manifest(target: Path, *, skills: list[str], version: str | None) -> None:
    payload = {"skills": skills, "version": version}
    (target / _MANIFEST_NAME).write_text(json.dumps(payload, indent=2) + "\n")


def install_into(
    target: Target, archive_bytes: bytes, *, version: str | None
) -> tuple[int, int]:
    """Install the toolkit into one agent directory, replacing a prior install.

    Removes what a previous run recorded in the manifest before copying, so a
    skill retired upstream does not survive as an orphan. Falls back to the
    incoming skill names when no manifest exists (a first run, or a hand
    install) — that still cleans what it is about to overwrite.

    Parameters
    ----------
    target : Target
        Agent and destination directory.
    archive_bytes : bytes
        The toolkit zip.
    version : str, optional
        Release version, recorded in the manifest.

    Returns
    -------
    tuple of (int, int)
        ``(installed, retired)`` — skills copied in, and skills deleted that are
        *not* in the incoming release (i.e. genuinely gone upstream). Skills that
        were simply replaced are not counted, since nothing was lost.
    """
    archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    incoming = _skill_names(archive)
    if not incoming:
        raise SkillsCliError("The downloaded archive contained no skills.")

    target.path.mkdir(parents=True, exist_ok=True)

    # Prefer the manifest: it names what this tool installed, including skills
    # the incoming release no longer has. Without one (first run, or a previous
    # hand install) fall back to the incoming names, which still clears what is
    # about to be overwritten.
    previous = _read_manifest(target.path) or incoming
    retired = 0
    for name in previous:
        stale = target.path / name
        existed = stale.exists()
        if stale.is_dir():
            shutil.rmtree(stale)
        elif existed:
            stale.unlink()
        if existed and name not in incoming:
            retired += 1

    root = Path(archive.namelist()[0]).parts[0]
    prefix = f"{root}/skills/"
    for entry in archive.namelist():
        if not entry.startswith(prefix) or entry.endswith("/"):
            continue
        relative = Path(entry).relative_to(f"{root}/skills")
        destination = target.path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(entry) as src, open(destination, "wb") as dst:
            shutil.copyfileobj(src, dst)

    _write_manifest(target.path, skills=incoming, version=version)
    return len(incoming), retired


def _bundle_version(*, api_url: str, api_key: str) -> str | None:
    """Best-effort toolkit version, for reporting. None when unavailable."""
    import requests

    try:
        response = requests.get(
            f"{api_url}/agent/skills",
            headers={"X-API-Key": api_key},
            timeout=30,
        )
        if response.ok:
            version = response.json().get("version")
            return str(version) if version else None
    except (requests.RequestException, ValueError):
        return None
    return None


def _run_install(args: argparse.Namespace) -> int:
    api_url, api_key = _api_url(), _api_key()
    targets = resolve_targets(
        args.agent,
        global_scope=args.global_scope,
        project_root=Path.cwd(),
        home=Path.home(),
    )

    # Download before the version lookup: the download surfaces auth and
    # connectivity failures with a precise message, while the version lookup is
    # best-effort and returns None on the same failures. Doing it first would
    # print an optimistic "Downloading …" line above the real error.
    print(f"Downloading Ionworks Agentic Toolkit from {api_url} …")
    archive_bytes = fetch_bundle(api_url=api_url, api_key=api_key)
    version = _bundle_version(api_url=api_url, api_key=api_key)
    if version:
        print(f"  version {version}")

    for target in targets:
        installed, retired = install_into(target, archive_bytes, version=version)
        detail = f", {retired} retired skill(s) removed" if retired else ""
        print(f"  {target.agent}: {installed} skills → {target.path}{detail}")

    scope = "global" if args.global_scope else "project"
    print(f"Done ({scope} scope). Ask your agent to use the `install` skill to verify.")
    return 0


def _run_list(args: argparse.Namespace) -> int:
    root = Path.home() if args.global_scope else Path.cwd()
    targets = [
        Target(agent=a, path=root.joinpath(*_AGENT_SKILL_DIRS[a]))
        for a in (args.agent or sorted(_AGENT_SKILL_DIRS))
    ]
    found = False
    for target in targets:
        skills = _read_manifest(target.path)
        if not skills:
            continue
        found = True
        data = json.loads((target.path / _MANIFEST_NAME).read_text())
        version = data.get("version") or "unknown"
        print(f"{target.agent}: v{version}, {len(skills)} skills → {target.path}")
    if not found:
        scope = "global" if args.global_scope else "project"
        print(f"No Ionworks skills installed in {scope} scope.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ionworks",
        description="Ionworks command line tools.",
    )
    sub = parser.add_subparsers(dest="group", required=True)

    skills = sub.add_parser("skills", help="Manage the Ionworks Agentic Toolkit.")
    skills_sub = skills.add_subparsers(dest="command", required=True)

    def add_scope_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "-g",
            "--global",
            dest="global_scope",
            action="store_true",
            help="Use the user-level directory (~/) instead of this project.",
        )
        p.add_argument(
            "-a",
            "--agent",
            nargs="+",
            metavar="AGENT",
            help=(
                "Target agents explicitly (default: auto-detect). Supported: "
                + ", ".join(sorted(_AGENT_SKILL_DIRS))
            ),
        )

    install = skills_sub.add_parser(
        "install",
        help="Download the toolkit and install it into your agent(s).",
        description=(
            "Download the toolkit and install it. Re-run to update: skills from "
            "the previous install are removed first, so anything retired "
            "upstream does not linger."
        ),
    )
    add_scope_flags(install)
    install.set_defaults(func=_run_install)

    # `update` is the same operation — install already replaces a prior install
    # — but users look for the word, and aliasing is cheaper than explaining.
    update = skills_sub.add_parser(
        "update",
        help="Alias for `install`: fetch the latest and replace what is there.",
    )
    add_scope_flags(update)
    update.set_defaults(func=_run_install)

    listing = skills_sub.add_parser(
        "list", help="Show which Ionworks skills are installed."
    )
    add_scope_flags(listing)
    listing.set_defaults(func=_run_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``ionworks`` console script.

    Parameters
    ----------
    argv : list of str, optional
        Arguments to parse. Defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        Process exit status.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SkillsCliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130
