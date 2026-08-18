# Claude Code on opencode Zen's free models

	sudo ./install.sh
	claude-free                 # Claude Code, on a free model
	claude-free --models        # what is available
	claude-free --status        # is the proxy up
	claude-free --stop

Models live in `~/.config/rhodep/claude-free.conf`.

## Why this works at all

Three facts, each checked on the device before any code was written:

	claude binary          56 references to ANTHROPIC_BASE_URL
	Zen provider           api: https://opencode.ai/zen/v1
	                       npm: @ai-sdk/openai-compatible
	free model, no key     POST /chat/completions -> HTTP 200

Claude Code talks to exactly one API shape and lets you move it with
`ANTHROPIC_BASE_URL`. Zen speaks the OpenAI shape, and its `-free` models answer
**without an API key at all**. So the only missing piece is a translator.

All 25 free models report `tool_call: true`, which is the part that matters:
Claude Code is a tool-calling loop, and a model that cannot call tools is
useless to it no matter how well it writes.

## The proxy

`zen-anthropic-proxy.mjs`, Node standard library only — adding a dependency tree
to a phone to translate two JSON shapes would be a poor trade.

The request direction is mostly renaming. The response direction is not: Claude
Code expects Anthropic's event sequence

	message_start
	content_block_start / content_block_delta / content_block_stop
	message_delta (stop_reason, usage)
	message_stop

with tool calls arriving as `input_json_delta` fragments, while OpenAI streams
the same information in a completely different arrangement. That is a small state
machine, not a field mapping: content blocks have to be numbered, opened once and
closed once, and a tool call has to close any open text block first.

Model names are mapped rather than passed through — Claude Code asks for
`haiku` for cheap background work and appends things like `[1m]` to request a
larger window, and neither is a valid upstream id.

## Verified end to end

Not "the proxy returns 200", but Claude Code actually doing a job:

	claude-free -p "Lee dato.txt y deci solo la clave" --allowedTools=Read
	rhodep-4242

That answer required the full loop: tools sent upstream, a `tool_use` block
streamed back, Claude Code running Read, the `tool_result` translated into an
OpenAI `tool` message, and a second round trip. Text-only replies and
non-streaming requests were checked separately.

## What it does not do

- **Images are not forwarded.** They become a placeholder line, so the model can
  say it cannot see them instead of the attachment vanishing silently.
- **Prompt caching does nothing.** `cache_control` is dropped; Zen has no
  equivalent, so long sessions cost full input tokens every turn.
- **Anthropic `thinking` blocks are dropped.** Reasoning models have their own
  channel and do not want ours; their `reasoning` field is ignored on the way
  back rather than shown as text.
- **One diagnostic line per session stays.** Claude Code prints
  `[claude-code:unrecognized_model]` because these models are not in its table.
  `CLAUDE_CODE_MAX_CONTEXT_TOKENS`, set from opencode's model cache, removes the
  paragraph about auto-compacting, but the one-liner needs a `modelOverrides`
  entry whose format is only visible inside a minified binary. Filtering stderr
  would hide real errors too, so it stays.
- **These are not Claude.** They call tools and follow instructions less
  reliably, which shows up as loops and skipped steps in long agentic runs.

For Zen's paid models, set `OPENCODE_API_KEY` and the proxy will send it.

## Where things are

	/usr/local/bin/claude-free                        the wrapper
	/usr/local/libexec/rhodep/zen-anthropic-proxy.mjs the translator
	~/.config/rhodep/claude-free.conf                 model choices
	~/.cache/claude-free/proxy.log                    the proxy's log

The proxy listens on `127.0.0.1` only, and starts on demand — the wrapper waits
for `/health` rather than racing it, since a connection refused on the first
request looks exactly like a broken proxy.
