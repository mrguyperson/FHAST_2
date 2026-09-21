# FHAST

**FHAST (Fish Habitat Assessment and Simulation Tool)** is a spatially explicit,
agent-based modeling system for assessing how river habitat conditions and habitat
alterations affect anadromous fish.

FHAST combines a QGIS-based user interface, R-based spatial processing and analysis,
and a NetLogo agent-based simulation. The model is designed to represent interacting
physical, physiological, and behavioral processes rather than treating habitat
variables independently.

> **About this repository**
>
> This repository is an independently hosted fork and continuation of
> [pndphd/FHAST_2](https://github.com/pndphd/FHAST_2).
>
> The existing FHAST scientific model and working Windows distribution are being
> preserved while the surrounding software infrastructure is incrementally
> modernized to improve reproducibility, testing, maintainability, and eventually
> cross-platform support.
>
> See [Modernization status](#modernization-status) for the current state of that work.

## What FHAST does

River-management, infrastructure, and restoration projects can change many aspects
of fish habitat at the same time. Changes to flow, depth, velocity, cover,
vegetation, food availability, predation risk, and other environmental conditions
may interact, and an action that improves one biological outcome may negatively
affect another.

FHAST was developed to provide a mechanistic framework for evaluating those
interacting effects.

The model represents individual fish in spatially explicit river habitat and uses
physiological and behavioral processes to simulate outcomes through time. Current
model components include juvenile rearing, migration and movement, spawning, redd
incubation, growth and condition, predation, and multiple causes of mortality.

FHAST has been developed for work with anadromous fishes including salmonids and
sturgeon. Applications presented by the original development team include evaluation
of habitat alterations such as changing cover, altering flows, and changing food
availability, with outputs describing effects on fish growth, survival, and
population-level outcomes.

The framework combines hydraulic and spatial habitat information with biological
parameters and daily environmental conditions. The default FHAST input structure
includes:

- fish population data;
- daily environmental conditions;
- fish parameters;
- river-grid geometry;
- hydraulic rasters;
- cover and canopy information;
- tree growth;
- habitat parameters;
- fish interaction parameters;
- predator parameters;
- optional areas of interest and spatial selection rules.

FHAST is intended to support comparison of scenarios rather than reduce habitat
quality to a single static metric. The application includes tools for running
simulations, comparing run outputs, parameter fitting, ordinary-high-water-mark
analysis, loading FHAST data into QGIS, preparing input files, and batch
reprojection.

## How FHAST works

The current execution chain is:

QGIS / OSGeo4W
→ FHAST QGIS plugins
→ R
→ NetLogo through `nlrx`
→ postprocessing and reporting

The bundled QGIS interface is the primary user-facing application.

The simulation launcher collects the selected inputs and creates a run
configuration. R performs input preparation and spatial processing, initializes the
model, drives NetLogo through `nlrx`, and performs postprocessing and report
generation.

The NetLogo model represents the agent-based biological simulation. During model
execution, habitat conditions such as depth, velocity, shade, cover, turbidity
effects, and predator conditions are updated before fish and redd processes are
evaluated.

Major model procedure groups include:

- habitat setup and fish hatching;
- juvenile behavior and rearing;
- spawning;
- redd processes;
- movement and path finding;
- output generation.

See [AGENTS.md](AGENTS.md) for the verified repository architecture and execution
flow.

## User interface and analysis tools

The distributed QGIS profile contains several FHAST-specific plugins, including
tools for:

- running FHAST simulations from loaded project inputs;
- creating and managing FHAST input files;
- loading FHAST outputs and inputs into QGIS;
- creating FHAST templates;
- comparing two simulation runs and producing a comparison report;
- ordinary-high-water-mark overlap analysis;
- fitting model parameters;
- batch reprojection and supporting spatial-data preparation.

The repository also contains R scripts for model setup, spatial processing,
simulation control, postprocessing, run comparison, parameter analysis, reporting,
and related workflows.

## Current distribution

FHAST is currently distributed in this repository as a self-contained,
Windows-oriented application bundle.

The repository contains both FHAST source code and bundled third-party runtimes,
including:

- QGIS / OSGeo4W;
- Python and Qt used by QGIS;
- R and a private R package library;
- NetLogo;
- Java;
- Pandoc.

This makes the repository unusually large and means it should not currently be
treated like a conventional source-only software project.

The existing bundled Windows application remains the compatibility reference while
a reproducible build process is developed.

For current user instructions, see
[FHAST Run Instructions 2.0.pdf](<FHAST Run Instructions 2.0.pdf>).

## Repository structure

Important project-owned locations include:

- `FHAST/scripts/` — R model pipeline, spatial processing, analysis, and reports.
- `FHAST/scripts/main/` — main setup, simulation, and postprocessing orchestration.
- `FHAST/scripts/NetLogo/` — the NetLogo model and R/NetLogo controller.
- `FHAST/default_input/` — default FHAST run-input template.
- `profile/profiles/default/python/plugins/` — bundled QGIS plugins, including the
  FHAST user interface.
- `FHAST/FHAST_App/` — application deployment files and bundled R environment.
- `FHAST/developer_scripts/` — validation and modernization tooling.
- `build/` — machine-readable runtime inventory and acquisition metadata.
- `AGENTS.md` — verified architecture, development constraints, and validation
  guidance.

The root-level `apps/`, `bin/`, `etc/`, `include/`, `lib/`, and `share/`
directories belong primarily to the bundled OSGeo4W/QGIS distribution.

## Relationship to the original project

The upstream FHAST repository is:

https://github.com/pndphd/FHAST_2

This repository was created independently from the upstream `main` branch rather
than through GitHub's formal Fork button. GitHub therefore does not identify it as
part of the upstream repository's fork network, but the upstream project is its
direct technical and historical starting point.

The purpose of this fork is not to replace the FHAST model with a new model.

Modernization work is being carried out around the existing scientific
implementation, with infrastructure and packaging changes deliberately separated
from changes to model behavior.

The current goals are to:

- preserve existing scientific behavior during infrastructure work;
- document the actual runtime architecture and dependency relationships;
- replace implicit and duplicated runtime-path assumptions with maintainable
  interfaces;
- make bundled runtime versions explicit and verifiable;
- establish evidence-backed sources for those runtimes;
- reconstruct the current Windows distribution reproducibly;
- add deterministic validation and CI;
- make dependency upgrades deliberate and reviewable;
- eventually remove vendored third-party runtimes when reproducible replacements
  have been demonstrated;
- eventually support maintainable builds on additional operating systems.

## Modernization status

Modernization is being performed through small, reviewable changes. The current
working Windows bundle remains in place until replacement mechanisms have been
implemented and validated.

### Implemented

- [x] Documented the repository architecture, execution chain, and modernization
      constraints in `AGENTS.md`.
- [x] Added an offline documentation-consistency checker.
- [x] Added GitHub Actions validation for maintained documentation.
- [x] Centralized FHAST-root and bundled-R path discovery for the active QGIS
      simulation and analysis launchers.
- [x] Added focused Windows launcher-path tests.
- [x] Centralized shared bootstrap paths used by the active R launch wrappers.
- [x] Added structural and path-equivalence tests for the R launch wrappers.
- [x] Added `build/windows-runtime-manifest.json`, a machine-readable inventory of
      the checked-in Windows runtime components.
- [x] Added deterministic validation of runtime versions and locations against
      static repository metadata.
- [x] Added `build/windows-runtime-sources.json`, separating installed-runtime
      identity from acquisition provenance.
- [x] Added explicit `verified`, `partial`, and `unresolved` acquisition states so
      missing historical evidence is not silently replaced with newer artifacts.
- [x] Added offline CI validation of runtime acquisition metadata.
- [x] Added a checksum-verifying runtime artifact fetcher for components whose
      acquisition sources are already verified.
- [x] Added offline tests covering runtime download selection, checksum validation,
      cache reuse, failed downloads, and safe artifact publication.

### Runtime reconstruction

- [ ] Resolve the complete historical OSGeo4W package and dependency set.
- [x] Verify the historical OSGeo4W v1 source and checksum for the bundled QGIS package.
- [ ] Verify the historical Windows NetLogo distribution used by FHAST.
- [ ] Tie the recorded Oracle JDK download conclusively to the exact bundled JDK
      build.
- [ ] Establish the relationship between the NetLogo distribution and its bundled
      JRE.
- [ ] Identify the exact bundled Pandoc version.
- [ ] Add deterministic extraction of verified runtime artifacts.
- [ ] Assemble the expected runtime directory layout from acquisition inputs.
- [ ] Compare reconstructed runtime contents and behavior with the checked-in
      reference bundle.
- [ ] Build the complete Windows application from clean CI inputs.

### R environment

- [ ] Inventory and distinguish direct and transitive R package dependencies.
- [ ] Establish reproducible R package versions.
- [ ] Introduce an appropriate dependency lock mechanism.
- [ ] Reconstruct the FHAST private R library during the build rather than relying
      indefinitely on the checked-in package library.
- [ ] Validate the reconstructed R environment against the reference application.

### Testing and releases

- [ ] Establish controlled model baselines for infrastructure regression testing.
- [ ] Add higher-level Windows application startup tests.
- [ ] Exercise the full QGIS → R → NetLogo chain in automated validation where
      practical.
- [ ] Compare controlled simulation outputs before replacing bundled components.
- [ ] Produce versioned Windows build artifacts in CI.
- [ ] Document a clean developer and release workflow.

### Longer-term goals

- [ ] Remove checked-in third-party runtime trees after reproducible replacements
      are demonstrated.
- [ ] Reduce the size of the source repository substantially.
- [ ] Separate source, build inputs, and generated distribution artifacts more
      clearly.
- [ ] Support maintainable Linux builds.
- [ ] Support maintainable macOS builds.
- [ ] Evaluate platform-specific distribution formats after the core application
      can be built reproducibly.

Linux Flatpak packaging and other distribution-format decisions are deliberately
deferred until the underlying application and runtime reconstruction are stable.

## Runtime reproducibility metadata

Two files currently define the modernization boundary around the Windows runtime:

- `build/windows-runtime-manifest.json` records what is present in the checked-in
  bundle and the static evidence for its version.
- `build/windows-runtime-sources.json` records what is known about acquiring those
  same components again.

The acquisition metadata intentionally distinguishes verified artifacts from
partial or unresolved historical evidence.

The current artifact fetcher will download only entries already marked as verified.
It does not install or extract them and does not substitute newer software for
unresolved historical components.

This is intentionally conservative: a successful download and checksum match
establish the identity of an artifact, not equivalence of a reconstructed FHAST
distribution.

## Development principles

Modernization work in this fork follows several rules:

1. Preserve scientific behavior unless a change is explicitly scientific in scope.
2. Keep model/scientific changes separate from infrastructure and packaging work.
3. Prefer small, reviewable changes.
4. Document existing behavior before replacing it.
5. Treat the current Windows application as the compatibility reference.
6. Do not claim reproducibility until acquisition, assembly, and validation have
   actually been demonstrated.
7. Prefer deterministic, offline tests and CI where practical.
8. Do not remove bundled runtimes merely because a replacement approach is
   planned.

Contributors should read [AGENTS.md](AGENTS.md) before changing architecture,
runtime handling, packaging, or model execution.

## Development team and history

The existing FHAST release notes identify the development team as:

- Peter N. Dudley — Principal Investigator
- Jesse A. Black
- Stephanie G. Diaz
- Ted W. Hermann
- Chris John
- Kwanmok Kim

The release notes also document contributions spanning model design, sampling and
gridding, metabolic and feeding algorithms, movement and migration, predation,
pathfinding, GUI development, and related ecological research.

See [README.R](README.R) for the existing release history and contribution notes.

The checked-in release notes currently identify version **2.2.0 (Sharp Scale)**,
dated 2026-04-16. The existing citation text in `README.R` has not yet been updated
to that release and should be treated as historical project metadata rather than a
version-authoritative source.

## Background

FHAST was developed to support evaluation of habitat effects on anadromous fish
where multiple ecological processes interact. Public presentations by the original
development team describe applications to habitat alteration and restoration,
including changes to flow, cover, and food availability, and applications involving
salmonids and sturgeon.

Related public project descriptions include:

- [FHAST: A new modeling package for assessing habitat effects on anadromous fish
  (Ecological Society of America, 2023)](https://esa2023.eventscribe.net/fsPopup.asp?PresentationID=1275962&mode=presInfo)
- [FHAST: A mechanistic based tool for assessing habitat effects on anadromous fish
  (NOAA Science Seminar, 2024)](https://www.star.nesdis.noaa.gov/star/NOAAScienceSeminars_2024.php)

## Citation

The original repository contains citation information in [README.R](README.R).
Because the release notes and citation text currently refer to different FHAST
versions, this fork does not silently rewrite the project citation here.

Users preparing a publication or report should verify the appropriate FHAST
version and citation with the original project documentation.

## License

No root-level software license is currently declared in this repository.

Before redistributing reconstructed third-party runtimes or publishing packaged
release artifacts, the applicable FHAST and dependency licensing terms should be
reviewed explicitly.
