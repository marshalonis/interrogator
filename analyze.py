#!/usr/bin/env python3
"""
Analysis Script: Analyzes local git repositories and produces modernization reports
using AWS Bedrock LLMs.

Usage:
    python analyze.py --repo-dir <path> --output-dir <path> --pat <token>
                      [--aws-region <region>] [--verbose]
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# ---------------------------------------------------------------------------
# Language detection by file extension
# ---------------------------------------------------------------------------

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".c": "C",
    ".h": "C/C++ Header",
    ".hpp": "C++ Header",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala",
    ".r": "R",
    ".R": "R",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "SASS",
    ".less": "LESS",
    ".xml": "XML",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".tf": "Terraform",
    ".tfvars": "Terraform",
    ".gradle": "Gradle",
    ".groovy": "Groovy",
    ".lua": "Lua",
    ".pl": "Perl",
    ".pm": "Perl",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hrl": "Erlang",
    ".hs": "Haskell",
    ".clj": "Clojure",
    ".dart": "Dart",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".ipynb": "Jupyter Notebook",
}

# Directories to skip during scanning
SKIP_DIRS: set[str] = {
    ".git", "__pycache__", "node_modules", ".tox", ".venv", "venv",
    ".env", "dist", "build", "target", ".idea", ".vscode",
}

# Maximum characters of code to send to the LLM
MAX_SAMPLE_CHARS = 20_000

# Default Bedrock model
DEFAULT_MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=level,
    )
    return logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze git repositories and produce modernization reports via AWS Bedrock.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-dir",
        required=True,
        metavar="PATH",
        help=(
            "Path to a single git repository OR a directory containing multiple "
            "git repositories (batch mode)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        metavar="PATH",
        help="Directory where {project_name}_report.json files will be written.",
    )
    parser.add_argument(
        "--pat",
        required=True,
        metavar="TOKEN",
        help="GitLab Personal Access Token (reserved for future API use).",
    )
    parser.add_argument(
        "--aws-region",
        default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        metavar="REGION",
        help="AWS region for Bedrock API calls (default: us-east-1 or AWS_DEFAULT_REGION).",
    )
    parser.add_argument(
        "--model-id",
        default=DEFAULT_MODEL_ID,
        metavar="MODEL_ID",
        help=f"Bedrock model ID to use for analysis (default: {DEFAULT_MODEL_ID}).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Repository discovery
# ---------------------------------------------------------------------------

def discover_repos(repo_dir: Path, log: logging.Logger) -> list[Path]:
    """
    Return a list of git repository paths.

    If repo_dir itself is a git repo, return [repo_dir].
    Otherwise return all immediate subdirectories that are git repos.
    """
    if (repo_dir / ".git").exists():
        log.info("Single repository mode: %s", repo_dir)
        return [repo_dir]

    repos = []
    for entry in sorted(repo_dir.iterdir()):
        if entry.is_dir() and (entry / ".git").exists():
            log.info("Discovered repository: %s", entry.name)
            repos.append(entry)
    return repos


# ---------------------------------------------------------------------------
# File scanning and language detection
# ---------------------------------------------------------------------------

def count_lines(file_path: Path) -> int:
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def scan_repository(repo_path: Path, log: logging.Logger) -> dict:
    """
    Walk the repository and return a metadata dict:
      - file_count: total source files found
      - total_lines_of_code: sum of lines across all source files
      - language_line_counts: {language: line_count}
      - language_file_counts: {language: file_count}
      - file_listing: list of relative file paths
      - code_sample: up to MAX_SAMPLE_CHARS of code for LLM context
    """
    log.info("Scanning repository: %s", repo_path.name)

    language_line_counts: dict[str, int] = {}
    language_file_counts: dict[str, int] = {}
    file_listing: list[str] = []
    code_sample_parts: list[str] = []
    sample_chars = 0

    for root, dirs, files in os.walk(repo_path):
        # Prune skip directories in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for filename in files:
            file_path = Path(root) / filename
            ext = file_path.suffix.lower()
            language = EXTENSION_TO_LANGUAGE.get(ext) or EXTENSION_TO_LANGUAGE.get(file_path.suffix)

            rel_path = file_path.relative_to(repo_path)
            file_listing.append(str(rel_path))

            if language is None:
                log.debug("Unknown extension, skipping language count: %s", rel_path)
                continue

            lines = count_lines(file_path)
            language_line_counts[language] = language_line_counts.get(language, 0) + lines
            language_file_counts[language] = language_file_counts.get(language, 0) + 1

            # Collect code samples
            if sample_chars < MAX_SAMPLE_CHARS:
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    snippet = content[: MAX_SAMPLE_CHARS - sample_chars]
                    code_sample_parts.append(f"### {rel_path}\n{snippet}")
                    sample_chars += len(snippet)
                except OSError:
                    pass

    total_lines = sum(language_line_counts.values())
    file_count = len(file_listing)

    log.info(
        "Scan complete for %s: %d files, %d lines of code",
        repo_path.name,
        file_count,
        total_lines,
    )
    log.debug("Languages found: %s", list(language_line_counts.keys()))

    return {
        "file_count": file_count,
        "total_lines_of_code": total_lines,
        "language_line_counts": language_line_counts,
        "language_file_counts": language_file_counts,
        "file_listing": file_listing,
        "code_sample": "\n\n".join(code_sample_parts),
    }


def compute_language_breakdown(language_line_counts: dict[str, int], total_lines: int) -> dict[str, float]:
    if total_lines == 0:
        return {}
    return {
        lang: round(count / total_lines * 100, 2)
        for lang, count in sorted(language_line_counts.items(), key=lambda x: -x[1])
    }


def detect_dependencies(repo_path: Path, log: logging.Logger) -> list[str]:
    """
    Heuristically extract dependency names from common manifest files.
    Returns a flat list of dependency strings.
    """
    deps: list[str] = []

    dependency_files = [
        ("requirements.txt", _parse_requirements_txt),
        ("pyproject.toml", _parse_pyproject_toml),
        ("package.json", _parse_package_json),
        ("pom.xml", _parse_pom_xml),
        ("build.gradle", _parse_build_gradle),
        ("go.mod", _parse_go_mod),
        ("Gemfile", _parse_gemfile),
        ("Cargo.toml", _parse_cargo_toml),
    ]

    for filename, parser in dependency_files:
        candidate = repo_path / filename
        if candidate.exists():
            log.debug("Parsing dependency file: %s", candidate)
            try:
                found = parser(candidate)
                deps.extend(found)
                log.debug("  Found %d dependencies in %s", len(found), filename)
            except Exception as exc:
                log.warning("Could not parse %s: %s", filename, exc)

    return sorted(set(deps))


def _parse_requirements_txt(path: Path) -> list[str]:
    deps = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("-"):
            # Strip version specifiers
            name = line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].split("!=")[0].split("[")[0].strip()
            if name:
                deps.append(name)
    return deps


def _parse_pyproject_toml(path: Path) -> list[str]:
    import re
    text = path.read_text(encoding="utf-8", errors="replace")
    deps = []
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in ("[tool.poetry.dependencies]", "[project]"):
            in_deps = True
        elif stripped.startswith("[") and in_deps:
            in_deps = False
        elif in_deps:
            # Match lines like: requests = "^2.0"  or  requests = {version = ...}
            m = re.match(r'^(\w[\w\-]*)\s*=', stripped)
            if m and m.group(1).lower() not in ("python", "name", "version", "description"):
                deps.append(m.group(1))
    return deps


def _parse_package_json(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    deps = list(data.get("dependencies", {}).keys())
    deps += list(data.get("devDependencies", {}).keys())
    return deps


def _parse_pom_xml(path: Path) -> list[str]:
    import xml.etree.ElementTree as ET
    tree = ET.parse(path)
    root = tree.getroot()
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    deps = []
    for dep in root.findall(".//m:dependency", ns):
        artifact = dep.find("m:artifactId", ns)
        if artifact is not None and artifact.text:
            deps.append(artifact.text.strip())
    return deps


def _parse_build_gradle(path: Path) -> list[str]:
    import re
    text = path.read_text(encoding="utf-8", errors="replace")
    # Match: implementation 'group:artifact:version'  or  implementation "group:artifact:version"
    pattern = re.compile(r"""(?:implementation|api|compile|runtimeOnly|testImplementation)\s+['"]([^'"]+)['"]""")
    deps = []
    for m in pattern.finditer(text):
        parts = m.group(1).split(":")
        if len(parts) >= 2:
            deps.append(parts[1])
    return deps


def _parse_go_mod(path: Path) -> list[str]:
    import re
    text = path.read_text(encoding="utf-8", errors="replace")
    deps = []
    in_require = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "require (":
            in_require = True
        elif stripped == ")" and in_require:
            in_require = False
        elif in_require or stripped.startswith("require "):
            m = re.match(r'(?:require\s+)?(\S+)\s+\S+', stripped)
            if m:
                deps.append(m.group(1))
    return deps


def _parse_gemfile(path: Path) -> list[str]:
    import re
    text = path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(r"""gem\s+['"](\w[\w\-]*)['"]""")
    return [m.group(1) for m in pattern.finditer(text)]


