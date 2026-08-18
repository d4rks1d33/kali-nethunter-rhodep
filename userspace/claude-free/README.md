# Claude Code on opencode Zen's free models

	sudo ./install.sh
	claude-free                        # Claude Code, on a free model
	claude-free --model                # list the free models, marking the current
	claude-free --model kimi-k2.5-free # use that one from now on
	claude-free --small-model <name>   # the one used for background work
	claude-free --status               # is the proxy up
	claude-free --stop

	  *   1000000   128000  yes    nemotron-3-ultra-free
          262144   262144  yes    kimi-k2.5-free
	  s    262144    32768  yes    ling-3.0-tiny-free

`*` is in use, `s` is background work. A name that Zen does not offer is refused
rather than written, since a typo would otherwise surface as a 404 in the middle
of a session. The choice is saved in `~/.config/rhodep/claude-free.conf`, and the
proxy is restarted so it picks the new names up.

## What survives an update

Worth knowing before relying on this, since Claude Code updates itself.

**opencode updating changes nothing.** Nothing here runs opencode. The proxy
talks to `https://opencode.ai/zen/v1` over HTTP, which is a server, not the
binary. The only thing read from opencode is `~/.cache/opencode/models.json`, for
the model list and the context window — if its shape ever changes, `--model`
lists nothing and the context hint is skipped, and sessions still work.

**Claude Code updating is low risk but not zero.** The four variables it is
driven with — `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`,
`ANTHROPIC_SMALL_FAST_MODEL` — are documented and used by every proxy setup, so
they are unlikely to move. What could: a new version calling an endpoint the proxy
does not implement, which comes back as an Anthropic-shaped error with the route
named in the log rather than as a hang; or the `~/.claude.json` format changing,
which makes `--trust` a no-op instead of corrupting the file. Note that `claude`
lives in `~/.local/bin` and is not an apt package, so the port's holds do not
cover it.

**The likely breakage is neither of those.** The free models are promotional and
get retired without notice, and a retired model means every request fails
upstream. So the model is checked at launch, and if it is gone the largest
free model that still supports tool calling is used instead:

	claude-free: nemotron-3-ultra-free is gone from Zen; using mimo-v2-pro-free instead
	claude-free: make it permanent with: claude-free --model mimo-v2-pro-free

`claude-free --doctor` checks the whole chain in one go — node, python3, claude,
the proxy file, the model cache, whether both models are still offered, whether
Zen answers, whether the proxy is up, and whether the current directory is
trusted.

## The trust prompt

Claude Code asks once per directory and remembers it. That mechanism works —
measured three ways in a directory that had never been opened:

	answer it, exit with /exit      hasTrustDialogAccepted = true
	answer it, then kill the session hasTrustDialogAccepted = true
	open it again                   no question asked

So `claude-free` does **not** touch trust on launch. Two earlier theories of mine
were wrong: it is not that the answer needs a clean exit, and it is not that the
write fails. What was actually wrong was a stored `false` for `/home/kali`, which
makes the question come back every time and which answering does not appear to
clear.

For that case, and for pre-approving a directory deliberately:

	claude-free --trust      # this directory, no question next time
	claude-free --untrust    # ask again

`CLAUDE_FREE_AUTO_TRUST=1` in the config trusts every directory on launch, for
anyone who wants that. It is off by default, because trusting a directory without
being asked is exactly what the question exists to prevent.

## Where things are

	/usr/local/bin/claude-free                        the wrapper
	/usr/local/libexec/rhodep/zen-anthropic-proxy.mjs the translator
	~/.config/rhodep/claude-free.conf                 model choices
	~/.cache/claude-free/proxy.log                    the proxy's log

The proxy listens on `127.0.0.1` only, and starts on demand — the wrapper waits
for `/health` rather than racing it, since a connection refused on the first
request looks exactly like a broken proxy.
