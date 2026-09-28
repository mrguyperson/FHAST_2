; Compiled in a temporary directory by tests; the resulting EXE is never run.
Unicode true
Name "FHAST extraction fixture"
OutFile "fixture.exe"
SetCompressor /SOLID lzma
Section
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=helper.txt "payload.txt"
  SetOutPath "$INSTDIR\App\Folder With Spaces"
  File /oname=Payload.txt "payload.txt"
SectionEnd
