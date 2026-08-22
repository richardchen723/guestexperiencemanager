# Guest-experience result replication

The Codex scheduled task analyzes private stay and review inputs on the local
Mac. After each local import, it streams a result-only JSON payload over SSH to
the application server and imports it into the server's local PostgreSQL
`brain` schema. PostgreSQL port 5432 remains private.

Deploy the application code before enabling the production step so the remote
server has the `sync-import` command and guest-experience tables:

```bash
./deployment/update-ec2.sh
```

The local production sync command is:

```bash
.venv/bin/python -m brain.guest_experience_codex sync-production \
  --run-id RUN_ID \
  --ssh-target ubuntu@PRODUCTION_IP \
  --identity-file /absolute/path/to/key.pem
```

Use `--pending` instead of `--run-id` to retry all unsynced result batches
inside the current one-month analysis window. A successful production import is
recorded in the local analysis run. Failed or interrupted transfers remain
pending and are safe to retry.

The SSH target and key can alternatively be configured with:

- `GUEST_EXPERIENCE_PRODUCTION_SSH_TARGET`
- `GUEST_EXPERIENCE_PRODUCTION_SSH_KEY`
- `GUEST_EXPERIENCE_PRODUCTION_SSH_PORT`
- `GUEST_EXPERIENCE_PRODUCTION_APP_DIR`
- `GUEST_EXPERIENCE_PRODUCTION_PYTHON`
- `GUEST_EXPERIENCE_PRODUCTION_ENV_FILE`
- `GUEST_EXPERIENCE_PRODUCTION_TIMEOUT_SECONDS`

Only stored analysis rows and evidence identifiers are replicated. Raw guest
messages, stay notes, and raw public/private review text are not part of the
replication payload. Existing production issue resolutions, operator comments,
and ticket links are preserved on retries.
