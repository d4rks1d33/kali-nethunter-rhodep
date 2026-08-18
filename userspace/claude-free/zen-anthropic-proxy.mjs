#!/usr/bin/env node
// Anthropic Messages API in, OpenAI chat completions out.
//
// Claude Code only ever talks to one shape of API, and it lets you move it with
// ANTHROPIC_BASE_URL. opencode Zen speaks the OpenAI shape and its "-free"
// models answer without a key at all. This sits between the two.
//
// Node's standard library only, because adding a dependency tree to a phone to
// translate two JSON shapes would be a poor trade.
//
// The awkward part is not the request, it is the streaming response: Claude Code
// wants Anthropic's event sequence - message_start, content_block_start,
// content_block_delta, content_block_stop, message_delta, message_stop - with
// tool calls arriving as input_json_delta fragments. OpenAI streams the same
// information in an entirely different arrangement, so that translation is a
// small state machine rather than a field rename.

import http from "node:http"

const PORT = Number(process.env.ZEN_PROXY_PORT || 8787)
const HOST = process.env.ZEN_PROXY_HOST || "127.0.0.1"
const UPSTREAM = (process.env.ZEN_BASE_URL || "https://opencode.ai/zen/v1").replace(/\/$/, "")
const MAIN_MODEL = process.env.ZEN_MODEL || "nemotron-3-ultra-free"
const SMALL_MODEL = process.env.ZEN_SMALL_MODEL || "ling-3.0-tiny-free"
const API_KEY = process.env.OPENCODE_API_KEY || ""
const DEBUG = Boolean(process.env.ZEN_PROXY_DEBUG)

const log = (...a) => console.error(new Date().toISOString(), ...a)
const debug = (...a) => { if (DEBUG) log("debug", ...a) }

// Claude Code asks for whatever it was configured with, and for a few names of
// its own for cheap background work. Anything that is not already a Zen model
// gets mapped rather than passed through to a 404.
function pickModel(requested) {
	// Claude Code appends things like "[1m]" to ask for a larger context window,
	// and prefixes providers with a slash. Neither belongs in an upstream model id.
	const m = String(requested || "").replace(/\[[^\]]*\]$/, "").replace(/^opencode\//, "").trim()
	if (/haiku|small|fast/i.test(m)) return SMALL_MODEL
	if (m.endsWith("-free")) return m
	return MAIN_MODEL
}

function textOf(content) {
	if (typeof content === "string") return content
	if (!Array.isArray(content)) return ""
	return content
		.filter((b) => b && (b.type === "text" || typeof b.text === "string"))
		.map((b) => b.text || "")
		.join("\n")
}

function toOpenAI(body) {
	const messages = []
	const system = textOf(body.system)
	if (system) messages.push({ role: "system", content: system })

	for (const m of body.messages || []) {
		if (typeof m.content === "string") {
			messages.push({ role: m.role, content: m.content })
			continue
		}
		const texts = []
		const toolCalls = []
		const toolResults = []
		for (const b of m.content || []) {
			if (!b || !b.type) continue
			switch (b.type) {
				case "text":
					texts.push(b.text || "")
					break
				case "tool_use":
					toolCalls.push({
						id: b.id,
						type: "function",
						function: { name: b.name, arguments: JSON.stringify(b.input ?? {}) },
					})
					break
				case "tool_result":
					toolResults.push({
						role: "tool",
						tool_call_id: b.tool_use_id,
						content: textOf(b.content) || (b.is_error ? "error" : ""),
					})
					break
				case "image": {
					// Kept rather than dropped: a model that cannot see it will say so,
					// which is more useful than silently losing the attachment.
					const src = b.source || {}
					if (src.type === "base64" && src.data)
						texts.push(`[image ${src.media_type || "image/png"}, ${src.data.length} base64 chars omitted]`)
					break
				}
				// thinking and redacted_thinking are Anthropic-only bookkeeping; the
				// upstream model has its own reasoning channel and does not want ours.
				default:
					break
			}
		}
		// Tool results are their own messages upstream and must not be folded into
		// the user turn that carried them.
		messages.push(...toolResults)
		if (m.role === "assistant") {
			const out = { role: "assistant" }
			const joined = texts.join("\n").trim()
			if (joined) out.content = joined
			if (toolCalls.length) out.tool_calls = toolCalls
			if (out.content || out.tool_calls) messages.push(out)
		} else {
			const joined = texts.join("\n").trim()
			if (joined) messages.push({ role: "user", content: joined })
		}
	}

	const req = {
		model: pickModel(body.model),
		messages,
		stream: Boolean(body.stream),
	}
	if (body.max_tokens) req.max_tokens = body.max_tokens
	if (typeof body.temperature === "number") req.temperature = body.temperature
	if (typeof body.top_p === "number") req.top_p = body.top_p
	if (Array.isArray(body.stop_sequences) && body.stop_sequences.length) req.stop = body.stop_sequences
	if (req.stream) req.stream_options = { include_usage: true }

	// Only client-side tools travel; Anthropic's server-side tools have no
	// equivalent and would be rejected as malformed functions.
	const tools = (body.tools || []).filter((t) => t && t.name && t.input_schema)
	if (tools.length) {
		req.tools = tools.map((t) => ({
			type: "function",
			function: {
				name: t.name,
				description: t.description || "",
				parameters: t.input_schema || { type: "object", properties: {} },
			},
		}))
	}
	const tc = body.tool_choice
	if (tc && req.tools) {
		if (tc.type === "auto") req.tool_choice = "auto"
		else if (tc.type === "any") req.tool_choice = "required"
		else if (tc.type === "tool" && tc.name) req.tool_choice = { type: "function", function: { name: tc.name } }
		else if (tc.type === "none") req.tool_choice = "none"
	}
	return req
}

