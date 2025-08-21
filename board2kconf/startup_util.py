import warnings
### !!! IMPORTANT This module is imported before external dependencies have been verifed, but after python version
### has been. No external imports are allowed in this file (unless they are try/catch'd)
### 3.9 syntax may be used anywhere in this file

from importlib.metadata import version, metadata, PackageNotFoundError
from typing import List
from . import PROJECT_DISTRO_NAME, PROJECT_NAME
from pathlib import Path
import re

### The following is taken straight from the python packaging docs, with the later identifier changed
VERSION_PATTERN = r"""
    v?
    (?:
        (?:(?P<epoch>[0-9]+)!)?                           # epoch
        (?P<release>[0-9]+(?:\.[0-9]+)*)                  # release segment
        (?P<pre>                                          # pre-release
            [-_\.]?
            (?P<pre_l>(a|b|c|rc|alpha|beta|pre|preview))
            [-_\.]?
            (?P<pre_n>[0-9]+)?
        )?
        (?P<post>                                         # post release
            (?:-(?P<post_n1>[0-9]+))
            |
            (?:
                [-_\.]?
                (?P<post_l>post|rev|r)
                [-_\.]?
                (?P<post_n2>[0-9]+)?
            )
        )?
        (?P<dev>                                          # dev release
            [-_\.]?
            (?P<dev_l>dev)
            [-_\.]?
            (?P<dev_n>[0-9]+)?
        )?
    )
    (?:\+(?P<local>[a-z0-9]+(?:[-_\.][a-z0-9]+)*))?       # local version
"""

VERSION_RE = re.compile(
    r"^\s*" + VERSION_PATTERN + r"\s*$",
    re.VERBOSE | re.IGNORECASE,
    )

### END EXCERPT


def check_dependencies() -> bool:
    my_deps = get_my_deps()
    for dep in my_deps:
        print(dep)
        pass
    raise SystemExit(0)
    return True

def parse_specifier(spec: str):
    # Python treats v0.2.3 and 0.2.3 as equivelant
    if spec.startswith('v'):
        spec = spec[1:]


def get_my_deps() -> List[str]:
    # Load from importlib (we were pip installed)
    # This also gets egg-info if present
    try:
        my_meta = metadata(PROJECT_DISTRO_NAME)
        return my_meta.get_all('Requires-Dist')
    except PackageNotFoundError:
        pass
    # Check if there is a pyproject.toml in the dir above us
    # this would be the case if we were in a bare git checkout
    pyproject_toml = Path(__file__).parents[1] / 'pyproject.toml'
    if pyproject_toml.is_file():
        try:
            import tomllib
            with pyproject_toml.open("rb") as f:
                pyproject = tomllib.load(f)
                return pyproject['project']['dependencies']
        except ImportError:
            warnings.warn(f'{PROJECT_NAME} is unable to verify it\'s dependencies on this version of python ' +
                                 'continuing anyway, but there later errors may be a result of missing or wrong' +
                                 'dependencies')
            return []
        except KeyError:
            raise RuntimeError("Could not read dependencies from parsed pyproject.toml.")
        except Exception:
            raise RuntimeError("Unable to read the dependency list. This is a bug, and should be reported."
                               f"Please include special detail in your report as to how you installed {PROJECT_NAME}")
    warnings.warn(f'{PROJECT_NAME} could not verify dependencies, if it fails due to an ImportError later, verify installation')
    return []

