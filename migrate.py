#!/usr/bin/env python3
"""
Migration Script: Migrates local git repositories to GitLab.

Usage:
    python migrate.py --repo-dir <path> --namespace <gitlab-namespace-url> --pat <token>
                      [--dry-run] [--verbose]
"""

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

import requests


def setup_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=level,
    )
    return logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate local git repositories to GitLab.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-dir",
        required=True,
        metavar="PATH",
        help="Directory containing local git repositories to migrate.",
    )
    parser.add_argument(
        "--namespace",
        required=True,
        metavar="URL",
        help="GitLab group/namespace URL (e.g. https://gitlab.example.com/mygroup).",
    )
    parser.add_argument(
        "--pat",
        required=True,
        metavar="TOKEN",
        help="GitLab Personal Access Token with API permissions.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without creating projects or pushing code.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging.",
    )
    return parser.parse_args()


def discover_repos(repo_dir: Path, log: logging.Logger) -> list[Path]:
    """Find all git repositories directly under repo_dir."""
    repos = []
    for entry in sorted(repo_dir.iterdir()):
        if entry.is_dir() and (entry / ".git").exists():
            log.info("Discovered repository: %s", entry.name)
            repos.append(entry)
        else:
            log.debug("Skipping non-repo path: %s", entry)
    return repos


def parse_gitlab_base_and_namespace(namespace_url: str) -> tuple[str, str]:
    """
    Split a GitLab namespace URL into base URL and namespace path.

    Example:
        https://gitlab.example.com/mygroup  ->  ("https://gitlab.example.com", "mygroup")
        https://gitlab.example.com/a/b      ->  ("https://gitlab.example.com", "a/b")
    """
    # Strip trailing slash
    url = namespace_url.rstrip("/")
    # Detect scheme + host (everything up to the third slash, if present)
    parts = url.split("/")
    # parts[0] = "https:", parts[1] = "", parts[2] = "hostname", parts[3..] = namespace
    if len(parts) < 4:
        raise ValueError(
            f"Cannot parse GitLab namespace URL: {namespace_url!r}. "
            "Expected format: https://hostname/namespace"
        )
    base_url = "/".join(parts[:3])
    namespace_path = "/".join(parts[3:])
    return base_url, namespace_path


def validate_auth(base_url: str, pat: str, log: logging.Logger) -> None:
    """Verify the PAT is valid by calling the /api/v4/user endpoint."""
    url = f"{base_url}/api/v4/user"
    log.debug("Validating PAT against %s", url)
    try:
        resp = requests.get(url, headers={"PRIVATE-TOKEN": pat}, timeout=15)
    except requests.RequestException as exc:
        log.error("Network error while validating PAT: %s", exc)
        sys.exit(1)

    if resp.status_code == 401:
        log.error(
            "Authentication failed: PAT is invalid or lacks permissions (HTTP 401)."
        )
        sys.exit(1)
    if not resp.ok:
        log.error(
            "Unexpected response while validating PAT: HTTP %s – %s",
            resp.status_code,
            resp.text,
        )
        sys.exit(1)

    user = resp.json()
    log.info("Authenticated as GitLab user: %s", user.get("username", "<unknown>"))


def project_exists(base_url: str, pat: str, namespace_path: str, project_name: str, log: logging.Logger) -> bool:
    """Return True if a project with the given name already exists in the namespace."""
    full_path = f"{namespace_path}/{project_name}".strip("/")
    encoded = requests.utils.quote(full_path, safe="")
    url = f"{base_url}/api/v4/projects/{encoded}"
    log.debug("Checking existence of project at %s", url)
    try:
        resp = requests.get(url, headers={"PRIVATE-TOKEN": pat}, timeout=15)
    except requests.RequestException as exc:
        log.warning("Network error checking project existence for %s: %s", project_name, exc)
        return False
    return resp.status_code == 200


