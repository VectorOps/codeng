import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from vocode.settings import ToolSpec
from vocode.tools import base as tools_base


class ReadFileReq(BaseModel):
    path: str = Field(description="Project file path to read relative to project root.")


class ReadFileResp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: int
    content_type: Optional[str] = Field(default=None, alias="content-type")
    content_encoding: Optional[str] = Field(default=None, alias="content-encoding")
    body: Optional[str] = None
    error: Optional[str] = None


@tools_base.ToolFactory.register("read_files")
class ReadFilesTool(tools_base.BaseTool):
    name = "read_files"

    def _encode_output(self, obj: ReadFileResp) -> str:
        def _reason(code: int) -> str:
            return {
                200: "OK",
                400: "Bad Request",
                403: "Forbidden",
                404: "Not Found",
                500: "Internal Server Error",
            }.get(code, "Unknown")

        status = int(obj.status)
        lines = [f"Status: {status} {_reason(status)}"]
        if obj.content_type:
            lines.append(f"Content-Type: {obj.content_type}")
        if obj.content_encoding:
            lines.append(f"Content-Encoding: {obj.content_encoding}")
        lines.append("")
        if obj.body is not None:
            lines.append(obj.body)
        elif obj.error:
            lines.append(f"Error: {obj.error}")
        return "\n".join(lines)

    def _response(
        self,
        *,
        status: int,
        content_type: Optional[str] = None,
        content_encoding: Optional[str] = None,
        body: Optional[str] = None,
        error: Optional[str] = None,
    ) -> tools_base.ToolTextResponse:
        return tools_base.ToolTextResponse(
            text=self._encode_output(
                ReadFileResp(
                    status=status,
                    content_type=content_type,
                    content_encoding=content_encoding,
                    body=body,
                    error=error,
                )
            )
        )

    def _resolve_project_path(self, raw_path: str) -> Path:
        if not raw_path:
            raise ValueError("Empty path")
        rel_path = Path(raw_path)
        if rel_path.is_absolute():
            raise ValueError("Path must be relative to project root")
        base_path = self.prj.base_path.resolve()
        full_path = (base_path / rel_path).resolve()
        try:
            _ = full_path.relative_to(base_path)
        except ValueError as exc:
            raise ValueError("Path escapes project root") from exc
        return full_path

    def _is_gitignored(self, full_path: Path) -> bool:
        gitignore_path = self.prj.base_path / ".gitignore"
        if not gitignore_path.is_file():
            return False
        try:
            import pathspec
        except ImportError:
            return False
        try:
            lines = gitignore_path.read_text(encoding="utf-8").splitlines()
            spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
            rel_path = str(full_path.relative_to(self.prj.base_path.resolve()))
            return spec.match_file(rel_path)
        except Exception:
            return False

    async def run(self, req: tools_base.ToolReq, args: Any):
        _ = req
        raw_path: Optional[str] = None
        if isinstance(args, dict):
            arg_path = args.get("path")
            if isinstance(arg_path, str):
                raw_path = arg_path

        if raw_path is None:
            return self._response(status=400, error="Missing path")

        try:
            full_path = self._resolve_project_path(raw_path)
        except ValueError as exc:
            return self._response(status=400, error=str(exc))

        if self._is_gitignored(full_path):
            return self._response(status=403, error="Path is ignored by .gitignore")
        if not full_path.exists():
            return self._response(status=404, error="Path not found")
        if not full_path.is_file():
            return self._response(status=404, error="Path is not a file")

        try:
            data = full_path.read_bytes()
        except OSError as exc:
            return self._response(status=500, error=f"Failed to read file: {exc}")

        mime, _ = mimetypes.guess_type(str(full_path))
        if not mime:
            mime = "application/octet-stream"

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return self._response(
                status=200,
                content_type=mime,
                content_encoding="base64",
                body=base64.b64encode(data).decode("ascii"),
            )

        if mime == "application/octet-stream":
            mime = "text/plain; charset=utf-8"
        elif mime.startswith("text/") and "charset=" not in mime:
            mime = f"{mime}; charset=utf-8"

        return self._response(
            status=200,
            content_type=mime,
            body=text,
        )

    async def openapi_spec(self, spec: ToolSpec) -> Dict[str, Any]:
        _ = spec
        return {
            "name": self.name,
            "description": (
                "Read and return the full contents of the specified project file "
                "from the local filesystem under the project root as an HTTP-like "
                "response. Response fields: status, content-type, content-encoding, "
                "and body (the file). Interpretation: read the header lines "
                "(Status, Content-Type, Content-Encoding) followed by a blank line "
                "and then the body. If content-encoding is 'base64', the body is "
                "base64-encoded; if omitted, the body is plain text. On errors, "
                "status is a non-200 code and an 'error' message explains the failure. "
                "If a root .gitignore exists, ignored files are not readable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path to read under the project root.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        }
