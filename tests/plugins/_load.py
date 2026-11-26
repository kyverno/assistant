"""Load a profile plugin as a package, the way Hermes does, without Hermes installed; and the engine package."""
import importlib
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
sys.dont_write_bytecode = True


def plugin(name: str, directory: pathlib.Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, directory / "__init__.py", submodule_search_locations=[str(directory)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def engine():
    """The engine package (engine/repo_engine), imported the way engine/serve.py imports it."""
    path = str(ROOT / "engine")
    if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module("repo_engine")


def contributor():
    return plugin("contributor", ROOT / "profiles/contributor/plugins/contributor")
