# Operational deployment contract

1. Copy `.env.example` to the project-root `.env` and set `APP_ENV=production`, the frozen V1 identity, strict `CORS_ORIGINS`, and a high-entropy `API_KEY`. Keep the file ACL-restricted to the service account.
2. Configure a private HTTPS ingress/reverse proxy that forwards only to `http://127.0.0.1:8000`. Do not bind Uvicorn to a public interface.
3. Set Vercel server-side values `NAVORA_API_BASE_URL` and `NAVORA_API_KEY`; set `NEXT_PUBLIC_API_BASE_URL=/api/navora` only.
4. Run `ops/windows/install-navora-tasks.ps1` in an elevated PowerShell. It creates separate automatic-restart API and worker tasks; neither uses `--reload`.
5. Confirm `/health`, authenticated `/api/status`, and that worker state is `RUNNING`. A second worker must show `PAUSED` and make no predictions.

The service scripts intentionally do not create a public tunnel or alter MT5 credentials. HTTPS host/certificate provisioning is an operator-owned ingress decision.