def _parse_cargo_toml(path: Path) -> list[str]:
    import re
    text = path.read_text(encoding="utf-8", errors="replace")
    deps = []
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in ("[dependencies]", "[dev-dependencies]", "[build-dependencies]"):
            in_deps = True
        elif stripped.startswith("[") and in_deps:
            in_deps = False
        elif in_deps:
            m = re.match(r'^([\w\-]+)\s*=', stripped)
            if m:
                deps.append(m.group(1))
    return deps


# ---------------------------------------------------------------------------
# AWS Bedrock LLM analysis
# ---------------------------------------------------------------------------

def build_analysis_prompt(project_name: str, file_listing: list[str], code_sample: str) -> str:
    file_list_str = "\n".join(file_listing[:200])  # Limit listing size
    return f"""You are a software modernization expert. Analyze the following repository and provide a structured JSON response.

Repository: {project_name}

File listing (up to 200 files):
{file_list_str}

Code samples:
{code_sample}

Respond ONLY with a valid JSON object containing these fields:
{{
  "inputs": ["<list of inputs: config files, env vars, CLI args, API endpoints consumed, message queues consumed, databases read, etc.>"],
  "outputs": ["<list of outputs: APIs exposed, files written, databases written, queues produced, external services called, etc.>"],
  "llm_recommendations": ["<list of modernization recommendations>"]
}}

Be specific and concise. If information is not determinable from the samples, make reasonable inferences from the file listing and project structure."""


