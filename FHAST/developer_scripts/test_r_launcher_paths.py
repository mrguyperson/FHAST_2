"""Structural/path-equivalence checks; these tests do not execute R.

Run: python3 FHAST/developer_scripts/test_r_launcher_paths.py
The helper deliberately contains only base-R file.path assignments. Check that
small structure and model its Windows paths with ntpath, without parsing general R.
"""

import ast
import ntpath
from pathlib import Path
import re
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'FHAST/launcher_paths.R'
WRAPPERS = {
    'run_fhast.R': ('main', 'run_all.R'),
    'run_compare.R': ('compare_runs', 'compare_runs.R'),
    'run_ohwm.R': ('ohwm_analysis', 'ohwm_analysis.R'),
    'run_param.R': ('param_analysis', 'param_analysis.R'),
}
BOOTSTRAP = "source(file.path(getwd(), 'launcher_paths.R'), local = TRUE)"


def assignments():
    """Reject helper syntax outside the three simple bootstrap assignments."""
    result = []
    for line in HELPER.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(
            r"(\w+) = file\.path\((getwd\(\)|appwd), ('.*')\)", line)
        if not match:
            raise AssertionError('Unexpected R bootstrap expression: ' + line)
        name, base, arguments = match.groups()
        parts = ast.literal_eval('(' + arguments + ',)')
        if not all(isinstance(part, str) for part in parts):
            raise AssertionError('Bootstrap path components must be string literals')
        result.append((name, base, parts))
    return result


def windows_paths(cwd):
    values = {}
    for name, base, parts in assignments():
        parent = cwd if base == 'getwd()' else values[base]
        values[name] = ntpath.normpath(ntpath.join(parent, *parts))
    return values


class RLauncherPathsTest(unittest.TestCase):
    def test_paths_match_previous_bootstrap_for_windows_locations(self):
        for cwd in (r'C:\FHAST', r'D:\FHAST bundle with spaces\FHAST',
                    r'\\server\shared bundle\FHAST'):
            with self.subTest(cwd=cwd):
                self.assertEqual(windows_paths(cwd), {
                    'appwd': cwd + r'\FHAST_app',
                    'applibpath': cwd + r'\FHAST_app\app\library',
                    'scriptwd': cwd + r'\scripts',
                })

    def test_paths_reach_representative_bundle_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='fhast-r-paths-') as temporary:
            app_root = Path(temporary) / 'bundle with spaces' / 'FHAST'
            # Use actual distribution casing; Windows matches FHAST_app to it.
            (app_root / 'FHAST_App/app/library').mkdir(parents=True)
            (app_root / 'scripts').mkdir()
            values = windows_paths(str(app_root))
            for value in values.values():
                relative = ntpath.relpath(value, str(app_root))
                target = app_root.joinpath(*relative.replace('FHAST_app', 'FHAST_App').split('\\'))
                self.assertTrue(target.is_dir(), value)

    def test_wrappers_delegate_before_package_loading_and_keep_entry_points(self):
        for name, entry in WRAPPERS.items():
            with self.subTest(wrapper=name):
                text = (ROOT / 'FHAST/FHAST_App/dist/script/R' / name).read_text(encoding='utf-8')
                code = '\n'.join(line for line in text.splitlines()
                                 if not line.lstrip().startswith('#'))
                self.assertEqual(code.count(BOOTSTRAP), 1)
                bootstrap = code.index(BOOTSTRAP)
                self.assertLess(code.index('commandArgs(trailingOnly = TRUE)'), bootstrap)
                self.assertLess(bootstrap, code.index('if (!dir.exists(applibpath))'))
                self.assertLess(bootstrap, code.index('.libPaths(c(applibpath, .Library))'))
                self.assertLess(bootstrap, code.index("ensure('jsonlite', load = TRUE)"))
                self.assertIn("config = fromJSON(file.path(appwd, 'app', 'config.cfg'))", code)
                self.assertIn("source(file.path(appwd, 'app', 'app.R'))", code)
                self.assertIn("source(file.path(scriptwd, '%s', '%s'))" % entry, code)
                self.assertNotRegex(code, r'(?m)^\s*(appwd|applibpath|scriptwd)\s*(=|<-)')
                self.assertNotRegex(code, r'\bsetwd\s*\(')


if __name__ == '__main__':
    unittest.main(verbosity=2)
