{
  buildNpmPackage,
  importNpmLock,
  nodejs_22,
}:

(buildNpmPackage.override { nodejs = nodejs_22; }) {
  pname = "second-brain-frontend";
  version = "0.1.0";
  src = ../frontend;

  # The committed npm lockfile carries integrity hashes, avoiding a separate
  # manually maintained npm dependency hash.
  npmDeps = importNpmLock { npmRoot = ../frontend; };
  npmConfigHook = importNpmLock.npmConfigHook;

  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    cp -r dist "$out/"
    runHook postInstall
  '';
}
