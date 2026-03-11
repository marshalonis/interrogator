# Requirements Document

## Introduction

A set of Python scripts that provide two core capabilities for legacy codebase modernization workflows: (1) migrating local git repositories to GitLab by creating new projects and pushing code, and (2) analyzing repositories using AWS Bedrock LLMs to produce modernization-ready reports covering language, inputs, outputs, and other relevant characteristics.

## Glossary

- **Migration_Script**: The Python script responsible for creating GitLab projects and uploading local repository contents to them.
- **Analysis_Script**: The Python script responsible for analyzing repository codebases and producing modernization reports using AWS Bedrock.
- **GitLab_API**: The GitLab REST API used for project creation and repository operations, authenticated via a Personal Access Token.
- **PAT**: A GitLab Personal Access Token used for authenticating API requests.
- **Bedrock_Client**: The AWS Bedrock runtime client used to invoke foundation models for code analysis.
- **Modernization_Report**: The structured output produced by the Analysis_Script containing project metadata, language breakdown, inputs, outputs, and modernization insights.
- **Repo_Directory**: A local filesystem directory containing one or more git repositories to be migrated or analyzed.

## Requirements

### Requirement 1: Migrate Local Repositories to GitLab

**User Story:** As a modernization engineer, I want to migrate a batch of local git repositories to GitLab, so that legacy codebases are centralized and accessible for team collaboration.

#### Acceptance Criteria

1. WHEN the Migration_Script is invoked with a Repo_Directory path, a GitLab group/namespace URL, and a PAT, THE Migration_Script SHALL discover all git repositories within the Repo_Directory.
2. WHEN a git repository is discovered, THE Migration_Script SHALL create a new project on GitLab under the specified namespace using the GitLab_API.
3. WHEN a new GitLab project is created, THE Migration_Script SHALL push all branches and tags from the local repository to the newly created GitLab project.
4. WHEN a GitLab project with the same name already exists in the target namespace, THE Migration_Script SHALL skip that repository and log a warning message.
5. IF the PAT is invalid or lacks sufficient permissions, THEN THE Migration_Script SHALL report a clear authentication error and terminate gracefully.
6. IF a network error occurs during migration, THEN THE Migration_Script SHALL log the error for the affected repository and continue processing remaining repositories.
7. WHEN migration completes, THE Migration_Script SHALL produce a summary listing each repository and its migration status (success, skipped, or failed).

### Requirement 2: Analyze Repositories for Modernization

**User Story:** As a modernization engineer, I want to analyze one or more repositories to understand their structure, languages, inputs, and outputs, so that I can plan modernization efforts effectively.

#### Acceptance Criteria

1. WHEN the Analysis_Script is invoked with a Repo_Directory path (or a single repo path), a PAT, and AWS credentials, THE Analysis_Script SHALL scan the repository contents and collect file metadata.
2. WHEN repository contents are collected, THE Analysis_Script SHALL identify the programming languages used and calculate a percentage breakdown by file count or lines of code.
3. WHEN repository contents are collected, THE Analysis_Script SHALL send relevant code samples and file listings to the Bedrock_Client for LLM-powered analysis.
4. WHEN the Bedrock_Client returns analysis results, THE Analysis_Script SHALL extract information about project inputs (e.g., configuration files, environment variables, API endpoints consumed) and outputs (e.g., APIs exposed, files generated, data written).
5. WHEN analysis is complete for a repository, THE Analysis_Script SHALL produce a Modernization_Report containing: project name, language breakdown, identified inputs, identified outputs, dependencies, and LLM-generated modernization recommendations.
6. WHEN multiple repositories are analyzed, THE Analysis_Script SHALL produce a separate Modernization_Report for each repository.
7. IF the Bedrock_Client call fails, THEN THE Analysis_Script SHALL log the error and produce a partial report with the locally gathered metadata (languages, file counts) without LLM insights.

### Requirement 3: Modernization Report Output

**User Story:** As a modernization engineer, I want reports in a structured, readable format, so that I can share findings with stakeholders and use them as input for planning tools.

#### Acceptance Criteria

1. THE Modernization_Report SHALL be output as a JSON file per repository.
2. THE Modernization_Report SHALL contain the following fields: project_name, languages (with percentages), inputs, outputs, dependencies, file_count, total_lines_of_code, and llm_recommendations.
3. WHEN the Analysis_Script serializes a Modernization_Report to JSON, THE Analysis_Script SHALL produce valid, parseable JSON.
4. WHEN the Analysis_Script deserializes a Modernization_Report from JSON, THE Analysis_Script SHALL reconstruct an equivalent Modernization_Report object (round-trip property).
5. WHEN an output directory is specified, THE Analysis_Script SHALL write all reports to that directory using the naming convention `{project_name}_report.json`.

### Requirement 4: Command-Line Interface

**User Story:** As a modernization engineer, I want to run the scripts from the command line with clear arguments, so that I can integrate them into automation pipelines.

#### Acceptance Criteria

1. THE Migration_Script SHALL accept command-line arguments for: repo directory path, GitLab namespace URL, and PAT.
2. THE Analysis_Script SHALL accept command-line arguments for: repo directory path (single or batch), output directory, PAT, and AWS region.
3. WHEN required arguments are missing, THE Migration_Script SHALL display a usage message listing all required and optional arguments.
4. WHEN required arguments are missing, THE Analysis_Script SHALL display a usage message listing all required and optional arguments.
5. WHERE a `--dry-run` flag is provided, THE Migration_Script SHALL simulate the migration process without creating GitLab projects or pushing code, and log what actions would be taken.

### Requirement 5: Logging and Observability

**User Story:** As a modernization engineer, I want clear logging throughout script execution, so that I can troubleshoot issues and audit completed operations.

#### Acceptance Criteria

1. THE Migration_Script SHALL log each significant action (repository discovery, project creation, push operation, skip, error) with timestamps.
2. THE Analysis_Script SHALL log each significant action (repository scan, LLM invocation, report generation, error) with timestamps.
3. WHERE a `--verbose` flag is provided, THE Migration_Script SHALL output detailed debug-level logging.
4. WHERE a `--verbose` flag is provided, THE Analysis_Script SHALL output detailed debug-level logging.
