"""Run with: python3 FHAST/developer_scripts/test_fhast_paths.py

Path and command-construction tests only; no QGIS, R, or NetLogo is launched.
"""

import ast
import importlib.util
import ntpath
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PLUGINS = ROOT / 'profile/profiles/default/python/plugins'
CALLERS = {
    'run_fhast_simulation': 'run_fhast.R',
    'compare_runs': 'run_compare.R',
    'ohwm_overlap': 'run_ohwm.R',
    'parameter_fitter': 'run_param.R',
}


def load_helper(path):
    spec = importlib.util.spec_from_file_location('fhast_paths', str(path))
    module = importlib.util.module_from_spec(spec)
    # Avoid writing bytecode into the distributed profile.
    exec(compile(path.read_text(), str(path), 'exec'), module.__dict__)
    return module


class LaunchPathsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fhast-paths-')
        self.addCleanup(self.temporary.cleanup)
        self.bundle = Path(self.temporary.name).resolve() / 'FHAST bundle with spaces'
        self.plugins = self.bundle / 'profile/profiles/default/python/plugins'
        self.plugins.mkdir(parents=True)
        helper = self.plugins / 'fhast_paths.py'
        shutil.copyfile(PLUGINS / 'fhast_paths.py', helper)
        self.helper = load_helper(helper)
        runtime = self.bundle / 'FHAST/FHAST_App/dist'
        rscript = runtime / 'R-Portable/App/R-Portable/bin/Rscript.exe'
        rscript.parent.mkdir(parents=True)
        rscript.touch()
        wrappers = runtime / 'script/R'
        wrappers.mkdir(parents=True)
        for wrapper in CALLERS.values():
            (wrappers / wrapper).touch()

    def test_all_callers_resolve_same_bundle_as_legacy_parent_walk(self):
        for caller, wrapper in CALLERS.items():
            with self.subTest(caller=caller):
                root, rscript, script = self.helper.launch_paths(wrapper)
                legacy_root = ntpath.normpath(
                    str(self.plugins / caller) + '\\..' * 6 + '\\FHAST')
                self.assertEqual(ntpath.normpath(root), legacy_root)
                self.assertEqual(Path(root), self.bundle / 'FHAST')
                self.assertEqual(rscript, r'.\FHAST_App\dist\R-Portable\App\R-Portable\bin\Rscript.exe')
                self.assertEqual(script, '.\\FHAST_app\\dist\\script\\R\\' + wrapper)
                self.assertTrue(Path(root, *rscript.split('\\')).is_file())
                # Windows treats the existing FHAST_app spelling as FHAST_App.
                self.assertTrue(Path(root, *script.replace('FHAST_app', 'FHAST_App').split('\\')).is_file())

    def test_resolution_does_not_depend_on_or_change_working_directory(self):
        before = self.helper.launch_paths('run_fhast.R')
        previous = Path.cwd()
        try:
            os.chdir(self.temporary.name)
            working_directory = Path.cwd()
            self.assertEqual(self.helper.launch_paths('run_fhast.R'), before)
            self.assertEqual(Path.cwd(), working_directory)
        finally:
            os.chdir(previous)

    def test_current_bundle_targets_exist(self):
        helper = load_helper(PLUGINS / 'fhast_paths.py')
        for wrapper in CALLERS.values():
            with self.subTest(wrapper=wrapper):
                root, rscript, script = helper.launch_paths(wrapper)
                self.assertEqual(Path(root), ROOT / 'FHAST')
                self.assertTrue(Path(root, *rscript.split('\\')).is_file())
                self.assertTrue(Path(root, *script.replace('FHAST_app', 'FHAST_App').split('\\')).is_file())

    def test_import_from_plugin_search_path_without_qgis_or_plugin_activation(self):
        environment = os.environ.copy()
        environment['PYTHONPATH'] = str(self.plugins)
        result = subprocess.run(
            [sys.executable, '-B', '-c',
             'from fhast_paths import launch_paths; print(launch_paths("run_fhast.R")[0])'],
            cwd=self.temporary.name, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.bundle / 'FHAST'))

    def test_existing_shell_command_and_arguments_are_preserved(self):
        # Evaluate only the actual command expressions, never a plugin's UI/run().
        tails = {
            'run_fhast_simulation': '"output with spaces/config.txt" 1',
            'compare_runs': '"output with spaces" first/input second/input',
            'ohwm_overlap': '"output with spaces" ohwm/input footprint/input',
            'parameter_fitter': '"output with spaces" model_type output/folder',
        }
        for caller, wrapper in CALLERS.items():
            with self.subTest(caller=caller):
                path = PLUGINS / caller / (caller + '.py')
                tree = ast.parse(path.read_text())
                assignments = {
                    node.targets[0].id: node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                }
                root, rscript, script = self.helper.launch_paths(wrapper)
                resolvers = [node for node in ast.walk(tree)
                             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                             and node.func.id == 'launch_paths']
                self.assertEqual(len(resolvers), 1)
                self.assertEqual(
                    eval(compile(ast.Expression(resolvers[0]), str(path), 'eval'),
                         {'launch_paths': self.helper.launch_paths}),
                    (root, rscript, script),
                )
                values = dict(
                    fhast_root=root, rscript=rscript, r_wrapper=script,
                    new_path_f='output with spaces', new_path_formated='output with spaces',
                    preview_flag=1, folder_1_f='first/input', folder_2_f='second/input',
                    new_ohwm_f='ohwm/input', new_footprint_f='footprint/input',
                    model_type='model_type', new_folder_f='output/folder',
                )
                for name in ('start_command', 'quote_string', 'cd_command', 'fhast_run'):
                    values[name] = eval(compile(ast.Expression(assignments[name]), str(path), 'eval'), values)
                calls = [node for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                         and isinstance(node.func.value, ast.Name)
                         and node.func.value.id == 'os' and node.func.attr == 'system']
                self.assertEqual(len(calls), 1)
                command = eval(compile(ast.Expression(calls[0].args[0]), str(path), 'eval'), values)
                expected = ('start "RUNNING FHAST" cmd /K "cd ' + root + ' & '
                            + rscript + ' --vanilla "' + script + '" '
                            + tails[caller] + '"')
                self.assertEqual(command, expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
