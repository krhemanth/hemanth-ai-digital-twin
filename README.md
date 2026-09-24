---
title: hemanth-ai-digital-twin
emoji: 🤖
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: 6.14.0
python_version: 3.12
app_file: app.py
pinned: false
---

# Hemanth AI Digital Twin

The Space answers questions from Hemanth's verified resumes, LinkedIn export, summary,
and human-confirmed Gmail replies.

## Hugging Face Secrets

Configure these in **Space → Settings → Secrets**. Do not commit `.env`, OAuth client
files, OAuth tokens, or Pushover credentials.

- `OPENAI_API_KEY`
- `PUSHOVER_USER`
- `PUSHOVER_TOKEN`
- `OWNER_EMAIL`
- `GMAIL_OAUTH_TOKEN_JSON`: the complete JSON contents of the locally authorized
  `gmail_token.json` file

Optional SMTP fallback secrets:

- `CONTACT_SMTP_HOST`
- `CONTACT_SMTP_PORT`
- `CONTACT_SMTP_USERNAME`
- `CONTACT_SMTP_PASSWORD`
- `CONTACT_FROM_EMAIL`

## Persistent runtime data

Without attached storage, Hugging Face Space files are ephemeral. For durable chat
follow-ups and confirmed answers, mount persistent storage at `/data` and add the
Space variable `TWIN_DATA_DIR=/data`. Otherwise omit `TWIN_DATA_DIR`; the app will
write temporary runtime state beside `app.py`.
