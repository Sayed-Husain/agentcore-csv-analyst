r"""AgentCore Runtime entrypoint for the CSV analyst.

Same container code runs in two transports:

- LOCAL TEST (PowerShell):
    python -m agent.runtime
    # in another shell:
    $body = @{
      prompt   = "how many rows?"
      csv_text = [string](Get-Content data\sample_sales.csv -Raw)
    } | ConvertTo-Json
    Invoke-RestMethod -Uri http://localhost:8080/invocations `
      -Method Post -ContentType "application/json" -Body $body
  Note: the [string] cast is required — without it PowerShell sends the file's
  PSObject wrapper (with PSPath, PSDrive, etc.) instead of the raw text.

- DEPLOYED: AgentCore Runtime calls /invocations on this container directly.
  Clients hit it via the AgentCore API:
    aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn <arn> ...

Per invocation we open a fresh Code Interpreter session, upload the CSV inline
from the payload, run one turn, then close the session.
"""

import base64
import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.tools.code_interpreter_client import code_session
from dotenv import load_dotenv
from strands import Agent

from agent.tools import make_python_executor, upload_text_file

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("strands").setLevel(logging.INFO)

app = BedrockAgentCoreApp()
SANDBOX_CSV = "data.csv"


def build_agent(
    code_client,
    sandbox_csv: str = SANDBOX_CSV,
    image_sink: list | None = None,
) -> Agent:
    """Build an analyst agent bound to an open Code Interpreter session.

    Args:
        code_client: an active Code Interpreter session client.
        sandbox_csv: path inside the sandbox where the CSV has been uploaded.
        image_sink: optional list to capture PNG bytes of any matplotlib figures
            the agent produces.
    """
    model_id = os.environ.get("MODEL_ID", "us.amazon.nova-pro-v1:0")
    return Agent(
        model=model_id,
        system_prompt=(
            f"You are a data analyst. A CSV file is available at `{sandbox_csv}` in "
            "the working directory. Use the `python_executor` tool to load and "
            "analyze it with pandas. Before computing answers, briefly inspect the "
            "schema (columns, dtypes, shape) so you know what you're working with. "
            "Always print() values you want to read back — only stdout is returned. "
            "If you load the dataframe once, reuse it on later questions instead of "
            "reloading. "
            "When the user asks for a chart, plot, or visualization: build it with "
            "matplotlib, save with `plt.savefig('<name>.png', bbox_inches='tight')`, "
            "then `plt.close()`. Use a fresh filename per chart (chart1.png, "
            "chart2.png, ...). Do NOT call `plt.show()` — the sandbox is headless. "
            "The UI auto-detects and displays any new image file you save."
        ),
        tools=[make_python_executor(code_client, image_sink)],
    )


@app.entrypoint
def invoke(payload: dict) -> dict:
    """Handle one chat turn.

    Payload:
      {
        "prompt":   "the user's question",
        "csv_text": "<full CSV contents as a string>"
      }

    Returns:
      {"answer": "<text>", "images": ["<base64 PNG>", ...]}  on success
      {"error":  "<msg>"}                                    on missing input
    """
    prompt = payload.get("prompt", "")
    csv_text = payload.get("csv_text", "")

    if not isinstance(prompt, str) or not prompt.strip():
        return {"error": f"'prompt' must be a non-empty string (got {type(prompt).__name__})"}
    if not isinstance(csv_text, str) or not csv_text:
        return {"error": f"'csv_text' must be a non-empty string (got {type(csv_text).__name__})"}

    prompt = prompt.strip()
    region = os.environ.get("AWS_REGION", "us-east-1")
    image_sink: list[bytes] = []

    with code_session(region) as code_client:
        upload_text_file(code_client, SANDBOX_CSV, csv_text)
        agent = build_agent(code_client, SANDBOX_CSV, image_sink)
        response = agent(prompt)

    return {
        "answer": str(response),
        "images": [base64.b64encode(img).decode("ascii") for img in image_sink],
    }


if __name__ == "__main__":
    app.run()
