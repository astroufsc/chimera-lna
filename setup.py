from distutils.core import setup

setup(
    name='chimera-lna',
    version='0.0.1',
    packages=['chimera_lna', 'chimera_lna.instruments'],
    scripts=[],
    url='http://github.com/astroufsc/chimera-lna',
    license='GPL v2',
    author='William Schoenell',
    author_email='william@iaa.es',
    install_requires=['pyserial'],
    description='Chimera plugin for LNA domes'
)
