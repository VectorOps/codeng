from pathlib import Path
from importlib import resources


def _copy_resources(src, dest: Path) -> None:
    # src is a Traversable from importlib.resources.files
    if src.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            _copy_resources(child, dest / child.name)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with src.open("rb") as r, open(dest, "wb") as w:
            w.write(r.read())


def write_default_config(config_path: Path) -> None:
    """
    Write a default config file from packaged template and copy a sample directory
    next to it for reference.
    """
    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    # Copy sample directory from packaged data
    root = resources.files("vocode") / "config_templates"
    sample_src = root / "sample"
    if sample_src.exists() and sample_src.is_dir():
        _copy_resources(sample_src, config_dir / "sample")

    # Write default template config
    template_file = root / "config_template.yaml"
    template_text = template_file.read_text(encoding="utf-8")
    config_path.write_text(template_text, encoding="utf-8")
