"""Paths for FHAST's bundled Windows QGIS launchers (no QGIS imports).

QGIS puts this plugins directory on its Python import path. A standalone module
is not a QGIS plugin and requires neither metadata nor an activation order.
"""

from pathlib import Path


def launch_paths(wrapper_name):
    """Return (R working directory, relative Rscript, relative R wrapper).

    This module lives at <bundle>/profile/profiles/default/python/plugins/.
    Anchor discovery here, independent of the caller and process working directory.
    Keep R paths relative to FHAST/ and preserve their existing Windows spelling:
    callers must still cd to the returned working directory before invoking R.
    This does not select system R or interpret the legacy WSH r_exec settings.
    """
    app_root = Path(__file__).resolve().parents[5] / 'FHAST'
    rscript = r'.\FHAST_App\dist\R-Portable\App\R-Portable\bin\Rscript.exe'
    wrapper = '.\\FHAST_app\\dist\\script\\R\\' + wrapper_name
    return str(app_root), rscript, wrapper
