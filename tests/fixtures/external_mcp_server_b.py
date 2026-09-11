from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP("PLA v0.14 provider B fixture")


@mcp.tool(
    tags={"artifact", "file", "provider-b"},
    meta={
        "pla_artifacts": {
            "inputs": ["input_artifact"],
            "outputs": ["output_path"],
        }
    },
)
def append_marker(input_artifact: str, marker: str = " :: B") -> dict:
    """Append a marker to one UTF-8 text artifact."""
    source = Path(input_artifact)
    output = source.with_name(source.name + ".provider-b.txt")
    text = source.read_text(encoding="utf-8")
    output.write_text(text.rstrip("\r\n") + marker + "\n", encoding="utf-8")
    return {
        "input_artifact": str(source.resolve()),
        "output_path": str(output.resolve()),
        "marker": marker,
    }


@mcp.tool(
    tags={"artifact", "failure-test", "provider-b"},
    meta={
        "pla_artifacts": {
            "inputs": ["input_artifact"],
            "outputs": [],
        }
    },
)
def fail_after_read(input_artifact: str) -> dict:
    """Read the staged artifact and then fail deliberately."""
    source = Path(input_artifact)
    _ = source.read_bytes()
    raise RuntimeError("provider-b deliberate failure")


if __name__ == "__main__":
    mcp.run()
