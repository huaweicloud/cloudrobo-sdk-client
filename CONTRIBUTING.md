# Contributing to CloudRobo Client

Thank you for your interest in contributing to CloudRobo Client!

## Code of Conduct

This project adheres to a [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Development Setup

### Prerequisites

- Python >= 3.8
- pip

### Clone and Install

```bash
git clone https://github.com/HuaweiCloud/CloudRobo-Client.git
cd CloudRobo-Client

# Install all packages in editable mode
pip install -r requirements-dev-editable.txt
```

### Running Tests

```bash
# Run all tests
python -m pytest tests/ packages/ -v

# Run tests for a single package
python -m pytest packages/cloudrobo-asset/tests/ -v
```

## Code Style

We use the following tools for code quality:

- **Ruff** — Linting and formatting
- **Black** — Code formatting
- **isort** — Import sorting

Configuration is defined in the root `pyproject.toml`. Before submitting a PR, run:

```bash
ruff check .
ruff format .
```

## Git Workflow

### Branch Naming

Use descriptive branch names following these patterns:

- Feature branches: `feature_{单号}` (e.g., `feature_US2026052500143`)
- Bug fix branches: `bugfix_{单号}` (e.g., `bugfix_BUG2026082702191`)
- Documentation: `docs_{单号}` (e.g., `docs_US2026052500143`)
- Refactoring: `refactor_{单号}` (e.g., `refactor_US2026052500143`)

### Commit Messages

Follow the team's commit message format:

```
[单号] 描述
```

Examples:

```
[US2026052500143] 资产管理功能增强
[BUG2026082702191] 修复文档同步问题
[FE2026052300027] 新增MCP服务支持
```

Scopes (optional, for clarity):

- Package names: `asset`, `dataset`, `train`, `eval`, `infer`, `robot`, `dispatch`, `workspace`, `resource`, `r2c`, `core`
- Cross-package: `cli`, `sdk`, `docs`, `ci`

### Branching and Merging

- Always create a feature branch from `master`
- Rebase onto the latest `master` before opening a PR:

```bash
git fetch origin master
git rebase origin/master
```

- Squash commits before merging if the PR contains multiple WIP commits

## Pull Request Process

1. Ensure your code passes all tests: `python -m pytest tests/ packages/ -v`
2. Update documentation if you've changed functionality
3. Add or update tests for new features
4. Ensure your code follows the style guidelines
5. Submit a pull request with a clear description of the changes
6. At least one maintainer review is required before merging

## Testing Guidelines

- Every new feature should include corresponding unit tests
- Test files should be placed in the `tests/` directory of the corresponding package
- Test file naming convention: `test_{module_name}.py`
- Aim for meaningful test coverage; tests should verify behavior, not implementation details

## Reporting Issues

- Use the [GitHub Issues](https://github.com/HuaweiCloud/CloudRobo-Client/issues) page to report bugs or request features
- Include steps to reproduce the issue
- Include your Python version and OS information

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
