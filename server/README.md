# haushalttickets-server

FastAPI service holding tickets, attachments and history. Runs in an unprivileged
Debian LXC next to PostgreSQL.

Current state: skeleton with a health endpoint.

```bash
uv run --extra test pytest           # tests
uv run haushalttickets-server         # dev server on 127.0.0.1:8080
```

Settings are read from environment variables with the prefix `HAUSHALTTICKETS_`
(see `src/haushalttickets_server/config.py`).
