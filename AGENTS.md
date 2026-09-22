# Working on FHAST

FHAST (Fish Habitat Assessment and Simulation Tool) combines a QGIS Python
plugin interface, R preprocessing and reporting, and a NetLogo simulation driven
from R through `nlrx`. This repository contains a bundled Windows distribution,
not just application source. Preserve that distribution during modernization.

## Verified repository map

All paths below are relative to the repository root.

| Path | Current role |
| --- | --- |
| `FHAST/scripts/` | R model pipeline, spatial processing, analysis, and R Markdown reports. |
| `FHAST/scripts/main/` | Initialization and setup/model/postprocessing orchestration; [run_all.R](FHAST/scripts/main/run_all.R) is the simulation entry point. |
| `FHAST/scripts/NetLogo/` | [FHAST.nlogo](FHAST/scripts/NetLogo/FHAST.nlogo), included `.nls` model procedures, and [NetLogo_Controller.R](FHAST/scripts/NetLogo/NetLogo_Controller.R). |
| `FHAST/default_input/` | Bundled input configuration ([input_file.txt](FHAST/default_input/input_file.txt)). |
| `FHAST/developer_scripts/` | Developer batch-run and analysis scripts; inspect local assumptions before use. |
| `FHAST/FHAST.Rproj` | R project within the FHAST application directory. |
| `FHAST/launcher_paths.R` | Shared base-R bootstrap paths used by the four R launch wrappers, relative to their FHAST working directory. |
| `build/windows-runtime-manifest.json` | Versioned inventory of the current Windows runtime locations, versions, and evidence sources; not a launcher configuration. |
| `build/windows-runtime-sources.json` | Evidence-backed acquisition metadata and explicit historical gaps for those Windows runtimes; preparatory metadata, not a build script. |
| `build/osgeo4w-v1-package-lock.json` | Exact recorded OSGeo4W v1 x86_64 acquisition set, in `installed.db` order, with authoritative artifact paths, sizes and MD5 identities. |
| `FHAST/FHAST_App/dist/script/` | R launch wrappers ([run_fhast.R](FHAST/FHAST_App/dist/script/R/run_fhast.R), [run_compare.R](FHAST/FHAST_App/dist/script/R/run_compare.R), [run_ohwm.R](FHAST/FHAST_App/dist/script/R/run_ohwm.R), [run_param.R](FHAST/FHAST_App/dist/script/R/run_param.R)) and Windows Script Host deployment code. |
| `FHAST/FHAST_App/app/` | Startup helper [app.R](FHAST/FHAST_App/app/app.R), deployment settings [config.cfg](FHAST/FHAST_App/app/config.cfg), dependency list [packages.txt](FHAST/FHAST_App/app/packages.txt), and bundled R packages in `library/` (including `nlrx`). |
| `FHAST/FHAST_App/dist/R-Portable/App/R-Portable/` | Bundled R runtime and its standard library. |
| `FHAST/FHAST_App/dist/NetLogo 6.2.2/` | Bundled NetLogo application and runtime; the number is part of the existing directory name. |
| `FHAST/jdk-11/`, `FHAST/FHAST_App/dist/Pandoc/` | Bundled Java and report-rendering dependencies. |
| `apps/`, `bin/`, `etc/`, `include/`, `lib/`, `share/` | OSGeo4W distribution; QGIS is under `apps/qgis-ltr/`, alongside Python, Qt, GRASS, and SAGA under `apps/`. |
| `profile/profiles/default/` | Distributed QGIS profile, including settings in [QGIS3.ini](profile/profiles/default/QGIS/QGIS3.ini). |
| `profile/profiles/default/python/plugins/` | FHAST GUI source: simulation, file/template tools, comparison, OHWM, parameter fitting, and other plugins; also contains third-party/developer plugins. |
| `profile/profiles/default/python/plugins/fhast_paths.py` | Shared FHAST-root and relative R-launch paths for the four Python launcher plugins. |
| `OSGeo4W.bat`, `bin/o4w_env.bat`, `bin/qgis-ltr.bat`, `command.txt`, `customize.ini` | OSGeo4W/QGIS launch environment, command arguments, and UI customization. |
| `FHAST/fhast.bat`, `FHAST/run_command.txt`, `FHAST/NetLogoConfig.txt` | Additional launch commands and NetLogo path/version configuration. |
| `README.md`, `README.R`, `FHAST/README.md`, `FHAST/FHAST_App/README.md`, `FHAST Run Instructions 2.0.pdf` | Repository overview and roadmap, release notes, directory overview, deployment-framework notes, and user instructions. Plugin directories also contain README/help material. |

