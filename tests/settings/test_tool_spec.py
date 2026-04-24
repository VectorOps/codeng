from vocode import settings as vocode_settings


def test_tool_spec_defaults_skip_listing_to_false() -> None:
    spec = vocode_settings.ToolSpec(name="echo")

    assert spec.skip_listing is False


def test_tool_spec_coerce_reads_skip_listing() -> None:
    spec = vocode_settings.ToolSpec.model_validate(
        {
            "name": "echo",
            "skip_listing": True,
        }
    )

    assert spec.skip_listing is True