const STOP_REASONS = {
	stop: "end_turn",
	length: "max_tokens",
	tool_calls: "tool_use",
	function_call: "tool_use",
	content_filter: "end_turn",
}

function upstreamFetch(payload) {
	const headers = { "content-type": "application/json" }
	if (API_KEY) headers.authorization = `Bearer ${API_KEY}`
	return fetch(`${UPSTREAM}/chat/completions`, {
		method: "POST",
		headers,
		body: JSON.stringify(payload),
	})
}

function anthropicError(res, status, message) {
	const body = JSON.stringify({ type: "error", error: { type: "api_error", message } })
	res.writeHead(status, { "content-type": "application/json" })
	res.end(body)
}

async function handleNonStreaming(res, payload, model) {
	const upstream = await upstreamFetch(payload)
	const text = await upstream.text()
	if (!upstream.ok) return anthropicError(res, upstream.status, `upstream ${upstream.status}: ${text.slice(0, 400)}`)
	let data
	try {
		data = JSON.parse(text)
	} catch {
		return anthropicError(res, 502, `upstream sent no JSON: ${text.slice(0, 200)}`)
	}
	const choice = (data.choices || [])[0] || {}
	const msg = choice.message || {}
	const content = []
	if (msg.content) content.push({ type: "text", text: msg.content })
	for (const call of msg.tool_calls || []) {
		let input = {}
		try {
			input = JSON.parse(call.function?.arguments || "{}")
		} catch {
			input = { _raw: call.function?.arguments || "" }
		}
		content.push({ type: "tool_use", id: call.id || `call_${Math.random().toString(36).slice(2)}`, name: call.function?.name, input })
	}
	if (!content.length) content.push({ type: "text", text: "" })
	const usage = data.usage || {}
	res.writeHead(200, { "content-type": "application/json" })
	res.end(JSON.stringify({
		id: data.id || `msg_${Date.now()}`,
		type: "message",
		role: "assistant",
		model,
		content,
		stop_reason: STOP_REASONS[choice.finish_reason] || "end_turn",
		stop_sequence: null,
		usage: {
			input_tokens: usage.prompt_tokens || 0,
			output_tokens: usage.completion_tokens || 0,
		},
	}))
}

