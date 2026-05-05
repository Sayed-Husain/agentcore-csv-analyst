"""Streamlit UI for the CSV analyst — talks to the deployed AgentCore Runtime.

Each chat message is a single InvokeAgentRuntime HTTPS call. The agent loop and
Code Interpreter sandbox both run in AWS; Streamlit is a thin client. The runtime
returns {"answer": text, "images": [base64 PNG, ...]} — we render both.

Known limits:
- No conversation memory across turns (each invocation is independent).
- CSV is shipped on every invocation. Fine for small files; for production, push
  to S3 once and pass an S3 URI in the payload instead.
"""

import base64
import json
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3  # noqa: E402
import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN")
REGION = os.environ.get("AWS_REGION", "us-east-1")
THINKING_RE = re.compile(r"<thinking>\s*(.*?)\s*</thinking>", re.DOTALL)

st.set_page_config(page_title="CSV Analyst", page_icon=":bar_chart:", layout="wide")
st.title("CSV Analyst — hosted")

if not RUNTIME_ARN:
    st.error(
        "AGENTCORE_RUNTIME_ARN is not set. Add it to `.env`:\n\n"
        "```\nAGENTCORE_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:...\n```\n\n"
        "You can get it from `agentcore status` or the deploy output."
    )
    st.stop()


@st.cache_resource
def runtime_client():
    """One boto3 client per Streamlit process, reused across invocations."""
    return boto3.client("bedrock-agentcore", region_name=REGION)


def render_assistant(text: str, images: list[bytes] | None = None) -> None:
    """Pull <thinking> blocks into a collapsible expander, render answer text,
    then any chart images returned by the agent."""
    thoughts = THINKING_RE.findall(text or "")
    main = THINKING_RE.sub("", text or "").strip()
    if thoughts:
        with st.expander("Reasoning", expanded=False):
            for t in thoughts:
                st.markdown(t.strip())
    if main:
        st.markdown(main)
    for img in images or []:
        st.image(img)


def invoke_runtime(
    prompt: str, csv_text: str, session_id: str
) -> tuple[str, list[bytes]]:
    """Send one chat turn to the deployed runtime; return (answer, images).

    Network errors and runtime errors both surface as user-readable strings rather
    than raising — keeps the UI responsive even when the backend hiccups.
    """
    payload = json.dumps({"prompt": prompt, "csv_text": csv_text}).encode("utf-8")
    response = runtime_client().invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        runtimeSessionId=session_id,
        payload=payload,
        contentType="application/json",
    )

    body = response.get("response")
    if hasattr(body, "read"):
        body = body.read()
    if isinstance(body, bytes):
        body = body.decode("utf-8")

    try:
        data = json.loads(body)
    except Exception:
        return f"(unparseable response: {body!r})", []

    if isinstance(data, dict) and "error" in data:
        return f"Runtime error: {data['error']}", []
    if isinstance(data, dict) and "answer" in data:
        images: list[bytes] = []
        for b64 in data.get("images") or []:
            try:
                images.append(base64.b64decode(b64))
            except Exception:
                pass
        return data["answer"], images
    return f"(unexpected response shape: {data!r})", []


# --- Sidebar: upload + reset ---
with st.sidebar:
    st.markdown("### Upload a CSV")
    uploaded = st.file_uploader("CSV", type="csv", label_visibility="collapsed")

    st.markdown("---")
    if "filename" in st.session_state:
        st.success(f"Active: `{st.session_state.filename}`")
        st.caption(f"Runtime session: `{st.session_state.agent_session_id[:12]}…`")
        if st.button("Reset session"):
            for k in ("agent_session_id", "csv_text", "filename", "messages"):
                st.session_state.pop(k, None)
            st.rerun()
    else:
        st.info("No active session")

    st.markdown("---")
    st.caption(
        f"Hosted agent: `{RUNTIME_ARN.split('/')[-1]}`. Each chat message is one "
        "InvokeAgentRuntime call; the agent loop runs in AWS, not on your laptop."
    )

# --- Initialize on new upload ---
if uploaded is not None and uploaded.name != st.session_state.get("filename"):
    st.session_state.csv_text = uploaded.getvalue().decode("utf-8")
    st.session_state.filename = uploaded.name
    # Runtime session IDs must be 33+ chars; uuid4 is 36, but we pad anyway.
    st.session_state.agent_session_id = str(uuid.uuid4()) + "00"
    st.session_state.messages = []
    st.rerun()

# --- Chat ---
if "csv_text" not in st.session_state:
    st.info("Upload a CSV in the sidebar to begin.")
else:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                render_assistant(msg["content"], msg.get("images", []))
            else:
                st.markdown(msg["content"])

    if prompt := st.chat_input("Ask a question about your data..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Asking the deployed agent..."):
                try:
                    answer, images = invoke_runtime(
                        prompt,
                        st.session_state.csv_text,
                        st.session_state.agent_session_id,
                    )
                except Exception as e:
                    answer, images = f"Invocation failed: {type(e).__name__}: {e}", []
            render_assistant(answer, images)

        st.session_state.messages.append(
            {"role": "assistant", "content": answer, "images": images}
        )
