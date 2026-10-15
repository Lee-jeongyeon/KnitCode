from setuptools import setup

setup(
    name="graphproj",
    entry_points={
        "console_scripts": [
            "gp = pkg.cli:main",
            "gp-go = pkg.cli:Runner.go",
            "ghost = nope.mod:f",
        ],
    },
)
