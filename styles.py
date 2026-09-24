"""Presentation constants for the Gradio digital-twin application."""

EXAMPLES = [
    "Give me a recruiter-friendly summary of Hemanth's strongest skills.",
    "What Agentic AI, RAG, LangGraph, and MCP systems has Hemanth built?",
    "Describe a project where Hemanth delivered measurable business impact.",
    "How does Hemanth design scalable Java, AWS, and data-engineering systems?",
    "I'm interested in collaborating with Hemanth on an AI product.",
    "What personal interests can you share about Hemanth?",
]

CSS = r"""
:root {
  color-scheme: light;
  --twin-bg: #efeae2;
  --twin-panel: #f0f2f5;
  --twin-panel-solid: #ffffff;
  --twin-panel-soft: #ffffff;
  --twin-border: #d8dfE3;
  --twin-border-strong: #00a884;
  --twin-text: #111b21;
  --twin-muted: #54656f;
  --twin-cyan: #008069;
  --twin-blue: #00a884;
  --twin-violet: #00a884;
  --twin-green: #00a884;
  --twin-shadow: 0 8px 30px rgba(11, 20, 26, 0.12);
}

body:not(.dark), body.dark {
  --twin-bg: #efeae2;
  --twin-panel: #f0f2f5;
  --twin-panel-solid: #ffffff;
  --twin-panel-soft: #ffffff;
  --twin-border: rgba(27, 64, 91, 0.16);
  --twin-border-strong: #00a884;
  --twin-text: #111b21;
  --twin-muted: #54656f;
  --twin-shadow: 0 8px 30px rgba(11, 20, 26, 0.12);
}

html, body, gradio-app {
  min-height: 100%;
  color-scheme: light !important;
  background: var(--twin-bg) !important;
}

body.dark,
body.dark gradio-app,
.dark .gradio-container,
.gradio-container,
.gradio-container .main,
.gradio-container .contain,
.gradio-container .wrap {
  background-color: var(--twin-bg) !important;
  color: var(--twin-text) !important;
}

footer, .built-with, .show-api, .api-docs { display: none !important; }

.gradio-container {
  width: min(100%, 1040px) !important;
  margin: 0 auto !important;
  padding: 34px 24px 50px !important;
  color: var(--twin-text) !important;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
}

.gradio-container h1 {
  margin: 0 !important;
  color: var(--twin-text) !important;
  font-size: clamp(1.7rem, 4vw, 2.55rem) !important;
  line-height: 1.08 !important;
  letter-spacing: -0.045em !important;
  font-weight: 760 !important;
  text-align: left !important;
}

.gradio-container h1::before {
  content: "AVAILABLE FOR AI COLLABORATION";
  display: block;
  width: fit-content;
  margin-bottom: 12px;
  padding: 6px 10px;
  border: 1px solid rgba(87, 227, 173, 0.38);
  border-radius: 999px;
  color: var(--twin-green);
  background: rgba(87, 227, 173, 0.08);
  font: 650 0.68rem/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  letter-spacing: 0.12em;
}

.gradio-container > .prose,
.gradio-container .prose {
  color: var(--twin-muted) !important;
}

.chatbot, .chatbot.block {
  position: relative;
  min-height: 560px !important;
  overflow: hidden;
  border: 1px solid var(--twin-border) !important;
  border-radius: 20px !important;
  background-color: #efeae2 !important;
  background-image:
    radial-gradient(circle at 20px 20px, rgba(17, 27, 33, 0.025) 1px, transparent 1px) !important;
  background-size: 22px 22px !important;
  box-shadow: var(--twin-shadow) !important;
  backdrop-filter: blur(18px);
}

.chatbot.pending,
.chatbot:focus-within,
.dark .chatbot,
.dark .chatbot.block {
  background-color: #efeae2 !important;
  color: #111b21 !important;
}

.chatbot::before {
  content: "";
  position: absolute;
  inset: 0 0 auto;
  height: 2px;
  z-index: 2;
  pointer-events: none;
  background: #00a884;
}

.chatbot > .block-label,
.chatbot > label,
.chatbot .label-wrap { display: none !important; }

.chatbot .placeholder,
.chatbot .placeholder * {
  color: var(--twin-muted) !important;
}

.message-row,
.message-row > div,
.message-row .role,
.message-wrap,
.bubble-wrap {
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
}

.message-row {
  width: 100% !important;
  flex: 0 0 100% !important;
}

.chatbot .message-wrap,
.chatbot .bubble-wrap {
  width: 100% !important;
  max-width: 100% !important;
}

.chatbot .message-row.bubble {
  width: min(78%, 760px) !important;
  max-width: min(78%, 760px) !important;
}

.message-row .message,
.message-row .message-bubble,
.message-row .bubble,
.chatbot .message {
  width: 100% !important;
  min-width: min(340px, 72vw) !important;
  max-width: 100% !important;
  padding: 12px 15px !important;
  border-radius: 16px !important;
  font-size: 14px !important;
  line-height: 1.62 !important;
  white-space: normal !important;
  word-break: normal !important;
  overflow-wrap: break-word !important;
  box-shadow: none !important;
}

.message-row.user-row .message,
.message-row.user-row .message-bubble,
.message-row.user-row .bubble,
.message-row.user-row .user,
.message-row[data-role="user"] .message,
.message-row[data-role="user"] .message-bubble {
  color: #111b21 !important;
  border: 1px solid #c8e9c3 !important;
  background: #d9fdd3 !important;
  box-shadow: 0 1px 1px rgba(11, 20, 26, 0.08) !important;
}

.message-row.bot-row .message,
.message-row.bot-row .message-bubble,
.message-row.bot-row .bubble,
.message-row.bot-row .bot,
.message-row[data-role="assistant"] .message,
.message-row[data-role="assistant"] .message-bubble {
  color: var(--twin-text) !important;
  border: 1px solid var(--twin-border) !important;
  border-left: 3px solid #00a884 !important;
  background: #ffffff !important;
  box-shadow: 0 1px 1px rgba(11, 20, 26, 0.08) !important;
}

.message-row .message p,
.message-row .message-bubble p,
.message-row .bubble p {
  margin: 0 0 0.65em !important;
  color: inherit !important;
  font-size: inherit !important;
  line-height: inherit !important;
}

.message-row .message p:last-child,
.message-row .message-bubble p:last-child,
.message-row .bubble p:last-child { margin-bottom: 0 !important; }

.message-row a { color: var(--twin-cyan) !important; }
.message-row code {
  border: 1px solid var(--twin-border);
  border-radius: 6px;
  background: #f0f2f5 !important;
  color: inherit !important;
}

textarea, input[type="text"] {
  min-height: 54px !important;
  border: 1px solid var(--twin-border) !important;
  border-radius: 14px !important;
  background: var(--twin-panel-solid) !important;
  color: var(--twin-text) !important;
  font-size: 14px !important;
  line-height: 1.45 !important;
  padding: 14px 16px !important;
  transition: border-color 150ms ease, box-shadow 150ms ease !important;
}

.gradio-container .form,
.gradio-container .input-container,
.gradio-container .input-row,
.gradio-container form {
  background: #f0f2f5 !important;
  color: #111b21 !important;
}

textarea:disabled, input[type="text"]:disabled,
textarea[readonly], input[type="text"][readonly] {
  opacity: 1 !important;
  background: #f7f8fa !important;
  color: #111b21 !important;
  -webkit-text-fill-color: #111b21 !important;
}

textarea:focus, input[type="text"]:focus {
  border-color: var(--twin-border-strong) !important;
  outline: none !important;
  box-shadow: 0 0 0 3px rgba(0, 168, 132, 0.14) !important;
}

textarea::placeholder, input::placeholder { color: var(--twin-muted) !important; }

button.primary,
button[variant="primary"],
button.submit,
.submit-button {
  min-height: 52px !important;
  border: 0 !important;
  border-radius: 13px !important;
  background: #00a884 !important;
  color: #ffffff !important;
  font-weight: 750 !important;
  box-shadow: 0 8px 20px rgba(0, 128, 105, 0.2) !important;
  transition: transform 140ms ease, filter 140ms ease !important;
}

button.primary:hover,
button.submit:hover,
.submit-button:hover {
  filter: brightness(1.08);
  transform: translateY(-1px);
}

.examples, .examples-holder, [data-testid="examples"] {
  margin-top: 16px !important;
  padding: 0 !important;
  background: transparent !important;
}

.examples button, .example, [data-testid="examples"] button {
  min-height: 0 !important;
  padding: 9px 12px !important;
  border: 1px solid var(--twin-border) !important;
  border-radius: 999px !important;
  background: var(--twin-panel) !important;
  color: var(--twin-muted) !important;
  font: 500 12px/1.35 Inter, ui-sans-serif, system-ui, sans-serif !important;
  text-align: left !important;
  text-transform: none !important;
  letter-spacing: 0 !important;
}

.examples button:hover, .example:hover, [data-testid="examples"] button:hover {
  border-color: var(--twin-border-strong) !important;
  color: var(--twin-cyan) !important;
}

.icon-button, .chatbot .icon-button {
  min-height: 0 !important;
  padding: 5px !important;
  border: 0 !important;
  border-radius: 8px !important;
  background: transparent !important;
  color: var(--twin-muted) !important;
}

::-webkit-scrollbar { width: 9px; height: 9px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  border: 2px solid transparent;
  border-radius: 999px;
  background: rgba(130, 162, 186, 0.34);
  background-clip: padding-box;
}
::selection { background: var(--twin-cyan); color: #06111a; }

@media (max-width: 680px) {
  .gradio-container { padding: 22px 12px 34px !important; }
  .chatbot, .chatbot.block { min-height: 68vh !important; border-radius: 15px !important; }
  .message-row .message,
  .message-row .message-bubble,
  .message-row .bubble,
  .chatbot .message {
    width: 92% !important;
    min-width: 0 !important;
    max-width: 92% !important;
  }
  .message-row .message-wrap,
  .message-row .bubble-wrap,
  .chatbot .message-wrap,
  .chatbot .bubble-wrap {
    width: 100% !important;
    max-width: 100% !important;
  }
  .chatbot .message-row.bubble {
    width: 92% !important;
    max-width: 92% !important;
  }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
}
"""

JS = r"""
() => {
  document.title = "Hemanth · AI Digital Twin";

  const focusLatestInput = () => {
    const inputs = [...document.querySelectorAll("textarea")];
    const input = inputs.at(-1);
    if (input && !input.disabled && !input.readOnly) input.focus({ preventScroll: true });
  };

  const watchInput = (input) => {
    if (input.dataset.twinWatched) return;
    input.dataset.twinWatched = "true";
    let unavailable = input.disabled || input.readOnly;
    new MutationObserver(() => {
      const nextUnavailable = input.disabled || input.readOnly;
      if (unavailable && !nextUnavailable) input.focus({ preventScroll: true });
      unavailable = nextUnavailable;
    }).observe(input, { attributes: true, attributeFilter: ["disabled", "readonly"] });
  };

  const scan = () => document.querySelectorAll("textarea").forEach(watchInput);
  setTimeout(() => { scan(); focusLatestInput(); }, 350);
  new MutationObserver(scan).observe(document.body, { childList: true, subtree: true });
};
"""
