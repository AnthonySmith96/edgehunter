# Security policy

EdgeHunter does not need wallet keys and cannot place real-money orders. Keep provider keys only in the ignored `.env` file. Never attach `.env`, `.local/`, `data/`, databases, logs, or PocketBase credentials to an issue.

Report a vulnerability through GitHub's **Security → Report a vulnerability** flow. Include a minimal reproduction and redact credentials and personal data. Please do not open a public issue for an unpatched vulnerability or an exposed secret.

Only the current default branch receives security fixes. If a secret is exposed, revoke it with the provider first; removing it from Git history does not make it safe again.