## Current execution architecture

`OSGeo4W.bat` initializes the bundled environment through `bin/o4w_env.bat`.
`command.txt` supplies QGIS arguments selecting the customization file and bundled
profile; `bin/qgis-ltr.bat` prepares Qt/Python/QGIS and starts the QGIS executable.

The `run_fhast_simulation` plugin collects inputs and writes a run-specific
`config.txt`. It opens a Windows command shell, navigates from the shared helper's location
to `FHAST/`, and invokes bundled `Rscript.exe` with `run_fhast.R`, the configuration
path, and a preview flag. Other analysis plugins use dedicated R wrappers.
`fhast_loader` loads input files into QGIS; it is not the simulation launcher.

`run_fhast_simulation`, `compare_runs`, `ohwm_overlap`, and `parameter_fitter`
import `launch_paths` from `fhast_paths.py` beside the plugin packages. QGIS already
imports from this plugins directory; the helper needs no plugin activation or
additional `sys.path` changes. It anchors the bundle root to its own file location
and returns the FHAST working directory plus the existing relative Rscript/wrapper
paths. Keep the helper with the distributed profile; copying an individual plugin
alone does not provide it. Shell behavior and legacy batch/WSH paths remain unchanged.

The four R wrappers source `FHAST/launcher_paths.R` before package loading to set
`appwd`, `applibpath`, and `scriptwd` in the caller's environment. This is the shared
source for their deployment-directory, private-library, and script-directory paths.
It uses only base R and retains `getwd()` as the anchor: the launchers must still
start R in `FHAST/`. It neither changes directories nor selects an R executable.

`run_fhast.R` sets the private R library path, reads deployment configuration and
package names, loads packages, sources `app.R` to locate Pandoc, then sources
`scripts/main/run_all.R`. The latter performs setup and spatial/input processing,
invokes `NetLogo_Controller.R`, and runs postprocessing/report generation. The
controller reads `NetLogoConfig.txt`, configures `nlrx`, and runs `FHAST.nlogo`
with a run-folder input. The model includes the neighboring `.nls` procedures.
The argument-driven entry sets Java discovery to the bundled JDK.

Do not infer a running Shiny frontend from deployment names or comments:
`app.R` currently configures Pandoc. The deployment README contains generic and
stale instructions; for example, `FHAST/run_command.txt` references a missing
`dist/script/R/run.R`. Verify any documented command against its caller and files.

Legacy launchers are retained, not repaired: `FHAST/fhast.bat` uses `Rscript.exe`
from `PATH` and caller-relative script paths; no current application caller was
found. `FHAST/run_command.txt` also has no verified reader. The WSH
[run.wsf](FHAST/FHAST_App/dist/script/wsf/run.wsf) loads
[run.js](FHAST/FHAST_App/dist/script/wsf/js/run.js), which reads `app/config.cfg`
from its working directory, uses its parent as the launch base, and targets the
same missing `run.R`. No current QGIS caller of this WSH route was found.
`config.cfg`'s `r_exec.home` and WSH fallback apply only to that legacy route;
they do not configure the active Python launchers. Deployment README and
`FHAST/FHAST_App/dist/USAGE.md` examples describe that older framework.
Pandoc discovery in `app.R`, Java discovery in `run_all.R`, and NetLogo discovery
through `NetLogoConfig.txt` retain their existing independent contracts.

## Change boundaries and runtime paths

- Make small, reviewable, behavior-preserving changes. Preserve the current
  working Windows distribution; avoid unrelated cleanup or dependency upgrades.
- Keep infrastructure/packaging work separate from scientific/model changes.
  Never incidentally change model logic, defaults, seeds or stochastic execution,
  inputs, spatial/data transformations, units, output schemas, or report meaning.
  Scientific changes require an explicit scope and appropriate result validation.
- Develop reproducible installation/build/runtime mechanisms alongside the
  bundled distribution. Remove vendored runtime trees only after replacements
  are implemented and verified. Generic upstream deployment advice to stop
  tracking bundled libraries is not a migration plan for this repository.
- Avoid new machine-specific absolute paths. Prefer application-relative paths
  and centralize runtime discovery when practical, preserving current behavior
  while replacements are developed.
- Treat working directory, profile layout, shell quoting, and path case as
  compatibility contracts. The shared helper derives FHAST from the profile layout;
  R uses `getwd()` and `here()`. Existing references vary in case (`FHAST_app`
  versus `FHAST_App`, `netlogo` versus `NetLogo`) and rely on Windows behavior.
  Do not assume this distribution runs unchanged on a case-sensitive system.
