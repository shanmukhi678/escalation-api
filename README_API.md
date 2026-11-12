# EscalationGuard Tools API (pre-built; NOT an agent)

Allowed pre-work: an API endpoint. The three agents (prompts, reasoning, tool-calling, workflow) are built on hackathon day.
Contains only SAMPLE data. Never put real customer or ticket data on a publicly reachable service.

## Endpoints (interactive docs at /docs, machine-readable at /openapi.json)
| Method | Path | Used by | What it does |
|---|---|---|---|
| GET | /tickets?status=open | Agent 1 | Tickets with full message threads (Jira-shaped sample data) |
| GET | /risk?min_level=HIGH | Agent 1 | Risk score 0-100, level and contributing factors per customer |
| GET | /risk/{customer_id} | Agent 1/2 | One customer in detail |
| POST | /analyze  {"text": "..."} | Agent 1 | Sentiment, urgency, escalation phrases for one message |
| POST | /jira/issues | Agent 3 | MOCK create-ticket (returns ESC-101..., kept in memory) |
| GET | /jira/issues | demo | MOCK list of created tickets |
| POST | /notify/email | Agent 3 | MOCK e-mail (kept in an outbox) |
| GET | /notify/outbox | demo | MOCK list of sent e-mails |
| GET | /health | monitoring | no auth |

If the environment variable API_KEY is set, every call except /health needs the header `X-API-Key: <value>`.
Sample data: 2 CRITICAL (Northwind, Greenfield Clinic), 1 HIGH (Apex), 1 MEDIUM (Bluebird), 1 LOW (resolved Harbor Foods).

## Run locally (Command Prompt, from this folder)
    python -m venv .venv
    .venv\Scripts\activate.bat
    pip install -r requirements.txt
    python -m pytest -q                      # 7 passed
    set API_KEY=choose-a-secret
    python -m uvicorn api_server:app --port 8000
    # open http://localhost:8000/docs

## Making it reachable from AWS / UIA
AgentCore agents run on AWS, so "localhost" is invisible to them. The API must be on a public HTTPS URL.
Ask your mentor which is approved; options:
1. Host it in the Kiro AWS account (container from the Dockerfile) once your access arrives.
2. A public host you are allowed to use (Render, Railway, Fly.io, ...) with API_KEY set.
3. A temporary tunnel from your laptop (ngrok / cloudflared); only works while the laptop stays on, and may be blocked by company policy.

Check it: open https://<your-url>/health in a browser on mobile data (not VPN). It must return {"status":"ok"}.

## Registering in UIA
Control Tower -> API registry: add each endpoint you want agents to use (name, method, URL) and send the X-API-Key header if set.
Agents can also call it directly from their own tool code with plain HTTPS requests.
