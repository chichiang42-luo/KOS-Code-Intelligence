# Security Policy

## Supported Versions

Security fixes are provided for the latest released minor version.

## Reporting

Please report vulnerabilities privately through GitHub Security Advisories for this repository. Do not include secrets, private source code, or sensitive repository contents in a public issue.

## Local Data Boundary

KOS indexes local source code into a local SQLite database. The v0.3.1 MCP server does not send repository content over the network and is restricted to the repository selected when the server starts. Supported Tree-sitter grammars are installed as package dependencies and do not download parsers at indexing time.
