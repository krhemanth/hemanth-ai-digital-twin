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
- `PRIVACY_CONTACT_EMAIL`: public contact address displayed on the Privacy Policy
- `WHATSAPP_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_HUMAN_RECIPIENT`
- `WHATSAPP_VERIFY_TOKEN`
- `META_APP_SECRET`
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

## Privacy Policy

The application serves a public Privacy Policy at `/privacy-policy` in both local
and hosted environments. Locally, the default URL is
`http://127.0.0.1:7860/privacy-policy`. On Render, use
`https://hemanth-ai-digital-twin.onrender.com/privacy-policy` after deployment.

## WhatsApp human follow-up

The main application serves the WhatsApp callback at `/webhook`. On Render, configure
Meta with `https://hemanth-ai-digital-twin.onrender.com/webhook` and the value of
`WHATSAPP_VERIFY_TOKEN`. Owner replies must start with the eight-character request
code included in the notification, for example `4ED37FB0 Your answer`.
