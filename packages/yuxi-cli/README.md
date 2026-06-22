# yuxi-cli

Yuxi command line client.

First-stage scope:

- remote management through `~/.yuxi/config.toml`
- browser login
- API Key import through `--api-key`
- `whoami`, `status`, and `logout`
- server discovery and compatibility check for Yuxi `>=0.7.1`
- `yuxi kb upload` for knowledge base file uploads
- `yuxi agent eval` for running existing Langfuse dataset experiments with a logged-in remote