def create_project(
    base_url: str,
    pat: str,
    namespace_path: str,
    project_name: str,
    log: logging.Logger,
) -> str | None:
    """
    Create a GitLab project. Returns the remote URL on success, None on failure.
    The namespace may be a group path or a username.
    """
    # Resolve namespace_id (group or user)
    encoded_ns = requests.utils.quote(namespace_path, safe="")
    ns_url = f"{base_url}/api/v4/namespaces/{encoded_ns}"
    log.debug("Resolving namespace id for %s", namespace_path)
    try:
        ns_resp = requests.get(ns_url, headers={"PRIVATE-TOKEN": pat}, timeout=15)
    except requests.RequestException as exc:
        log.error("Network error resolving namespace for %s: %s", project_name, exc)
        return None

    if not ns_resp.ok:
        log.error(
            "Could not resolve namespace '%s': HTTP %s – %s",
            namespace_path,
            ns_resp.status_code,
            ns_resp.text,
        )
        return None

    namespace_id = ns_resp.json().get("id")
    log.debug("Namespace id = %s", namespace_id)

    create_url = f"{base_url}/api/v4/projects"
    payload = {
        "name": project_name,
        "path": project_name,
        "namespace_id": namespace_id,
        "visibility": "private",
    }
    log.debug("Creating project %s in namespace %s (id=%s)", project_name, namespace_path, namespace_id)
    try:
        resp = requests.post(
            create_url,
            headers={"PRIVATE-TOKEN": pat},
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        log.error("Network error creating project %s: %s", project_name, exc)
        return None

    if not resp.ok:
        log.error(
            "Failed to create project %s: HTTP %s – %s",
            project_name,
            resp.status_code,
            resp.text,
        )
        return None

    remote_url = resp.json().get("http_url_to_repo")
    log.info("Created GitLab project: %s -> %s", project_name, remote_url)
    return remote_url


def inject_token_into_url(url: str, pat: str) -> str:
    """Embed the PAT as HTTP Basic Auth credentials into the remote URL."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return f"{scheme}://oauth2:{pat}@{rest}"


def push_repo(repo_path: Path, remote_url: str, pat: str, log: logging.Logger) -> bool:
    """Push all branches and tags to the remote. Returns True on success."""
    authed_url = inject_token_into_url(remote_url, pat)

    # Add a temporary remote named 'gitlab-migration'
    remote_name = "gitlab-migration"
    log.debug("Adding remote '%s' -> %s", remote_name, remote_url)

    def run(cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
        )

    # Remove stale remote if it exists
    run(["git", "remote", "remove", remote_name])

    result = run(["git", "remote", "add", remote_name, authed_url])
    if result.returncode != 0:
        log.error("Failed to add remote for %s: %s", repo_path.name, result.stderr.strip())
        return False

    log.info("Pushing branches and tags for %s ...", repo_path.name)
    push_result = run(["git", "push", "--mirror", remote_name])

    # Clean up temporary remote regardless of outcome
    run(["git", "remote", "remove", remote_name])

    if push_result.returncode != 0:
        log.error(
            "Push failed for %s:\n%s",
            repo_path.name,
            push_result.stderr.strip(),
        )
        return False

    log.debug("Push output for %s: %s", repo_path.name, push_result.stdout.strip())
    return True


def migrate_repo(
    repo: Path,
    base_url: str,
    namespace_path: str,
    pat: str,
    dry_run: bool,
    log: logging.Logger,
) -> str:
    """
    Migrate a single repository. Returns 'success', 'skipped', or 'failed'.
    """
    project_name = repo.name
    log.info("--- Processing: %s ---", project_name)

    if dry_run:
        log.info("[DRY-RUN] Would check if project '%s' exists in '%s'.", project_name, namespace_path)
        log.info("[DRY-RUN] Would create GitLab project '%s'.", project_name)
        log.info("[DRY-RUN] Would push all branches and tags from '%s'.", repo)
        return "success (dry-run)"

    if project_exists(base_url, pat, namespace_path, project_name, log):
        log.warning("Project '%s' already exists in namespace '%s'. Skipping.", project_name, namespace_path)
        return "skipped"

    remote_url = create_project(base_url, pat, namespace_path, project_name, log)
    if remote_url is None:
        return "failed"

    success = push_repo(repo, remote_url, pat, log)
    return "success" if success else "failed"


def print_summary(results: dict[str, str], log: logging.Logger) -> None:
    log.info("=" * 60)
    log.info("MIGRATION SUMMARY")
    log.info("=" * 60)
    for repo_name, status in results.items():
        log.info("  %-40s %s", repo_name, status.upper())
    log.info("=" * 60)
    counts = {}
    for status in results.values():
        counts[status] = counts.get(status, 0) + 1
    for status, count in sorted(counts.items()):
        log.info("  %s: %d", status.capitalize(), count)


def main() -> None:
    args = parse_args()
    log = setup_logging(args.verbose)

    repo_dir = Path(args.repo_dir).expanduser().resolve()
    if not repo_dir.is_dir():
        log.error("Repo directory does not exist or is not a directory: %s", repo_dir)
        sys.exit(1)

    try:
        base_url, namespace_path = parse_gitlab_base_and_namespace(args.namespace)
    except ValueError as exc:
        log.error("%s", exc)
        sys.exit(1)

    if args.dry_run:
        log.info("DRY-RUN mode enabled – no GitLab projects will be created.")
    else:
        validate_auth(base_url, args.pat, log)

    repos = discover_repos(repo_dir, log)
    if not repos:
        log.warning("No git repositories found in %s.", repo_dir)
        sys.exit(0)

    log.info("Found %d repository/repositories to migrate.", len(repos))

    results: dict[str, str] = {}
    for repo in repos:
        status = migrate_repo(repo, base_url, namespace_path, args.pat, args.dry_run, log)
        results[repo.name] = status

    print_summary(results, log)


if __name__ == "__main__":
    main()
