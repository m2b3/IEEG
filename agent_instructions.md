# Agent Instructions

## Scope
- Never access or operate on the parent of this directory.
- Stay strictly within this directory and its subdirectories.

## Logging
- Log every user question and every final assistant answer to `codex.md`.
- Do NOT log:
  - interim reasoning
  - progress updates
  - permission requests
  - tool outputs
  - any non-final messages

- Do NOT mention logging in normal replies.
- Perform logging silently unless explicitly asked.

- Forbidden phrases:
  - "I am appending this to codex.md"
  - "I will log this interaction"
  - Any mention of logging behavior in normal responses

## Code Execution
- Any analysis or code used to follow an instruction must be written to a file.
- The file must be runnable and inspectable by the user.

## Logging Format
Append each exchange to `codex.md` in chronological order using:

## User
<the user's question>

## Assistant
<the final answer>

## Git Safety
- Never run commands that modify git history without explicit confirmation
- Never delete branches
- Never push to remote unless explicitly instructed
- Always ask before destructive actions

Ignore environment, cache, and generated folders such as `.venv`, `__pycache__`, and similar non-source directories unless explicitly asked to inspect them.