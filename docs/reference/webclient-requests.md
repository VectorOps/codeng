# Web client requests

The outbound web client supports explicit HTTP methods and optional request bodies. Use this protocol when an LLM or tool needs to make a request beyond a simple `GET`.

## Quick reference

Supported methods:

- `GET`
- `HEAD`
- `POST`
- `PUT`
- `PATCH`
- `DELETE`

Supported body kinds:

- `text`
- `binary`

Rules:

- `GET` and `HEAD` must not include a body.
- A body must include `content_type`.
- Binary payloads must be base64-encoded on the wire.
- Do not set `Content-Length` manually.
- If a `Content-Type` header is present, it must match `body.content_type`.

## Request shape

```json
{
  "url": "https://api.example.com/items/123",
  "method": "PUT",
  "headers": {
    "Authorization": "Bearer ${TOKEN}"
  },
  "timeout_s": 20,
  "body": {
    "kind": "text",
    "content_type": "application/json",
    "text": "{\"name\":\"updated\"}",
    "encoding": "utf-8"
  }
}
```

## Text body

Use `kind: "text"` for JSON, plain text, XML, YAML, and other text payloads.

```json
{
  "url": "https://api.example.com/messages",
  "method": "POST",
  "body": {
    "kind": "text",
    "content_type": "text/plain; charset=utf-8",
    "text": "hello world",
    "encoding": "utf-8"
  }
}
```

## Binary body

Use `kind: "binary"` for raw bytes.

```json
{
  "url": "https://api.example.com/upload",
  "method": "PUT",
  "body": {
    "kind": "binary",
    "content_type": "application/octet-stream",
    "data_base64": "AAEC"
  }
}
```

## LLM authoring instructions

When generating a web client request:

1. Set `method` explicitly for any request that is not `GET`.
2. Omit `body` for `GET` and `HEAD`.
3. Use `kind: "text"` for JSON and plain text payloads.
4. Use `kind: "binary"` and `data_base64` for binary payloads.
5. Set the payload media type in `body.content_type`.
6. Only add headers the remote API requires.
7. Do not set `Content-Length`.
8. If you include a `Content-Type` header, make it identical to `body.content_type`.

## Validation behavior

The web client rejects requests when:

- the method is not allowed by settings
- a body is attached to `GET` or `HEAD`
- the body exceeds `max_request_body_bytes`
- the body content type is not allowed by settings
- binary bodies are disabled by settings
- `Content-Type` conflicts with `body.content_type`