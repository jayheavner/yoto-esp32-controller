from setuptools import setup, find_packages

setup(
    name="yoto_core",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "requests>=2.31.0",
        "paho-mqtt>=1.6.1",
    ],
    python_requires=">=3.13",
)