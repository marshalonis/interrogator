# Interrogator

A pair of Python scripts for legacy codebase modernization workflows:

- **`migrate.py`** — Migrate local git repositories to GitLab
- **`analyze.py`** — Analyze repositories and produce modernization reports using AWS Bedrock

---

## Requirements

- Python 3.10+
- [requests](https://pypi.org/project/requests/) (for `migrate.py`)
- [boto3](https://pypi.org/project/boto3/) (for `analyze.py`)
- AWS credentials configured (for `analyze.py`)
- A GitLab Personal Access Token with `api` scope (for `migrate.py`)

Install dependencies:

```bash
pip install requests boto3
```

---

## migrate.py

Discovers all git repositories in a directory and migrates them to GitLab by creating new projects and pushing all branches and tags.

### Usage

```bash
python migrate.py --repo-dir <path> --namespace <gitlab-namespace-url> --pat <token> [--dry-run] [--verbose]
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `--repo-dir` | Yes | Directory containing local git repositories |
| `--namespace` | Yes | GitLab group/namespace URL (e.g. `https://gitlab.example.com/mygroup`) |
| `--pat` | Yes | GitLab Personal Access Token with `api` scope |
| `--dry-run` | No | Simulate migration without creating projects or pushing code |
| `--verbose` | No | Enable debug-level logging |

### Examples

Migrate all repos in `~/projects` to a GitLab group:

```bash
python migrate.py \
  --repo-dir ~/projects \
  --namespace https://gitlab.example.com/my-team \
  --pat glpat-xxxxxxxxxxxxxxxxxxxx
```

Preview what would happen without making any changes:

```bash
python migrate.py \
  --repo-dir ~/projects \
  --namespace https://gitlab.example.com/my-team \
  --pat glpat-xxxxxxxxxxxxxxxxxxxx \
  --dry-run
```

### Behavior

- Skips any repository whose project name already exists in the target namespace (logs a warning)
- Reports a clear error and exits if the PAT is invalid or lacks permissions
- Logs errors for individual repository failures and continues processing the rest
- Prints a summary table at the end with status (`SUCCESS`, `SKIPPED`, or `FAILED`) for each repository

---

## analyze.py

Scans one or more git repositories, detects programming languages and dependencies, sends code samples to AWS Bedrock for LLM-powered analysis, and writes a structured JSON modernization report for each repository.

### Usage

```bash
python analyze.py --repo-dir <path> --output-dir <path> --pat <token> [--aws-region <region>] [--model-id <id>] [--verbose]
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `--repo-dir` | Yes | Path to a single git repository, or a directory containing multiple repositories |
| `--output-dir` | Yes | Directory where `{project_name}_report.json` files will be written |
| `--pat` | Yes | GitLab Personal Access Token (reserved for future GitLab API integration) |
| `--aws-region` | No | AWS region for Bedrock API calls (default: `us-east-1` or `$AWS_DEFAULT_REGION`) |
| `--model-id` | No | Bedrock model ID (default: `anthropic.claude-3-5-sonnet-20241022-v2:0`) |
| `--verbose` | No | Enable debug-level logging |

### Examples

Analyze a single repository:

```bash
python analyze.py \
  --repo-dir ~/projects/my-app \
  --output-dir ~/reports \
  --pat glpat-xxxxxxxxxxxxxxxxxxxx
```

Analyze all repositories in a directory, specifying AWS region:

```bash
python analyze.py \
  --repo-dir ~/projects \
  --output-dir ~/reports \
  --pat glpat-xxxxxxxxxxxxxxxxxxxx \
  --aws-region us-west-2
```

### AWS Credentials

`analyze.py` uses `boto3` to call AWS Bedrock. Credentials are resolved in the standard AWS order:

1. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
2. AWS credentials file (`~/.aws/credentials`)
3. IAM role (if running on EC2/ECS)

The IAM principal must have the `bedrock:InvokeModel` permission for the chosen model.

### Report Format

Each repository produces a `{project_name}_report.json` file in the output directory:

```json
{
  "project_name": "my-app",
  "languages": {
    "Python": 72.4,
    "YAML": 18.1,
    "Shell": 9.5
  },
  "file_count": 42,
  "total_lines_of_code": 3821,
  "inputs": [
    "Environment variable: DATABASE_URL",
    "Config file: config/settings.yaml",
    "REST API consumed: https://api.example.com/v1"
  ],
  "outputs": [
    "REST API exposed: /api/v1/users",
    "Writes to PostgreSQL database",
    "Generates CSV reports to /tmp/output"
  ],
  "dependencies": [
    "boto3",
    "flask",
    "psycopg2",
    "requests"
  ],
  "llm_recommendations": [
    "Containerize the application using Docker for portability",
    "Replace direct SQL queries with an ORM for maintainability",
    "Externalize configuration using a secrets manager"
  ]
}
```

If the Bedrock call fails, the report is still written with all locally gathered fields (`languages`, `file_count`, `total_lines_of_code`, `dependencies`) and empty `inputs`, `outputs`, and `llm_recommendations` arrays.

### Supported Languages

The analyzer detects 40+ languages by file extension, including Python, JavaScript, TypeScript, Java, Go, Rust, C/C++, C#, Ruby, PHP, Swift, Kotlin, Scala, SQL, Shell, Terraform, and more.

### Supported Dependency Manifests

| File | Ecosystem |
|---|---|
| `requirements.txt` | Python (pip) |
| `pyproject.toml` | Python (Poetry/PEP 517) |
| `package.json` | Node.js (npm/yarn) |
| `pom.xml` | Java (Maven) |
| `build.gradle` | Java/Kotlin (Gradle) |
| `go.mod` | Go |
| `Gemfile` | Ruby (Bundler) |
| `Cargo.toml` | Rust (Cargo) |

---

## Logging

Both scripts log with timestamps in the format `YYYY-MM-DDTHH:MM:SS [LEVEL] message`.

Pass `--verbose` to either script to enable `DEBUG`-level output, which includes API request details, file-by-file scan progress, and LLM prompt/response information.