- Inspect existing developer-specific absolute paths before running scripts.
  Avoid committing incidental QGIS profile state, generated outputs, or runtime
  changes produced during validation.

## Documentation policy

Inspect and update relevant documentation whenever a change affects installation,
startup, paths, dependency versions, configuration, commands, workflows, inputs,
outputs, or scientific behavior. Update this map when locations or architecture
change. Check the user PDF, release notes, deployment notes, plugin help, and
report templates as relevant; distinguish stale examples from executable behavior.

Run `python3 FHAST/developer_scripts/check_docs.py` from the repository root
(Python 3.9+ standard library and Git) after documentation changes or changes to documented
paths, scripts, or configuration-file locations, and before submitting those changes.
[The checker](FHAST/developer_scripts/check_docs.py) also runs on pull requests and
pushes to `main` via [GitHub Actions](.github/workflows/docs.yml).
Run it in a Git worktree. CI sparsely materializes only the checker and allowlisted
Markdown files. Missing targets pass only if the sparse checkout's Git index marks
them (or tracked descendants for directories) as omitted with `skip-worktree`.
Ordinary working-tree deletions and missing untracked targets still fail; present
local files are checked normally. No target contents or network access are needed
for index-based existence checks.

Its explicit Markdown allowlist is `README.md`, `AGENTS.md`, `FHAST/README.md`, and
`FHAST/FHAST_App/README.md`: the repository landing page, contributor guidance,
the project-directory overview, and mixed project/historical deployment notes. It
checks this map's backtick paths in the Path column and local inline/image-link
destinations and reference definitions in those documents. Document current
script/configuration-file references as relative links (as in this map), rather than
relying on guesses about inline code. Use forward slashes and angle-wrapped or
percent-encoded destinations for spaces or parentheses. Add new maintained Markdown
files explicitly to the allowlist.

It does not scan vendored trees, generated plugin help, PDF instructions, `README.R`,
or R Markdown reports. Those remain subject to manual documentation review.
Code examples, historical bare paths/commands, Windows drive/UNC links, external
URLs, and fragment-only links are skipped. For file links with fragments or query
strings, only the file path is checked. Anchor validity, reference-label matching,
HTML links, and full Markdown syntax are outside this small checker's scope.
It does not run scripts or prove semantic/scientific consistency, configuration-key
validity, or version agreement. Add deterministic checks for those only when an
authoritative source and reliable comparison are established.

Avoid duplicating version numbers here.
`NetLogoConfig.txt` supplies the controller's version/path; `packages.txt` lists
R package names but is not a version lockfile. Consult bundled metadata for actual
installed versions rather than treating generic README examples as authoritative.

## Validation

- Run `python3 FHAST/developer_scripts/check_osgeo4w_package_lock.py` after changes
  to the [package lock](build/osgeo4w-v1-package-lock.json), installed inventory, or
  individually verified QGIS/Python/Qt sources. Run
  `python3 FHAST/developer_scripts/test_osgeo4w_package_lock.py` for checker changes.
  [Package-lock CI](.github/workflows/osgeo4w-package-lock.yml) runs both offline on
  PRs and pushes to `main`, with five sparse inputs and no LFS downloads.
  The lock pins exactly 143 recorded packages; source paths come from the official
  v1 checksum inventory, not guessed package directories. It does not reconstruct
  historical dependency resolution or prove availability, authenticity, dependency
  closure, or customized installed-tree equivalence. Preserve unresolved resolver
  discrepancies separately; never silently add dependencies to this recorded set.
  The existing artifact fetcher does not consume the package lock yet.
- The manual acquisition command is
  `python3 FHAST/developer_scripts/fetch_runtime_artifacts.py --output-dir <directory>`.
  Choose a directory outside this repository/runtime bundle. Optional
  `--component <name>` selects one component; `--dry-run` validates and lists
  eligible downloads without writing files or accessing the network.
  [The fetcher](FHAST/developer_scripts/fetch_runtime_artifacts.py) downloads only
  existing `verified` standalone artifact/package entries from source metadata.
  Partial/unresolved entries and bundled children stay blocked; resolve their
  evidence separately. Downloads are not installs, extraction, or packaging.
  Existing files are checksum-verified and reused; mismatches fail without
  overwriting them. Temporary downloads are verified before atomic publication
  and cleaned up on failure. Publication requires filesystem hard-link support
  (for example NTFS); unsupported filesystems fail without accepting an artifact.
  Recorded checksums identify artifacts, not full FHAST bundle equivalence;
  historical MD5 does not provide a modern authenticity guarantee.
  Run `python3 FHAST/developer_scripts/test_fetch_runtime_artifacts.py` for fetcher
  changes. Source CI also runs these tests with in-memory response fixtures and
  no public network access or real runtime downloads.