async function handleStreaming(res, payload, model) {
	const upstream = await upstreamFetch(payload)
	if (!upstream.ok || !upstream.body) {
		const text = upstream.body ? await upstream.text() : ""
		return anthropicError(res, upstream.status || 502, `upstream ${upstream.status}: ${text.slice(0, 400)}`)
	}

	res.writeHead(200, {
		"content-type": "text/event-stream",
		"cache-control": "no-cache",
		connection: "keep-alive",
	})
	const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)

	const id = `msg_${Date.now()}`
	send("message_start", {
		type: "message_start",
		message: {
			id, type: "message", role: "assistant", model, content: [],
			stop_reason: null, stop_sequence: null,
			usage: { input_tokens: 0, output_tokens: 0 },
		},
	})

	// Anthropic numbers content blocks and expects each one opened and closed
	// exactly once, so the mapping from OpenAI's flat deltas has to be tracked.
	let nextIndex = 0
	let textIndex = null
	const toolBlocks = new Map() // upstream tool_call index -> our block index
	let stopReason = "end_turn"
	let usage = { input_tokens: 0, output_tokens: 0 }

	const closeText = () => {
		if (textIndex !== null) {
			send("content_block_stop", { type: "content_block_stop", index: textIndex })
			textIndex = null
		}
	}

	let buffer = ""
	try {
		for await (const chunk of upstream.body) {
			buffer += Buffer.from(chunk).toString("utf8")
			const lines = buffer.split("\n")
			buffer = lines.pop() || ""
			for (const line of lines) {
				const trimmed = line.trim()
				if (!trimmed.startsWith("data:")) continue
				const raw = trimmed.slice(5).trim()
				if (!raw || raw === "[DONE]") continue
				let parsed
				try {
					parsed = JSON.parse(raw)
				} catch {
					debug("unparseable chunk", raw.slice(0, 120))
					continue
				}
				if (parsed.usage) {
					usage = {
						input_tokens: parsed.usage.prompt_tokens || usage.input_tokens,
						output_tokens: parsed.usage.completion_tokens || usage.output_tokens,
					}
				}
				const choice = (parsed.choices || [])[0]
				if (!choice) continue
				const delta = choice.delta || {}

				if (typeof delta.content === "string" && delta.content.length) {
					if (textIndex === null) {
						textIndex = nextIndex++
						send("content_block_start", {
							type: "content_block_start", index: textIndex,
							content_block: { type: "text", text: "" },
						})
					}
					send("content_block_delta", {
						type: "content_block_delta", index: textIndex,
						delta: { type: "text_delta", text: delta.content },
					})
				}

				for (const call of delta.tool_calls || []) {
					const key = call.index ?? 0
					if (!toolBlocks.has(key)) {
						// A tool call cannot be interleaved with text in Anthropic's
						// stream, so the text block closes first.
						closeText()
						const index = nextIndex++
						toolBlocks.set(key, index)
						send("content_block_start", {
							type: "content_block_start", index,
							content_block: {
								type: "tool_use",
								id: call.id || `call_${key}_${Date.now()}`,
								name: call.function?.name || "unknown",
								input: {},
							},
						})
					}
					const fragment = call.function?.arguments
					if (fragment) {
						send("content_block_delta", {
							type: "content_block_delta", index: toolBlocks.get(key),
							delta: { type: "input_json_delta", partial_json: fragment },
						})
					}
				}

				if (choice.finish_reason) stopReason = STOP_REASONS[choice.finish_reason] || "end_turn"
			}
		}
	} catch (err) {
		log("stream failed:", err.message)
	}

	closeText()
	for (const index of toolBlocks.values()) send("content_block_stop", { type: "content_block_stop", index })
	send("message_delta", {
		type: "message_delta",
		delta: { stop_reason: stopReason, stop_sequence: null },
		usage: { output_tokens: usage.output_tokens },
	})
	send("message_stop", { type: "message_stop" })
	res.end()
}

const server = http.createServer((req, res) => {
	if (req.method === "GET" && (req.url === "/health" || req.url === "/")) {
		res.writeHead(200, { "content-type": "application/json" })
		return res.end(JSON.stringify({ ok: true, upstream: UPSTREAM, model: MAIN_MODEL, small: SMALL_MODEL }))
	}

	let body = ""
	req.on("data", (c) => { body += c })
	req.on("end", async () => {
		let parsed = {}
		try {
			parsed = body ? JSON.parse(body) : {}
		} catch {
			return anthropicError(res, 400, "request body was not JSON")
		}

		// Claude Code asks for a token count before large requests. An estimate
		// keeps it moving; Zen exposes no counting endpoint.
		if (req.url?.includes("count_tokens")) {
			const chars = JSON.stringify(parsed.messages || []).length + textOf(parsed.system).length
			res.writeHead(200, { "content-type": "application/json" })
			return res.end(JSON.stringify({ input_tokens: Math.ceil(chars / 4) }))
		}

		if (!req.url?.includes("/messages")) return anthropicError(res, 404, `no route for ${req.url}`)

		const payload = toOpenAI(parsed)
		const model = payload.model
		debug("->", model, "stream:", payload.stream, "tools:", payload.tools?.length || 0, "messages:", payload.messages.length)
		try {
			if (payload.stream) await handleStreaming(res, payload, model)
			else await handleNonStreaming(res, payload, model)
		} catch (err) {
			log("request failed:", err.message)
			if (!res.headersSent) anthropicError(res, 502, err.message)
			else res.end()
		}
	})
})

server.listen(PORT, HOST, () => {
	log(`zen-anthropic-proxy on http://${HOST}:${PORT} -> ${UPSTREAM}`)
	log(`main model ${MAIN_MODEL}, small model ${SMALL_MODEL}${API_KEY ? ", using OPENCODE_API_KEY" : ", no key (free models only)"}`)
})
