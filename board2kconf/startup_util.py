from dataclasses import dataclass, field
import warnings
from functools import cached_property, total_ordering
### !!! IMPORTANT This module is imported before external dependencies have been verifed, but after python version
### has been. No external imports are allowed in this file (unless they are try/catch'd)
### 3.9 syntax may be used anywhere in this file

from importlib.metadata import version as importlib_ver, metadata, PackageNotFoundError
from platform import release
from typing import List, Optional, Tuple
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


@dataclass
class Distribution(object):
    distro_name: str
    version: 'DistVersion'

@dataclass
class DistVersion(object):
    epoch: int = 0
    release: Optional[Tuple] = ()
    pre_release: Optional[str] = None
    post_release: Optional[str] = None
    dev_release: Optional[str] = None

    __literal_string: Optional[str] = None

    def __str__(self):
        if self.__literal_string:
            return self.__literal_string
        return (
            f"{self.epoch}!" if self.epoch else "" +
            ".".join(release()) +
            f".{self.pre_release}" if self.pre_release else "" +
            f".{self.post_release}" if self.post_release else "" +
            f".{self.dev_release}" if self.dev_release else ""
        )

    @classmethod
    def from_str(cls, in_str):
        matches = VERSION_RE.match(in_str)
        return cls(
            matches['epoch'] or 0,
            tuple(matches['release'].split('.')),
            matches['pre'],
            matches['post'],
            matches['dev']
        )

    def to_spec(self):
        return DistRequirementVersionNumber(
            self.epoch,
            self.release,
            self.pre_release,
            self.post_release,
            self.dev_release
        )

    @classmethod
    def installed(cls, distro_name):
        return cls.from_str(importlib_ver(distro_name))


    def __eq__(self, other: 'DistVersion'):
        if self.__literal_string and other.__literal_string:
            return self.__literal_string == self.__literal_string
        return(
            self.epoch == other.epoch and
            self.release == other.release and
            self.pre_release == other.pre_release and
            self.post_release == other.post_release and
            self.dev_release == other.dev_release
        )

    def rel_compare_value(self, other_release: Tuple):
        # We truncate our ver tuple to be the same length as the rel, and then we can compare as normal
        if len(other_release) == len(self.release):
            return self.release
        elif len(other_release) > len(self.release):
            # Compared release is longer, pad our release with zeroes
            return *self.release, *((0,) * (len(other_release) - len(self.release)))
        else: # len(other_release) < len(self.release)
            # Our release is shorter, truncate our release to the same length
            return self.release[:len(other_release)]

@dataclass
class DistRequirementVersion(object):
    operator: str
    version: 'DistRequirementVersionNumber'

    def satisfied_by(self, version: DistVersion) -> bool:
        if self.operator == "===":
            # Literal string matching
            return self.version.exact(version)
        elif self.operator == "==":
            return self.version == version
        elif self.operator == "!=":
            return self.version != version
        # Below operators seem inverted, because operators in requirements are in the other order
        elif self.operator == ">=":
            return self.version <= version
        elif self.operator == '>':
            return self.version < version
        elif self.operator == '<=':
            return self.version >= version
        elif self.operator == '<':
            return self.version > version
        elif self.operator == '~=':
            return self.version.compat(version)
        else:
            raise RuntimeError("Unexpected operator for version satisfaction check")


@dataclass
class DistRequirementVersionNumber(DistVersion):

    def exact(self, other):
        return super().__eq__(other)

    def compat(self, other):
        raise NotImplementedError("The 'compatible package' operator is not supported")

    def __eq__(self, other):
        return (
                self.epoch == other.epoch and
                self.rel_compare_value(other.release) == other.release and
                self.pre_release == other.pre_release and
                self.post_release == other.post_release and
                self.dev_release == other.dev_release
        )

    def __ge__(self, other):
        return self == other or self > other

    def __le__(self, other):
        return self == other or self < other

    def __gt__(self, other):
        if self.post_release or self.pre_release or self.dev_release:
            return False

        if self.epoch > other.epoch:
            return True
        elif self.epoch < other.epoch:
            return False

        if self.rel_compare_value(other.release) > other.release:
            return True
        return False


    def __lt__(self, other):
        if self.post_release or self.pre_release or self.dev_release:
            return False

        if self.epoch < other.epoch:
            return True
        elif self.epoch > other.epoch:
            return False

        if self.rel_compare_value(other.release) < other.release:
            return True
        return False



@dataclass
class Dependency(object):
    distro_name: str
    operator: Optional[str] = None
    version_specifiers: List[DistRequirementVersion] = field(default_factory=list)
    qualifiers: List[str] = field(default_factory=list)



    @classmethod
    def from_string(cls, in_str):
        pass


    def satisfied_by(self, dist: Distribution) -> bool:
        if dist.distro_name != self.distro_name:
            return False

        for spec in self.version_specifiers:
            if not spec.satisfied_by(dist.version):
                return False
        # If there were no non-matching specifiers, we'll wind up here as if they all matched
        return True



def check_dependencies() -> bool:
    my_deps = get_my_deps()
    for dep in my_deps:
        print(f'{dep}:\n'
              f'  Installed: {DistVersion.installed(dep)!r}\n')
    warnings.warn("Dep checking not enabled")
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