def invoke_bedrock(
    bedrock_client,
    model_id: str,
    prompt: str,
    log: logging.Logger,
) -> dict | None:
    """
    Invoke a Bedrock model with the given prompt.
    Returns parsed JSON dict from the model response, or None on failure.
    """
    log.info("Invoking Bedrock model: %s", model_id)
    log.debug("Prompt length: %d characters", len(prompt))

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }

    try:
        response = bedrock_client.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json",
        )
    except ClientError as exc:
        log.error("Bedrock ClientError: %s", exc)
        return None
    except BotoCoreError as exc:
        log.error("Bedrock BotoCoreError: %s", exc)
        return None

    response_body = json.loads(response["body"].read())
    log.debug("Bedrock response: %s", response_body)

    # Extract text content from the response
    content_blocks = response_body.get("content", [])
    raw_text = ""
    for block in content_blocks:
        if block.get("type") == "text":
            raw_text += block.get("text", "")

    if not raw_text:
        log.error("Bedrock returned empty content.")
        return None

    # The prompt asks for JSON only; parse it
    try:
        # Strip markdown code fences if present
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        return json.loads(text)
    except json.JSONDecodeError as exc:
        log.error("Failed to parse Bedrock response as JSON: %s\nRaw: %s", exc, raw_text[:500])
        return None


