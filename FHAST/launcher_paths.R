# Shared bootstrap paths for the four bundled R launch wrappers.
# The Windows launchers must still start R with FHAST/ as its working directory.
# Use base R here: the private package library has not been added to .libPaths yet.
# Preserve the existing FHAST_app spelling and caller's working directory.
appwd = file.path(getwd(), 'FHAST_app')
applibpath = file.path(appwd, 'app', 'library')
scriptwd = file.path(getwd(), 'scripts')
