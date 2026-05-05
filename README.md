# CSV Analyst

Natural-language data analysis agent on **AWS Bedrock AgentCore**. Upload a CSV,
ask questions in English, get text answers and charts back. The agent runs in
AgentCore Runtime; the local Streamlit is a thin client over HTTPS.

![CSV Analyst — bar chart of revenue by region](docs/ui-chart.png)

## How it works

The user's question goes to the deployed agent, which writes Python on the fly,
runs it in a sandboxed Code Interpreter (pandas / numpy / matplotlib
pre-installed), iterates if needed, and returns text plus any matplotlib figures
the model generated.

![Architecture](docs/architecture.svg)

The container is just an orchestration shell — Bedrock runs the model, Code
Interpreter runs the Python. Streamlit could be swapped for curl or any HTTP
client and the contract is unchanged.

## Stack

- AgentCore Runtime + Code Interpreter
- Strands (agent framework)
- Bedrock — Amazon Nova Pro
- CodeBuild → ECR (toolkit handles the ARM64 cross-compile)
- Streamlit, boto3

## Run it

```powershell
pip install -r requirements.txt
pip install bedrock-agentcore-starter-toolkit

agentcore configure --entrypoint agent/runtime.py
agentcore deploy

# put the runtime ARN from deploy output in .env, then:
streamlit run ui/app.py
```

For local iteration without redeploy: `python -m agent.runtime` serves the same
container code on `localhost:8080`.

To clean up: `agentcore destroy` removes the runtime, ECR, and the toolkit's
CodeBuild project. The runtime IAM role, source S3 bucket, and CloudWatch log
groups need explicit `aws ... delete-...` calls.

## Notes

- Charts come back as base64 PNGs in the JSON response (sandbox saves the file,
  host detects it via a glob, response payload carries the bytes). The model
  doesn't return binary through its conversation.
- Conversation memory across turns is intentionally not implemented — each
  invocation is independent. Add it by either passing chat history in the
  payload or wiring AgentCore Memory.

## License

MIT — see [LICENSE](LICENSE).
