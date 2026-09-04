# Security Policy

## Reporting a Vulnerability

We take the security of CloudRobo Client seriously. If you discover a security vulnerability, please report it to us responsibly.

### How to Report

Please send your report to **hwcloudrobo@huawei.com** with the following information:

- Description of the vulnerability
- Steps to reproduce the issue
- Potential impact assessment
- Any suggested fixes (optional)

### What to Expect

- **Acknowledgment**: We will acknowledge receipt of your report within **7 business days**.
- **Assessment**: We will evaluate the report and determine the scope and impact.
- **Resolution**: If the vulnerability is confirmed, we will work on a fix and release a patched version.
- **Disclosure**: We will coordinate with you on the public disclosure timeline.

### Scope

This security policy applies to the CloudRobo Client codebase. Vulnerabilities in the Huawei Cloud platform itself should be reported through Huawei Cloud's security advisory channels.

### Out of Scope

- Issues that have already been reported or publicly known
- Issues in third-party dependencies (please report to the respective project)
- Social engineering or physical security issues

## Security Best Practices

When using CloudRobo Client:

- Never commit credentials (AK/SK) to version control
- Use environment variables for sensitive configuration
- Keep your packages up to date
- Review the `pyproject.toml` dependencies for your use case
