"""Build hook: compiles constants.py → .pyc and strips source."""
import os
import py_compile
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

PACKAGE = "dragon"
PROTECTED = "constants.py"


class build_py(_build_py):
    def run(self):
        super().run()
        self._compile_protected()

    def _compile_protected(self):
        """Compile protected module to .pyc and delete .py source."""
        build_lib = os.path.join(self.build_lib, PACKAGE) if self.build_lib else PACKAGE
        src = os.path.join(build_lib, PROTECTED)
        if os.path.exists(src):
            pyc = py_compile.compile(src, dfile=src, doraise=False)
            if pyc:
                shutil.copy2(pyc, src + "c")
                os.remove(src)
                print(f"[build_py] {PROTECTED} → .pyc (source removed)")


setup(
    cmdclass={"build_py": build_py},
)
