"""Load a profile plugin as a package, the way Hermes does, without Hermes installed."""
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


def repo_intel():
    return plugin("repo_intel", ROOT / "profiles/maintainer/plugins/repo-intel")


def contributor():
    return plugin("contributor", ROOT / "profiles/contributor/plugins/contributor")
