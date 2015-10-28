chimera-lna plugin
==================

A chimera_ plugin for LNA_ domes. 

Usage
-----

Install chimera_ on your computer, and then, this package. Edit the configuration file adding one of the
supported LNA domes as on the example below.

Installation
------------

Besides chimera_, ``chimera-lna`` depends only of pyserial_.

::

    pip install -U git+https://github.com/astroufsc/chimera-lna.git


Configuration Examples
----------------------

Here goes examples of the configuration to be added on ``chimera.config`` file.

* LNA_ 40cm dome

::

    dome:
     type: DomeLNA40cm
     name: dome
     device: COM7
     telescope: 200.131.64.200:7666/TheSkyTelescope/paramount
     telescope: /FakeTelescope/fake
     model: COTE/LNA

Dome Offset
-----------

From the Paramount ME mount specification page:

::

    Use the following equation to compute the distance from the center point of the mount to the intersection of the right ascension and declination axis.

    Distance (cm) = 33 * cos(latitude + 29) - 10

Details on the calculations of the mount-dome offset can be found here_ and mirrored `alternatively here`_.

Tested Hardware
---------------

This plugin was tested on these hardware:

* LNA_ 40cm dome. This dome is a custom build.


Contact
-------

For more information, contact us on chimera's discussion list:
https://groups.google.com/forum/#!forum/chimera-discuss

Bug reports and patches are welcome and can be sent over our GitHub page:
https://github.com/astroufsc/chimera-lna/

.. _chimera: https://www.github.com/astroufsc/chimera/
.. _pyserial: http://pyserial.sourceforge.net/
.. _JMI Smart 232: http://www.jimsmobile.com/
.. _LNA: http://www.lna.br/
.. _MEADE LX200: http://www.meade.com/products/telescopes/lx200.html
.. _Optec TCF-S: http://www.optecinc.com/astronomy/catalog/tcf/tcf-s.htm
.. _here: http://www.dppobservatory.net/DomeAutomation/dome_synchronisation.pdf
.. _alternatively here: https://raw.githubusercontent.com/astroufsc/chimera-lna/master/docs/dome_synchronisation.pdf