- Run `python3 FHAST/developer_scripts/check_runtime_sources.py` after acquisition
  metadata or runtime manifest changes, and
  `python3 FHAST/developer_scripts/test_runtime_sources.py` for checker changes.
  [Source metadata](build/windows-runtime-sources.json) describes how current
  Windows components could be acquired; the runtime manifest describes what is
  installed. Keep component names and versions consistent. URLs and checksums
  require recorded authoritative evidence; never substitute newer artifacts or
  label LFS object IDs as upstream checksums. Incomplete historical acquisition
  must remain `partial` or `unresolved` with explicit `unresolved` reasons.
  `verified` identifies an individual source artifact, not equivalence of the
  complete customized FHAST bundle. Historical MD5 values are not modern
  authenticity guarantees. The checker documents schema 1 and validates structure,
  manifest agreement, bundled relationships and installed OSGeo4W archive names.
  [Source CI](.github/workflows/runtime-sources.yml) runs both commands offline on
  PRs and pushes to `main`, with only JSON, checker/tests and `installed.db` checked
  out; no runtime binaries or LFS downloads. Evidence truth, remote availability,
  dependency closure and binary equivalence still require separate verification.
  This is preparatory metadata, not a download/build script or cross-platform plan.
- Run `python3 FHAST/developer_scripts/check_runtime_manifest.py` after runtime
  version, location, or metadata changes, and update
  [the manifest](build/windows-runtime-manifest.json) in the same change. It is the
  maintained inventory for the current bundled Windows runtime, not a future
  cross-platform packaging specification. Runtime metadata remains the evidence;
  launchers do not consume the manifest. Package-manager versions include their
  packaging revisions. Unknown versions are explicitly `null` with explanatory
  notes; never promote directory names, guide dates, or examples to verified versions.
  The checker validates schema, directory/source existence, and selected static
  metadata fields. It does not establish binary integrity, dependency completeness,
  license/redistribution rights, or successful runtime execution.
  Run `python3 FHAST/developer_scripts/test_runtime_manifest.py` for checker changes.
  [Manifest CI](.github/workflows/runtime-manifest.yml) runs both commands with a
  sparse checkout and no LFS downloads. The NetLogo GUI JRE's version evidence is
  inside `rt.jar`, so that specific archive is included; only its manifest is read.
- Run `python3 FHAST/developer_scripts/test_fhast_paths.py` for launcher-path changes.
  [Windows CI](.github/workflows/launcher-paths.yml) runs the same tests using
  `python` on pull requests and pushes to `main`.
  These standard-library tests use a temporary bundle with spaces and check command
  construction without launching QGIS, R, or NetLogo; they do not validate Windows
  shell execution or the end-to-end application.
- Run `python3 FHAST/developer_scripts/test_r_launcher_paths.py` for R bootstrap
  path changes; the same Windows workflow runs it with `python`. These structural
  and path-equivalence tests do not execute R or prove R startup succeeds.
- Run existing relevant tests/checks when applicable and available. Report exact
  checks and outcomes, prerequisites that prevented execution, and what remains
  unverified. Never equate static inspection with a successful application run.
- Several FHAST plugin directories contain Plugin Builder `test/` scaffolding,
  Makefile test/lint targets, `pb_tool.cfg`, translation scripts, and Sphinx help
  builds. For example, `run_fhast_simulation` has dialog, QGIS-environment,
  resource, and translation tests. These are not end-to-end scientific tests.
  Its `make test` uses nose and suppresses failures; inspect actual results,
  not just the exit status. Review environment and deployment paths before use.
- Bundled dependencies also contain upstream tests. The documentation check above
  has CI coverage; no FHAST-wide automated scientific regression suite was found.
  `FHAST/scripts/compare_runs/check_runs.R` checks compatibility of
  user runs during comparison; it is not a standalone regression test suite.
- Packaging work should establish and validate the complete Windows chain:
  QGIS -> FHAST plugin -> R -> nlrx -> NetLogo, including input preparation and
  resulting outputs/reports. Compare against a recorded baseline with controlled
  inputs and seeds before replacing bundled components. Until exercised, state
  clearly that this chain remains unverified.