# ---------------------------------------------------------------------------
# Report assembly and output
# ---------------------------------------------------------------------------

def build_report(
    project_name: str,
    scan_data: dict,
    llm_data: dict | None,
) -> dict:
    languages = compute_language_breakdown(
        scan_data["language_line_counts"],
        scan_data["total_lines_of_code"],
    )

    report = {
        "project_name": project_name,
        "languages": languages,
        "file_count": scan_data["file_count"],
        "total_lines_of_code": scan_data["total_lines_of_code"],
        "inputs": llm_data.get("inputs", []) if llm_data else [],
        "outputs": llm_data.get("outputs", []) if llm_data else [],
        "dependencies": scan_data.get("dependencies", []),
        "llm_recommendations": llm_data.get("llm_recommendations", []) if llm_data else [],
    }
    return report


def write_report(report: dict, output_dir: Path, log: logging.Logger) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{report['project_name']}_report.json"
    out_path = output_dir / filename
    log.info("Writing report: %s", out_path)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Main analysis workflow per repository
# ---------------------------------------------------------------------------

def analyze_repo(
    repo_path: Path,
    bedrock_client,
    model_id: str,
    output_dir: Path,
    log: logging.Logger,
) -> str:
    """
    Analyze a single repository. Returns 'success' or 'failed'.
    """
    project_name = repo_path.name
    log.info("=== Analyzing: %s ===", project_name)

    # Step 1: Scan
    scan_data = scan_repository(repo_path, log)
    scan_data["dependencies"] = detect_dependencies(repo_path, log)

    # Step 2: Invoke LLM
    llm_data = None
    if bedrock_client is not None:
        prompt = build_analysis_prompt(project_name, scan_data["file_listing"], scan_data["code_sample"])
        llm_data = invoke_bedrock(bedrock_client, model_id, prompt, log)
        if llm_data is None:
            log.warning(
                "Bedrock analysis failed for %s. Producing partial report without LLM insights.",
                project_name,
            )
    else:
        log.warning("No Bedrock client available. Producing partial report without LLM insights.")

    # Step 3: Build and write report
    report = build_report(project_name, scan_data, llm_data)

    # Validate round-trip serialization
    json_str = json.dumps(report, indent=2)
    try:
        reconstructed = json.loads(json_str)
        assert reconstructed == report
    except (json.JSONDecodeError, AssertionError) as exc:
        log.error("Report round-trip validation failed for %s: %s", project_name, exc)
        return "failed"

    write_report(report, output_dir, log)
    log.info("Report complete for: %s", project_name)
    return "success"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    log = setup_logging(args.verbose)

    repo_dir = Path(args.repo_dir).expanduser().resolve()
    if not repo_dir.exists():
        log.error("Repo directory does not exist: %s", repo_dir)
        sys.exit(1)

    output_dir = Path(args.output_dir).expanduser().resolve()

    # Discover repositories
    repos = discover_repos(repo_dir, log)
    if not repos:
        log.warning("No git repositories found in %s.", repo_dir)
        sys.exit(0)

    log.info("Found %d repository/repositories to analyze.", len(repos))

    # Set up Bedrock client
    bedrock_client = None
    try:
        bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=args.aws_region,
        )
        log.info("Bedrock client initialized (region: %s, model: %s)", args.aws_region, args.model_id)
    except Exception as exc:
        log.error("Failed to initialize Bedrock client: %s. Reports will lack LLM insights.", exc)

    # Analyze each repository
    results: dict[str, str] = {}
    for repo in repos:
        status = analyze_repo(repo, bedrock_client, args.model_id, output_dir, log)
        results[repo.name] = status

    # Summary
    log.info("=" * 60)
    log.info("ANALYSIS SUMMARY")
    log.info("=" * 60)
    for repo_name, status in results.items():
        log.info("  %-40s %s", repo_name, status.upper())
    log.info("=" * 60)


if __name__ == "__main__":
    main()
